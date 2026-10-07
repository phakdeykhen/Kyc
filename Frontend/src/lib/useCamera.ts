import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

export type CameraState = "idle" | "starting" | "live" | "unavailable";

/**
 * One camera stream for a capture step. Frames are grabbed raw (unmirrored) because the server's
 * liveness geometry depends on the real left and right; only the preview is mirrored for selfies.
 */
export function useCamera(facing: "user" | "environment") {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [state, setState] = useState<CameraState>("idle");

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setState("idle");
  }, []);

  const start = useCallback(async () => {
    stop();
    if (!navigator.mediaDevices?.getUserMedia) {
      setState("unavailable");
      return;
    }
    setState("starting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false, video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1440 } },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => undefined);
      }
      setState("live");
    } catch {
      setState("unavailable");
    }
  }, [facing, stop]);

  useEffect(() => () => stop(), [stop]);

  /** A JPEG of the current frame, scaled down to maxWidth when given. */
  const grab = useCallback((maxWidth?: number, quality = 0.92): Promise<Blob | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight) return Promise.resolve(null);
    const scale = maxWidth ? Math.min(1, maxWidth / video.videoWidth) : 1;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext("2d")?.drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
  }, []);

  return { videoRef, state, start, stop, grab };
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
