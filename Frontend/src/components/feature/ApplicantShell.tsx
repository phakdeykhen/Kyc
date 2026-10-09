import type { ReactNode } from "react";
import { LockKeyhole } from "lucide-react";
import Brand from "./Brand";

/** Page frame for the person being verified: no console navigation, no staff identity. */
export function ApplicantShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <header className="applicant-header">
        <div className="applicant-header-inner">
          <Brand />
          <span><LockKeyhole size={13} />Secure verification</span>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[960px] flex-1 px-4 py-6 md:px-6 md:py-10">{children}</main>
      <footer className="applicant-footer">Powered by Verix · Privacy at every step</footer>
    </div>
  );
}

export function ApplicantMessage({ icon, title, text, tone = "neutral", children }:
  { icon: string; title: string; text: string; tone?: "neutral" | "ok" | "warn"; children?: ReactNode }) {
  const color = tone === "ok" ? "bg-primary-100 text-primary-700" : tone === "warn" ? "bg-accent-100 text-accent-700" : "bg-secondary-100 text-secondary-700";
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-background-200 bg-background-50 px-6 py-14 text-center">
      <span className={`flex h-16 w-16 items-center justify-center rounded-full ${color}`}>
        <i className={`${icon} text-3xl leading-none`}></i>
      </span>
      <h1 className="font-heading text-xl font-semibold text-foreground-950">{title}</h1>
      <p className="max-w-md font-label text-sm text-foreground-600">{text}</p>
      {children}
    </div>
  );
}
