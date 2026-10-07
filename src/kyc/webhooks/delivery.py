"""Webhook target validation and an SSRF-safe HTTP sender.

A webhook URL is chosen by a customer, so the platform must not become a proxy into its
own network. Every delivery resolves the host, refuses any non-public address (loopback,
private, link-local such as the cloud metadata service, multicast, reserved), then
connects to that exact address, so DNS cannot change between the check and the request.
TLS still verifies the certificate against the hostname. Redirects are not followed.
"""

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048
RESPONSE_READ_LIMIT = 1024


class TargetNotAllowed(ValueError):
    """The URL is malformed or resolves to an address deliveries must not reach."""


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    path: str
    address: str


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, allow_private: bool = False) -> tuple[str, str, int, str]:
    """Syntax checks only: (scheme, host, port, path-with-query)."""
    if len(url) > MAX_URL_LENGTH:
        raise TargetNotAllowed("URL is too long.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise TargetNotAllowed("URL is not valid.") from None
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise TargetNotAllowed("Webhook URLs must use https.")
    if not parts.hostname or parts.username or parts.password or parts.fragment:
        raise TargetNotAllowed("Use a URL with a host and without credentials or a fragment.")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return parts.scheme, parts.hostname, port or (443 if parts.scheme == "https" else 80), path


def resolve(url: str, allow_private: bool = False) -> Target:
    scheme, host, port, path = check_url(url, allow_private)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise TargetNotAllowed("The webhook host does not resolve.") from None
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise TargetNotAllowed("The webhook host does not resolve.")
    if not allow_private and not all(_public(address) for address in addresses):
        # Every address must be public, or a round-robin record could smuggle in an internal one.
        raise TargetNotAllowed("The webhook host resolves to a non-public address.")
    return Target(scheme, host, port, path, addresses[0])


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, target: Target, timeout: float):
        super().__init__(target.host, target.port, timeout=timeout)
        self._address = target.address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, target: Target, timeout: float, context: ssl.SSLContext):
        super().__init__(target.host, target.port, timeout=timeout, context=context)
        self._address = target.address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


@dataclass(frozen=True)
class SendResult:
    status_code: int | None
    error: str | None   # a short code, never the response body

    @property
    def delivered(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


class HTTPSender:
    """POSTs a signed body to a validated, pinned address."""

    def __init__(self, timeout: float = 10.0, allow_private: bool = False, context: ssl.SSLContext | None = None):
        self.timeout = timeout
        self.allow_private = allow_private
        self.context = context or ssl.create_default_context()

    def send(self, url: str, body: bytes, headers: dict[str, str]) -> SendResult:
        try:
            target = resolve(url, self.allow_private)
        except TargetNotAllowed:
            return SendResult(None, "TARGET_NOT_ALLOWED")
        connection = (_PinnedHTTPSConnection(target, self.timeout, self.context) if target.scheme == "https"
                      else _PinnedHTTPConnection(target, self.timeout))
        try:
            connection.request("POST", target.path, body=body, headers=headers)
            response = connection.getresponse()
            response.read(RESPONSE_READ_LIMIT)
            return SendResult(response.status, None if 200 <= response.status < 300 else f"HTTP_{response.status}")
        except TimeoutError:
            return SendResult(None, "TIMEOUT")
        except ssl.SSLError:
            return SendResult(None, "TLS_ERROR")
        except (OSError, http.client.HTTPException):
            return SendResult(None, "CONNECTION_ERROR")
        finally:
            connection.close()
