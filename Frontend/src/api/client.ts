import { apiJson, type Credential } from "@/api/http";
import type {
  ApiKeyCreated, ApiKeyInfo, CaptureResult, ClientToken, ConsentRecord, CountryRegistry, DocumentSide, DocumentTypeCode,
  DocumentTypeInfo, ErasureReport, KycSession, LivenessChallenge, LivenessGuide, LivenessResult, NfcResult,
  OrganizationInfo, SelfieResult, SessionResult, VerificationLevel, VerifyResult, WebhookDeliveryInfo,
  WebhookEndpointInfo, WebhookEndpointWithSecret,
} from "@/api/types";

// The client API (spec §7–§27). Two credentials reach it:
//   ApiKey  – the customer's backend (here: the operator console, held in tab memory only)
//   Client  – a session client token on the applicant's device; it works for one session only
// The server enforces which credential may call what; these types only document it.

export type ApiKey = Extract<Credential, { kind: "apiKey" }>;
export type Client = Extract<Credential, { kind: "client" }>;
type SessionCaller = ApiKey | Client;

const session = (id: string) => `/v1/kyc/${encodeURIComponent(id)}`;

export interface NewSession {
  user_id: string;
  country: string;
  expected_document_type: DocumentTypeCode;
  verification_level: VerificationLevel;
}

