import { useState, type FormEvent, type ReactNode } from "react";
import { ApiError } from "@/api/http";
import { useApiKey, useStaffAuth } from "@/auth/useStaffAuth";

interface ApiKeyGateProps {
  /** What the page needs the key for, e.g. "create verification sessions". */
  purpose: string;
  /** Scopes the page needs; a connected key lacking all of them is reported. */
  scopes?: string[];
  children: ReactNode;
}

/**
 * Renders children once an API key is connected; otherwise a form to connect one.
 *
 * The key is kept in this tab's memory with the reviewer token, never in storage. In production your
 * own server holds the key and creates sessions; this console is the operator's view of that same API.
 */
export default function ApiKeyGate({ purpose, scopes = [], children }: ApiKeyGateProps) {
  const { apiKey, organization, has } = useApiKey();
  if (apiKey && organization) {
    const missing = scopes.length > 0 && !scopes.some(has);
    if (missing) {
      return (
        <div className="rounded-lg border border-accent-200 bg-accent-50 p-5 font-label text-sm text-accent-900">
          <p className="font-medium">The connected API key cannot {purpose}.</p>
          <p className="mt-1 text-accent-800">It needs one of these scopes: <code className="font-mono">{scopes.join(", ")}</code>.
            Connect a key that holds it.</p>
          <div className="mt-4"><ConnectForm compact /></div>
        </div>
      );
    }
    return <>{children}</>;
  }
  return (
    <div className="rounded-lg border border-background-200 bg-background-50 p-5 md:p-6">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-100 text-primary-700">
          <i className="ri-key-2-line text-lg leading-none"></i>
        </span>
        <div className="min-w-0">
          <h2 className="font-heading text-base font-semibold text-foreground-950">Connect an API key to {purpose}</h2>
          <p className="mt-1 max-w-2xl font-label text-sm text-foreground-600">
            Reviewer tokens only reach the review endpoints. Sessions, keys and webhooks use an organization API key
            (<code className="font-mono">kyc_…</code>, from <code className="font-mono">scripts/manage_tenants.py</code> or the
            API keys tab). It stays in this tab's memory and is forgotten on reload.
          </p>
        </div>
      </div>
      <div className="mt-5"><ConnectForm /></div>
    </div>
  );
}

function ConnectForm({ compact = false }: { compact?: boolean }) {
  const { connectApiKey, reviewer } = useStaffAuth();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    setError("");
    try {
      await connectApiKey(key);
      setKey("");
    } catch (caught) {
      const failure = caught as ApiError;
      setError(failure.status === 401 ? "That key is not valid for this organization, or it was revoked or expired."
        : failure.status === 403 ? failure.message : failure.message || "Could not connect the key.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} autoComplete="off" className={compact ? "" : "max-w-xl"}>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input type="password" value={key} onChange={(e) => setKey(e.target.value)} spellCheck={false}
               placeholder="kyc_…" aria-label="API key"
               className="min-w-0 flex-1 rounded-md border border-background-300 bg-background-50 px-3 py-2.5 font-mono text-sm text-foreground-900 placeholder:text-foreground-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100" />
        <button type="submit" disabled={busy || !key.trim()}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-60">
          <i className={`${busy ? "ri-loader-4-line animate-spin" : "ri-link"} text-base leading-none`}></i>
          {busy ? "Checking…" : "Connect key"}
        </button>
      </div>
      {!compact && reviewer && (
        <p className="mt-2 font-label text-xs text-foreground-500">
          Organization <span className="font-mono">{reviewer.organizationId}</span> (from your sign-in).
        </p>
      )}
      {error && <p role="alert" className="mt-2 font-label text-sm text-accent-700">{error}</p>}
    </form>
  );
}
