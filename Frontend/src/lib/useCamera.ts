import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

export type CameraState = "idle" | "starting" | "live" | "unavailable";

export interface CameraFrame {
  blob: Blob;
  /** Camera frame presentation timestamp, in the same monotonic time base as performance.now(). */
  capturedAt: number;
  /** Changes only when the browser presents a new camera frame. */
  frameId: number;
  mediaTime: number;
}

const cameraError = (caught: unknown): string => {
  const name = caught instanceof Error ? caught.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "Allow camera access in your browser, then try again.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No usable camera was found. Connect a camera and try again.";
  if (name === "NotReadableError" || name === "AbortError") return "The camera is busy or was interrupted. Close other camera apps and try again.";
  return "The camera could not provide a picture. Restart the camera and try again.";
};

/**
 * One camera stream for a capture step. Frames are grabbed raw (unmirrored) because the server's
 * liveness geometry depends on the real left and right; only the preview is mirrored for selfies.
 */
export function useCamera(facing: "user" | "environment") {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const generationRef = useRef(0);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const capturePendingRef = useRef(false);
  const callbackRef = useRef<number | null>(null);
  const nextFrameRef = useRef(0);
  const presentedRef = useRef<{ frameId: number; mediaTime: number; receivedAt: number } | null>(null);
  const lastFreshRef = useRef<number | null>(null);
  const [state, setState] = useState<CameraState>("idle");
  const [error, setError] = useState<string | null>(null);

  const stop = useCallback(() => {
    generationRef.current += 1;
    const video = videoRef.current;
    if (callbackRef.current !== null) video?.cancelVideoFrameCallback?.(callbackRef.current);
    callbackRef.current = null;
    presentedRef.current = null;
    lastFreshRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (video) video.srcObject = null;
    setState("idle");
  }, []);

  const start = useCallback(async () => {
    stop();
    setError(null);
    const generation = generationRef.current;
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Camera access is unavailable. Open this page in a supported browser over a secure connection.");
      setState("unavailable");
      return;
    }
    setState("starting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false, video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1440 } },
      });
      // A permission request can finish after unmount or a newer request (including Strict Mode).
      if (generation !== generationRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      stream.getVideoTracks().forEach((track) => track.addEventListener("ended", () => {
        if (generation !== generationRef.current) return;
        stop();
        setError("The camera stopped. Restart the camera and try again.");
        setState("unavailable");
      }, { once: true }));
      stream.getVideoTracks().forEach((track) => {
        track.addEventListener("mute", () => {
          if (generation === generationRef.current) setError("The camera was interrupted. Keep this page open and restore camera access.");
        });
        track.addEventListener("unmute", () => {
          if (generation === generationRef.current) setError(null);
        });
      });
      const video = videoRef.current;
      if (!video) throw new Error("Camera preview is unavailable");
      video.srcObject = stream;
      await video.play();
      // Safari can resolve play() before dimensions are available. A live state must mean usable pixels.
      const deadline = Date.now() + 10000;
      while (!video.videoWidth || !video.videoHeight || video.readyState < 2) {
        if (generation !== generationRef.current) return;
        if (Date.now() > deadline) throw new Error("Camera did not provide frames");
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      if (generation !== generationRef.current) return;
      // Decode bookkeeping stays outside React, so a 30/60 fps camera does not re-render the UI.
      if (video.requestVideoFrameCallback) {
        const presented = (_now: number, metadata: { mediaTime: number }) => {
          if (generation !== generationRef.current) return;
          presentedRef.current = { frameId: ++nextFrameRef.current, mediaTime: metadata.mediaTime, receivedAt: performance.now() };
          callbackRef.current = video.requestVideoFrameCallback(presented);
        };
        callbackRef.current = video.requestVideoFrameCallback(presented);
      }
      setState("live");
    } catch (caught) {
      if (generation !== generationRef.current) return;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      if (videoRef.current) videoRef.current.srcObject = null;
      setError(cameraError(caught));
      setState("unavailable");
    }
  }, [facing, stop]);

  useEffect(() => () => stop(), [stop]);

  const capture = useCallback(async (maxWidth?: number, quality = 0.92, freshOnly = false): Promise<CameraFrame | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight || video.paused || video.readyState < 2
        || capturePendingRef.current
        || !streamRef.current?.getVideoTracks().some((track) => track.readyState === "live" && track.enabled && !track.muted)) return null;
    let presented = presentedRef.current;
    // Older browsers expose media time instead of frame callbacks. Reading it is cheap and does not
    // start a rendering loop or upload identical paused frames.
    if (!video.requestVideoFrameCallback && (!presented || video.currentTime !== presented.mediaTime)) {
      presented = { frameId: ++nextFrameRef.current, mediaTime: video.currentTime, receivedAt: performance.now() };
      presentedRef.current = presented;
    }
    if (freshOnly && (!presented || presented.frameId === lastFreshRef.current || performance.now() - presented.receivedAt > 1500)) return null;
    const generation = generationRef.current;
    const scale = maxWidth ? Math.min(1, maxWidth / video.videoWidth) : 1;
    const canvas = canvasRef.current ?? (canvasRef.current = document.createElement("canvas"));
    const width = Math.round(video.videoWidth * scale), height = Math.round(video.videoHeight * scale);
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return null;
    capturePendingRef.current = true;
    try {
      const capturedAt = presented?.receivedAt ?? performance.now();
      context.drawImage(video, 0, 0, width, height);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
      if (!blob || generation !== generationRef.current) return null;
      if (freshOnly && presented) lastFreshRef.current = presented.frameId;
      return { blob, capturedAt, frameId: presented?.frameId ?? ++nextFrameRef.current, mediaTime: presented?.mediaTime ?? video.currentTime };
    } finally {
      capturePendingRef.current = false;
    }
  }, []);

  /** A JPEG of the current frame, scaled down to maxWidth when given. */
  const grab = useCallback(async (maxWidth?: number, quality = 0.92): Promise<Blob | null> =>
    (await capture(maxWidth, quality))?.blob ?? null, [capture]);
  /** One newly presented frame. A paused, stale, interrupted or overlapping capture yields null. */
  const grabFresh = useCallback((maxWidth?: number, quality = 0.92) => capture(maxWidth, quality, true), [capture]);

  return { videoRef, state, error, start, stop, grab, grabFresh };
}

