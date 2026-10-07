import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApplicantMessage, ApplicantShell } from "@/components/feature/ApplicantShell";
import StatusBadge from "@/components/base/StatusBadge";
import Stepper, { type FlowStep } from "@/pages/verify/components/Stepper";
import ConsentStage from "@/pages/verify/components/ConsentStage";
import CaptureStage from "@/pages/verify/components/CaptureStage";
import SelfieStage from "@/pages/verify/components/SelfieStage";
import LivenessStage from "@/pages/verify/components/LivenessStage";
import NfcStage from "@/pages/verify/components/NfcStage";
import ProcessingStage from "@/pages/verify/components/ProcessingStage";
import ApplicantGovernmentVerification from "@/pages/verify/components/ApplicantGovernmentVerification";
import { kycApi, type Client } from "@/api/client";
import { ApiError } from "@/api/http";
import type { KycSession, SessionStatus, VerificationLevel } from "@/api/types";
import { statusMeta } from "@/lib/badges";
import { APPLICANT_FINISHED, SIDE_LABELS, documentLabel, requiredSides } from "@/lib/catalog";
import { readApplicantCredential } from "@/pages/verify/applicantLink";

const POLL_MS = 2000;
const consentKey = (id: string) => `kyc-consent:${id}`;

function stepsFor(level: VerificationLevel, sides: string[]): (FlowStep & { statuses: SessionStatus[] })[] {
  const steps: (FlowStep & { statuses: SessionStatus[] })[] = [
    { key: "document", label: `Document (${sides.map((side) => SIDE_LABELS[side as keyof typeof SIDE_LABELS]).join(" + ")})`,
      icon: "ri-id-card-line", statuses: ["CREATED", "DOCUMENT_REQUIRED", "DOCUMENT_PROCESSING"] },
  ];
  if (level !== "DOCUMENT_ONLY") steps.push({ key: "selfie", label: "Selfie", icon: "ri-user-smile-line", statuses: ["SELFIE_REQUIRED"] });
  if (level === "DOCUMENT_FACE_LIVENESS" || level === "DOCUMENT_FACE_LIVENESS_NFC")
    steps.push({ key: "liveness", label: "Movement check", icon: "ri-shield-user-line", statuses: ["LIVENESS_REQUIRED"] });
  if (level === "DOCUMENT_FACE_LIVENESS_NFC") steps.push({ key: "nfc", label: "Passport chip", icon: "ri-scan-line", statuses: ["NFC_REQUIRED"] });
  steps.push({ key: "decision", label: "Decision", icon: "ri-scales-3-line", statuses: ["PROCESSING"] });
  return steps;
}

