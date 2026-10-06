import { useEffect, useState } from "react";
import { pipelineSteps } from "@/mocks/kyc";

interface ProcessingStageProps {
  onDone: () => void;
}

export default function ProcessingStage({ onDone }: ProcessingStageProps) {
  const [doneCount, setDoneCount] = useState(0);
  const complete = doneCount >= pipelineSteps.length;
  const progress = Math.round((doneCount / pipelineSteps.length) * 100);

  useEffect(() => {
    if (complete) {
      const t = setTimeout(onDone, 1000);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setDoneCount((c) => c + 1), 430);
    return () => clearTimeout(t);
  }, [doneCount, complete, onDone]);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-heading text-lg font-semibold text-foreground-950">Running verification pipeline</h2>
        <p className="mt-0.5 font-label text-sm text-foreground-600">
          Deterministic checks — no single model is the final authority.
        </p>
      </div>

      <div className="rounded-lg border border-background-200 bg-background-50 p-5">
        <div className="flex items-center justify-between font-label text-xs">
          <span className="text-foreground-600">Pipeline progress</span>
          <span className="font-medium text-foreground-900">{progress}%</span>
        </div>
        <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-background-200">
          <div className="h-full rounded-full bg-primary-500 transition-all duration-300" style={{ width: `${progress}%` }}></div>
        </div>

        <div className="mt-5 flex flex-col">
          {pipelineSteps.map((step, i) => {
            const done = i < doneCount;
            const active = i === doneCount;
            return (
              <div key={step.id} className="flex items-start gap-3 py-2">
                <span
                  className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${
                    done ? "bg-primary-100 text-primary-700" : active ? "bg-accent-100 text-accent-700" : "bg-background-100 text-foreground-300"
                  }`}
                >
                  <i
                    className={`${done ? "ri-check-line" : active ? "ri-loader-4-line animate-spin" : step.icon} text-base leading-none`}
                  ></i>
                </span>
                <div className="min-w-0 flex-1">
                  <div className={`font-label text-sm ${done || active ? "font-medium text-foreground-950" : "text-foreground-400"}`}>
                    {step.label}
                  </div>
                  <div className={`font-label text-xs ${done || active ? "text-foreground-500" : "text-foreground-300"}`}>
                    {step.detail}
                  </div>
                </div>
                {done && (
                  <span className="mt-1 font-mono text-[10px] text-primary-700">
                    {`${(0.18 + i * 0.11).toFixed(2)}s`}
                  </span>
                )}
              </div>
            );
          })}
        </div>

        {complete && (
          <div className="mt-4 flex animate-fade-up items-center gap-3 rounded-md border border-primary-200 bg-primary-50 px-4 py-3">
            <i className="ri-checkbox-circle-fill text-xl leading-none text-primary-600"></i>
            <div>
              <div className="font-label text-sm font-medium text-primary-900">Decision computed: PASS</div>
              <div className="font-label text-xs text-primary-700">
                Reason codes: DOCUMENT_VALID · FACE_MATCH · LIVENESS_PASS
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}