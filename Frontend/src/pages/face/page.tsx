import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { faceIdentityApi as api } from "@/api/faceIdentity";
import type { Capabilities, Enrollment, FaceCredential } from "@/api/faceIdentity";
import { useCamera } from "@/lib/useCamera";
import FaceScanRing from "@/pages/verify/components/FaceScanRing";
import type { Direction } from "@/pages/verify/components/FaceScanRing";

const words = {
  en: {
    title: "Face recognition", optional: "Optional face enrollment", scan: "Scan Face", enable: "Enable Face Recognition",
    purpose: "Recognize you during future live scans. Choosing not to enroll does not change your completed KYC.",
    fields: "Permitted information that may appear", retention: "Your encrypted face template expires after", days: "days.",
    privacy: "Movement images are discarded. You can withdraw consent and delete enrollment here at any time. Backup copies expire under the service’s retention policy; deleted enrollments remain blocked after a database restore.",
    consent: "I consent to future face recognition under this notice.", notice: "Notice", decline: "No thanks", disable: "Withdraw consent and delete enrollment",
    ready: "Ready", positioning: "Position your face", checking: "Checking liveness", recognizing: "Recognizing", submitting: "Confirming enrollment",
    LOOK_STRAIGHT: "Look straight ahead and hold still.", TURN_LEFT: "Turn your head slightly to your left.", TURN_RIGHT: "Turn your head slightly to your right.",
    LOOK_UP: "Lift your chin slightly.", LOOK_DOWN: "Lower your chin slightly.",
    ONE_FACE_ONLY: "Only one person should be in the frame.", MORE_LIGHT: "Please improve the lighting.",
    REDUCE_LIGHT: "Move away from direct bright light.", EVEN_LIGHTING: "Face an even light source.", CENTER_FACE: "Center your face in the circle.",
    MOVE_CLOSER: "Move closer to the camera.", MOVE_BACK: "Move back so your whole face is visible.", HOLD_STILL: "Hold still for a clear picture.",
    FACE_VISIBILITY_UNVERIFIED: "Keep your eyes and whole face visible. Try again or use your QR code.",
    FRESH_CAPTURE_REQUIRED: "Start again with a fresh camera scan.", CAMERA_CHECK_UNAVAILABLE: "Camera checking is unavailable. Use your QR code.",
    hold: "Hold still", WRONG_DIRECTION: "Follow the arrow slowly, keeping your whole face visible.",
    matched: "Face recognized.", recovery: "We could not confirm your identity. Try again or use your QR code.",
    enrolled: "Face recognition enabled.", review: "Your enrollment needs an authorized review.", deleted: "Enrollment deleted. Your KYC result is unchanged.",
    reenroll: "Update face enrollment", backup: "Approved encrypted backup retention (days)",
    alternative: "You can decline face recognition and use staff-assisted verification. Ask a staff member to check your account or existing KYC result through their authorized console.",
    staff: "Staff-assisted verification",
    unavailable: "Face recognition is not available yet. You can use your QR code instead. Your KYC result is unchanged.",
    auth: "Connect your authenticated account or registered device", org: "Organization ID", token: "Account or device credential", connect: "Connect", signout: "Sign out",
    help: "Use the short-lived credential issued by your service after account or device authentication. A verification link is not sufficient.",
    qr: "Use QR code or get help", cancel: "Cancel", done: "Done", clear: "Clear result", result: "Permitted profile", steps: "steps confirmed",
    display_name: "Display name", internal_reference: "Internal reference", department: "Department", membership_status: "Membership status", kyc_status: "KYC status",
    remaining: "Clearing in", seconds: "seconds", current: "Enrollment status", expired: "This scan expired. Please start again.",
  },
  km: {
    title: "ការសម្គាល់មុខ", optional: "ការចុះឈ្មោះមុខតាមជម្រើស", scan: "ស្កេនមុខ", enable: "បើកការសម្គាល់មុខ",
    purpose: "សម្គាល់អ្នកនៅពេលស្កេនផ្ទាល់នាពេលអនាគត។ ការមិនចុះឈ្មោះមិនប៉ះពាល់ដល់លទ្ធផល KYC របស់អ្នកទេ។",
    fields: "ព័ត៌មានដែលអនុញ្ញាតឱ្យបង្ហាញ", retention: "ទិន្នន័យមុខដែលបានអ៊ិនគ្រីបរបស់អ្នកផុតកំណត់ក្រោយ", days: "ថ្ងៃ។",
    privacy: "រូបភាពចលនាមុខត្រូវបានលុបចោល។ អ្នកអាចដកការយល់ព្រម និងលុបការចុះឈ្មោះនៅទីនេះបានគ្រប់ពេល។ ច្បាប់ចម្លងបម្រុងផុតកំណត់តាមគោលការណ៍រក្សាទុករបស់សេវាកម្ម។ ការចុះឈ្មោះដែលបានលុបនៅតែត្រូវបានរារាំងបន្ទាប់ពីស្ដារមូលដ្ឋានទិន្នន័យ។",
    consent: "ខ្ញុំយល់ព្រមឱ្យសម្គាល់មុខនាពេលអនាគត តាមសេចក្ដីជូនដំណឹងនេះ។", notice: "សេចក្ដីជូនដំណឹង", decline: "មិនចុះឈ្មោះ", disable: "ដកការយល់ព្រម និងលុបការចុះឈ្មោះ",
    ready: "រួចរាល់", positioning: "ដាក់មុខឱ្យចំ", checking: "កំពុងពិនិត្យភាពរស់", recognizing: "កំពុងសម្គាល់មុខ", submitting: "កំពុងបញ្ជាក់ការចុះឈ្មោះ",
    LOOK_STRAIGHT: "មើលត្រង់ទៅមុខ ហើយនៅឱ្យនឹង។", TURN_LEFT: "បង្វិលក្បាលទៅខាងឆ្វេងបន្តិច។", TURN_RIGHT: "បង្វិលក្បាលទៅខាងស្ដាំបន្តិច។",
    LOOK_UP: "ងើយចង្កាបន្តិច។", LOOK_DOWN: "ឱនចង្កាបន្តិច។",
    ONE_FACE_ONLY: "ក្នុងស៊ុមត្រូវមានមនុស្សតែម្នាក់ប៉ុណ្ណោះ។", MORE_LIGHT: "សូមបង្កើនពន្លឺ។", REDUCE_LIGHT: "ចៀសចេញពីពន្លឺខ្លាំងចំមុខ។",
    EVEN_LIGHTING: "បែរមុខទៅរកពន្លឺស្មើគ្នា។", CENTER_FACE: "ដាក់មុខនៅចំកណ្ដាលរង្វង់។", MOVE_CLOSER: "ខិតទៅជិតកាមេរ៉ា។",
    MOVE_BACK: "ថយក្រោយ ដើម្បីឱ្យឃើញមុខទាំងមូល។", HOLD_STILL: "នៅឱ្យនឹង ដើម្បីឱ្យរូបភាពច្បាស់។",
    FACE_VISIBILITY_UNVERIFIED: "ឱ្យកាមេរ៉ាឃើញភ្នែក និងមុខទាំងមូល។ សាកល្បងម្ដងទៀត ឬប្រើកូដ QR។",
    FRESH_CAPTURE_REQUIRED: "សូមចាប់ផ្ដើមស្កេនមុខផ្ទាល់ថ្មីម្ដងទៀត។", CAMERA_CHECK_UNAVAILABLE: "មិនអាចពិនិត្យកាមេរ៉ាបានទេ។ សូមប្រើកូដ QR។",
    hold: "នៅឱ្យនឹង", WRONG_DIRECTION: "ធ្វើតាមព្រួញយឺតៗ ដោយឱ្យឃើញមុខទាំងមូល។",
    matched: "បានសម្គាល់មុខ។", recovery: "យើងមិនអាចបញ្ជាក់អត្តសញ្ញាណរបស់អ្នកបានទេ។ សូមសាកល្បងម្ដងទៀត ឬប្រើកូដ QR។",
    enrolled: "បានបើកការសម្គាល់មុខ។", review: "ការចុះឈ្មោះរបស់អ្នកត្រូវការការពិនិត្យពីអ្នកមានសិទ្ធិ។", deleted: "បានលុបការចុះឈ្មោះ។ លទ្ធផល KYC របស់អ្នកមិនផ្លាស់ប្ដូរទេ។",
    reenroll: "ធ្វើបច្ចុប្បន្នភាពការចុះឈ្មោះមុខ", backup: "រយៈពេលរក្សាច្បាប់ចម្លងបម្រុងដែលបានអ៊ិនគ្រីប (ថ្ងៃ)",
    alternative: "អ្នកអាចមិនប្រើការសម្គាល់មុខ ហើយស្នើឱ្យបុគ្គលិកជួយផ្ទៀងផ្ទាត់។ សូមឱ្យបុគ្គលិកពិនិត្យគណនី ឬលទ្ធផល KYC របស់អ្នក តាមប្រព័ន្ធដែលពួកគេមានសិទ្ធិប្រើ។",
    staff: "ការផ្ទៀងផ្ទាត់ដោយបុគ្គលិក",
    unavailable: "ការសម្គាល់មុខមិនទាន់អាចប្រើបានទេ។ អ្នកអាចប្រើកូដ QR ជំនួស។ លទ្ធផល KYC របស់អ្នកមិនផ្លាស់ប្ដូរទេ។",
    auth: "ភ្ជាប់គណនី ឬឧបករណ៍ដែលបានចុះបញ្ជី", org: "លេខសម្គាល់ស្ថាប័ន", token: "ព័ត៌មានផ្ទៀងផ្ទាត់គណនី ឬឧបករណ៍", connect: "ភ្ជាប់", signout: "ចាកចេញ",
    help: "ប្រើព័ត៌មានផ្ទៀងផ្ទាត់រយៈពេលខ្លីដែលសេវាកម្មផ្ដល់ឱ្យ ក្រោយការចូលគណនី ឬការផ្ទៀងផ្ទាត់ឧបករណ៍។ តំណ KYC មិនគ្រប់គ្រាន់ទេ។",
    qr: "ប្រើកូដ QR ឬស្នើជំនួយ", cancel: "បោះបង់", done: "រួចរាល់", clear: "លុបលទ្ធផលលើអេក្រង់", result: "ព័ត៌មានដែលអនុញ្ញាត", steps: "ជំហានបានបញ្ជាក់",
    display_name: "ឈ្មោះបង្ហាញ", internal_reference: "លេខយោងផ្ទៃក្នុង", department: "ផ្នែក", membership_status: "ស្ថានភាពសមាជិក", kyc_status: "ស្ថានភាព KYC",
    remaining: "លុបលទ្ធផលក្នុង", seconds: "វិនាទី", current: "ស្ថានភាពចុះឈ្មោះ", expired: "ការស្កេននេះផុតកំណត់ហើយ។ សូមចាប់ផ្ដើមម្ដងទៀត។",
  },
} as const;

