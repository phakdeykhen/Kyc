const documentMix = [
  { label: "Cambodia National ID", value: 42, tone: "bg-primary-500" },
  { label: "Cambodia Passport", value: 21, tone: "bg-primary-400" },
  { label: "International Passport", value: 15, tone: "bg-accent-500" },
  { label: "Cambodia NSSF", value: 12, tone: "bg-secondary-400" },
  { label: "Foreign National ID", value: 10, tone: "bg-secondary-300" },
];

const topSignals = [
  { code: "LOW_OCR_CONFIDENCE", label: "Low OCR confidence", count: 34, tone: "text-accent-700 bg-accent-100" },
  { code: "FACE_SCORE_BORDERLINE", label: "Face score borderline", count: 21, tone: "text-accent-700 bg-accent-100" },
  { code: "FIELD_MISMATCH", label: "Cross-source mismatch", count: 12, tone: "text-accent-700 bg-accent-100" },
  { code: "EXPIRED_DOCUMENT", label: "Expired document", count: 7, tone: "text-accent-900 bg-accent-200" },
];

export default function SignalPanel() {
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
        <h3 className="font-heading text-sm font-semibold text-foreground-950">Document mix</h3>
        <p className="mt-0.5 font-label text-xs text-foreground-500">Share of sessions by document type</p>
        <div className="mt-4 flex flex-col gap-3">
          {documentMix.map((d) => (
            <div key={d.label}>
              <div className="flex items-center justify-between font-label text-xs">
                <span className="text-foreground-700">{d.label}</span>
                <span className="font-medium text-foreground-900">{d.value}%</span>
              </div>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-background-200">
                <div className={`h-full rounded-full ${d.tone}`} style={{ width: `${d.value}%` }}></div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
        <div className="flex items-center justify-between">
          <h3 className="font-heading text-sm font-semibold text-foreground-950">Top review signals</h3>
          <i className="ri-alarm-warning-line text-base leading-none text-accent-600"></i>
        </div>
        <div className="mt-3.5 flex flex-col gap-2">
          {topSignals.map((s) => (
            <div key={s.code} className="flex items-center justify-between gap-3 rounded-md border border-background-100 px-3 py-2">
              <div className="min-w-0">
                <div className="truncate font-label text-xs font-medium text-foreground-800">{s.label}</div>
                <div className="truncate font-mono text-[10px] text-foreground-500">{s.code}</div>
              </div>
              <span className={`shrink-0 rounded-full px-2 py-0.5 font-label text-[11px] font-semibold ${s.tone}`}>
                {s.count}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}