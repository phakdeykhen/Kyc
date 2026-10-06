import { useCallback, useEffect, useState } from "react";
import Modal from "@/components/base/Modal";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { ApiKeyCreated, ApiKeyInfo } from "@/api/types";
import { useApiKey } from "@/auth/useStaffAuth";
import { SCOPE_LABELS } from "@/lib/catalog";
import { dateTime, since } from "@/pages/review/format";

const th = "px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500";
const input = "mt-1.5 w-full rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-sm text-foreground-900 placeholder:text-foreground-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100";

/** GET/POST/DELETE /v1/api-keys. A new key can never hold more scope, rate, network or lifetime than the key creating it. */
export default function ApiKeysPanel() {
  const { apiKey, organization } = useApiKey();
  const ownScopes = organization?.credential.scopes ?? [];
  const [keys, setKeys] = useState<ApiKeyInfo[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["sessions:write", "sessions:read"].filter((scope) => ownScopes.includes(scope)));
  const [expiresDays, setExpiresDays] = useState("");
  const [rateLimit, setRateLimit] = useState("");
  const [cidrs, setCidrs] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<ApiKeyCreated | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ApiKeyInfo | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    if (!apiKey) return;
    try {
      setKeys(await kycApi.listKeys(apiKey));
      setLoadError("");
    } catch (caught) {
      setLoadError((caught as Error).message);
    }
  }, [apiKey]);

  useEffect(() => {
    load();
  }, [load]);

  const resetForm = () => {
    setCreateOpen(false);
    setName("");
    setExpiresDays("");
    setRateLimit("");
    setCidrs("");
    setError("");
  };

  const create = async () => {
    if (!apiKey) return;
    if (!name.trim()) return setError("Give the key a recognizable name.");
    if (scopes.length === 0) return setError("Select at least one scope.");
    const days = expiresDays ? Number(expiresDays) : undefined;
    if (days !== undefined && (!Number.isInteger(days) || days < 1 || days > 730)) return setError("Expiry must be 1–730 days.");
    const rate = rateLimit ? Number(rateLimit) : undefined;
    if (rate !== undefined && (!Number.isInteger(rate) || rate < 1)) return setError("Rate limit must be a positive whole number.");
    const networks = cidrs.split(/[\s,]+/).map((value) => value.trim()).filter(Boolean);
    setBusy(true);
    setError("");
    try {
      const created = await kycApi.createKey(apiKey, {
        name: name.trim(), scopes, expires_in_days: days, rate_limit_per_minute: rate,
        allowed_cidrs: networks.length ? networks : undefined,
      });
      resetForm();
      setRevealed(created);
      await load();
    } catch (caught) {
      setError((caught as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async () => {
    if (!apiKey || !revokeTarget) return;
    setBusy(true);
    try {
      await kycApi.revokeKey(apiKey, revokeTarget.id);
      setRevokeTarget(null);
      await load();
    } catch (caught) {
      setLoadError((caught as ApiError).message);
      setRevokeTarget(null);
    } finally {
      setBusy(false);
    }
  };

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const currentKeyId = organization?.credential.key_id;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-lg border border-background-200 bg-background-50 p-4 sm:flex-row sm:items-center sm:justify-between md:p-5">
        <div>
          <h2 className="font-heading text-base font-semibold text-foreground-950">API keys</h2>
          <p className="mt-0.5 font-label text-sm text-foreground-600">
            Keys authenticate your server. Only a SHA-256 of each key is stored. To rotate: create the new key, deploy it, then revoke the old one.
          </p>
        </div>
        <button type="button" onClick={() => setCreateOpen(true)}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600">
          <i className="ri-add-line text-base leading-none"></i>
          Create API key
        </button>
      </div>

      {loadError && <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{loadError}</p>}

      <div className="overflow-hidden rounded-lg border border-background-200 bg-background-50">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-left">
            <thead>
              <tr className="border-b border-background-200 bg-background-100/60">
                <th className={`${th} md:px-5`}>Name</th>
                <th className={th}>Key</th>
                <th className={th}>Scopes</th>
                <th className={th}>Limits</th>
                <th className={th}>Created</th>
                <th className={th}>Last used</th>
                <th className={`${th} text-right`}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {!keys && !loadError && (
                <tr><td colSpan={7} className="px-5 py-8 text-center font-label text-sm text-foreground-500">Loading…</td></tr>
              )}
              {keys?.length === 0 && (
                <tr><td colSpan={7} className="px-5 py-8 text-center font-label text-sm text-foreground-500">No provisioned keys yet.</td></tr>
              )}
              {keys?.map((key) => {
                const active = key.status === "ACTIVE";
                return (
                  <tr key={key.id} className="border-b border-background-100 last:border-0">
                    <td className="px-4 py-3 md:px-5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-label text-sm font-medium text-foreground-950">{key.name}</span>
                        {!active && <span className="rounded-full bg-background-200 px-2 py-0.5 font-label text-[10px] text-foreground-600">{key.status.toLowerCase()}</span>}
                        {key.id === currentKeyId && <span className="rounded-full bg-primary-100 px-2 py-0.5 font-label text-[10px] text-primary-800">this console</span>}
                      </div>
                      <div className="font-mono text-[10px] text-foreground-400">{key.created_by}</div>
                    </td>
                    <td className="px-4 py-3"><span className="rounded bg-background-100 px-2 py-1 font-mono text-[11px] text-foreground-700">{key.key_prefix}…</span></td>
                    <td className="px-4 py-3">
                      <div className="flex max-w-[240px] flex-wrap gap-1">
                        {key.scopes.map((scope) => <span key={scope} className="rounded bg-secondary-100 px-1.5 py-0.5 font-mono text-[10px] text-secondary-800">{scope}</span>)}
                      </div>
                    </td>
                    <td className="px-4 py-3 font-label text-[11px] text-foreground-600">
                      <div>{key.rate_limit_per_minute}/min</div>
                      {key.allowed_cidrs.length > 0 && <div className="font-mono" title={key.allowed_cidrs.join(", ")}>{key.allowed_cidrs.length} network{key.allowed_cidrs.length > 1 ? "s" : ""}</div>}
                      {key.expires_at && <div>expires {dateTime(key.expires_at)}</div>}
                    </td>
                    <td className="px-4 py-3 font-label text-xs text-foreground-600">{dateTime(key.created_at)}</td>
                    <td className="px-4 py-3 font-label text-xs text-foreground-600">{key.last_used_at ? `${since(key.last_used_at)} ago` : "Never"}</td>
                    <td className="px-4 py-3 text-right">
                      {active ? (
                        <button type="button" onClick={() => setRevokeTarget(key)}
                                className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-accent-300 px-2.5 py-1.5 font-label text-xs text-accent-700 transition-colors hover:bg-accent-50">
                          <i className="ri-forbid-2-line text-sm leading-none"></i>Revoke
                        </button>
                      ) : <span className="font-label text-xs text-foreground-400">{key.revoked_at ? `revoked ${since(key.revoked_at)} ago` : "—"}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <Modal open={createOpen} onClose={resetForm} title="Create API key" icon="ri-key-2-line"
             subtitle="The full key is shown once, right after creation."
             footer={(
               <>
                 <button type="button" onClick={resetForm}
                         className="rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100">Cancel</button>
                 <button type="button" onClick={create} disabled={busy}
                         className="inline-flex items-center justify-center gap-2 rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600 disabled:opacity-60">
                   <i className={`${busy ? "ri-loader-4-line animate-spin" : "ri-key-2-line"} text-base leading-none`}></i>Create key
                 </button>
               </>
             )}>
        <label className="font-label text-sm font-medium text-foreground-800" htmlFor="key-name">Key name</label>
        <input id="key-name" type="text" value={name} maxLength={120} onChange={(e) => setName(e.target.value)}
               placeholder="e.g. Production backend" className={input} />

        <div className="mt-4 font-label text-sm font-medium text-foreground-800">Scopes</div>
        <p className="font-label text-xs text-foreground-500">Only scopes the connected key holds can be granted.</p>
        <div className="mt-2 flex flex-col gap-2">
          {Object.keys(SCOPE_LABELS).map((scope) => {
            const allowed = ownScopes.includes(scope);
            const on = scopes.includes(scope);
            return (
              <button key={scope} type="button" disabled={!allowed}
                      onClick={() => setScopes((prev) => (on ? prev.filter((item) => item !== scope) : [...prev, scope]))}
                      className={`flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                        on ? "border-primary-300 bg-primary-50" : "border-background-200 bg-background-50 hover:bg-background-100"}`}>
                <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${on ? "border-primary-500 bg-primary-500" : "border-background-400"}`}>
                  {on && <i className="ri-check-line text-[11px] leading-none text-background-50"></i>}
                </span>
                <span className="min-w-0">
                  <span className="block font-mono text-xs text-foreground-900">{scope}</span>
                  <span className="block font-label text-xs text-foreground-600">{SCOPE_LABELS[scope]}</span>
                </span>
              </button>
            );
          })}
        </div>

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className="font-label text-sm font-medium text-foreground-800" htmlFor="key-expiry">Expires in (days)</label>
            <input id="key-expiry" type="number" min={1} max={730} value={expiresDays} onChange={(e) => setExpiresDays(e.target.value)}
                   placeholder="never" className={input} />
          </div>
          <div>
            <label className="font-label text-sm font-medium text-foreground-800" htmlFor="key-rate">Rate limit (per minute)</label>
            <input id="key-rate" type="number" min={1} value={rateLimit} onChange={(e) => setRateLimit(e.target.value)}
                   placeholder={organization?.credential.rate_limit_per_minute ? `up to ${organization.credential.rate_limit_per_minute}` : "default"} className={input} />
          </div>
        </div>
        <label className="mt-4 block font-label text-sm font-medium text-foreground-800" htmlFor="key-cidrs">Allowed networks</label>
        <input id="key-cidrs" type="text" value={cidrs} onChange={(e) => setCidrs(e.target.value)}
               placeholder="e.g. 203.0.113.0/24, 198.51.100.7 (empty: same as the connected key)" className={`${input} font-mono`} />
        {error && <p role="alert" className="mt-3 font-label text-sm text-accent-700">{error}</p>}
      </Modal>

      <Modal open={revealed !== null} onClose={() => setRevealed(null)} title="Copy your API key" icon="ri-key-2-line"
             subtitle="It is shown only this once. Store it in your server's secret manager."
             footer={<button type="button" onClick={() => setRevealed(null)} className="rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 hover:bg-primary-600">I have stored it</button>}>
        <div className="flex items-center gap-2 rounded-md border border-background-200 bg-background-100 p-3">
          <code className="min-w-0 flex-1 break-all font-mono text-xs text-foreground-900">{revealed?.api_key}</code>
          <button type="button" onClick={() => revealed && copy(revealed.api_key)}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md border border-background-300 bg-background-50 px-2.5 py-1.5 font-label text-xs text-foreground-700 hover:bg-background-100">
            <i className={`${copied ? "ri-check-line" : "ri-file-copy-line"} text-sm leading-none`}></i>{copied ? "Copied" : "Copy"}
          </button>
        </div>
        <p className="mt-3 flex items-start gap-2 font-label text-xs text-foreground-600">
          <i className="ri-information-line mt-0.5 text-sm leading-none text-accent-600"></i>
          Never put this key in a browser or mobile app. Devices get a one-session client token from your server instead.
        </p>
      </Modal>

      <Modal open={revokeTarget !== null} onClose={() => setRevokeTarget(null)} title="Revoke API key" icon="ri-forbid-2-line"
             subtitle="Requests using this key fail immediately." maxWidthClass="max-w-md"
             footer={(
               <>
                 <button type="button" onClick={() => setRevokeTarget(null)} className="rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">Cancel</button>
                 <button type="button" onClick={revoke} disabled={busy}
                         className="inline-flex items-center gap-2 rounded-md bg-accent-600 px-4 py-2.5 font-label text-sm font-medium text-background-50 hover:bg-accent-700 disabled:opacity-60">
                   <i className="ri-forbid-2-line text-base leading-none"></i>Revoke key
                 </button>
               </>
             )}>
        <p className="font-label text-sm text-foreground-700">
          Revoke <span className="font-medium text-foreground-950">{revokeTarget?.name}</span>? This cannot be undone.
          {revokeTarget?.id === currentKeyId && " This is the key this console is using; the console will lose access."}
        </p>
      </Modal>
    </div>
  );
}
