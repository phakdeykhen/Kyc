import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import ApiKeyGate from "@/components/feature/ApiKeyGate";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { ClientToken, DocumentTypeCode, DocumentTypeInfo, KycSession, VerificationLevel } from "@/api/types";
import { useApiKey } from "@/auth/useStaffAuth";
import {
  DEFAULT_LEVEL, DOCUMENT_LABELS, LEVELS, SIDE_LABELS, countryFlag, countryName, documentLabel, newIdempotencyKey,
} from "@/lib/catalog";
import { applicantLink } from "@/pages/verify/applicantLink";

const card = "rounded-lg border border-background-200 bg-background-50 p-5";
const heading = "font-heading text-sm font-semibold text-foreground-950";

export default function NewVerification() {
  const navigate = useNavigate();
  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />
      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <div className="mb-6">
          <button type="button" onClick={() => navigate("/console")}
                  className="inline-flex items-center gap-1 font-label text-sm text-foreground-600 transition-colors hover:text-foreground-950">
            <i className="ri-arrow-left-line text-base leading-none"></i>
            Console
          </button>
          <h1 className="mt-3 font-heading text-2xl font-semibold tracking-tight text-foreground-950 md:text-3xl">New verification</h1>
          <p className="mt-1.5 max-w-2xl font-label text-sm text-foreground-600">
            Choose the document and checks you need, then share a secure link.
            The applicant gives consent and completes verification on their own device.
          </p>
        </div>
        <ApiKeyGate purpose="create verification sessions" scopes={["sessions:write"]}>
          <NewSessionForm />
        </ApiKeyGate>
      </main>
      <SiteFooter />
    </div>
  );
}

