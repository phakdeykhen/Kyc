import type { GovernmentVerification } from "@/api/types";

const STATES: Record<GovernmentVerification["status"], { label: string; text: string }> = {
  PENDING: { label: "Waiting for scan", text: "We’re waiting for your document scan to be read." },
  LINK_AVAILABLE: { label: "Not checked", text: "Your scan contains a government verification link. Open the official record and compare its details with your document." },
  LINK_RESTRICTED: { label: "Not checked", text: "An official government QR was found. This account cannot open the private record." },
  NO_OFFICIAL_QR: { label: "Unavailable", text: "Government verification needs an official QR code. No Verify.gov.kh link was found in this scan." },
  UNAVAILABLE: { label: "Unavailable", text: "The government verification link could not be read. Try scanning a clearer photo if your document has an official QR code." },
  LINK_EXPIRED: { label: "Link unavailable", text: "Your stored scan has expired. Scan your document again to open its government record." },
  ERASED: { label: "Removed", text: "The document data and its government verification link have been removed." },
};

function trustedRecordLink(data: GovernmentVerification | null | undefined): string | null {
  if (data?.status !== "LINK_AVAILABLE" || !data.verification_url) return null;
  try {
    const url = new URL(data.verification_url);
    return url.origin === "https://verify.gov.kh" && url.pathname.startsWith("/verify/") && !url.username && !url.password
      ? url.href : null;
  } catch {
    return null;
  }
}

export default function GovernmentVerificationPanel({ data, loading = false, error = false, onRetry }:
  { data?: GovernmentVerification | null; loading?: boolean; error?: boolean; onRetry?: () => void }) {
  if (!data && !loading && !error) return null;
  const state = loading ? { label: "Loading", text: "Checking for an official government verification link…" }
    : error ? { label: "Unavailable", text: "We couldn’t load government verification. Please try again." }
    : STATES[data.status] ?? STATES.UNAVAILABLE;
  const link = trustedRecordLink(data);
  return (
    <section className="rounded-xl border border-background-200 bg-background-50 p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-secondary-100 text-secondary-700">
            <i className="ri-government-line text-xl leading-none" aria-hidden="true"></i>
          </span>
          <div>
            <h2 className="font-heading text-base font-semibold text-foreground-950">Government verification</h2>
            <p className="mt-0.5 font-label text-xs text-foreground-500">Cambodia · Verify.gov.kh</p>
          </div>
        </div>
        <span aria-live="polite" className="shrink-0 rounded-full bg-background-100 px-2.5 py-1 font-label text-xs font-medium text-foreground-600">
          {state.label}
        </span>
      </div>
      <p className="mt-3 font-label text-sm text-foreground-600">{state.text}</p>
      {link && !loading && !error && (
        <a href={link} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"
           className="mt-4 inline-flex items-center gap-2 rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600">
          <i className="ri-external-link-line text-base leading-none" aria-hidden="true"></i>
          Open government verification
          <span className="sr-only">(opens a new tab)</span>
        </a>
      )}
      {error && onRetry && (
        <button type="button" onClick={onRetry} className="mt-3 font-label text-sm font-medium text-primary-700 hover:underline">
          Try again
        </button>
      )}
      {!loading && !error && data?.status !== "ERASED" && (
        <p className="mt-3 font-label text-xs text-foreground-500">
          An official verification result has not been received.
          {!link && <a href="https://verify.gov.kh/" target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"
                       className="ml-1 text-primary-700 hover:underline">Visit the official website<span className="sr-only"> (opens a new tab)</span>.</a>}
        </p>
      )}
    </section>
  );
}
