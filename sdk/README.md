# KYC platform SDKs (Phase 16)

| Package | Where it runs | Status |
| --- | --- | --- |
| [`python/`](python) `kyc_sdk` | Your backend (Python 3.10+, standard library only) | Implemented, tested against the real API |
| [`typescript/`](typescript) `@kyc-platform/sdk` | Node 22.18+, browsers, React Native, edge runtimes (fetch + Web Crypto) | Implemented, tested, type-checked |
| [`openapi.json`](openapi.json) | Contract for generated clients (`scripts/export_openapi.py`) | Generated |
| Android/Kotlin, iOS/Swift, Flutter | Native camera, NFC chip reading | **Not built yet**: see the contract below |

## The integration model

```
your backend (API key)                      user's device (session client token)
──────────────────────                      ────────────────────────────────────
create_session(idempotency_key)  ──►
issue_client_token(session)      ──► kst_… ──► upload document / selfie / liveness / NFC
                                                poll get_session() for status
◄── signed webhook kyc.verified / kyc.rejected / kyc.review.required
get_result(session)   (identity only with results:identity)
```

- **Never ship an API key to a device.** Devices get a session client token. It can
  capture evidence for, and read the status of, one session; it cannot read results.
- Every decision is made on the server: quality gate, OCR, MRZ, chip verification,
  face match, liveness and risk. The SDKs only capture and transport evidence (spec §28).
- Use an `Idempotency-Key` when creating sessions so that retries are safe.

## Python

```python
from kyc_sdk import KYCClient, SessionClient, verify_webhook

kyc = KYCClient("https://kyc.example.com", api_key=KEY, organization_id=ORG)
session = kyc.create_session("customer-42", "KH", "KH_NATIONAL_ID", idempotency_key="signup-42")
token = kyc.issue_client_token(session["session_id"])["client_token"]

device = SessionClient("https://kyc.example.com", token, ORG)       # e.g. a kiosk
device.upload_document(session["session_id"], "FRONT", front_jpeg)
device.upload_document(session["session_id"], "BACK", back_jpeg)

endpoint = kyc.create_webhook("https://api.example.com/kyc/events", ["kyc.verified", "kyc.rejected"])
WEBHOOK_SECRET = endpoint["secret"]   # shown once
```

## TypeScript

```ts
import { KYCClient, SessionClient, verifyWebhook } from "@kyc-platform/sdk";

const kyc = new KYCClient("https://kyc.example.com", process.env.KYC_API_KEY!, ORG);
const session = await kyc.createSession({ userId: "customer-42", country: "KH", expectedDocumentType: "KH_NATIONAL_ID", idempotencyKey: "signup-42" });
const { client_token } = await kyc.issueClientToken(session.session_id);

// In the browser, with the token your backend handed over:
const device = new SessionClient("https://kyc.example.com", client_token, ORG);
await device.uploadDocument(session.session_id, "FRONT", frontBlob);
```

Run the tests with `npm test` (Node 22.18+ runs the TypeScript directly). Type-check
with `npm run typecheck`.

## Receiving webhooks

Each delivery is a `POST` with a JSON body and these headers:

| Header | Meaning |
| --- | --- |
| `KYC-Signature` | `t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>`. During a secret rotation it carries two `v1` values. |
| `KYC-Event-ID` | The event's ID; the same on every retry. **Deduplicate on it.** |
| `KYC-Event-Type` | For example `kyc.verified` |
| `KYC-Delivery-ID`, `KYC-Delivery-Attempt` | For your logs |

```python
# Flask example
@app.post("/kyc/events")
def kyc_events():
    try:
        event = verify_webhook(request.get_data(), request.headers.get("KYC-Signature"), WEBHOOK_SECRET)
    except WebhookVerificationError:
        return "", 400
    if already_processed(event["id"]):
        return "", 200
    handle(event)            # e.g. on kyc.verified, fetch get_result(event["data"]["session_id"])
    return "", 200
```

```ts
// Express example: use the raw body
app.post("/kyc/events", express.raw({ type: "application/json" }), async (req, res) => {
  try {
    const event = await verifyWebhook(req.body, req.header("KYC-Signature"), WEBHOOK_SECRET);
    if (!(await alreadyProcessed(event.id))) await handle(event);
    res.sendStatus(200);
  } catch {
    res.sendStatus(400);
  }
});
```

Respond with 2xx within 10 seconds and do slow work afterwards. Any other response,
a redirect or a timeout is retried. With the default 8 attempts, the waits between them
are 30 s, 2 min, 10 min, 30 min, 1 h, 3 h and 6 h, so the last try comes about 11 hours
after the first. After that the delivery is abandoned; it can be re-sent with `redeliver`.

Events can arrive out of order; keep the highest `session_version` per session. Payloads carry
identifiers, status and decision codes only, never identity data:

```json
{"id": "…", "type": "kyc.verified", "api_version": "2026-10-06", "created_at": "…", "organization_id": "…",
 "data": {"session_id": "…", "user_id": "customer-42", "status": "VERIFIED", "previous_status": "MANUAL_REVIEW", "session_version": 9,
          "verification_level": "DOCUMENT_ONLY",
          "decision": {"result": "REVIEW", "reason_codes": ["DOCUMENT_AUTHENTICITY_UNVERIFIED"], "policy_version": "…"},
          "review": {"action": "APPROVE", "reason_code": "DOCUMENT_CONFIRMED_GENUINE"}}}
```

## Contract for the native mobile SDKs (to be built)

The Kotlin, Swift and Flutter SDKs should wrap the same session-client-token API.
They can be generated from `openapi.json` and extended with native capture:

1. **Token.** Receive `session_id`, `client_token` and the organization ID from the app's
   own backend. Keep the token in memory only; it expires with the session.
2. **Document.** Capture with framing and on-device hints, then
   `POST /v1/kyc/{id}/documents` (`side`). Act on `capture_status: RECAPTURE` and its
   `instructions`. The server's gate decides; the device does not.
3. **Selfie.** Ask for explicit consent, then send `POST /v1/kyc/{id}/selfie` with
   `biometric_consent=true`.
4. **Liveness.** `POST …/liveness/challenge`, then show the randomized steps. Send raw,
   unmirrored frames with `frame_steps` and the `nonce` to `POST …/liveness`.
5. **ePassport NFC** (Android `IsoDep`, iOS `NFCTagReaderSession` with an ICAO reader).
   Get `POST …/nfc/challenge`, then read the chip with PACE/BAC using the MRZ key. Send
   SOD, DG1, DG2 and DG15 plus the Active Authentication signature of the challenge to
   `POST …/nfc`. The server performs Passive and Active Authentication.
6. **Status.** Poll `GET /v1/kyc/{id}` or let the backend push status via webhooks.
   The device never receives the result or the identity data.
