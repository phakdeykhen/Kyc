import { useNavigate } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import StatCard from "@/pages/console/components/StatCard";
import SessionsTable from "@/pages/console/components/SessionsTable";
import SignalPanel from "@/pages/console/components/SignalPanel";

const pipelineStages = [
  { icon: "ri-file-text-line", label: "Document", sub: "OCR + MRZ + QR" },
  { icon: "ri-user-smile-line", label: "Biometrics", sub: "Face + liveness" },
  { icon: "ri-scales-3-line", label: "Risk", sub: "Policy engine" },
  { icon: "ri-verified-badge-line", label: "Decision", sub: "Pass / Review / Fail" },
];

export default function ConsoleHome() {
  const navigate = useNavigate();

  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <section className="overflow-hidden rounded-xl border border-background-200 bg-background-100/60">
          <div className="grid grid-cols-1 gap-8 p-6 md:p-9 lg:grid-cols-[1.15fr_1fr] lg:items-center">
            <div>
              <span className="inline-flex items-center gap-2 rounded-full border border-primary-200 bg-primary-50 px-3 py-1 font-label text-xs font-medium text-primary-800">
                <span className="h-1.5 w-1.5 rounded-full bg-primary-500"></span>
                Identity verification console
              </span>
              <h1 className="mt-4 font-heading text-3xl font-semibold leading-tight tracking-tight text-foreground-950 md:text-4xl">
                Verify identities across countries, from one console
              </h1>
              <p className="mt-3 max-w-xl font-label text-sm leading-relaxed text-foreground-600 md:text-base">
                Start a verification session, capture a document and a live selfie, then
                let the pipeline run quality, OCR, MRZ, biometric and risk checks — with
                deterministic decisions and a review queue for the edge cases.
              </p>
              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                <button
                  type="button"
                  onClick={() => navigate("/verify/new")}
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-5 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
                >
                  <i className="ri-add-line text-base leading-none"></i>
                  Start a verification
                </button>
                <button
                  type="button"
                  onClick={() => navigate("/verify/sess_demo_01")}
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 bg-background-50 px-5 py-2.5 font-label text-sm font-medium text-foreground-800 transition-colors hover:bg-background-100"
                >
                  <i className="ri-play-circle-line text-base leading-none"></i>
                  Open demo session
                </button>
              </div>
            </div>

            <div className="rounded-lg border border-background-200 bg-background-50 p-5">
              <div className="flex items-center justify-between">
                <span className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">
                  Verification pipeline
                </span>
                <span className="rounded-full bg-primary-100 px-2 py-0.5 font-label text-[11px] font-medium text-primary-800">
                  live
                </span>
              </div>
              <div className="mt-4 flex flex-col gap-2.5">
                {pipelineStages.map((stage, i) => (
                  <div key={stage.label} className="flex items-center gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary-100 text-secondary-700">
                      <i className={`${stage.icon} text-base leading-none`}></i>
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="font-label text-sm font-medium text-foreground-900">{stage.label}</div>
                      <div className="font-label text-[11px] text-foreground-500">{stage.sub}</div>
                    </div>
                    {i < pipelineStages.length - 1 && (
                      <i className="ri-arrow-right-line text-base leading-none text-foreground-300"></i>
                    )}
                  </div>
                ))}
              </div>
              <div className="mt-4 flex items-center gap-2 rounded-md bg-accent-50 px-3 py-2 font-label text-xs text-accent-800">
                <i className="ri-information-line text-sm leading-none"></i>
                Engines are simulated in this sandbox.
              </div>
            </div>
          </div>
        </section>

        <section className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-4 lg:grid-cols-5">
          <StatCard label="Sessions (24h)" value="1,284" delta="+8.2% vs yesterday" trend="up" icon="ri-stack-line" tone="primary" />
          <StatCard label="Verified" value="1,102" delta="85.8% pass rate" trend="up" icon="ri-verified-badge-line" tone="primary" />
          <StatCard label="Manual review" value="96" delta="7.5% of sessions" trend="flat" icon="ri-eye-line" tone="accent" />
          <StatCard label="Rejected" value="86" delta="+1.1% vs yesterday" trend="down" icon="ri-close-circle-line" tone="accent" />
          <StatCard label="Avg. time" value="52s" delta="-6s vs yesterday" trend="up" icon="ri-timer-line" tone="secondary" />
        </section>

        <section className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <SessionsTable />
          </div>
          <SignalPanel />
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}