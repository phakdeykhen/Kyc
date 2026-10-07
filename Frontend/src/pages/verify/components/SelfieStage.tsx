import { useEffect, useRef, useState } from "react";
import type { Client } from "@/api/client";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { SelfieResult, SessionStatus } from "@/api/types";
import { useCamera, useLightHint } from "@/lib/useCamera";
import CameraView from "@/pages/verify/components/CameraView";
import QualityGrid from "@/pages/verify/components/QualityGrid";

interface SelfieStageProps {
  credential: Client;
  sessionId: string;
  onSessionChanged: (status: SessionStatus) => void;
}

/** Live selfie, compared only with this session's document portrait. Needs explicit biometric consent. */
export default function SelfieStage({ credential, sessionId, onSessionChanged }: SelfieStageProps) {
  const camera = useCamera("user");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [last, setLast] = useState<SelfieResult | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const lightHint = useLightHint(camera.videoRef, camera.state === "live" && !busy);
  const { start } = camera;

  useEffect(() => {
    start();
  }, [start]);

  const submit = async (blob: Blob | null) => {
    if (!blob || busy || !consent) return;
    setBusy(true);
    setError("");
    try {
      const result = await kycApi.uploadSelfie(credential, sessionId, blob);
      setLast(result);
      if (result.status !== "SELFIE_REQUIRED") {
        camera.stop();
        onSessionChanged(result.status);
      }
    } catch (caught) {
      const failure = caught as ApiError;
      if (failure.status === 409) {
        camera.stop();
        return onSessionChanged("SELFIE_REQUIRED");
      }
      setError(failure.status === 429 ? "Too many selfie attempts for this session. Ask the service that sent you for a new link."
        : failure.status === 503 ? "Face checking is unavailable right now. Try again in a moment."
        : failure.message);
    } finally {
      setBusy(false);
    }
  };

  const quality = last?.quality ?? {};
  const numeric = Object.entries(quality).filter(([, value]) => typeof value === "number" && value >= 0 && value <= 1) as [string, number][];

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-heading text-lg font-semibold text-foreground-950">Take a selfie</h2>
        <p className="mt-0.5 font-label text-sm text-foreground-600">Look straight at the camera with your whole face inside the oval, in even light.</p>
      </div>

      <label className="flex cursor-pointer items-start gap-3 rounded-md border border-background-200 bg-background-100/60 p-3.5">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)}
               className="mt-0.5 h-4 w-4 shrink-0 accent-[oklch(var(--primary-500))]" />
        <span className="font-label text-xs leading-relaxed text-foreground-700">
          I agree that my selfie is compared with the photo on my document to confirm I am its owner. The face template is
          stored encrypted, deleted after the retention period, and never shared through the API.
        </span>
      </label>

      <CameraView videoRef={camera.videoRef} state={camera.state} shape="face"
                  hint={busy ? "Checking your selfie…" : lightHint ?? "Center your face in the oval and look at the camera."}
                  good={camera.state === "live" && !lightHint && !busy}
                  overlay={busy ? (
                    <div className="flex h-full items-center justify-center bg-foreground-950/50">
                      <i className="ri-loader-4-line animate-spin text-4xl leading-none text-primary-300"></i>
                    </div>
                  ) : undefined} />

      <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
        <button type="button" disabled={!consent || busy || camera.state !== "live"} onClick={async () => submit(await camera.grab())}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50">
          <i className="ri-camera-lens-line text-base leading-none"></i>
          Take selfie
        </button>
        <button type="button" disabled={!consent || busy} onClick={() => fileRef.current?.click()}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 px-5 py-2.5 font-label text-sm font-medium text-foreground-800 transition-colors hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-50">
          <i className="ri-upload-2-line text-base leading-none"></i>
          Upload a selfie
        </button>
        <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden"
               onChange={(event) => {
                 const [file] = Array.from(event.target.files ?? []);
                 event.target.value = "";
                 if (file) submit(file);
               }} />
      </div>
      {!consent && <p className="text-center font-label text-xs text-foreground-500">Tick the consent box to continue.</p>}

      {error && <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{error}</p>}

      {last && (
        <QualityGrid
          title="Selfie quality"
          passed={last.capture_status === "ACCEPTED"}
          metrics={numeric.map(([key, value]) => ({ key, label: key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()), value }))}
          instructions={last.instructions}
          policyVersion={`${last.attempts_remaining} attempts left`}
        />
      )}
    </div>
  );
}
