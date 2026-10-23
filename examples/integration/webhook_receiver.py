"""Runnable receiver: raw-body verification, rotation, durable deduplication and monotonic versions.

KYC_WEBHOOK_SECRETS is a comma-separated list (new secret, previous secret during overlap).
KYC_APPLICATION_ID and KYC_TENANT_ID must identify the configured application subscription.
Run: PYTHONPATH=examples/integration:sdk/python uvicorn webhook_receiver:app

This example stores notifications/results only; it performs no business authorization.
"""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from kyc_sdk.webhooks import verify_webhook, WebhookVerificationError
from backend import IntegrationBackend

app = FastAPI(title="Example integration webhook consumer")


@contextmanager
def connect(path=None):
    path = path or os.environ.get("KYC_RECEIVER_DATABASE", "var/integration_receiver.sqlite")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS processed_events (tenant TEXT, application TEXT, event_id TEXT, PRIMARY KEY (tenant, application, event_id))")
    db.execute("CREATE TABLE IF NOT EXISTS results (tenant TEXT, application TEXT, session_id TEXT, version INTEGER, outcome TEXT, sandbox INTEGER, PRIMARY KEY (tenant, application, session_id))")
    try:
        yield db
    finally:
        db.close()


def accept_result(db, event, result):
    """The insert and version comparison commit together. Duplicate or older results cannot overwrite state."""
    tenant, application = event["organization_id"], event["data"]["application_id"]
    data = event["data"]
    if (str(result["session_id"]) != data["session_id"] or type(result["result_version"]) is not int
            or result["result_version"] < data["result_version"]):
        raise ValueError("Authoritative result is behind this notification; retry later.")
    if result["outcome"] not in {"VERIFIED", "MANUAL_REVIEW", "REJECTED", "EXPIRED", "TECHNICAL_ERROR"}:
        raise ValueError("Authoritative result is not available yet.")
    if type(result["sandbox"]) is not bool or result["sandbox"] != data["sandbox"]:
        raise ValueError("Sandbox/production mismatch.")
    with db:
        inserted = db.execute("INSERT OR IGNORE INTO processed_events VALUES (?, ?, ?)",
                              (tenant, application, event["id"])).rowcount
        if not inserted:
            return False
        db.execute("""INSERT INTO results VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant, application, session_id) DO UPDATE SET
              version=excluded.version, outcome=excluded.outcome, sandbox=excluded.sandbox
            WHERE excluded.version > results.version""",
            (tenant, application, data["session_id"], result["result_version"], result["outcome"], result["sandbox"]))
    return True


@app.post("/webhooks/kyc")
async def receive(request: Request):
    # Never parse and re-serialize JSON before checking the signature.
    incoming = bytearray()
    async for chunk in request.stream():
        incoming.extend(chunk)
        if len(incoming) > 64 * 1024:
            raise HTTPException(413)
    body = bytes(incoming)
    event = None
    for secret in os.environ["KYC_WEBHOOK_SECRETS"].split(","):
        try:
            event = verify_webhook(body, request.headers.get("KYC-Signature", ""), secret.strip())
            break
        except (WebhookVerificationError, ValueError):
            continue
    if not isinstance(event, dict):
        raise HTTPException(401, detail="Invalid webhook signature or timestamp.")
    try:
        data = event["data"]
        UUID(event["id"]); UUID(data["session_id"])
        valid = (event["type"] == "verification.result.updated" and event["organization_id"] == os.environ["KYC_TENANT_ID"]
            and data["application_id"] == os.environ["KYC_APPLICATION_ID"] and type(data["result_version"]) is int
            and data["result_version"] > 0 and type(data["sandbox"]) is bool)
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise HTTPException(400, detail="Unexpected webhook subscription or event.")
    # A duplicate is acknowledged even if the API is temporarily unavailable.
    with connect() as db:
        duplicate = db.execute("SELECT 1 FROM processed_events WHERE tenant=? AND application=? AND event_id=?",
            (event["organization_id"], data["application_id"], event["id"])).fetchone()
    if duplicate:
        return {"received": True, "duplicate": True}
    try:
        result = await run_in_threadpool(IntegrationBackend().result, data["session_id"])
        with connect() as db:
            accept_result(db, event, result)
    except Exception:
        # No event receipt is committed on fetch/processing failure, so sender retries remain effective.
        raise HTTPException(503, detail="Retry delivery later.") from None
    return {"received": True}