export default function FaceIdentityPage() {
  const location = useLocation();
  const enroll = location.pathname.endsWith("/enroll");
  const kiosk = location.pathname.endsWith("/kiosk");
  const [language, setLanguage] = useState<"en" | "km">("en");
  const t = words[language];
  const text = (key: string) => t[key as keyof typeof t] ?? t.recovery;
  const [credential, setCredential] = useState<FaceCredential | null>(null);
  const [organization, setOrganization] = useState("");
  const [token, setToken] = useState("");
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("ready");
  const [message, setMessage] = useState("");
  const [step, setStep] = useState<string | null>(null);
  const [progress, setProgress] = useState({ confirmed: 0, total: 4, held: 0, required: 2 });
  const [profile, setProfile] = useState<Record<string, string> | null>(null);
  const [clearAt, setClearAt] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const camera = useCamera("user");
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const stop = camera.stop;
  const clear = () => {
    setProfile(null); setClearAt(0); setRemaining(0); setMessage(""); setPhase("ready"); setStep(null);
    setProgress({ confirmed: 0, total: 4, held: 0, required: 2 });
  };

  useEffect(() => {
    mounted.current = true;
    const reset = () => {
      active.current?.abort(); stop(); setBusy(false); setPhase("ready"); setMessage(""); setStep(null);
      setProfile(null); setClearAt(0); setRemaining(0);
      setProgress({ confirmed: 0, total: 4, held: 0, required: 2 });
    };
    const hide = () => { if (document.visibilityState !== "visible") reset(); };
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", reset);
    return () => { mounted.current = false; active.current?.abort(); stop(); document.removeEventListener("visibilitychange", hide); window.removeEventListener("pagehide", reset); };
  }, [stop]);

  useEffect(() => {
    if (!clearAt) return;
    const update = () => {
      const seconds = Math.max(0, Math.ceil((clearAt - Date.now()) / 1000));
      setRemaining(seconds);
      if (!seconds) {
        setProfile(null); setClearAt(0); setMessage(""); setPhase("ready"); setStep(null);
        setProgress({ confirmed: 0, total: 4, held: 0, required: 2 });
      }
    };
    update();
    const timer = window.setInterval(update, 250);
    return () => window.clearInterval(timer);
  }, [clearAt]);

  const connect = async () => {
    setBusy(true); setMessage("");
    try {
      const next: FaceCredential = { kind: "face", organizationId: organization.trim(), token: token.trim() };
      const info = await api.capabilities(next);
      if (kiosk && info.actor_kind !== "KIOSK") throw new Error("DEVICE_AUTHENTICATION_REQUIRED");
      if (enroll && info.actor_kind !== "PERSON") throw new Error("PERSON_AUTHENTICATION_REQUIRED");
      setCredential(next); setCapabilities(info); setToken("");
      if (info.actor_kind === "PERSON") setEnrollment(await api.enrollment(next));
    } catch { setMessage("recovery"); }
    finally { setBusy(false); }
  };

  const run = async () => {
    if (!credential || !capabilities?.ready || active.current || (enroll && (!consent || !capabilities.enrollment_allowed))) return;
    const abort = new AbortController(); active.current = abort;
    setBusy(true); clear(); setMessage(""); setPhase("positioning");
    try {
      await camera.start();
      if (abort.signal.aborted) return;
      if (!camera.videoRef.current?.srcObject) throw new Error("CAMERA_UNAVAILABLE");
      const purpose = capabilities.actor_kind === "PERSON" ? "self_profile" : capabilities.actor_kind === "KIOSK" ? "staff_lookup" : "customer_service";
      let session = await api.create(credential, enroll, capabilities.notice_version, purpose, abort.signal);
      let missing = 0;
      for (;;) {
        if (abort.signal.aborted || !mounted.current) return;
        if (Date.now() >= Date.parse(session.expires_at)) { setMessage("expired"); return; }
        const tick = performance.now();
        setStep(session.step);
        setPhase(session.step_index === 0 ? "positioning" : "checking");
        const frame = await camera.grabFresh(640, .85);
        if (!frame) {
          if (++missing > 8) throw new Error("CAMERA_STOPPED");
          await new Promise(resolve => window.setTimeout(resolve, 650));
          continue;
        }
        missing = 0;
        session = await api.frame(credential, enroll, session, frame.blob, abort.signal);
        if (abort.signal.aborted || !mounted.current) return;
        const video = camera.videoRef.current;
        const stream = video?.srcObject as MediaStream | null;
        if (performance.now() - frame.capturedAt > 2000 || document.visibilityState !== "visible" || !video || video.paused
            || !stream?.getVideoTracks().some(track => track.readyState === "live" && track.enabled && !track.muted)) {
          throw new Error("STALE_OR_INTERRUPTED_CAMERA_CHECK");
        }
        if (session.outcome) { setMessage("recovery"); return; }
        setProgress({ confirmed: session.step_index, total: session.steps_total,
          held: session.state === "STEP_COMPLETE" ? 0 : session.held, required: session.required });
        setMessage(session.instructions[0] ?? (session.state === "WRONG_DIRECTION" ? "WRONG_DIRECTION" : session.held ? "hold" : ""));
        if (session.state === "READY_TO_COMPLETE") break;
        await new Promise(resolve => window.setTimeout(resolve, Math.max(0, 650 - (performance.now() - tick))));
      }
      setStep(null); setPhase(enroll ? "submitting" : "recognizing"); setMessage("");
      const result = await api.complete(credential, enroll, session.session_id, crypto.randomUUID(), abort.signal);
      if (abort.signal.aborted || !mounted.current) return;
      if (enroll) {
        setMessage(result.status === "ACTIVE" ? "enrolled" : result.status === "DUPLICATE_REVIEW" ? "review" : "recovery");
        setEnrollment(await api.enrollment(credential)); setConsent(false);
      } else if (result.outcome === "MATCHED") {
        const reference = await api.result(credential, session.session_id, abort.signal);
        if (reference.outcome !== "MATCHED" || !reference.recognition_reference) { setMessage("recovery"); return; }
        const disclosure = await api.profile(credential, session.session_id, reference.recognition_reference, crypto.randomUUID(), abort.signal);
        if (abort.signal.aborted || !mounted.current) return;
        setProfile(disclosure.profile); setClearAt(Date.now() + Math.min(disclosure.clear_after_seconds, kiosk ? 15 : 30) * 1000);
        setMessage("matched");
      } else setMessage("recovery");
      setPhase("done");
    } catch {
      if (!abort.signal.aborted && mounted.current) setMessage("recovery");
    } finally {
      if (active.current === abort) active.current = null;
      if (mounted.current) { setBusy(false); stop(); }
    }
  };

  const withdraw = async () => {
    if (!credential || busy) return;
    setBusy(true); clear();
    try { await api.withdraw(credential); setEnrollment(await api.enrollment(credential)); setMessage("deleted"); setConsent(false); }
    catch { setMessage("recovery"); }
    finally { setBusy(false); }
  };
  const button = "min-h-11 rounded-lg border border-slate-500 px-5 py-3 font-medium disabled:opacity-40";
  return <main lang={language} className="min-h-screen bg-slate-950 px-4 py-8 font-body leading-relaxed text-slate-100" style={{ lineHeight: language === "km" ? 1.9 : 1.6 }}>
    <div className="mx-auto max-w-xl space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4"><h1 className="text-2xl font-semibold">{enroll ? t.optional : t.title}</h1>
        <select className="min-h-11 rounded bg-slate-800 px-3" aria-label="Language" value={language} onChange={event => setLanguage(event.target.value as "en" | "km")}>
          <option value="en">English</option><option value="km">ខ្មែរ</option></select></header>
      {!credential ? <form className="space-y-4" onSubmit={event => { event.preventDefault(); void connect(); }}>
        <h2 className="text-lg">{t.auth}</h2><p>{t.help}</p>
        <label className="block">{t.org}<input required autoComplete="off" className="mt-2 block w-full rounded bg-slate-800 p-3" value={organization} onChange={event => setOrganization(event.target.value)} /></label>
        <label className="block">{t.token}<input type="password" required autoComplete="off" className="mt-2 block w-full rounded bg-slate-800 p-3" value={token} onChange={event => setToken(event.target.value)} /></label>
        <button className={button} disabled={busy}>{t.connect}</button>
      </form> : <>
        <div className="flex flex-wrap justify-between gap-3"><p>{enrollment ? `${t.current}: ${enrollment.active ? "✓" : "—"}` : t.ready}</p>
          <button className={button} onClick={() => { active.current?.abort(); stop(); clear(); setCredential(null); setCapabilities(null); setBusy(false); }}>{t.signout}</button></div>
        {!capabilities?.ready && <p role="status" className="rounded-lg border border-amber-500 p-4">⚠ {t.unavailable}</p>}
        {enroll && capabilities?.kyc_status === "VERIFIED" && <section className="space-y-4 rounded-xl border border-slate-600 p-5">
          <p>{t.purpose}</p><p>{t.fields}: {capabilities.permitted_fields.map(text).join(", ")}</p>
          <p>{t.retention} {capabilities.retention_days ?? "—"} {t.days}</p><p>{t.privacy}</p>
          <p>{t.backup}: {capabilities.backup_retention_days ?? "—"}</p>
          <p>{t.notice}: {capabilities.notice_version ?? "—"}</p>
          <label className="flex items-start gap-3"><input type="checkbox" className="mt-2 h-5 w-5" checked={consent} onChange={event => setConsent(event.target.checked)} disabled={busy} /><span>{t.consent}</span></label>
          <Link to="/" className="inline-block min-h-11 underline">{t.decline}</Link>
        </section>}
        <section className="space-y-4" aria-label={t.scan}>
          <FaceScanRing videoRef={camera.videoRef} cameraState={camera.state} arcs={{}} theme="dark" showCamera={busy}
            cue={step && step !== "LOOK_STRAIGHT" ? step as Direction : null}
            progress={progress.confirmed / progress.total} feedback={progress.held ? "hold" : "active"} />
          <h2 className="text-center text-xl font-semibold" aria-live="polite">{step ? text(step) : text(phase)}</h2>
          {busy && <p className="text-center">{progress.confirmed} / {progress.total} {t.steps} · {progress.held} / {progress.required} {t.hold}</p>}
          {camera.error && <p role="alert">⚠ {language === "en" ? camera.error : t.recovery}</p>}
          <button className={`${button} w-full bg-blue-700`} disabled={busy || !capabilities?.ready || (enroll && (!consent || !capabilities.enrollment_allowed))} onClick={() => void run()}>{enroll ? enrollment?.active ? t.reenroll : t.enable : t.scan}</button>
          {busy && <button className={`${button} w-full`} onClick={() => { active.current?.abort(); stop(); setBusy(false); setPhase("ready"); }}>{t.cancel}</button>}
        </section>
        {enroll && enrollment && enrollment.status !== "NOT_ENROLLED" && <button className={`${button} w-full`} disabled={busy} onClick={() => void withdraw()}>{t.disable}</button>}
      </>}
      {message && <p role="status" aria-live="polite" className="rounded-lg border border-slate-500 p-4">{["matched", "enrolled", "deleted"].includes(message) ? "✓" : "⚠"} {text(message)}</p>}
      {profile && <section aria-label={t.result} className="space-y-4 rounded-xl border border-emerald-500 p-5"><h2 className="text-xl font-semibold">{t.result}</h2>
        <dl>{Object.entries(profile).map(([field, value]) => <div key={field} className="py-2"><dt className="text-slate-300">{text(field)}</dt><dd className="break-words text-lg">{value}</dd></div>)}</dl>
        <p>{t.remaining} {remaining} {t.seconds}</p><button className={button} onClick={clear}>{t.clear}</button></section>}
      <section className="space-y-3 border-t border-slate-700 pt-4"><p>{t.alternative}</p>
        <Link to="/signin" className="inline-flex min-h-11 items-center underline" onClick={() => { active.current?.abort(); stop(); clear(); }}>{t.staff}</Link></section>
    </div>
  </main>;
}
