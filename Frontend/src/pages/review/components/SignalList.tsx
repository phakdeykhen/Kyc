import StatusBadge from "@/components/base/StatusBadge";
import type { CaseSignal } from "@/api/types";
import { humanize, severityBadge } from "@/pages/review/format";

export default function SignalList({ signals }: { signals: CaseSignal[] }) {
  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-heading text-sm font-semibold text-foreground-950">Fraud signals</h3>
        <span className="rounded-full bg-background-200 px-2.5 py-1 font-label text-[11px] text-foreground-700">
          {signals.length} raised
        </span>
      </div>

      {signals.length === 0 ? (
        <div className="mt-3.5 flex items-center gap-2 rounded-md border border-primary-200 bg-primary-50 px-3 py-3 font-label text-sm text-primary-800">
          <i className="ri-shield-check-line text-base leading-none"></i>
          No fraud signals on this session.
        </div>
      ) : (
        <div className="mt-3.5 flex flex-col gap-2">
          {signals.map((s) => (
            <div key={`${s.signal}-${s.detector}`} className="rounded-md border border-background-100 bg-background-50 px-3 py-2.5">
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-start gap-2">
                  <i className="ri-alarm-warning-line mt-0.5 text-base leading-none text-accent-600"></i>
                  <span className="font-label text-sm font-medium text-foreground-900">{humanize(s.signal)}</span>
                </div>
                <StatusBadge meta={severityBadge(s.severity)} size="sm" />
              </div>
              <p className="mt-1.5 pl-6 font-label text-xs leading-relaxed text-foreground-600">
                {[humanize(s.category),
                  s.fields.length ? `fields: ${s.fields.join(", ")}` : "",
                  s.sources.length ? `sources: ${s.sources.join(" vs ")}` : ""].filter(Boolean).join(" · ")}
              </p>
              {Object.keys(s.details).length > 0 && (
                <p className="mt-1 break-words pl-6 font-mono text-[10px] text-foreground-500">{JSON.stringify(s.details)}</p>
              )}
              <p className="mt-1 pl-6 font-mono text-[10px] text-foreground-400">{s.signal}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
