import StatusBadge from "@/components/base/StatusBadge";
import { checkMeta } from "@/lib/kycSimulation";
import type { CheckResult, ResultChecksBlock } from "@/types/kyc";

interface ChecksListProps {
  checks: ResultChecksBlock;
}

const rows: { key: keyof ResultChecksBlock; label: string; icon: string }[] = [
  { key: "documentQuality", label: "Document quality", icon: "ri-focus-3-line" },
  { key: "mrz", label: "MRZ", icon: "ri-barcode-box-line" },
  { key: "barcode", label: "QR / barcode", icon: "ri-qr-scan-2-line" },
  { key: "nfc", label: "ePassport NFC", icon: "ri-scan-line" },
  { key: "faceMatch", label: "Face match (1:1)", icon: "ri-user-follow-line" },
  { key: "liveness", label: "Liveness / anti-spoof", icon: "ri-shield-user-line" },
  { key: "fraud", label: "Fraud signals", icon: "ri-alarm-warning-line" },
];

export default function ChecksList({ checks }: ChecksListProps) {
  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
      <h3 className="font-heading text-sm font-semibold text-foreground-950">Verification checks</h3>
      <div className="mt-3.5 flex flex-col gap-2">
        {rows.map((row) => (
          <div
            key={row.key}
            className="flex items-center justify-between gap-3 rounded-md border border-background-100 px-3 py-2.5"
          >
            <span className="flex min-w-0 items-center gap-2">
              <i className={`${row.icon} text-base leading-none text-foreground-500`}></i>
              <span className="truncate font-label text-sm text-foreground-800">{row.label}</span>
            </span>
            <StatusBadge meta={checkMeta(checks[row.key] as CheckResult)} size="sm" />
          </div>
        ))}
      </div>
    </div>
  );
}