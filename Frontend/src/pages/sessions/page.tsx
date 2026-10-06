import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import StatusBadge from "@/components/base/StatusBadge";
import Modal from "@/components/base/Modal";
import ApiKeyGate from "@/components/feature/ApiKeyGate";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import { reviewApi } from "@/api/review";
import type { ErasureReport, ReviewCase, SessionResult } from "@/api/types";
import { useApiKey, useReviewer } from "@/auth/useStaffAuth";
import { checkMeta, decisionMeta, statusMeta } from "@/lib/badges";
import { countryFlag, countryName, documentLabel } from "@/lib/catalog";
import { applicantLink } from "@/pages/verify/applicantLink";
import { CHECK_LABELS, dateTime, humanize, shortId } from "@/pages/review/format";

const card = "rounded-lg border border-background-200 bg-background-50 p-4 md:p-5";
const title = "font-heading text-sm font-semibold text-foreground-950";
const OPEN_STATUSES = new Set(["CREATED", "DOCUMENT_REQUIRED", "DOCUMENT_PROCESSING", "SELFIE_REQUIRED", "LIVENESS_REQUIRED", "NFC_REQUIRED"]);

/** One session as the customer's backend sees it: status, masked result, and the backend actions. */
export default function SessionDetailPage() {
  const { sessionId = "" } = useParams();
  const { credential, signOut } = useReviewer();
  const [data, setData] = useState<ReviewCase | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let live = true;
    reviewApi.getCase(credential, sessionId)
      .then((value) => { if (live) { setData(value); setError(""); } })
      .catch((caught) => {
        if (!live) return;
        if (caught instanceof ApiError && caught.status === 401) return signOut();
        setError(caught instanceof ApiError && caught.status === 404 ? "No session with this ID in your organization."
          : caught instanceof ApiError && caught.status === 422 ? "That is not a valid session ID." : (caught as Error).message);
      });
    return () => { live = false; };
  }, [credential, sessionId, signOut, reload]);

  const session = data?.session;
  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />
      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <nav className="flex items-center gap-1.5 font-label text-xs text-foreground-500">
          <Link to="/console" className="hover:text-foreground-800">Console</Link>
          <i className="ri-arrow-right-s-line text-sm leading-none"></i>
          <span className="text-foreground-800">Session <span className="font-mono">{shortId(sessionId)}</span></span>
        </nav>
        {error && <p role="alert" className="mt-4 rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{error}</p>}
        {!session ? (!error && (
          <div className="mt-10 flex items-center justify-center gap-2 font-label text-sm text-foreground-500">
            <i className="ri-loader-4-line animate-spin text-base leading-none"></i>Loading session…
          </div>
        )) : (
          <>
            <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="font-heading text-2xl font-semibold tracking-tight text-foreground-950">
                    {documentLabel(session.expected_document_type)}
                  </h1>
                  <StatusBadge meta={statusMeta(session.status)} size="sm" />
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-label text-xs text-foreground-500">
                  <span className="inline-flex items-center gap-1"><i className="ri-user-line text-sm leading-none"></i>{session.user_id}</span>
                  <span>{countryFlag(session.country)} {countryName(session.country)}</span>
                  <span>{humanize(session.verification_level)}</span>
                  <span>Created {dateTime(session.created_at)}</span>
                  {OPEN_STATUSES.has(session.status) && <span>Expires {dateTime(session.expires_at)}</span>}
                </div>
                <div className="mt-1 font-mono text-[11px] text-foreground-400">{session.session_id}</div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Link to={`/review/${session.session_id}`}
                      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 px-3.5 py-2.5 font-label text-sm font-medium text-foreground-800 hover:bg-background-100">
                  <i className="ri-file-search-line text-base leading-none"></i>
                  {session.status === "MANUAL_REVIEW" ? "Review this case" : "Full case evidence"}
                </Link>
                <button type="button" onClick={() => setReload((n) => n + 1)}
                        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 px-3.5 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">
                  <i className="ri-refresh-line text-base leading-none"></i>Refresh
                </button>
              </div>
            </div>

            <div className="mt-6">
              <ApiKeyGate purpose="read results and act on this session" scopes={["sessions:read", "sessions:write", "data:erase"]}>
                <BackendView data={data} onChanged={() => setReload((n) => n + 1)} />
              </ApiKeyGate>
            </div>
          </>
        )}
      </main>
      <SiteFooter />
    </div>
  );
}

