import type { ReactNode, RefObject } from "react";
import type { CameraState } from "@/lib/useCamera";

interface CameraViewProps {
  videoRef: RefObject<HTMLVideoElement | null>;
  state: CameraState;
  shape: "document" | "passport" | "face";
  /** Status line under the preview. */
  hint?: string | null;
  good?: boolean;
  /** Drawn over the preview (busy spinners, liveness cues). */
  overlay?: ReactNode;
  children?: ReactNode;
}

/** Camera preview with a document frame or face oval. Selfie previews are mirrored; captured frames are not. */
export default function CameraView({ videoRef, state, shape, hint, good = false, overlay, children }: CameraViewProps) {
  const face = shape === "face";
  return (
    <div className="relative overflow-hidden rounded-xl border border-background-300 bg-foreground-950">
      <div className="relative flex min-h-[320px] items-center justify-center md:min-h-[420px]">
        <video ref={videoRef} playsInline muted
               className={`absolute inset-0 h-full w-full object-cover ${face ? "-scale-x-100" : ""} ${state === "live" ? "opacity-100" : "opacity-0"}`} />
        {state !== "live" && (
          <div className="relative z-10 flex flex-col items-center gap-2 px-6 text-center font-label text-sm text-background-50/80">
            <i className={`${state === "starting" ? "ri-loader-4-line animate-spin" : "ri-camera-off-line"} text-3xl leading-none`}></i>
            {state === "starting" ? "Starting camera…" : state === "unavailable"
              ? "Camera unavailable here. Allow camera access, or upload a photo instead." : "Camera off"}
          </div>
        )}
        {state === "live" && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
            {face ? (
              <div className={`h-[78%] max-h-[340px] aspect-[3/4] rounded-[50%] border-2 ${good ? "border-primary-400" : "border-background-50/70"}`}
                   style={{ boxShadow: "0 0 0 9999px rgb(0 0 0 / 0.45)" }} />
            ) : (
              <div className={`w-[86%] max-w-[560px] rounded-lg border-2 ${good ? "border-primary-400" : "border-background-50/70"}`}
                   style={{ aspectRatio: shape === "passport" ? "1.42" : "1.586", boxShadow: "0 0 0 9999px rgb(0 0 0 / 0.45)" }} />
            )}
          </div>
        )}
        {overlay && <div className="absolute inset-0 z-20">{overlay}</div>}
      </div>
      {hint && (
        <div className={`border-t border-background-50/10 px-4 py-2.5 text-center font-label text-xs ${good ? "text-primary-200" : "text-background-50/80"}`}>
          {hint}
        </div>
      )}
      {children}
    </div>
  );
}
