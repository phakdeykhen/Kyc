"""Process sessions waiting in DOCUMENT_PROCESSING (deferred mode, or retries after OCR failures),
then run cross-checks and fraud signals for PROCESSING sessions that have not been analyzed.

Usage: python scripts/process_documents.py [ORGANIZATION_ID ...]
Defaults to the local development organization. Safe to run repeatedly.
"""

from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy.orm import sessionmaker

from kyc.core.config import get_settings
from kyc.db.session import build_engine
from kyc.main import (build_biometric_cipher, build_capture_store, build_document_processor, build_field_cipher,
                      build_fraud_analyzer)
from kyc.biometrics import FaceMatchPolicy
from kyc.services.documents import pending_sessions
from kyc.services.fraud import processing_sessions

settings = get_settings()
engine = build_engine(settings)
factory = sessionmaker(engine, expire_on_commit=False)
store, cipher = build_capture_store(settings), build_field_cipher(settings)
processor = build_document_processor(settings, factory, store, cipher)
analyzer = build_fraud_analyzer(settings, factory, cipher, build_biometric_cipher(settings), store, FaceMatchPolicy(
    version=settings.face_match_policy_version, pass_threshold=settings.face_match_pass_threshold,
    fail_threshold=settings.face_match_fail_threshold, calibrated=settings.face_match_calibrated,
    calibration_reference=settings.face_match_calibration_reference))
reason = processor.unavailable_reason()
if reason:
    raise SystemExit(f"Document processing unavailable: {reason}")
organizations = [UUID(value) for value in sys.argv[1:]] or [settings.development_organization_id]
for organization_id in organizations:
    for session_id in pending_sessions(factory, organization_id):
        outcome = processor.process(organization_id, session_id, uuid4())
        print(f"{session_id}: {outcome.status} {' '.join(outcome.reason_codes)}".rstrip())
    for session_id in processing_sessions(factory, organization_id):
        print(f"{session_id}: fraud analysis {analyzer.analyze(organization_id, session_id, uuid4())}")
engine.dispose()
