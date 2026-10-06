import type { SessionStatus } from "@/api/types";

interface ProcessingStageProps {
  status: SessionStatus;
  checkedAt: number | null;
  onRefresh: () => void;
}

const STEPS = [
  { icon: "ri-file-text-line", label: "Reading the document", detail: "Classification, Khmer/Latin OCR, MRZ and barcode" },
  { icon: "ri-git-compare-line", label: "Cross-checking", detail: "Every source compared with every other, plus fraud signals" },
  { icon: "ri-scales-3-line", label: "Deciding", detail: "Fixed rules give one answer with its reasons" },
];

/** Waiting while the server works. The page polls the session; this only shows where it is. */
export default function ProcessingStage({ status, checkedAt, onRefresh }: ProcessingStageProps) {
  const active = status === "DOCUMENT_PROCESSING" ? 0 : 2;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-heading text-lg font-semibold text-foreground-950">
          {status === "DOCUMENT_PROCESSING" ? "Reading your document" : "Checking your details"}
        </h2>
        <p className="mt-0.5 font-label text-sm text-foreground-600">This usually takes a few seconds. Keep this page open.</p>
      </div>
      <div className="rounded-lg border border-background-200 bg-background-50 p-5">
        <div className="flex flex-col">
          {STEPS.map((step, index) => {
            const done = index < active;
            const current = index === active || (status === "PROCESSING" && index === 1);
            return (
              <div key={step.label} className="flex items-start gap-3 py-2">
                <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${
                  done ? "bg-primary-100 text-primary-700" : current ? "bg-accent-100 text-accent-700" : "bg-background-100 text-foreground-300"}`}>
                  <i className={`${done ? "ri-check-line" : current ? "ri-loader-4-line animate-spin" : step.icon} text-base leading-none`}></i>
                </span>
                <div>
                  <div className={`font-label text-sm ${done || current ? "font-medium text-foreground-950" : "text-foreground-400"}`}>{step.label}</div>
                  <div className="font-label text-xs text-foreground-500">{step.detail}</div>
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-background-200 pt-3 font-label text-xs text-foreground-500">
          <span>{checkedAt ? `Last checked ${new Date(checkedAt).toLocaleTimeString()}` : "Checking…"}</span>
          <button type="button" onClick={onRefresh} className="inline-flex items-center gap-1 text-primary-700 hover:underline">
            <i className="ri-refresh-line text-sm leading-none"></i>Check now
          </button>
        </div>
      </div>
    </div>
  );
}
