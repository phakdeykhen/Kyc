import StatusBadge from "@/components/base/StatusBadge";
import { decisionMeta } from "@/lib/kycSimulation";
import { reasonCodes } from "@/mocks/kyc";
import type { KYCResult } from "@/types/kyc";

interface DecisionPanelProps {
  result: KYCResult;
}

const severityClass = {
  info: "bg-primary-100 text-primary-900",
  warning: "bg-accent-100 text-accent-900",
  critical: "bg-accent-600 text-background-50",
};

export default function DecisionPanel({ result }: DecisionPanelProps) {
  const meters = [
    { label: "Face match score", value: result.decision.faceMatchScore },
    { label: "Liveness score", value: result.decision.livenessScore },
  ];

  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
      <div className="flex items-center justify-between">
        <h3 className="font-heading text-sm font-semibold text-foreground-950">Risk decision</h3>
        <StatusBadge meta={decisionMeta(result.decision.result)} size="sm" />
      </div>

      <div className="mt-4 flex flex-col gap-3">
        {meters.map((m) => (
          <div key={m.label}>
            <div className="flex items-center justify-between font-label text-xs">
              <span className="text-foreground-600">{m.label}</span>
              <span className="font-mono font-medium text-foreground-900">{m.value.toFixed(2)}</span>
            </div>
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-background-200">
              <div className="h-full rounded-full bg-primary-500" style={{ width: `${m.value * 100}%` }}></div>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4 border-t border-background-200 pt-4">
        <div className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">Reason codes</div>
        <div className="mt-2.5 flex flex-col gap-2">
          {result.decision.reasonCodes.map((code) => {
            const meta = reasonCodes.find((r) => r.code === code);
            const severity = meta?.severity || "info";
            return (
              <div key={code} className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-label text-xs text-foreground-800">{meta?.label || code}</div>
                  <div className="truncate font-mono text-[10px] text-foreground-500">{code}</div>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-0.5 font-label text-[10px] font-medium ${severityClass[severity]}`}>
                  {severity}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}