import type { Credential } from "./http";
import { apiJson } from "./http";

export type FaceCredential = Extract<Credential, { kind: "face" }>;
export interface Capabilities {
  ready: boolean;
  actor_kind: "PERSON" | "KIOSK" | "OPERATOR";
  kyc_status: string | null;
  enrollment_allowed: boolean;
  retention_days: number | null;
  backup_retention_days: number | null;
  notice_version: string | null;
  permitted_fields: string[];
  purposes: string[];
}
export interface Enrollment { status: string; active: boolean; expires_at: string | null }
export interface FaceCapture {
  session_id: string; expires_at: string; ticket: string;
  step: string | null; step_index: number; steps_total: number;
  held: number; required: number; state: string; instructions: string[];
  outcome?: string; message?: string;
}
export interface FaceResult {
  outcome?: string; status?: string; message: string;
  recognition_reference?: string;
}
const path = (enroll: boolean) => enroll ? "/v1/face-enrollments/sessions" : "/v1/recognition/sessions";
export const faceIdentityApi = {
  capabilities: (credential: FaceCredential) => apiJson<Capabilities>(credential, "/v1/face-recognition/capabilities"),
  enrollment: (credential: FaceCredential) => apiJson<Enrollment>(credential, "/v1/me/face-enrollment"),
  withdraw: (credential: FaceCredential) => apiJson<{ message: string }>(credential, "/v1/me/face-enrollment", { method: "DELETE" }),
  create: (credential: FaceCredential, enroll: boolean, notice: string | null, purpose: string, signal: AbortSignal) =>
    apiJson<FaceCapture>(credential, path(enroll), { method: "POST", signal, timeoutMs: 10000,
      json: enroll ? { consent: true, notice_version: notice } : { purpose } }),
  frame: (credential: FaceCredential, enroll: boolean, session: FaceCapture, blob: Blob, signal: AbortSignal) => {
    const form = new FormData();
    form.append("ticket", session.ticket);
    form.append("frame", blob, "live.jpg");
    return apiJson<FaceCapture>(credential, `${path(enroll)}/${encodeURIComponent(session.session_id)}/frames`,
      { method: "POST", form, signal, timeoutMs: 10000 });
  },
  complete: (credential: FaceCredential, enroll: boolean, id: string, key: string, signal: AbortSignal) =>
    apiJson<FaceResult>(credential, `${path(enroll)}/${encodeURIComponent(id)}/complete`,
      { method: "POST", headers: { "Idempotency-Key": key }, json: {}, signal, timeoutMs: 30000 }),
  result: (credential: FaceCredential, id: string, signal: AbortSignal) =>
    apiJson<FaceResult>(credential, `/v1/recognition/sessions/${encodeURIComponent(id)}/result`, { signal, timeoutMs: 10000 }),
  profile: (credential: FaceCredential, id: string, reference: string, key: string, signal: AbortSignal) =>
    apiJson<{ profile: Record<string, string>; clear_after_seconds: number }>(credential,
      `/v1/recognition/sessions/${encodeURIComponent(id)}/profile`, { method: "POST", signal, timeoutMs: 10000,
        headers: { "Idempotency-Key": key }, json: { recognition_reference: reference } }),
};
