import { useState } from "react";
import { documentTypes } from "@/mocks/kyc";
import { registryCountries } from "@/mocks/developers";

const statusClass: Record<string, string> = {
  GA: "bg-primary-100 text-primary-900 border-primary-200",
  BETA: "bg-accent-100 text-accent-900 border-accent-300",
  PLANNED: "bg-background-200 text-foreground-600 border-background-300",
};

export default function RegistryPanel() {
  const [scope, setScope] = useState<"ALL" | "KH" | "INTL">("ALL");

  const countries = scope === "ALL" ? registryCountries : registryCountries.filter((c) => c.code === scope);
  const types = scope === "ALL" ? documentTypes : documentTypes.filter((d) => d.country === scope);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-heading text-base font-semibold text-foreground-950">Document &amp; country registry</h2>
            <p className="mt-0.5 font-label text-sm text-foreground-600">
              Every document type resolves to an adapter that knows its fields, sides and verification route.
            </p>
          </div>
          <div className="inline-flex items-center gap-1 rounded-full border border-background-200 bg-background-100 p-1">
            {[
              { id: "ALL", label: "All" },
              { id: "KH", label: "Cambodia" },
              { id: "INTL", label: "International" },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setScope(tab.id as "ALL" | "KH" | "INTL")}
                className={`whitespace-nowrap rounded-full px-3 py-1.5 font-label text-xs font-medium transition-colors ${
                  scope === tab.id ? "bg-background-50 text-foreground-950" : "text-foreground-600 hover:text-foreground-900"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {countries.map((c) => (
          <div key={c.code} className="rounded-lg border border-background-200 bg-background-50 p-4">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2.5">
                <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-background-100 text-xl leading-none">
                  {c.flag}
                </span>
                <div>
                  <div className="font-label text-sm font-medium text-foreground-950">{c.name}</div>
                  <div className="font-mono text-[11px] text-foreground-500">{c.code}</div>
                </div>
              </div>
              <span
                className={`inline-flex items-center rounded-full border px-2 py-0.5 font-label text-[10px] font-medium ${statusClass[c.status]}`}
              >
                {c.status}
              </span>
            </div>
            <p className="mt-3 font-label text-xs leading-relaxed text-foreground-600">{c.documentTypes}</p>
            <div className="mt-3 flex items-center gap-3 border-t border-background-100 pt-3 font-label text-[11px] text-foreground-500">
              <span className="inline-flex items-center gap-1.5">
                <i className="ri-plug-line text-sm leading-none"></i>
                {c.adapters} adapters
              </span>
              <span className="inline-flex items-center gap-1.5">
                <i className="ri-translate-2 text-sm leading-none"></i>
                locale-aware
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="overflow-hidden rounded-lg border border-background-200 bg-background-50">
        <div className="flex items-center justify-between border-b border-background-200 px-4 py-3.5 md:px-5">
          <h3 className="font-heading text-sm font-semibold text-foreground-950">Document types</h3>
          <span className="font-label text-xs text-foreground-500">{types.length} registered</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] border-collapse text-left">
            <thead>
              <tr className="border-b border-background-200 bg-background-100/60">
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500 md:px-5">Type</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Family</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Adapters</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Sides</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Signals</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Locales</th>
              </tr>
            </thead>
            <tbody>
              {types.map((d) => (
                <tr key={d.id} className="border-b border-background-100 last:border-0">
                  <td className="px-4 py-3 md:px-5">
                    <div className="font-label text-sm font-medium text-foreground-950">{d.label}</div>
                    <div className="font-mono text-[10px] text-foreground-500">{d.id}</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded bg-secondary-100 px-2 py-0.5 font-label text-[11px] text-secondary-800">
                      {d.family}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-[11px] text-foreground-700">{d.adapter}</td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1">
                      {d.sides.map((s) => (
                        <span key={s} className="rounded bg-background-200 px-1.5 py-0.5 font-label text-[10px] text-foreground-700">
                          {s}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2.5 font-label text-xs">
                      <span className={d.mrz ? "text-primary-700" : "text-foreground-300"}>
                        <i className="ri-barcode-box-line text-base leading-none"></i>
                        <span className="sr-only">MRZ</span>
                      </span>
                      <span className={d.qr ? "text-primary-700" : "text-foreground-300"}>
                        <i className="ri-qr-scan-2-line text-base leading-none"></i>
                        <span className="sr-only">QR</span>
                      </span>
                      <span className={d.nfc ? "text-primary-700" : "text-foreground-300"}>
                        <i className="ri-scan-line text-base leading-none"></i>
                        <span className="sr-only">NFC</span>
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 font-label text-xs text-foreground-600">{d.locales}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex flex-col gap-2 rounded-lg border border-dashed border-background-300 bg-background-100/50 p-4 sm:flex-row sm:items-center sm:justify-between md:p-5">
        <div className="flex items-start gap-2.5">
          <i className="ri-add-circle-line mt-0.5 text-lg leading-none text-primary-600"></i>
          <div>
            <p className="font-label text-sm font-medium text-foreground-900">Need a new country or document type?</p>
            <p className="mt-0.5 font-label text-xs text-foreground-600">
              Adding an adapter keeps the KYC core untouched — request coverage and we wire it into the registry.
            </p>
          </div>
        </div>
        <button
          type="button"
          className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 bg-background-50 px-4 py-2.5 font-label text-sm font-medium text-foreground-800 transition-colors hover:bg-background-100"
        >
          <i className="ri-mail-send-line text-base leading-none"></i>
          Request coverage
        </button>
      </div>
    </div>
  );
}