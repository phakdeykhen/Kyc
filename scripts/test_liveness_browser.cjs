/** Behavioral browser checks for camera startup and the guided ring; uses synthetic API evidence only.
 * KYC_UI_URL=https://127.0.0.1:3000 KYC_PLAYWRIGHT_MODULE=/path/to/playwright node scripts/test_liveness_browser.cjs
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const { chromium } = require(process.env.KYC_PLAYWRIGHT_MODULE || "playwright");

const base = process.env.KYC_UI_URL || "https://127.0.0.1:3000";
const sessionId = "11111111-1111-4111-8111-111111111111";
const session = { session_id: sessionId, organization_id: "test-org", user_id: "synthetic-applicant",
  country: "KH", expected_document_type: "KH_NATIONAL_ID", verification_level: "DOCUMENT_FACE_LIVENESS",
  status: "LIVENESS_REQUIRED", created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 600000).toISOString(), version: 1 };
const steps = ["LOOK_STRAIGHT", "TURN_LEFT", "LOOK_UP", "TURN_RIGHT"];

async function main() {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.KYC_BROWSER_EXECUTABLE ? { executablePath: process.env.KYC_BROWSER_EXECUTABLE } : {}),
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
  const results = [];
  try {
    for (const scenario of ["complete", "exhausted", "camera_denied", "cancel_positioning"]) {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 },
        isMobile: true, hasTouch: true, ignoreHTTPSErrors: true, permissions: ["camera"] });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      let positioned = 0, challenges = 0, submitted = 0;
      const guideCounts = {};
      if (scenario === "camera_denied") await page.addInitScript(() => {
        navigator.mediaDevices.getUserMedia = async () => { throw new DOMException("Denied", "NotAllowedError"); };
      });
      await page.route("**/v1/**", async route => {
        const path = new URL(route.request().url()).pathname;
        const fulfill = body => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
        if (path === `/v1/kyc/${sessionId}`) return fulfill(session);
        if (path.endsWith("/liveness/position")) {
          positioned++;
          if (scenario === "exhausted") return route.fulfill({ status: 429, contentType: "application/json",
            body: JSON.stringify({ reason_code: "LIVENESS_ATTEMPTS_EXCEEDED", detail: "No attempts left", attempts_remaining: 0 }) });
          return fulfill({ face: "OK", state: scenario === "cancel_positioning" || positioned < 3 ? "POSITIONING" : "READY",
            instructions: ["CENTER_FACE"], attempts_remaining: 5 });
        }
        if (path.endsWith("/liveness/challenge")) {
          assert.ok(positioned >= 4, "A challenge must wait for two usable positioning frames");
          challenges++;
          return fulfill({ session_id: sessionId, challenge_id: "test-challenge", nonce: "test-nonce",
            steps: steps.map((step, index) => ({ index, step, instruction: step })), attempts_remaining: 4,
            expires_at: new Date(Date.now() + 120000).toISOString(), frames: { min: 4, max: 12 } });
        }
        if (path.endsWith("/liveness/guide")) {
          const match = route.request().postData().match(/name="step"\r\n\r\n(\d+)/);
          const index = Number(match[1]);
          guideCounts[index] = (guideCounts[index] || 0) + 1;
          const count = guideCounts[index];
          return fulfill({ step: steps[index], face: "OK", state: index === 0 ? "CENTERED"
            : count === 1 ? "WRONG_DIRECTION" : count < 3 ? "KEEP_GOING" : "DONE", progress: count === 1 ? 0 : count < 3 ? .5 : 1 });
        }
        if (path.endsWith("/liveness")) {
          const data = route.request().postData();
          assert.equal((data.match(/name="frames"/g) || []).length, 8);
          assert.ok(data.includes("0,0,1,1,2,2,3,3"));
          submitted++;
          return fulfill({ session_id: sessionId, status: "LIVENESS_REQUIRED", result: "REVIEW", reason_codes: [],
            instructions: [], retry_allowed: true, attempts_remaining: 3, calibrated: false });
        }
        return fulfill({});
      });
      await page.goto(`${base}/verify/${sessionId}#org=test-org&token=synthetic-token`);
      if (scenario === "camera_denied") {
        await page.getByRole("button", { name: "Retry camera" }).waitFor();
        assert.equal(challenges, 0);
        assert.equal(await page.getByRole("button", { name: "Start face scan" }).count(), 0);
      } else {
        const start = page.getByRole("button", { name: "Start face scan" });
        await start.waitFor();
        await page.waitForFunction(() => {
          const video = document.querySelector("video");
          return video && video.videoWidth > 0 && getComputedStyle(video).opacity === "1";
        });
        assert.equal(challenges, 0, "Preview must not consume an attempt");
        if (scenario === "complete") await page.screenshot({ path: "var/liveness-preview.png" });
        await start.click();
        await page.getByText("Position your face in the circle", { exact: true }).waitFor();
        if (scenario === "exhausted") {
          const disabled = page.getByRole("button", { name: "New verification link needed" });
          await disabled.waitFor();
          assert.ok(await disabled.isDisabled());
          assert.equal(challenges, 0);
        } else if (scenario === "cancel_positioning") {
          await page.waitForFunction(() => document.querySelector("video")?.videoWidth > 0);
          await page.getByRole("button", { name: "Cancel", exact: true }).click();
          await page.getByRole("button", { name: "Start face scan" }).waitFor();
          assert.equal(challenges, 0);
        } else {
          await page.getByText("Turn your head to your left", { exact: true }).waitFor();
          assert.equal(await page.getByText("Tilt your head up", { exact: true }).count(), 0);
          const videoBox = await page.locator("video").boundingBox();
          assert.ok(videoBox.width > 200 && videoBox.y < 250);
          await page.screenshot({ path: "var/liveness-guided.png" });
          await page.getByText("Please try the movement check again", { exact: true }).waitFor({ timeout: 30000 });
          assert.equal(challenges, 1);
          assert.equal(submitted, 1);
          assert.ok(Object.values(guideCounts).every(count => count >= 2));
        }
      }
      assert.deepEqual(errors, []);
      results.push({ scenario, passed: true, positioned, challenges, submitted });
      await context.close();
    }
    await fs.writeFile("var/liveness-browser-results.json", JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results));
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
