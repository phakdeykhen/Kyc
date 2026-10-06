import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import StatusBadge from "@/components/base/StatusBadge";
import FieldsTable from "@/pages/verify/components/FieldsTable";
import ChecksList from "@/pages/verify/components/ChecksList";
import DecisionPanel from "@/pages/verify/components/DecisionPanel";
import { getDocumentType, statusMeta } from "@/lib/kycSimulation";
import { countries, defaultResult } from "@/mocks/kyc";
import type { KYCResult } from "@/types/kyc";

export default function VerificationResult() {
  const navigate = useNavigate();
  const location = useLocation();
  const [showJson, setShowJson] = useState(false);

  const result = useMemo<KYCResult>(() => {
    const state = (location.state || {}) as { result?: KYCResult };
    return state.result || defaultResult;
  }, [location.state]);

  const doc = getDocumentType(result.documentType);
  const flag = countries.find((c) => c.code === result.country)?.flag || "🌍";

  const identityRows = [
    { label: "Full name (Latin)", value: result.identity.fullName },
    { label: "Full name (local)", value: result.identity.fullNameLocal },
    { label: "Date of birth", value: result.identity.dateOfBirth },
    { label: "Nationality", value: result.identity.nationality },
    { label: "Sex", value: result.identity.sex },
    { label: "Address", value: result.identity.address },
  ];

  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <div className="mb-6">
          <button
            type="button"
            onClick={() => navigate("/console")}
            className="inline-flex items-center gap-1 font-label text-sm text-foreground-600 transition-colors hover:text-foreground-950"
          >
            <i className="ri-arrow-left-line text-base leading-none"></i>
            Console
          </button>
        </div>

        <section className="animate-fade-up overflow-hidden rounded-xl border border-primary-200 bg-primary-50">
          <div className="flex flex-col gap-5 p-6 md:flex-row md:items-center md:justify-between md:p-7">
            <div className="flex items-start gap-4">
              <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-primary-500 text-background-50">
                <i className="ri-verified-badge-line text-2xl leading-none"></i>
              </span>
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="font-heading text-2xl font-semibold tracking-tight text-primary-950">
                    Identity verified
                  </h1>
                  <StatusBadge meta={statusMeta(result.status)} size="sm" />
                </div>
                <p className="mt-1.5 font-label text-sm text-primary-800">
                  {result.applicant} · {doc.label} · {result.level}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-[11px] text-primary-700">
                  <span>{result.sessionId}</span>
                  <span className="hidden sm:inline">·</span>
                  <span>{result.reference}</span>
                  <span className="hidden sm:inline">·</span>
                  <span>{result.completedAt}</span>
                </div>
              </div>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <button
                type="button"
                onClick={() => navigate("/verify/new")}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
              >
                <i className="ri-add-line text-base leading-none"></i>
                Start another
              </button>
              <button
                type="button"
                onClick={() => setShowJson((v) => !v)}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-primary-300 bg-background-50 px-4 py-2.5 font-label text-sm font-medium text-primary-900 transition-colors hover:bg-primary-100"
              >
                <i className="ri-code-s-slash-line text-base leading-none"></i>
                {showJson ? "Hide JSON" : "View JSON"}
              </button>
            </div>
          </div>
        </section>

        {showJson && (
          <pre className="mt-4 max-h-[320px] overflow-auto rounded-lg border border-background-200 bg-foreground-950 p-4 font-mono text-xs leading-relaxed text-background-50">
{JSON.stringify(result, null, 2)}
          </pre>
        )}

        <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="flex flex-col gap-4 lg:col-span-2">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
                <h3 className="font-heading text-sm font-semibold text-foreground-950">Document</h3>
                <div className="mt-3 flex items-center gap-3">
                  <span className="text-2xl leading-none">{flag}</span>
                  <div>
                    <div className="font-label text-sm font-medium text-foreground-900">{doc.label}</div>
                    <div className="font-label text-xs text-foreground-500">Family · {result.document.type}</div>
                  </div>
                </div>
                <dl className="mt-4 flex flex-col gap-2.5 font-label text-sm">
                  <div className="flex items-center justify-between">
                    <dt className="text-foreground-500">Number</dt>
                    <dd className="font-mono text-xs text-foreground-900">{result.document.documentNumberMasked}</dd>
                  </div>
                  <div className="flex items-center justify-between">
                    <dt className="text-foreground-500">Expiry</dt>
                    <dd>
                      <span className="rounded-full bg-primary-100 px-2 py-0.5 font-label text-[11px] font-medium text-primary-900">
                        {result.document.expiryStatus}
                      </span>
                    </dd>
                  </div>
                  <div className="flex items-center justify-between">
                    <dt className="text-foreground-500">Adapter</dt>
                    <dd className="font-mono text-[10px] text-foreground-600">{doc.adapter}</dd>
                  </div>
                </dl>
              </div>

              <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
                <h3 className="font-heading text-sm font-semibold text-foreground-950">Identity</h3>
                <dl className="mt-3 flex flex-col gap-2.5 font-label text-sm">
                  {identityRows.map((row) => (
                    <div key={row.label} className="flex items-start justify-between gap-3">
                      <dt className="shrink-0 text-xs text-foreground-500">{row.label}</dt>
                      <dd className="text-right font-medium text-foreground-900">{row.value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </div>

            <FieldsTable fields={result.fields} />
          </div>

          <div className="flex flex-col gap-4">
            <ChecksList checks={result.checks} />
            <DecisionPanel result={result} />
          </div>
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}