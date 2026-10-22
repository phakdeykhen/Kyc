import type { LivenessGuide } from "@/api/types";

// Feedback may be smoothed; evidence never is. Only consecutive fresh server-positive
// samples count, regardless of animation, elapsed time, or the advisory HOLDING state.
export const MAX_GUIDE_AGE_MS = 2000;
export const MAX_HOLD_GAP_MS = 2000;
export type FeedbackPhase = "active" | "hold" | "confirmed" | "correction";

export class LivenessFeedback {
  held = 0;
  holding = false;
  progress = 0;
  private lastFrameId = -1;
  private lastCapturedAt = -Infinity;
  private wrongFrames = 0;

  readonly required: number;
  readonly moving: boolean;

  constructor(required: number, moving: boolean) {
    this.required = required;
    this.moving = moving;
  }

  reset() {
    this.held = 0;
    this.holding = false;
    this.progress = 0;
    this.wrongFrames = 0;
  }

  update(body: LivenessGuide, frame: { frameId: number; capturedAt: number }, now: number, usable: boolean) {
    const fresh = usable && frame.frameId > this.lastFrameId && now >= frame.capturedAt
      && now - frame.capturedAt <= MAX_GUIDE_AGE_MS;
    const consecutive = frame.capturedAt - this.lastCapturedAt <= MAX_HOLD_GAP_MS;
    this.lastFrameId = Math.max(this.lastFrameId, frame.frameId);
    this.lastCapturedAt = frame.capturedAt;
    if (!fresh || body.face !== "OK") {
      this.reset();
      return { fresh, accepted: false, complete: false, phase: "correction" as FeedbackPhase, wrong: false };
    }
    if (!consecutive) this.reset();
    const accepted = body.state === (this.moving ? "DONE" : "CENTERED");
    this.held = accepted ? this.held + 1 : 0;
    this.holding = accepted || (this.holding && body.state === "HOLDING");
    this.wrongFrames = body.state === "WRONG_DIRECTION" ? this.wrongFrames + 1 : 0;
    const target = Math.min(1, Math.max(0, Number.isFinite(body.progress) ? body.progress : 0));
    this.progress = accepted ? 1 : this.progress + 0.45 * (target - this.progress);
    const complete = this.held >= this.required;
    const wrong = this.wrongFrames >= 2;
    const phase: FeedbackPhase = complete ? "confirmed" : this.holding ? "hold" : wrong ? "correction" : "active";
    return { fresh, accepted, complete, phase, wrong };
  }
}
