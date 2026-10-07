"""Production approval matrix and readiness classification.

Each component carries an honest status from a closed vocabulary. The overall result is
computed, never asserted: READY only when every critical component is PRODUCTION_READY;
READY_WITH_LIMITATIONS when only non-critical components fall short; otherwise NOT_READY.

Statuses describe evidence, not intent. A component tested only with synthetic data is
NOT_REAL_WORLD_TESTED however good its unit tests are. Update an entry only with the
evidence that justifies it (a test run, a calibration report, a device test log).
"""

from dataclasses import asdict, dataclass, field

STATUSES = ("PRODUCTION_READY", "COMPLETE", "PARTIAL", "MOCK_ONLY", "UNCALIBRATED", "NOT_REAL_WORLD_TESTED",
            "NOT_IMPLEMENTED", "NOT_SUPPORTED", "SECURITY_RISK", "PERFORMANCE_RISK", "BROKEN", "INSECURE")
# Any of these on a critical component blocks production (spec: never READY while a critical
# feature is UNCALIBRATED, MOCK_ONLY, NOT_REAL_WORLD_TESTED, BROKEN or INSECURE).
BLOCKING = set(STATUSES) - {"PRODUCTION_READY"}


@dataclass(frozen=True)
class Component:
    name: str
    critical: bool
    status: str
    implemented: str        # YES | PARTIAL | NO
    automated_tests: str    # YES | PARTIAL | NO
    real_tested: str        # YES | PARTIAL | NO
    calibrated: str         # YES | NO | N/A
    security_reviewed: str  # YES | PARTIAL | NO
    evidence: str
    blocker: str | None = None
    next_action: str = ""
    limitations: tuple[str, ...] = field(default_factory=tuple)


def _c(name, critical, status, impl, tests, real, cal, sec, evidence, blocker=None, next_action="", limitations=()):
    if status not in STATUSES:
        raise ValueError(f"unknown status {status}")
    return Component(name, critical, status, impl, tests, real, cal, sec, evidence, blocker, next_action, tuple(limitations))


