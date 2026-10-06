import { instructionText } from "@/lib/catalog";

export interface QualityMetric {
  key: string;
  label: string;
  value: number; // 0..1, as the quality gate returns it
}

interface QualityGridProps {
  title: string;
  passed: boolean;
  metrics: QualityMetric[];
  overall?: number | null;
  instructions: string[];
  policyVersion?: string;
}

/** The server's quality-gate scores for one capture, and what to change when it was refused. */
export default function QualityGrid({ title, passed, metrics, overall, instructions, policyVersion }: QualityGridProps) {
  const percent = (value: number) => Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className={`rounded-lg border bg-background-50 p-4 md:p-5 ${passed ? "border-background-200" : "border-accent-300"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-heading text-sm font-semibold text-foreground-950">{title}</h3>
        <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-label text-[11px] font-medium ${
          passed ? "border-primary-200 bg-primary-100 text-primary-900" : "border-accent-300 bg-accent-100 text-accent-900"}`}>
          <i className={`${passed ? "ri-check-line" : "ri-refresh-line"} text-xs leading-none`}></i>
          {passed ? "Accepted" : "Retake needed"}
          {overall !== undefined && overall !== null ? ` · ${percent(overall)}%` : ""}
        </span>
      </div>

      {metrics.length > 0 && (
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {metrics.map((metric) => {
            const value = percent(metric.value);
            return (
              <div key={metric.key}>
                <div className="flex items-center justify-between font-label text-xs">
                  <span className="text-foreground-600">{metric.label}</span>
                  <span className="font-medium text-foreground-900">{value}%</span>
                </div>
                <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-background-200">
                  <div className={`h-full rounded-full ${value >= 70 ? "bg-primary-500" : value >= 45 ? "bg-accent-500" : "bg-accent-600"}`}
                       style={{ width: `${value}%` }}></div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {instructions.length > 0 && (
        <div className={`mt-4 rounded-md border p-3 ${passed ? "border-background-200 bg-background-100/60" : "border-accent-200 bg-accent-100/60"}`}>
          <p className="flex items-center gap-1.5 font-label text-xs font-semibold text-foreground-900">
            <i className="ri-lightbulb-line text-sm leading-none"></i>
            {passed ? "Tips" : "What to change"}
          </p>
          <ul className="mt-2 space-y-1.5">
            {instructions.map((code) => (
              <li key={code} className="flex items-start gap-2 font-label text-xs text-foreground-800">
                <i className="ri-arrow-right-s-line mt-0.5 text-sm leading-none"></i>
                <span>{instructionText(code)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {policyVersion && <p className="mt-3 font-mono text-[10px] text-foreground-400">{policyVersion}</p>}
    </div>
  );
}
