import { useEffect, useMemo, useState } from "react";
import { livenessChallenges } from "@/mocks/kyc";

interface LivenessStageProps {
  onComplete: () => void;
  onBack: () => void;
}

export default function LivenessStage({ onComplete, onBack }: LivenessStageProps) {
  const challenge = useMemo(
    () => livenessChallenges[Math.floor(Math.random() * livenessChallenges.length)],
    [],
  );
  const [progress, setProgress] = useState(0);
  const done = progress >= 100;

  useEffect(() => {
    if (done) return;
    const t = setInterval(() => {
      setProgress((p) => Math.min(100, p + 4));
    }, 90);
    return () => clearInterval(t);
  }, [done]);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="font-heading text-lg font-semibold text-foreground-950">Liveness challenge</h2>
        <p className="mt-0.5 font-label text-sm text-foreground-600">
          Follow the on-screen prompt. The challenge is chosen at random each attempt.
        </p>
      </div>

      <div className="relative overflow-hidden rounded-xl border border-background-300 bg-foreground-950">
        <div className="flex min-h-[360px] flex-col items-center justify-center gap-5 p-6 md:min-h-[440px]">
          <div
            className="flex h-40 w-40 items-center justify-center rounded-full p-1.5"
            style={{
              background: `conic-gradient(oklch(var(--primary-500)) ${progress}%, oklch(var(--background-800)) 0)`,
            }}
          >
            <div className="flex h-full w-full items-center justify-center rounded-full bg-foreground-950">
              <i
                className={`${done ? "ri-check-line text-primary-400" : "ri-user-smile-line text-background-50/70"} text-5xl leading-none transition-colors`}
              ></i>
            </div>
          </div>

          <div className="text-center">
            {done ? (
              <>
                <p className="font-heading text-lg font-semibold text-background-50">Liveness confirmed</p>
                <p className="mt-1 font-label text-sm text-background-50/60">
                  Presentation-attack score 0.97 · challenge matched
                </p>
              </>
            ) : (
              <>
                <p className="font-label text-xs uppercase tracking-widest text-primary-300">Your challenge</p>
                <p className="mt-1.5 font-heading text-xl font-semibold text-background-50">{challenge}</p>
                <p className="mt-1.5 font-label text-sm text-background-50/60">Hold still while we confirm motion…</p>
              </>
            )}
          </div>

          <div className="w-full max-w-[320px]">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-background-50/10">
              <div className="h-full rounded-full bg-primary-500 transition-all" style={{ width: `${progress}%` }}></div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100"
        >
          <i className="ri-arrow-left-line text-base leading-none"></i>
          Back
        </button>

        <button
          type="button"
          disabled={!done}
          onClick={onComplete}
          className={`inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md px-5 py-2.5 font-label text-sm font-medium transition-colors ${
            done
              ? "bg-primary-500 text-background-50 hover:bg-primary-600"
              : "cursor-not-allowed bg-background-300 text-foreground-400"
          }`}
        >
          Run verification
          <i className="ri-cpu-line text-base leading-none"></i>
        </button>
      </div>
    </div>
  );
}