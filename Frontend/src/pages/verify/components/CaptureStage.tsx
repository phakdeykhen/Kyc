import { useEffect, useRef, useState } from "react";
import type { Client } from "@/api/client";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { CaptureResult, DocumentCaptureState, DocumentSide, DocumentTypeCode, SessionStatus } from "@/api/types";
import { QUALITY_LABELS, SIDE_LABELS, documentLabel } from "@/lib/catalog";
import { acceptedDocumentSides, documentInstructionMessages, documentReasonMessages, nextDocumentSide } from "@/lib/documentCapture";
import { useCamera, useLightHint } from "@/lib/useCamera";
import CameraView from "@/pages/verify/components/CameraView";
import QualityGrid from "@/pages/verify/components/QualityGrid";

interface CaptureStageProps {
  credential: Client;
  sessionId: string;
  documentType: DocumentTypeCode;
  sides: DocumentSide[];
  capture?: DocumentCaptureState | null;
  /** Called when the session moved on (all sides in, or a status change the page must follow). */
  onSessionChanged: (status: SessionStatus) => void;
  /** The server says consent is missing: go back to the consent step. */
  onConsentMissing: () => void;
  /** After every upload, so the page shows the session's current status. */
  onUploaded: () => void;
}

/** Document photos, one side at a time. The server's quality gate decides; nothing is judged here. */
export default function CaptureStage({ credential, sessionId, documentType, sides, capture, onSessionChanged, onConsentMissing, onUploaded }: CaptureStageProps) {
  const camera = useCamera("environment");
  const [accepted, setAccepted] = useState<Record<string, boolean>>(() => acceptedDocumentSides(capture));
  const [remaining, setRemaining] = useState<number | null>(capture?.attempts_remaining ?? null);
  const [last, setLast] = useState<CaptureResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);
  const current = nextDocumentSide(sides, accepted);
  const working = busy || capturing;
  const exhausted = remaining === 0 || capture?.retry_allowed === false;
  const lightHint = useLightHint(camera.videoRef, camera.state === "live" && !working);
  const { start } = camera;

  useEffect(() => {
    start();
  }, [start]);

  useEffect(() => {
    if (!capture) return;
    setAccepted(acceptedDocumentSides(capture));
    setRemaining(capture.attempts_remaining);
  }, [capture]);

  const submit = async (blob: Blob | null) => {
    if (!blob || !current || busy || exhausted) return;
    setBusy(true);
    setError("");
    try {
      const result = await kycApi.uploadDocument(credential, sessionId, current, blob);
      setLast(result);
      setRemaining(result.attempts_remaining);
      const nextAccepted = Object.fromEntries(Object.entries(result.sides).map(([side, value]) => [side, value === "ACCEPTED"]));
      setAccepted(nextAccepted);
      if (result.status !== "DOCUMENT_REQUIRED" && result.status !== "CREATED") {
        camera.stop();
        onSessionChanged(result.status);
      } else {
        onUploaded();
      }
    } catch (caught) {
      const failure = caught as ApiError;
      if (failure.reasonCode === "DOCUMENT_CONSENT_REQUIRED") return onConsentMissing();
      if (failure.status === 409) {
        camera.stop();
        return onSessionChanged("DOCUMENT_REQUIRED");  // the page re-reads the real status
      }
      const attemptsExceeded = failure.reasonCode === "CAPTURE_ATTEMPTS_EXCEEDED";
      if (attemptsExceeded) {
        setRemaining(0);
        onUploaded();
      }
      setError(attemptsExceeded ? "No photo attempts left. Ask the service that sent you for a new verification link."
        : failure.status === 429 ? "The service is busy with too many requests. Wait a moment, then try again."
        : failure.status === 413 ? "That photo is too large. Use the camera, or a smaller photo."
        : failure.message);
    } finally {
      setBusy(false);
    }
  };

  const takePhoto = async () => {
    if (working || exhausted || !current) return;
    setCapturing(true);
    setError("");
    try {
      const blob = await camera.grab();
      if (!blob) setError(camera.error ?? "The camera could not take a photo. Keep the camera open and try again, or upload a photo.");
      else await submit(blob);
    } catch {
      setError("The camera could not take a photo. Retry the camera or upload a photo.");
    } finally {
      setCapturing(false);
    }
  };

  const passport = sides.includes("DATA_PAGE");
  const hint = working ? "Checking photo quality…"
    : lightHint ?? (current ? `Fit the ${SIDE_LABELS[current].toLowerCase()} inside the frame, on a plain dark surface, then take the photo.` : null);
  const recapture = capture?.state === "RECAPTURE";
  const reasons = recapture ? documentReasonMessages(capture.reason_codes) : [];
  const instructions = recapture ? documentInstructionMessages(capture.instructions) : [];
  const photoReasons = last?.capture_status === "RECAPTURE" ? documentReasonMessages(last.reason_codes) : [];

  return (
    <div className="flex flex-col gap-4">
      {recapture && (
        <div role="alert" className="rounded-lg border border-accent-300 bg-accent-50 p-4" data-testid="document-feedback">
          <h3 className="flex items-center gap-2 font-label text-sm font-semibold text-accent-900">
            <i className="ri-error-warning-line text-lg" aria-hidden="true" />Please retake your document photos
          </h3>
          <p className="mt-2 font-label text-sm text-foreground-900">
            {reasons.length ? reasons.join(" ") : "We couldn't read these document photos clearly enough to continue."}
          </p>
          <p className="mt-2 font-label text-sm text-foreground-700">Photo quality passed, but the document still needs readable text and the correct sides.</p>
          {instructions.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 font-label text-sm text-foreground-900">
            {instructions.map((instruction) => <li key={instruction}>{instruction}</li>)}
          </ul>}
          {current && <p className="mt-2 font-label text-sm font-medium text-accent-900">Next: photograph the {SIDE_LABELS[current].toLowerCase()}.</p>}
        </div>
      )}
      {exhausted && <p role="alert" className="rounded-lg border border-accent-300 bg-accent-50 p-4 font-label text-sm text-accent-900">
        No photo attempts left. Ask the service that sent you for a new verification link.
      </p>}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-heading text-lg font-semibold text-foreground-950">
            {current ? `Photograph the ${SIDE_LABELS[current].toLowerCase()}` : "All photos received"}
          </h2>
          <p className="mt-0.5 font-label text-sm text-foreground-600">{documentLabel(documentType)}</p>
        </div>
        <ol className="flex flex-wrap gap-1.5">
          {sides.map((side) => (
            <li key={side} className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-label text-[11px] ${
              accepted[side] ? "border-primary-200 bg-primary-100 text-primary-900"
                : side === current ? "border-primary-300 bg-primary-50 text-primary-800" : "border-background-200 text-foreground-500"}`}>
              <i className={`${accepted[side] ? "ri-check-line" : "ri-id-card-line"} text-xs leading-none`}></i>
              {SIDE_LABELS[side]}
            </li>
          ))}
        </ol>
      </div>

      <CameraView videoRef={camera.videoRef} state={camera.state} shape={passport ? "passport" : "document"} hint={hint}
                  good={camera.state === "live" && !lightHint && !working}
                  overlay={working ? (
                    <div className="flex h-full items-center justify-center bg-foreground-950/50">
                      <i className="ri-loader-4-line animate-spin text-4xl leading-none text-primary-300"></i>
                    </div>
                  ) : undefined} />

      <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
        <button type="button" disabled={!current || working || exhausted || camera.state !== "live"} onClick={takePhoto}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-50">
          <i className="ri-camera-lens-line text-base leading-none"></i>
          Take photo
        </button>
        <button type="button" disabled={!current || working || exhausted} onClick={() => fileRef.current?.click()}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 px-5 py-2.5 font-label text-sm font-medium text-foreground-800 transition-colors hover:bg-background-100 disabled:cursor-not-allowed disabled:opacity-50">
          <i className="ri-upload-2-line text-base leading-none"></i>
          Upload a photo
        </button>
        {camera.state === "unavailable" && (
          <button type="button" onClick={camera.start}
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 px-5 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">
            <i className="ri-refresh-line text-base leading-none"></i>Retry camera
          </button>
        )}
        <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" disabled={working || exhausted}
               onChange={(event) => {
                 const [file] = Array.from(event.target.files ?? []);
                 event.target.value = "";
                 if (file) submit(file);
               }} />
      </div>

      {remaining !== null && <p className="text-center font-label text-xs text-foreground-600">{remaining} photo attempts left.</p>}

      {error && <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{error}</p>}
      {!error && camera.state === "unavailable" && camera.error && <p role="alert" className="font-label text-sm text-accent-800">{camera.error}</p>}

      {last && <p className="font-label text-sm text-foreground-800" role="status" data-testid="document-photo-feedback">
        {last.capture_status === "ACCEPTED" ? `${SIDE_LABELS[last.side]} photo quality passed. Your document details still need to be read and checked.`
          : photoReasons.length ? photoReasons.join(" ") : "This photo did not pass the quality check. Follow the tips below and try again."}
      </p>}

      {last && (
        <QualityGrid
          title={`${SIDE_LABELS[last.side]} · photo quality`}
          passed={last.capture_status === "ACCEPTED"}
          overall={last.quality.overall_quality}
          metrics={Object.entries(QUALITY_LABELS).map(([key, label]) => ({
            key, label, value: Number(last.quality[key as keyof typeof last.quality] ?? 0) }))}
          instructions={last.instructions}
          policyVersion={`${last.quality.policy_version} · ${last.attempts_remaining} attempts left`}
        />
      )}
    </div>
  );
}
