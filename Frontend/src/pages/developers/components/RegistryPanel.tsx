import { useEffect, useState } from "react";
import { kycApi } from "@/api/client";
import type { CountryRegistry, DocumentTypeInfo } from "@/api/types";
import { useApiKey } from "@/auth/useStaffAuth";
import { DOCUMENT_LABELS, SIDE_LABELS, countryFlag, countryName, documentLabel } from "@/lib/catalog";

const th = "px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500";

/** GET /v1/document-types and /v1/countries, as the platform reports them now. */
export default function RegistryPanel() {
  const { apiKey } = useApiKey();
  const [types, setTypes] = useState<DocumentTypeInfo[] | null>(null);
  const [registry, setRegistry] = useState<CountryRegistry | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!apiKey) return;
    let live = true;
    Promise.all([kycApi.documentTypes(apiKey), kycApi.countries(apiKey)])
      .then(([documentTypes, countries]) => {
        if (!live) return;
        setTypes(documentTypes);
        setRegistry(countries);
      })
      .catch((caught) => live && setError((caught as Error).message));
    return () => { live = false; };
  }, [apiKey]);

  if (error) return <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{error}</p>;
  if (!types || !registry) return <div className="flex items-center gap-2 py-8 font-label text-sm text-foreground-500"><i className="ri-loader-4-line animate-spin"></i>Loading…</div>;

  const needle = query.trim().toLowerCase();
  const countries = registry.countries.filter((code) => !needle || code.toLowerCase().includes(needle) || countryName(code).toLowerCase().includes(needle));

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Summary icon="ri-plug-line" label="Country adapters" value={registry.verification_adapters_available.map((code) => `${countryFlag(code)} ${countryName(code)}`).join(", ")} />
        <Summary icon="ri-earth-line" label="Any issuing country" value={registry.any_country_document_types.map(documentLabel).join(", ")} />
        <Summary icon="ri-map-pin-line" label="Accepted country codes" value={`${registry.countries.length} ISO 3166-1 codes`} />
      </div>

      <div className="overflow-hidden rounded-lg border border-background-200 bg-background-50">
        <div className="flex items-center justify-between border-b border-background-200 px-4 py-3.5 md:px-5">
          <h3 className="font-heading text-sm font-semibold text-foreground-950">Document types</h3>
          <span className="font-label text-xs text-foreground-500">{types.length} registered</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-left">
            <thead>
              <tr className="border-b border-background-200 bg-background-100/60">
                <th className={`${th} md:px-5`}>Type</th>
                <th className={th}>Country</th>
                <th className={th}>Sides</th>
                <th className={th}>Quality gate</th>
                <th className={th}>Adapter</th>
              </tr>
            </thead>
            <tbody>
              {types.map((item) => (
                <tr key={item.type} className="border-b border-background-100 last:border-0">
                  <td className="px-4 py-3 md:px-5">
                    <div className="font-label text-sm font-medium text-foreground-950">{documentLabel(item.type)}</div>
                    <div className="font-mono text-[10px] text-foreground-500">{item.type}</div>
                    {DOCUMENT_LABELS[item.type]?.description && <div className="font-label text-[11px] text-foreground-500">{DOCUMENT_LABELS[item.type].description}</div>}
                  </td>
                  <td className="px-4 py-3 font-label text-xs text-foreground-700">{item.country ? `${countryFlag(item.country)} ${item.country}` : "Any"}</td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1">
                      {item.required_sides.map((side) => (
                        <span key={side} className="rounded bg-background-200 px-1.5 py-0.5 font-label text-[10px] text-foreground-700">{SIDE_LABELS[side]}</span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 font-label text-xs text-foreground-700">{item.capture_quality_gate.toLowerCase()}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex rounded-full border px-2 py-0.5 font-label text-[10px] font-medium ${
                      item.adapter_status === "AVAILABLE" ? "border-primary-200 bg-primary-100 text-primary-900" : "border-background-300 bg-background-200 text-foreground-600"}`}>
                      {item.adapter_status.toLowerCase()}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-lg border border-background-200 bg-background-50 p-4 md:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="font-heading text-sm font-semibold text-foreground-950">Countries</h3>
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search countries"
                 className="w-full rounded-md border border-background-300 bg-background-50 px-3 py-1.5 font-label text-sm text-foreground-900 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100 sm:w-60" />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-5">
          {countries.map((code) => (
            <span key={code} className={`flex items-center gap-2 truncate rounded-md border px-2.5 py-1.5 font-label text-xs ${
              registry.verification_adapters_available.includes(code) ? "border-primary-200 bg-primary-50 text-primary-900" : "border-background-100 text-foreground-700"}`}>
              <span className="text-base leading-none">{countryFlag(code)}</span>
              <span className="truncate">{countryName(code)}</span>
              <span className="ml-auto font-mono text-[10px] text-foreground-400">{code}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function Summary({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-4">
      <div className="flex items-center gap-2 font-label text-xs text-foreground-500"><i className={`${icon} text-sm leading-none`}></i>{label}</div>
      <div className="mt-2 font-label text-sm font-medium text-foreground-900">{value}</div>
    </div>
  );
}
