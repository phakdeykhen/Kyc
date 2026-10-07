// One fetch wrapper for the KYC API. Every request names the credential it uses, because the API
// keeps them apart: reviewer tokens only reach /v1/review, API keys only the client API, and a
// session client token only its own session. In development Vite proxies /v1 to the backend.

export type Credential =
  | { kind: "reviewer"; organizationId: string; token: string }
  | { kind: "apiKey"; organizationId: string; key: string }
  | { kind: "client"; organizationId: string; token: string };

export class ApiError extends Error {
  readonly status: number;
  readonly reasonCode: string | null;
  readonly body: Record<string, unknown> | null;

  constructor(status: number, message: string, reasonCode: string | null, body: Record<string, unknown> | null) {
    super(message);
    this.status = status;
    this.reasonCode = reasonCode;
    this.body = body;
  }
}

const API_BASE = (import.meta.env.VITE_KYC_API_BASE as string | undefined) ?? "";
const DEFAULT_TIMEOUT_MS = 60000;

function authHeaders(credential: Credential): Record<string, string> {
  const headers: Record<string, string> = { "X-Organization-ID": credential.organizationId };
  if (credential.kind === "apiKey") headers["X-API-Key"] = credential.key;
  else headers.Authorization = `Bearer ${credential.token}`;
  return headers;
}

// FastAPI validation errors: [{loc: ["body", "user_id"], msg: "..."}, ...]
function validationMessage(detail: unknown[]): string {
  const first = detail[0] as { loc?: unknown[]; msg?: string } | undefined;
  if (!first?.msg) return "Some values are not valid.";
  const field = (first.loc ?? []).filter((part) => part !== "body").join(".");
  return field ? `${field}: ${first.msg.replace(/^Value error, /, "")}` : first.msg;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  headers?: Record<string, string>;
  query?: Record<string, string | number | undefined | null>;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  timeoutMs?: number;
}

async function send<T>(credential: Credential, path: string, options: RequestOptions,
                       readResponse: (response: Response) => Promise<T>): Promise<T> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const query = params.toString();
  const url = `${API_BASE}${path}${query ? `?${query}` : ""}`;
  const headers = { ...authHeaders(credential), ...(options.headers ?? {}) };
  let body: FormData | string | undefined;
  if (options.form) body = options.form;
  else if (options.json !== undefined) {
    body = JSON.stringify(options.json);
    headers["Content-Type"] = "application/json";
  }
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = timeoutMs ? new AbortController() : null;
  let timedOut = false;
  const cancel = () => controller?.abort();
  if (options.signal?.aborted) cancel();
  options.signal?.addEventListener("abort", cancel, { once: true });
  const timer = timeoutMs ? setTimeout(() => { timedOut = true; controller?.abort(); }, timeoutMs) : null;
  try {
    const response = await fetch(url, { method: options.method ?? "GET", headers, body, signal: controller?.signal ?? options.signal });
    if (!response.ok) {
      let data: Record<string, unknown> | null = null;
      try {
        data = await response.json();
      } catch (error) {
        if (timedOut || (error as Error).name === "AbortError") throw error;
      }
      const detail = data?.detail;
      const message = typeof detail === "string" ? detail
        : Array.isArray(detail) ? validationMessage(detail) : `Request failed (${response.status}).`;
      throw new ApiError(response.status, message, (data?.reason_code as string) ?? null, data);
    }
    // Keep timeout/cancellation active until the body is read, including a stalled JSON stream.
    return await readResponse(response);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timedOut) throw new ApiError(0, "The connection took too long. Try again.", "REQUEST_TIMEOUT", null);
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "Could not reach the KYC service. Check your connection and try again.", null, null);
  } finally {
    if (timer !== null) clearTimeout(timer);
    options.signal?.removeEventListener("abort", cancel);
  }
}

export async function apiJson<T>(credential: Credential, path: string, options: RequestOptions = {}): Promise<T> {
  return send<T>(credential, path, options, response => response.json());
}

export async function apiBlob(credential: Credential, path: string, options: RequestOptions = {}): Promise<Blob> {
  return send<Blob>(credential, path, options, response => response.blob());
}
