"""Process sessions waiting in DOCUMENT_PROCESSING (deferred mode, or retries after OCR failures).

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
from kyc.main import build_capture_store, build_document_processor, build_field_cipher
from kyc.services.documents import pending_sessions

settings = get_settings()
engine = build_engine(settings)
factory = sessionmaker(engine, expire_on_commit=False)
processor = build_document_processor(settings, factory, build_capture_store(settings), build_field_cipher(settings))
reason = processor.unavailable_reason()
if reason:
    raise SystemExit(f"Document processing unavailable: {reason}")
organizations = [UUID(value) for value in sys.argv[1:]] or [settings.development_organization_id]
for organization_id in organizations:
    for session_id in pending_sessions(factory, organization_id):
        outcome = processor.process(organization_id, session_id, uuid4())
        print(f"{session_id}: {outcome.status} {' '.join(outcome.reason_codes)}".rstrip())
engine.dispose()
