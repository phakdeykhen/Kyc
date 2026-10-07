import { useCallback, useEffect, useRef, useState } from "react";
import type { Client } from "@/api/client";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { LivenessChallenge, LivenessResult, SessionStatus } from "@/api/types";
import { instructionText } from "@/lib/catalog";
import { useCamera, useLightHint } from "@/lib/useCamera";
import FaceScanRing from "@/pages/verify/components/FaceScanRing";
import type { ArcState, Direction } from "@/pages/verify/components/FaceScanRing";

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
  LOOK_STRAIGHT: { say: "Look straight at the camera" },
  TURN_LEFT: { say: "Turn your head to your left",
    more: "Turn further, as if looking over your left shoulder.", wrong: "That's the other way. Turn to your left." },
  TURN_RIGHT: { say: "Turn your head to your right",
    more: "Turn further, as if looking over your right shoulder.", wrong: "That's the other way. Turn to your right." },
  LOOK_UP: { say: "Tilt your head up",
    more: "Lift your chin higher, as if looking at the ceiling.", wrong: "That's down. Tilt your head up instead." },
  LOOK_DOWN: { say: "Tilt your head down",
    more: "Lower your chin further, as if looking at the floor.", wrong: "That's up. Tilt your head down instead." },
};
const FACE_TEXT: Record<string, string> = {
  NO_FACE: "We can't see your face. Keep it inside the circle.",
  MULTIPLE_FACES: "Only your face should be in view.",
  UNCLEAR: "Hold still with your whole face in the circle and good light.",
};
const HOLD_FRAMES = 2;
const GUIDE_INTERVAL_MS = 650;  // ≈90 checks a minute, inside the client-token rate limit
const NUDGE_AFTER_MS = 5000;
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
  baseline: Blob | null;
  stopped: StopReason | null;
  abort: AbortController;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)));

