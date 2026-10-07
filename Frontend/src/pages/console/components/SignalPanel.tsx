import { Link } from "react-router-dom";
import type { QueueItem, SessionListItem } from "@/api/types";
import { documentLabel } from "@/lib/catalog";
import { humanize } from "@/pages/review/format";

interface SignalPanelProps {
  sessions: SessionListItem[] | null;
  queue: QueueItem[] | null;
}

const TONES = ["bg-primary-500", "bg-primary-400", "bg-accent-500", "bg-secondary-400", "bg-secondary-300", "bg-background-400"];

function tally(values: string[]): [string, number][] {
  const counts = new Map<string, number>();
  values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/** Document mix of recent sessions, and why cases are waiting for review (from the live queue). */
export default function SignalPanel({ sessions, queue }: SignalPanelProps) {
  const mix = sessions ? tally(sessions.map((item) => item.expected_document_type)) : [];
  const reasons = queue ? tally(queue.flatMap((item) => [...item.reason_codes, ...item.high_signals])).slice(0, 6) : [];
  const share = (count: number) => (sessions && sessions.length ? Math.round((count / sessions.length) * 100) : 0);

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
        <h3 className="font-heading text-sm font-semibold text-foreground-950">Document mix</h3>
        <p className="mt-0.5 font-label text-xs text-foreground-500">
          {sessions ? `Share of the latest ${sessions.length} sessions` : "Loading…"}
        </p>
        <div className="mt-4 flex flex-col gap-3">
          {sessions && mix.length === 0 && <p className="font-label text-xs text-foreground-500">No sessions yet.</p>}
          {mix.map(([type, count], index) => (
            <div key={type}>
              <div className="flex items-center justify-between font-label text-xs">
                <span className="text-foreground-700">{documentLabel(type)}</span>
                <span className="font-medium text-foreground-900">{share(count)}% <span className="text-foreground-400">({count})</span></span>
              </div>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-background-200">
                <div className={`h-full rounded-full ${TONES[index % TONES.length]}`} style={{ width: `${share(count)}%` }}></div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
        <div className="flex items-center justify-between">
          <h3 className="font-heading text-sm font-semibold text-foreground-950">Why cases wait for review</h3>
          <i className="ri-alarm-warning-line text-base leading-none text-accent-600"></i>
        </div>
        <p className="mt-0.5 font-label text-xs text-foreground-500">Reason codes and high-severity signals in the queue</p>
        <div className="mt-3.5 flex flex-col gap-2">
          {queue && reasons.length === 0 && <p className="font-label text-xs text-foreground-500">The review queue is empty.</p>}
          {reasons.map(([code, count]) => (
            <div key={code} className="flex items-center justify-between gap-3 rounded-md border border-background-100 px-3 py-2">
              <div className="min-w-0">
                <div className="truncate font-label text-xs font-medium text-foreground-800">{humanize(code)}</div>
                <div className="truncate font-mono text-[10px] text-foreground-500">{code}</div>
              </div>
              <span className="shrink-0 rounded-full bg-accent-100 px-2 py-0.5 font-label text-[11px] font-semibold text-accent-700">{count}</span>
            </div>
          ))}
        </div>
        {queue && queue.length > 0 && (
          <Link to="/review" className="mt-3 inline-flex items-center gap-1 font-label text-xs text-primary-700 hover:underline">
            Open the review queue <i className="ri-arrow-right-line text-sm leading-none"></i>
          </Link>
        )}
      </div>
    </div>
  );
}
