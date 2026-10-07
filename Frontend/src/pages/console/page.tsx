import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import StatCard from "@/pages/console/components/StatCard";
import SessionsTable from "@/pages/console/components/SessionsTable";
import SignalPanel from "@/pages/console/components/SignalPanel";
import { ApiError } from "@/api/http";
import { reviewApi } from "@/api/review";
import type { QueueItem, SessionList, SessionStatus } from "@/api/types";
import { useReviewer } from "@/auth/useStaffAuth";

const IN_PROGRESS: SessionStatus[] = [
  "CREATED", "DOCUMENT_REQUIRED", "DOCUMENT_PROCESSING", "SELFIE_REQUIRED", "LIVENESS_REQUIRED", "NFC_REQUIRED", "PROCESSING",
];
const REFRESH_MS = 30000;

const pipelineStages = [
  { icon: "ri-file-text-line", label: "Document engine", sub: "Quality gate, classification, Khmer/Latin OCR, MRZ, QR, chip" },
  { icon: "ri-user-smile-line", label: "Biometric engine", sub: "Selfie, active liveness, 1:1 face match" },
  { icon: "ri-git-compare-line", label: "Fraud engine", sub: "Cross-check every source, 7 detectors" },
  { icon: "ri-scales-3-line", label: "Risk engine", sub: "Deterministic policy: PASS · REVIEW · FAIL" },
];

export default function ConsoleHome() {
  const navigate = useNavigate();
  const { credential, profile, signOut } = useReviewer();
  const [sessions, setSessions] = useState<SessionList | null>(null);
  const [queue, setQueue] = useState<{ total: number; items: QueueItem[] } | null>(null);
  const [error, setError] = useState("");
  const [loadedAt, setLoadedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sessionPage, queuePage] = await Promise.all([
        reviewApi.sessions(credential, { limit: 100 }),
        reviewApi.queue(credential, { limit: 100 }),
      ]);
      setSessions(sessionPage);
      setQueue({ total: queuePage.total, items: queuePage.items });
      setLoadedAt(Date.now());
      setError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return signOut();
      setError((caught as Error).message);
    } finally {
      setLoading(false);
    }
  }, [credential, signOut]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const counts = sessions?.counts;
  const total = sessions?.total ?? null;
  const verified = counts?.VERIFIED ?? null;
  const rejected = counts?.REJECTED ?? null;
  const decided = verified !== null && rejected !== null ? verified + rejected : null;
  const inProgress = counts ? IN_PROGRESS.reduce((sum, status) => sum + (counts[status] ?? 0), 0) : null;
  const percent = (part: number | null) => (part !== null && decided ? `${Math.round((part / decided) * 100)}% of decided` : undefined);

  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <section className="overflow-hidden rounded-xl border border-background-200 bg-background-100/60">
          <div className="grid grid-cols-1 gap-8 p-6 md:p-9 lg:grid-cols-[1.15fr_1fr] lg:items-center">
            <div>
              <span className="inline-flex items-center gap-2 rounded-full border border-primary-200 bg-primary-50 px-3 py-1 font-label text-xs font-medium text-primary-800">
                <span className="h-1.5 w-1.5 rounded-full bg-primary-500"></span>
                Signed in as {profile.display_name} · {profile.role.toLowerCase()}
              </span>
              <h1 className="mt-4 font-heading text-3xl font-semibold leading-tight tracking-tight text-foreground-950 md:text-4xl">
                Verify identities across countries, from one console
              </h1>
              <p className="mt-3 max-w-xl font-label text-sm leading-relaxed text-foreground-600 md:text-base">
                Start a session, send the person their capture link, and follow it through the document, biometric, fraud
                and risk engines. Anything the rules cannot settle lands in the review queue.
              </p>
              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                <button type="button" onClick={() => navigate("/verify/new")}
                        className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600">
                  <i className="ri-add-line text-base leading-none"></i>
                  Start a verification
                </button>
                <button type="button" onClick={() => navigate("/review")}
                        className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 bg-background-50 px-5 py-2.5 font-label text-sm font-medium text-foreground-800 transition-colors hover:bg-background-100">
                  <i className="ri-eye-line text-base leading-none"></i>
                  Review queue{queue ? ` (${queue.total})` : ""}
                </button>
              </div>
            </div>

            <div className="rounded-lg border border-background-200 bg-background-50 p-5">
              <div className="flex items-center justify-between">
                <span className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">Verification pipeline</span>
                <button type="button" onClick={load} disabled={loading}
                        className="inline-flex items-center gap-1 rounded-full bg-primary-100 px-2 py-0.5 font-label text-[11px] font-medium text-primary-800 disabled:opacity-60">
                  <i className={`ri-refresh-line text-xs leading-none ${loading ? "animate-spin" : ""}`}></i>
                  {loadedAt ? new Date(loadedAt).toLocaleTimeString() : "live"}
                </button>
              </div>
              <div className="mt-4 flex flex-col gap-2.5">
                {pipelineStages.map((stage) => (
                  <div key={stage.label} className="flex items-center gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary-100 text-secondary-700">
                      <i className={`${stage.icon} text-base leading-none`}></i>
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="font-label text-sm font-medium text-foreground-900">{stage.label}</div>
                      <div className="font-label text-[11px] text-foreground-500">{stage.sub}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {error && (
          <div role="alert" className="mt-4 flex items-center justify-between gap-3 rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">
            <span>{error}</span>
            <button type="button" onClick={load} className="underline">Try again</button>
          </div>
        )}

        <section className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-5">
          <StatCard label="All sessions" value={total} note="in your organization" icon="ri-stack-line" tone="secondary" />
          <StatCard label="In progress" value={inProgress} note="waiting for the person or the engines" icon="ri-loader-4-line" tone="secondary" />
          <StatCard label="Manual review" value={queue?.total ?? null} note="waiting for a reviewer" icon="ri-eye-line" tone="accent" />
          <StatCard label="Verified" value={verified} note={percent(verified)} icon="ri-verified-badge-line" tone="primary" />
          <StatCard label="Rejected" value={rejected} note={percent(rejected)} icon="ri-close-circle-line" tone="accent" />
        </section>

        <section className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <SessionsTable items={sessions ? sessions.items.slice(0, 12) : null} total={total} />
          </div>
          <SignalPanel sessions={sessions?.items ?? null} queue={queue?.items ?? null} />
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}
