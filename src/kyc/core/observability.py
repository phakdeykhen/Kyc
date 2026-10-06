"""Phase 17 logging: secrets are redacted before any record is written, and refused
requests become structured security events.

Security events carry the request ID, route template, client address, valid organization ID and a
credential *type*,
never a credential, identity value or request body.
"""

import json
import ipaddress
import logging
from pathlib import Path
import re
import sys
import traceback
from datetime import datetime, timezone
from uuid import UUID

SECURITY_LOGGER = "kyc.security"
_PATTERNS = (
    # Platform credentials and webhook secrets: keep the type, drop the secret.
    (re.compile(r"\b(kyc|kst|rvw|whsec)_[A-Za-z0-9_\-]{8,}"), r"\1_[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"), "Bearer [REDACTED]"),
    # Passwords inside database URLs.
    (re.compile(r"(\w+(?:\+\w+)?://[^:/@\s]+:)[^@\s]+@"), r"\1[REDACTED]@"),
    # A base64-encoded 32-byte key, as used by every keyring.
    (re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{43}=(?![A-Za-z0-9+/=])"), "[REDACTED_KEY]"),
)
_handler = None


def route_label(request) -> str:
    """Only application-defined templates; raw paths can hold identity values or tokens."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "<unmatched>"


def exception_summary(exc_info) -> str:
    """Keep diagnostic frames without exception messages, source lines or SQL bind values."""
    exception_type, _, trace = exc_info
    frames = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
              for frame in traceback.extract_tb(trace)]
    return json.dumps({"type": exception_type.__name__, "frames": frames}, separators=(",", ":"))


def _redact_values(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact(str(key)): _redact_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_values(item) for item in value]
    return value


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Renders the message once, redacted, so no handler or formatter sees the raw arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        # A downstream handler must never render the original exception or cached traceback.
        if record.exc_info:
            record.exc_text = exception_summary(record.exc_info)
        elif not getattr(record, "_kyc_exception_sanitized", False):
            record.exc_text = None
        record.exc_info = None
        record.stack_info = None
        record._kyc_exception_sanitized = True
        if hasattr(record, "security"):
            record.security = _redact_values(record.security)
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(), "severity": record.levelname,
                 "logger": record.name, "message": redact(record.getMessage())}
        event = getattr(record, "security", None)
        if event:
            entry["security"] = _redact_values(event)
        if record.exc_info:
            entry["exception"] = exception_summary(record.exc_info)
        elif record.exc_text and getattr(record, "_kyc_exception_sanitized", False):
            entry["exception"] = redact(record.exc_text)
        return json.dumps(entry, default=str, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if record.exc_info:
            record.exc_text = exception_summary(record.exc_info)
            record._kyc_exception_sanitized = True
        elif not getattr(record, "_kyc_exception_sanitized", False):
            record.exc_text = None
        text = super().format(record)
        event = getattr(record, "security", None)
        return redact(text + (" " + json.dumps(event, default=str, sort_keys=True) if event else ""))


def configure_logging(log_format: str) -> None:
    """Configure application and ASGI error logs; URL access logs can carry PII and tokens."""
    global _handler
    handler = _handler or logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter() if log_format == "json" else TextFormatter())
    if _handler is None:
        handler.addFilter(RedactingFilter())
    for name in ("kyc", "uvicorn", "uvicorn.error"):
        root = logging.getLogger(name)
        root.handlers = [handler]
        root.setLevel(logging.INFO)
        root.propagate = False
    logging.getLogger("uvicorn.access").disabled = True
    _handler = handler


def credential_label(headers) -> str | None:
    """What kind of credential was presented, without any secret part of it."""
    api_key = headers.get("x-api-key") or ""
    bearer = (headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if api_key.startswith("kyc_"):
        return "api_key"
    if api_key:
        return "development_key"
    for prefix, label in (("kst_", "session_client_token"), ("rvw_", "reviewer_token")):
        if bearer.startswith(prefix):
            return label
    return "bearer" if bearer else None


def security_event(request, status: int, reason: str | None) -> None:
    try:
        organization = str(UUID(request.headers.get("x-organization-id") or ""))
    except ValueError:
        organization = None
    try:
        client = str(ipaddress.ip_address(request.client.host)) if request.client else None
    except ValueError:
        client = None
    event = reason if reason and re.fullmatch(r"[A-Z0-9_]{1,80}", reason) else f"HTTP_{status}"
    method = request.method if request.method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"} else "OTHER"
    logging.getLogger(SECURITY_LOGGER).warning(
        "request refused", extra={"security": {
            "event": event, "status": status, "request_id": str(request.state.request_id),
            "method": method, "path": route_label(request),
            "client": client,
            "organization_id": organization, "credential": credential_label(request.headers)}})
