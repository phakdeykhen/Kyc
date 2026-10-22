import { useMemo } from "react";
import type { ReactNode, RefObject } from "react";
import type { CameraState } from "@/lib/useCamera";
import "./FaceScanRing.css";

export type Direction = "LOOK_UP" | "TURN_RIGHT" | "LOOK_DOWN" | "TURN_LEFT";
export interface ArcState {
  status: "todo" | "active" | "done";
  progress: number;  // 0–1, for the active arc
}
export type ScanFeedback = "active" | "hold" | "confirmed" | "correction";

interface FaceScanRingProps {
  videoRef: RefObject<HTMLVideoElement | null>;
  cameraState: CameraState;
  /** Movements in this challenge; a direction without an entry stays neutral. */
  arcs: Partial<Record<Direction, ArcState>>;
  /** Every movement done: the whole ring turns green. */
  complete?: boolean;
  /** Face held in the circle during the baseline step. */
  aligned?: boolean;
  /** Overall progress from confirmed steps and the current server movement measurement. */
  progress?: number;
  /** Fraction of challenge movements actually confirmed; the remaining active progress stays blue. */
  confirmedProgress?: number;
  showCamera: boolean;
  /** Arrow pointing where to move. */
  cue?: Direction | null;
  /** Mirrors recognition state; visual completion never approves verification. */
  feedback?: ScanFeedback;
  theme: "light" | "dark";
  /** Drawn in the middle of the circle (intro icon, spinner, check mark). */
  center?: ReactNode;
}

// Mirror only the camera image. A person's right appears on their right in a selfie preview, so the
// instructions and arrow directions keep their anatomical meaning and are never CSS-mirrored.
const CENTRES: Record<Direction, number> = { LOOK_UP: 0, TURN_RIGHT: 90, LOOK_DOWN: 180, TURN_LEFT: 270 };
const TICKS = 72;
const ARROWS: Record<Direction, string> = {
  LOOK_UP: "M12 20V4m-7 7 7-7 7 7",
  TURN_RIGHT: "M4 12h16m-7-7 7 7-7 7",
  LOOK_DOWN: "M12 4v16m-7-7 7 7 7-7",
  TURN_LEFT: "M20 12H4m7-7-7 7 7 7",
};

function nearest(angle: number): { direction: Direction; offset: number } {
  let best: { direction: Direction; offset: number } = { direction: "LOOK_UP", offset: 360 };
  for (const [direction, centre] of Object.entries(CENTRES) as [Direction, number][]) {
    const raw = Math.abs(angle - centre) % 360;
    const offset = Math.min(raw, 360 - raw);
    if (offset < best.offset) best = { direction, offset };
  }
  return best;
}

export default function FaceScanRing({ videoRef, cameraState, arcs, complete = false, aligned = false, showCamera,
                                       progress, confirmedProgress, cue = null, feedback = "active", theme, center }: FaceScanRingProps) {
  const dark = theme === "dark";
  const idle = dark ? (aligned ? "stroke-white/70" : "stroke-white/35") : "stroke-foreground-300";
  const activeTone = dark ? "stroke-[#60A5FA]" : "stroke-[#1D4ED8]";
  const confirmedTone = dark ? "stroke-[#4ADE80]" : "stroke-[#15803D]";
  const up = arcs.LOOK_UP;
  const down = arcs.LOOK_DOWN;
  const left = arcs.TURN_LEFT;
  const right = arcs.TURN_RIGHT;
  const ticks = useMemo(() => Array.from({ length: TICKS }, (_, index) => {
    const currentArcs = { LOOK_UP: up, LOOK_DOWN: down, TURN_LEFT: left, TURN_RIGHT: right };
    const angle = (index * 360) / TICKS;
    const { direction, offset } = nearest(angle);
    const arc = currentArcs[direction];
    let tone = idle;
    let long = false;
    if (complete || (confirmedProgress !== undefined
      ? index < Math.floor(Math.min(1, Math.max(0, confirmedProgress)) * TICKS)
      : progress === undefined && arc?.status === "done")) {
      tone = confirmedTone;
      long = true;
    } else if (progress !== undefined && index < Math.floor(Math.min(1, Math.max(0, progress)) * TICKS)) {
      tone = activeTone;
      long = true;
    } else if (arc?.status === "active") {
      // Fills outward from the middle of the arc as the head moves further.
      long = arc.progress > 0 && offset <= arc.progress * 45;
      tone = long ? activeTone : idle;
    }
    return (
      <line key={index} x1="100" y1="16" x2="100" y2={long ? 4 : 9} transform={`rotate(${angle} 100 100)`}
            strokeWidth="2.4" strokeLinecap="round" className={`transition-all duration-200 motion-reduce:transition-none ${tone}`} />
    );
  }), [activeTone, complete, confirmedProgress, confirmedTone, down, idle, left, progress, right, up]);
  const confirmed = complete || feedback === "confirmed";
  const showBadge = !!cue || confirmed || feedback === "correction";

  return (
    <div className="face-scan-ring">
      <div className="relative aspect-square min-w-0">
        <svg viewBox="0 0 200 200" className="absolute inset-0 h-full w-full" aria-hidden="true">{ticks}</svg>
        <div className={`absolute left-[11%] top-[11%] h-[78%] w-[78%] overflow-hidden rounded-full ${dark ? "bg-foreground-900" : "bg-background-100"}`}>
          <video ref={videoRef} autoPlay playsInline muted
                 className={`h-full w-full -scale-x-100 object-cover transition-opacity duration-200 motion-reduce:transition-none ${
                   showCamera && cameraState === "live" ? "opacity-100" : "opacity-0"}`} />
          {center && <div className="absolute inset-0 flex items-center justify-center">{center}</div>}
        </div>
      </div>
      {/* The reserved gutter stays in place even between steps. The badge never covers camera pixels. */}
      <div className="face-scan-cue-slot" aria-hidden="true">
        {showBadge && (
          <div className="face-scan-cue" data-direction={cue ?? undefined} data-feedback={confirmed ? "confirmed" : feedback}>
            <svg className="face-scan-cue-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.75" strokeLinecap="round" strokeLinejoin="round">
              <path d={confirmed ? "m5 12 4 4L19 6" : cue ? ARROWS[cue] : "M12 8v5m0 3h.01M12 3 2 21h20L12 3Z"} />
            </svg>
            {feedback === "correction" && cue && !confirmed && (
              <svg className="face-scan-cue-warning" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2 1 22h22L12 2Z" />
                <path d="M12 8v6m0 3h.01" fill="none" stroke="#111827" strokeWidth="2.5" strokeLinecap="round" />
              </svg>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
