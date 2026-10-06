import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import StatusBadge from "@/components/base/StatusBadge";
import SignalList from "@/pages/review/components/SignalList";
import AuditTrail from "@/pages/review/components/AuditTrail";
import DecisionModal from "@/pages/review/components/DecisionModal";
import { BiometricPanel, CaseChecks, CaseFields, CaseImages, RiskPanel } from "@/pages/review/components/CaseEvidence";
import { ApiError } from "@/api/http";
import { reviewApi } from "@/api/review";
import type { ReviewAction, ReviewCase } from "@/api/types";
import { useReviewer } from "@/auth/useStaffAuth";
import { dateTime, humanize, sessionStatusBadge, shortId, since } from "@/pages/review/format";

const ACTIONS: { action: ReviewAction; label: string; icon: string; className: string }[] = [
  { action: "REQUEST_RECAPTURE", label: "Request recapture", icon: "ri-camera-lens-line",
    className: "border border-background-300 bg-background-50 text-foreground-800 hover:bg-background-100" },
  { action: "REJECT", label: "Reject", icon: "ri-close-line", className: "bg-accent-600 text-background-50 hover:bg-accent-700" },
  { action: "APPROVE", label: "Approve", icon: "ri-check-line", className: "bg-primary-500 text-background-50 hover:bg-primary-600" },
];

export default function ReviewSessionPage() {
  const { sessionId = "" } = useParams<{ sessionId: string }>();
  const { credential, signOut } = useReviewer();
  const [data, setData] = useState<ReviewCase | null>(null);
  const [loadError, setLoadError] = useState("");
  const [modalAction, setModalAction] = useState<ReviewAction | null>(null);
  const [saving, setSaving] = useState(false);
  const [decisionError, setDecisionError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await reviewApi.getCase(credential, sessionId));
      setLoadError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return signOut();
      setLoadError(caught instanceof ApiError && caught.status === 404 ? "No session with this ID in your organization."
        : caught instanceof ApiError && caught.status === 422 ? "That is not a valid session ID."
        : (caught as Error).message);
    }
  }, [credential, sessionId, signOut]);

  useEffect(() => {
    setData(null);
    load();
  }, [load]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 5000);
    return () => clearTimeout(timer);
  }, [notice]);

  const decide = async (action: ReviewAction, reasonCode: string, note: string) => {
    if (!data) return;
    setSaving(true);
    setDecisionError("");
    try {
      const result = await reviewApi.decide(credential, sessionId, {
        action, reason_code: reasonCode, note, expected_version: data.session.version,
      });
      setModalAction(null);
      setNotice(`Decision saved: ${humanize(action)} · the session is now ${humanize(result.status).toLowerCase()}.`);
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return signOut();
      const failure = caught as ApiError;
      if (failure.reasonCode === "APPROVAL_BLOCKED") {
        const blockers = (failure.body?.blockers as string[] | undefined) ?? [];
        setDecisionError(`This case cannot be approved (${blockers.join(", ")}). Request recapture or reject instead.`);
      } else if (failure.reasonCode === "CASE_CHANGED" || failure.reasonCode === "CASE_NOT_IN_REVIEW") {
        setModalAction(null);
        setNotice("");
        await load();
        setLoadError(failure.reasonCode === "CASE_CHANGED"
          ? "The case changed since you opened it. It has been reloaded: review it again."
          : "This case is no longer waiting for review.");
      } else {
        setDecisionError(failure.message || "The decision was not saved.");
      }
    } finally {
      setSaving(false);
    }
  };

  const session = data?.session;
  const open = session?.status === "MANUAL_REVIEW";
  const canDecide = !!data && Object.keys(data.decision_options).length > 0;

  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <nav className="flex items-center gap-1.5 font-label text-xs text-foreground-500">
          <Link to="/console" className="hover:text-foreground-800">Console</Link>
          <i className="ri-arrow-right-s-line text-sm leading-none"></i>
          <Link to="/review" className="hover:text-foreground-800">Review</Link>
          <i className="ri-arrow-right-s-line text-sm leading-none"></i>
          <span className="font-mono text-foreground-800">{shortId(sessionId)}</span>
        </nav>

        {notice && (
          <div role="status" className="mt-4 flex items-center gap-2 rounded-md border border-primary-200 bg-primary-50 px-4 py-3 font-label text-sm text-primary-800">
            <i className="ri-checkbox-circle-line text-base leading-none"></i>
            {notice}
          </div>
        )}
        {loadError && (
          <div role="alert" className="mt-4 rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">
            {loadError}
          </div>
        )}

        {!data || !session ? (
          !loadError && (
            <div className="mt-10 flex items-center justify-center gap-2 font-label text-sm text-foreground-500">
              <i className="ri-loader-4-line animate-spin text-base leading-none"></i>
              Loading case…
            </div>
          )
        ) : (
          <>
            <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="font-heading text-2xl font-semibold tracking-tight text-foreground-950">
                    Case <span className="font-mono">{shortId(session.session_id)}</span>
                  </h1>
                  <StatusBadge meta={sessionStatusBadge(session.status)} size="sm" />
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-label text-xs text-foreground-500">
                  <span className="inline-flex items-center gap-1" title="Customer user ID">
                    <i className="ri-user-line text-sm leading-none"></i>{session.user_id}
                  </span>
                  <span>{humanize(session.expected_document_type)} · {session.country}</span>
                  <span>{humanize(session.verification_level)}</span>
                  <span className="inline-flex items-center gap-1">
                    <i className="ri-time-line text-sm leading-none"></i>
                    {open ? `In review ${since(session.updated_at)} · does not expire while in review`
                      : `Last change ${dateTime(session.updated_at)}`}
                  </span>
                </div>
                <div className="mt-1 font-mono text-[11px] text-foreground-400">{session.session_id}</div>
              </div>

              {open && canDecide && (
                <div className="flex flex-wrap items-center gap-2">
                  {ACTIONS.map((item) => (
                    <button key={item.action} type="button"
                            onClick={() => { setDecisionError(""); setModalAction(item.action); }}
                            className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-3.5 py-2.5 font-label text-sm font-medium transition-colors ${item.className}`}>
                      <i className={`${item.icon} text-base leading-none`}></i>
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
              {open && !canDecide && (
                <p className="rounded-md border border-background-200 bg-background-100/60 px-3 py-2 font-label text-xs text-foreground-600">
                  Your role can view this case but not decide it.
                </p>
              )}
            </div>

            <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[1.6fr_1fr] lg:items-start">
              <div className="flex min-w-0 flex-col gap-4">
                <CaseImages credential={credential} data={data} />
                <CaseFields data={data} />
                <CaseChecks data={data} />
                <AuditTrail entries={data.history} />
              </div>
              <div className="flex min-w-0 flex-col gap-4">
                <RiskPanel data={data} />
                <SignalList signals={data.fraud_signals} />
                <BiometricPanel data={data} />
              </div>
            </div>
          </>
        )}
      </main>

      <DecisionModal
        action={modalAction}
        caseLabel={sessionId}
        reasonCodes={modalAction && data ? data.decision_options[modalAction] ?? [] : []}
        busy={saving}
        error={decisionError}
        onClose={() => setModalAction(null)}
        onConfirm={decide}
      />

      <SiteFooter />
    </div>
  );
}
