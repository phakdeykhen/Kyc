"""HTTP client for the KYC API. Standard library only.

    client = KYCClient("https://kyc.example.com", api_key="kyc_…", organization_id="…")
    session = client.create_session("customer-42", "KH", "KH_NATIONAL_ID", idempotency_key="signup-42")
    token = client.issue_client_token(session["session_id"])["client_token"]   # hand to the device
    ...
    result = client.get_result(session["session_id"])

`KYCClient` is for your server (API key). `SessionClient` is what a device or kiosk
holds: a session client token that can only capture evidence for one session.
"""

from collections.abc import Callable
import json
from typing import Any
import urllib.error
import urllib.request
from uuid import uuid4

# (method, url, headers, body) -> (status, headers, body)
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]
USER_AGENT = "kyc-sdk-python/0.16.0"


class KYCAPIError(Exception):
    def __init__(self, status: int, body: Any, request_id: str | None):
        detail = body.get("detail") if isinstance(body, dict) else body
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.body, self.request_id = status, body, request_id
        self.retry_after = None


def urllib_transport(timeout: float = 30.0) -> Transport:
    def send(method, url, headers, body):
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
        except urllib.error.HTTPError as error:
            return error.code, {k.lower(): v for k, v in error.headers.items()}, error.read()
    return send


def _multipart(fields: dict[str, str], files: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    boundary = uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
             for name, value in fields.items()]
    for name, filename, content_type, data in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


class _Base:
    def __init__(self, base_url: str, organization_id: str, credential_headers: dict[str, str],
                 transport: Transport | None = None):
        self.base_url = base_url.rstrip("/")
        self._headers = {"X-Organization-ID": str(organization_id), "User-Agent": USER_AGENT, **credential_headers}
        self._transport = transport or urllib_transport()

    def _request(self, method: str, path: str, json_body: Any = None, form: tuple[bytes, str] | None = None,
                 headers: dict[str, str] | None = None, raw_response: bool = False):
        body, extra = None, {}
        if form is not None:
            body, extra = form[0], {"Content-Type": form[1]}
        elif json_body is not None or method in ("POST", "PATCH"):
            body, extra = json.dumps(json_body if json_body is not None else {}).encode(), {"Content-Type": "application/json"}
        if body is not None:
            extra["Content-Length"] = str(len(body))
        status, response_headers, data = self._transport(method, self.base_url + path,
                                                         {**self._headers, **extra, **(headers or {})}, body)
        parsed = json.loads(data) if data and response_headers.get("content-type", "").startswith("application/json") else data
        if status >= 400:
            error = KYCAPIError(status, parsed, response_headers.get("x-request-id"))
            error.retry_after = response_headers.get("retry-after")
            raise error
        return (parsed, response_headers) if raw_response else parsed

    # Capture steps, shared by server and device clients.
    def get_session(self, session_id: str) -> dict:
        return self._request("GET", f"/v1/kyc/{session_id}")

    def upload_document(self, session_id: str, side: str, image: bytes, filename: str = "document.jpg",
                        content_type: str = "image/jpeg") -> dict:
        """side: FRONT, BACK or DATA_PAGE (passports)."""
        return self._request("POST", f"/v1/kyc/{session_id}/documents",
                             form=_multipart({"side": side}, [("file", filename, content_type, image)]))

    def upload_selfie(self, session_id: str, image: bytes, biometric_consent: bool, filename: str = "selfie.jpg",
                      content_type: str = "image/jpeg") -> dict:
        """Only send biometric_consent=True after the person has actually agreed."""
        return self._request("POST", f"/v1/kyc/{session_id}/selfie", form=_multipart(
            {"biometric_consent": "true" if biometric_consent else "false"}, [("file", filename, content_type, image)]))

    def liveness_challenge(self, session_id: str) -> dict:
        return self._request("POST", f"/v1/kyc/{session_id}/liveness/challenge")

    def submit_liveness(self, session_id: str, challenge_id: str, nonce: str, frames: list[tuple[bytes, int]]) -> dict:
        """frames: (raw unmirrored JPEG, step index) for each captured frame."""
        files = [("frames", f"frame-{index}.jpg", "image/jpeg", data) for index, (data, _) in enumerate(frames)]
        fields = {"challenge_id": challenge_id, "nonce": nonce, "frame_steps": ",".join(str(step) for _, step in frames)}
        return self._request("POST", f"/v1/kyc/{session_id}/liveness", form=_multipart(fields, files))

    def nfc_challenge(self, session_id: str) -> dict:
        return self._request("POST", f"/v1/kyc/{session_id}/nfc/challenge")

    def submit_nfc(self, session_id: str, read_status: str = "READ", access_protocol: str | None = None,
                   challenge_id: str | None = None, aa_signature: bytes | None = None,
                   data_groups: dict[str, bytes] | None = None) -> dict:
        """data_groups keys: sod, dg1, dg2, dg15 — raw bytes as read from the chip."""
        fields = {"read_status": read_status}
        if access_protocol:
            fields["access_protocol"] = access_protocol
        if challenge_id:
            fields["challenge_id"] = challenge_id
        if aa_signature:
            fields["aa_signature"] = aa_signature.hex()
        files = [(name, f"{name}.bin", "application/octet-stream", data) for name, data in (data_groups or {}).items()]
        return self._request("POST", f"/v1/kyc/{session_id}/nfc", form=_multipart(fields, files))