export default function LivenessStage({ credential, sessionId, onSessionChanged }: LivenessStageProps) {
  const camera = useCamera("user");
  const runRef = useRef<Run | null>(null);
  const [running, setRunning] = useState(false);
  const [checking, setChecking] = useState(false);
  const [step, setStep] = useState<{ text: string; kind: Kind | "checking"; index: number; total: number } | null>(null);
  const [coach, setCoach] = useState<{ text: string; progress: number | null; good: boolean }>({ text: "", progress: null, good: false });
  const [dots, setDots] = useState<{ steps: string[]; current: number; done: number } | null>(null);
  const [help, setHelp] = useState<string[] | null>(null);
  const [outcome, setOutcome] = useState<{ tone: "ok" | "retry"; title: string; detail: string; instructions: string[] } | null>(null);
  const lightHint = useLightHint(camera.videoRef, camera.state === "live" && !running);
  const { start, stop: stopCamera, grab } = camera;

  useEffect(() => {
    start();
    return () => {
      const run = runRef.current;
      if (run) {
        run.stopped = "CANCELLED";
        run.abort.abort();
      }
    };
  }, [start]);

  const showStep = useCallback((run: Run, index: number, kind: Kind) => {
    const steps = run.challenge.steps;
    const text = STEP_TEXT[steps[index].step] ?? { say: steps[index].instruction };
    setStep({
      text: kind === "center" ? "Look back at the center" : kind === "baseline" ? "Position your face in the circle" : text.say,
      kind, index, total: steps.length - 1,
    });
    setCoach({ text: kind === "move" ? "Move slowly to fill the circle." : "Hold still for a moment.", progress: 0, good: false });
  }, []);

  // Tips appear beside the camera while the check keeps running, so the step completes as soon as it is done.
  const showTips = useCallback((run: Run, index: number, kind: Kind) => {
    const stepName = run.challenge.steps[index].step;
    setHelp(kind === "move"
      ? [STEP_TEXT[stepName]?.more ?? "Move your head clearly.", "Move slowly and keep your whole face inside the circle.",
         "Keep the camera still at eye level and move only your head.", "Make sure your face is evenly lit."]
      : ["Hold the camera at eye level, about an arm's length away.", "Keep your whole face inside the circle.",
         "Find even light and remove anything covering your face."]);
  }, []);

  // Repeats until HOLD_FRAMES consecutive frames satisfy the step; returns those frames.
  const followStep = useCallback(async (run: Run, index: number, kind: Kind): Promise<Blob[]> => {
    const text = STEP_TEXT[run.challenge.steps[index].step] ?? { say: "" };
    showStep(run, index, kind);
    let held: Blob[] = [];
    const started = Date.now();
    let tipsShown = false;
    for (;;) {
      if (run.stopped) throw new LivenessStop(run.stopped);
      const tick = Date.now();
      const blob = await grab(640, 0.85);
      let satisfied = false;
      if (blob) {
        try {
          const body = await kycApi.livenessGuide(credential, sessionId, run.challenge, index, blob, run.baseline, run.abort.signal);
          if (run.stopped) throw new LivenessStop(run.stopped);
          if (body.face !== "OK") {
            held = [];
            setCoach({ text: FACE_TEXT[body.face] ?? FACE_TEXT.UNCLEAR, progress: 0, good: false });
          } else {
            satisfied = kind === "baseline" || body.state === (kind === "center" ? "CENTERED" : "DONE");
            const elapsed = Date.now() - started;
            const hint = satisfied ? (held.length + 1 >= HOLD_FRAMES ? "Done!" : "Hold it there…")
              : body.state === "WRONG_DIRECTION" ? text.wrong ?? "Other way."
              : kind === "center" ? "Look straight at the camera again."
              : elapsed > NUDGE_AFTER_MS ? text.more ?? "Keep going." : "Keep going, slowly.";
            setCoach({ text: hint, progress: kind === "move" ? (satisfied ? 1 : body.progress) : (satisfied ? 1 : 0), good: satisfied });
          }
        } catch (caught) {
          if (caught instanceof LivenessStop) throw caught;
          if (run.stopped) throw new LivenessStop(run.stopped);
          const failure = caught as ApiError;
          if (failure.status === 409 || failure.status === 404) throw new LivenessStop("CHALLENGE_CLOSED", failure);
          if (failure.status !== 0 && failure.status !== 429 && failure.status < 500) throw new LivenessStop("ERROR", failure);
          setCoach({ text: "The connection is slow. Keep still…", progress: null, good: false });
        }
      }
      if (satisfied && blob) {
        held.push(blob);
        if (held.length >= HOLD_FRAMES) {
          setHelp(null);
          return held;
        }
      } else {
        held = [];
        if (!tipsShown && Date.now() - started > HELP_AFTER_MS) {
          showTips(run, index, kind);
          tipsShown = true;
        }
      }
      await wait(GUIDE_INTERVAL_MS - (Date.now() - tick));
    }
  }, [credential, grab, sessionId, showStep, showTips]);

  const finish = () => {
    runRef.current = null;
    setRunning(false);
    setHelp(null);
    setStep(null);
    setDots(null);
    setCoach({ text: "", progress: null, good: false });
  };

  const show = (result: LivenessResult) => {
    const retry = result.retry_allowed && result.status === "LIVENESS_REQUIRED";
    setOutcome({
      tone: result.result === "PASS" ? "ok" : "retry",
      title: retry ? "Please try the movement check again" : result.result === "FAIL" ? "The movement check did not pass"
        : result.result === "PASS" ? "Movement check passed" : "Movement check recorded",
      detail: retry ? `${result.attempts_remaining} attempts left.` : "Your capture is complete. We are checking your details.",
      instructions: result.instructions ?? [],
    });
    if (!retry) {
      stopCamera();
      onSessionChanged(result.status);
    }
  };

  const run = async (): Promise<void> => {
    if (running || camera.state !== "live") return;
    setOutcome(null);
    setRunning(true);
    let challenge: LivenessChallenge;
    try {
      challenge = await kycApi.livenessChallenge(credential, sessionId);
    } catch (caught) {
      finish();
      const failure = caught as ApiError;
      if (failure.status === 409) return onSessionChanged("LIVENESS_REQUIRED");
      setOutcome({ tone: "retry", title: failure.status === 429 ? "No attempts left" : "Could not start the check",
                   detail: failure.status === 429 ? "Ask the service that sent you for a new link." : failure.message, instructions: [] });
      return;
    }
    const current: Run = { challenge, baseline: null, stopped: null, abort: new AbortController() };
    runRef.current = current;
    const stepNames = challenge.steps.map((item) => item.step);
    const frames: Blob[] = [];
    const indexes: number[] = [];
    try {
      setDots({ steps: stepNames, current: 0, done: 0 });
      const baseline = await followStep(current, 0, "baseline");
      current.baseline = baseline[0];
      baseline.forEach((blob) => { frames.push(blob); indexes.push(0); });
      for (let index = 1; index < challenge.steps.length; index++) {
        setDots({ steps: stepNames, current: index, done: index });
        const done = await followStep(current, index, "move");
        done.forEach((blob) => { frames.push(blob); indexes.push(index); });
        setDots({ steps: stepNames, current: index + 1, done: index + 1 });
        if (index < challenge.steps.length - 1) await followStep(current, 0, "center");
      }
    } catch (caught) {
      const stop = caught instanceof LivenessStop ? caught : new LivenessStop("ERROR");
      current.abort.abort();
      finish();
      if (stop.reason === "RESTART") return run();
      if (stop.reason === "CHALLENGE_CLOSED") {
        setOutcome({ tone: "retry", title: "Time ran out for this check",
                     detail: `Press "I'm ready" to start a new check. ${challenge.attempts_remaining} attempts left.`, instructions: [] });
      } else if (stop.reason === "ERROR") {
        setOutcome({ tone: "retry", title: "Something went wrong", detail: stop.failure?.message ?? "Try again.", instructions: [] });
      }
      return;
    }
    setChecking(true);
    setStep({ text: "Checking…", kind: "checking", index: 0, total: 0 });
    setCoach({ text: "All steps done. Checking…", progress: null, good: true });
    try {
      const result = await kycApi.submitLiveness(credential, sessionId, challenge, frames, indexes);
      finish();
      show(result);
    } catch (caught) {
      finish();
      const failure = caught as ApiError;
      setOutcome({ tone: "retry", title: "The check could not be completed", detail: failure.message, instructions: [] });
    } finally {
      setChecking(false);
    }
  };

  const stopRun = (reason: StopReason) => {
    const current = runRef.current;
    if (!current) return;
    current.stopped = reason;
    current.abort.abort();
    setHelp(null);
  };

  // Ring arcs: one per movement in this challenge, filled as each is completed.
  const arcs: Partial<Record<Direction, ArcState>> = {};
  dots?.steps.forEach((name, index) => {
    if (index === 0 || !(name in STEP_TEXT)) return;
    const active = index === dots.current && step?.kind === "move";
    arcs[name as Direction] = { status: index < dots.done ? "done" : active ? "active" : "todo",
                                progress: active ? coach.progress ?? 0 : 0 };
  });
  const cue = running && step?.kind === "move" && !coach.good ? dots?.steps[step.index] as Direction : null;
  const checkingNow = step?.kind === "checking";

  // One ring (and so one <video> holding the camera stream) stays mounted; only the panel around it changes.
  return (
    <div className="flex flex-col gap-4">
      <div className={running ? "flex flex-col gap-4 rounded-2xl bg-foreground-950 px-5 pb-7 pt-4"
                              : "flex flex-col items-center gap-5 px-2 py-4 text-center"}>
        {running && (
          <div className="flex items-center justify-between">
            <button type="button" onClick={() => stopRun("CANCELLED")} disabled={checking}
                    className="font-label text-[15px] text-primary-300 hover:text-primary-200 disabled:opacity-40">Cancel</button>
            {step && step.kind !== "checking" && step.total > 0 && (
              <span className="font-label text-xs text-background-50/60">
                {Math.min(dots?.done ?? 0, step.total)} of {step.total} done
              </span>
            )}
          </div>
        )}
        <FaceScanRing videoRef={camera.videoRef} cameraState={camera.state} arcs={running ? arcs : {}}
                      theme={running ? "dark" : "light"} showCamera={running} complete={checkingNow}
                      aligned={running && step?.kind !== "move" && coach.good} cue={cue}
                      center={!running ? <i className="ri-emotion-happy-line text-[7rem] leading-none text-foreground-300"></i>
                        : checkingNow ? (
                          <span className="flex h-20 w-20 items-center justify-center rounded-full bg-foreground-950/60">
                            <i className="ri-loader-4-line animate-spin text-4xl leading-none text-background-50"></i>
                          </span>
                        ) : undefined} />
        {running ? (
          <>
            <div className="min-h-[88px] text-center" aria-live="polite">
              <p className="font-heading text-xl font-semibold text-background-50">{step?.text}</p>
              <p className={`mt-1.5 font-label text-[15px] ${coach.good ? "text-primary-300" : "text-background-50/75"}`}>{coach.text}</p>
            </div>
            {help && (
              <div className="rounded-xl bg-background-50/10 px-4 py-3">
                <ul className="list-disc space-y-1 pl-5 font-label text-sm text-background-50/85">
                  {help.map((tip) => <li key={tip}>{tip}</li>)}
                </ul>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <p className="font-label text-xs text-background-50/60">We're still checking, so keep trying.</p>
                  <button type="button" onClick={() => stopRun("RESTART")}
                          className="whitespace-nowrap font-label text-sm text-primary-300 hover:text-primary-200">Start over</button>
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            <div className="max-w-sm">
              <h2 className="font-heading text-xl font-semibold text-foreground-950">Movement check</h2>
              <p className="mt-1.5 font-label text-[15px] leading-relaxed text-foreground-500">
                First, position your face in the circle. Then move your head slowly in each direction shown to
                complete the circle. No video is kept.
              </p>
            </div>
            {camera.state === "live" && lightHint && <p className="font-label text-sm text-accent-800">{lightHint}</p>}
            {camera.state === "starting" && (
              <p className="font-label text-sm text-foreground-500"><i className="ri-loader-4-line mr-1 animate-spin"></i>Starting camera…</p>
            )}
            {camera.state === "unavailable" ? (
              <div className="flex flex-col items-center gap-2">
                <p className="font-label text-sm text-foreground-600">This check needs a live camera. Allow camera access and try again.</p>
                <button type="button" onClick={camera.start}
                        className="inline-flex items-center gap-2 rounded-full border border-background-300 px-5 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">
                  <i className="ri-refresh-line text-base leading-none"></i>Retry camera
                </button>
              </div>
            ) : (
              <button type="button" onClick={run} disabled={camera.state !== "live"}
                      className="w-full max-w-sm rounded-full bg-primary-500 px-6 py-3.5 font-label text-base font-semibold text-background-50 transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50">
                {outcome ? "Try again" : "Get Started"}
              </button>
            )}
          </>
        )}
      </div>

      {outcome && (
        <div className={`rounded-lg border p-4 ${outcome.tone === "ok" ? "border-primary-200 bg-primary-50" : "border-accent-200 bg-accent-50"}`}>
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
