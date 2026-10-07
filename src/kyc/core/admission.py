"""Per-process admission control for API requests (Phase 18).

Load testing found that with more requests in flight than Starlette's worker threads (40),
requests holding a pooled database connection waited for a thread while every thread
waited for a connection. Nothing progressed until the 10 s pool timeout, and the process
answered 503 at ~5 requests/s.

This middleware admits at most MAX_CONCURRENT_REQUESTS API requests at once (by default the
connection pool's capacity minus a reserve for the webhook dispatcher and audit writes) and
lets the rest wait cheaply on the event loop. A permit is held until the application call
returns, which includes background work such as inline document processing. Requests that
wait longer than REQUEST_QUEUE_TIMEOUT_SECONDS get 503 with Retry-After, which clients and
load balancers retry, instead of piling up.
"""

import asyncio
import json
from uuid import uuid4

# Connections kept free for the webhook dispatcher threads and out-of-transaction audit writes.
POOL_RESERVE = 3
ADMITTED_PREFIXES = ("/v1/",)


def concurrency_limit(settings) -> int:
    if settings.max_concurrent_requests:
        return settings.max_concurrent_requests
    return max(1, settings.db_pool_size + settings.db_max_overflow - POOL_RESERVE)


class AdmissionControl:
    def __init__(self, app):
        self.app = app
        self.semaphore: asyncio.Semaphore | None = None
        self.limit = 0
        self.waiting = 0

    def _ready(self, scope) -> asyncio.Semaphore | None:
        settings = scope["app"].state.settings if "app" in scope else None
        if settings is None:
            return None
        limit = concurrency_limit(settings)
        if self.semaphore is None or limit != self.limit:
            self.semaphore, self.limit = asyncio.Semaphore(limit), limit
        return self.semaphore

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(ADMITTED_PREFIXES):
            return await self.app(scope, receive, send)
        semaphore = self._ready(scope)
        if semaphore is None:
            return await self.app(scope, receive, send)
        timeout = scope["app"].state.settings.request_queue_timeout_seconds
        self.waiting += 1
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout)
        except TimeoutError:
            return await self._busy(send)
        finally:
            self.waiting -= 1
        try:
            await self.app(scope, receive, send)
        finally:
            semaphore.release()

    @staticmethod
    async def _busy(send) -> None:
        request_id = str(uuid4())
        body = json.dumps({"detail": "The service is busy; retry shortly.", "reason_code": "OVERLOADED",
                           "request_id": request_id}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                   (b"retry-after", b"1"), (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                   (b"x-request-id", request_id.encode()),
                   (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")]
        await send({"type": "http.response.start", "status": 503, "headers": headers})
        await send({"type": "http.response.body", "body": body})
