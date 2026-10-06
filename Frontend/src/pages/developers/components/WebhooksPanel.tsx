import { useCallback, useEffect, useState } from "react";
import Modal from "@/components/base/Modal";
import { kycApi } from "@/api/client";
import { ApiError } from "@/api/http";
import type { WebhookDeliveryInfo, WebhookEndpointInfo, WebhookEndpointWithSecret } from "@/api/types";
import { useApiKey } from "@/auth/useStaffAuth";
import { dateTime, shortId, since } from "@/pages/review/format";

const DELIVERY_STATUS: Record<string, string> = {
  DELIVERED: "bg-primary-100 text-primary-900 border-primary-200",
  PENDING: "bg-accent-100 text-accent-900 border-accent-300",
  ABANDONED: "bg-accent-600 text-background-50 border-accent-600",
};
const input = "mt-1.5 w-full rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-sm text-foreground-900 placeholder:text-foreground-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100";
const smallButton = "inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-2.5 py-1.5 font-label text-xs transition-colors disabled:opacity-50";
const th = "px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500";

type Editing = { mode: "create" } | { mode: "edit"; endpoint: WebhookEndpointInfo };

/** /v1/webhooks: signed (HMAC-SHA256) callbacks, retried with backoff; secrets shown once at creation and rotation. */
export default function WebhooksPanel() {
  const { apiKey } = useApiKey();
  const [endpoints, setEndpoints] = useState<WebhookEndpointInfo[] | null>(null);
  const [eventTypes, setEventTypes] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [events, setEvents] = useState<string[]>([]);
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState("");
  const [secret, setSecret] = useState<WebhookEndpointWithSecret | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WebhookEndpointInfo | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDeliveryInfo[] | null>(null);
  const [deliveryFilter, setDeliveryFilter] = useState<"" | "PENDING" | "DELIVERED" | "ABANDONED">("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    if (!apiKey) return;
    try {
      const [list, types] = await Promise.all([kycApi.listEndpoints(apiKey), kycApi.eventTypes(apiKey)]);
      setEndpoints(list);
      setEventTypes(types);
      setSelected((current) => current && list.some((item) => item.id === current) ? current : list[0]?.id ?? null);
      setError("");
    } catch (caught) {
      setError((caught as Error).message);
    }
  }, [apiKey]);

  const loadDeliveries = useCallback(async () => {
    if (!apiKey || !selected) {
      setDeliveries(null);
      return;
    }
    try {
      setDeliveries(await kycApi.deliveries(apiKey, selected, deliveryFilter || undefined));
    } catch (caught) {
      setError((caught as Error).message);
    }
  }, [apiKey, selected, deliveryFilter]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    loadDeliveries();
  }, [loadDeliveries]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 5000);
    return () => clearTimeout(timer);
  }, [notice]);

  const openCreate = () => {
    setEditing({ mode: "create" });
    setUrl("");
    setDescription("");
    setEvents([]);
    setFormError("");
  };

  const openEdit = (endpoint: WebhookEndpointInfo) => {
    setEditing({ mode: "edit", endpoint });
    setUrl(endpoint.url);
    setDescription(endpoint.description ?? "");
    setEvents(endpoint.event_types);
    setFormError("");
  };

  const save = async () => {
    if (!apiKey || !editing) return;
    if (!/^https:\/\/.+/i.test(url.trim())) return setFormError("Enter an https:// URL. Its host must resolve to public addresses only.");
    setBusy("save");
    setFormError("");
    try {
      if (editing.mode === "create") {
        const created = await kycApi.createEndpoint(apiKey, { url: url.trim(), event_types: events, description: description.trim() || undefined });
        setSecret(created);
        setSelected(created.id);
      } else {
        await kycApi.updateEndpoint(apiKey, editing.endpoint.id, { url: url.trim(), event_types: events, description: description.trim() || null });
        setNotice("Endpoint updated.");
      }
      setEditing(null);
      await load();
    } catch (caught) {
      setFormError((caught as ApiError).message);
    } finally {
      setBusy("");
    }
  };

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError("");
    try {
      await action();
    } catch (caught) {
      setError((caught as ApiError).message);
    } finally {
      setBusy("");
    }
  };

  const rotate = (endpoint: WebhookEndpointInfo) => run(`rotate-${endpoint.id}`, async () => {
    if (!apiKey) return;
    setSecret(await kycApi.rotateSecret(apiKey, endpoint.id));
    await load();
  });

  const test = (endpoint: WebhookEndpointInfo) => run(`test-${endpoint.id}`, async () => {
    if (!apiKey) return;
    const delivery = await kycApi.sendTest(apiKey, endpoint.id);
    setNotice(`Test event queued (${delivery.status.toLowerCase()}). It appears in the deliveries below.`);
    setSelected(endpoint.id);
    await loadDeliveries();
  });

  const redeliver = (delivery: WebhookDeliveryInfo) => run(`redeliver-${delivery.id}`, async () => {
    if (!apiKey || !selected) return;
    await kycApi.redeliver(apiKey, selected, delivery.id);
    setNotice("Delivery queued again with the same event ID.");
    await loadDeliveries();
  });

  const remove = () => run("delete", async () => {
    if (!apiKey || !deleteTarget) return;
    await kycApi.deleteEndpoint(apiKey, deleteTarget.id);
    setDeleteTarget(null);
    await load();
  });

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-lg border border-background-200 bg-background-50 p-4 sm:flex-row sm:items-center sm:justify-between md:p-5">
        <div>
          <h2 className="font-heading text-base font-semibold text-foreground-950">Webhook endpoints</h2>
          <p className="mt-0.5 max-w-2xl font-label text-sm text-foreground-600">
            Signed events as sessions move. Verify <code className="font-mono">KYC-Signature</code> (t=…,v1=HMAC-SHA256 of "t.body") and the
            5-minute timestamp window, and deduplicate on <code className="font-mono">KYC-Event-ID</code>. Order events by <code className="font-mono">session_version</code>.
          </p>
        </div>
        <button type="button" onClick={openCreate}
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600">
          <i className="ri-add-line text-base leading-none"></i>Add endpoint
        </button>
      </div>

      {notice && <p role="status" className="rounded-md border border-primary-200 bg-primary-50 px-4 py-3 font-label text-sm text-primary-800">{notice}</p>}
      {error && <p role="alert" className="rounded-md border border-accent-200 bg-accent-50 px-4 py-3 font-label text-sm text-accent-800">{error}</p>}

      {!endpoints ? (
        <div className="flex items-center gap-2 py-8 font-label text-sm text-foreground-500"><i className="ri-loader-4-line animate-spin"></i>Loading…</div>
      ) : endpoints.length === 0 ? (
        <div className="rounded-lg border border-dashed border-background-300 bg-background-100/50 p-8 text-center font-label text-sm text-foreground-600">
          No endpoints yet. Add one to receive <code className="font-mono">kyc.verified</code>, <code className="font-mono">kyc.review.required</code> and the other events.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {endpoints.map((endpoint) => (
            <div key={endpoint.id}
                 className={`rounded-lg border bg-background-50 p-4 ${selected === endpoint.id ? "border-primary-300" : "border-background-200"}`}>
              <div className="flex items-start justify-between gap-2">
                <button type="button" onClick={() => setSelected(endpoint.id)} className="flex min-w-0 items-start gap-2 text-left">
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary-100 text-secondary-700">
                    <i className="ri-link-m text-base leading-none"></i>
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-xs text-foreground-900">{endpoint.url}</span>
                    {endpoint.description && <span className="block truncate font-label text-xs text-foreground-600">{endpoint.description}</span>}
                    <span className="mt-1 inline-flex items-center gap-1.5 font-label text-[11px] text-foreground-500">
                      <span className={`h-1.5 w-1.5 rounded-full ${endpoint.active ? "bg-primary-500" : "bg-accent-500"}`}></span>
                      {endpoint.active ? "Active" : "Disabled"}
                      {endpoint.consecutive_failures > 0 && ` · ${endpoint.consecutive_failures} failures in a row`}
                    </span>
                  </span>
                </button>
                <button type="button" onClick={() => openEdit(endpoint)} className={`${smallButton} border-background-300 text-foreground-700 hover:bg-background-100`}>
                  <i className="ri-edit-line text-sm leading-none"></i>Edit
                </button>
              </div>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {(endpoint.event_types.length ? endpoint.event_types : ["all kyc.* events"]).map((event) => (
                  <span key={event} className="rounded bg-accent-100 px-2 py-0.5 font-mono text-[10px] text-accent-900">{event}</span>
                ))}
              </div>
              {endpoint.previous_secret_expires_at && (
                <p className="mt-2 font-label text-[11px] text-foreground-500">
                  Old secret also signs until {dateTime(endpoint.previous_secret_expires_at)}.
                </p>
              )}

              <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
                <button type="button" onClick={() => test(endpoint)} disabled={!!busy}
                        className={`${smallButton} border-background-300 text-foreground-700 hover:bg-background-100`}>
                  <i className={`${busy === `test-${endpoint.id}` ? "ri-loader-4-line animate-spin" : "ri-send-plane-line"} text-sm leading-none`}></i>Send test
                </button>
                <button type="button" onClick={() => rotate(endpoint)} disabled={!!busy}
                        className={`${smallButton} border-background-300 text-foreground-700 hover:bg-background-100`}>
                  <i className={`${busy === `rotate-${endpoint.id}` ? "ri-loader-4-line animate-spin" : "ri-refresh-line"} text-sm leading-none`}></i>Rotate secret
                </button>
                <button type="button" onClick={() => setDeleteTarget(endpoint)} disabled={!!busy}
                        className={`${smallButton} border-accent-300 text-accent-700 hover:bg-accent-50`}>
                  <i className="ri-delete-bin-line text-sm leading-none"></i>Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {selected && (
        <div className="overflow-hidden rounded-lg border border-background-200 bg-background-50">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-background-200 px-4 py-3.5 md:px-5">
            <h3 className="font-heading text-sm font-semibold text-foreground-950">
              Deliveries · <span className="font-mono text-xs font-normal text-foreground-600">{endpoints?.find((item) => item.id === selected)?.url}</span>
            </h3>
            <div className="flex items-center gap-2">
              <select value={deliveryFilter} onChange={(e) => setDeliveryFilter(e.target.value as typeof deliveryFilter)}
                      className="rounded-md border border-background-300 bg-background-50 px-2 py-1.5 font-label text-xs text-foreground-800">
                <option value="">All</option>
                <option value="PENDING">Pending</option>
                <option value="DELIVERED">Delivered</option>
                <option value="ABANDONED">Abandoned</option>
              </select>
              <button type="button" onClick={loadDeliveries} className={`${smallButton} border-background-300 text-foreground-700 hover:bg-background-100`}>
                <i className="ri-refresh-line text-sm leading-none"></i>Refresh
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[780px] border-collapse text-left">
              <thead>
                <tr className="border-b border-background-200 bg-background-100/60">
                  <th className={`${th} md:px-5`}>Event</th>
                  <th className={th}>Session</th>
                  <th className={th}>Status</th>
                  <th className={th}>Attempts</th>
                  <th className={th}>Last attempt</th>
                  <th className="px-4 py-2.5"></th>
                </tr>
              </thead>
              <tbody>
                {deliveries?.length === 0 && (
                  <tr><td colSpan={6} className="px-5 py-8 text-center font-label text-sm text-foreground-500">No deliveries yet.</td></tr>
                )}
                {deliveries?.map((delivery) => (
                  <tr key={delivery.id} className="border-b border-background-100 last:border-0">
                    <td className="px-4 py-3 md:px-5">
                      <div className="font-mono text-xs text-foreground-900">{delivery.event_type}</div>
                      <div className="font-mono text-[10px] text-foreground-400">{shortId(delivery.event_id)}</div>
                    </td>
                    <td className="px-4 py-3 font-mono text-[11px] text-foreground-600">{delivery.session_id ? shortId(delivery.session_id) : "—"}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 font-label text-[11px] font-medium ${DELIVERY_STATUS[delivery.status] ?? "border-background-300 bg-background-200 text-foreground-600"}`}>
                        {delivery.last_status_code !== null && <span className="font-mono">{delivery.last_status_code}</span>}
                        {delivery.status.toLowerCase()}
                      </span>
                      {delivery.last_error && <div className="mt-1 max-w-[220px] truncate font-label text-[10px] text-accent-700" title={delivery.last_error}>{delivery.last_error}</div>}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-foreground-600">{delivery.attempts}</td>
                    <td className="px-4 py-3 font-label text-xs text-foreground-600">
                      {delivery.last_attempt_at ? `${since(delivery.last_attempt_at)} ago` : "—"}
                      {delivery.status === "PENDING" && <div className="text-[10px] text-foreground-400">next {dateTime(delivery.next_attempt_at)}</div>}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {delivery.status !== "DELIVERED" ? (
                        <button type="button" onClick={() => redeliver(delivery)} disabled={!!busy}
                                className={`${smallButton} border-background-300 text-foreground-700 hover:bg-background-100`}>
                          <i className="ri-refresh-line text-sm leading-none"></i>Redeliver
                        </button>
                      ) : <i className="ri-check-line text-base leading-none text-primary-600"></i>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Modal open={editing !== null} onClose={() => setEditing(null)} icon="ri-link-m"
             title={editing?.mode === "edit" ? "Edit webhook endpoint" : "Add webhook endpoint"}
             subtitle="https only. Redirects are not followed and private addresses are refused."
             footer={(
               <>
                 <button type="button" onClick={() => setEditing(null)} className="rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">Cancel</button>
                 <button type="button" onClick={save} disabled={busy === "save"}
                         className="inline-flex items-center gap-2 rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 hover:bg-primary-600 disabled:opacity-60">
                   <i className={`${busy === "save" ? "ri-loader-4-line animate-spin" : "ri-save-line"} text-base leading-none`}></i>
                   {editing?.mode === "edit" ? "Save" : "Add endpoint"}
                 </button>
               </>
             )}>
        <label className="font-label text-sm font-medium text-foreground-800" htmlFor="hook-url">Endpoint URL</label>
        <input id="hook-url" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.example.com/hooks/kyc" className={`${input} font-mono`} />
        <label className="mt-4 block font-label text-sm font-medium text-foreground-800" htmlFor="hook-description">Description <span className="font-normal text-foreground-400">(optional)</span></label>
        <input id="hook-description" type="text" maxLength={200} value={description} onChange={(e) => setDescription(e.target.value)} className={input} />
        <div className="mt-4 font-label text-sm font-medium text-foreground-800">Events</div>
        <p className="font-label text-xs text-foreground-500">Select none to receive every <code className="font-mono">kyc.*</code> event.</p>
        <div className="mt-2 flex flex-col gap-2">
          {Object.entries(eventTypes).map(([event, text]) => {
            const on = events.includes(event);
            return (
              <button key={event} type="button" onClick={() => setEvents((prev) => (on ? prev.filter((item) => item !== event) : [...prev, event]))}
                      className={`flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors ${on ? "border-primary-300 bg-primary-50" : "border-background-200 bg-background-50 hover:bg-background-100"}`}>
                <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${on ? "border-primary-500 bg-primary-500" : "border-background-400"}`}>
                  {on && <i className="ri-check-line text-[11px] leading-none text-background-50"></i>}
                </span>
                <span className="min-w-0">
                  <span className="block font-mono text-xs text-foreground-900">{event}</span>
                  <span className="block font-label text-xs text-foreground-600">{text}</span>
                </span>
              </button>
            );
          })}
        </div>
        {formError && <p role="alert" className="mt-3 font-label text-sm text-accent-700">{formError}</p>}
      </Modal>

      <Modal open={secret !== null} onClose={() => setSecret(null)} title="Copy the signing secret" icon="ri-key-line"
             subtitle="Shown only this once. Store it where your webhook receiver can read it."
             footer={<button type="button" onClick={() => setSecret(null)} className="rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 hover:bg-primary-600">I have stored it</button>}>
        <div className="flex items-center gap-2 rounded-md border border-background-200 bg-background-100 p-3">
          <code className="min-w-0 flex-1 break-all font-mono text-xs text-foreground-900">{secret?.secret}</code>
          <button type="button" onClick={() => secret && copy(secret.secret)}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md border border-background-300 bg-background-50 px-2.5 py-1.5 font-label text-xs text-foreground-700 hover:bg-background-100">
            <i className={`${copied ? "ri-check-line" : "ri-file-copy-line"} text-sm leading-none`}></i>{copied ? "Copied" : "Copy"}
          </button>
        </div>
        {secret?.previous_secret_expires_at && (
          <p className="mt-3 font-label text-xs text-foreground-600">
            The old secret keeps signing alongside this one until {dateTime(secret.previous_secret_expires_at)}, so you can deploy without dropping events.
          </p>
        )}
      </Modal>

      <Modal open={deleteTarget !== null} onClose={() => setDeleteTarget(null)} title="Delete webhook endpoint" icon="ri-delete-bin-line"
             subtitle="No further events are sent to it." maxWidthClass="max-w-md"
             footer={(
               <>
                 <button type="button" onClick={() => setDeleteTarget(null)} className="rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 hover:bg-background-100">Cancel</button>
                 <button type="button" onClick={remove} disabled={busy === "delete"}
                         className="inline-flex items-center gap-2 rounded-md bg-accent-600 px-4 py-2.5 font-label text-sm font-medium text-background-50 hover:bg-accent-700 disabled:opacity-60">
                   <i className="ri-delete-bin-line text-base leading-none"></i>Delete
                 </button>
               </>
             )}>
        <p className="break-all font-mono text-xs text-foreground-800">{deleteTarget?.url}</p>
      </Modal>
    </div>
  );
}
