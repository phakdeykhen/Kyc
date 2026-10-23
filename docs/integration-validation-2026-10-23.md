# Integration service validation — 2026-10-23

The multi-tenant integration service is implemented in the existing FastAPI, SQLAlchemy and PostgreSQL architecture. Production KYC and face identification retain separate release gates, closed by default. This report records implementation and local validation; it is not an approval of biometric accuracy or a production deployment.

## Completed work

- Tenant organizations now contain applications with separate scoped, hashed, expiring and revocable credentials. External subject IDs are unique within a tenant. Revision `0014_integrations` adds application/session bindings, versioned decisions, shared usage counters, registered devices, face capture authorization and webhook attempt history with tenant foreign keys and forced row-level security.
- Backend session creation validates configured workflows and exact return URLs, supports application-scoped idempotency, and issues an opaque session ID, hosted URL and short-lived session capture token. Application keys cannot bypass the integration boundary through legacy tenant administration or result routes.
- The mobile hosted flow uses tenant branding, document/selfie consent, capture and liveness, reload recovery, expiry/error handling and completion navigation. The browser receives no application secret and does not retrieve the authoritative result. Required NFC is never silently bypassed; the native device handoff remains outstanding.
- Authenticated results preserve all five final outcomes and monotonic versions. Identity/check disclosure requires both credential scopes and application allowlists. Encrypted result identity has retention, rekey and erasure support independently of the decision history.
- Decisions and application webhook delivery events commit together. Exact-body signatures, timestamps, stable event IDs, rotating secrets, backoff, delivery history and manual redelivery are supported. The receiver example durably handles duplicates and older versions. Configured webhook targets use public-address validation, pinned delivery and no redirects.
- Optional enrollment and recognition sessions bind tenant, application, purpose, population and subject/device. Enrollment requires real verified KYC and separate recognition consent; capture requires fresh liveness. Recognition searches permitted tenant enrollments and returns only authorized references, external subject IDs and permitted profile fields. Device revocation and capture replay checks apply throughout. No business action is attached to a KYC or recognition outcome.
- Developer operations include the OpenAPI contract, sandbox provisioning and simulations, a backend example, webhook receiver, usage/delivery dashboard, shared rate limits and quotas, credential lifecycle and API version/deprecation policy.

## Validation evidence

| Check | Recorded result | Evidence |
| --- | --- | --- |
| Full Python regression suite with local PostgreSQL enabled | `Ran 600 tests in 76.427s`; `OK (skipped=2)` | [Full test log](../artifacts/integration-python-tests.txt) |
| Focused integration and restricted-role PostgreSQL run | 19 tests; `OK` | [Isolation test log](../artifacts/integration-isolation-tests.txt) |
| Hosted Chromium mobile scenarios | All 8 passed: document, selfie, verified, review, technical error, expired token, camera denied and Khmer | [Machine-readable results](../artifacts/hosted-verification-browser.json), [mobile screenshot](../artifacts/hosted-verification-km.png) |
| Existing capture client smoke checks | Passed document/biometric consent, recapture, polling, liveness retries, unavailable camera and cleanup checks | [Capture test script](../scripts/test_capture_client.cjs) |
| Schema and migration checks | Upgrade/downgrade and metadata alignment passed; generated PostgreSQL schema has 42 forced tenant policies | [Schema tests](../tests/test_schema.py), full suite above |
| API contract export | 78 paths; export completed | [OpenAPI](../sdk/openapi.json), [export script](../scripts/export_openapi.py) |

The full suite includes cross-tenant and sibling-application access attempts, credential scopes/revocation, configured callbacks and SSRF rejection, session expiry, capture token rotation and replay, durable decisions/outbox rollback, identity disclosure, retention/rekey/erasure, webhook forgery/timestamps/rotation, duplicates and out-of-order events, retries/redelivery history, device revocation and recognition isolation. The PostgreSQL integration test uses a restricted runtime role, verifies tenant-mismatched foreign keys fail, and races two applications against a tenant quota of one; exactly one creation succeeds.

The runner's two skip entries cover `NativeModelSmokeTests.setUpClass` (two native face smoke methods) and `NativePhotoAttackTests.test_moving_and_tilting_a_photo_never_completes_the_challenge`. The pinned native models/public OpenCV fixture were unavailable. A class-level skip is not counted like an individual executed test, so the runner summary is preserved without inferring a passed-test count.

Hosted browser tests use synthetic API responses and a fake camera. Integration face tests use controlled providers and policy fixtures. These establish application behavior and isolation, not real document authenticity, presentation-attack resistance or identification accuracy.

Reproduce from the repository root with the existing virtual environment and an approved disposable PostgreSQL database:

```sh
TEST_DATABASE_URL=postgresql+psycopg2:///kyc_integrations_test \
  .venv/bin/python -m unittest discover -s tests -q

KYC_PLAYWRIGHT_MODULE=/tmp/kyc-face-browser/node_modules/playwright \
  node scripts/test_hosted_verification_browser.cjs

node scripts/test_capture_client.cjs
.venv/bin/python scripts/export_openapi.py
git diff --check
```

The Playwright module path is environment-specific and requires an installed Chromium browser. PostgreSQL tests require a disposable database name ending in `_test` and administrative access to create temporary schemas/roles; they exercise the application with restricted roles and remove their temporary schemas/roles. Validation used the separate local `kyc_integrations_test` database, not a client tenant database.

## Integration artifacts

- [Integration guide](integration-service.md): application configuration, hosted quick start, scopes, recognition, operations and version policy.
- [Backend example](../examples/integration/backend.py): create a hosted session and fetch the authoritative result using a server-held credential.
- [Webhook receiver](../examples/integration/webhook_receiver.py): verify the exact body, rotated secrets and timestamps, then deduplicate and apply authoritative result versions transactionally.
- [Sandbox provisioning](../scripts/provision_sandbox.py): create sandbox application credentials in an exclusive mode-0600 file for an already provisioned tenant.
- [OpenAPI contract](../sdk/openapi.json): integration and existing platform endpoints.

## Remaining release blockers

1. **Production KYC evidence:** complete representative document/attack evaluation, validated comparison/liveness evidence and operational approval in the [KYC readiness report](production-readiness-2026-10-07.md). Configuration declarations and passing synthetic tests do not supply that evidence.
2. **Independent identification evidence:** obtain approved, unexpired calibration with measured identification/ambiguity thresholds, validated visibility/liveness, population capacity and the separately keyed biometric/deletion-ledger deployment. See [face identity readiness](face-identity.md). KYC approval cannot enable this capability.
3. **NFC-required workflows:** implement and validate the native capture/device handoff. The hosted browser explains the limitation and cannot complete required chip capture by itself.
4. **Localization:** review the Khmer consent/completion copy and translate the remaining English capture/help guidance before a fully localized release.
5. **Deployment:** review/apply revision `0014_integrations` and runtime/biometric grants, provision production secrets and configured destinations, and configure the public HTTPS origin, webhook worker and retention jobs. No live deployment, real tenant migration, production credentials or production enablement was performed.
