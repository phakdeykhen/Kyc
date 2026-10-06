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