/** Cheap on-device pre-check of exposure and focus in the middle of the frame (advisory only). */
export function useLightHint(videoRef: RefObject<HTMLVideoElement | null>, active: boolean): string | null {
  const [hint, setHint] = useState<string | null>(null);
  useEffect(() => {
    if (!active) {
      setHint(null);
      return;
    }
    const probe = document.createElement("canvas");
    const timer = window.setInterval(() => {
      const video = videoRef.current;
      if (!video || !video.videoWidth) return;
      const width = 200, height = Math.round(200 * (video.videoHeight / video.videoWidth || 0.75));
      probe.width = width;
      probe.height = height;
      const context = probe.getContext("2d", { willReadFrequently: true });
      if (!context) return;
      context.drawImage(video, 0, 0, width, height);
      const x0 = Math.round(width * 0.15), y0 = Math.round(height * 0.2);
      const data = context.getImageData(x0, y0, width - 2 * x0, height - 2 * y0);
      const w = data.width, h = data.height, lum = new Float32Array(w * h);
      let sum = 0;
      for (let i = 0; i < w * h; i++) {
        const v = 0.299 * data.data[i * 4] + 0.587 * data.data[i * 4 + 1] + 0.114 * data.data[i * 4 + 2];
        lum[i] = v;
        sum += v;
      }
      let lapSum = 0, lapSq = 0, n = 0;
      for (let y = 1; y < h - 1; y++) {
        for (let x = 1; x < w - 1; x++) {
          const i = y * w + x;
          const l = lum[i - 1] + lum[i + 1] + lum[i - w] + lum[i + w] - 4 * lum[i];
          lapSum += l;
          lapSq += l * l;
          n++;
        }
      }
      const mean = sum / (w * h);
      const sharpness = lapSq / n - (lapSum / n) ** 2;
      setHint(mean < 60 ? "Find more light." : mean > 225 ? "Too bright. Move out of direct light."
        : sharpness < 40 ? "Hold still, the picture is blurry." : null);
    }, 300);
    return () => window.clearInterval(timer);
  }, [videoRef, active]);
  return hint;
}
