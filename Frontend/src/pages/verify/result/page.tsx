import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApplicantMessage, ApplicantShell } from "@/components/feature/ApplicantShell";
import { kycApi, type Client } from "@/api/client";
import { ApiError } from "@/api/http";
import type { KycSession } from "@/api/types";
import { readApplicantCredential } from "@/pages/verify/applicantLink";

// The person's view of the outcome. Reason codes, scores and reviewer notes stay with the
// integrating service (GET /result with an API key, and webhooks); none of them are shown here.

const OUTCOMES = {
  VERIFIED: { icon: "ri-verified-badge-line", tone: "ok", title: "Identity verified", text: "You are done. You can close this page." },
  MANUAL_REVIEW: { icon: "ri-time-line", tone: "neutral", title: "Your details are being reviewed",
    text: "A member of staff will check your verification. You can close this page; the service that sent you will be told the result." },
  REJECTED: { icon: "ri-close-circle-line", tone: "warn", title: "We could not verify your identity",
    text: "Contact the service that sent you if you think this is a mistake." },
  EXPIRED: { icon: "ri-timer-flash-line", tone: "warn", title: "This verification expired",
    text: "It was not completed in time. Ask the service that sent you for a new link." },
} as const;

const REVIEW_POLL_MS = 15000;

export default function VerificationResult() {
  const { sessionId = "" } = useParams();
  const [credential] = useState<Client | null>(() => readApplicantCredential(sessionId));
  const [session, setSession] = useState<KycSession | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!credential) return;
    try {
      setSession(await kycApi.getSession(credential, sessionId));
      setError("");
    } catch (caught) {
      const failure = caught as ApiError;
      setError(failure.status === 401 ? "This link is no longer valid." : failure.message);
    }
  }, [credential, sessionId]);

  useEffect(() => {
    load();
  }, [load]);

  // A case in review can still change; keep the page current while it is open.
  useEffect(() => {
    if (session?.status !== "MANUAL_REVIEW") return;
    const timer = window.setInterval(load, REVIEW_POLL_MS);
    return () => window.clearInterval(timer);
  }, [session?.status, load]);

  if (!credential) {
    return (
      <ApplicantShell>
        <ApplicantMessage icon="ri-link-unlink" title="Open the link you were sent"
                          text="This page needs the full verification link from the service that asked you to verify." />
      </ApplicantShell>
    );
  }
  if (error) {
    return <ApplicantShell><ApplicantMessage icon="ri-error-warning-line" tone="warn" title="Something went wrong" text={error} /></ApplicantShell>;
  }
  if (!session) {
    return (
      <ApplicantShell>
        <div className="flex items-center justify-center gap-2 py-16 font-label text-sm text-foreground-500">
          <i className="ri-loader-4-line animate-spin text-base leading-none"></i>Loading…
        </div>
      </ApplicantShell>
    );
  }
  const outcome = OUTCOMES[session.status as keyof typeof OUTCOMES];
  if (!outcome) {
    return (
      <ApplicantShell>
        <ApplicantMessage icon="ri-arrow-go-back-line" title="Your verification is not finished" text="There are still steps to complete.">
          <Link to={`/verify/${encodeURIComponent(sessionId)}`}
                className="mt-2 inline-flex items-center gap-2 rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 hover:bg-primary-600">
            Continue
          </Link>
        </ApplicantMessage>
      </ApplicantShell>
    );
  }
  return (
    <ApplicantShell>
      <ApplicantMessage icon={outcome.icon} tone={outcome.tone} title={outcome.title} text={outcome.text} />
    </ApplicantShell>
  );
}
