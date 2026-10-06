import { useState } from "react";
import Modal from "@/components/base/Modal";
import { apiKeys, apiScopes } from "@/mocks/developers";
import type { ApiKey } from "@/types/kyc";

function generateKey() {
  const rand = () => Math.random().toString(36).slice(2, 10);
  return `rdy_live_${rand()}${rand()}${rand()}${rand()}`.slice(0, 44);
}

export default function ApiKeysPanel() {
  const [keys, setKeys] = useState<ApiKey[]>(apiKeys);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newScopes, setNewScopes] = useState<string[]>(["session:create", "session:read"]);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const [copied, setCopied] = useState("");
  const [error, setError] = useState("");

  const copy = async (value: string, tag: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(tag);
      setTimeout(() => setCopied(""), 2000);
    } catch {
      setCopied("");
    }
  };

  const toggleScope = (id: string) => {
    setNewScopes((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));
  };

  const handleCreate = () => {
    if (!newName.trim()) {
      setError("Give the key a recognizable name.");
      return;
    }
    if (newScopes.length === 0) {
      setError("Select at least one scope.");
      return;
    }
    const full = generateKey();
    const key: ApiKey = {
      id: `key_${Date.now()}`,
      name: newName.trim(),
      prefix: `${full.slice(0, 17)}${"*".repeat(12)}`,
      scopes: [...newScopes],
      created: new Date().toISOString().slice(0, 10),
      lastUsed: "Never",
      status: "ACTIVE",
    };
    setKeys((prev) => [key, ...prev]);
    setRevealedKey(full);
    setCreateOpen(false);
    setNewName("");
    setNewScopes(["session:create", "session:read"]);
    setError("");
  };

  const handleRotate = (target: ApiKey) => {
    const full = generateKey();
    setKeys((prev) =>
      prev.map((k) =>
        k.id === target.id
          ? { ...k, prefix: `${full.slice(0, 17)}${"*".repeat(12)}`, lastUsed: "Just now", status: "ACTIVE" }
          : k,
      ),
    );
    setRevealedKey(full);
  };

  const handleRevoke = () => {
    if (!revokeTarget) return;
    setKeys((prev) => prev.map((k) => (k.id === revokeTarget.id ? { ...k, status: "REVOKED" } : k)));
    setRevokeTarget(null);
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 rounded-lg border border-background-200 bg-background-50 p-4 sm:flex-row sm:items-center sm:justify-between md:p-5">
        <div>
          <h2 className="font-heading text-base font-semibold text-foreground-950">API keys</h2>
          <p className="mt-0.5 font-label text-sm text-foreground-600">
            Keys authenticate your server calls to the verification API. Scope each key to the minimum it needs.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
        >
          <i className="ri-add-line text-base leading-none"></i>
          Create API key
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-background-200 bg-background-50">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] border-collapse text-left">
            <thead>
              <tr className="border-b border-background-200 bg-background-100/60">
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500 md:px-5">Name</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Key</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Scopes</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Created</th>
                <th className="px-4 py-2.5 font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Last used</th>
                <th className="px-4 py-2.5 text-right font-label text-[11px] font-medium uppercase tracking-wide text-foreground-500">Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id} className="border-b border-background-100 last:border-0">
                  <td className="px-4 py-3 md:px-5">
                    <div className="flex items-center gap-2">
                      <span className="font-label text-sm font-medium text-foreground-950">{k.name}</span>
                      {k.status === "REVOKED" && (
                        <span className="rounded-full bg-background-200 px-2 py-0.5 font-label text-[10px] text-foreground-600">
                          revoked
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded bg-background-100 px-2 py-1 font-mono text-[11px] text-foreground-700">
                      {k.prefix}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex max-w-[220px] flex-wrap gap-1">
                      {k.scopes.map((s) => (
                        <span key={s} className="rounded bg-secondary-100 px-1.5 py-0.5 font-mono text-[10px] text-secondary-800">
                          {s}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 font-label text-xs text-foreground-600">{k.created}</td>
                  <td className="px-4 py-3 font-label text-xs text-foreground-600">{k.lastUsed}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      {k.status === "ACTIVE" ? (
                        <>
                          <button
                            type="button"
                            onClick={() => handleRotate(k)}
                            className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-background-300 px-2.5 py-1.5 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100"
                          >
                            <i className="ri-refresh-line text-sm leading-none"></i>
                            Rotate
                          </button>
                          <button
                            type="button"
                            onClick={() => setRevokeTarget(k)}
                            className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-accent-300 px-2.5 py-1.5 font-label text-xs text-accent-700 transition-colors hover:bg-accent-50"
                          >
                            <i className="ri-forbid-2-line text-sm leading-none"></i>
                            Revoke
                          </button>
                        </>
                      ) : (
                        <span className="font-label text-xs text-foreground-400">No actions</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <Modal
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          setError("");
        }}
        title="Create API key"
        subtitle="The full key is shown only once, right after creation."
        icon="ri-key-2-line"
        footer={
          <>
            <button
              type="button"
              onClick={() => {
                setCreateOpen(false);
                setError("");
              }}
              className="inline-flex items-center justify-center whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleCreate}
              className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
            >
              <i className="ri-key-2-line text-base leading-none"></i>
              Create key
            </button>
          </>
        }
      >
        <label className="font-label text-sm font-medium text-foreground-800">Key name</label>
        <input
          type="text"
          value={newName}
          onChange={(e) => {
            setNewName(e.target.value);
            if (error) setError("");
          }}
          placeholder="e.g. Production · Risk engine"
          className="mt-1.5 w-full rounded-md border border-background-300 bg-background-50 px-3 py-2 font-label text-sm text-foreground-900 placeholder:text-foreground-400 focus:border-primary-400 focus:outline-none focus:ring-2 focus:ring-primary-100"
        />

        <div className="mt-4 font-label text-sm font-medium text-foreground-800">Scopes</div>
        <div className="mt-2 flex flex-col gap-2">
          {apiScopes.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                toggleScope(s.id);
                if (error) setError("");
              }}
              className={`flex items-start gap-3 rounded-md border px-3 py-2.5 text-left transition-colors ${
                newScopes.includes(s.id)
                  ? "border-primary-300 bg-primary-50"
                  : "border-background-200 bg-background-50 hover:bg-background-100"
              }`}
            >
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                  newScopes.includes(s.id) ? "border-primary-500 bg-primary-500" : "border-background-400"
                }`}
              >
                {newScopes.includes(s.id) && <i className="ri-check-line text-[11px] leading-none text-background-50"></i>}
              </span>
              <span className="min-w-0">
                <span className="block font-mono text-xs text-foreground-900">{s.label}</span>
                <span className="block font-label text-xs text-foreground-600">{s.description}</span>
              </span>
            </button>
          ))}
        </div>
        {error && <p className="mt-2 font-label text-xs text-accent-700">{error}</p>}
      </Modal>

      <Modal
        open={revealedKey !== null}
        onClose={() => setRevealedKey(null)}
        title="Copy your API key"
        subtitle="For security this key is only displayed once — store it in a secrets manager."
        icon="ri-key-2-line"
        footer={
          <button
            type="button"
            onClick={() => setRevealedKey(null)}
            className="inline-flex items-center justify-center whitespace-nowrap rounded-md bg-primary-500 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-primary-600"
          >
            I have stored it
          </button>
        }
      >
        <div className="flex items-center gap-2 rounded-md border border-background-200 bg-background-100 p-3">
          <code className="min-w-0 flex-1 truncate font-mono text-xs text-foreground-900">{revealedKey}</code>
          <button
            type="button"
            onClick={() => revealedKey && copy(revealedKey, "key")}
            className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-background-300 bg-background-50 px-2.5 py-1.5 font-label text-xs text-foreground-700 transition-colors hover:bg-background-100"
          >
            <i className={`${copied === "key" ? "ri-check-line" : "ri-file-copy-line"} text-sm leading-none`}></i>
            {copied === "key" ? "Copied" : "Copy"}
          </button>
        </div>
        <p className="mt-3 flex items-start gap-2 font-label text-xs text-foreground-600">
          <i className="ri-information-line mt-0.5 text-sm leading-none text-accent-600"></i>
          Never embed this key in client-side code. Call the API from your server and proxy requests where needed.
        </p>
      </Modal>

      <Modal
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        title="Revoke API key"
        subtitle="Any request using this key will start failing immediately."
        icon="ri-forbid-2-line"
        maxWidthClass="max-w-md"
        footer={
          <>
            <button
              type="button"
              onClick={() => setRevokeTarget(null)}
              className="inline-flex items-center justify-center whitespace-nowrap rounded-md border border-background-300 px-4 py-2.5 font-label text-sm text-foreground-700 transition-colors hover:bg-background-100"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleRevoke}
              className="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md bg-accent-600 px-4 py-2.5 font-label text-sm font-medium text-background-50 transition-colors hover:bg-accent-700"
            >
              <i className="ri-forbid-2-line text-base leading-none"></i>
              Revoke key
            </button>
          </>
        }
      >
        <p className="font-label text-sm text-foreground-700">
          You are about to revoke <span className="font-medium text-foreground-950">{revokeTarget?.name}</span>. This
          cannot be undone — you will need to create a new key.
        </p>
      </Modal>
    </div>
  );
}