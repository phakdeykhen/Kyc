import type { Client } from "@/api/client";

// The applicant link carries the organization and the session client token in the URL fragment,
// which browsers never send to a server. On arrival the page moves them into sessionStorage (this
// tab only, gone when it closes) so a reload mid-capture does not lose the session, and removes them
// from the address bar and history.

const storageKey = (sessionId: string) => `kyc-client:${sessionId}`;

export function applicantLink(sessionId: string, organizationId: string, clientToken: string): string {
  const base = new URL(import.meta.env.BASE_URL || "/", window.location.origin);
  const url = new URL(`verify/${encodeURIComponent(sessionId)}`, base);
  url.hash = new URLSearchParams({ org: organizationId, token: clientToken }).toString();
  return url.toString();
}

export function readApplicantCredential(sessionId: string): Client | null {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const org = fragment.get("org");
  const token = fragment.get("token");
  if (org && token) {
    try {
      sessionStorage.setItem(storageKey(sessionId), JSON.stringify({ org, token }));
    } catch {
      // Storage blocked: the credential still works for this page view.
    }
    window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    return { kind: "client", organizationId: org, token };
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey(sessionId)) ?? "null") as { org?: string; token?: string } | null;
    if (saved?.org && saved.token) return { kind: "client", organizationId: saved.org, token: saved.token };
  } catch {
    // ignore unreadable storage
  }
  return null;
}

export function forgetApplicantCredential(sessionId: string): void {
  try {
    sessionStorage.removeItem(storageKey(sessionId));
  } catch {
    // ignore
  }
}
