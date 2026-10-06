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
}
document["security"] = [{"ApiKey": [], "Organization": []}, {"SessionClientToken": [], "Organization": []}]
output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
print(f"Wrote {output} ({len(document['paths'])} paths).")
