import { Link } from "react-router-dom";

export default function SiteFooter() {
  return (
    <footer className="mt-14 w-full bg-primary-950 text-background-50">
      <div className="mx-auto grid grid-cols-1 gap-8 px-4 py-12 md:grid-cols-4 md:px-6">
        <div className="md:col-span-2">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-500 text-background-50">
              <i className="ri-fingerprint-2-line text-xl leading-none"></i>
            </span>
            <span className="font-heading text-lg font-semibold">Verix KYC</span>
          </div>
          <p className="mt-4 max-w-sm font-label text-sm leading-relaxed text-background-50/70">
            Multi-country identity verification. Pluggable document adapters, biometric
            matching and deterministic risk decisions — designed to add new countries
            without rewriting the KYC core.
          </p>
        </div>
        <div>
          <h4 className="font-heading text-sm font-semibold text-background-50">Platform</h4>
          <ul className="mt-3 flex flex-col gap-2 font-label text-sm text-background-50/70">
            <li><Link to="/console" className="hover:text-background-50">Console</Link></li>
            <li><Link to="/verify/new" className="hover:text-background-50">New verification</Link></li>
            <li><Link to="/review" className="hover:text-background-50">Review queue</Link></li>
            <li><Link to="/developers" className="hover:text-background-50">Developer console</Link></li>
          </ul>
        </div>
        <div>
          <h4 className="font-heading text-sm font-semibold text-background-50">Coverage</h4>
          <ul className="mt-3 flex flex-col gap-2 font-label text-sm text-background-50/70">
            <li>Cambodia National ID</li>
            <li>Cambodia Passport &amp; NSSF</li>
            <li>International Passport / MRZ</li>
            <li>Foreign ID &amp; Residence Card</li>
          </ul>
        </div>
      </div>
      <div className="border-t border-background-50/10">
        <div className="mx-auto flex flex-col items-start justify-between gap-2 px-4 py-5 font-label text-xs text-background-50/60 sm:flex-row sm:items-center md:px-6">
          <span>© 2026 Verix Identity Cloud. Connected to the KYC API ({import.meta.env.MODE}).</span>
          <span className="inline-flex items-center gap-1.5">
            <i className="ri-shield-check-line text-sm leading-none"></i>
            Credentials are kept in this tab's memory only
          </span>
        </div>
      </div>
    </footer>
  );
}