class SessionClient(_Base):
    """Device-side client holding a session client token (never an API key)."""

    def __init__(self, base_url: str, client_token: str, organization_id: str, transport: Transport | None = None):
        super().__init__(base_url, organization_id, {"Authorization": f"Bearer {client_token}"}, transport)


class KYCClient(_Base):
    """Server-side client holding an API key."""

    def __init__(self, base_url: str, api_key: str, organization_id: str, transport: Transport | None = None):
        super().__init__(base_url, organization_id, {"X-API-Key": api_key}, transport)

    def create_session(self, user_id: str, country: str, expected_document_type: str,
                       verification_level: str = "DOCUMENT_FACE_LIVENESS", idempotency_key: str | None = None) -> dict:
        """Pass an idempotency_key (8–128 chars) so that a retry after a timeout cannot create a duplicate."""
        body = {"user_id": user_id, "country": country, "expected_document_type": expected_document_type,
                "verification_level": verification_level}
        return self._request("POST", "/v1/kyc/sessions", body,
                             headers={"Idempotency-Key": idempotency_key} if idempotency_key else None)

    def issue_client_token(self, session_id: str) -> dict:
        return self._request("POST", f"/v1/kyc/{session_id}/client-token")

    def get_result(self, session_id: str) -> dict:
        return self._request("GET", f"/v1/kyc/{session_id}/result")

    def verify(self, session_id: str) -> dict:
        return self._request("POST", f"/v1/kyc/{session_id}/verify")

    def organization(self) -> dict:
        return self._request("GET", "/v1/organization")

    def document_types(self) -> dict:
        return self._request("GET", "/v1/document-types")

    def countries(self) -> dict:
        return self._request("GET", "/v1/countries")

    # API keys (keys:manage)
    def list_api_keys(self) -> list:
        return self._request("GET", "/v1/api-keys")

    def create_api_key(self, name: str, scopes: list[str], expires_in_days: int | None = None,
                       rate_limit_per_minute: int | None = None) -> dict:
        body = {"name": name, "scopes": scopes, "expires_in_days": expires_in_days,
                "rate_limit_per_minute": rate_limit_per_minute}
        return self._request("POST", "/v1/api-keys", {key: value for key, value in body.items() if value is not None})

    def revoke_api_key(self, key_id: str) -> dict:
        return self._request("DELETE", f"/v1/api-keys/{key_id}")

    # Webhooks (webhooks:manage)
    def create_webhook(self, url: str, event_types: list[str] | None = None, description: str | None = None) -> dict:
        """The response carries `secret` once; store it to verify signatures."""
        body = {"url": url, "event_types": event_types or []}
        if description:
            body["description"] = description
        return self._request("POST", "/v1/webhooks", body)

    def list_webhooks(self, include_deleted: bool = False) -> list:
        return self._request("GET", "/v1/webhooks" + ("?include_deleted=true" if include_deleted else ""))

    def get_webhook(self, endpoint_id: str) -> dict:
        return self._request("GET", f"/v1/webhooks/{endpoint_id}")

    def update_webhook(self, endpoint_id: str, **changes) -> dict:
        return self._request("PATCH", f"/v1/webhooks/{endpoint_id}", changes)

    def delete_webhook(self, endpoint_id: str) -> dict:
        return self._request("DELETE", f"/v1/webhooks/{endpoint_id}")

    def rotate_webhook_secret(self, endpoint_id: str) -> dict:
        return self._request("POST", f"/v1/webhooks/{endpoint_id}/rotate-secret")

    def test_webhook(self, endpoint_id: str) -> dict:
        return self._request("POST", f"/v1/webhooks/{endpoint_id}/test")

    def list_deliveries(self, endpoint_id: str, status: str | None = None, limit: int = 25) -> list:
        query = f"?limit={limit}" + (f"&status={status}" if status else "")
        return self._request("GET", f"/v1/webhooks/{endpoint_id}/deliveries{query}")

    def redeliver(self, endpoint_id: str, delivery_id: str) -> dict:
        return self._request("POST", f"/v1/webhooks/{endpoint_id}/deliveries/{delivery_id}/redeliver")
