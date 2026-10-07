import { useCallback, useEffect, useRef, useState } from "react";
import type { Client } from "@/api/client";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { LivenessChallenge, LivenessResult, SessionStatus } from "@/api/types";
import { instructionText } from "@/lib/catalog";
import { useCamera, useLightHint } from "@/lib/useCamera";
import CameraView from "@/pages/verify/components/CameraView";

interface LivenessStageProps {
  credential: Client;
  sessionId: string;
  onSessionChanged: (status: SessionStatus) => void;
}

// Guided active liveness. The server picks a random sequence of head movements. For each step the page
// asks the server, a frame at a time, whether the movement is done (same geometry as the final check),
// and moves on once HOLD_FRAMES frames in a row show it. Raw frames are then submitted with their step
// index; the server judges them again, in memory, and keeps none.

const STEP_TEXT: Record<string, { say: string; cue: string; more?: string; wrong?: string }> = {
  LOOK_STRAIGHT: { say: "Look straight at the camera", cue: "" },
  TURN_LEFT: { say: "Turn your head to your left", cue: "ri-arrow-left-line",
    more: "Turn further, as if looking over your left shoulder.", wrong: "That's the other way. Turn to your left." },
  TURN_RIGHT: { say: "Turn your head to your right", cue: "ri-arrow-right-line",
    more: "Turn further, as if looking over your right shoulder.", wrong: "That's the other way. Turn to your right." },
  LOOK_UP: { say: "Tilt your head up", cue: "ri-arrow-up-line",
    more: "Lift your chin higher, as if looking at the ceiling.", wrong: "That's down. Tilt your head up instead." },
  LOOK_DOWN: { say: "Tilt your head down", cue: "ri-arrow-down-line",
    more: "Lower your chin further, as if looking at the floor.", wrong: "That's up. Tilt your head down instead." },
};
const FACE_TEXT: Record<string, string> = {
  NO_FACE: "We can't see your face. Keep it inside the oval.",
  MULTIPLE_FACES: "Only your face should be in view.",
  UNCLEAR: "Hold still with your whole face in the oval and good light.",
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
  const [step, setStep] = useState<{ text: string; cue: string; coach: string; index: number; total: number } | null>(null);
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
    const text = STEP_TEXT[steps[index].step] ?? { say: steps[index].instruction, cue: "" };
    setStep({
      text: kind === "center" ? "Turn your head back to the center" : text.say,
      cue: kind === "move" ? text.cue : "",
      coach: kind === "move" ? `Step ${index} of ${steps.length - 1} · keep going until the bar is full` : kind === "baseline" ? "Hold still for a moment." : "",
      index, total: steps.length - 1,
    });
    setCoach({ text: kind === "move" ? "Move slowly." : "Keep your face inside the oval.", progress: 0, good: false });
  }, []);

  // Tips appear beside the camera while the check keeps running, so the step completes as soon as it is done.
  const showTips = useCallback((run: Run, index: number, kind: Kind) => {
    const stepName = run.challenge.steps[index].step;
    setHelp(kind === "move"
      ? [STEP_TEXT[stepName]?.more ?? "Move your head clearly.", "Move slowly and keep your whole face inside the oval.",
         "Keep the camera still at eye level and move only your head.", "Make sure your face is evenly lit."]
      : ["Hold the camera at eye level, about an arm's length away.", "Keep your whole face inside the oval.",
         "Find even light and remove anything covering your face."]);
  }, []);

  // Repeats until HOLD_FRAMES consecutive frames satisfy the step; returns those frames.
  const followStep = useCallback(async (run: Run, index: number, kind: Kind): Promise<Blob[]> => {
    const text = STEP_TEXT[run.challenge.steps[index].step] ?? { say: "", cue: "" };
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
    const stepNames = challenge.steps.map((item) => STEP_TEXT[item.step]?.say ?? item.instruction);
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
    setStep({ text: "Checking…", cue: "", coach: "", index: 0, total: 0 });
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

  const overlay = running && step ? (
    <div className="flex h-full flex-col items-center justify-between p-4">
      <div className="rounded-lg bg-foreground-950/70 px-4 py-2 text-center">
        <p className="font-heading text-lg font-semibold text-background-50">{step.text}</p>
        {step.coach && <p className="mt-0.5 font-label text-xs text-background-50/70">{step.coach}</p>}
      </div>
      {step.cue && <i className={`${step.cue} animate-pulse text-6xl leading-none text-primary-300 drop-shadow`}></i>}
      <div className="w-full max-w-[320px] rounded-lg bg-foreground-950/70 px-4 py-2">
        <p className={`text-center font-label text-sm ${coach.good ? "text-primary-200" : "text-background-50"}`}>{coach.text}</p>
        {coach.progress !== null && (
          <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-background-50/20">
            <div className="h-full rounded-full bg-primary-400 transition-all" style={{ width: `${Math.round(coach.progress * 100)}%` }}></div>
          </div>
        )}
      </div>
    </div>
  ) : undefined;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-heading text-lg font-semibold text-foreground-950">Movement check</h2>
        <p className="mt-0.5 font-label text-sm text-foreground-600">
          Shows that you are present in person. Follow each instruction: move your head slowly, then hold still.
          The order is random every time, and no video is kept.
        </p>
      </div>

      {dots && (
        <ol className="flex flex-wrap gap-1.5">
          {dots.steps.map((label, index) => (
            <li key={`${index}-${label}`} className={`rounded-full border px-2.5 py-1 font-label text-[11px] ${
              index < dots.done ? "border-primary-200 bg-primary-100 text-primary-900"
                : index === dots.current ? "border-primary-300 bg-primary-50 text-primary-800" : "border-background-200 text-foreground-500"}`}>
              {index < dots.done && <i className="ri-check-line mr-1 text-xs leading-none"></i>}{label}
            </li>
          ))}
        </ol>
      )}

      <CameraView videoRef={camera.videoRef} state={camera.state} shape="face" overlay={overlay}
                  good={running ? coach.good : camera.state === "live" && !lightHint}
                  hint={running ? null : lightHint ?? "Light and focus OK. Press I'm ready, then follow each instruction."} />

      {help && (
        <div className="rounded-lg border border-accent-200 bg-accent-50 p-4">
          <p className="font-label text-sm font-medium text-accent-900">Having trouble? Try this:</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 font-label text-sm text-accent-900">
            {help.map((tip) => <li key={tip}>{tip}</li>)}
          </ul>
          <p className="mt-2 font-label text-xs text-accent-800">We're still checking, so keep trying.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => stopRun("RESTART")}
                    className="rounded-md border border-background-300 bg-background-50 px-4 py-2 font-label text-sm text-foreground-800 hover:bg-background-100">Start over</button>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
        {!running ? (
          <button type="button" onClick={run} disabled={camera.state !== "live"}
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50">
            <i className="ri-play-circle-line text-base leading-none"></i>
            I'm ready
          </button>
        ) : (
          <button type="button" onClick={() => stopRun("CANCELLED")} disabled={checking}
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 px-5 py-2.5 font-label text-sm font-medium text-foreground-800 transition-colors hover:bg-background-100 disabled:opacity-50">
            <i className="ri-stop-circle-line text-base leading-none"></i>
            Cancel
          </button>
        )}
        {camera.state === "unavailable" && (
          <button type="button" onClick={camera.start}
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 px-5 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">
            <i className="ri-refresh-line text-base leading-none"></i>Retry camera
          </button>
        )}
      </div>
      {camera.state === "unavailable" && (
        <p className="text-center font-label text-xs text-foreground-500">This check needs a live camera; photos cannot be uploaded for it.</p>
      )}

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
