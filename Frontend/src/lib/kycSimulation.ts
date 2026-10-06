import type {
  CheckResult,
  DecisionResult,
  DocumentSide,
  DocumentTypeOption,
  KYCResult,
  KYCStatus,
  Priority,
  QualityReport,
  SignalSeverity,
  VerificationLevel,
} from "@/types/kyc";
import { documentTypes } from "@/mocks/kyc";

export function getDocumentType(id: string): DocumentTypeOption {
  return documentTypes.find((d) => d.id === id) || documentTypes[0];
}

export interface BadgeMeta {
  label: string;
  className: string;
  icon: string;
}

export function statusMeta(status: KYCStatus): BadgeMeta {
  const map: Record<KYCStatus, BadgeMeta> = {
    CREATED: { label: "Created", className: "bg-secondary-100 text-secondary-800 border-secondary-200", icon: "ri-add-circle-line" },
    DOCUMENT_REQUIRED: { label: "Document required", className: "bg-secondary-100 text-secondary-800 border-secondary-200", icon: "ri-file-add-line" },
    DOCUMENT_PROCESSING: { label: "Document processing", className: "bg-accent-100 text-accent-900 border-accent-200", icon: "ri-loader-4-line" },
    SELFIE_REQUIRED: { label: "Selfie required", className: "bg-secondary-100 text-secondary-800 border-secondary-200", icon: "ri-user-smile-line" },
    LIVENESS_REQUIRED: { label: "Liveness required", className: "bg-secondary-100 text-secondary-800 border-secondary-200", icon: "ri-shield-user-line" },
    NFC_REQUIRED: { label: "NFC required", className: "bg-secondary-100 text-secondary-800 border-secondary-200", icon: "ri-scan-line" },
    PROCESSING: { label: "Processing", className: "bg-accent-100 text-accent-900 border-accent-200", icon: "ri-loader-4-line" },
    MANUAL_REVIEW: { label: "Manual review", className: "bg-accent-100 text-accent-900 border-accent-200", icon: "ri-eye-line" },
    VERIFIED: { label: "Verified", className: "bg-primary-100 text-primary-900 border-primary-200", icon: "ri-verified-badge-line" },
    REJECTED: { label: "Rejected", className: "bg-accent-600 text-background-50 border-accent-600", icon: "ri-close-circle-line" },
    EXPIRED: { label: "Expired", className: "bg-secondary-100 text-secondary-700 border-secondary-200", icon: "ri-time-line" },
  };
  return map[status];
}

export function decisionMeta(result: DecisionResult): BadgeMeta {
  const map: Record<DecisionResult, BadgeMeta> = {
    PASS: { label: "PASS", className: "bg-primary-100 text-primary-900 border-primary-200", icon: "ri-checkbox-circle-line" },
    REVIEW: { label: "REVIEW", className: "bg-accent-100 text-accent-900 border-accent-200", icon: "ri-eye-line" },
    FAIL: { label: "FAIL", className: "bg-accent-600 text-background-50 border-accent-600", icon: "ri-close-circle-line" },
  };
  return map[result];
}

export function checkMeta(result: CheckResult): BadgeMeta {
  const map: Record<CheckResult, BadgeMeta> = {
    PASS: { label: "PASS", className: "bg-primary-100 text-primary-900 border-primary-200", icon: "ri-check-line" },
    REVIEW: { label: "REVIEW", className: "bg-accent-100 text-accent-900 border-accent-200", icon: "ri-eye-line" },
    FAIL: { label: "FAIL", className: "bg-accent-600 text-background-50 border-accent-600", icon: "ri-close-line" },
    NOT_APPLICABLE: { label: "N/A", className: "bg-secondary-100 text-secondary-500 border-secondary-200", icon: "ri-subtract-line" },
  };
  return map[result];
}

export function priorityMeta(priority: Priority): BadgeMeta {
  const map: Record<Priority, BadgeMeta> = {
    HIGH: { label: "High", className: "bg-accent-600 text-background-50 border-accent-600", icon: "ri-alarm-warning-line" },
    NORMAL: { label: "Normal", className: "bg-secondary-100 text-secondary-700 border-secondary-200", icon: "ri-equalizer-line" },
    LOW: { label: "Low", className: "bg-background-200 text-foreground-600 border-background-300", icon: "ri-arrow-down-line" },
  };
  return map[priority];
}

export function signalSeverityMeta(severity: SignalSeverity): BadgeMeta {
  const map: Record<SignalSeverity, BadgeMeta> = {
    high: { label: "High", className: "bg-accent-600 text-background-50 border-accent-600", icon: "ri-alert-line" },
    medium: { label: "Medium", className: "bg-accent-100 text-accent-900 border-accent-300", icon: "ri-error-warning-line" },
    low: { label: "Low", className: "bg-secondary-100 text-secondary-700 border-secondary-200", icon: "ri-information-line" },
  };
  return map[severity];
}

export function riskTone(score: number): "primary" | "accent" | "secondary" {
  if (score >= 70) return "accent";
  if (score >= 40) return "secondary";
  return "primary";
}

