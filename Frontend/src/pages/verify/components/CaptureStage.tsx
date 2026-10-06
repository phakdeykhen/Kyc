import { useEffect, useState } from "react";
import type { DocumentSide, DocumentTypeOption, QualityReport } from "@/types/kyc";
import { documentQualityReport } from "@/lib/kycSimulation";
import QualityGrid from "@/pages/verify/components/QualityGrid";
import DemoToggle from "@/pages/verify/components/DemoToggle";

interface CaptureStageProps {
  documentType: DocumentTypeOption;
  side: DocumentSide;
  onComplete: (report: QualityReport) => void;
  onBack: () => void;
}

type Phase = "align" | "analyzing" | "review";

const sideLabel = (side: DocumentSide) => (side === "FRONT" ? "Front" : "Back");

export default function CaptureStage({ documentType, side, onComplete, onBack }: CaptureStageProps) {
  const [phase, setPhase] = useState<Phase>("align");
  const [report, setReport] = useState<QualityReport | null>(null);
  const [simulatePoor, setSimulatePoor] = useState(false);

  useEffect(() => {
    if (phase !== "analyzing") return;
    const t = setTimeout(() => {
      setReport(documentQualityReport(side, simulatePoor));
      setPhase("review");
    }, 1700);
    return () => clearTimeout(t);
  }, [phase, side, simulatePoor]);

  const handleDemoToggle = (value: boolean) => {
    setSimulatePoor(value);
    if (phase === "review") setReport(documentQualityReport(side, value));
  };

  const isCard =
    documentType.family === "NATIONAL_ID" || documentType.family === "NSSF" || documentType.family === "RESIDENCE_CARD";
  const ratio = isCard ? "1.586" : "1.42";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-heading text-lg font-semibold text-foreground-950">
            Capture {sideLabel(side).toLowerCase()} of document
          </h2>
          <p className="mt-0.5 font-label text-sm text-foreground-600">
            {documentType.label} · place it flat on a plain background
          </p>
        </div>
        <span className="hidden items-center gap-1.5 rounded-full bg-secondary-100 px-3 py-1 font-label text-xs text-secondary-700 sm:inline-flex">
          <i className="ri-scan-2-line text-sm leading-none"></i>
          Auto quality check
        </span>
      </div>

      <DemoToggle enabled={simulatePoor} onChange={handleDemoToggle} />

      {phase !== "review" ? (
        <div className="relative overflow-hidden rounded-xl border border-background-300 bg-foreground-950">
          <div className="kyc-grid-bg absolute inset-0 opacity-[0.06]"></div>
          <div className="flex min-h-[360px] items-center justify-center p-6 md:min-h-[440px]">
            <div className="relative w-full max-w-[560px]" style={{ aspectRatio: ratio }}>
              <div className="absolute inset-0 rounded-lg border-2 border-dashed border-background-50/25"></div>
              {/* corner brackets */}
              {[
                "left-0 top-0 border-l-2 border-t-2 rounded-tl-lg",
                "right-0 top-0 border-r-2 border-t-2 rounded-tr-lg",
                "left-0 bottom-0 border-l-2 border-b-2 rounded-bl-lg",
                "right-0 bottom-0 border-r-2 border-b-2 rounded-br-lg",
              ].map((cls) => (
                <span key={cls} className={`absolute h-8 w-8 border-primary-400 ${cls}`}></span>
              ))}

              {phase === "align" ? (
                <div className="absolute inset-6 flex flex-col justify-between rounded-lg bg-background-50/[0.04] p-4">
                  <div className="flex items-start gap-2">
                    <span className="flex h-12 w-9 items-center justify-center rounded bg-background-50/10">
                      <i className="ri-user-line text-lg leading-none text-background-50/40"></i>
                    </span>
                    <div className="flex-1">
                      <div className="h-2.5 w-28 rounded bg-background-50/15"></div>
                      <div className="mt-1.5 h-2 w-20 rounded bg-background-50/10"></div>
                      <div className="mt-3 h-2 w-40 rounded bg-background-50/10"></div>
                      <div className="mt-1.5 h-2 w-32 rounded bg-background-50/10"></div>
                    </div>
                  </div>
                  <div className="h-2 w-24 rounded bg-background-50/10"></div>
                  <div className="animate-scanline absolute inset-x-0 top-1/2 h-16 bg-gradient-to-b from-transparent via-primary-400/25 to-transparent"></div>
                </div>
              ) : (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
                  <span className="relative flex h-14 w-14 items-center justify-center">
                    <span className="absolute inset-0 animate-pulse-ring rounded-full border-2 border-primary-400"></span>
                    <i className="ri-loader-4-line animate-spin text-3xl leading-none text-primary-400"></i>
                  </span>
                  <span className="font-label text-sm text-background-50/80">Reading document quality…</span>
                </div>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-center gap-2 border-t border-background-50/10 px-4 py-3">
            {[
              { ok: true, label: "Document detected" },
              { ok: true, label: "Orientation correct" },
              { ok: phase === "analyzing", label: "Checking blur & glare" },
            ].map((hint) => (
              <span
                key={hint.label}
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-label text-[11px] ${
                  hint.ok ? "bg-primary-500/20 text-primary-100" : "bg-background-50/10 text-background-50/60"
                }`}
              >
                <i className={`${hint.ok ? "ri-check-line" : "ri-loader-4-line"} text-xs leading-none`}></i>
                {hint.label}
              </span>
            ))}
          </div>

          {phase === "align" && (
            <div className="absolute bottom-5 left-1/2 -translate-x-1/2">
              <button
                type="button"
                onClick={() => setPhase("analyzing")}
                className="flex h-16 w-16 items-center justify-center rounded-full border-4 border-background-50/80 bg-primary-500 text-background-50 transition-transform hover:scale-105"
                aria-label="Capture"
              >
                <i className="ri-camera-lens-line text-2xl leading-none"></i>
              </button>
            </div>
          )}
        </div>
      ) : (
        report && <QualityGrid report={report} title="Document quality assessment" />
      )}

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={phase === "align" ? onBack : () => setPhase("align")}
          className="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100"
        >
          <i className="ri-arrow-left-line text-base leading-none"></i>
          {phase === "align" ? "Cancel" : "Retake"}
        </button>

        {phase === "review" && report && report.passed && (
          <button
            type="button"
            onClick={() => onComplete(report)}
            className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
          >
            Use this photo
            <i className="ri-arrow-right-line text-base leading-none"></i>
          </button>
        )}

        {phase === "review" && report && !report.passed && (
          <button
            type="button"
            onClick={() => {
              setReport(null);
              setPhase("align");
            }}
            className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-accent-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-accent-600"
          >
            <i className="ri-camera-lens-line text-base leading-none"></i>
            Recapture document
          </button>
        )}
      </div>
    </div>
  );
}