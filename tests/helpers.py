"""In-process ASGI HTTP transport. No listening socket or HTTP client dependency."""

import asyncio
import json
from uuid import uuid4


def multipart(fields=None, files=None):
    """Encode form fields and (name, filename, content_type, bytes) files."""
    boundary = uuid4().hex
    parts = []
    for name, value in (fields or {}).items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, filename, content_type, data in files or []:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


async def call(app, path, method="GET", body=None, headers=None, raw=None, content_type="application/json"):
    payload = raw if raw is not None else json.dumps(body).encode() if body is not None else b""
    incoming_headers = {"host": "test", "content-type": content_type, "content-length": str(len(payload)), **(headers or {})}
    path, _, query = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": query.encode(), "root_path": "",
             "headers": [(name.lower().encode(), value.encode()) for name, value in incoming_headers.items()],
             "client": ("127.0.0.1", 1234), "server": ("test", 80)}
    sent = []
    received = False
    finished = asyncio.Event()

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            finished.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=120)  # includes background OCR
    start = next(message for message in sent if message["type"] == "http.response.start")
    data = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    response_headers = {name.decode(): value.decode() for name, value in start["headers"]}
    parsed = json.loads(data) if response_headers.get("content-type", "").startswith("application/json") else data
    return start["status"], parsed, response_headers