function BackendView({ data, onChanged }: { data: ReviewCase; onChanged: () => void }) {
  const { apiKey, has } = useApiKey();
  const session = data.session;
  const [result, setResult] = useState<SessionResult | null>(null);
  const [resultError, setResultError] = useState("");
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "link" | "verify" | "erase">("");
  const [actionError, setActionError] = useState("");
  const [eraseOpen, setEraseOpen] = useState(false);
  const [erased, setErased] = useState<ErasureReport | null>(null);
  const [copied, setCopied] = useState(false);

  const loadResult = useCallback(async () => {
    if (!apiKey || !has("sessions:read")) return;
    try {
      setResult(await kycApi.result(apiKey, session.session_id));
      setResultError("");
    } catch (caught) {
      setResultError((caught as Error).message);
    }
  }, [apiKey, has, session.session_id]);

  useEffect(() => {
    loadResult();
  }, [loadResult, session.version]);

  const act = async (kind: "link" | "verify" | "erase") => {
    if (!apiKey) return;
    setBusy(kind);
    setActionError("");
    try {
      if (kind === "link") {
        const token = await kycApi.clientToken(apiKey, session.session_id);
        setLink(applicantLink(session.session_id, apiKey.organizationId, token.client_token));
      } else if (kind === "verify") {
        await kycApi.verify(apiKey, session.session_id);
        onChanged();
      } else {
        setErased(await kycApi.erase(apiKey, session.session_id));
        setEraseOpen(false);
        onChanged();
      }
      await loadResult();
    } catch (caught) {
      const failure = caught as ApiError;
      setActionError(failure.status === 409 && kind === "verify" ? "The session is not ready for a decision yet (it must be PROCESSING)."
        : failure.message);
    } finally {
      setBusy("");
    }
  };

  const copy = async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const open = OPEN_STATUSES.has(session.status);
  const button = "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md px-4 py-2.5 font-label text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.6fr_1fr] lg:items-start">
      <div className="flex min-w-0 flex-col gap-4">
        {resultError && <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{resultError}</p>}
        {!has("sessions:read") && <p className={`${card} font-label text-sm text-foreground-600`}>This key lacks <code className="font-mono">sessions:read</code>, so the result is not shown.</p>}
        {result && <ResultView result={result} />}
      </div>

      <div className="flex min-w-0 flex-col gap-4">
        {result && <DecisionCard result={result} />}
        <div className={card}>
          <h3 className={title}>Backend actions</h3>
          <p className="mt-1 font-label text-xs text-foreground-500">What your server can do through the API with this key.</p>
          <div className="mt-4 flex flex-col gap-2">
            {open && has("sessions:write") && (
              <button type="button" onClick={() => act("link")} disabled={!!busy}
                      className={`${button} bg-primary-500 text-background-50 hover:bg-primary-600`}>
                <i className={`${busy === "link" ? "ri-loader-4-line animate-spin" : "ri-link"} text-base leading-none`}></i>
                New applicant link
              </button>
            )}
            {session.status === "PROCESSING" && has("sessions:write") && (
              <button type="button" onClick={() => act("verify")} disabled={!!busy}
                      className={`${button} border border-background-300 text-foreground-800 hover:bg-background-100`}>
                <i className={`${busy === "verify" ? "ri-loader-4-line animate-spin" : "ri-scales-3-line"} text-base leading-none`}></i>
                Run the decision now
              </button>
            )}
            {has("data:erase") && !result?.erased_at && (
              <button type="button" onClick={() => setEraseOpen(true)} disabled={!!busy}
                      className={`${button} border border-accent-300 text-accent-700 hover:bg-accent-50`}>
                <i className="ri-delete-bin-6-line text-base leading-none"></i>
                Erase personal data
              </button>
            )}
            {!has("sessions:write") && !has("data:erase") && (
              <p className="font-label text-xs text-foreground-500">This key holds no write or erase scope.</p>
            )}
          </div>
          {link && (
            <div className="mt-4 rounded-md border border-background-200 bg-background-100 p-3">
              <p className="font-label text-xs text-foreground-600">New link issued. The previous link no longer works.</p>
              <div className="mt-2 flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-foreground-900">{link}</code>
                <button type="button" onClick={copy}
                        className="inline-flex items-center gap-1 rounded-md border border-background-300 bg-background-50 px-2 py-1 font-label text-xs text-foreground-700 hover:bg-background-100">
                  <i className={`${copied ? "ri-check-line" : "ri-file-copy-line"} text-sm leading-none`}></i>{copied ? "Copied" : "Copy"}
                </button>
              </div>
            </div>
          )}
          {erased && (
            <p className="mt-4 rounded-md border border-primary-200 bg-primary-50 px-3 py-2 font-label text-xs text-primary-800">
              {erased.already_erased ? "Already erased" : "Erased"} at {dateTime(erased.erased_at)}
              {erased.encrypted_objects ? ` · ${erased.encrypted_objects} stored files queued for deletion` : ""}.
            </p>
          )}
          {actionError && <p role="alert" className="mt-3 font-label text-sm text-accent-700">{actionError}</p>}
        </div>
      </div>

      <Modal open={eraseOpen} onClose={() => setEraseOpen(false)} title="Erase this person's data?" icon="ri-delete-bin-6-line"
             subtitle="Data-subject request. This cannot be undone." maxWidthClass="max-w-md"
             footer={(
               <>
                 <button type="button" onClick={() => setEraseOpen(false)}
                         className="rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">Cancel</button>
                 <button type="button" onClick={() => act("erase")} disabled={busy === "erase"}
                         className="inline-flex items-center gap-2 rounded-md bg-accent-600 px-4 py-2.5 font-label text-sm font-medium text-background-50 hover:bg-accent-700 disabled:opacity-60">
                   <i className={`${busy === "erase" ? "ri-loader-4-line animate-spin" : "ri-delete-bin-6-line"} text-base leading-none`}></i>
                   Erase now
                 </button>
               </>
             )}>
        <p className="font-label text-sm text-foreground-700">
          Photos, document fields, face templates, chip results and reviewer notes are deleted now. The status, decision reason
          codes and the audit trail are kept. An unfinished session is closed first.
        </p>
      </Modal>
    </div>
  );
}

