import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";

import { KYCAPIError, KYCClient, SessionClient, WebhookVerificationError, verifyWebhook } from "../src/index.ts";

const secret = "whsec_test-secret";
const body = '{"api_version":"2026-10-06","data":{"session_id":"s1","status":"VERIFIED"},"id":"evt-1","type":"kyc.verified"}';

function header(timestamp: number, ...secrets: string[]): string {
  return [`t=${timestamp}`, ...secrets.map((key) => `v1=${createHmac("sha256", key).update(`${timestamp}.${body}`).digest("hex")}`)].join(",");
}

test("verifies a signature made the way the server makes it", async () => {
  const now = 1_800_000_000;
  const event = await verifyWebhook(body, header(now, "whsec_other", secret), secret, { now });
  assert.equal(event.id, "evt-1");
  assert.equal(event.type, "kyc.verified");
  const bytes = new TextEncoder().encode(body);
  assert.equal((await verifyWebhook(bytes, header(now, secret), secret, { now })).data.status, "VERIFIED");
});

test("rejects tampering, replays, wrong secrets and malformed headers", async () => {
  const now = 1_800_000_000;
  await assert.rejects(verifyWebhook(body.replace("VERIFIED", "REJECTED"), header(now, secret), secret, { now }), WebhookVerificationError);
  await assert.rejects(verifyWebhook(body, header(now - 301, secret), secret, { now }), /tolerance/);
  await assert.rejects(verifyWebhook(body, header(now, "whsec_wrong"), secret, { now }), /does not match/);
  await assert.rejects(verifyWebhook(body, "v1=abc", secret, { now }), /Malformed/);
  await assert.rejects(verifyWebhook(body, null, secret, { now }), /Malformed/);
});

test("matches a vector signed by the Python server implementation", async () => {
  // Output of kyc.webhooks.signing.sign(["whsec_vector"], b'{"id":"evt-v"}', timestamp=1700000000)
  const vector = "t=1700000000,v1=90870da5e2231e5e61565173cbcbc02cbac27ff5f8c378bcc32fef94f8999ad5";
  const event = await verifyWebhook('{"id":"evt-v"}', vector, "whsec_vector", { now: 1_700_000_000 });
  assert.equal(event.id, "evt-v");
});

test("clients send the right credentials, paths and bodies", async () => {
  const calls: { url: string; init: RequestInit }[] = [];
  const fakeFetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    if (url.endsWith("/result")) {
      return new Response(JSON.stringify({ detail: "This credential lacks the required scope." }), {
        status: 403, headers: { "content-type": "application/json", "x-request-id": "req-1" },
      });
    }
    return new Response(JSON.stringify({ session_id: "s1", status: "CREATED" }), { status: 201, headers: { "content-type": "application/json" } });
  }) as typeof fetch;

  const server = new KYCClient("https://kyc.example/", "kyc_key", "org-1", { fetch: fakeFetch });
  await server.createSession({ userId: "u1", country: "KH", expectedDocumentType: "KH_NATIONAL_ID", idempotencyKey: "signup-0001" });
  const created = calls[0];
  assert.equal(created.url, "https://kyc.example/v1/kyc/sessions");
  const headers = created.init.headers as Record<string, string>;
  assert.equal(headers["X-API-Key"], "kyc_key");
  assert.equal(headers["X-Organization-ID"], "org-1");
  assert.equal(headers["Idempotency-Key"], "signup-0001");
  assert.deepEqual(JSON.parse(String(created.init.body)), {
    user_id: "u1", country: "KH", expected_document_type: "KH_NATIONAL_ID", verification_level: "DOCUMENT_FACE_LIVENESS",
  });

  const device = new SessionClient("https://kyc.example", "kst_token", "org-1", { fetch: fakeFetch });
  await device.uploadDocument("s1", "FRONT", new Uint8Array([0xff, 0xd8]));
  const upload = calls[1];
  assert.equal(upload.url, "https://kyc.example/v1/kyc/s1/documents");
  assert.equal((upload.init.headers as Record<string, string>).Authorization, "Bearer kst_token");
  assert.ok(upload.init.body instanceof FormData);
  assert.equal((upload.init.body as FormData).get("side"), "FRONT");

  await assert.rejects(server.getResult("s1"), (error: unknown) =>
    error instanceof KYCAPIError && error.status === 403 && error.requestId === "req-1");
});
