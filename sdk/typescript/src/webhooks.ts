/**
 * Verify the platform's webhook signatures (Web Crypto: Node 18+, Deno, Bun, edge runtimes).
 *
 *   const event = await verifyWebhook(rawBody, request.headers.get("KYC-Signature"), secret);
 *
 * Pass the raw request body, not re-serialized JSON. Then deduplicate on `event.id`:
 * deliveries are at-least-once, and a retry carries the same event ID.
 */

export const SIGNATURE_HEADER = "KYC-Signature";
export const DEFAULT_TOLERANCE_SECONDS = 300;

export interface WebhookEvent {
  id: string;
  type: string;
  api_version: string;
  created_at: string;
  organization_id: string;
  data: Record<string, unknown>;
}

export class WebhookVerificationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebhookVerificationError";
  }
}

const encoder = new TextEncoder();

function toBytes(payload: string | Uint8Array): Uint8Array {
  return typeof payload === "string" ? encoder.encode(payload) : payload;
}

function hex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a.charCodeAt(index) ^ b.charCodeAt(index);
  return difference === 0;
}

export async function computeSignature(secret: string, timestamp: number, payload: string | Uint8Array): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const body = toBytes(payload);
  const prefix = encoder.encode(`${timestamp}.`);
  const message = new Uint8Array(prefix.length + body.length);
  message.set(prefix);
  message.set(body, prefix.length);
  return hex(await crypto.subtle.sign("HMAC", key, message));
}

export async function verifyWebhook(
  payload: string | Uint8Array,
  signatureHeader: string | null | undefined,
  secret: string,
  options: { toleranceSeconds?: number; now?: number } = {},
): Promise<WebhookEvent> {
  let timestamp: number | undefined;
  const signatures: string[] = [];
  for (const item of (signatureHeader ?? "").split(",")) {
    const [name, value = ""] = item.trim().split("=", 2);
    if (name === "t" && /^\d+$/.test(value)) timestamp = Number(value);
    else if (name === "v1" && value) signatures.push(value);
  }
  if (timestamp === undefined || signatures.length === 0) throw new WebhookVerificationError("Malformed signature header.");
  const now = options.now ?? Math.floor(Date.now() / 1000);
  if (Math.abs(now - timestamp) > (options.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS)) {
    throw new WebhookVerificationError("Signature timestamp is outside the tolerance (possible replay).");
  }
  const expected = await computeSignature(secret, timestamp, payload);
  if (!signatures.some((candidate) => constantTimeEqual(expected, candidate))) {
    throw new WebhookVerificationError("Signature does not match.");
  }
  const text = typeof payload === "string" ? payload : new TextDecoder().decode(payload);
  return JSON.parse(text) as WebhookEvent;
}
