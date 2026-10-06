import StatusBadge from "@/components/base/StatusBadge";
import type { CaseHistoryEntry } from "@/api/types";
import { decisionMeta } from "@/lib/kycSimulation";
import { dateTime, humanize } from "@/pages/review/format";

const ACTION_BADGE = { APPROVE: "PASS", REJECT: "FAIL", REQUEST_RECAPTURE: "REVIEW" } as const;

/** Earlier reviewer decisions on this session. Notes are shown only to roles that may see identity data. */
export default function AuditTrail({ entries }: { entries: CaseHistoryEntry[] }) {
  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
      <h3 className="font-heading text-sm font-semibold text-foreground-950">Review history</h3>
      {entries.length === 0 ? (
        <p className="mt-3 font-label text-sm text-foreground-500">No earlier reviews.</p>
      ) : (
        <ol className="mt-4 flex flex-col">
          {entries.map((e, i) => (
            <li key={`${e.decided_at}-${i}`} className="relative flex gap-3 pb-4 last:pb-0">
              {i < entries.length - 1 && <span className="absolute left-[11px] top-6 h-full w-px bg-background-200"></span>}
              <span className="relative z-10 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-secondary-100 text-secondary-700">
                <i className="ri-history-line text-xs leading-none"></i>
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge meta={{ ...decisionMeta(ACTION_BADGE[e.action]), label: humanize(e.action) }} size="sm" />
                  <span className="font-label text-sm font-medium text-foreground-900">{humanize(e.reason_code)}</span>
                  <span className="font-mono text-[10px] text-foreground-400">{dateTime(e.decided_at)}</span>
                </div>
                {e.note && <p className="mt-1 whitespace-pre-wrap font-label text-xs text-foreground-700">{e.note}</p>}
                <p className="mt-0.5 font-label text-[11px] text-foreground-500">by {e.reviewer}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