export default function VerificationSession() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  const [credential] = useState<Client | null>(() => readApplicantCredential(sessionId));
  const [session, setSession] = useState<KycSession | null>(null);
  const [error, setError] = useState("");
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [consented, setConsented] = useState(() => {
    try {
      return sessionStorage.getItem(consentKey(sessionId)) === "1";
    } catch {
      return false;
    }
  });
  const [now, setNow] = useState(Date.now());
  // Shown above the card capture when the selfie step sent the person back to retake the card.
  const [portraitRecapture, setPortraitRecapture] = useState(false);

  const refresh = useCallback(async () => {
    if (!credential) return;
    try {
      const current = await kycApi.getSession(credential, sessionId);
      setSession(current);
      setCheckedAt(Date.now());
      setError("");
      if (APPLICANT_FINISHED.has(current.status)) navigate(`/verify/${encodeURIComponent(sessionId)}/done`, { replace: true });
    } catch (caught) {
      const failure = caught as ApiError;
      setError(failure.status === 401 ? "This link is no longer valid. It may have expired, or a newer link was sent to you."
        : failure.status === 404 || failure.status === 422 ? "This verification link is not valid."
        : failure.message);
    }
  }, [credential, sessionId, navigate]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // The server works on its own in these statuses; keep checking until it moves on.
  const status = session?.status;
  useEffect(() => {
    if (status && status !== "CREATED" && status !== "DOCUMENT_REQUIRED") setPortraitRecapture(false);
  }, [status]);
  useEffect(() => {
    if (status !== "DOCUMENT_PROCESSING" && status !== "PROCESSING") return;
    const timer = window.setTimeout(refresh, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [status, checkedAt, refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000);
    return () => window.clearInterval(timer);
  }, []);

  const onSessionChanged = useCallback(() => { refresh(); }, [refresh]);
  const markConsented = () => {
    try {
      sessionStorage.setItem(consentKey(sessionId), "1");
    } catch {
      // ignore
    }
    setConsented(true);
  };
  const consentMissing = () => {
    try {
      sessionStorage.removeItem(consentKey(sessionId));
    } catch {
      // ignore
    }
    setConsented(false);
  };

  const sides = useMemo(() => (session ? requiredSides(session.expected_document_type) : []), [session]);
  const steps = useMemo(() => (session ? stepsFor(session.verification_level, sides) : []), [session, sides]);
  const current = session ? Math.max(0, steps.findIndex((step) => step.statuses.includes(session.status))) : 0;
  const minutesLeft = session ? Math.max(0, Math.round((Date.parse(session.expires_at) - now) / 60000)) : null;

  if (!credential) {
    return (
      <ApplicantShell>
        <ApplicantMessage icon="ri-link-unlink" title="Open the link you were sent"
                 text="This page needs the full verification link from the service that asked you to verify. Open that link again on this device." />
      </ApplicantShell>
    );
  }

  let stage = null;
  if (session) {
    const s = session.status;
    if ((s === "CREATED" || s === "DOCUMENT_REQUIRED") && !consented) {
      stage = <ConsentStage credential={credential} sessionId={sessionId} documentType={session.expected_document_type}
                            onConsented={markConsented} onSessionChanged={onSessionChanged} />;
    } else if (s === "CREATED" || s === "DOCUMENT_REQUIRED") {
      stage = (
        <>
          {portraitRecapture && (
            <div role="alert" className="mb-4 flex gap-3 rounded-lg border border-accent-200 bg-accent-50 px-4 py-3">
              <i className="ri-error-warning-line mt-0.5 text-lg leading-none text-accent-700"></i>
              <div className="font-label text-sm text-accent-900">
                <p className="font-semibold">Please take your ID card photos again</p>
                <p className="mt-1">
                  We couldn't see the face photo on your card clearly enough to compare it with your selfie.
                  Lay the card flat in good light without reflections, tap the photo on the card to focus,
                  and hold the phone still while it takes the picture.
                </p>
              </div>
            </div>
          )}
          <CaptureStage key="document" credential={credential} sessionId={sessionId}
                        documentType={session.expected_document_type} sides={sides}
                        onSessionChanged={onSessionChanged} onConsentMissing={consentMissing} onUploaded={refresh} />
        </>
      );
    } else if (s === "SELFIE_REQUIRED") {
      stage = <SelfieStage credential={credential} sessionId={sessionId} onSessionChanged={onSessionChanged}
                           onDocumentRecapture={() => setPortraitRecapture(true)} />;
    } else if (s === "LIVENESS_REQUIRED") {
      stage = <LivenessStage credential={credential} sessionId={sessionId} onSessionChanged={onSessionChanged} />;
    } else if (s === "NFC_REQUIRED") {
      stage = <NfcStage credential={credential} sessionId={sessionId} onSessionChanged={onSessionChanged} onRefresh={refresh} />;
    } else if (s === "DOCUMENT_PROCESSING" || s === "PROCESSING") {
      stage = <ProcessingStage status={s} checkedAt={checkedAt} onRefresh={refresh} />;
    }
  }

  return (
    <ApplicantShell>
      {error && (
        <div role="alert" className="mb-4 flex items-center justify-between gap-3 rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">
          <span>{error}</span>
          <button type="button" onClick={refresh} className="shrink-0 underline">Try again</button>
        </div>
      )}
      {!session ? (
        !error && (
          <div className="flex items-center justify-center gap-2 py-16 font-label text-sm text-foreground-500">
            <i className="ri-loader-4-line animate-spin text-base leading-none"></i>Loading your verification…
          </div>
        )
      ) : (
        <>
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="font-heading text-2xl font-semibold tracking-tight text-foreground-950">Verify your identity</h1>
              <p className="mt-1 font-label text-sm text-foreground-600">
                {documentLabel(session.expected_document_type)}
                {minutesLeft !== null && ` · ${minutesLeft > 0 ? `about ${minutesLeft} min left` : "expiring now"}`}
              </p>
            </div>
            <StatusBadge meta={statusMeta(session.status)} />
          </div>
          <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
            <Stepper steps={steps} current={current} />
          </div>
          <div className="mt-4 rounded-lg border border-background-200 bg-background-50 p-5 md:p-6">{stage}</div>
          {["SELFIE_REQUIRED", "LIVENESS_REQUIRED", "NFC_REQUIRED", "PROCESSING"].includes(session.status) && (
            <div className="mt-4">
              <ApplicantGovernmentVerification credential={credential} sessionId={sessionId} version={session.version} />
            </div>
          )}
          <p className="mt-4 flex items-start gap-2 font-label text-xs text-foreground-500">
            <i className="ri-shield-check-line mt-0.5 text-sm leading-none"></i>
            Your photos are encrypted and only used to verify you. Face data is never shared through the API and is deleted after the retention period.
          </p>
        </>
      )}
    </ApplicantShell>
  );
}
