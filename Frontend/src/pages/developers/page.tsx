import { useState } from "react";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import ApiKeyGate from "@/components/feature/ApiKeyGate";
import ApiKeysPanel from "@/pages/developers/components/ApiKeysPanel";
import WebhooksPanel from "@/pages/developers/components/WebhooksPanel";
import RegistryPanel from "@/pages/developers/components/RegistryPanel";
import { useApiKey } from "@/auth/useStaffAuth";

type Tab = "keys" | "webhooks" | "registry";

const tabs: { id: Tab; label: string; icon: string; scope?: string }[] = [
  { id: "keys", label: "API keys", icon: "ri-key-2-line", scope: "keys:manage" },
  { id: "webhooks", label: "Webhooks", icon: "ri-link-m", scope: "webhooks:manage" },
  { id: "registry", label: "Document registry", icon: "ri-book-2-line" },
];

const API_BASE = `${(import.meta.env.VITE_KYC_API_BASE as string | undefined) || window.location.origin}/v1`;

export default function DevelopersPage() {
  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />
      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <nav className="flex items-center gap-1.5 font-label text-xs text-foreground-500">
          <span>Console</span>
          <i className="ri-arrow-right-s-line text-sm leading-none"></i>
          <span className="text-foreground-800">Developer</span>
        </nav>
        <h1 className="mt-2 font-heading text-2xl font-semibold tracking-tight text-foreground-950 md:text-3xl">Developer console</h1>
        <p className="mt-1.5 max-w-2xl font-label text-sm text-foreground-600">
          Manage your organization's API keys, subscribe to signed webhooks, and see which document types and countries the platform supports.
        </p>
        <div className="mt-6">
          <ApiKeyGate purpose="manage keys and webhooks">
            <DeveloperTabs />
          </ApiKeyGate>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}

function DeveloperTabs() {
  const { organization, has, disconnect } = useApiKey();
  const [tab, setTab] = useState<Tab>(() => (has("keys:manage") ? "keys" : has("webhooks:manage") ? "webhooks" : "registry"));
  const [copied, setCopied] = useState(false);
  if (!organization) return null;
  const credential = organization.credential;

  const copyBase = async () => {
    try {
      await navigator.clipboard.writeText(API_BASE);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <>
      <section className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <div className="rounded-lg border border-background-200 bg-background-50 p-4">
          <div className="font-label text-xs text-foreground-500">Organization</div>
          <div className="mt-2 flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-secondary-100 text-secondary-700">
              <i className="ri-building-2-line text-base leading-none"></i>
            </span>
            <div className="min-w-0">
              <div className="flex items-center gap-2 font-label text-sm font-medium text-foreground-950">
                {organization.name}
                <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${organization.active ? "bg-primary-100 text-primary-800" : "bg-accent-100 text-accent-800"}`}>
                  {organization.active ? "active" : "suspended"}
                </span>
              </div>
              <div className="truncate font-mono text-[11px] text-foreground-500">{organization.organization_id}</div>
            </div>
          </div>
          <p className="mt-3 font-label text-[11px] text-foreground-500">
            Retention: personal data {organization.retention.pii_retention_days} d · photos {organization.retention.capture_retention_hours} h ·
            face templates {organization.retention.template_retention_hours} h
          </p>
        </div>
        <div className="rounded-lg border border-background-200 bg-background-50 p-4">
          <div className="flex items-center justify-between">
            <span className="font-label text-xs text-foreground-500">Connected key</span>
            <button type="button" onClick={disconnect} className="font-label text-xs text-accent-700 hover:underline">Disconnect</button>
          </div>
          <div className="mt-2 font-mono text-[11px] text-foreground-700">
            {credential.type.replace(/_/g, " ")}{credential.key_id ? ` · ${credential.key_id.slice(0, 8)}` : ""}
            {credential.rate_limit_per_minute ? ` · ${credential.rate_limit_per_minute}/min` : ""}
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            {credential.scopes.map((scope) => (
              <span key={scope} className="rounded bg-secondary-100 px-1.5 py-0.5 font-mono text-[10px] text-secondary-800">{scope}</span>
            ))}
          </div>
        </div>
        <div className="rounded-lg border border-background-200 bg-background-50 p-4">
          <div className="font-label text-xs text-foreground-500">API base URL</div>
          <div className="mt-2 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-md border border-background-200 bg-background-100 px-3 py-2 font-mono text-xs text-foreground-900">{API_BASE}</code>
            <button type="button" onClick={copyBase}
                    className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100">
              <i className={`${copied ? "ri-check-line" : "ri-file-copy-line"} text-sm leading-none`}></i>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <p className="mt-2 font-label text-[11px] text-foreground-500">
            Headers: <code className="font-mono">X-API-Key</code> + <code className="font-mono">X-Organization-ID</code>. OpenAPI: <code className="font-mono">/docs</code>
          </p>
        </div>
      </section>

      <div className="mt-6 inline-flex flex-wrap items-center gap-1 rounded-full border border-background-200 bg-background-100 p-1">
        {tabs.map((item) => (
          <button key={item.id} type="button" onClick={() => setTab(item.id)}
                  className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-3.5 py-1.5 font-label text-sm font-medium transition-colors ${
                    tab === item.id ? "bg-background-50 text-foreground-950" : "text-foreground-600 hover:text-foreground-900"}`}>
            <i className={`${item.icon} text-sm leading-none`}></i>
            {item.label}
            {item.scope && !has(item.scope) && <i className="ri-lock-line text-xs leading-none text-foreground-400" title={`Needs ${item.scope}`}></i>}
          </button>
        ))}
      </div>

      <section className="mt-4">
        {tab === "keys" && (has("keys:manage") ? <ApiKeysPanel /> : <NeedsScope scope="keys:manage" />)}
        {tab === "webhooks" && (has("webhooks:manage") ? <WebhooksPanel /> : <NeedsScope scope="webhooks:manage" />)}
        {tab === "registry" && <RegistryPanel />}
      </section>
    </>
  );
}

function NeedsScope({ scope }: { scope: string }) {
  return (
    <div className="rounded-lg border border-background-200 bg-background-100/60 p-6 font-label text-sm text-foreground-600">
      The connected key lacks <code className="font-mono">{scope}</code>. Connect a key that holds it to use this tab.
    </div>
  );
}
