import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import { countries, documentTypes } from "@/mocks/kyc";
import type { VerificationLevel } from "@/types/kyc";

const levels: { id: VerificationLevel; label: string; desc: string }[] = [
  { id: "BASIC", label: "Basic", desc: "Document quality + OCR only" },
  { id: "STANDARD", label: "Standard", desc: "Document + face match + liveness" },
  { id: "ENHANCED", label: "Enhanced", desc: "Adds fraud signals, MRZ/NFC cross-check" },
];

function makeSessionId(): string {
  return `sess_${Math.random().toString(36).slice(2, 10)}`;
}

export default function NewVerification() {
  const navigate = useNavigate();
  const [country, setCountry] = useState("KH");
  const [documentTypeId, setDocumentTypeId] = useState("KH_NATIONAL_ID");
  const [level, setLevel] = useState<VerificationLevel>("STANDARD");
  const [reference, setReference] = useState("");
  const [consent, setConsent] = useState(false);

  const countryDocTypes = useMemo(
    () => documentTypes.filter((d) => d.country === country),
    [country],
  );

  const selected = useMemo(
    () => documentTypes.find((d) => d.id === documentTypeId) || countryDocTypes[0],
    [documentTypeId, countryDocTypes],
  );

  const handleCountry = (code: string) => {
    setCountry(code);
    const first = documentTypes.find((d) => d.country === code);
    if (first) setDocumentTypeId(first.id);
  };

  const canStart = Boolean(selected) && consent;

  const startSession = () => {
    if (!canStart || !selected) return;
    const sessionId = makeSessionId();
    navigate(`/verify/${sessionId}`, {
      state: {
        sessionId,
        country,
        documentTypeId: selected.id,
        level,
        reference: reference.trim(),
        applicant: reference.trim() || "Unnamed Applicant",
      },
    });
  };

  const capChips = [
    { on: selected?.mrz, label: "MRZ", icon: "ri-barcode-box-line" },
    { on: selected?.qr, label: "QR / barcode", icon: "ri-qr-scan-2-line" },
    { on: selected?.nfc, label: "NFC chip", icon: "ri-scan-line" },
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
          <h1 className="mt-3 font-heading text-2xl font-semibold tracking-tight text-foreground-950 md:text-3xl">
            New verification
          </h1>
          <p className="mt-1.5 font-label text-sm text-foreground-600">
            Choose the issuing country and document type, then set the verification level.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_340px]">
          <div className="flex flex-col gap-6">
            {/* Country */}
            <section className="rounded-lg border border-background-200 bg-background-50 p-5">
              <h2 className="font-heading text-sm font-semibold text-foreground-950">1. Issuing country</h2>
              <div className="mt-3.5 grid grid-cols-1 gap-3 sm:grid-cols-2">
                {countries.map((c) => (
                  <button
                    key={c.code}
                    type="button"
                    onClick={() => handleCountry(c.code)}
                    className={`flex items-center gap-3 rounded-lg border p-3.5 text-left transition-colors ${
                      country === c.code
                        ? "border-primary-400 bg-primary-50"
                        : "border-background-200 hover:bg-background-100"
                    }`}
                  >
                    <span className="text-2xl leading-none">{c.flag}</span>
                    <span className="min-w-0">
                      <span className="block font-label text-sm font-medium text-foreground-950">{c.name}</span>
                      <span className="block font-label text-xs text-foreground-500">{c.region}</span>
                    </span>
                    {country === c.code && (
                      <i className="ri-checkbox-circle-fill ml-auto text-lg leading-none text-primary-600"></i>
                    )}
                  </button>
                ))}
              </div>
            </section>

            {/* Document type */}
            <section className="rounded-lg border border-background-200 bg-background-50 p-5">
              <h2 className="font-heading text-sm font-semibold text-foreground-950">2. Document type</h2>
              <div className="mt-3.5 flex flex-col gap-3">
                {countryDocTypes.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() => setDocumentTypeId(d.id)}
                    className={`rounded-lg border p-4 text-left transition-colors ${
                      documentTypeId === d.id
                        ? "border-primary-400 bg-primary-50"
                        : "border-background-200 hover:bg-background-100"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-label text-sm font-medium text-foreground-950">{d.label}</div>
                        <div className="mt-0.5 font-label text-xs text-foreground-500">{d.description}</div>
                      </div>
                      <i
                        className={`mt-0.5 shrink-0 text-lg leading-none ${
                          documentTypeId === d.id ? "ri-checkbox-circle-fill text-primary-600" : "ri-checkbox-blank-circle-line text-foreground-300"
                        }`}
                      ></i>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-1.5">
                      <span className="rounded bg-secondary-100 px-2 py-0.5 font-mono text-[10px] text-secondary-700">
                        {d.adapter}
                      </span>
                      <span className="rounded bg-background-100 px-2 py-0.5 font-label text-[10px] text-foreground-600">
                        {d.sides.length} side{d.sides.length > 1 ? "s" : ""}
                      </span>
                      <span className="rounded bg-background-100 px-2 py-0.5 font-label text-[10px] text-foreground-600">
                        {d.locales}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </section>

            {/* Level */}
            <section className="rounded-lg border border-background-200 bg-background-50 p-5">
              <h2 className="font-heading text-sm font-semibold text-foreground-950">3. Verification level</h2>
              <div className="mt-3.5 grid grid-cols-1 gap-3 sm:grid-cols-3">
                {levels.map((l) => (
                  <button
                    key={l.id}
                    type="button"
                    onClick={() => setLevel(l.id)}
                    className={`rounded-lg border p-3.5 text-left transition-colors ${
                      level === l.id ? "border-primary-400 bg-primary-50" : "border-background-200 hover:bg-background-100"
                    }`}
                  >
                    <div className="font-label text-sm font-medium text-foreground-950">{l.label}</div>
                    <div className="mt-1 font-label text-xs leading-snug text-foreground-500">{l.desc}</div>
                  </button>
                ))}
              </div>
            </section>

            {/* Applicant + consent */}
            <section className="rounded-lg border border-background-200 bg-background-50 p-5">
              <h2 className="font-heading text-sm font-semibold text-foreground-950">4. Applicant &amp; consent</h2>
              <label className="mt-3.5 block font-label text-xs font-medium text-foreground-700">
                Applicant reference <span className="font-normal text-foreground-400">(optional)</span>
              </label>
              <input
                type="text"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder="e.g. Sok Chanthy / APP-20481"
                className="mt-1.5 w-full rounded-md border border-background-300 bg-background-50 px-3 py-2.5 text-sm text-foreground-900 outline-none transition-colors placeholder:text-foreground-400 focus:border-primary-400 focus:ring-2 focus:ring-primary-200"
              />

              <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-md border border-background-200 bg-background-100/60 p-3.5">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[oklch(var(--primary-500))]"
                />
                <span className="font-label text-xs leading-relaxed text-foreground-600">
                  The applicant consents to identity verification, including document capture,
                  biometric face processing and liveness checks. Biometric templates are stored
                  encrypted with a configurable retention period and are never returned via the API.
                </span>
              </label>
            </section>
          </div>

          {/* Summary */}
          <aside className="lg:sticky lg:top-24 lg:self-start">
            <div className="rounded-lg border border-background-200 bg-background-100/60 p-5">
              <h3 className="font-heading text-sm font-semibold text-foreground-950">Session summary</h3>
              <dl className="mt-4 flex flex-col gap-3 font-label text-sm">
                <div className="flex items-start justify-between gap-3">
                  <dt className="text-foreground-500">Country</dt>
                  <dd className="text-right font-medium text-foreground-900">
                    {countries.find((c) => c.code === country)?.name}
                  </dd>
                </div>
                <div className="flex items-start justify-between gap-3">
                  <dt className="text-foreground-500">Document</dt>
                  <dd className="text-right font-medium text-foreground-900">{selected?.label}</dd>
                </div>
                <div className="flex items-start justify-between gap-3">
                  <dt className="text-foreground-500">Level</dt>
                  <dd className="text-right font-medium text-foreground-900">{level}</dd>
                </div>
                <div className="flex items-start justify-between gap-3">
                  <dt className="text-foreground-500">Sides to capture</dt>
                  <dd className="text-right font-medium text-foreground-900">{selected?.sides.length}</dd>
                </div>
              </dl>

              <div className="mt-4 border-t border-background-200 pt-4">
                <div className="font-label text-xs font-medium uppercase tracking-wide text-foreground-500">
                  Checks applied
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {capChips.map((chip) => (
                    <span
                      key={chip.label}
                      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 font-label text-[11px] ${
                        chip.on
                          ? "border-primary-200 bg-primary-50 text-primary-800"
                          : "border-background-200 bg-background-50 text-foreground-400"
                      }`}
                    >
                      <i className={`${chip.icon} text-xs leading-none`}></i>
                      {chip.label}
                    </span>
                  ))}
                  <span className="inline-flex items-center gap-1 rounded-full border border-primary-200 bg-primary-50 px-2.5 py-1 font-label text-[11px] text-primary-800">
                    <i className="ri-user-follow-line text-xs leading-none"></i>
                    Face match
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-full border border-primary-200 bg-primary-50 px-2.5 py-1 font-label text-[11px] text-primary-800">
                    <i className="ri-shield-user-line text-xs leading-none"></i>
                    Liveness
                  </span>
                </div>
              </div>

              <button
                type="button"
                disabled={!canStart}
                onClick={startSession}
                className={`mt-5 inline-flex w-full items-center justify-center gap-2 whitespace-nowrap rounded-md px-4 py-2.5 font-label text-sm font-medium transition-colors ${
                  canStart
                    ? "bg-primary-500 text-background-50 hover:bg-primary-600"
                    : "cursor-not-allowed bg-background-300 text-foreground-400"
                }`}
              >
                <i className="ri-arrow-right-circle-line text-base leading-none"></i>
                Start verification session
              </button>
              {!consent && (
                <p className="mt-2 text-center font-label text-[11px] text-foreground-500">
                  Applicant consent is required to start.
                </p>
              )}
            </div>
          </aside>
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}