// Response shapes of the KYC API, as the backend returns them (snake_case kept on purpose).

export type SessionStatus =
  | "CREATED"
  | "DOCUMENT_REQUIRED"
  | "DOCUMENT_PROCESSING"
  | "SELFIE_REQUIRED"
  | "LIVENESS_REQUIRED"
  | "NFC_REQUIRED"
  | "PROCESSING"
  | "MANUAL_REVIEW"
  | "VERIFIED"
  | "REJECTED"
  | "EXPIRED";

export const SESSION_STATUSES: SessionStatus[] = [
  "CREATED", "DOCUMENT_REQUIRED", "DOCUMENT_PROCESSING", "SELFIE_REQUIRED", "LIVENESS_REQUIRED", "NFC_REQUIRED",
  "PROCESSING", "MANUAL_REVIEW", "VERIFIED", "REJECTED", "EXPIRED",
];

export type VerificationLevel = "DOCUMENT_ONLY" | "DOCUMENT_FACE" | "DOCUMENT_FACE_LIVENESS" | "DOCUMENT_FACE_LIVENESS_NFC";
export type CheckValue = "PASS" | "REVIEW" | "FAIL" | "NOT_APPLICABLE" | "UNAVAILABLE" | string;
export type ReviewAction = "APPROVE" | "REJECT" | "REQUEST_RECAPTURE";
export type ReviewerRole = "REVIEWER" | "AUDITOR";

export interface ReviewerProfile {
  reviewer_id: string;
  display_name: string;
  role: ReviewerRole;
  organization_id: string;
  permissions: string[];
}

export interface Page<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export interface QueueItem {
  session_id: string;
  version: number;
  verification_level: VerificationLevel;
  expected_document_type: string;
  country: string;
  in_review_since: string;
  waiting_minutes: number;
  expires_at: string;
  reason_codes: string[];
  high_signals: string[];
  medium_signals: string[];
}

export interface SessionListItem {
  session_id: string;
  user_id: string;
  status: SessionStatus;
  expected_document_type: string;
  country: string;
  verification_level: VerificationLevel;
  created_at: string;
  updated_at: string;
  expires_at: string;
  expired: boolean;
  erased: boolean;
}

export interface SessionList extends Page<SessionListItem> {
  counts: Record<SessionStatus, number>;
}

export interface CaseImage {
  image_id: string;
  kind: string; // DOCUMENT_FRONT | DOCUMENT_BACK | DOCUMENT_DATA_PAGE | SELFIE
  media_type: string;
  available_until: string;
}

export interface CaseField {
  name: string;
  confidence: number;
  source: string;
  side: string | null;
  flags: string[];
  value: string | null; // null when the role may not see identity values
}

export interface CaseSignal {
  signal: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  category: string;
  detector: string | null;
  fields: string[];
  sources: string[];
  details: Record<string, unknown>;
}

export interface CaseHistoryEntry {
  action: ReviewAction;
  reason_code: string;
  decided_at: string;
  reviewer: string;
  note: string | null;
}

export interface ReviewCase {
  session: {
    session_id: string;
    status: SessionStatus;
    version: number;
    verification_level: VerificationLevel;
    country: string;
    expected_document_type: string;
    user_id: string;
    created_at: string;
    updated_at: string;
    expires_at: string;
  };
  permissions: string[];
  decision_options: Partial<Record<ReviewAction, string[]>>;
  risk: null | {
    decision: "PASS" | "REVIEW" | "FAIL";
    reason_codes: string[];
    policy_version: string;
    assessed_at: string;
    trace: { outcome: string; reason: string; rule: string }[];
    authenticity_sources: string[];
  };
  checks: Record<string, CheckValue>;
  government_verification?: GovernmentVerification | null;
  fraud_signals: CaseSignal[];
  document: null | {
    type: string;
    issuing_country: string | null;
    classification_confidence: number | null;
    document_number: string | null;
  };
  fields: CaseField[];
  mrz: null | { format: string; valid: boolean; check_digits: Record<string, boolean>; field_consistency: Record<string, unknown> };
  barcodes: { symbology: string; format_valid: boolean; signature_present: boolean; signature_valid: boolean | null; fields: Record<string, unknown> }[];
  nfc: null | { status: string; passive_authentication: string | null; active_authentication: string | null; reason_codes: string[] };
  face_comparisons: { reference: string; score: number; result: string; policy_version: string; calibrated: boolean }[];
  liveness: null | { result: string; score: number; attack_type: string | null; reason_codes: string[]; calibrated: boolean | null };
  images: CaseImage[];
  history: CaseHistoryEntry[];
}

export interface DecisionResponse {
  session_id: string;
  status: SessionStatus;
  version: number;
  action: ReviewAction;
  reason_code: string;
}

// ---- Client API (backend / device) ----

export type DocumentTypeCode =
  | "KH_NATIONAL_ID" | "KH_PASSPORT" | "KH_NSSF" | "PASSPORT" | "NATIONAL_ID" | "RESIDENCE_CARD" | "DRIVING_LICENSE" | "UNKNOWN";
export type DocumentSide = "FRONT" | "BACK" | "DATA_PAGE";

