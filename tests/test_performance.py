"""Phase 18: admission control, concurrent status reads and the load-test tool's statistics."""

import asyncio
import importlib.util
from pathlib import Path
import time
import unittest

from kyc.core.admission import POOL_RESERVE, concurrency_limit
from tests.helpers import call
from tests import test_api
from tests.test_api import configuration

ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("load_test", ROOT / "scripts" / "load_test.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConcurrencyLimitTests(unittest.TestCase):
    def test_default_leaves_a_pool_reserve(self):
        from uuid import uuid4
        settings = configuration(uuid4(), db_pool_size=5, db_max_overflow=5)
        self.assertEqual(concurrency_limit(settings), 10 - POOL_RESERVE)
        self.assertEqual(concurrency_limit(configuration(uuid4(), db_pool_size=1, db_max_overflow=0)), 1)
        self.assertEqual(concurrency_limit(configuration(uuid4(), max_concurrent_requests=24)), 24)


class AdmissionControlTests(unittest.IsolatedAsyncioTestCase):
    create = test_api.SessionAPITests.create
    asyncTearDown = test_api.SessionAPITests.asyncTearDown

    async def asyncSetUp(self):
        await test_api.SessionAPITests.asyncSetUp(self)
        self.release = asyncio.Event()

        async def slow():
            await self.release.wait()
            return {"done": True}
        self.app.add_api_route("/v1/slow", slow)
        self.app.state.settings.max_concurrent_requests = 1
        self.app.state.settings.request_queue_timeout_seconds = 0.2

    async def test_excess_requests_queue_then_get_503_with_retry_after(self):
        first = asyncio.create_task(call(self.app, "/v1/slow"))
        await asyncio.sleep(0.05)
        started = time.perf_counter()
        code, body, headers = await call(self.app, "/v1/document-types", headers=self.headers)
        self.assertEqual((code, body["reason_code"]), (503, "OVERLOADED"))
        self.assertEqual(headers["retry-after"], "1")
        self.assertIn("x-request-id", headers)
        self.assertGreaterEqual(time.perf_counter() - started, 0.18, "The request waited for a slot first")
        code, _, _ = await call(self.app, "/health/live")
        self.assertEqual(code, 200, "Health probes are never queued")
        self.release.set()
        code, body, _ = await first
        self.assertEqual((code, body), (200, {"done": True}))
        code, _, _ = await call(self.app, "/v1/document-types", headers=self.headers)
        self.assertEqual(code, 200, "The slot is returned after the request")

    async def test_a_queued_request_proceeds_when_a_slot_frees_in_time(self):
        self.app.state.settings.request_queue_timeout_seconds = 5
        first = asyncio.create_task(call(self.app, "/v1/slow"))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(call(self.app, "/v1/document-types", headers=self.headers))
        await asyncio.sleep(0.1)
        self.assertFalse(second.done(), "The second request waits instead of failing")
        self.release.set()
        self.assertEqual((await first)[0], 200)
        self.assertEqual((await second)[0], 200)

    async def test_queued_status_reads_all_succeed_in_turn(self):
        self.app.state.settings.request_queue_timeout_seconds = 10
        session = (await self.create())["session_id"]
        results = await asyncio.gather(*(call(self.app, f"/v1/kyc/{session}", headers=self.headers) for _ in range(20)))
        self.assertEqual({code for code, _, _ in results}, {200})
        self.assertEqual({body["version"] for _, body, _ in results}, {1}, "Reads never change the session")


class LoadToolTests(unittest.TestCase):
    def test_percentiles_and_summary(self):
        tool = load_tool()
        self.assertIsNone(tool.percentile([], .5))
        values = [float(item) for item in range(1, 101)]
        self.assertEqual((tool.percentile(values, .5), tool.percentile(values, .95), tool.percentile(values, 1)), (50, 95, 100))
        level = tool.Level(2, 10.0, [tool.Sample("status_poll", 200, .010), tool.Sample("status_poll", 200, .030),
                                     tool.Sample("result", 503, .050), tool.Sample("result", 0, 1.0)])
        summary = level.summary()
        self.assertEqual((summary["requests"], summary["throughput_rps"], summary["success_rate"]), (4, 0.4, 0.5))
        self.assertEqual(summary["statuses"], {"0": 1, "200": 2, "503": 1})
        self.assertEqual(summary["operations"]["status_poll"]["p50_ms"], 10.0)
