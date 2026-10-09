import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Menu, Plus, X } from "lucide-react";
import { useApiKey, useStaffAuth } from "@/auth/useStaffAuth";
import Brand from "./Brand";

const navItems = [
  { label: "Overview", to: "/console" },
  { label: "New verification", to: "/verify/new" },
  { label: "Review queue", to: "/review" },
  { label: "Developers", to: "/developers" },
];

export default function AppHeader() {
  const navigate = useNavigate();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const { profile, signOut } = useStaffAuth();
  const { apiKey, organization } = useApiKey();
  const initials = (profile?.display_name ?? "").split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "?";
  const leave = () => { signOut(); navigate("/signin", { replace: true }); };
  const isActive = (to: string) => location.pathname.startsWith(to);

  return (
    <header className="marketing-header staff-header">
      <div className="marketing-header-inner">
        <Link to="/" aria-label="Verix KYC home"><Brand /></Link>
        <nav className="marketing-nav" aria-label="Console navigation">
          {navItems.map((item) => <Link key={item.to} to={item.to} aria-current={isActive(item.to) ? "page" : undefined}>{item.label}</Link>)}
        </nav>
        <div className="marketing-header-actions">
          <Link to="/developers" className="staff-connection" title={apiKey ? `Connected to ${organization?.name ?? "your organization"}` : "Connect an API key in developer tools"}>
            <span className="status-dot" style={apiKey ? undefined : { background: "#666" }} /><span>{apiKey ? organization?.name ?? "Connected" : "Connect API key"}</span>
          </Link>
          <Link to="/verify/new" className="button button-small button-white"><Plus size={14} />New verification</Link>
          {profile && <><span className="staff-avatar" title={`${profile.display_name} · ${profile.role.toLowerCase()}`}>{initials}</span><button type="button" className="staff-signout" onClick={leave}>Sign out</button></>}
          <button type="button" className="mobile-menu-toggle" aria-label={menuOpen ? "Close navigation" : "Open navigation"} aria-expanded={menuOpen} aria-controls="staff-mobile-nav" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={22} /> : <Menu size={22} />}</button>
        </div>
      </div>
      {menuOpen && <nav id="staff-mobile-nav" className="marketing-mobile-nav" aria-label="Mobile console navigation">
        {navItems.map((item) => <Link key={item.to} to={item.to} aria-current={isActive(item.to) ? "page" : undefined} onClick={() => setMenuOpen(false)}>{item.label}</Link>)}
        {profile && <button type="button" onClick={leave} className="py-3 text-left text-sm text-foreground-600">Sign out ({profile.display_name})</button>}
      </nav>}
    </header>
  );
}
