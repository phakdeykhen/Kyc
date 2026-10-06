import { useState } from "react";
import AppHeader from "@/components/feature/AppHeader";
import SiteFooter from "@/components/feature/SiteFooter";
import ApiKeysPanel from "@/pages/developers/components/ApiKeysPanel";
import WebhooksPanel from "@/pages/developers/components/WebhooksPanel";
import RegistryPanel from "@/pages/developers/components/RegistryPanel";

type Tab = "keys" | "webhooks" | "registry";

const tabs: { id: Tab; label: string; icon: string }[] = [
  { id: "keys", label: "API keys", icon: "ri-key-2-line" },
  { id: "webhooks", label: "Webhooks", icon: "ri-link-m" },
  { id: "registry", label: "Document registry", icon: "ri-book-2-line" },
];

export default function DevelopersPage() {
  const [tab, setTab] = useState<Tab>("keys");
  const [env, setEnv] = useState<"SANDBOX" | "PRODUCTION">("SANDBOX");
  const [copied, setCopied] = useState(false);

  const baseUrl = env === "SANDBOX" ? "https://sandbox.api.verix.io/v1" : "https://api.verix.io/v1";

  const copyBase = async () => {
    try {
      await navigator.clipboard.writeText(baseUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-background-50">
      <AppHeader />

      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-8 md:px-6 md:py-10">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <nav className="flex items-center gap-1.5 font-label text-xs text-foreground-500">
              <span>Console</span>
              <i className="ri-arrow-right-s-line text-sm leading-none"></i>
              <span className="text-foreground-800">Developer</span>
            </nav>
            <h1 className="mt-2 font-heading text-2xl font-semibold tracking-tight text-foreground-950 md:text-3xl">
              Developer console
            </h1>
            <p className="mt-1.5 max-w-2xl font-label text-sm text-foreground-600">
              Manage credentials for every tenant you operate, subscribe to signed webhooks,
              and review which countries and document types the platform supports.
            </p>
          </div>

          <div className="inline-flex items-center gap-1 rounded-full border border-background-200 bg-background-100 p-1">
            {(["SANDBOX", "PRODUCTION"] as const).map((e) => (
              <button
                key={e}
                type="button"
                onClick={() => setEnv(e)}
                className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1.5 font-label text-xs font-medium transition-colors ${
                  env === e ? "bg-background-50 text-foreground-950" : "text-foreground-600 hover:text-foreground-900"
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${e === "PRODUCTION" ? "bg-primary-500" : "bg-accent-500"}`}></span>
                {e === "SANDBOX" ? "Sandbox" : "Production"}
              </button>
            ))}
          </div>
        </div>

        <section className="mt-6 grid grid-cols-1 gap-3 lg:grid-cols-3">
          <div className="rounded-lg border border-background-200 bg-background-50 p-4 lg:col-span-2">
            <div className="font-label text-xs text-foreground-500">API base URL</div>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-md border border-background-200 bg-background-100 px-3 py-2 font-mono text-sm text-foreground-900">
                {baseUrl}
              </code>
              <button
                type="button"
                onClick={copyBase}
                className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100"
              >
                <i className={`${copied ? "ri-check-line" : "ri-file-copy-line"} text-sm leading-none`}></i>
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
          </div>
          <div className="rounded-lg border border-background-200 bg-background-50 p-4">
            <div className="font-label text-xs text-foreground-500">Tenant</div>
            <div className="mt-2 flex items-center gap-2.5">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-secondary-100 text-secondary-700">
                <i className="ri-building-2-line text-base leading-none"></i>
              </span>
              <div>
                <div className="font-label text-sm font-medium text-foreground-950">Acme Pay Platform</div>
                <div className="font-mono text-[11px] text-foreground-500">tenant_acme_9f2k</div>
              </div>
            </div>
          </div>
        </section>

        <div className="mt-6 inline-flex items-center gap-1 rounded-full border border-background-200 bg-background-100 p-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-3.5 py-1.5 font-label text-sm font-medium transition-colors ${
                tab === t.id ? "bg-background-50 text-foreground-950" : "text-foreground-600 hover:text-foreground-900"
              }`}
            >
              <i className={`${t.icon} text-sm leading-none`}></i>
              {t.label}
            </button>
          ))}
        </div>

        <section className="mt-4">
          {tab === "keys" && <ApiKeysPanel />}
          {tab === "webhooks" && <WebhooksPanel />}
          {tab === "registry" && <RegistryPanel />}
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}