export interface KycSession {
  session_id: string;
  organization_id: string;
  user_id: string;
  country: string;
  expected_document_type: DocumentTypeCode;
  verification_level: VerificationLevel;
  status: SessionStatus;
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

export interface ConsentRecord {
  session_id: string;
  scope: string;
  policy_version: string;
  granted_at: string;
}

export interface CaptureQuality {
  blur_score: number;
  glare_score: number;
  brightness_score: number;
  shadow_score: number;
  document_coverage: number;
  perspective_score: number;
  resolution_score: number;
  overall_quality: number;
  policy_version: string;
}

export interface CaptureResult {
  session_id: string;
  status: SessionStatus;
  side: DocumentSide;
  capture_status: "ACCEPTED" | "RECAPTURE";
  quality: CaptureQuality;
  reason_codes: string[];
  instructions: string[];
  sides: Record<string, "ACCEPTED" | "REQUIRED">;
  next_step: string;
  attempts_remaining: number;
}

export interface FaceComparison {
  score: number;
  metric: "COSINE_SIMILARITY";
  result: CheckValue;
  policy_version: string;
  model_name: string;
  model_version: string;
  calibrated: boolean;
}

export interface SelfieResult {
  session_id: string;
  status: SessionStatus;
  capture_status: "ACCEPTED" | "RECAPTURE";
  quality: Record<string, number | string | string[]>;
  reason_codes: string[];
  instructions: string[];
  next_step: string;
  attempts_remaining: number;
  comparison: FaceComparison | null;
}

export interface LivenessChallenge {
  session_id: string;
  challenge_id: string;
  nonce: string;
  steps: { index: number; step: string; instruction: string }[];
  expires_at: string;
  frames: { min: number; max: number };
  attempts_remaining: number;
}

export interface LivenessGuide {
  step: string;
  face: "OK" | "NO_FACE" | "MULTIPLE_FACES" | "UNCLEAR" | string;
  state: "DONE" | "CENTERED" | "WRONG_DIRECTION" | string | null;
  progress: number;
}

export interface LivenessResult {
  session_id: string;
  status: SessionStatus;
  result: "PASS" | "REVIEW" | "FAIL" | string;
  reason_codes: string[];
  instructions: string[];
  retry_allowed: boolean;
  attempts_remaining: number;
  calibrated: boolean;
}

export interface FacePosition {
  face: string;
  state: "READY" | "POSITIONING";
  instructions: string[];
  attempts_remaining: number;
}

export interface NfcResult {
  session_id: string;
  status: SessionStatus;
  [key: string]: unknown;
}

export interface RiskDecisionView {
  result: "PASS" | "REVIEW" | "FAIL";
  reason_codes: string[];
  policy_version: string;
  assessed_at: string;
}

export interface VerifyResult {
  session_id: string;
  status: SessionStatus;
  decision: RiskDecisionView;
}

export interface GovernmentVerification {
  provider: "VERIFY_GOV_KH";
  status: "PENDING" | "LINK_AVAILABLE" | "LINK_RESTRICTED" | "NO_OFFICIAL_QR" | "UNAVAILABLE" | "LINK_EXPIRED" | "ERASED";
  verified: false;
  verification_url: string | null;
  portal_url: "https://verify.gov.kh/";
}

export interface SessionResult {
  session_id: string;
  status: SessionStatus;
  document: null | {
    country: string | null;
    type: DocumentTypeCode;
    document_number_masked: string | null;
    expiry_status: "VALID" | "EXPIRED" | "UNKNOWN" | "NOT_APPLICABLE";
  };
  identity: null | {
    full_name: string | null;
    full_name_local: string | null;
    date_of_birth: string | null;
    sex: string | null;
    nationality: string | null;
  };
  identity_masked: boolean;
  government_verification?: GovernmentVerification | null;
  mrz: null | {
    format: string;
    mrz_valid: boolean;
    check_digit_results: Record<string, { expected: string; computed: string; valid: boolean }>;
    field_consistency: Record<string, string>;
  };
  face_comparison: FaceComparison | null;
  checks: Record<string, CheckValue>;
  review_flags: string[];
  fraud_signals: { signal: string; severity: "LOW" | "MEDIUM" | "HIGH"; category: string }[];
  decision: RiskDecisionView | null;
  review: null | { action: ReviewAction; reason_code: string; decided_at: string };
  erased_at: string | null;
}

export interface ErasureReport {
  session_id: string;
  status: SessionStatus;
  erased_at: string;
  already_erased: boolean;
  deleted: Record<string, number>;
  encrypted_objects: number;
}

export interface DocumentTypeInfo {
  type: DocumentTypeCode;
  country: string | null;
  required_sides: DocumentSide[];
  capture_quality_gate: string;
  adapter_status: "AVAILABLE" | "PLANNED";
}

export interface CountryRegistry {
  countries: string[];
  verification_adapters_available: string[];
  any_country_document_types: DocumentTypeCode[];
}

export interface OrganizationInfo {
  organization_id: string;
  name: string;
  active: boolean;
  retention: { pii_retention_days: number; capture_retention_hours: number; template_retention_hours: number };
  credential: { type: string; key_id: string | null; scopes: string[]; rate_limit_per_minute: number | null };
  available_scopes: Record<string, string>;
}

export interface ApiKeyInfo {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  rate_limit_per_minute: number;
  allowed_cidrs: string[];
  status: string;
  created_at: string;
  created_by: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
}

export interface ApiKeyCreated extends ApiKeyInfo {
  api_key: string;
}

export interface WebhookEndpointInfo {
  id: string;
  url: string;
  description: string | null;
  event_types: string[];
  active: boolean;
  created_at: string;
  disabled_at: string | null;
  consecutive_failures: number;
  previous_secret_expires_at: string | null;
}

export interface WebhookEndpointWithSecret extends WebhookEndpointInfo {
  secret: string;
}

export interface WebhookDeliveryInfo {
  id: string;
  event_id: string;
  event_type: string;
  session_id: string | null;
  status: "PENDING" | "DELIVERED" | "ABANDONED" | string;
  attempts: number;
  next_attempt_at: string;
  last_attempt_at: string | null;
  last_status_code: number | null;
  last_error: string | null;
  delivered_at: string | null;
  created_at: string;
}
