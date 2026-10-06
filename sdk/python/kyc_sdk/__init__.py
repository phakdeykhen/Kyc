"""Python SDK for the Universal Identity Platform KYC API (Phase 16)."""

from kyc_sdk.client import KYCAPIError, KYCClient, SessionClient, urllib_transport
from kyc_sdk.webhooks import SIGNATURE_HEADER, WebhookVerificationError, verify_webhook

__all__ = ["KYCAPIError", "KYCClient", "SessionClient", "SIGNATURE_HEADER", "WebhookVerificationError",
           "urllib_transport", "verify_webhook"]
__version__ = "0.16.0"
