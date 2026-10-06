import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import ReviewFilters, { type ReviewFilterState } from "@/pages/review/components/ReviewFilters";
import { AllSessionsTable, QueueTable } from "@/pages/review/components/ReviewQueueTable";
import { ApiError } from "@/api/http";
import { reviewApi } from "@/api/review";
import { SESSION_STATUSES, type QueueItem, type SessionListItem, type SessionStatus } from "@/api/types";
import { useReviewer } from "@/auth/useStaffAuth";

const PAGE = 50;

function readFilters(params: URLSearchParams): ReviewFilterState {
  const status = params.get("status") as SessionStatus | null;
  return {
    view: params.get("view") === "all" ? "all" : "queue",
    status: status && SESSION_STATUSES.includes(status) ? status : "",
    userId: params.get("user") ?? "",
  };
}

export default function ReviewQueuePage() {
  const navigate = useNavigate();
  const { credential, signOut } = useReviewer();
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);

  const [queue, setQueue] = useState<{ total: number; items: QueueItem[] } | null>(null);
  const [all, setAll] = useState<{ total: number; items: SessionListItem[] } | null>(null);
  const [counts, setCounts] = useState<Partial<Record<SessionStatus, number>> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null);
  const [reload, setReload] = useState(0);

  const fail = useCallback((caught: unknown) => {
    if (caught instanceof ApiError && caught.status === 401) signOut();  // token expired or rotated
    else setError(caught instanceof Error ? caught.message : "Something went wrong.");
  }, [signOut]);

  // Queue total and per-status counts feed the tabs and stat cards on either view.
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    Promise.all([
      reviewApi.queue(credential, { limit: 100 }),
      reviewApi.sessions(credential, {
        limit: PAGE, status: filters.view === "all" ? filters.status : "", userId: filters.view === "all" ? filters.userId : "",
      }),
    ]).then(([queuePage, sessionPage]) => {
      if (!live) return;
      setQueue({ total: queuePage.total, items: queuePage.items });
      setAll({ total: sessionPage.total, items: sessionPage.items });
      setCounts(sessionPage.counts);
      setRefreshedAt(Date.now());
    }).catch((caught) => live && fail(caught)).finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [credential, filters.view, filters.status, filters.userId, reload, fail]);

  const loadMore = async () => {
    if (!all) return;
    setLoading(true);
    try {
      const next = await reviewApi.sessions(credential, {
        limit: PAGE, offset: all.items.length, status: filters.status, userId: filters.userId,
      });
      setAll({ total: next.total, items: [...all.items, ...next.items] });
    } catch (caught) {
      fail(caught);
    } finally {
      setLoading(false);
    }
  };

  const setFilters = (next: ReviewFilterState) => {
    const search = new URLSearchParams();
    if (next.view === "all") search.set("view", "all");
    if (next.view === "all" && next.status) search.set("status", next.status);
    if (next.view === "all" && next.userId) search.set("user", next.userId);
    setParams(search);
  };

  const open = (id: string) => navigate(`/review/${id}`);
  const highSignalCases = queue ? queue.items.filter((item) => item.high_signals.length > 0).length : null;
  const stats = [
    { label: "Waiting for review", value: queue?.total, icon: "ri-inbox-line", tone: "primary" },
    { label: "With high-severity signals", value: highSignalCases, icon: "ri-alarm-warning-line", tone: "accent" },
    { label: "Verified", value: counts?.VERIFIED, icon: "ri-verified-badge-line", tone: "primary" },
    { label: "Rejected", value: counts?.REJECTED, icon: "ri-close-circle-line", tone: "secondary" },
  ];

  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <nav className="flex items-center gap-1.5 font-label text-xs text-foreground-500">
              <span>Console</span>
              <i className="ri-arrow-right-s-line text-sm leading-none"></i>
              <span className="text-foreground-800">Review</span>
            </nav>
            <h1 className="mt-2 font-heading text-2xl font-semibold tracking-tight text-foreground-950 md:text-3xl">
              Manual review
            </h1>
            <p className="mt-1.5 max-w-2xl font-label text-sm text-foreground-600">
              Cases the risk engine could not settle wait here for a human decision. Cases in review do not expire.
              Every case you open, and every photo you view, is recorded in the audit log.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setReload((n) => n + 1)}
            disabled={loading}
            className="inline-flex items-center gap-2 whitespace-nowrap rounded-md border border-background-200 bg-background-100/70 px-3 py-2 font-label text-xs text-foreground-600 transition-colors hover:bg-background-100 disabled:opacity-60"
          >
            <i className={`ri-refresh-line text-sm leading-none text-primary-600 ${loading ? "animate-spin" : ""}`}></i>
            {refreshedAt ? `Refreshed ${new Date(refreshedAt).toLocaleTimeString()}` : "Loading…"}
          </button>
        </div>

        <section className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4 md:gap-4">
          {stats.map((s) => (
            <div key={s.label} className="rounded-lg border border-background-200 bg-background-50 p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="font-label text-xs text-foreground-500">{s.label}</span>
                <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
                  s.tone === "primary" ? "bg-primary-100 text-primary-700"
                    : s.tone === "accent" ? "bg-accent-100 text-accent-700" : "bg-secondary-100 text-secondary-700"}`}>
                  <i className={`${s.icon} text-sm leading-none`}></i>
                </span>
              </div>
              <div className="mt-2 font-heading text-2xl font-semibold text-foreground-950">
                {s.value === undefined || s.value === null ? "—" : s.value.toLocaleString()}
              </div>
            </div>
          ))}
        </section>

        <section className="mt-6">
          <ReviewFilters
            value={filters}
            onChange={setFilters}
            onOpenSession={open}
            queueTotal={queue?.total ?? null}
            counts={counts}
            resultCount={filters.view === "all" ? all?.total ?? null : null}
          />
        </section>

        {error && (
          <div role="alert" className="mt-4 flex items-center justify-between gap-3 rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">
            <span>{error}</span>
            <button type="button" onClick={() => setReload((n) => n + 1)} className="underline">Try again</button>
          </div>
        )}

        <section className="mt-4">
          {filters.view === "queue" ? (
            queue ? <QueueTable items={queue.items} onOpen={open} /> : <Loading />
          ) : all ? (
            <>
              <AllSessionsTable items={all.items} onOpen={open} />
              {all.items.length < all.total && (
                <div className="mt-3 flex justify-center">
                  <button type="button" onClick={loadMore} disabled={loading}
                          className="rounded-md border border-background-300 px-4 py-2 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100 disabled:opacity-60">
                    Load more ({(all.total - all.items.length).toLocaleString()} left)
                  </button>
                </div>
              )}
            </>
          ) : <Loading />}
          {filters.view === "queue" && queue && queue.total > queue.items.length && (
            <p className="mt-3 font-label text-xs text-foreground-500">
              Showing the {queue.items.length} longest-waiting of {queue.total.toLocaleString()} cases.
            </p>
          )}
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}

function Loading() {
  return (
    <div className="flex items-center justify-center gap-2 rounded-lg border border-background-200 bg-background-50 py-16 font-label text-sm text-foreground-500">
      <i className="ri-loader-4-line animate-spin text-base leading-none"></i>
      Loading…
    </div>
  );
}
