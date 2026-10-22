import { useCallback, useEffect, useRef, useState } from "react";
import type { Client } from "@/api/client";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { LivenessChallenge, LivenessResult, SessionStatus } from "@/api/types";
import { instructionText } from "@/lib/catalog";
import { useCamera, useLightHint } from "@/lib/useCamera";
import { LivenessFeedback } from "@/lib/livenessFeedback";
import type { FeedbackPhase } from "@/lib/livenessFeedback";
import FaceScanRing from "@/pages/verify/components/FaceScanRing";
import type { Direction } from "@/pages/verify/components/FaceScanRing";

interface LivenessStageProps {
  credential: Client;
  sessionId: string;
  onSessionChanged: (status: SessionStatus) => void;
}

// Guided active liveness. The server picks a random sequence of head movements. For each step the page
// asks the server, a frame at a time, whether the movement is done (same geometry as the final check),
// and moves on once HOLD_FRAMES frames in a row show it. Raw frames are then submitted with their step
// index; the server judges them again, in memory, and keeps none.

const STEP_TEXT: Record<string, { say: string; more?: string; wrong?: string }> = {
  LOOK_STRAIGHT: { say: "Look straight ahead." },
  TURN_LEFT: { say: "Turn your head to your left.",
    more: "Keep the camera still and turn only your head slightly.", wrong: "Turn to your left, following the arrow." },
  TURN_RIGHT: { say: "Turn your head to your right.",
    more: "Keep the camera still and turn only your head slightly.", wrong: "Turn to your right, following the arrow." },
  LOOK_UP: { say: "Lift your chin slightly.",
    more: "Keep your eyes and whole face visible to the camera.", wrong: "Lift your chin slightly, following the arrow." },
  LOOK_DOWN: { say: "Lower your chin slightly.",
    more: "Keep your eyes and whole face visible to the camera.", wrong: "Lower your chin slightly, following the arrow." },
};
const FACE_TEXT: Record<string, string> = {
  NO_FACE: "Tracking lost. Bring your face back into the circle.",
  MULTIPLE_FACES: "Only your face should be in view.",
  UNCLEAR: "Hold still with your whole face visible.",
};
const RECOVERY_TEXT: Record<string, string> = {
  MORE_LIGHT: "Move to brighter, even light.", REDUCE_LIGHT: "Move away from direct bright light.",
  EVEN_LIGHTING: "Face an even light source.", MOVE_CLOSER: "Move closer to the camera.",
  MOVE_BACK: "Move back so your whole face is visible.", CENTER_FACE: "Center your face in the circle.",
  HOLD_STILL: "Hold still for a clear picture.", FACE_CAMERA: "Keep your eyes and whole face visible.",
};
const HOLD_FRAMES = 2;
const GUIDE_INTERVAL_MS = 650;  // ≈90 checks a minute, inside the client-token rate limit
const HELP_AFTER_MS = 15000;

type Kind = "baseline" | "move" | "center";
type StopReason = "CANCELLED" | "RESTART" | "CHALLENGE_CLOSED" | "ERROR";

class LivenessStop extends Error {
  readonly reason: StopReason;
  readonly failure?: ApiError;

  constructor(reason: StopReason, failure?: ApiError) {
    super(reason);
    this.reason = reason;
    this.failure = failure;
  }
}

interface Run {
  challenge: LivenessChallenge;
  baseline: Blob[];
  stopped: StopReason | null;
  abort: AbortController;
  calibration?: string;
  useCalibration: boolean;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)));

function usableCamera(video: HTMLVideoElement | null): boolean {
  const stream = video?.srcObject as MediaStream | null;
  return document.visibilityState === "visible" && !!video && !video.paused && video.readyState >= 2
    && typeof stream?.getVideoTracks === "function"
    && stream.getVideoTracks().some((track) => track.readyState === "live" && track.enabled && !track.muted);
}

