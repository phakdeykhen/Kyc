import { useEffect, useState, type FormEvent } from "react";
import { SESSION_STATUSES, type SessionStatus } from "@/api/types";
import { humanize } from "@/pages/review/format";

export type ReviewView = "queue" | "all";

export interface ReviewFilterState {
  view: ReviewView;
  status: SessionStatus | "";
  userId: string;
}

interface ReviewFiltersProps {
  value: ReviewFilterState;
  onChange: (next: ReviewFilterState) => void;
  onOpenSession: (sessionId: string) => void;
  queueTotal: number | null;
  counts: Partial<Record<SessionStatus, number>> | null;
  resultCount: number | null;
}

const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const field = "rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-sm text-foreground-800 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100";

export default function ReviewFilters({ value, onChange, onOpenSession, queueTotal, counts, resultCount }: ReviewFiltersProps) {
  const [userId, setUserId] = useState(value.userId);
  const [sessionId, setSessionId] = useState("");
  const [idError, setIdError] = useState("");
  useEffect(() => setUserId(value.userId), [value.userId]);

  const total = counts ? Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0) : null;
  const tabs: { id: ReviewView; label: string; icon: string; count: number | null }[] = [
    { id: "queue", label: "Waiting for review", icon: "ri-eye-line", count: queueTotal },
    { id: "all", label: "All sessions", icon: "ri-stack-line", count: total },
  ];

  const search = (event: FormEvent) => {
    event.preventDefault();
    onChange({ ...value, userId: userId.trim() });
  };

  const open = (event: FormEvent) => {
    event.preventDefault();
    const id = sessionId.trim();
    if (!UUID.test(id)) {
      setIdError("Paste a full session ID (UUID).");
      return;
    }
    setIdError("");
    onOpenSession(id);
  };

  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-3 md:p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex items-center gap-1 rounded-full border border-background-200 bg-background-100 p-1" role="tablist">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={value.view === tab.id}
              onClick={() => onChange({ ...value, view: tab.id })}
              className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1.5 font-label text-xs font-medium transition-colors ${
                value.view === tab.id ? "bg-background-50 text-foreground-950" : "text-foreground-600 hover:text-foreground-900"
              }`}
            >
              <i className={`${tab.icon} text-sm leading-none`}></i>
              {tab.label}
              {tab.count !== null && <span className="text-foreground-500">({tab.count.toLocaleString()})</span>}
            </button>
          ))}
        </div>
        {resultCount !== null && (
          <span className="font-label text-xs text-foreground-500">
            {resultCount.toLocaleString()} {resultCount === 1 ? "session" : "sessions"} matched
          </span>
        )}
      </div>

      <div className="mt-3 flex flex-col gap-2 lg:flex-row lg:items-center">
        {value.view === "all" && (
          <>
            <select
              aria-label="Status"
              value={value.status}
              onChange={(e) => onChange({ ...value, status: e.target.value as SessionStatus | "" })}
              className={`${field} cursor-pointer`}
            >
              <option value="">All statuses</option>
              {SESSION_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {humanize(status)}{counts ? ` (${(counts[status] ?? 0).toLocaleString()})` : ""}
                </option>
              ))}
            </select>
            <form onSubmit={search} className="relative flex flex-1 gap-2">
              <div className="relative flex-1">
                <i className="ri-user-search-line pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm leading-none text-foreground-400"></i>
                <input
                  type="search"
                  aria-label="Customer user ID"
                  value={userId}
                  maxLength={128}
                  onChange={(e) => setUserId(e.target.value)}
                  placeholder="Customer user ID (exact match)"
                  className={`${field} w-full pl-9`}
                />
              </div>
              <button type="submit" className="whitespace-nowrap rounded-md border border-background-300 px-3 py-2 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100">
                Search
              </button>
            </form>
            <button
              type="button"
              onClick={() => onChange({ ...value, status: "", userId: "" })}
              className="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 px-3 py-2 font-label text-sm text-foreground-600 transition-colors hover:bg-background-100"
            >
              <i className="ri-refresh-line text-sm leading-none"></i>
              Reset
            </button>
          </>
        )}
        <form onSubmit={open} className={`flex gap-2 ${value.view === "all" ? "lg:w-80" : "flex-1 lg:max-w-md"}`}>
          <input
            aria-label="Session ID"
            value={sessionId}
            spellCheck={false}
            onChange={(e) => { setSessionId(e.target.value); if (idError) setIdError(""); }}
            placeholder="Open session by ID…"
            className={`${field} w-full font-mono text-xs`}
          />
          <button type="submit" className="whitespace-nowrap rounded-md bg-primary-500 px-3 py-2 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600">
            Open
          </button>
        </form>
      </div>
      {idError && <p className="mt-2 font-label text-xs text-accent-700">{idError}</p>}
    </div>
  );
}
