"""SDK transport checks use real loopback HTTP and never send a credential externally."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
from kyc_sdk import KYCAPIError, KYCClient, SessionClient  # noqa: E402


class SDKTransportSecurityTests(unittest.TestCase):
    def test_remote_plaintext_and_credential_bearing_urls_are_refused(self):
        for url in ("http://api.example.com", "http://localhost.evil.example", "https://user:password@api.example.com",
                    "https://api.example.com?token=secret", "https://api.example.com#secret", "file:///tmp/api"):
            for client in (KYCClient, SessionClient):
                with self.subTest(url=url, client=client.__name__), self.assertRaises(ValueError):
                    client(url, "synthetic-credential", "organization")
        for url in ("https://api.example.com", "http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"):
            KYCClient(url, "synthetic-credential", "organization")

    def test_redirects_cannot_forward_credentials_or_document_data(self):
        requests = []

        class Receiver(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("X-API-Key")))
                self.send_response(302)
                self.send_header("Location", "/credential-sink")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = KYCClient(f"http://127.0.0.1:{server.server_port}", "synthetic-api-credential", "organization")
            with self.assertRaises(KYCAPIError) as raised:
                client.document_types()
            self.assertEqual(raised.exception.status, 302)
            self.assertEqual(requests, [("/v1/document-types", "synthetic-api-credential")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