export default function LivenessStage({ credential, sessionId, onSessionChanged }: LivenessStageProps) {
  const camera = useCamera("user");
  const cameraErrorRef = useRef<string | null>(null);
  const runRef = useRef<Run | null>(null);
  const preflightRef = useRef<AbortController | null>(null);
  const preflightReasonRef = useRef<StopReason | null>(null);
  const mountedRef = useRef(true);
  const scanRef = useRef<HTMLDivElement | null>(null);
  const [running, setRunning] = useState(false);
  const [checking, setChecking] = useState(false);
  const [attemptsRemaining, setAttemptsRemaining] = useState<number | null>(null);
  const [step, setStep] = useState<{ text: string; kind: Kind | "checking" | "complete"; index: number; total: number } | null>(null);
  const [coach, setCoach] = useState<{ text: string; progress: number | null; good: boolean; phase?: FeedbackPhase; held?: number; required?: number }>({ text: "", progress: null, good: false });
  const [dots, setDots] = useState<{ steps: string[]; current: number; done: number } | null>(null);
  const [help, setHelp] = useState<string | null>(null);
  const [muted, setMuted] = useState(true);
  const speechAvailable = typeof window !== "undefined" && "speechSynthesis" in window;
  const [outcome, setOutcome] = useState<{ tone: "ok" | "retry"; title: string; detail: string; instructions: string[] } | null>(null);
  const lightHint = useLightHint(camera.videoRef, camera.state === "live" && !running);
  const { start, stop: stopCamera, grabFresh } = camera;

  useEffect(() => { cameraErrorRef.current = camera.error; }, [camera.error]);

  useEffect(() => {
    if (!speechAvailable) return;
    window.speechSynthesis.cancel();
    if (!muted && running && step && !["checking", "complete"].includes(step.kind)) {
      const speech = new SpeechSynthesisUtterance(step.text);
      speech.lang = "en-US";
      speech.rate = 0.95;
      window.speechSynthesis.speak(speech);
    }
    return () => window.speechSynthesis.cancel();
  }, [muted, running, speechAvailable, step]);

  useEffect(() => {
    mountedRef.current = true;
    start();
    return () => {
      mountedRef.current = false;
      preflightRef.current?.abort();
      const run = runRef.current;
      if (run) {
        run.stopped = "CANCELLED";
        run.abort.abort();
      }
    };
  }, [start]);

  useEffect(() => {
    if (!running) return;
    const scan = scanRef.current;
    const previous = document.body.style.overflow;
    const previousFocus = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => scan?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus());
    document.body.style.overflow = "hidden";
    const containFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const controls = scan?.querySelectorAll<HTMLElement>("button:not(:disabled), [href], [tabindex='0']");
      if (!controls?.length) { event.preventDefault(); return; }
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !scan?.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", containFocus);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", containFocus);
      document.body.style.overflow = previous;
      if (previousFocus?.isConnected && previousFocus !== document.body && previousFocus !== document.documentElement) previousFocus.focus();
      else scan?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    };
  }, [running]);

  const showStep = useCallback((run: Run, index: number, kind: Kind) => {
    const steps = run.challenge.steps;
    const text = STEP_TEXT[steps[index].step] ?? { say: steps[index].instruction };
    setStep({
      text: kind === "move" ? text.say : "Look straight ahead.",
      kind, index, total: steps.length - 1,
    });
    setHelp(null);
    setCoach({ text: kind === "move" ? "Move slowly, keeping your whole face visible." : kind === "center" ? "Return to your starting position." : "Center your face in the circle.",
      progress: 0, good: false, phase: "active", held: 0, required: run.challenge.frames.stable_frames_per_step ?? HOLD_FRAMES });
  }, []);

  // Tips appear beside the camera while the check keeps running, so the step completes as soon as it is done.
  const showTips = useCallback((run: Run, index: number, kind: Kind) => {
    const stepName = run.challenge.steps[index].step;
    setHelp(kind === "move" ? STEP_TEXT[stepName]?.more ?? "Keep the camera still and move only your head."
      : "Hold the camera at eye level, about an arm's length away.");
  }, []);

  // Network inference is serial; each request uses the newest decoded camera frame.
  // Hysteresis affects coaching only. Evidence still requires strict positive server results.
  const followStep = useCallback(async (run: Run, index: number, kind: Kind): Promise<Blob[]> => {
    const name = run.challenge.steps[index].step;
    const text = STEP_TEXT[name] ?? { say: run.challenge.steps[index].instruction };
    const required = run.challenge.frames.stable_frames_per_step ?? HOLD_FRAMES;
    const feedback = new LivenessFeedback(required, kind === "move");
    showStep(run, index, kind);
    let held: Blob[] = [];
    const started = Date.now();
    const expiresAt = Date.parse(run.challenge.expires_at);
    let lastFrame = started;
    let tipsShown = false;
    for (;;) {
      if (run.stopped) throw new LivenessStop(run.stopped);
      if (Date.now() >= expiresAt) throw new LivenessStop("CHALLENGE_CLOSED");
      const tick = Date.now();
      const frame = document.visibilityState === "visible" ? await grabFresh(640, 0.85) : null;
      if (frame) {
        lastFrame = Date.now();
        try {
          const body = await kycApi.livenessGuide(credential, sessionId, run.challenge, index, frame.blob,
            run.baseline[0] ?? null, run.abort.signal, run.baseline.slice(1), feedback.holding, run.calibration);
          if (run.stopped) throw new LivenessStop(run.stopped);
          if (!mountedRef.current) throw new LivenessStop("CANCELLED");
          if (Date.now() >= expiresAt) throw new LivenessStop("CHALLENGE_CLOSED");
          if (run.useCalibration && typeof body.calibration === "string") run.calibration = body.calibration;
          const usable = usableCamera(camera.videoRef.current) && body.step === name;
          const result = feedback.update(body, frame, performance.now(), usable);
          if (result.accepted) held = feedback.held === 1 ? [frame.blob] : [...held, frame.blob];
          else held = [];
          const recovery = body.instructions?.map((code) => RECOVERY_TEXT[code]).find(Boolean);
          const hint = !result.fresh ? "Waiting for a fresh camera check. Hold still."
            : body.face !== "OK" ? recovery ?? FACE_TEXT[body.face] ?? FACE_TEXT.UNCLEAR
            : result.complete ? (kind === "move" ? "Step confirmed." : "Position confirmed.")
            : result.phase === "hold" ? (kind === "move" ? "Movement detected. Hold still." : "Centered. Hold still.")
            : result.wrong ? text.wrong ?? "Follow the arrow slowly."
            : kind === "center" ? "Return to your starting position."
            : kind === "baseline" ? "Center your face and hold still."
            : feedback.progress > 0.1 ? "Movement recognized. Keep moving slowly."
            : "Move only your head slightly, keeping your face visible.";
          setCoach({ text: hint, progress: feedback.progress, good: result.accepted,
            phase: result.phase, held: feedback.held, required });
          if (result.complete) {
            setHelp(null);
            if (kind === "move") {
              setStep({ text: "Movement complete", kind: "move", index, total: run.challenge.steps.length - 1 });
              setDots({ steps: run.challenge.steps.map((item) => item.step), current: index, done: index + 1 });
            }
            // A short confirmation transition; this delay never supplies evidence.
            await wait(220);
            if (run.stopped) throw new LivenessStop(run.stopped);
            if (Date.now() >= expiresAt) throw new LivenessStop("CHALLENGE_CLOSED");
            return held;
          }
        } catch (caught) {
          if (caught instanceof LivenessStop) throw caught;
          if (run.stopped) throw new LivenessStop(run.stopped);
          const failure = caught as ApiError;
          if (failure.status === 409 || failure.status === 404) throw new LivenessStop("CHALLENGE_CLOSED", failure);
          const refreshCalibration = failure.reasonCode === "CALIBRATION_UNUSABLE" && !!run.calibration;
          if (refreshCalibration) {
            run.calibration = undefined;
            run.useCalibration = false;
          } else if (failure.status !== 0 && failure.status !== 429 && failure.status < 500) throw new LivenessStop("ERROR", failure);
          feedback.reset();
          held = [];
          setCoach({ text: refreshCalibration ? "Refreshing the camera check. Hold still."
            : failure.status === 429 ? "Checks are busy. Hold still while we retry." : "The connection is slow. Hold still while we retry.",
            progress: 0, good: false, phase: "correction", held: 0, required });
        }
      } else {
        feedback.reset();
        held = [];
        setCoach({ text: document.visibilityState !== "visible" ? "Return to the scan to continue." : cameraErrorRef.current ?? "Waiting for the camera. Hold still.",
          progress: 0, good: false, phase: "correction", held: 0, required });
        if (Date.now() - lastFrame > 5000 && document.visibilityState === "visible") {
          throw new LivenessStop("ERROR", new ApiError(0, "The camera stopped. Restart the camera and try again.", "CAMERA_STOPPED", null));
        }
      }
      if (!tipsShown && Date.now() - started > HELP_AFTER_MS) {
        showTips(run, index, kind);
        tipsShown = true;
      }
      await wait(GUIDE_INTERVAL_MS - (Date.now() - tick));
    }
  }, [camera.videoRef, credential, grabFresh, sessionId, showStep, showTips]);

  const finish = () => {
    runRef.current = null;
    preflightRef.current = null;
    setRunning(false);
    setHelp(null);
    setStep(null);
    setDots(null);
    setCoach({ text: "", progress: null, good: false });
  };

  const show = async (result: LivenessResult) => {
    setAttemptsRemaining(result.attempts_remaining);
    const retry = result.retry_allowed && result.status === "LIVENESS_REQUIRED";
    setOutcome({
      tone: result.result === "PASS" ? "ok" : "retry",
      title: retry ? "Please try the movement check again" : result.result === "FAIL" ? "The movement check did not pass"
        : result.result === "PASS" ? "Movement check passed" : "Movement check recorded",
      detail: retry ? `${result.attempts_remaining} attempts left.` : result.result === "FAIL" ? "The server could not verify this movement check."
        : result.result === "PASS" ? "The server accepted the movement check. Identity verification continues."
        : "Movement capture received. Further review is required.",
      instructions: result.instructions ?? [],
    });
    if (!retry) {
      if (result.result !== "FAIL") {
        setRunning(true);
        setStep({ text: result.result === "PASS" ? "Movement check passed" : "Movement capture received", kind: "complete", index: 0, total: 0 });
        setCoach({ text: result.result === "PASS" ? "Server check passed. Verification continues." : "Submitted for review. Identity is not yet verified.", progress: 1, good: true, phase: "confirmed" });
        await wait(900);
        if (!mountedRef.current) return;
        finish();
      }
      stopCamera();
      onSessionChanged(result.status);
    }
  };

  const run = async (): Promise<void> => {
    if (runRef.current || preflightRef.current || camera.state !== "live" || attemptsRemaining === 0) return;
    setOutcome(null);
    setRunning(true);
    setStep({ text: "Position your face in the circle", kind: "baseline", index: 0, total: 0 });
    setCoach({ text: "Look straight ahead and hold still.", progress: 0, good: false, phase: "active", held: 0, required: HOLD_FRAMES });
    const preflight = new AbortController();
    preflightReasonRef.current = null;
    preflightRef.current = preflight;
    let baseline: Blob[] = [];
    let challenge: LivenessChallenge;
    try {
      // Positioning and technical retries happen before a challenge consumes an attempt or starts its TTL.
      const started = Date.now();
      let lastFrame = Date.now();
      const feedback = new LivenessFeedback(HOLD_FRAMES, false);
      while (baseline.length < HOLD_FRAMES) {
        if (preflight.signal.aborted) throw new LivenessStop("CANCELLED");
        const tick = Date.now();
        const frame = document.visibilityState === "visible" ? await grabFresh(640, 0.85) : null;
        if (frame) {
          lastFrame = Date.now();
          const body = await kycApi.livenessPosition(credential, sessionId, frame.blob, preflight.signal);
          if (preflight.signal.aborted) throw new LivenessStop("CANCELLED");
          setAttemptsRemaining(body.attempts_remaining);
          const result = feedback.update({ step: "LOOK_STRAIGHT", face: body.face,
            state: body.state === "READY" ? "CENTERED" : null, progress: body.state === "READY" ? 1 : 0 },
            frame, performance.now(), usableCamera(camera.videoRef.current));
          if (result.accepted) baseline = feedback.held === 1 ? [frame.blob] : [...baseline, frame.blob];
          else baseline = [];
          setCoach({ text: !result.fresh ? "Waiting for a fresh camera check. Hold still."
            : result.accepted ? "Hold still." : RECOVERY_TEXT[body.instructions[0]] ?? FACE_TEXT[body.face]
            ?? instructionText(body.instructions[0] ?? "FACE_CAMERA"),
            progress: baseline.length / HOLD_FRAMES, good: result.accepted, phase: result.accepted ? "hold" : "correction",
            held: baseline.length, required: HOLD_FRAMES });
        } else {
          feedback.reset();
          baseline = [];
          setCoach({ text: document.visibilityState === "visible" ? cameraErrorRef.current ?? "Waiting for the camera. Hold still." : "Return to the scan to continue.",
            progress: 0, good: false, phase: "correction", held: 0, required: HOLD_FRAMES });
          if (Date.now() - lastFrame > 5000 && document.visibilityState === "visible") {
            throw new ApiError(0, "The camera stopped. Restart the camera and try again.", "CAMERA_STOPPED", null);
          }
        }
        if (Date.now() - started > HELP_AFTER_MS) setHelp("Hold the camera at eye level, with even light on your face.");
        if (baseline.length < HOLD_FRAMES) await wait(GUIDE_INTERVAL_MS - (Date.now() - tick));
      }
      setHelp(null);
      challenge = await kycApi.livenessChallenge(credential, sessionId, preflight.signal);
      if (preflight.signal.aborted || !mountedRef.current) throw new LivenessStop("CANCELLED");
      setAttemptsRemaining(challenge.attempts_remaining);
      const required = challenge.frames.stable_frames_per_step ?? HOLD_FRAMES;
      if (!Number.isInteger(required) || required < 2 || required > 3 || challenge.steps.length * required > challenge.frames.max) {
        throw new ApiError(422, "This challenge requires an unsupported capture format. Please contact support.", "CHALLENGE_FORMAT_UNSUPPORTED", null);
      }
    } catch (caught) {
      if (!mountedRef.current) return;
      finish();
      if (preflight.signal.aborted || caught instanceof LivenessStop) {
        if (preflightReasonRef.current === "RESTART") return run();
        return;
      }
      const failure = caught as ApiError;
      if (failure.status === 409) return onSessionChanged("LIVENESS_REQUIRED");
      const exhausted = failure.reasonCode === "LIVENESS_ATTEMPTS_EXCEEDED";
      if (exhausted) setAttemptsRemaining(0);
      setOutcome({ tone: "retry", title: exhausted ? "No attempts left" : "Could not start the check",
                   detail: exhausted ? "Ask the service that sent you for a new verification link." : failure.message, instructions: [] });
      return;
    }
    preflightRef.current = null;
    const current: Run = { challenge, baseline, stopped: null, abort: new AbortController(), useCalibration: true };
    runRef.current = current;
    const stepNames = challenge.steps.map((item) => item.step);
    const frames: Blob[] = [];
    const indexes: number[] = [];
    try {
      setDots({ steps: stepNames, current: 0, done: 0 });
      const acceptedBaseline = await followStep(current, 0, "baseline");
      current.baseline = acceptedBaseline;
      current.calibration = undefined;
      acceptedBaseline.forEach((blob) => { frames.push(blob); indexes.push(0); });
      for (let index = 1; index < challenge.steps.length; index++) {
        setDots({ steps: stepNames, current: index, done: index });
        const done = await followStep(current, index, "move");
        done.forEach((blob) => { frames.push(blob); indexes.push(index); });
        setDots({ steps: stepNames, current: index + 1, done: index + 1 });
        if (index < challenge.steps.length - 1) await followStep(current, 0, "center");
      }
    } catch (caught) {
      if (!mountedRef.current) return;
      const stop = caught instanceof LivenessStop ? caught : new LivenessStop("ERROR");
      current.abort.abort();
      finish();
      if (stop.reason === "RESTART") return run();
      if (stop.reason === "CHALLENGE_CLOSED") {
        setOutcome({ tone: "retry", title: "Time ran out for this check",
                     detail: challenge.attempts_remaining > 0 ? "Try again when you are ready."
                       : "Ask the service that sent you for a new verification link.", instructions: [] });
      } else if (stop.reason === "ERROR") {
        setOutcome({ tone: "retry", title: "Something went wrong", detail: stop.failure?.message ?? "Try again.", instructions: [] });
      }
      return;
    }
    setChecking(true);
    setStep({ text: "Submitting", kind: "checking", index: 0, total: 0 });
    setCoach({ text: "Movement complete. Waiting for the server result.", progress: null, good: false, phase: "active" });
    try {
      const result = await kycApi.submitLiveness(credential, sessionId, challenge, frames, indexes, current.abort.signal);
      if (!mountedRef.current) return;
      finish();
      await show(result);
    } catch (caught) {
      if (!mountedRef.current) return;
      finish();
      const failure = caught as ApiError;
      setOutcome({ tone: "retry", title: "The check could not be completed", detail: failure.message, instructions: [] });
    } finally {
      if (mountedRef.current) setChecking(false);
    }
  };

  const stopRun = (reason: StopReason) => {
    if (preflightRef.current) {
      preflightReasonRef.current = reason;
      preflightRef.current.abort();
      return;
    }
    const current = runRef.current;
    if (!current) return;
    current.stopped = reason;
    current.abort.abort();
    setHelp(null);
  };

  // Confirmed steps fill the whole circumference; only the current direction is shown.
  const moves = dots ? dots.steps.length - 1 : 0;
  const confirmed = Math.max(0, (dots?.done ?? 0) - 1);
  const ringProgress = moves ? Math.min(1, (confirmed + (step?.kind === "move" && step.index === dots?.current && coach.phase !== "confirmed" ? coach.progress ?? 0 : 0)) / moves) : 0;
  const cue = running && step?.kind === "move" ? dots?.steps[step.index] as Direction : null;
  const checkingNow = step?.kind === "checking";
  const complete = step?.kind === "complete";
  const phase = coach.phase ?? "active";
  const held = coach.held ?? 0;
  const required = coach.required ?? HOLD_FRAMES;
  const confirmedProgress = moves ? confirmed / moves : 0;
  const feedbackTone = phase === "confirmed" ? "text-[#86EFAC]" : phase === "correction" ? "text-[#FDE68A]" : "text-[#93C5FD]";

  // One mounted video and fixed regions. The overlay itself can scroll at every breakpoint.
  return (
    <div className="flex flex-col gap-4">
      <div className={running ? "fixed inset-0 z-[100] overflow-y-auto overscroll-contain bg-[#0F172A] px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))] text-white"
                              : "flex flex-col items-center gap-5 px-2 py-4 text-center"}
           ref={scanRef} role={running ? "dialog" : undefined} aria-modal={running ? true : undefined} aria-label={running ? "Face movement check" : undefined}
           data-testid="liveness-scan" data-phase={checkingNow ? "checking" : complete ? "complete" : step?.kind === "center" ? "center" : phase}>
        <div className={running ? "mx-auto flex min-h-full w-full max-w-lg flex-col items-stretch gap-4" : "contents"}>
          {running && (
            <div className="sticky top-0 z-10 flex min-h-11 shrink-0 items-center justify-between gap-3 bg-[#0F172A]">
              <button type="button" onClick={() => stopRun("CANCELLED")} disabled={checking}
                      className="min-h-11 rounded-lg px-2 font-label text-sm text-[#BFDBFE] hover:bg-white/10 disabled:opacity-60">Cancel</button>
              <span className="font-label text-sm text-white" aria-label="Completed challenges">
                {moves ? `${confirmed} of ${moves} done` : "Positioning"}
              </span>
              {speechAvailable && (
                <button type="button" onClick={() => setMuted((value) => !value)} aria-pressed={!muted}
                        aria-label={muted ? "Unmute spoken guidance" : "Mute spoken guidance"}
                        className="min-h-11 rounded-lg px-2 font-label text-sm text-[#BFDBFE] hover:bg-white/10">
                  <i className={`${muted ? "ri-volume-mute-line" : "ri-volume-up-line"} mr-1`} aria-hidden="true" />
                  {muted ? "Sound off" : "Sound on"}
                </button>
              )}
            </div>
          )}
          <FaceScanRing videoRef={camera.videoRef} cameraState={camera.state} arcs={{}} progress={running ? ringProgress : 0}
                        confirmedProgress={running ? confirmedProgress : 0}
                        theme={running ? "dark" : "light"} showCamera complete={complete || (checkingNow && moves > 0 && confirmed === moves)}
                        aligned={running && step?.kind !== "move" && coach.good} cue={cue} feedback={phase}
                        center={camera.state !== "live" ? <i className={`${camera.state === "starting" ? "ri-loader-4-line animate-spin motion-reduce:animate-none" : "ri-camera-off-line"} text-5xl leading-none text-slate-500`} aria-hidden="true" />
                          : checkingNow ? <span className="flex h-16 w-16 items-center justify-center rounded-full bg-slate-900/90">
                              <i className="ri-loader-4-line animate-spin text-3xl text-white motion-reduce:animate-none" aria-hidden="true" />
                            </span> : undefined} />
          {running ? (
            <>
              <div className="min-h-[100px] shrink-0 text-center" aria-live="polite" aria-atomic="true" data-testid="liveness-status">
                <p className="font-heading text-xl font-semibold leading-snug text-white">{step?.text}</p>
                <p className={`mt-2 font-label text-[15px] leading-relaxed ${feedbackTone}`}>
                  {phase === "confirmed" ? <span aria-hidden="true">✓ </span> : phase === "correction" ? <span aria-hidden="true">⚠ </span> : null}
                  {coach.text}
                </p>
              </div>
              <div className="mx-auto min-h-[52px] w-full max-w-sm shrink-0" data-testid="liveness-hold">
                {!checkingNow && !complete && (
                  <>
                    <div className="mb-2 flex items-center justify-between font-label text-sm text-slate-200">
                      <span>{phase === "confirmed" ? "✓ Hold confirmed" : "Hold progress"}</span>
                      <span>{Math.min(held, required)} of {required} checks</span>
                    </div>
                    <div role="progressbar" aria-label="Valid hold checks" aria-valuemin={0} aria-valuemax={required} aria-valuenow={Math.min(held, required)}
                         aria-valuetext={`${Math.min(held, required)} of ${required} fresh checks confirmed`}
                         className="h-2 overflow-hidden rounded-full bg-slate-600">
                      <div className={`h-full rounded-full transition-[width] duration-200 motion-reduce:transition-none ${phase === "confirmed" ? "bg-[#4ADE80]" : "bg-[#60A5FA]"}`}
                           style={{ width: `${Math.min(held / required, 1) * 100}%` }} />
                    </div>
                  </>
                )}
              </div>
              <div className="mx-auto flex min-h-[9rem] w-full max-w-sm shrink-0 flex-col justify-center rounded-xl px-3 py-2 text-center" data-testid="liveness-help">
                {help && <>
                  <p className="font-label text-sm leading-relaxed text-slate-200">{help}</p>
                  <button type="button" onClick={() => stopRun("RESTART")}
                          className="mt-1 min-h-11 self-center rounded-lg px-3 font-label text-sm text-[#BFDBFE] hover:bg-white/10">Start over</button>
                </>}
              </div>
            </>
          ) : (
            <>
              <div className="max-w-sm">
                <h2 className="font-heading text-xl font-semibold text-foreground-950">Movement check</h2>
                <p className="mt-1.5 font-label text-[15px] leading-relaxed text-foreground-500">
                  Position your face in the circle, then follow each arrow with a slight head movement.
                  Keep the camera still and your whole face visible. No video is kept.
                </p>
              </div>
              {camera.state === "live" && lightHint && <p className="font-label text-sm text-accent-800">{lightHint}</p>}
              {camera.state === "starting" && (
                <p className="font-label text-sm text-foreground-500"><i className="ri-loader-4-line mr-1 animate-spin motion-reduce:animate-none" aria-hidden="true" />Starting camera…</p>
              )}
              {camera.state === "unavailable" ? (
                <div className="flex flex-col items-center gap-2">
                  <p className="max-w-sm font-label text-sm text-foreground-600">{camera.error ?? "This check needs a live camera. Allow camera access and try again."}</p>
                  <button type="button" onClick={camera.start}
                          className="inline-flex min-h-11 items-center gap-2 rounded-full border border-background-300 px-5 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">
                    <i className="ri-refresh-line text-base leading-none" aria-hidden="true" />Retry camera
                  </button>
                </div>
              ) : (
                <button type="button" onClick={run} disabled={camera.state !== "live" || attemptsRemaining === 0}
                        className="w-full max-w-sm rounded-full bg-primary-500 px-6 py-3.5 font-label text-base font-semibold text-background-50 transition-colors duration-200 hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none">
                  {attemptsRemaining === 0 ? "New verification link needed" : outcome ? "Try again" : "Start face scan"}
                </button>
              )}
            </>
          )}
        </div>
      </div>
      {outcome && (
        <div className={`rounded-lg border p-4 ${outcome.tone === "ok" ? "border-primary-200 bg-primary-50" : "border-accent-200 bg-accent-50"}`} role="status">
          <p className={`font-label text-sm font-semibold ${outcome.tone === "ok" ? "text-primary-900" : "text-accent-900"}`}>{outcome.title}</p>
          <p className="mt-1 font-label text-sm text-foreground-700">{outcome.detail}</p>
          {outcome.instructions.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5 font-label text-xs text-foreground-700">
              {outcome.instructions.map((code) => <li key={code}>{instructionText(code)}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
