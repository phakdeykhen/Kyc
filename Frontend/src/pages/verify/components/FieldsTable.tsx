import type { ExtractedField } from "@/types/kyc";

interface FieldsTableProps {
  fields: ExtractedField[];
}

export default function FieldsTable({ fields }: FieldsTableProps) {
  return (
    <div className="rounded-lg border border-background-200 bg-background-50">
      <div className="flex items-center justify-between border-b border-background-200 px-4 py-3.5 md:px-5">
        <h3 className="font-heading text-sm font-semibold text-foreground-950">Extracted fields</h3>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-secondary-100 px-2.5 py-1 font-label text-[11px] text-secondary-700">
          <i className="ri-scan-2-line text-xs leading-none"></i>
          OCR · raw vs normalized
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] border-collapse text-left">
          <thead>
            <tr className="border-b border-background-200">
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500 md:px-5">Field</th>
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Raw OCR</th>
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Normalized</th>
              <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => (
              <tr key={f.field} className="border-b border-background-100 last:border-0">
                <td className="px-4 py-3 md:px-5">
                  <div className="font-label text-sm text-foreground-900">{f.label}</div>
                  <div className="font-mono text-[10px] text-foreground-500">{f.field}</div>
                </td>
                <td className="px-4 py-3 font-mono text-xs text-foreground-600">{f.rawValue}</td>
                <td className="px-4 py-3 font-mono text-xs font-medium text-foreground-950">{f.normalizedValue}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-14 overflow-hidden rounded-full bg-background-200">
                      <div
                        className={`h-full rounded-full ${f.confidence >= 0.9 ? "bg-primary-500" : "bg-accent-500"}`}
                        style={{ width: `${Math.round(f.confidence * 100)}%` }}
                      ></div>
                    </div>
                    <span className="font-mono text-xs text-foreground-700">{Math.round(f.confidence * 100)}%</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}