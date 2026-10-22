/** Synthetic browser profiling and layout checks, never identity verification.
 * Start Frontend's Vite server, then run:
 * KYC_UI_URL=http://127.0.0.1:3001 KYC_PLAYWRIGHT_MODULE=/path/to/playwright node scripts/profile_liveness_browser.cjs
 * Baseline source is pinned below (override KYC_BASELINE_REVISION); the face provider is mocked.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createHash } = require("node:crypto");
const os = require("node:os");
const { chromium } = require(process.env.KYC_PLAYWRIGHT_MODULE || "playwright");
const root = path.resolve(__dirname, "..");
const fixture = path.join(root, "Frontend", ".liveness-browser-fixture");
const base = process.env.KYC_UI_URL || "http://127.0.0.1:3001";
const output = path.join(root, "artifacts", "liveness-improvements");
const baselineRevision = process.env.KYC_BASELINE_REVISION || "3629ebce84a93165425773f5766ac421ab6bd11a";
const directions = ["LOOK_STRAIGHT", "TURN_LEFT", "LOOK_DOWN", "TURN_RIGHT", "LOOK_UP"];
const labels = ["Look straight ahead.", "Turn your head to your left.", "Lower your chin slightly.", "Turn your head to your right.", "Lift your chin slightly."];

async function prepare() {
  await fs.mkdir(fixture, { recursive: true });
  await fs.mkdir(output, { recursive: true });
  for (const [source, destination] of [
    ["Frontend/src/lib/useCamera.ts", "useCamera.ts"],
    ["Frontend/src/pages/verify/components/FaceScanRing.tsx", "FaceScanRing.tsx"],
    ["Frontend/src/pages/verify/components/LivenessStage.tsx", "LivenessStage.tsx"],
  ]) {
    const before = execFileSync("git", ["show", `${baselineRevision}:${source}`], { cwd: root, encoding: "utf8" })
      .replaceAll('"@/lib/useCamera"', '"./useCamera"')
      .replaceAll('"@/pages/verify/components/FaceScanRing"', '"./FaceScanRing"');
    await fs.writeFile(path.join(fixture, destination), before);
  }
  await fs.writeFile(path.join(fixture, "index.html"), '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="./entry.tsx"></script></body></html>');
  await fs.writeFile(path.join(fixture, "entry.tsx"), `
    import { StrictMode } from "react";
    import { createRoot } from "react-dom/client";
    import "/src/index.css";
    import Before from "./LivenessStage";
    import After from "/src/pages/verify/components/LivenessStage";
    const Component = new URLSearchParams(location.search).get("variant") === "baseline" ? Before : After;
    createRoot(document.getElementById("root")!).render(<StrictMode><main style={{maxWidth:512,margin:"0 auto",padding:16}}>
      <Component credential={{kind:"client",organizationId:"synthetic-org",token:"synthetic-token"}}
        sessionId="synthetic-session" onSessionChanged={(status) => { (window as any).completedStatus = status; }} />
    </main></StrictMode>);
  `);
}

function instrument() {
  const metric = window.scanMetrics = { canvases: 0, draws: [], encodes: [], requests: [], maxConcurrent: 0, cameraStarts: 0, mutations: 0, raf: [], videoFrames: 0, longTasks: [] };
  const create = document.createElement.bind(document);
  document.createElement = (...args) => { if (args[0] === "canvas") metric.canvases++; return create(...args); };
  const draw = CanvasRenderingContext2D.prototype.drawImage;
  CanvasRenderingContext2D.prototype.drawImage = function (...args) { const start = performance.now(); const result = draw.apply(this, args); metric.draws.push(performance.now() - start); return result; };
  const encode = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function (callback, ...args) { const start = performance.now(); return encode.call(this, blob => { metric.encodes.push(performance.now() - start); callback(blob); }, ...args); };
  const media = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = (...args) => { metric.cameraStarts++; return media(...args); };
  const request = window.fetch.bind(window);
  let concurrent = 0;
  window.fetch = async (input, ...args) => {
    if (!String(input).includes("/liveness")) return request(input, ...args);
    const start = performance.now(); metric.maxConcurrent = Math.max(metric.maxConcurrent, ++concurrent);
    try { const result = await request(input, ...args); metric.requests.push({path:String(input).split("/").at(-1),ms:performance.now()-start}); return result; }
    finally { concurrent--; }
  };
  const mutations = new MutationObserver(records => { metric.mutations += records.length; });
  mutations.observe(document, { subtree: true, childList: true, characterData: true, attributes: true });
  let last = performance.now();
  const frame = now => { metric.raf.push(now - last); last = now; requestAnimationFrame(frame); };
  requestAnimationFrame(frame);
  document.addEventListener("loadedmetadata", event => {
    if (event.target.tagName !== "VIDEO" || !event.target.requestVideoFrameCallback) return;
    const videoFrame = () => { metric.videoFrames++; event.target.requestVideoFrameCallback(videoFrame); };
    event.target.requestVideoFrameCallback(videoFrame);
  }, true);
  try { new PerformanceObserver(list => metric.longTasks.push(...list.getEntries().map(entry => entry.duration))).observe({type:"longtask",buffered:true}); } catch { /* Optional API. */ }
}

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function summary(values) {
  const sorted = values.toSorted((a,b) => a-b);
  return { count: sorted.length, median: +(sorted[Math.floor(sorted.length*.5)] || 0).toFixed(2), p95: +(sorted[Math.min(sorted.length-1, Math.floor(sorted.length*.95))] || 0).toFixed(2) };
}

