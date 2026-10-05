"""Capture retention: delete expired captures and sweep ciphertext with no database row.

Runs per organization inside that tenant's transaction, so row-level security
still applies. Object deletion happens only after the database commit.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from kyc.db.models import DocumentImage, IdentityDocument
from kyc.db.session import set_tenant
from kyc.storage.captures import CaptureStore

# Objects are written before their row commits; never sweep anything this fresh.
ORPHAN_GRACE = timedelta(minutes=15)


@dataclass(frozen=True)
class PurgeReport:
    expired_images: int
    expired_documents: int
    deleted_objects: int


def purge_organization(factory: sessionmaker, store: CaptureStore, organization_id: UUID,
                       now: datetime | None = None) -> PurgeReport:
    now = now or datetime.now(timezone.utc)
    with factory() as db, db.begin():
        set_tenant(db, organization_id)
        expired = db.scalars(sa.select(DocumentImage).where(DocumentImage.organization_id == organization_id,
                                                            DocumentImage.delete_after <= now)).all()
        for image in expired:
            db.delete(image)
        documents = db.scalars(sa.select(IdentityDocument).where(IdentityDocument.organization_id == organization_id,
                                                                 IdentityDocument.delete_after <= now)).all()
        for document in documents:
            db.delete(document)  # cascades to images, fields and checks
        db.flush()
        live = set(db.scalars(sa.select(DocumentImage.encrypted_object_ref)
                              .where(DocumentImage.organization_id == organization_id)).all())
    deleted = 0
    for ref in store.list_refs(organization_id):
        modified = store.modified_at(ref)
        if ref in live or modified is None or modified > now - ORPHAN_GRACE:
            continue
        store.delete(ref)
        deleted += 1
    return PurgeReport(len(expired), len(documents), deleted)
