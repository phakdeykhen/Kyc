"""Independent face readiness and least-privilege checks. Does not mark KYC ready."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa
from kyc.core.config import get_settings
from kyc.biometrics.opencv import OpenCVFaceEngine
from kyc.db.privileges import FACE_ROLE, FACE_TABLE_PRIVILEGES
from kyc.face_identity.service import FaceIdentityService
from kyc.main import build_biometric_cipher, build_capture_store, build_field_cipher
from scripts.security_check import catalog_checks


def main():
    settings = get_settings()
    state = SimpleNamespace(field_cipher=build_field_cipher(settings), biometric_cipher=build_biometric_cipher(settings),
        capture_store=build_capture_store(settings), session_factory=None,
        face_engine=OpenCVFaceEngine(settings.face_models_dir / "face_detection_yunet_2023mar.onnx",
                                    settings.face_models_dir / "face_recognition_sface_2021dec.onnx"))
    service = FaceIdentityService(settings, state)
    checks = []
    try:
        readiness = service.readiness()
        if settings.migration_database_url:
            admin = sa.create_engine(settings.migration_database_url.get_secret_value(), hide_parameters=True)
            try:
                with admin.connect() as db:
                    checks = catalog_checks(db, role=FACE_ROLE,
                        table_privileges={name: frozenset(value.split()) for name, value in FACE_TABLE_PRIVILEGES.items()},
                        column_updates={}, definer_functions=())
            finally:
                admin.dispose()
        ready = readiness["ready"] and bool(checks) and all(row["result"] == "PASS" for row in checks)
        print(json.dumps({**readiness, "ready": ready, "database_checks": checks}, indent=2))
        return 0 if ready else 1
    finally:
        if service.engine:
            service.engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