export const kycApi = {
  // Backend (API key) ---------------------------------------------------------------------------
  createSession: (credential: ApiKey, body: NewSession, idempotencyKey: string) =>
    apiJson<KycSession>(credential, "/v1/kyc/sessions", { method: "POST", json: body, headers: { "Idempotency-Key": idempotencyKey } }),
  clientToken: (credential: ApiKey, id: string) =>
    apiJson<ClientToken>(credential, `${session(id)}/client-token`, { method: "POST" }),
  result: (credential: ApiKey, id: string) => apiJson<SessionResult>(credential, `${session(id)}/result`),
  verify: (credential: ApiKey, id: string) => apiJson<VerifyResult>(credential, `${session(id)}/verify`, { method: "POST" }),
  erase: (credential: ApiKey, id: string) => apiJson<ErasureReport>(credential, `${session(id)}/erase`, { method: "POST" }),
  documentTypes: (credential: ApiKey) =>
    apiJson<{ document_types: DocumentTypeInfo[] }>(credential, "/v1/document-types").then((data) => data.document_types),
  countries: (credential: ApiKey) => apiJson<CountryRegistry>(credential, "/v1/countries"),

  // Backend or device ---------------------------------------------------------------------------
  getSession: (credential: SessionCaller, id: string, signal?: AbortSignal) =>
    apiJson<KycSession>(credential, session(id), { signal }),
  consent: (credential: SessionCaller, id: string) =>
    apiJson<ConsentRecord>(credential, `${session(id)}/consent`, {
      method: "POST", json: { scope: "DOCUMENT_PROCESSING", granted: true } }),
  uploadDocument: (credential: SessionCaller, id: string, side: DocumentSide, file: Blob) => {
    const form = new FormData();
    form.append("side", side);
    form.append("file", file, "document.jpg");
    return apiJson<CaptureResult>(credential, `${session(id)}/documents`, { method: "POST", form });
  },
  uploadSelfie: (credential: SessionCaller, id: string, file: Blob) => {
    const form = new FormData();
    form.append("biometric_consent", "true");
    form.append("file", file, "selfie.jpg");
    return apiJson<SelfieResult>(credential, `${session(id)}/selfie`, { method: "POST", form });
  },
  livenessChallenge: (credential: SessionCaller, id: string) =>
    apiJson<LivenessChallenge>(credential, `${session(id)}/liveness/challenge`, { method: "POST", json: {} }),
  livenessGuide: (credential: SessionCaller, id: string, challenge: LivenessChallenge, step: number, frame: Blob,
                  baseline: Blob | null, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("challenge_id", challenge.challenge_id);
    form.append("nonce", challenge.nonce);
    form.append("step", String(step));
    form.append("frame", frame, "frame.jpg");
    if (baseline) form.append("baseline", baseline, "baseline.jpg");
    return apiJson<LivenessGuide>(credential, `${session(id)}/liveness/guide`, { method: "POST", form, signal });
  },
  submitLiveness: (credential: SessionCaller, id: string, challenge: LivenessChallenge, frames: Blob[], steps: number[]) => {
    const form = new FormData();
    form.append("challenge_id", challenge.challenge_id);
    form.append("nonce", challenge.nonce);
    form.append("frame_steps", steps.join(","));
    frames.forEach((frame, index) => form.append("frames", frame, `frame-${index}.jpg`));
    return apiJson<LivenessResult>(credential, `${session(id)}/liveness`, { method: "POST", form });
  },
  /** Browsers cannot reach a passport chip (Web NFC is NDEF only); a device without a reader reports so. */
  reportNfcUnavailable: (credential: SessionCaller, id: string, readStatus: "NOT_SUPPORTED" | "NOT_AVAILABLE") => {
    const form = new FormData();
    form.append("read_status", readStatus);
    return apiJson<NfcResult>(credential, `${session(id)}/nfc`, { method: "POST", form });
  },

  // Organization and keys -----------------------------------------------------------------------
  organization: (credential: ApiKey) => apiJson<OrganizationInfo>(credential, "/v1/organization"),
  listKeys: (credential: ApiKey) => apiJson<ApiKeyInfo[]>(credential, "/v1/api-keys"),
  createKey: (credential: ApiKey, body: { name: string; scopes: string[]; expires_in_days?: number;
                                          rate_limit_per_minute?: number; allowed_cidrs?: string[] }) =>
    apiJson<ApiKeyCreated>(credential, "/v1/api-keys", { method: "POST", json: body }),
  revokeKey: (credential: ApiKey, keyId: string) =>
    apiJson<ApiKeyInfo>(credential, `/v1/api-keys/${encodeURIComponent(keyId)}`, { method: "DELETE" }),

  // Webhooks ------------------------------------------------------------------------------------
  eventTypes: (credential: ApiKey) =>
    apiJson<{ event_types: Record<string, string> }>(credential, "/v1/webhooks/event-types").then((data) => data.event_types),
  listEndpoints: (credential: ApiKey) => apiJson<WebhookEndpointInfo[]>(credential, "/v1/webhooks"),
  createEndpoint: (credential: ApiKey, body: { url: string; event_types: string[]; description?: string }) =>
    apiJson<WebhookEndpointWithSecret>(credential, "/v1/webhooks", { method: "POST", json: body }),
  updateEndpoint: (credential: ApiKey, endpointId: string,
                   body: { url?: string; event_types?: string[]; description?: string | null }) =>
    apiJson<WebhookEndpointInfo>(credential, `/v1/webhooks/${encodeURIComponent(endpointId)}`, { method: "PATCH", json: body }),
  deleteEndpoint: (credential: ApiKey, endpointId: string) =>
    apiJson<WebhookEndpointInfo>(credential, `/v1/webhooks/${encodeURIComponent(endpointId)}`, { method: "DELETE" }),
  rotateSecret: (credential: ApiKey, endpointId: string) =>
    apiJson<WebhookEndpointWithSecret>(credential, `/v1/webhooks/${encodeURIComponent(endpointId)}/rotate-secret`, { method: "POST" }),
  sendTest: (credential: ApiKey, endpointId: string) =>
    apiJson<WebhookDeliveryInfo>(credential, `/v1/webhooks/${encodeURIComponent(endpointId)}/test`, { method: "POST" }),
  deliveries: (credential: ApiKey, endpointId: string, status?: "PENDING" | "DELIVERED" | "ABANDONED") =>
    apiJson<WebhookDeliveryInfo[]>(credential, `/v1/webhooks/${encodeURIComponent(endpointId)}/deliveries`,
                                   { query: { status, limit: 25 } }),
  redeliver: (credential: ApiKey, endpointId: string, deliveryId: string) =>
    apiJson<WebhookDeliveryInfo>(credential,
      `/v1/webhooks/${encodeURIComponent(endpointId)}/deliveries/${encodeURIComponent(deliveryId)}/redeliver`, { method: "POST" }),
};
