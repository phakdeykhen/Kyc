import type { ApiKey, WebhookDelivery, WebhookEndpoint } from "@/types/kyc";

export const apiKeys: ApiKey[] = [
  {
    id: "key_live_01",
    name: "Production · Mobile app",
    prefix: "rdy_live_9f2k************",
    scopes: ["session:create", "session:read", "result:read"],
    created: "2026-04-12",
    lastUsed: "2 minutes ago",
    status: "ACTIVE",
  },
  {
    id: "key_live_02",
    name: "Production · Partner portal",
    prefix: "rdy_live_74hq************",
    scopes: ["session:create", "session:read", "webhook:manage"],
    created: "2026-05-03",
    lastUsed: "3 hours ago",
    status: "ACTIVE",
  },
  {
    id: "key_test_01",
    name: "Sandbox · Integration tests",
    prefix: "rdy_test_2b8x************",
    scopes: ["session:create", "session:read", "result:read", "registry:read"],
    created: "2026-06-19",
    lastUsed: "Yesterday",
    status: "ACTIVE",
  },
  {
    id: "key_live_00",
    name: "Production · Legacy SDK",
    prefix: "rdy_live_1a0c************",
    scopes: ["session:create", "session:read"],
    created: "2025-11-02",
    lastUsed: "48 days ago",
    status: "REVOKED",
  },
];

export const webhookEndpoints: WebhookEndpoint[] = [
  {
    id: "wh_01",
    url: "https://api.acme-pay.com/hooks/kyc",
    events: ["session.completed", "session.manual_review", "decision.updated"],
    status: "ACTIVE",
    secretMasked: "whsec_••••••••••••4f21",
  },
  {
    id: "wh_02",
    url: "https://ops.acme-pay.com/events/identity",
    events: ["decision.updated", "session.expired"],
    status: "ACTIVE",
    secretMasked: "whsec_••••••••••••9ac8",
  },
  {
    id: "wh_03",
    url: "https://staging.acme-pay.com/hooks/kyc",
    events: ["session.completed"],
    status: "PAUSED",
    secretMasked: "whsec_••••••••••••01bd",
  },
];

export const webhookEvents: { id: string; label: string; description: string }[] = [
  { id: "session.created", label: "Session created", description: "A verification session was created for an applicant." },
  { id: "session.completed", label: "Session completed", description: "All capture steps are done and processing finished." },
  { id: "session.manual_review", label: "Session manual review", description: "The risk engine routed a session to the review queue." },
  { id: "session.expired", label: "Session expired", description: "A session exceeded its allowed completion window." },
  { id: "decision.updated", label: "Decision updated", description: "A reviewer changed the outcome of a session." },
  { id: "document.recapture_requested", label: "Recapture requested", description: "A document or selfie recapture was requested." },
];

export const webhookDeliveries: WebhookDelivery[] = [
  { id: "dl_8812", event: "session.completed", endpoint: "api.acme-pay.com/hooks/kyc", statusCode: 200, result: "SUCCESS", attempts: 1, at: "2026-10-05 09:13" },
  { id: "dl_8811", event: "decision.updated", endpoint: "ops.acme-pay.com/events/identity", statusCode: 200, result: "SUCCESS", attempts: 1, at: "2026-10-05 08:36" },
  { id: "dl_8810", event: "session.manual_review", endpoint: "api.acme-pay.com/hooks/kyc", statusCode: 502, result: "RETRYING", attempts: 3, at: "2026-10-05 08:22" },
  { id: "dl_8809", event: "session.completed", endpoint: "staging.acme-pay.com/hooks/kyc", statusCode: 0, result: "FAILED", attempts: 5, at: "2026-10-05 07:51" },
  { id: "dl_8808", event: "session.expired", endpoint: "ops.acme-pay.com/events/identity", statusCode: 200, result: "SUCCESS", attempts: 1, at: "2026-10-05 07:14" },
  { id: "dl_8807", event: "session.completed", endpoint: "api.acme-pay.com/hooks/kyc", statusCode: 200, result: "SUCCESS", attempts: 2, at: "2026-10-05 06:02" },
];

export const apiScopes: { id: string; label: string; description: string }[] = [
  { id: "session:create", label: "session:create", description: "Create new verification sessions." },
  { id: "session:read", label: "session:read", description: "Read session status and metadata." },
  { id: "result:read", label: "result:read", description: "Read extracted fields and decisions." },
  { id: "webhook:manage", label: "webhook:manage", description: "Create and update webhook endpoints." },
  { id: "registry:read", label: "registry:read", description: "Read the country and document-type registry." },
];

export const registryCountries: {
  code: string;
  name: string;
  flag: string;
  documentTypes: string;
  adapters: number;
  status: "GA" | "BETA" | "PLANNED";
}[] = [
  { code: "KH", name: "Cambodia", flag: "🇰🇭", documentTypes: "National ID, Passport, NSSF Card", adapters: 3, status: "GA" },
  { code: "INTL", name: "International / Other", flag: "🌍", documentTypes: "Passport (MRZ), Generic ID", adapters: 2, status: "GA" },
  { code: "TH", name: "Thailand", flag: "🇹🇭", documentTypes: "Thai National ID, Passport", adapters: 2, status: "BETA" },
  { code: "VN", name: "Vietnam", flag: "🇻🇳", documentTypes: "CCCD, Passport", adapters: 2, status: "BETA" },
  { code: "SG", name: "Singapore", flag: "🇸🇬", documentTypes: "NRIC, Passport", adapters: 2, status: "BETA" },
  { code: "PH", name: "Philippines", flag: "🇵🇭", documentTypes: "PhilSys ID, Passport", adapters: 1, status: "PLANNED" },
  { code: "ID", name: "Indonesia", flag: "🇮🇩", documentTypes: "e-KTP, Passport", adapters: 1, status: "PLANNED" },
];