function NewSessionForm() {
  const { apiKey } = useApiKey();
  const [types, setTypes] = useState<DocumentTypeInfo[] | null>(null);
  const [countries, setCountries] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");
  const [country, setCountry] = useState("KH");
  const [documentType, setDocumentType] = useState<DocumentTypeCode>("KH_NATIONAL_ID");
  const [level, setLevel] = useState<VerificationLevel>(DEFAULT_LEVEL);
  const [userId, setUserId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // One key per form fill: a retry after a timeout replays the same session instead of creating two.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [created, setCreated] = useState<{ session: KycSession; token: ClientToken } | null>(null);

  useEffect(() => {
    if (!apiKey) return;
    let live = true;
    Promise.all([kycApi.documentTypes(apiKey), kycApi.countries(apiKey)])
      .then(([documentTypes, registry]) => {
        if (!live) return;
        setTypes(documentTypes.filter((item) => item.type !== "UNKNOWN"));
        setCountries(["KH", ...registry.countries.filter((code) => code !== "KH")]);
      })
      .catch((caught) => live && setLoadError((caught as Error).message));
    return () => { live = false; };
  }, [apiKey]);

  // KH_* types require country KH; the other types work for any issuing country.
  const available = useMemo(() => (types ?? []).filter((item) => country === "KH" || !item.type.startsWith("KH_")), [types, country]);
  const selected = available.find((item) => item.type === documentType) ?? available[0];
  const isPassport = selected?.required_sides.includes("DATA_PAGE") ?? false;

  const changeCountry = (code: string) => {
    setCountry(code);
    if (code !== "KH" && documentType.startsWith("KH_")) setDocumentType(documentType === "KH_PASSPORT" ? "PASSPORT" : "NATIONAL_ID");
  };

  const touch = () => {
    setError("");
    setIdempotencyKey(newIdempotencyKey());
  };

  const start = async () => {
    if (!apiKey || !selected) return;
    if (!userId.trim()) {
      setError("Enter your reference for this person (user ID), e.g. your customer number.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const session = await kycApi.createSession(apiKey, {
        user_id: userId.trim(), country, expected_document_type: selected.type, verification_level: level,
      }, idempotencyKey);
      const token = await kycApi.clientToken(apiKey, session.session_id);
      setCreated({ session, token });
    } catch (caught) {
      const failure = caught as ApiError;
      setError(failure.status === 409 ? "This request was already sent with different details. Change a field and try again."
        : failure.message);
    } finally {
      setBusy(false);
    }
  };

  if (created && apiKey) {
    return <SessionCreated session={created.session} token={created.token} organizationId={apiKey.organizationId}
                           onAnother={() => { setCreated(null); setUserId(""); touch(); }} />;
  }
  if (loadError) return <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{loadError}</p>;
  if (!types) {
    return (
      <div className="flex items-center gap-2 py-10 font-label text-sm text-foreground-500">
        <i className="ri-loader-4-line animate-spin text-base leading-none"></i>Loading document types…
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_340px]">
      <div className="flex flex-col gap-6">
        <section className={card}>
          <h2 className={heading}>1. Issuing country</h2>
          <div className="mt-3.5 flex flex-col gap-3 sm:flex-row sm:items-center">
            <span className="text-3xl leading-none">{countryFlag(country)}</span>
            <select value={country} onChange={(e) => { changeCountry(e.target.value); touch(); }}
                    className="w-full rounded-md border border-background-300 bg-background-50 px-3 py-2.5 font-label text-sm text-foreground-900 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100 sm:max-w-sm">
              {countries.map((code) => <option key={code} value={code}>{countryName(code)} ({code})</option>)}
            </select>
          </div>
          <p className="mt-2 font-label text-xs text-foreground-500">
            Cambodian documents have dedicated adapters. Passports from any country are read from their MRZ.
          </p>
        </section>

        <section className={card}>
          <h2 className={heading}>2. Document type</h2>
          <div className="mt-3.5 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {available.map((item) => {
              const active = selected?.type === item.type;
              return (
                <button key={item.type} type="button" onClick={() => { setDocumentType(item.type); touch(); }}
                        className={`rounded-lg border p-4 text-left transition-colors ${active ? "border-primary-400 bg-primary-50" : "border-background-200 hover:bg-background-100"}`}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-label text-sm font-medium text-foreground-950">{documentLabel(item.type)}</div>
                      <div className="mt-0.5 font-label text-xs text-foreground-500">{DOCUMENT_LABELS[item.type]?.description}</div>
                    </div>
                    <i className={`mt-0.5 shrink-0 text-lg leading-none ${active ? "ri-checkbox-circle-fill text-primary-600" : "ri-checkbox-blank-circle-line text-foreground-300"}`}></i>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    <span className="rounded bg-secondary-100 px-2 py-0.5 font-mono text-[10px] text-secondary-700">{item.type}</span>
                    {item.required_sides.map((side) => (
                      <span key={side} className="rounded bg-background-100 px-2 py-0.5 font-label text-[10px] text-foreground-600">{SIDE_LABELS[side]}</span>
                    ))}
                    <span className={`rounded px-2 py-0.5 font-label text-[10px] ${item.adapter_status === "AVAILABLE" ? "bg-primary-100 text-primary-800" : "bg-accent-100 text-accent-800"}`}>
                      adapter {item.adapter_status.toLowerCase()}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section className={card}>
          <h2 className={heading}>3. Verification level</h2>
          <div className="mt-3.5 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {LEVELS.map((item) => (
              <button key={item.id} type="button" onClick={() => { setLevel(item.id); touch(); }}
                      className={`rounded-lg border p-3.5 text-left transition-colors ${level === item.id ? "border-primary-400 bg-primary-50" : "border-background-200 hover:bg-background-100"}`}>
                <div className="font-label text-sm font-medium text-foreground-950">{item.label}</div>
                <div className="mt-1 font-mono text-[10px] text-foreground-500">{item.id}</div>
                <div className="mt-1.5 font-label text-xs leading-snug text-foreground-600">{item.steps}</div>
                <div className="mt-0.5 font-label text-xs leading-snug text-foreground-500">{item.checks}</div>
              </button>
            ))}
          </div>
          {level === "DOCUMENT_FACE_LIVENESS_NFC" && !isPassport && (
            <p className="mt-3 font-label text-xs text-accent-700">The chip step applies to ePassports. Pick a passport type, or a lower level.</p>
          )}
          {level === "DOCUMENT_FACE_LIVENESS_NFC" && (
            <p className="mt-2 font-label text-xs text-foreground-500">
              Browsers cannot read passport chips: the person needs the mobile app for that step.
            </p>
          )}
        </section>

        <section className={card}>
          <h2 className={heading}>4. Your reference for the person</h2>
          <label htmlFor="user-id" className="mt-3.5 block font-label text-xs font-medium text-foreground-700">User ID</label>
          <input id="user-id" type="text" value={userId} maxLength={128}
                 onChange={(e) => { setUserId(e.target.value); touch(); }}
                 placeholder="e.g. customer-20481"
                 className="mt-1.5 w-full rounded-md border border-background-300 bg-background-50 px-3 py-2.5 font-mono text-sm text-foreground-900 outline-none transition-colors placeholder:text-foreground-400 focus:border-primary-400 focus:ring-2 focus:ring-primary-100" />
          <p className="mt-2 font-label text-xs text-foreground-500">
            Use an opaque ID from your system, not a name. Reviewers can search by it. Consent is collected from the person on their own device before capture.
          </p>
        </section>
      </div>

      <aside className="lg:sticky lg:top-24 lg:self-start">
        <div className="rounded-lg border border-background-200 bg-background-100/60 p-5">
          <h3 className={heading}>Session summary</h3>
          <dl className="mt-4 flex flex-col gap-3 font-label text-sm">
            <Row label="Country" value={`${countryFlag(country)} ${countryName(country)}`} />
            <Row label="Document" value={selected ? documentLabel(selected.type) : "—"} />
            <Row label="Sides" value={selected ? selected.required_sides.map((side) => SIDE_LABELS[side]).join(" + ") : "—"} />
            <Row label="Level" value={LEVELS.find((item) => item.id === level)?.label ?? level} />
            <Row label="User ID" value={userId.trim() || "—"} mono />
          </dl>
          {error && <p role="alert" className="mt-4 rounded-md border border-accent-200 bg-accent-50 px-3 py-2 font-label text-xs text-accent-800">{error}</p>}
          <button type="button" disabled={busy || !selected} onClick={start}
                  className="mt-5 inline-flex w-full items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-60">
            <i className={`${busy ? "ri-loader-4-line animate-spin" : "ri-arrow-right-circle-line"} text-base leading-none`}></i>
            {busy ? "Creating…" : "Create session"}
          </button>
          <p className="mt-2 text-center font-label text-[11px] text-foreground-500">Sessions expire if not completed in time.</p>
        </div>
      </aside>
    </div>
  );
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="shrink-0 text-foreground-500">{label}</dt>
      <dd className={`min-w-0 break-words text-right font-medium text-foreground-900 ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}

function SessionCreated({ session, token, organizationId, onAnother }:
  { session: KycSession; token: ClientToken; organizationId: string; onAnother: () => void }) {
  const link = applicantLink(session.session_id, organizationId, token.client_token);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-xl border border-primary-200 bg-primary-50 p-6">
        <div className="flex items-start gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary-500 text-background-50">
            <i className="ri-check-line text-xl leading-none"></i>
          </span>
          <div className="min-w-0">
            <h2 className="font-heading text-lg font-semibold text-primary-950">Session created</h2>
            <p className="mt-1 font-label text-sm text-primary-800">
              {documentLabel(session.expected_document_type)} · {session.country} · {session.verification_level} · expires{" "}
              {new Date(session.expires_at).toLocaleTimeString()}
            </p>
            <p className="mt-1 break-all font-mono text-[11px] text-primary-700">{session.session_id}</p>
          </div>
        </div>
      </section>

      <section className={card}>
        <h3 className={heading}>Send this link to the person</h3>
        <p className="mt-1 font-label text-sm text-foreground-600">
          It carries a client token that works for this session only, and only until it expires. Issuing a new link revokes this one.
        </p>
        <div className="mt-3 flex items-center gap-2 rounded-md border border-background-200 bg-background-100 p-3">
          <code className="min-w-0 flex-1 truncate font-mono text-xs text-foreground-900">{link}</code>
          <button type="button" onClick={copy}
                  className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-background-300 bg-background-50 px-2.5 py-1.5 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100">
            <i className={`${copied ? "ri-check-line" : "ri-file-copy-line"} text-sm leading-none`}></i>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          <a href={link} target="_blank" rel="noreferrer"
             className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600">
            <i className="ri-external-link-line text-base leading-none"></i>
            Open capture on this device
          </a>
          <Link to={`/sessions/${session.session_id}`}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm font-medium text-foreground-800 transition-colors hover:bg-background-100">
            <i className="ri-file-list-3-line text-base leading-none"></i>
            Follow this session
          </Link>
          <button type="button" onClick={onAnother}
                  className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm font-medium text-foreground-800 transition-colors hover:bg-background-100">
            <i className="ri-add-line text-base leading-none"></i>
            Create another
          </button>
        </div>
      </section>
    </div>
  );
}