function ResultView({ result }: { result: SessionResult }) {
  const identity = result.identity;
  const identityRows = identity ? [
    ["Full name (Latin)", identity.full_name], ["Full name (local)", identity.full_name_local],
    ["Date of birth", identity.date_of_birth], ["Sex", identity.sex], ["Nationality", identity.nationality],
  ] : [];
  return (
    <>
      {result.erased_at && (
        <p className="rounded-md border border-secondary-200 bg-secondary-100 px-4 py-3 font-label text-sm text-secondary-800">
          Personal and biometric data was erased on {dateTime(result.erased_at)}. Only the status and reason codes remain.
        </p>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className={card}>
          <h3 className={title}>Document</h3>
          {result.document ? (
            <dl className="mt-3 flex flex-col gap-2.5 font-label text-sm">
              <Row label="Type" value={documentLabel(result.document.type)} />
              <Row label="Issuing country" value={result.document.country ?? "—"} />
              <Row label="Number" value={result.document.document_number_masked ?? "—"} mono />
              <Row label="Expiry" value={humanize(result.document.expiry_status)} />
            </dl>
          ) : <p className="mt-3 font-label text-sm text-foreground-500">No document read yet.</p>}
        </div>
        <div className={card}>
          <div className="flex items-center justify-between">
            <h3 className={title}>Identity</h3>
            {result.identity_masked && (
              <span className="rounded-full bg-secondary-100 px-2 py-0.5 font-label text-[10px] text-secondary-700" title="The key lacks results:identity">masked</span>
            )}
          </div>
          {identity ? (
            <dl className="mt-3 flex flex-col gap-2.5 font-label text-sm">
              {identityRows.map(([label, value]) => <Row key={label} label={label as string} value={(value as string | null) ?? "—"} />)}
            </dl>
          ) : <p className="mt-3 font-label text-sm text-foreground-500">No identity fields yet.</p>}
        </div>
      </div>

      <div className={card}>
        <h3 className={title}>Checks</h3>
        {Object.keys(result.checks).length === 0 ? <p className="mt-3 font-label text-sm text-foreground-500">No checks have run yet.</p> : (
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {Object.entries(result.checks).map(([key, value]) => (
              <div key={key} className="flex items-center justify-between gap-3 rounded-md border border-background-100 px-3 py-2">
                <span className="truncate font-label text-sm text-foreground-800">{CHECK_LABELS[key] ?? humanize(key)}</span>
                <StatusBadge meta={checkMeta(value)} size="sm" />
              </div>
            ))}
          </div>
        )}
      </div>

      {result.mrz && (
        <div className={card}>
          <div className="flex items-center justify-between">
            <h3 className={title}>MRZ ({result.mrz.format})</h3>
            <StatusBadge meta={checkMeta(result.mrz.mrz_valid ? "PASS" : "FAIL")} size="sm" />
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(result.mrz.check_digit_results).map(([field, digit]) => (
              <span key={field} className={`rounded px-2 py-0.5 font-mono text-[10px] ${digit.valid ? "bg-primary-100 text-primary-800" : "bg-accent-100 text-accent-800"}`}>
                {field} {digit.valid ? "✓" : "✗"}
              </span>
            ))}
            {Object.entries(result.mrz.field_consistency).map(([field, value]) => (
              <span key={`c-${field}`} className={`rounded px-2 py-0.5 font-mono text-[10px] ${value === "MISMATCH" ? "bg-accent-100 text-accent-800" : "bg-background-100 text-foreground-600"}`}>
                {field}: {value}
              </span>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0 text-xs text-foreground-500">{label}</dt>
      <dd className={`min-w-0 break-words text-right font-medium text-foreground-900 ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}

function DecisionCard({ result }: { result: SessionResult }) {
  return (
    <div className={card}>
      <div className="flex items-center justify-between">
        <h3 className={title}>Decision</h3>
        {result.decision && <StatusBadge meta={decisionMeta(result.decision.result)} size="sm" />}
      </div>
      {!result.decision ? <p className="mt-3 font-label text-sm text-foreground-500">The risk engine has not decided yet.</p> : (
        <>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {result.decision.reason_codes.map((code) => (
              <span key={code} className="rounded bg-background-100 px-2 py-0.5 font-mono text-[10px] text-foreground-700">{code}</span>
            ))}
          </div>
          <p className="mt-2 font-mono text-[10px] text-foreground-400">{result.decision.policy_version} · {dateTime(result.decision.assessed_at)}</p>
        </>
      )}
      {result.review && (
        <div className="mt-4 border-t border-background-200 pt-3">
          <p className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">Manual review</p>
          <p className="mt-1 font-label text-sm text-foreground-800">{humanize(result.review.action)} · <span className="font-mono text-xs">{result.review.reason_code}</span></p>
          <p className="font-label text-xs text-foreground-500">{dateTime(result.review.decided_at)}</p>
        </div>
      )}
      {result.fraud_signals.length > 0 && (
        <div className="mt-4 border-t border-background-200 pt-3">
          <p className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">Fraud signals</p>
          <ul className="mt-2 flex flex-col gap-1.5">
            {result.fraud_signals.map((signal) => (
              <li key={signal.signal} className="flex items-center justify-between gap-2 font-mono text-[11px] text-foreground-800">
                <span className="truncate">{signal.signal}</span>
                <span className={`rounded-full px-2 py-0.5 font-label text-[10px] ${signal.severity === "HIGH" ? "bg-accent-600 text-background-50" : signal.severity === "MEDIUM" ? "bg-accent-100 text-accent-900" : "bg-secondary-100 text-secondary-700"}`}>
                  {signal.severity.toLowerCase()}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.review_flags.length > 0 && (
        <div className="mt-4 border-t border-background-200 pt-3">
          <p className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">Review flags</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {result.review_flags.map((flag) => <span key={flag} className="rounded bg-accent-100 px-2 py-0.5 font-mono text-[10px] text-accent-900">{flag}</span>)}
          </div>
        </div>
      )}
    </div>
  );
}