async function run(browser, variant, viewport, options = {}) {
  const context = await browser.newContext({ viewport, ignoreHTTPSErrors: true, permissions: ["camera"], reducedMotion: options.reducedMotion ? "reduce" : "no-preference" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(instrument);
  if (options.cameraDenied) await page.addInitScript(() => { navigator.mediaDevices.getUserMedia = async () => { window.scanMetrics.cameraStarts++; throw new DOMException("Denied", "NotAllowedError"); }; });
  const cdp = await context.newCDPSession(page);
  if (options.cpu) await cdp.send("Emulation.setCPUThrottlingRate", { rate: options.cpu });
  const counts = {};
  let positioned = 0, submitted = 0;
  const uploads = [];
  const rejectedFrames = new Set();
  let tokenRequests = 0, referenceRequests = 0, tokenRejected = false;
  const digest = async file => createHash("sha256").update(Buffer.from(await file.arrayBuffer())).digest("hex");
  await page.route("**/v1/**", async route => {
    const url = route.request().url();
    uploads.push({path:url.split("/").at(-1),bytes:route.request().postDataBuffer()?.length ?? 0});
    const fulfill = body => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    await delay(50);
    if (url.endsWith("/position")) return fulfill({ face: "OK", state: ++positioned >= 3 ? "READY" : "POSITIONING", instructions: ["CENTER_FACE"], attempts_remaining: 5 });
    if (url.endsWith("/challenge")) return fulfill({ challenge_id:"synthetic-challenge", nonce:"synthetic-nonce", steps:directions.map((step,index)=>({index,step,instruction:step})), frames:{min:10,max:12,stable_frames_per_step:2}, attempts_remaining:4, expires_at:new Date(Date.now()+120000).toISOString() });
    if (url.endsWith("/guide")) {
      const index = Number(route.request().postData().match(/name="step"\r\n\r\n(\d+)/)[1]);
      const count = counts[index] = (counts[index] || 0) + 1;
      if(variant==="current") {
        const form=await new Response(route.request().postDataBuffer(),{headers:{"Content-Type":route.request().headers()["content-type"]}}).formData();
        if(form.has("calibration")) {
          tokenRequests++;
          assert.equal(form.has("baseline"),false,"A calibration token replaces repeated neutral image uploads");
          assert.equal(form.getAll("baseline_extra").length,0);
          if(options.tokenFallback&&!tokenRejected) {
            tokenRejected=true;
            return route.fulfill({status:422,contentType:"application/json",body:JSON.stringify({detail:"Calibration unavailable",reason_code:"CALIBRATION_UNUSABLE"})});
          }
        } else {
          referenceRequests++;
          assert.equal(form.has("baseline"),true);
          assert.equal(form.getAll("baseline_extra").length,1,"Guide and final check share both neutral samples");
        }
      }
      if ((options.stale && index===1 && count===1) || (options.trackingLoss && index===2 && count===2)) {
        const form=await new Response(route.request().postDataBuffer(),{headers:{"Content-Type":route.request().headers()["content-type"]}}).formData();
        rejectedFrames.add(await digest(form.get("frame")));
      }
      if (options.stale && index === 1 && count === 1) await delay(2200);
      if (options.inFlightDisabled && index === 1 && count === 1) {
        await page.evaluate(()=>{document.querySelector("video").srcObject.getVideoTracks()[0].enabled=false;});
        await delay(250);
      }
      const lost = options.trackingLoss && index === 2 && count === 2;
      return fulfill({ step:directions[index], face:lost ? "NO_FACE" : "OK", state:index === 0 ? "CENTERED" : options.help ? "KEEP_GOING" : count === 1 && !options.stale && !options.inFlightDisabled ? "KEEP_GOING" : "DONE", progress:index === 0 || count > 1 ? 1 : .5, calibration:"synthetic-calibration" });
    }
    if (url.endsWith("/liveness")) {
      const data = route.request().postData();
      assert.equal((data.match(/name="frames"/g)||[]).length, 10);
      assert.ok(data.includes("0,0,1,1,2,2,3,3,4,4"));
      const form=await new Response(route.request().postDataBuffer(),{headers:{"Content-Type":route.request().headers()["content-type"]}}).formData();
      for(const frame of form.getAll("frames")) assert.equal(rejectedFrames.has(await digest(frame)),false,"Stale or lost-tracking frames must never become final evidence");
      submitted++;
      await delay(400);
      return fulfill({status:"MANUAL_REVIEW",result:"REVIEW",reason_codes:[],instructions:[],retry_allowed:false,attempts_remaining:3,calibrated:false});
    }
    throw new Error(`Unexpected synthetic request ${url}`);
  });
  await page.goto(`${base}/.liveness-browser-fixture/index.html?variant=${variant}`);
  if (options.cameraDenied) {
    await page.getByRole("button", {name:"Retry camera"}).waitFor();
    await page.getByText("Allow camera access in your browser, then try again.", {exact:true}).waitFor();
    assert.equal(submitted, 0);
  } else {
    await page.getByRole("button", {name:"Start face scan"}).waitFor();
    await page.waitForFunction(() => document.querySelector("video")?.videoWidth > 0);
    await page.getByRole("button", {name:"Start face scan"}).click();
    if (variant === "current") {
      for (let index=1;index<directions.length;index++) {
        try { await page.getByText(labels[index], {exact:true}).waitFor({timeout:30000}); }
        catch(error) { await page.screenshot({path:path.join(output,`failure-${variant}-${index}.png`),fullPage:true}); console.error(JSON.stringify({options,counts,positioned,submitted,errors,text:await page.locator("body").innerText()})); throw error; }
        const badge = page.locator(`.face-scan-cue[data-direction="${directions[index]}"]`);
        await badge.waitFor();
        const a = await badge.boundingBox(), v = await page.locator("video").boundingBox();
        assert.ok(a.x >= v.x+v.width, "Badge must be outside the camera image");
        assert.ok(a.width >= 48 && a.width <= 56, "Responsive badge is 48–56 px");
        // A prior green confirmation transitions back to active blue over 200 ms.
        await page.waitForFunction(direction=>{
          const el=document.querySelector(`.face-scan-cue[data-direction="${direction}"]`);
          const values=el&&getComputedStyle(el).backgroundColor.match(/[\d.]+/g).map(Number);
          return values&&values.slice(0,3).every((value,channel)=>Math.abs(value-[29,78,216][channel])<=1);
        },directions[index]);
        assert.equal(await page.locator("video").evaluate(el => new DOMMatrixReadOnly(getComputedStyle(el).transform).a), -1, "Only the camera preview is mirrored");
        const scan = page.getByTestId("liveness-scan");
        assert.equal(await scan.evaluate(el => el.scrollWidth <= el.clientWidth), true, "No horizontal clipping");
        const before = await badge.boundingBox();
        await delay(120);
        const after = await badge.boundingBox();
        assert.ok(Math.abs(before.x-after.x)<.1 && Math.abs(before.y-after.y)<.1, "Badge anchor stays fixed during animation");
        if (options.reducedMotion) assert.equal(await badge.locator(".face-scan-cue-icon").evaluate(el=>getComputedStyle(el).animationName), "none");
        await page.screenshot({path:path.join(output, `${variant}-${viewport.width}-${directions[index]}${options.reducedMotion?"-reduced":""}.png`)});
        if (options.cameraInterrupted || options.frozenCamera || options.inFlightDisabled) {
          if(options.inFlightDisabled) {
            await page.waitForFunction(()=>document.querySelector("video").srcObject.getVideoTracks()[0].enabled===false);
            await page.waitForFunction(()=>document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")==="0");
          }
          else if(options.frozenCamera) await page.evaluate(()=>document.querySelector("video").pause());
          else await page.evaluate(() => { const track=document.querySelector("video").srcObject.getVideoTracks()[0];track.stop();track.dispatchEvent(new Event("ended")); });
          await page.getByText("The camera stopped. Restart the camera and try again.", {exact:true}).first().waitFor({timeout:10000});
          if(options.cameraInterrupted) await page.getByRole("button", {name:"Retry camera"}).waitFor();
          assert.equal(submitted,0); break;
        }
        if (options.help) {
          const help=page.getByTestId("liveness-help");
          const boundsBefore=await help.boundingBox();
          try { await page.getByRole("button", {name:"Start over"}).waitFor({timeout:25000}); }
          catch(error) { await page.screenshot({path:path.join(output,"help-failure.png"),fullPage:true}); console.error(await page.locator("body").innerText()); throw error; }
          const boundsAfter=await help.boundingBox();
          await help.scrollIntoViewIfNeeded();
          await page.screenshot({path:path.join(output, `help-${viewport.width}.png`)});
          assert.ok(Math.abs(boundsAfter.y-boundsBefore.y)<1 && Math.abs(boundsAfter.height-boundsBefore.height)<1, `Reserved help region must not expand or jump: ${JSON.stringify({boundsBefore,boundsAfter})}`);
          await page.getByRole("button", {name:"Cancel",exact:true}).click();
          assert.equal(submitted,0); break;
        }
      }
    }
    if (!options.help && !options.cameraInterrupted && !options.frozenCamera && !options.inFlightDisabled) {
      await page.waitForFunction(()=>window.completedStatus === "MANUAL_REVIEW", undefined, {timeout:60000});
      assert.equal(submitted,1);
      assert.equal(await page.getByText("Identity verified", {exact:true}).count(),0,"A synthetic movement capture never approves identity");
      if(options.stale) assert.ok(counts[1]>=3,"A stale positive response requires two later fresh checks");
      if(options.trackingLoss) assert.ok(counts[2]>=4,"Tracking loss resets the consecutive fresh-frame hold");
      if(options.tokenFallback) assert.equal(tokenRejected,true,"The optional calibration token failure was exercised");
      if(variant==="current"&&!options.tokenFallback) assert.equal(referenceRequests,2,"Calibration refreshes once after the accepted challenge baseline replaces preflight");
    }
  }
  const metric = await page.evaluate(()=>window.scanMetrics);
  assert.deepEqual(errors,[]);
  assert.equal(metric.maxConcurrent <= 1,true,"Inference/network requests must remain serial");
  assert.equal(metric.cameraStarts,2,"React Strict Mode starts only its initial and replacement camera streams");
  const result={variant,viewport,...options,passed:true,synthetic:true,baselineRevision,browser:browser.version(),host:{platform:os.platform(),architecture:os.arch(),cpu:os.cpus()[0]?.model},
    camera:"Chromium synthetic media stream; no physical camera or person",network:"Playwright route mock, 50 ms per response; final submission adds 400 ms",positioned,submitted,tokenRequests,referenceRequests,cameraStarts:metric.cameraStarts,canvasAllocations:metric.canvases,
    drawMs:summary(metric.draws),jpegEncodeMs:summary(metric.encodes),rafIntervalMs:summary(metric.raf.slice(2)),videoPresentedFrames:metric.videoFrames,
    domMutationRecords:metric.mutations,maxConcurrentRequests:metric.maxConcurrent,requestMs:summary(metric.requests.map(r=>r.ms)),requestCount:metric.requests.length,
    uploadBytes:summary(uploads.filter(item=>item.path==="guide").map(item=>item.bytes)),totalUploadBytes:uploads.reduce((sum,item)=>sum+item.bytes,0),longTasksMs:metric.longTasks};
  await context.close(); return result;
}

async function main() {
  await prepare();
  const browser=await chromium.launch({headless:true,args:["--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"],...(process.env.KYC_BROWSER_EXECUTABLE?{executablePath:process.env.KYC_BROWSER_EXECUTABLE}:{})});
  const results=[];
  try {
    const cases=process.env.KYC_PROFILE_CASES?.split(",") || ["baseline","current","mobile","tablet","reduced","help","camera_denied","camera_interrupted","frozen_camera","stale","tracking_loss","token_fallback","in_flight_camera_disabled"];
    for(const test of cases) {
      const options={reducedMotion:test==="reduced",help:test==="help",cameraDenied:test==="camera_denied",cameraInterrupted:test==="camera_interrupted",frozenCamera:test==="frozen_camera",stale:test==="stale",trackingLoss:test==="tracking_loss",tokenFallback:test==="token_fallback",inFlightDisabled:test==="in_flight_camera_disabled",cpu:test==="mobile"?4:0};
      const viewport=test==="tablet"?{width:768,height:1024}:test==="baseline"||test==="current"?{width:1280,height:800}:test==="help"?{width:320,height:568}:{width:390,height:844};
      const result=await run(browser,test==="baseline"?"baseline":"current",viewport,options);
      results.push({test,...result}); await fs.writeFile(path.join(output,process.env.KYC_PROFILE_OUTPUT||"browser-profile.json"),JSON.stringify(results,null,2));
      console.log(JSON.stringify({test,...result}));
    }
  } finally { await browser.close(); await fs.rm(fixture,{recursive:true,force:true}); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
