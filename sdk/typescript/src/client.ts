/**
 * Fetch-based client for the KYC API (Node 18+, browsers, React Native, edge runtimes).
 *
 * `KYCClient` runs on your server with an API key. `SessionClient` runs on the user's
 * device with a session client token from `issueClientToken`; never ship an API key to
 * a browser or app.
 */

export type VerificationLevel = "DOCUMENT_ONLY" | "DOCUMENT_FACE" | "DOCUMENT_FACE_LIVENESS" | "DOCUMENT_FACE_LIVENESS_NFC";
export type DocumentSide = "FRONT" | "BACK" | "DATA_PAGE";
export type Json = Record<string, any>;

export interface Session {
  session_id: string;
  organization_id: string;
  user_id: string;
  country: string;
  expected_document_type: string;
  verification_level: VerificationLevel;
  status: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
  version: number;
}

export interface ClientToken {
  session_id: string;
  client_token: string;
  scope: string;
  expires_at: string;
}

export class KYCAPIError extends Error {
  readonly status: number;
  readonly body: unknown;
  readonly requestId: string | null;
  readonly retryAfter: string | null;

  constructor(status: number, body: unknown, requestId: string | null, retryAfter: string | null) {
    const detail = body && typeof body === "object" && "detail" in body ? (body as Json).detail : body;
    super(`HTTP ${status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    this.name = "KYCAPIError";
    this.status = status;
    this.body = body;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
  }
}

type BinaryInput = Blob | Uint8Array;

function asBlob(data: BinaryInput, type: string): Blob {
  return data instanceof Blob ? data : new Blob([data as Uint8Array<ArrayBuffer>], { type });
}

interface Options {
  fetch?: typeof fetch;
}

class BaseClient {
  protected readonly baseUrl: string;
  protected readonly headers: Record<string, string>;
  private readonly fetcher: typeof fetch;

  constructor(baseUrl: string, organizationId: string, credentialHeaders: Record<string, string>, options: Options = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.headers = { "X-Organization-ID": organizationId, ...credentialHeaders };
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  protected async request<T>(method: string, path: string, init: { json?: unknown; form?: FormData; headers?: Record<string, string> } = {}): Promise<T> {
    const headers: Record<string, string> = { ...this.headers, ...(init.headers ?? {}) };
    let body: BodyInit | undefined;
    if (init.form) body = init.form;
    else if (init.json !== undefined || method === "POST" || method === "PATCH") {
      body = JSON.stringify(init.json ?? {});
      headers["Content-Type"] = "application/json";
    }
    const response = await this.fetcher(this.baseUrl + path, { method, headers, body });
    const text = await response.text();
    const parsed = (response.headers.get("content-type") ?? "").startsWith("application/json") && text ? JSON.parse(text) : text;
    if (!response.ok) {
      throw new KYCAPIError(response.status, parsed, response.headers.get("x-request-id"), response.headers.get("retry-after"));
    }
    return parsed as T;
  }

  getSession(sessionId: string): Promise<Session> {
    return this.request("GET", `/v1/kyc/${sessionId}`);
  }

  uploadDocument(sessionId: string, side: DocumentSide, image: BinaryInput, filename = "document.jpg"): Promise<Json> {
    const form = new FormData();
    form.append("side", side);
    form.append("file", asBlob(image, "image/jpeg"), filename);
    return this.request("POST", `/v1/kyc/${sessionId}/documents`, { form });
  }

  /** Only pass biometricConsent = true after the person has actually agreed. */
  uploadSelfie(sessionId: string, image: BinaryInput, biometricConsent: boolean, filename = "selfie.jpg"): Promise<Json> {
    const form = new FormData();
    form.append("biometric_consent", biometricConsent ? "true" : "false");
    form.append("file", asBlob(image, "image/jpeg"), filename);
    return this.request("POST", `/v1/kyc/${sessionId}/selfie`, { form });
  }

  livenessChallenge(sessionId: string): Promise<Json> {
    return this.request("POST", `/v1/kyc/${sessionId}/liveness/challenge`);
  }

  /** frames: raw (unmirrored) JPEG frames, each with the challenge step it belongs to. */
  submitLiveness(sessionId: string, challengeId: string, nonce: string, frames: { image: BinaryInput; step: number }[]): Promise<Json> {
    const form = new FormData();
    form.append("challenge_id", challengeId);
    form.append("nonce", nonce);
    form.append("frame_steps", frames.map((frame) => frame.step).join(","));
    frames.forEach((frame, index) => form.append("frames", asBlob(frame.image, "image/jpeg"), `frame-${index}.jpg`));
    return this.request("POST", `/v1/kyc/${sessionId}/liveness`, { form });
  }

  nfcChallenge(sessionId: string): Promise<Json> {
    return this.request("POST", `/v1/kyc/${sessionId}/nfc/challenge`);
  }

  submitNfc(sessionId: string, input: {
    readStatus?: "READ" | "NOT_SUPPORTED" | "NOT_AVAILABLE" | "FAILED";
    accessProtocol?: "PACE" | "BAC";
    challengeId?: string;
    aaSignatureHex?: string;
    dataGroups?: Partial<Record<"sod" | "dg1" | "dg2" | "dg15", BinaryInput>>;
  }): Promise<Json> {
    const form = new FormData();
    form.append("read_status", input.readStatus ?? "READ");
    if (input.accessProtocol) form.append("access_protocol", input.accessProtocol);
    if (input.challengeId) form.append("challenge_id", input.challengeId);
    if (input.aaSignatureHex) form.append("aa_signature", input.aaSignatureHex);
    for (const [name, data] of Object.entries(input.dataGroups ?? {})) {
      if (data) form.append(name, asBlob(data, "application/octet-stream"), `${name}.bin`);
    }
    return this.request("POST", `/v1/kyc/${sessionId}/nfc`, { form });
  }
}

/** Device-side client: a session client token can capture evidence for, and read the status of, one session. */
export class SessionClient extends BaseClient {
  constructor(baseUrl: string, clientToken: string, organizationId: string, options: Options = {}) {
    super(baseUrl, organizationId, { Authorization: `Bearer ${clientToken}` }, options);
  }
}

/** Server-side client holding an API key. */
export class KYCClient extends BaseClient {
  constructor(baseUrl: string, apiKey: string, organizationId: string, options: Options = {}) {
    super(baseUrl, organizationId, { "X-API-Key": apiKey }, options);
  }

  /** Pass an idempotencyKey (8–128 chars) so that retrying after a timeout cannot create a duplicate session. */
  createSession(input: { userId: string; country: string; expectedDocumentType: string; verificationLevel?: VerificationLevel; idempotencyKey?: string }): Promise<Session> {
    return this.request("POST", "/v1/kyc/sessions", {
      json: {
        user_id: input.userId,
        country: input.country,
        expected_document_type: input.expectedDocumentType,
        verification_level: input.verificationLevel ?? "DOCUMENT_FACE_LIVENESS",
      },
      headers: input.idempotencyKey ? { "Idempotency-Key": input.idempotencyKey } : undefined,
    });
  }

  issueClientToken(sessionId: string): Promise<ClientToken> {
    return this.request("POST", `/v1/kyc/${sessionId}/client-token`);
  }

  getResult(sessionId: string): Promise<Json> {
    return this.request("GET", `/v1/kyc/${sessionId}/result`);
  }

  verify(sessionId: string): Promise<Json> {
    return this.request("POST", `/v1/kyc/${sessionId}/verify`);
  }

  organization(): Promise<Json> {
    return this.request("GET", "/v1/organization");
  }

  documentTypes(): Promise<Json> {
    return this.request("GET", "/v1/document-types");
  }

  countries(): Promise<Json> {
    return this.request("GET", "/v1/countries");
  }

  listApiKeys(): Promise<Json[]> {
    return this.request("GET", "/v1/api-keys");
  }

  createApiKey(input: { name: string; scopes: string[]; expiresInDays?: number; rateLimitPerMinute?: number }): Promise<Json> {
    return this.request("POST", "/v1/api-keys", {
      json: { name: input.name, scopes: input.scopes, expires_in_days: input.expiresInDays, rate_limit_per_minute: input.rateLimitPerMinute },
    });
  }

  revokeApiKey(keyId: string): Promise<Json> {
    return this.request("DELETE", `/v1/api-keys/${keyId}`);
  }

  /** The response carries `secret` once; store it to verify signatures. */
  createWebhook(input: { url: string; eventTypes?: string[]; description?: string }): Promise<Json> {
    return this.request("POST", "/v1/webhooks", { json: { url: input.url, event_types: input.eventTypes ?? [], description: input.description } });
  }

  listWebhooks(includeDeleted = false): Promise<Json[]> {
    return this.request("GET", `/v1/webhooks${includeDeleted ? "?include_deleted=true" : ""}`);
  }

  updateWebhook(endpointId: string, changes: { url?: string; event_types?: string[]; description?: string }): Promise<Json> {
    return this.request("PATCH", `/v1/webhooks/${endpointId}`, { json: changes });
  }

  deleteWebhook(endpointId: string): Promise<Json> {
    return this.request("DELETE", `/v1/webhooks/${endpointId}`);
  }

  rotateWebhookSecret(endpointId: string): Promise<Json> {
    return this.request("POST", `/v1/webhooks/${endpointId}/rotate-secret`);
  }

  testWebhook(endpointId: string): Promise<Json> {
    return this.request("POST", `/v1/webhooks/${endpointId}/test`);
  }

  listDeliveries(endpointId: string, options: { status?: "PENDING" | "DELIVERED" | "ABANDONED"; limit?: number } = {}): Promise<Json[]> {
    const query = new URLSearchParams({ limit: String(options.limit ?? 25), ...(options.status ? { status: options.status } : {}) });
    return this.request("GET", `/v1/webhooks/${endpointId}/deliveries?${query}`);
  }

  redeliver(endpointId: string, deliveryId: string): Promise<Json> {
    return this.request("POST", `/v1/webhooks/${endpointId}/deliveries/${deliveryId}/redeliver`);
  }
}
