export type KYCStatus =
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

export type DecisionResult = "PASS" | "REVIEW" | "FAIL";

export type CheckResult = "PASS" | "REVIEW" | "FAIL" | "NOT_APPLICABLE";

export type DocumentSide = "FRONT" | "BACK";

export type VerificationLevel = "BASIC" | "STANDARD" | "ENHANCED";

export interface Country {
  code: string;
  name: string;
  flag: string;
  region: string;
}

export interface DocumentTypeOption {
  id: string;
  label: string;
  family: string;
  country: string;
  sides: DocumentSide[];
  mrz: boolean;
  qr: boolean;
  nfc: boolean;
  adapter: string;
  description: string;
  locales: string;
}

export interface QualityMetric {
  key: string;
  label: string;
  value: number;
  unit: string;
}

export interface QualityReport {
  metrics: QualityMetric[];
  overall: number;
  passed: boolean;
  issues: string[];
}

export interface PipelineStep {
  id: string;
  label: string;
  detail: string;
  icon: string;
}

export interface SessionSummary {
  id: string;
  reference: string;
  applicant: string;
  country: string;
  documentType: string;
  level: VerificationLevel;
  status: KYCStatus;
  decision: DecisionResult | null;
  createdAt: string;
  duration: string;
}

export interface ResultDocumentBlock {
  country: string;
  type: string;
  documentNumberMasked: string;
  expiryStatus: string;
}

export interface ResultIdentityBlock {
  fullName: string;
  fullNameLocal: string;
  dateOfBirth: string;
  nationality: string;
  sex: string;
  address: string;
}

export interface ResultChecksBlock {
  documentQuality: CheckResult;
  mrz: CheckResult;
  barcode: CheckResult;
  nfc: CheckResult;
  faceMatch: CheckResult;
  liveness: CheckResult;
  fraud: CheckResult;
}

export interface ExtractedField {
  field: string;
  label: string;
  rawValue: string;
  normalizedValue: string;
  confidence: number;
}

export interface KYCResult {
  sessionId: string;
  reference: string;
  applicant: string;
  status: KYCStatus;
  country: string;
  documentType: string;
  level: VerificationLevel;
  completedAt: string;
  document: ResultDocumentBlock;
  identity: ResultIdentityBlock;
  checks: ResultChecksBlock;
  fields: ExtractedField[];
  decision: {
    result: DecisionResult;
    reasonCodes: string[];
    faceMatchScore: number;
    livenessScore: number;
  };
}

export interface ReasonCodeMeta {
  code: string;
  label: string;
  severity: "info" | "warning" | "critical";
}

export type Priority = "HIGH" | "NORMAL" | "LOW";

export type SignalSeverity = "low" | "medium" | "high";

export interface FraudSignal {
  code: string;
  label: string;
  severity: SignalSeverity;
  detail: string;
}

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  detail: string;
  at: string;
}

export interface ReviewCase {
  id: string;
  reference: string;
  applicant: string;
  country: string;
  documentType: string;
  level: VerificationLevel;
  status: KYCStatus;
  decision: DecisionResult | null;
  priority: Priority;
  riskScore: number;
  submittedAt: string;
  waitTime: string;
  assignee: string;
  signals: FraudSignal[];
  checks: ResultChecksBlock;
  fields: ExtractedField[];
  decisionResult: DecisionResult;
  reasonCodes: string[];
  faceMatchScore: number;
  livenessScore: number;
  audit: AuditEntry[];
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created: string;
  lastUsed: string;
  status: "ACTIVE" | "REVOKED";
}

export interface WebhookEndpoint {
  id: string;
  url: string;
  events: string[];
  status: "ACTIVE" | "PAUSED";
  secretMasked: string;
}

export interface WebhookDelivery {
  id: string;
  event: string;
  endpoint: string;
  statusCode: number;
  result: "SUCCESS" | "FAILED" | "RETRYING";
  attempts: number;
  at: string;
}