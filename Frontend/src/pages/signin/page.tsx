import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, FileCheck2, LoaderCircle, LockKeyhole, ShieldCheck } from "lucide-react";
import { ApiError } from "@/api/http";
import { useStaffAuth } from "@/auth/useStaffAuth";
import Brand from "@/components/feature/Brand";

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

  const input = "w-full rounded-lg border border-background-300 bg-background-50 px-4 py-3.5 font-label text-sm text-foreground-900 placeholder:text-foreground-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100";

  return (
    <div className="signin-page">
      <header className="signin-header"><Link to="/" aria-label="Verix KYC home"><Brand /></Link><Link to="/"><ArrowLeft size={14} />Back to home</Link></header>
      <main className="signin-layout content-width">
        <div className="signin-intro">
          <span className="eyebrow"><span className="status-dot" />YOUR VERIFICATION WORKSPACE</span>
          <h1>Good decisions<br />start with<br />a clear picture.</h1>
          <p>Welcome to your Verix console. Review identities, follow verifications, and keep your team moving with confidence.</p>
          <div className="hero-trust"><span><FileCheck2 size={16} />Every document. One workspace.</span><span><ShieldCheck size={16} />The context behind every decision.</span><span><LockKeyhole size={16} />Private by design.</span></div>
        </div>
        <form onSubmit={submit} className="signin-form" autoComplete="off">
        <h2>Welcome back.</h2>
        <p>Sign in with your organization’s reviewer credentials.</p>

        <label className="mt-6 block font-label text-sm font-medium text-foreground-800" htmlFor="organization-id">Organization ID</label>
        <input id="organization-id" className={`${input} mt-2 font-mono`} value={organizationId} spellCheck={false}
               onChange={(e) => setOrganizationId(e.target.value)} placeholder="Your organization UUID" required />

        <label className="mt-4 block font-label text-sm font-medium text-foreground-800" htmlFor="reviewer-token">Reviewer token</label>
        <input id="reviewer-token" type="password" className={`${input} mt-1.5 font-mono`} value={token} spellCheck={false}
               onChange={(e) => setToken(e.target.value)} placeholder="rvw_…" required />
        <p className="mt-2 font-label text-xs text-foreground-500">
          Use the reviewer token provided by your administrator.
        </p>

        {error && (
          <p role="alert" className="mt-4 rounded-md border border-accent-200 bg-accent-50 px-3 py-2.5 font-label text-sm text-accent-800">{error}</p>
        )}

        <button type="submit" disabled={busy}
                className="button button-white mt-7 w-full">
          {busy ? <LoaderCircle size={17} className="animate-spin" /> : null}
          {busy ? "Signing in…" : "Sign in to console"}
          {!busy && <ArrowRight size={16} />}
        </button>
        <div className="mt-6 flex items-start gap-2 border-t border-background-200 pt-5 text-[11px] leading-relaxed text-foreground-500"><LockKeyhole size={14} className="mt-0.5 shrink-0" /><span>Your credentials stay in this tab’s memory. Reloading or closing the tab signs you out.</span></div>
      </form>
      </main>
      <footer className="signin-footer"><span>© {new Date().getFullYear()} Verix Identity Cloud.</span><span>Have a verification link? Open it directly to verify your identity.</span></footer>
    </div>
  );
}
