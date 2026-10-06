import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "@/api/http";
import { useStaffAuth } from "@/auth/useStaffAuth";

const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

function safeNext(value: string | null): string {
  // Only same-app paths; never an absolute or protocol-relative URL.
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/console";
}

export default function SignInPage() {
  const { reviewer, signIn } = useStaffAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const [organizationId, setOrganizationId] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (reviewer) return <Navigate to={next} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!UUID.test(organizationId.trim())) {
      setError("Enter the full organization ID (a 36-character UUID).");
      return;
    }
    if (!token.trim().startsWith("rvw_")) {
      setError("Paste only the reviewer token: it starts with rvw_.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await signIn(organizationId, token);
      setToken("");
      navigate(next, { replace: true });
    } catch (caught) {
      const failure = caught as ApiError;
      setError(failure.status === 401 ? "That token is not valid for this organization. It may be mistyped, expired or rotated."
        : failure.status === 403 ? failure.message : failure.message || "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  const input = "w-full rounded-md border border-background-300 bg-background-50 px-3 py-2.5 font-label text-sm text-foreground-900 placeholder:text-foreground-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100";

  return (
    <div className="flex min-h-screen items-center justify-center bg-background-100/60 px-4 py-10">
      <form onSubmit={submit} className="w-full max-w-md rounded-xl border border-background-200 bg-background-50 p-6 shadow-sm md:p-8" autoComplete="off">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-500 text-background-50">
            <i className="ri-fingerprint-2-line text-xl leading-none"></i>
          </span>
          <span className="font-heading text-lg font-semibold text-foreground-950">Verix console</span>
        </div>
        <h1 className="mt-6 font-heading text-xl font-semibold text-foreground-950">Sign in as a reviewer</h1>
        <p className="mt-1.5 font-label text-sm text-foreground-600">
          Your token stays in this tab's memory only and is never saved. Reloading or closing the tab signs you out.
        </p>

        <label className="mt-6 block font-label text-sm font-medium text-foreground-800" htmlFor="organization-id">Organization ID</label>
        <input id="organization-id" className={`${input} mt-1.5 font-mono`} value={organizationId} spellCheck={false}
               onChange={(e) => setOrganizationId(e.target.value)} placeholder="00000000-0000-0000-0000-000000000000" required />

        <label className="mt-4 block font-label text-sm font-medium text-foreground-800" htmlFor="reviewer-token">Reviewer token</label>
        <input id="reviewer-token" type="password" className={`${input} mt-1.5 font-mono`} value={token} spellCheck={false}
               onChange={(e) => setToken(e.target.value)} placeholder="rvw_…" required />
        <p className="mt-1.5 font-label text-xs text-foreground-500">
          Issued with <code className="font-mono">scripts/create_reviewer.py</code>; shown once when created or rotated.
        </p>

        {error && (
          <p role="alert" className="mt-4 rounded-md border border-accent-200 bg-accent-50 px-3 py-2.5 font-label text-sm text-accent-800">{error}</p>
        )}

        <button type="submit" disabled={busy}
                className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600 disabled:cursor-not-allowed disabled:opacity-60">
          {busy ? <i className="ri-loader-4-line animate-spin text-base leading-none"></i> : <i className="ri-login-box-line text-base leading-none"></i>}
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
