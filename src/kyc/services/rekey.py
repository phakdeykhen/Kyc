"""Key rotation: re-encrypt stored data under each keyring's active key (Phase 17, spec §22).

Every keyring already decrypts with older versions and encrypts with the first. Rotation
is therefore: put the new key first, run this re-encryption, check the inventory shows no
data left under the old version, then remove the old key from the keyring.

Each record is opened with the context it was sealed with and sealed again under the
active key with the same context, so tenant/session binding is unchanged. Capture objects
are rewritten in place (atomic replace); the file's envelope names its key, so a crash
between the file and the row update leaves both readable.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from kyc.db.models import (BarcodeResult, BiometricTemplate, DocumentField, DocumentImage, ManualReview, SelfieCapture,
                           WebhookEndpoint)
from kyc.db.session import set_tenant
from kyc.webhooks.dispatcher import secret_context

CLASSES = ("document_fields", "barcode_payloads", "review_notes", "webhook_secrets", "biometric_templates",
           "capture_objects")


@dataclass
class ClassReport:
    before: Counter = field(default_factory=Counter)   # key version → records
    resealed: int = 0
    active: str | None = None
    skipped: str | None = None                          # why nothing could be done
    unreferenced: Counter = field(default_factory=Counter)
    metadata_repaired: int = 0

    def view(self) -> dict:
        return {"active_version": self.active, "records_by_version": dict(sorted(self.before.items())),
                "resealed": self.resealed,
                **({"unreferenced_objects_by_version": dict(sorted(self.unreferenced.items()))}
                   if self.unreferenced else {}),
                **({"metadata_repaired": self.metadata_repaired} if self.metadata_repaired else {}),
                **({"skipped": self.skipped} if self.skipped else {})}


@dataclass
class Keyrings:
    field_cipher: object | None = None      # PII FieldCipher
    biometric_cipher: object | None = None
    webhook_cipher: object | None = None    # WebhookSecretCipher
    capture_store: object | None = None


def _field_context(row: DocumentField, part: str) -> str:
    return f"field/{row.organization_id}/{row.session_id}/{row.document_id}/{row.field_name}/{part}"


def _barcode_context(row: BarcodeResult) -> str:
    side = (row.data_consistency or {}).get("side")
    return f"barcode/{row.organization_id}/{row.session_id}/{row.document_id}/{side}/{row.symbology}"


def _note_context(row: ManualReview) -> str:
    return f"review/{row.organization_id}/{row.session_id}/{row.id}/note"


def _template_context(row: BiometricTemplate) -> str:
    return (f"biometric/{row.organization_id}/{row.session_id}/{row.id}/{row.source}/"
            f"{row.model_name}/{row.model_version}/{row.model_sha256}")


def _object_scope(ref: str) -> tuple[UUID, UUID, UUID]:
    organization, session, object_name = ref.split("://", 1)[1].split("/")
    return UUID(organization), UUID(session), UUID(object_name.removesuffix(".bin"))


def _reseal_fields(db: Session, rows: Iterable[DocumentField], cipher, report: ClassReport, apply: bool) -> None:
    for row in rows:
        report.before[row.key_version] += 1
        if apply and row.key_version != cipher.active:
            for part, column in (("raw", "raw_value_ciphertext"), ("normalized", "normalized_value_ciphertext")):
                sealed = getattr(row, column)
                if sealed is not None:
                    value = cipher.open(sealed, row.key_version, _field_context(row, part))
                    setattr(row, column, cipher.seal(value, _field_context(row, part))[0])
            row.key_version = cipher.active
            report.resealed += 1


def _reseal_simple(rows, cipher, report: ClassReport, apply: bool, column: str, version_column: str,
                   context: Callable, active: str) -> None:
    for row in rows:
        version = getattr(row, version_column)
        report.before[version] += 1
        if apply and version != active:
            value = cipher.open(getattr(row, column), version, context(row))
            sealed, new_version = cipher.seal(value, context(row))
            setattr(row, column, sealed)
            setattr(row, version_column, new_version)
            report.resealed += 1


def _webhook_secrets(rows, cipher, report: ClassReport, apply: bool) -> None:
    for row in rows:
        context = secret_context(row.organization_id, row.id)
        for column, version_column in (("secret_ciphertext", "key_version"),
                                       ("previous_secret_ciphertext", "previous_key_version")):
            sealed, version = getattr(row, column), getattr(row, version_column)
            if sealed is None:
                continue
            report.before[version] += 1
            if apply and version != cipher.active_version:
                new_sealed, new_version = cipher.seal(cipher.open(sealed, version, context), context)
                setattr(row, column, new_sealed)
                setattr(row, version_column, new_version)
                report.resealed += 1


def _capture_objects(db: Session, organization_id: UUID, store, report: ClassReport, apply: bool) -> None:
    referenced = set()
    for model in (DocumentImage, SelfieCapture):
        query = sa.select(model).where(model.organization_id == organization_id).order_by(model.id)
        if apply:
            query = query.with_for_update()
        for row in db.scalars(query):
            referenced.add(row.encrypted_object_ref)
            # The database version may lag an atomic file replacement if its
            # transaction failed. Bind to the row's ID, never an ID taken from
            # the object reference, and inventory the authenticated envelope.
            data, version = store.get_with_version(row.encrypted_object_ref, row.organization_id, row.session_id, row.id)
            report.before[version] += 1
            if apply and version != store.active_version:
                stored = store.put(row.organization_id, row.session_id, row.id, data)
                if stored.ref != row.encrypted_object_ref:
                    raise RuntimeError("Capture store returned a different reference while re-encrypting.")
                row.key_version = stored.key_version
                report.resealed += 1
            elif apply and row.key_version != version:
                row.key_version = version
                report.metadata_repaired += 1
    # Failed uploads and interrupted erasures can leave objects without rows.
    # They still depend on their keys. Report them until the retention sweep
    # removes them; do not race that sweep by rewriting orphan objects.
    for ref in set(store.list_refs(organization_id)) - referenced:
        scope = _object_scope(ref)
        if scope[0] != organization_id:
            raise ValueError("Capture object is outside the requested organization.")
        _, version = store.get_with_version(ref, *scope)
        report.before[version] += 1
        report.unreferenced[version] += 1


def _rows(db: Session, model, organization_id: UUID, apply: bool, *conditions):
    query = sa.select(model).where(model.organization_id == organization_id, *conditions).order_by(model.id)
    return db.scalars(query.with_for_update() if apply else query)


def rekey_organization(factory: sessionmaker, organization_id: UUID, keys: Keyrings, apply: bool = True,
                       classes: Iterable[str] = CLASSES) -> dict[str, ClassReport]:
    """Inventory (apply=False) or re-encrypt one organization. Each class commits on its own."""
    classes = tuple(classes)
    unknown = set(classes) - set(CLASSES)
    if unknown:
        raise ValueError(f"Unknown data classes: {sorted(unknown)!r}.")
    reports: dict[str, ClassReport] = {}
    for name in classes:
        report = reports[name] = ClassReport()
        with factory() as db, db.begin():
            set_tenant(db, organization_id)
            if name in ("document_fields", "barcode_payloads", "review_notes"):
                cipher = keys.field_cipher
                if cipher is None:
                    report.skipped = "PII_ENCRYPTION_KEYS not configured"
                    continue
                report.active = cipher.active
                if name == "document_fields":
                    rows = _rows(db, DocumentField, organization_id, apply,
                                 sa.or_(DocumentField.raw_value_ciphertext.is_not(None),
                                        DocumentField.normalized_value_ciphertext.is_not(None)))
                    _reseal_fields(db, rows, cipher, report, apply)
                elif name == "barcode_payloads":
                    rows = _rows(db, BarcodeResult, organization_id, apply, BarcodeResult.payload_ciphertext.is_not(None))
                    _reseal_simple(rows, cipher, report, apply, "payload_ciphertext", "key_version", _barcode_context,
                                   cipher.active)
                else:
                    rows = _rows(db, ManualReview, organization_id, apply, ManualReview.reason_ciphertext.is_not(None))
                    _reseal_simple(rows, cipher, report, apply, "reason_ciphertext", "key_version", _note_context,
                                   cipher.active)
            elif name == "webhook_secrets":
                if keys.webhook_cipher is None:
                    report.skipped = "no webhook keyring configured"
                    continue
                report.active = keys.webhook_cipher.active_version
                _webhook_secrets(_rows(db, WebhookEndpoint, organization_id, apply), keys.webhook_cipher,
                                 report, apply)
            elif name == "biometric_templates":
                if keys.biometric_cipher is None:
                    report.skipped = "BIOMETRIC_ENCRYPTION_KEYS not configured"
                    continue
                report.active = keys.biometric_cipher.active
                _reseal_simple(_rows(db, BiometricTemplate, organization_id, apply), keys.biometric_cipher,
                               report, apply, "template_ciphertext", "key_version", _template_context,
                               keys.biometric_cipher.active)
            elif name == "capture_objects":
                if keys.capture_store is None:
                    report.skipped = "CAPTURE_ENCRYPTION_KEYS not configured"
                    continue
                report.active = keys.capture_store.active_version
                _capture_objects(db, organization_id, keys.capture_store, report, apply)
            else:
                raise ValueError(f"Unknown data class {name!r}.")
            db.flush()
    return reports


def retired_versions_in_use(reports: dict[str, ClassReport]) -> dict[str, list[str]]:
    """Per class, key versions other than the active one that still protect data."""
    return {name: sorted(version for version, count in report.before.items()
                         if count and version != report.active)
            for name, report in reports.items() if not report.skipped}
