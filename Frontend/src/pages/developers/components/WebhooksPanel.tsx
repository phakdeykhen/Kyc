import { useState } from "react";
import Modal from "@/components/base/Modal";
import { webhookDeliveries, webhookEndpoints, webhookEvents } from "@/mocks/developers";
import type { WebhookEndpoint } from "@/types/kyc";

const resultMeta: Record<string, { label: string; className: string }> = {
  SUCCESS: { label: "Delivered", className: "bg-primary-100 text-primary-900 border-primary-200" },
  RETRYING: { label: "Retrying", className: "bg-accent-100 text-accent-900 border-accent-300" },
  FAILED: { label: "Failed", className: "bg-accent-600 text-background-50 border-accent-600" },
};

export default function WebhooksPanel() {
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>(webhookEndpoints);
  const [addOpen, setAddOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["session.completed"]);
  const [error, setError] = useState("");
  const [revealed, setRevealed] = useState<string | null>(null);

  const toggleEvent = (id: string) =>
    setEvents((prev) => (prev.includes(id) ? prev.filter((e) => e !== id) : [...prev, id]));

  const toggleStatus = (id: string) =>
    setEndpoints((prev) =>
      prev.map((e) => (e.id === id ? { ...e, status: e.status === "ACTIVE" ? "PAUSED" : "ACTIVE" } : e)),
    );

  const removeEndpoint = (id: string) => setEndpoints((prev) => prev.filter((e) => e.id !== id));

  const handleAdd = () => {
    if (!/^https:\/\/.+/.test(url.trim())) {
      setError("Enter a valid HTTPS endpoint URL.");
      return;
    }
    if (events.length === 0) {
      setError("Subscribe to at least one event.");
      return;
    }
    setEndpoints((prev) => [
      ...prev,
      {
        id: `wh_${Date.now()}`,
        url: url.trim(),
        events: [...events],
        status: "ACTIVE",
        secretMasked: "whsec_••••••••••••" + Math.random().toString(16).slice(2, 6),
      },
    ]);
    setAddOpen(false);
    setUrl("");
    setEvents(["session.completed"]);
    setError("");
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-lg border border-background-200 bg-background-50 p-4 sm:flex-row sm:items-center sm:justify-between md:p-5">
        <div>
          <h2 className="font-heading text-base font-semibold text-foreground-950">Webhook endpoints</h2>
          <p className="mt-0.5 font-label text-sm text-foreground-600">
            Receive signed events when sessions complete or decisions change. Verify every payload with your signing secret.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setAddOpen(true)}
          className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
        >
          <i className="ri-add-line text-base leading-none"></i>
          Add endpoint
        </button>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {endpoints.map((e) => (
          <div key={e.id} className="rounded-lg border border-background-200 bg-background-50 p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="flex min-w-0 items-start gap-2">
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-secondary-100 text-secondary-700">
                  <i className="ri-link-m text-base leading-none"></i>
                </span>
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs text-foreground-900">{e.url}</p>
                  <span className="mt-1 inline-flex items-center gap-1.5 font-label text-[11px] text-foreground-500">
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${e.status === "ACTIVE" ? "bg-primary-500" : "bg-accent-500"}`}
                    ></span>
                    {e.status === "ACTIVE" ? "Active" : "Paused"}
                  </span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => toggleStatus(e.id)}
                className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-background-300 px-2.5 py-1.5 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100"
              >
                <i className={`${e.status === "ACTIVE" ? "ri-pause-line" : "ri-play-line"} text-sm leading-none`}></i>
                {e.status === "ACTIVE" ? "Pause" : "Activate"}
              </button>
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5">
              {e.events.map((ev) => (
                <span key={ev} className="rounded bg-accent-100 px-2 py-0.5 font-mono text-[10px] text-accent-900">
                  {ev}
                </span>
              ))}
            </div>

            <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-background-100 bg-background-100/60 px-3 py-2">
              <span className="font-label text-[11px] text-foreground-500">Signing secret</span>
              <span className="flex items-center gap-2">
                <code className="font-mono text-[11px] text-foreground-800">
                  {revealed === e.id ? e.secretMasked.replace(/•+/g, "4f21a9c8e07d1b63") : e.secretMasked}
                </code>
                <button
                  type="button"
                  onClick={() => setRevealed(revealed === e.id ? null : e.id)}
                  className="text-foreground-500 transition-colors hover:text-foreground-800"
                  aria-label="Toggle signing secret"
                >
                  <i className={`${revealed === e.id ? "ri-eye-off-line" : "ri-eye-line"} text-sm leading-none`}></i>
                </button>
              </span>
            </div>

            <div className="mt-3 flex items-center justify-end gap-2">
              <button
                type="button"
                className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-background-300 px-2.5 py-1.5 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100"
              >
                <i className="ri-send-plane-line text-sm leading-none"></i>
                Send test
              </button>
              <button
                type="button"
                onClick={() => removeEndpoint(e.id)}
                className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-accent-300 px-2.5 py-1.5 font-label text-xs text-accent-700 transition-colors hover:bg-accent-50"
              >
                <i className="ri-delete-bin-line text-sm leading-none"></i>
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="overflow-hidden rounded-lg border border-background-200 bg-background-50">
        <div className="flex items-center justify-between border-b border-background-200 px-4 py-3.5 md:px-5">
          <h3 className="font-heading text-sm font-semibold text-foreground-950">Recent deliveries</h3>
          <span className="rounded-full bg-secondary-100 px-2.5 py-1 font-label text-[11px] text-secondary-700">
            last 200
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-left">
            <thead>
              <tr className="border-b border-background-200 bg-background-100/60">
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500 md:px-5">Event</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Endpoint</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Status</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Attempts</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Time</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {webhookDeliveries.map((d) => (
                <tr key={d.id} className="border-b border-background-100 last:border-0">
                  <td className="px-4 py-3 md:px-5">
                    <span className="font-mono text-xs text-foreground-900">{d.event}</span>
                  </td>
                  <td className="px-4 py-3 font-mono text-[11px] text-foreground-600">{d.endpoint}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 font-label text-[11px] font-medium ${resultMeta[d.result].className}`}
                    >
                      {d.statusCode > 0 && <span className="font-mono">{d.statusCode}</span>}
                      {resultMeta[d.result].label}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-foreground-600">{d.attempts}</td>
                  <td className="px-4 py-3 font-label text-xs text-foreground-600">{d.at}</td>
                  <td className="px-4 py-3 text-right">
                    {d.result !== "SUCCESS" ? (
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-background-300 px-2.5 py-1.5 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100"
                      >
                        <i className="ri-refresh-line text-sm leading-none"></i>
                        Resend
                      </button>
                    ) : (
                      <i className="ri-check-line text-base leading-none text-primary-600"></i>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <Modal
        open={addOpen}
        onClose={() => {
          setAddOpen(false);
          setError("");
        }}
        title="Add webhook endpoint"
        subtitle="We POST signed JSON to your endpoint and retry failed deliveries with backoff."
        icon="ri-link-m"
        footer={
          <>
            <button
              type="button"
              onClick={() => {
                setAddOpen(false);
                setError("");
              }}
              className="inline-flex items-center justify-center whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleAdd}
              className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
            >
              <i className="ri-add-line text-base leading-none"></i>
              Add endpoint
            </button>
          </>
        }
      >
        <label className="font-label text-sm font-medium text-foreground-800">Endpoint URL</label>
        <input
          type="url"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            if (error) setError("");
          }}
          placeholder="https://api.your-domain.com/hooks/kyc"
          className="mt-1.5 w-full rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-sm text-foreground-900 placeholder:text-foreground-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100"
        />

        <div className="mt-4 font-label text-sm font-medium text-foreground-800">Subscribed events</div>
        <div className="mt-2 flex flex-col gap-2">
          {webhookEvents.map((ev) => (
            <button
              key={ev.id}
              type="button"
              onClick={() => {
                toggleEvent(ev.id);
                if (error) setError("");
              }}
              className={`flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors ${
                events.includes(ev.id)
                  ? "border-primary-300 bg-primary-50"
                  : "border-background-200 bg-background-50 hover:bg-background-100"
              }`}
            >
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                  events.includes(ev.id) ? "border-primary-500 bg-primary-500" : "border-background-400"
                }`}
              >
                {events.includes(ev.id) && <i className="ri-check-line text-[11px] leading-none text-background-50"></i>}
              </span>
              <span className="min-w-0">
                <span className="block font-mono text-xs text-foreground-900">{ev.id}</span>
                <span className="block font-label text-xs text-foreground-600">{ev.description}</span>
              </span>
            </button>
          ))}
        </div>
        {error && <p className="mt-2 font-label text-xs text-accent-700">{error}</p>}
      </Modal>
    </div>
  );
}