import type { ReactNode, RefObject } from "react";
import type { CameraState } from "@/lib/useCamera";

export type Direction = "LOOK_UP" | "TURN_RIGHT" | "LOOK_DOWN" | "TURN_LEFT";
export interface ArcState {
  status: "todo" | "active" | "done";
  progress: number;  // 0–1, for the active arc
}

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
  showCamera: boolean;
  /** Arrow pointing where to move. */
  cue?: Direction | null;
  theme: "light" | "dark";
  /** Drawn in the middle of the circle (intro icon, spinner, check mark). */
  center?: ReactNode;
}

// The selfie preview is mirrored, so the person's left is on the screen's left: each movement fills the
// arc on its own side, like a Face ID enrolment ring.
const CENTRES: Record<Direction, number> = { LOOK_UP: 0, TURN_RIGHT: 90, LOOK_DOWN: 180, TURN_LEFT: 270 };
const TICKS = 72;
const ARROWS: Record<Direction, { icon: string; place: string }> = {
  LOOK_UP: { icon: "ri-arrow-up-s-line", place: "left-1/2 top-[13%] -translate-x-1/2" },
  TURN_RIGHT: { icon: "ri-arrow-right-s-line", place: "right-[13%] top-1/2 -translate-y-1/2" },
  LOOK_DOWN: { icon: "ri-arrow-down-s-line", place: "bottom-[13%] left-1/2 -translate-x-1/2" },
  TURN_LEFT: { icon: "ri-arrow-left-s-line", place: "left-[13%] top-1/2 -translate-y-1/2" },
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
                                       progress, cue = null, theme, center }: FaceScanRingProps) {
  const dark = theme === "dark";
  const idle = dark ? (aligned ? "stroke-background-50/70" : "stroke-background-50/25") : "stroke-foreground-300";
  const ticks = Array.from({ length: TICKS }, (_, index) => {
    const angle = (index * 360) / TICKS;
    const { direction, offset } = nearest(angle);
    const arc = arcs[direction];
    let tone = idle;
    let long = false;
    if (complete || (progress !== undefined ? index < Math.floor(Math.min(1, Math.max(0, progress)) * TICKS) : arc?.status === "done")) {
      tone = "stroke-primary-400";
      long = true;
    } else if (arc?.status === "active") {
      // Fills outward from the middle of the arc as the head moves further.
      long = arc.progress > 0 && offset <= arc.progress * 45;
      tone = long ? "stroke-primary-400" : "stroke-background-50";
    }
    return (
      <line key={index} x1="100" y1="16" x2="100" y2={long ? 4 : 9} transform={`rotate(${angle} 100 100)`}
            strokeWidth="2.4" strokeLinecap="round" className={`transition-all duration-300 motion-reduce:transition-none ${tone}`} />
    );
  });

  return (
    <div className="relative mx-auto aspect-square w-full max-w-[340px] shrink-0">
      <svg viewBox="0 0 200 200" className="absolute inset-0 h-full w-full" aria-hidden="true">{ticks}</svg>
      <div className={`absolute left-[11%] top-[11%] h-[78%] w-[78%] overflow-hidden rounded-full ${dark ? "bg-foreground-900" : "bg-background-100"}`}>
        <video ref={videoRef} autoPlay playsInline muted
               className={`h-full w-full -scale-x-100 object-cover transition-opacity duration-500 ${
                 showCamera && cameraState === "live" ? "opacity-100" : "opacity-0"}`} />
        {center && <div className="absolute inset-0 flex items-center justify-center">{center}</div>}
      </div>
      {cue && (
        <i className={`${ARROWS[cue].icon} pointer-events-none absolute animate-pulse text-5xl leading-none text-background-50 drop-shadow-lg ${ARROWS[cue].place}`}></i>
      )}
    </div>
  );
}
