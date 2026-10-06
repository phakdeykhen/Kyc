import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useStaffAuth } from "@/auth/useStaffAuth";

interface NavItem {
  label: string;
  to: string;
}

const navItems: NavItem[] = [
  { label: "Console", to: "/console" },
  { label: "New verification", to: "/verify/new" },
  { label: "Review", to: "/review" },
  { label: "Developer", to: "/developers" },
];

export default function AppHeader() {
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const { profile, signOut } = useStaffAuth();
  const initials = (profile?.display_name ?? "")
    .split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "?";

  const isActive = (to: string) => location.pathname.startsWith(to);

  const leave = () => {
    signOut();
    navigate("/signin", { replace: true });
  };

  return (
    <header className="sticky top-0 z-40 w-full border-b border-background-200/80 bg-background-50/85 backdrop-blur-md">
      <div className="mx-auto flex h-16 items-center justify-between gap-4 px-4 md:px-6">
        <div className="flex items-center gap-8">
          <Link to="/console" className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-500 text-background-50">
              <i className="ri-fingerprint-2-line text-xl leading-none"></i>
            </span>
            <span className="font-heading text-lg font-semibold tracking-tight text-foreground-950">
              Verix
              <span className="ml-1.5 rounded bg-secondary-100 px-1.5 py-0.5 align-middle font-label text-[10px] font-medium uppercase tracking-wider text-secondary-700">
                KYC
              </span>
            </span>
          </Link>

          <nav className="hidden items-center gap-1 md:flex">
            {navItems.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={`rounded-md px-3 py-2 font-label text-sm transition-colors ${
                  isActive(item.to)
                    ? "bg-primary-100 text-primary-900"
                    : "text-foreground-600 hover:bg-background-100 hover:text-foreground-950"
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>

        <div className="hidden items-center gap-3 md:flex">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-accent-200 bg-accent-50 px-3 py-1 font-label text-xs font-medium text-accent-800">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-500"></span>
            Sandbox
          </span>
          <button
            type="button"
            onClick={() => navigate("/verify/new")}
            className="inline-flex items-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
          >
            <i className="ri-add-line text-base leading-none"></i>
            New verification
          </button>
          {profile && (
            <div className="flex items-center gap-2">
              <div title={`${profile.display_name} · ${profile.role.toLowerCase()}`}
                   className="flex h-9 w-9 items-center justify-center rounded-full bg-secondary-200 font-label text-sm font-semibold text-secondary-800">
                {initials}
              </div>
              <button type="button" onClick={leave}
                      className="whitespace-nowrap rounded-md border border-background-300 px-3 py-2 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100">
                Sign out
              </button>
            </div>
          )}
        </div>

        <button
          type="button"
          aria-label="Toggle menu"
          onClick={() => setMenuOpen((v) => !v)}
          className="flex h-9 w-9 items-center justify-center rounded-md border border-background-300 text-foreground-700 md:hidden"
        >
          <i className={`${menuOpen ? "ri-close-line" : "ri-menu-line"} text-xl leading-none`}></i>
        </button>
      </div>

      {menuOpen && (
        <div className="border-t border-background-200 bg-background-50 px-4 py-3 md:hidden">
          <div className="flex flex-col gap-1">
            {navItems.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                onClick={() => setMenuOpen(false)}
                className={`rounded-md px-3 py-2.5 font-label text-sm ${
                  isActive(item.to)
                    ? "bg-primary-100 text-primary-900"
                    : "text-foreground-700 hover:bg-background-100"
                }`}
              >
                {item.label}
              </Link>
            ))}
            {profile && (
              <button type="button" onClick={leave}
                      className="rounded-md px-3 py-2.5 text-left font-label text-sm text-foreground-700 hover:bg-background-100">
                Sign out ({profile.display_name})
              </button>
            )}
          </div>
        </div>
      )}
    </header>
  );
}