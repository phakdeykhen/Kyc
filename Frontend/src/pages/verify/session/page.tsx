import { useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import StatusBadge from "@/components/base/StatusBadge";
import Stepper, { type FlowStep } from "@/pages/verify/components/Stepper";
import CaptureStage from "@/pages/verify/components/CaptureStage";
import SelfieStage from "@/pages/verify/components/SelfieStage";
import LivenessStage from "@/pages/verify/components/LivenessStage";
import ProcessingStage from "@/pages/verify/components/ProcessingStage";
import { buildResult, getDocumentType, statusMeta } from "@/lib/kycSimulation";
import type { DocumentSide, KYCStatus, VerificationLevel } from "@/types/kyc";

interface SetupState {
  sessionId: string;
  country: string;
  documentTypeId: string;
  level: VerificationLevel;
  reference: string;
  applicant: string;
}

interface InternalStep extends FlowStep {
  type: "document" | "selfie" | "liveness" | "processing";
  side?: DocumentSide;
}

export default function VerificationSession() {
  const navigate = useNavigate();
  const { sessionId = "sess_unknown" } = useParams();
  const location = useLocation();

  const setup = useMemo<SetupState>(() => {
    const state = (location.state || {}) as Partial<SetupState>;
    return {
      sessionId,
      country: state.country || "KH",
      documentTypeId: state.documentTypeId || "KH_NATIONAL_ID",
      level: state.level || "STANDARD",
      reference: state.reference || "APP-20481",
      applicant: state.applicant || "Sok Chanthy",
    };
  }, [location.state, sessionId]);

  const doc = getDocumentType(setup.documentTypeId);

  const steps = useMemo<InternalStep[]>(() => {
    const list: InternalStep[] = doc.sides.map((side) => ({
      key: `doc_${side}`,
      label: side === "FRONT" ? "Document front" : "Document back",
      icon: "ri-id-card-line",
      type: "document",
      side,
    }));
    list.push({ key: "selfie", label: "Selfie", icon: "ri-user-smile-line", type: "selfie" });
    list.push({ key: "liveness", label: "Liveness", icon: "ri-shield-user-line", type: "liveness" });
    list.push({ key: "processing", label: "Processing", icon: "ri-cpu-line", type: "processing" });
    return list;
  }, [doc.sides]);

  const [current, setCurrent] = useState(0);
  const step = steps[current];

  const statusForStep = (): KYCStatus => {
    if (!step) return "PROCESSING";
    if (step.type === "document") return "DOCUMENT_REQUIRED";
    if (step.type === "selfie") return "SELFIE_REQUIRED";
    if (step.type === "liveness") return "LIVENESS_REQUIRED";
    return "PROCESSING";
  };

  const advance = () => setCurrent((c) => Math.min(c + 1, steps.length - 1));

  const goBack = () => {
    if (current === 0) {
      navigate("/verify/new");
    } else {
      setCurrent((c) => Math.max(0, c - 1));
    }
  };

  const finish = () => {
    const result = buildResult({
      sessionId: setup.sessionId,
      reference: setup.reference,
      applicant: setup.applicant,
      country: setup.country,
      documentTypeId: setup.documentTypeId,
      level: setup.level,
    });
    navigate(`/verify/${sessionId}/done`, { state: { result } });
  };

  const infoRows = [
    { label: "Session", value: setup.sessionId, mono: true },
    { label: "Applicant", value: setup.applicant },
    { label: "Country", value: setup.country },
    { label: "Document", value: doc.label },
    { label: "Level", value: setup.level },
    { label: "Expires", value: "in 29 min" },
  ];

  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <button
              type="button"
              onClick={() => navigate("/console")}
              className="inline-flex items-center gap-1 font-label text-sm text-foreground-600 transition-colors hover:text-foreground-950"
            >
              <i className="ri-close-line text-base leading-none"></i>
              Cancel session
            </button>
            <h1 className="mt-3 font-heading text-2xl font-semibold tracking-tight text-foreground-950 md:text-3xl">
              Identity verification
            </h1>
          </div>
          <StatusBadge meta={statusMeta(statusForStep())} />
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
          <div>
            <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
              <Stepper steps={steps} current={current} />
            </div>

            <div className="mt-4 rounded-lg border border-background-200 bg-background-50 p-5 md:p-6">
              {step.type === "document" && step.side && (
                <CaptureStage documentType={doc} side={step.side} onComplete={advance} onBack={goBack} />
              )}
              {step.type === "selfie" && <SelfieStage onComplete={advance} onBack={goBack} />}
              {step.type === "liveness" && <LivenessStage onComplete={advance} onBack={goBack} />}
              {step.type === "processing" && <ProcessingStage onDone={finish} />}
            </div>
          </div>

          <aside className="lg:sticky lg:top-24 lg:self-start">
            <div className="rounded-lg border border-background-200 bg-background-100/60 p-5">
              <h3 className="font-heading text-sm font-semibold text-foreground-950">Session details</h3>
              <dl className="mt-4 flex flex-col gap-3 font-label text-sm">
                {infoRows.map((row) => (
                  <div key={row.label} className="flex items-start justify-between gap-3">
                    <dt className="shrink-0 text-foreground-500">{row.label}</dt>
                    <dd className={`text-right font-medium text-foreground-900 ${row.mono ? "font-mono text-xs" : ""}`}>
                      {row.value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="mt-4 rounded-lg border border-primary-200 bg-primary-50 p-5">
              <div className="flex items-center gap-2">
                <i className="ri-lock-2-line text-base leading-none text-primary-700"></i>
                <h3 className="font-heading text-sm font-semibold text-primary-900">Privacy &amp; retention</h3>
              </div>
              <ul className="mt-3 flex flex-col gap-2 font-label text-xs leading-relaxed text-primary-800">
                <li className="flex gap-2">
                  <i className="ri-check-line mt-0.5 leading-none"></i>
                  Captures stored encrypted with short-lived signed URLs.
                </li>
                <li className="flex gap-2">
                  <i className="ri-check-line mt-0.5 leading-none"></i>
                  Biometric templates never returned via the API.
                </li>
                <li className="flex gap-2">
                  <i className="ri-check-line mt-0.5 leading-none"></i>
                  Configurable retention + automatic deletion per tenant.
                </li>
              </ul>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}