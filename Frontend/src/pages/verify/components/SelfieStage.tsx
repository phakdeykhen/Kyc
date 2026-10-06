import { useEffect, useState } from "react";
import type { QualityReport } from "@/types/kyc";
import { faceQualityReport } from "@/lib/kycSimulation";
import QualityGrid from "@/pages/verify/components/QualityGrid";
import DemoToggle from "@/pages/verify/components/DemoToggle";

interface SelfieStageProps {
  onComplete: (report: QualityReport) => void;
  onBack: () => void;
}

type Phase = "align" | "analyzing" | "review";

const liveHints = ["Exactly one face", "Face centered", "Eyes visible", "Good lighting"];

export default function SelfieStage({ onComplete, onBack }: SelfieStageProps) {
  const [phase, setPhase] = useState<Phase>("align");
  const [report, setReport] = useState<QualityReport | null>(null);
  const [simulatePoor, setSimulatePoor] = useState(false);

  useEffect(() => {
    if (phase !== "analyzing") return;
    const t = setTimeout(() => {
      setReport(faceQualityReport(simulatePoor));
      setPhase("review");
    }, 1500);
    return () => clearTimeout(t);
  }, [phase, simulatePoor]);

  const handleDemoToggle = (value: boolean) => {
    setSimulatePoor(value);
    if (phase === "review") setReport(faceQualityReport(value));
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-heading text-lg font-semibold text-foreground-950">Take a live selfie</h2>
        <p className="mt-0.5 font-label text-sm text-foreground-600">
          Align your face inside the oval and hold still
        </p>
      </div>

      <DemoToggle enabled={simulatePoor} onChange={handleDemoToggle} />

      {phase !== "review" ? (
        <div className="relative overflow-hidden rounded-xl border border-background-300 bg-foreground-950">
          <div className="flex min-h-[360px] items-center justify-center p-6 md:min-h-[440px]">
            <div className="relative flex h-[300px] w-[230px] items-center justify-center md:h-[360px] md:w-[270px]">
              <div className="absolute inset-0 rounded-full border-2 border-dashed border-background-50/25"></div>
              <span className="absolute inset-0 animate-pulse-ring rounded-full border-2 border-primary-400"></span>
              <i className="ri-user-line text-[110px] leading-none text-background-50/15 md:text-[130px]"></i>

              {phase === "analyzing" && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-full bg-foreground-950/70">
                  <i className="ri-loader-4-line animate-spin text-3xl leading-none text-primary-400"></i>
                  <span className="font-label text-xs text-background-50/80">Assessing face quality…</span>
                </div>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-center gap-2 border-t border-background-50/10 px-4 py-3">
            {liveHints.map((hint) => (
              <span
                key={hint}
                className="inline-flex items-center gap-1.5 rounded-full bg-primary-500/20 px-2.5 py-1 font-label text-[11px] text-primary-100"
              >
                <i className="ri-check-line text-xs leading-none"></i>
                {hint}
              </span>
            ))}
          </div>

          {phase === "align" && (
            <div className="absolute bottom-5 left-1/2 -translate-x-1/2">
              <button
                type="button"
                onClick={() => setPhase("analyzing")}
                className="flex h-16 w-16 items-center justify-center rounded-full border-4 border-background-50/80 bg-primary-500 text-background-50 transition-transform hover:scale-105"
                aria-label="Capture selfie"
              >
                <i className="ri-camera-lens-line text-2xl leading-none"></i>
              </button>
            </div>
          )}
        </div>
      ) : (
        report && <QualityGrid report={report} title="Face quality assessment" />
      )}

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={phase === "align" ? onBack : () => setPhase("align")}
          className="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100"
        >
          <i className="ri-arrow-left-line text-base leading-none"></i>
          {phase === "align" ? "Back" : "Retake"}
        </button>

        {phase === "review" && report && report.passed && (
          <button
            type="button"
            onClick={() => onComplete(report)}
            className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
          >
            Continue to liveness
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
            <i className="ri-user-smile-line text-base leading-none"></i>
            Retake selfie
          </button>
        )}
      </div>
    </div>
  );
}