COMPONENTS = (
    _c("Document Capture", True, "NOT_REAL_WORLD_TESTED", "YES", "YES", "PARTIAL", "NO", "PARTIAL",
       "Deterministic quality gate (blur, glare, brightness, shadow, coverage, perspective, resolution, multiple documents); "
       "6 real KH ID photos: 2 accepted, 4 sent to recapture with correct instructions.",
       "Quality thresholds not calibrated on real phone captures.",
       "Collect 200+ real captures per document across Android and iPhone; set thresholds from measured OCR success.",
       ("No live best-frame selection for document capture; the first accepted upload is processed.",)),
    _c("KH National ID", True, "NOT_REAL_WORLD_TESTED", "YES", "YES", "PARTIAL", "NO", "PARTIAL",
       "Adapter KH-NID-ADAPTER-2026.10.3; front MRZ (TD1) parsing; on 2 usable real cards the MRZ failed check digits or was not "
       "found, so both went to recapture/review (fail-safe).",
       "Real-card extraction rate unmeasured.", "Build a labelled set of 200 consented KH ID captures and measure field accuracy."),
    _c("KH NSSF", True, "NOT_REAL_WORLD_TESTED", "YES", "YES", "NO", "NO", "PARTIAL",
       "Adapter on the shared Khmer label engine; synthetic fixtures only.",
       "No measured extraction on real NSSF cards.", "Collect 100 consented NSSF captures; confirm whether the last number digit is a check digit."),
    _c("KH Passport", True, "NOT_REAL_WORLD_TESTED", "YES", "YES", "NO", "NO", "PARTIAL",
       "Visual fields + TD3 MRZ engine; synthetic specimens only.", "No real passport tested.",
       "Test 30+ real Cambodian passports (data page) with consent."),
    _c("OCR (Latin)", True, "NOT_REAL_WORLD_TESTED", "YES", "YES", "PARTIAL", "NO", "PARTIAL",
       "Tesseract 5 full-image OCR with a constrained numeric re-read.", "No CER/WER benchmark.",
       "Run scripts on a labelled set; report CER/WER per field and per capture condition."),
    _c("Khmer OCR", True, "PARTIAL", "PARTIAL", "YES", "PARTIAL", "NO", "PARTIAL",
       "Full-page khm+eng OCR plus a Khmer-digit re-read. On 2 usable real KH IDs the Khmer name read at confidence "
       "0.37-0.38 and sex, address and issue date were not found. Fixed: the Khmer digit model was silently skipped in the "
       "container image (Debian names it Khmer, not script/Khmer).",
       "No per-field ROI cropping, multi-variant preprocessing or voting; Khmer name/address accuracy unmeasured.",
       "Add per-field ROIs for KH ID front, 2-3x upscale + CLAHE variants with --psm 7 and voting; measure CER on 200 cards.",
       ("Low-confidence Khmer fields go to recapture/review, never PASS.",)),
    _c("MRZ", True, "NOT_REAL_WORLD_TESTED", "YES", "YES", "PARTIAL", "N/A", "PARTIAL",
       "TD1/TD2/TD3 parsing, all check digits, visual-zone comparison; dedicated MRZ OCR pass.",
       "Real-card MRZ read rate unmeasured (1 of 2 real cards failed check digits).",
       "Measure MRZ read rate on the labelled KH ID set; tune the MRZ band crop."),
    _c("QR/Barcode", False, "NOT_REAL_WORLD_TESTED", "YES", "YES", "NO", "N/A", "PARTIAL",
       "zxing decode, JWS/JSON/AAMVA/ICAO VDS parsing; signatures only against BARCODE_TRUST_STORE.",
       None, "Decode the NSSF 'VERIFY gov.kh' QR on real cards and document what it contains.",
       ("A decoded QR never counts as authenticity without a trusted signature.",)),
    _c("Document Authenticity", True, "PARTIAL", "PARTIAL", "YES", "NO", "N/A", "PARTIAL",
       "Authenticity accepted only from a verified chip or signed barcode; font, layout, text-region and pixel "
       "forensics are reported NOT_SUPPORTED; unverified documents go to REVIEW.",
       "No forensic detectors for Cambodian cards, so KH ID/NSSF can never auto-PASS.",
       "Decide the operating model: human review of authenticity, or an evaluated forensics vendor/model.",
       ("Every KH ID/NSSF session ends in MANUAL_REVIEW at best.",)),
    _c("Portrait Extraction", True, "NOT_REAL_WORLD_TESTED", "YES", "YES", "NO", "NO", "PARTIAL",
       "Portrait located by face detection with DOCUMENT_PORTRAIT quality gate; unusable portraits trigger recapture.",
       "Not measured on real cards.", "Measure portrait detection rate on the labelled KH ID set."),
    _c("Face Detection", True, "NOT_REAL_WORLD_TESTED", "YES", "YES", "PARTIAL", "N/A", "PARTIAL",
       "OpenCV YuNet 2023mar with pinned hashes; selfie quality gate.", "No real-device detection benchmark.",
       "Measure detection and recapture rates on pilot selfies (Android + iPhone)."),
    _c("Face Match", True, "UNCALIBRATED", "YES", "YES", "NO", "NO", "PARTIAL",
       "SFace 2021dec 1:1 cosine; uncalibrated policy always REVIEW; calibration framework (FAR/FRR/TAR/AUC, "
       "distributions) added; production refuses to start without a measured report.",
       "Face calibration missing.", "Collect consented genuine/impostor pairs (100+ / 1000+ minimum) and run scripts/calibrate_face.py.",
       ("Face match uses the uploaded selfie; liveness frames are bound to it, but the best liveness frame is not yet the "
        "comparison source.",)),
    _c("Liveness", True, "UNCALIBRATED", "YES", "YES", "PARTIAL", "NO", "PARTIAL",
       "Random single-use 4-step head-movement challenge, landmark geometry, replay and identity-continuity checks; "
       "uncalibrated policy is REVIEW at best; validation framework per attack type added.",
       "Liveness not validated against presentation attacks.",
       "Run printed-photo, phone-screen and video-replay attacks (20+ each) and 50+ genuine sessions; run scripts/calibrate_liveness.py.",
       ("3D masks, AI-generated media and virtual-camera injection are NOT_SUPPORTED.",)),
    _c("Android Mobile", True, "NOT_IMPLEMENTED", "NO", "NO", "NO", "N/A", "NO",
       "Only the development web capture page and the Verix front-end prototype exist; no native app or mobile SDK.",
       "No validated mobile capture.", "Build the mobile web flow or SDK and test on 5+ Android models."),
    _c("iPhone Mobile", True, "NOT_IMPLEMENTED", "NO", "NO", "NO", "N/A", "NO",
       "As Android.", "No validated mobile capture.", "Test on 3+ iPhone models (Safari camera permissions)."),
    _c("QR Handoff", True, "PARTIAL", "PARTIAL", "PARTIAL", "NO", "N/A", "PARTIAL",
       "Single-session client tokens (kst_) exist; no desktop-to-phone QR handoff screen, claim-once or live status channel.",
       "Spec QR handoff (QR_ALREADY_USED, MOBILE_SESSION_ALREADY_CLAIMED, desktop live status) not built.",
       "Add a handoff token endpoint with single claim and a status stream."),
    _c("NFC Android", False, "MOCK_ONLY", "NO", "PARTIAL", "NO", "N/A", "NO",
       "Server verification exists; the chip reader is only scripts/nfc_simulator.py.", None,
       "Keep NFC_ENABLED=false until a mobile reader is built and physically tested."),
    _c("NFC iOS", False, "MOCK_ONLY", "NO", "PARTIAL", "NO", "N/A", "NO", "As Android.", None,
       "Keep NFC_ENABLED=false until physically tested."),
    _c("CSCA", False, "NOT_IMPLEMENTED", "PARTIAL", "YES", "NO", "N/A", "PARTIAL",
       "Trust store loads a CSCA folder and checks validity windows; no ICAO PKD/master-list import, update process or "
       "revocation (CRL) checking. Unknown issuers stay REVIEW.", None,
       "Define the CSCA source and update process before enabling NFC."),
    _c("Fraud", True, "PARTIAL", "YES", "YES", "NO", "N/A", "PARTIAL",
       "Duplicate/replay/velocity within the tenant, MRZ/barcode/chip mismatches, metadata markers; forensic detectors "
       "NOT_SUPPORTED and reported as such.", "No screen/print reproduction (moire) detection.",
       "Measure signal precision during the pilot; add screen-replay detection."),
    _c("Risk Engine", True, "COMPLETE", "YES", "YES", "PARTIAL", "N/A", "PARTIAL",
       "Deterministic, versioned, tighten-only policy; FAIL > REVIEW > PASS; trace stored; uncalibrated face PASS now "
       "REVIEW; determinism test added.", "Depends on calibrated inputs before any auto-PASS.",
       "Pilot with 100% human review; compare reviewer and engine decisions."),
    _c("Manual Review", True, "COMPLETE", "YES", "YES", "NO", "N/A", "PARTIAL",
       "Separate reviewer tokens, REVIEWER/AUDITOR roles, reason codes, optimistic concurrency (409 CASE_CHANGED), "
       "approval blockers, audited views; case now shows check results, summary and timeline.",
       "Not used by real reviewers yet.", "Run reviewer training on pilot cases."),
    _c("RLS", True, "COMPLETE", "YES", "YES", "PARTIAL", "N/A", "YES",
       "Forced RLS on 25 tenant tables; live PostgreSQL isolation tests and scripts/security_check.py (24/24).",
       "Not verified on the production Cloud SQL instance.", "Run scripts/security_check.py against production before launch."),
    _c("Encryption", True, "PARTIAL", "YES", "YES", "NO", "N/A", "PARTIAL",
       "AES-256-GCM per data class with separate keyrings and rotation; keys come from environment secrets.",
       "No Cloud KMS; production key management unverified.", "Move keyrings to Cloud KMS envelope encryption (Phase 19)."),
    _c("Webhooks", False, "COMPLETE", "YES", "YES", "NO", "N/A", "YES",
       "Transactional outbox, HMAC-SHA256 with timestamp, retries, SSRF re-check per delivery.", None,
       "Test retries against a real receiver during the pilot."),
    _c("Workers", False, "PERFORMANCE_RISK", "YES", "YES", "PARTIAL", "N/A", "PARTIAL",
       "SKIP LOCKED document worker and webhook worker; OCR is the capacity limit (~24 docs/min on a laptop).", None,
       "Re-measure on Cloud Run sizes; add worker backlog metrics."),
    _c("GCP", True, "NOT_IMPLEMENTED", "NO", "NO", "NO", "N/A", "NO",
       "Phase 19 not started; Dockerfile exists but the image build was never verified.", "No production deployment.",
       "Build and scan the image; provision Cloud Run, Cloud SQL (private IP), GCS, KMS, Secret Manager."),
    _c("Monitoring", True, "PARTIAL", "YES", "YES", "NO", "N/A", "PARTIAL",
       "Dependency health (/health/ready, /health/dependencies), Prometheus /metrics, alert rules (infra/monitoring).",
       "Not connected to a monitoring system; queue/webhook backlog, KMS and certificate alerts missing.",
       "Wire /metrics into Cloud Monitoring/Prometheus and route alerts to on-call."),
    _c("Backup", True, "NOT_IMPLEMENTED", "NO", "NO", "NO", "N/A", "NO",
       "No backup configuration in the repository.", "Database backup not configured.",
       "Enable Cloud SQL automated backups + PITR; define capture-bucket retention."),
    _c("Restore", True, "NOT_IMPLEMENTED", "NO", "NO", "NO", "N/A", "NO",
       "Restore never tested.", "Restore never tested.", "Run and document a timed restore drill."),
    _c("Repository Data Hygiene", True, "SECURITY_RISK", "N/A", "NO", "N/A", "N/A", "NO",
       "The public repository contains real identity-card photos (ID/KhmerID, ID/NSSF) and personal data fragments in "
       "BUILD_PROGRESS.md.", "Real personal data is published in a public repository.",
       "Make the repository private, remove the files and purge them from git history; keep test data outside the repo."),
)


def classify(components=COMPONENTS) -> str:
    critical = [item for item in components if item.critical]
    if any(item.status in BLOCKING for item in critical):
        return "NOT_READY"
    if any(item.status in BLOCKING for item in components):
        return "READY_WITH_LIMITATIONS"
    return "READY"


def runtime_facts(settings) -> dict:
    """What this deployment's configuration proves (never upgrades a status by itself)."""
    return {"face_calibration_report": settings.face_match_calibration_file is not None,
            "liveness_validation_report": settings.liveness_validation_file is not None,
            "nfc_enabled": settings.nfc_enabled,
            "csca_trust_store": settings.nfc_csca_trust_store is not None,
            "environment": settings.environment}


def report(settings=None) -> dict:
    components = [asdict(item) for item in COMPONENTS]
    return {"overall": classify(), "facts": runtime_facts(settings) if settings is not None else None,
            "blockers": [{"component": item.name, "blocker": item.blocker} for item in COMPONENTS
                         if item.critical and item.status in BLOCKING],
            "components": components}
