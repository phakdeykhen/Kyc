import type { QualityReport } from "@/types/kyc";

interface QualityGridProps {
  report: QualityReport;
  title: string;
}

export default function QualityGrid({ report, title }: QualityGridProps) {
  const good = report.passed;
  return (
    <div
      className={`rounded-lg border bg-background-50 p-4 md:p-5 ${
        good ? "border-background-200" : "border-accent-300"
      }`}
    >
      <div className="flex items-center justify-between">
        <h3 className="font-heading text-sm font-semibold text-foreground-950">{title}</h3>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-label text-[11px] font-medium ${
            good
              ? "border-primary-200 bg-primary-100 text-primary-900"
              : "border-accent-300 bg-accent-100 text-accent-900"
          }`}
        >
          <i className={`${good ? "ri-check-line" : "ri-refresh-line"} text-xs leading-none`}></i>
          {good ? `Overall ${report.overall}%` : `Recapture needed · ${report.overall}%`}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {report.metrics.map((m) => (
          <div key={m.key}>
            <div className="flex items-center justify-between font-label text-xs">
              <span className="text-foreground-600">{m.label}</span>
              <span className="font-medium text-foreground-900">
                {m.value}
                {m.unit}
              </span>
            </div>
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-background-200">
              <div
                className={`h-full rounded-full ${m.value >= 85 ? "bg-primary-500" : m.value >= 70 ? "bg-accent-500" : "bg-accent-600"}`}
                style={{ width: `${m.value}%` }}
              ></div>
            </div>
          </div>
        ))}
      </div>

      {!good && report.issues.length > 0 && (
        <div className="mt-4 rounded-md border border-accent-200 bg-accent-100/60 p-3">
          <p className="flex items-center gap-1.5 font-label text-xs font-semibold text-accent-900">
            <i className="ri-error-warning-line text-sm leading-none"></i>
            Why this capture was rejected
          </p>
          <ul className="mt-2 space-y-1.5">
            {report.issues.map((issue) => (
              <li key={issue} className="flex items-start gap-2 font-label text-xs text-accent-900/90">
                <i className="ri-arrow-right-s-line mt-0.5 text-sm leading-none"></i>
                <span>{issue}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}