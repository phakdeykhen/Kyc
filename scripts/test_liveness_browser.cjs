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
    const scenarios = process.env.KYC_BROWSER_SCENARIOS?.split(",") ?? ["complete", "successful_capture", "exhausted", "camera_denied", "cancel_positioning",
                           "position_body_timeout", "challenge_body_timeout", "cancel_challenge", "camera_interrupted",
                           "challenge_expired_offline", "poll_recovery"];
    for (const scenario of scenarios) {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 },
        isMobile: true, hasTouch: true, ignoreHTTPSErrors: true, permissions: ["camera"] });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      let positioned = 0, challenges = 0, submitted = 0, sessionReads = 0;
      const guideCounts = {};
      if (scenario === "camera_denied") await page.addInitScript(() => {
        navigator.mediaDevices.getUserMedia = async () => { throw new DOMException("Denied", "NotAllowedError"); };
      });
      if (["position_body_timeout", "challenge_body_timeout", "cancel_challenge"].includes(scenario)) await page.addInitScript(endpoint => {
        const fetch = window.fetch.bind(window);
        window.fetch = async (input, options) => {
          if (!String(input).endsWith(`/liveness/${endpoint}`)) return fetch(input, options);
          window.stalledLivenessRequest = true;
          return new Response(new ReadableStream({ start(controller) {
            options.signal.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")), { once: true });
          } }), { status: 200, headers: { "Content-Type": "application/json" } });
        };
      }, scenario === "position_body_timeout" ? "position" : "challenge");
      await page.route("**/v1/**", async route => {
        const path = new URL(route.request().url()).pathname;
        const fulfill = body => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
        if (path === `/v1/kyc/${sessionId}`) {
          sessionReads++;
          if (scenario === "poll_recovery") {
            if (sessionReads === 3) return route.abort("connectionfailed");
            return fulfill({ ...session, status: sessionReads >= 4 ? "SELFIE_REQUIRED" : "DOCUMENT_PROCESSING" });
          }
          return fulfill(session);
        }
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
            expires_at: new Date(Date.now() + (scenario === "challenge_expired_offline" ? 1500 : 120000)).toISOString(),
            frames: { min: 8, max: 12 } });
        }
        if (path.endsWith("/liveness/guide")) {
          const match = route.request().postData().match(/name="step"\r\n\r\n(\d+)/);
          const index = Number(match[1]);
          guideCounts[index] = (guideCounts[index] || 0) + 1;
          if (scenario === "challenge_expired_offline") return route.abort("connectionfailed");
          const count = guideCounts[index];
          return fulfill({ step: steps[index], face: "OK", state: index === 0 ? "CENTERED"
            : count === 1 ? "WRONG_DIRECTION" : count < 3 ? "KEEP_GOING" : "DONE", progress: count === 1 ? 0 : count < 3 ? .5 : 1 });
        }
        if (path.endsWith("/liveness")) {
          const data = route.request().postData();
          assert.equal((data.match(/name="frames"/g) || []).length, 8);
          assert.ok(data.includes("0,0,1,1,2,2,3,3"));
          submitted++;
          return fulfill({ session_id: sessionId, status: scenario === "successful_capture" ? "MANUAL_REVIEW" : "LIVENESS_REQUIRED",
            result: "REVIEW", reason_codes: [], instructions: [], retry_allowed: scenario !== "successful_capture",
            attempts_remaining: 3, calibrated: false });
        }
        return fulfill({});
      });
      await page.goto(`${base}/verify/${sessionId}#org=test-org&token=synthetic-token`);
      if (scenario === "poll_recovery") {
        await page.getByRole("heading", { name: "Take a selfie", exact: true }).waitFor({ timeout: 10000 });
        assert.ok(sessionReads >= 4, "Polling must recover after a failed status request");
      } else if (scenario === "camera_denied") {
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
        } else if (scenario === "position_body_timeout" || scenario === "challenge_body_timeout") {
          await page.getByText("Could not start the check", { exact: true }).waitFor({ timeout: 15000 });
          await page.getByText("The connection took too long. Try again.", { exact: true }).waitFor();
          assert.equal(challenges, 0);
          assert.equal(submitted, 0);
          if (scenario === "challenge_body_timeout") assert.equal(positioned, 4);
          assert.ok(await page.getByRole("button", { name: "Try again", exact: true }).isEnabled());
        } else if (scenario === "cancel_challenge") {
          await page.waitForFunction(() => window.stalledLivenessRequest);
          await page.getByRole("button", { name: "Cancel", exact: true }).click();
          await page.getByRole("button", { name: "Start face scan" }).waitFor();
          assert.equal(positioned, 4);
          assert.equal(submitted, 0);
          assert.equal(await page.getByText("Could not start the check", { exact: true }).count(), 0);
        } else if (scenario === "cancel_positioning") {
          await page.waitForFunction(() => document.querySelector("video")?.videoWidth > 0);
          await page.getByRole("button", { name: "Cancel", exact: true }).click();
          await page.getByRole("button", { name: "Start face scan" }).waitFor();
          assert.equal(challenges, 0);
        } else if (scenario === "camera_interrupted") {
          await page.getByText("Turn your head to your left", { exact: true }).waitFor();
          await page.evaluate(() => {
            const track = document.querySelector("video").srcObject.getVideoTracks()[0];
            track.stop();
            track.dispatchEvent(new Event("ended"));
          });
          await page.getByText("The camera stopped. Restart the camera and try again.", { exact: true }).waitFor();
          try {
            await page.getByRole("button", { name: "Retry camera" }).waitFor({ timeout: 5000 });
          } catch (error) {
            await page.screenshot({ path: "var/liveness-camera-interruption-failure.png" });
            console.error(await page.locator("body").innerText());
            throw error;
          }
          assert.ok(await page.getByRole("button", { name: "Retry camera" }).isEnabled());
          assert.equal(challenges, 1);
          assert.equal(submitted, 0);
        } else if (scenario === "challenge_expired_offline") {
          await page.getByText("Time ran out for this check", { exact: true }).waitFor({ timeout: 15000 });
          assert.ok(await page.getByRole("button", { name: "Try again", exact: true }).isEnabled());
          assert.equal(challenges, 1);
          assert.equal(submitted, 0);
        } else {
          await page.getByText("Turn your head to your left", { exact: true }).waitFor();
          assert.equal(await page.getByText("Tilt your head up", { exact: true }).count(), 0);
          const videoBox = await page.locator("video").boundingBox();
          assert.ok(videoBox.width > 200 && videoBox.y < 250);
          await page.screenshot({ path: "var/liveness-guided.png" });
          if (scenario === "successful_capture") {
            await page.getByText("Face scan complete", { exact: true }).waitFor({ timeout: 30000 });
          } else {
            await page.getByText("Please try the movement check again", { exact: true }).waitFor({ timeout: 30000 });
          }
          assert.equal(challenges, 1);
          assert.equal(submitted, 1);
          assert.ok(Object.values(guideCounts).every(count => count >= 2));
        }
      }
      assert.deepEqual(errors, []);
      results.push({ scenario, passed: true, positioned, challenges, submitted, sessionReads });
      await fs.writeFile("var/liveness-browser-results.json", JSON.stringify(results, null, 2));
      await context.close();
    }
    await fs.writeFile("var/liveness-browser-results.json", JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results));
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
