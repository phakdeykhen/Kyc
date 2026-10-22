"""Write the API's OpenAPI document to sdk/openapi.json (the contract for generated SDKs).

  PYTHONPATH=src python scripts/export_openapi.py [OUTPUT]
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kyc.main import create_app  # noqa: E402

root = Path(__file__).resolve().parents[1]
output = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "sdk" / "openapi.json"
document = create_app().openapi()
document["components"]["securitySchemes"] = {
    "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key", "description": "Server-side API key (kyc_…)."},
    "Organization": {"type": "apiKey", "in": "header", "name": "X-Organization-ID", "description": "Organization UUID; required on every request."},
    "SessionClientToken": {"type": "http", "scheme": "bearer", "description": "Session client token (kst_…) for capture and status of one session."},
    "ReviewerToken": {"type": "http", "scheme": "bearer", "description": "Expiring reviewer token (rvw_…), separate from client credentials."},
}
server_security = {"ApiKey": [], "Organization": []}
device_security = {"SessionClientToken": [], "Organization": []}
document["security"] = [server_security]
capture_suffixes = {"", "/government-verification", "/consent", "/documents", "/documents/front", "/documents/back", "/selfie",
                    "/liveness", "/liveness/position", "/liveness/guide", "/liveness/challenge", "/nfc", "/nfc/challenge"}
for path, operations in document["paths"].items():
    for method, operation in operations.items():
        if method not in {"get", "post", "patch", "delete", "put", "head", "options", "trace"}:
            continue
        if path.startswith("/health/"):
            operation["security"] = []
        elif path.startswith("/v1/review/"):
            operation["security"] = [{"ReviewerToken": [], "Organization": []}]
        elif path.startswith("/v1/kyc/{session_id}") and path.removeprefix("/v1/kyc/{session_id}") in capture_suffixes:
            operation["security"] = [server_security, device_security]
output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
print(f"Wrote {output} ({len(document['paths'])} paths).")
