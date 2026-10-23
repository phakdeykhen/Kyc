/** Synthetic camera + mocked API UI tests. No biometric accuracy or real-device claims.
 * KYC_UI_URL=http://127.0.0.1:3011 KYC_PLAYWRIGHT_MODULE=/path/to/playwright node scripts/test_face_identity_browser.cjs
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const { chromium } = require(process.env.KYC_PLAYWRIGHT_MODULE || "playwright");
const base = process.env.KYC_UI_URL || "http://127.0.0.1:3011";
const steps = ["LOOK_STRAIGHT", "TURN_LEFT", "LOOK_DOWN", "TURN_RIGHT"];
const id = "11111111-1111-4111-8111-111111111111";

async function main() {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.KYC_BROWSER_EXECUTABLE ? { executablePath: process.env.KYC_BROWSER_EXECUTABLE } : {}),
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
  const results = [];
  try {
    for (const scenario of ["matched", "ambiguous", "not_ready", "khmer", "enrollment_consent", "camera_denied", "stale_response", "kyc_verified", "kyc_review"]) {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 }, permissions: ["camera"] });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      let frames = 0, completed = 0, disclosed = 0, consented = false;
      const expires = new Date(Date.now() + 120000).toISOString();
      const view = (state = "POSITIONING") => ({ session_id: id, expires_at: expires, ticket: "synthetic-frame-ticket-" + frames,
        step: steps[Math.floor(frames / 2)] || null, step_index: Math.floor(frames / 2), steps_total: 4,
        held: frames % 2, required: 2, state, instructions: [] });
      await page.addInitScript(denied => {
        window.__faceCameraStarts = 0;
        const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = async options => {
          window.__faceCameraStarts++;
          if (denied) throw new DOMException("Denied", "NotAllowedError");
          return original(options);
        };
      }, scenario === "camera_denied");
      await page.route("**/v1/**", async route => {
        const path = new URL(route.request().url()).pathname;
        const fulfill = body => route.fulfill({ status: 200, contentType: "application/json", headers: { "Cache-Control": "no-store" }, body: JSON.stringify(body) });
        if (path === "/v1/face-recognition/capabilities") return fulfill({ ready: scenario !== "not_ready", actor_kind: scenario === "enrollment_consent" ? "PERSON" : "KIOSK",
          kyc_status: "VERIFIED", enrollment_allowed: true, retention_days: 30, backup_retention_days: 30, notice_version: "SYNTHETIC",
          permitted_fields: ["display_name", "internal_reference", "kyc_status"], purposes: ["staff_lookup"] });
        if (path === "/v1/me/face-enrollment") return fulfill({ status: consented ? "ACTIVE" : "NOT_ENROLLED", active: consented, expires_at: expires });
        if (/\/(face-enrollments|recognition)\/sessions$/.test(path)) {
          if (scenario === "enrollment_consent") assert.equal(route.request().postDataJSON().consent, true);
          return fulfill(view());
        }
        if (path.endsWith("/frames")) {
          frames++;
          if (scenario === "stale_response") await new Promise(resolve => setTimeout(resolve, 2200));
          return fulfill(view(frames >= 8 ? "READY_TO_COMPLETE" : frames % 2 ? "DONE" : "STEP_COMPLETE"));
        }
        if (path.endsWith("/complete")) {
          completed++;
          assert.ok(route.request().headers()["idempotency-key"]);
          if (scenario === "enrollment_consent") { consented = true; return fulfill({ status: "ACTIVE", message: "Face recognition enabled." }); }
          return fulfill({ outcome: scenario === "ambiguous" ? "AMBIGUOUS" : "MATCHED", message: "" });
        }
        if (path.endsWith("/result")) return fulfill({ outcome: "MATCHED", message: "Face recognized.", recognition_reference: "opaque-private-reference" });
        if (path.endsWith("/profile")) {
          disclosed++;
          assert.equal(route.request().postDataJSON().recognition_reference, "opaque-private-reference");
          return fulfill({ profile: { display_name: "Synthetic Staff", internal_reference: "TEST-01", kyc_status: "VERIFIED" }, clear_after_seconds: 1 });
        }
        if (path === `/v1/kyc/${id}`) return fulfill({ session_id: id, organization_id: "test", user_id: "synthetic", status: scenario === "kyc_verified" ? "VERIFIED" : "MANUAL_REVIEW",
          country: "KH", expected_document_type: "KH_PASSPORT", verification_level: "DOCUMENT_FACE_LIVENESS", created_at: new Date().toISOString(), expires_at: expires, version: 1 });
        return fulfill({});
      });
      if (scenario.startsWith("kyc_")) {
        await page.goto(`${base}/verify/${id}/done#org=test&token=kst_synthetic_test_token`);
        await page.getByRole("heading", { name: scenario === "kyc_verified" ? "Identity verified" : "Your details are being reviewed" }).waitFor();
        assert.equal(await page.getByRole("link", { name: "Enable Face Recognition" }).count(), scenario === "kyc_verified" ? 1 : 0);
      } else {
        await page.goto(`${base}/face/${scenario === "enrollment_consent" ? "enroll" : "kiosk"}`);
        await page.getByLabel("Organization ID").fill("synthetic-organization");
        await page.getByLabel("Account or device credential").fill("fri_synthetic_private_test_token");
        await page.getByRole("button", { name: "Connect", exact: true }).click();
        await page.getByRole("button", { name: "Sign out", exact: true }).waitFor();
        if (scenario === "not_ready") {
          assert.equal(await page.getByRole("button", { name: "Scan Face", exact: true }).isDisabled(), true);
        } else if (scenario === "khmer") {
          await page.getByLabel("Language").selectOption("km");
          assert.equal(await page.locator('main[lang="km"]').count(), 1);
          await page.getByRole("button", { name: "ស្កេនមុខ", exact: true }).waitFor();
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
          await page.screenshot({ path: "artifacts/face-identity-khmer.png", fullPage: true });
        } else {
          const button = page.getByRole("button", { name: scenario === "enrollment_consent" ? "Enable Face Recognition" : "Scan Face", exact: true });
          if (scenario === "enrollment_consent") {
            assert.equal(await button.isDisabled(), true);
            await page.getByRole("checkbox").check();
          }
          await button.click();
          if (scenario === "camera_denied" || scenario === "stale_response") {
            await page.getByRole("status").filter({ hasText: "We could not confirm" }).waitFor({ timeout: 15000 });
            assert.equal(completed, 0); assert.equal(disclosed, 0);
          } else if (scenario === "ambiguous") {
            await page.getByRole("status").filter({ hasText: "We could not confirm" }).waitFor({ timeout: 15000 });
            assert.equal(completed, 1); assert.equal(disclosed, 0);
          } else if (scenario === "enrollment_consent") {
            await page.getByRole("status").filter({ hasText: "Face recognition enabled" }).waitFor({ timeout: 15000 });
            assert.equal(completed, 1); assert.equal(disclosed, 0);
          } else {
            await page.getByText("Synthetic Staff", { exact: true }).waitFor({ timeout: 15000 });
            assert.equal(disclosed, 1);
            await page.getByText("Synthetic Staff", { exact: true }).waitFor({ state: "detached", timeout: 5000 });
            assert.equal(await page.getByRole("status").filter({ hasText: "Face recognized" }).count(), 0);
            await page.getByRole("heading", { name: "Ready", exact: true }).waitFor();
          }
          assert.equal(await page.evaluate(() => window.__faceCameraStarts), 1, "Only one camera initialization per attempt");
        }
        const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
        assert.ok(!stored.includes("Synthetic Staff") && !stored.includes("fri_") && !stored.includes("opaque-private-reference"));
      }
      assert.deepEqual(errors, []);
      results.push({ scenario, passed: true, frames, completed, disclosed });
      await context.close();
    }
    await fs.writeFile("artifacts/face-identity-browser.json", JSON.stringify({ synthetic: true, results }, null, 2));
    console.log(JSON.stringify({ passed: results.length, synthetic: true }));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