export function documentQualityReport(side: DocumentSide, poor = false): QualityReport {
  if (poor) {
    const poorMetrics = [
      { key: "blur", label: "Sharpness", value: 43, unit: "%" },
      { key: "glare", label: "Glare", value: 51, unit: "%" },
      { key: "brightness", label: "Brightness", value: 58, unit: "%" },
      { key: "coverage", label: "Coverage", value: 69, unit: "%" },
      { key: "perspective", label: "Perspective", value: 54, unit: "%" },
    ];
    const poorOverall = Math.round(poorMetrics.reduce((s, m) => s + m.value, 0) / poorMetrics.length);
    return {
      metrics: poorMetrics,
      overall: poorOverall,
      passed: false,
      issues: [
        "Image is too blurry — hold the camera steady and tap to focus",
        "Glare detected on the document surface — tilt away from direct light",
        "Document edges fall outside the frame — fit the whole document inside the brackets",
      ],
    };
  }

  const metrics =
    side === "BACK"
      ? [
          { key: "blur", label: "Sharpness", value: 91, unit: "%" },
          { key: "glare", label: "Glare", value: 94, unit: "%" },
          { key: "brightness", label: "Brightness", value: 88, unit: "%" },
          { key: "coverage", label: "Coverage", value: 96, unit: "%" },
          { key: "perspective", label: "Perspective", value: 90, unit: "%" },
        ]
      : [
          { key: "blur", label: "Sharpness", value: 94, unit: "%" },
          { key: "glare", label: "Glare", value: 92, unit: "%" },
          { key: "brightness", label: "Brightness", value: 90, unit: "%" },
          { key: "coverage", label: "Coverage", value: 97, unit: "%" },
          { key: "perspective", label: "Perspective", value: 93, unit: "%" },
        ];
  const overall = Math.round(metrics.reduce((s, m) => s + m.value, 0) / metrics.length);
  return { metrics, overall, passed: overall >= 80, issues: [] };
}

export function faceQualityReport(poor = false): QualityReport {
  if (poor) {
    const poorMetrics = [
      { key: "faces", label: "Faces detected", value: 100, unit: "%" },
      { key: "centered", label: "Centering", value: 62, unit: "%" },
      { key: "eyes", label: "Eyes visible", value: 55, unit: "%" },
      { key: "light", label: "Illumination", value: 49, unit: "%" },
      { key: "pose", label: "Pose", value: 58, unit: "%" },
    ];
    const poorOverall = Math.round(poorMetrics.reduce((s, m) => s + m.value, 0) / poorMetrics.length);
    return {
      metrics: poorMetrics,
      overall: poorOverall,
      passed: false,
      issues: [
        "Face is not centered inside the oval — move your face to the middle",
        "Lighting is too dim — move to a brighter, even light",
        "Part of your face is outside the guide — bring your whole face into the frame",
      ],
    };
  }

  const metrics = [
    { key: "faces", label: "Faces detected", value: 100, unit: "%" },
    { key: "centered", label: "Centering", value: 95, unit: "%" },
    { key: "eyes", label: "Eyes visible", value: 100, unit: "%" },
    { key: "light", label: "Illumination", value: 89, unit: "%" },
    { key: "pose", label: "Pose", value: 92, unit: "%" },
  ];
  const overall = Math.round(metrics.reduce((s, m) => s + m.value, 0) / metrics.length);
  return { metrics, overall, passed: overall >= 80, issues: [] };
}

export interface BuildResultInput {
  sessionId: string;
  reference: string;
  applicant: string;
  country: string;
  documentTypeId: string;
  level: VerificationLevel;
}

export function buildResult(input: BuildResultInput): KYCResult {
  const dt = getDocumentType(input.documentTypeId);
  return {
    sessionId: input.sessionId,
    reference: input.reference,
    applicant: input.applicant,
    status: "VERIFIED",
    country: input.country,
    documentType: input.documentTypeId,
    level: input.level,
    completedAt: new Date().toISOString().slice(0, 16).replace("T", " "),
    document: {
      country: input.country,
      type: dt.family,
      documentNumberMasked: "******7842",
      expiryStatus: "VALID",
    },
    identity: {
      fullName: input.applicant.toUpperCase(),
      fullNameLocal: "សុខ ចាន់ធី",
      dateOfBirth: "1996-03-22",
      nationality: input.country === "KH" ? "KH" : "IDN",
      sex: "M",
      address: "Phnom Penh, Khan Toul Kork",
    },
    checks: {
      documentQuality: "PASS",
      mrz: dt.mrz ? "PASS" : "NOT_APPLICABLE",
      barcode: dt.qr ? "PASS" : "NOT_APPLICABLE",
      nfc: dt.nfc ? "PASS" : "NOT_APPLICABLE",
      faceMatch: "PASS",
      liveness: "PASS",
      fraud: "PASS",
    },
    fields: [
      { field: "full_name_local", label: "Full name (local)", rawValue: "សុខ ចាន់ធី", normalizedValue: "សុខ ចាន់ធី", confidence: 0.96 },
      { field: "full_name", label: "Full name (Latin)", rawValue: input.applicant.toUpperCase(), normalizedValue: input.applicant.toUpperCase(), confidence: 0.98 },
      { field: "document_number", label: "Document number", rawValue: "0784 2210 4482", normalizedValue: "078422104482", confidence: 0.95 },
      { field: "date_of_birth", label: "Date of birth", rawValue: "22/03/1996", normalizedValue: "1996-03-22", confidence: 0.97 },
      { field: "sex", label: "Sex", rawValue: "M", normalizedValue: "M", confidence: 0.94 },
      { field: "expiry_date", label: "Expiry date", rawValue: "09/11/2030", normalizedValue: "2030-11-09", confidence: 0.93 },
    ],
    decision: {
      result: "PASS",
      reasonCodes: dt.mrz
        ? ["DOCUMENT_VALID", "MRZ_VALID", "FACE_MATCH", "LIVENESS_PASS"]
        : ["DOCUMENT_VALID", "FACE_MATCH", "LIVENESS_PASS"],
      faceMatchScore: 0.94,
      livenessScore: 0.97,
    },
  };
}

export function findSessionResult(id: string): KYCResult {
  return { ...buildResult({ sessionId: id, reference: "APP-20481", applicant: "Sok Chanthy", country: "KH", documentTypeId: "KH_NATIONAL_ID", level: "STANDARD" }), sessionId: id };
}