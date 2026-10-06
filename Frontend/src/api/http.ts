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

function authHeaders(credential: Credential): Record<string, string> {
  const headers: Record<string, string> = { "X-Organization-ID": credential.organizationId };
  if (credential.kind === "apiKey") headers["X-API-Key"] = credential.key;
  else headers.Authorization = `Bearer ${credential.token}`;
  return headers;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  query?: Record<string, string | number | undefined | null>;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

async function send(credential: Credential, path: string, options: RequestOptions): Promise<Response> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const query = params.toString();
  const url = `${API_BASE}${path}${query ? `?${query}` : ""}`;
  const headers = authHeaders(credential);
  let body: FormData | string | undefined;
  if (options.form) body = options.form;
  else if (options.json !== undefined) {
    body = JSON.stringify(options.json);
    headers["Content-Type"] = "application/json";
  }
  let response: Response;
  try {
    response = await fetch(url, { method: options.method ?? "GET", headers, body, signal: options.signal });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "Could not reach the KYC service. Check your connection and try again.", null, null);
  }
  if (!response.ok) {
    let data: Record<string, unknown> | null = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail
      : Array.isArray(detail) ? "Some values are not valid." : `Request failed (${response.status}).`;
    throw new ApiError(response.status, message, (data?.reason_code as string) ?? null, data);
  }
  return response;
}

export async function apiJson<T>(credential: Credential, path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(credential, path, options);
  return (await response.json()) as T;
}

export async function apiBlob(credential: Credential, path: string, options: RequestOptions = {}): Promise<Blob> {
  const response = await send(credential, path, options);
  return response.blob();
}
