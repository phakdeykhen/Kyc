import { apiBlob, apiJson, type Credential } from "@/api/http";
import type {
  DecisionResponse, Page, QueueItem, ReviewAction, ReviewCase, ReviewerProfile, SessionList, SessionStatus,
} from "@/api/types";

type Reviewer = Extract<Credential, { kind: "reviewer" }>;

export const reviewApi = {
  me: (credential: Reviewer) => apiJson<ReviewerProfile>(credential, "/v1/review/me"),

  queue: (credential: Reviewer, options: { limit?: number; offset?: number; order?: "oldest" | "newest" } = {}) =>
    apiJson<Page<QueueItem>>(credential, "/v1/review/queue", { query: { limit: 100, order: "oldest", ...options } }),

  sessions: (credential: Reviewer,
             options: { limit?: number; offset?: number; status?: SessionStatus | ""; userId?: string } = {}) =>
    apiJson<SessionList>(credential, "/v1/review/sessions", {
      query: { limit: options.limit ?? 50, offset: options.offset ?? 0, status: options.status, user_id: options.userId },
    }),

  getCase: (credential: Reviewer, sessionId: string) =>
    apiJson<ReviewCase>(credential, `/v1/review/${encodeURIComponent(sessionId)}`),

  image: (credential: Reviewer, sessionId: string, imageId: string) =>
    apiBlob(credential, `/v1/review/${encodeURIComponent(sessionId)}/images/${encodeURIComponent(imageId)}`),

  decide: (credential: Reviewer, sessionId: string,
           decision: { action: ReviewAction; reason_code: string; note: string; expected_version: number }) =>
    apiJson<DecisionResponse>(credential, `/v1/review/${encodeURIComponent(sessionId)}/decision`,
                              { method: "POST", json: decision }),
};
