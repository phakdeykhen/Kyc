import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Plus, RefreshCw } from "lucide-react";
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
  { icon: "ri-file-text-line", label: "Document checks", sub: "Capture quality, details, and document authenticity" },
  { icon: "ri-user-smile-line", label: "Face & liveness", sub: "Selfie comparison and guided movement checks" },
  { icon: "ri-git-compare-line", label: "Fraud signals", sub: "Cross-check the evidence for inconsistencies" },
  { icon: "ri-scales-3-line", label: "Identity decision", sub: "Verified, rejected, or ready for your review" },
];

export default function ConsoleHome() {
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
        <section className="console-welcome">
          <div>
            <span className="eyebrow"><span className="status-dot" />YOUR IDENTITY WORKSPACE</span>
            <h1>Every identity.<br />A clearer picture.</h1>
            <p>Welcome back, {profile.display_name}. Start a verification, follow your sessions, and give the cases that need you a closer look.</p>
            <div className="hero-actions">
              <Link to="/verify/new" className="button button-white"><Plus size={17} />Start a verification</Link>
              <Link to="/review" className="button button-dark">Review queue{queue ? ` (${queue.total})` : ""}<ArrowRight size={16} /></Link>
            </div>
          </div>
          <div className="console-pipeline">
            <div className="console-pipeline-heading">
              <span>From capture to confidence</span>
              <button type="button" onClick={load} disabled={loading} aria-label="Refresh verification data"><RefreshCw size={12} className={loading ? "animate-spin" : ""} />{loadedAt ? new Date(loadedAt).toLocaleTimeString() : "Refresh"}</button>
            </div>
            <div className="flex flex-col gap-5">
              {pipelineStages.map((stage, index) => (
                <div key={stage.label} className="flex items-center gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-background-300 bg-background-100 text-foreground-800"><i className={`${stage.icon} text-lg leading-none`} aria-hidden="true"></i></span>
                  <div className="min-w-0 flex-1"><div className="text-xs font-medium text-foreground-900">{stage.label}</div><div className="mt-1 text-[10px] leading-relaxed text-foreground-500">{stage.sub}</div></div>
                  <span className="text-[10px] text-foreground-400">0{index + 1}</span>
                </div>
              ))}
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
