/** Synthetic API/camera browser checks; no real identity or verification accuracy claim. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const { chromium } = require(process.env.KYC_PLAYWRIGHT_MODULE || "playwright");
const id = "11111111-1111-4111-8111-111111111111";
const org = "22222222-2222-4222-8222-222222222222";
const token = "kst_" + "s".repeat(43);

async function main() {
  const server = http.createServer(async (request, response) => {
    const route = request.url.split("?")[0];
    const file = route === `/verify/${id}` ? "src/kyc/web/hosted/index.html" : route === "/verify-assets/capture.js"
      ? "src/kyc/web/capture/capture.js" : route === "/verify-assets/capture.css" ? "src/kyc/web/capture/capture.css" : null;
    if (!file) { response.writeHead(404).end(); return; }
    response.setHeader("Content-Type", file.endsWith(".js") ? "application/javascript" : file.endsWith(".css") ? "text/css" : "text/html");
    response.end(await fs.readFile(file));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
  const evidence = [];
  try {
    for (const scenario of ["document", "selfie", "verified", "review", "technical_error", "expired_token", "camera_denied", "khmer"]) {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 }, permissions: ["camera"] });
      const page = await context.newPage();
      const errors = [], paths = [];
      page.on("pageerror", error => errors.push(error.message));
      if (scenario === "camera_denied") await page.addInitScript(() => {
        navigator.mediaDevices.getUserMedia = async () => { throw new DOMException("Denied", "NotAllowedError"); };
      });
      await page.route("**/v1/**", async route => {
        const path = new URL(route.request().url()).pathname;
        paths.push(path);
        const headers = route.request().headers();
        assert.equal(headers.authorization, `Bearer ${token}`);
        assert.equal(headers["x-organization-id"], org);
        assert.equal(headers["x-api-key"], undefined);
        const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
        if (scenario === "expired_token") return reply({ detail: "Capture expired" }, 401);
        if (path.endsWith("/capture-context")) return reply({ session_id: id, locale: scenario === "khmer" ? "km" : "en",
          sandbox: true, branding: { display_name: "Synthetic School", color: "#164e63" }, required_sides: ["FRONT", "BACK"],
          return_url: "https://client.example/kyc/return", expected_document_type: "KH_NATIONAL_ID" });
        if (path.endsWith("/capture-status")) return reply({ session_id: id, expected_document_type: "KH_NATIONAL_ID",
          status: scenario === "verified" ? "VERIFIED" : scenario === "review" ? "MANUAL_REVIEW" : scenario === "technical_error"
            ? "TECHNICAL_ERROR" : scenario === "selfie" ? "SELFIE_REQUIRED" : "CREATED", expires_at: new Date(Date.now() + 600000).toISOString() });
        throw new Error("Unexpected hosted request: " + path);
      });
      await page.goto(`${base}/verify/${id}#token=${token}&organization=${org}`);
      if (scenario === "expired_token") await page.getByText("This link has expired.", { exact: false }).waitFor();
      else {
        await page.getByText("Synthetic School", { exact: true }).waitFor();
        await page.getByText("SANDBOX · simulation", { exact: true }).waitFor();
        if (["document", "khmer", "camera_denied"].includes(scenario)) {
          await page.locator("#capture").waitFor({ state: "visible" });
          assert.equal(await page.locator("#file").isDisabled(), true);
          await page.locator("#document-consent").check();
          assert.equal(await page.locator("#file").isDisabled(), false);
          if (scenario === "camera_denied") await page.getByText("Use Upload", { exact: false }).waitFor();
          if (scenario === "khmer") {
            assert.equal(await page.locator("html").getAttribute("lang"), "km");
            await page.screenshot({ path: "artifacts/hosted-verification-km.png", fullPage: true });
          }
          await page.reload();
          await page.locator("#capture").waitFor({ state: "visible" });
          assert.equal(await page.locator("#document-consent").isChecked(), false);
        } else if (scenario === "selfie") {
          await page.locator("#selfie").waitFor({ state: "visible" });
          assert.equal(await page.locator("#selfie-file").isDisabled(), true);
        } else {
          await page.locator("#return-link").waitFor({ state: "visible" });
          assert.equal(await page.locator("#return-link").getAttribute("href"), "https://client.example/kyc/return");
        }
        assert.equal(await page.locator("#api-key").count(), 0);
        assert.equal(await page.evaluate(() => location.hash), "");
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "Mobile page overflows");
      }
      assert.equal(paths.some(path => path.endsWith("/result")), false, "Applicant must not fetch the authoritative result");
      assert.deepEqual(errors, []);
      evidence.push({ scenario, result: "PASS", requests: paths.length });
      await context.close();
    }
    await fs.writeFile("artifacts/hosted-verification-browser.json", JSON.stringify({ synthetic: true, scenarios: evidence }, null, 2) + "\n");
    console.log(`${evidence.length} hosted browser scenarios passed (synthetic API and camera).`);
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
