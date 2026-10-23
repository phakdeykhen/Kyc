"""Backend-only hosted KYC integration. No API secret is sent to the user's device.

Install the existing Python transport package: pip install -e sdk/python
See docs/integration-service.md for environment variables and trust boundaries.
"""
import os
from uuid import UUID, uuid4

from kyc_sdk.client import KYCClient


class IntegrationBackend:
    def __init__(self):
        self.client = KYCClient(os.environ["KYC_BASE_URL"], api_key=os.environ["KYC_API_KEY"],
                                organization_id=os.environ["KYC_TENANT_ID"])

    def create_verification(self, authenticated_subject_id, reference, workflow_id, return_url, locale="en", idempotency_key=None):
        """Obtain subject/reference from the client's authenticated business record, not an anonymous form."""
        return self.client._request("POST", "/v1/verification-sessions", {
            "external_subject_id": authenticated_subject_id, "reference": reference, "workflow_id": workflow_id,
            "return_url": return_url, "locale": locale}, headers={"Idempotency-Key": idempotency_key or str(uuid4())})

    def result(self, session_id):
        identifier = UUID(str(session_id))
        return self.client._request("GET", f"/v1/verification-sessions/{identifier}/result")


if __name__ == "__main__":
    backend = IntegrationBackend()
    created = backend.create_verification("SYNTHETIC-EMP-00123", "synthetic-onboarding-456", "staff-onboarding",
                                          os.environ["KYC_RETURN_URL"], "km")
    # Only this short-lived hosted URL goes to the browser. Store the opaque ID against your own record.
    print(created["hosted_verification_url"])
