import type { SessionStatus } from "@/api/types";

export interface BadgeMeta {
  label: string;
  className: string;
  icon: string;
}

const NEUTRAL = "bg-secondary-100 text-secondary-800 border-secondary-200";
const BUSY = "bg-accent-100 text-accent-900 border-accent-200";
const GOOD = "bg-primary-100 text-primary-900 border-primary-200";
const BAD = "bg-accent-600 text-background-50 border-accent-600";

const STATUS: Record<SessionStatus, BadgeMeta> = {
  CREATED: { label: "Created", className: NEUTRAL, icon: "ri-add-circle-line" },
  DOCUMENT_REQUIRED: { label: "Document required", className: NEUTRAL, icon: "ri-file-add-line" },
  DOCUMENT_PROCESSING: { label: "Document processing", className: BUSY, icon: "ri-loader-4-line" },
  SELFIE_REQUIRED: { label: "Selfie required", className: NEUTRAL, icon: "ri-user-smile-line" },
  LIVENESS_REQUIRED: { label: "Liveness required", className: NEUTRAL, icon: "ri-shield-user-line" },
  NFC_REQUIRED: { label: "Chip read required", className: NEUTRAL, icon: "ri-scan-line" },
  PROCESSING: { label: "Processing", className: BUSY, icon: "ri-loader-4-line" },
  MANUAL_REVIEW: { label: "Manual review", className: BUSY, icon: "ri-eye-line" },
  VERIFIED: { label: "Verified", className: GOOD, icon: "ri-verified-badge-line" },
  REJECTED: { label: "Rejected", className: BAD, icon: "ri-close-circle-line" },
  EXPIRED: { label: "Expired", className: "bg-secondary-100 text-secondary-700 border-secondary-200", icon: "ri-time-line" },
};

const UNKNOWN: BadgeMeta = { label: "Unknown", className: "bg-background-200 text-foreground-600 border-background-300", icon: "ri-question-line" };

export function statusMeta(status: SessionStatus): BadgeMeta {
  return STATUS[status] ?? { ...UNKNOWN, label: status };
}

export function decisionMeta(result: string): BadgeMeta {
  if (result === "PASS") return { label: "PASS", className: GOOD, icon: "ri-checkbox-circle-line" };
  if (result === "REVIEW") return { label: "REVIEW", className: BUSY, icon: "ri-eye-line" };
  if (result === "FAIL") return { label: "FAIL", className: BAD, icon: "ri-close-circle-line" };
  return { ...UNKNOWN, label: result };
}

export function checkMeta(result: string): BadgeMeta {
  if (result === "PASS") return { label: "PASS", className: GOOD, icon: "ri-check-line" };
  if (result === "REVIEW") return { label: "REVIEW", className: BUSY, icon: "ri-eye-line" };
  if (result === "FAIL") return { label: "FAIL", className: BAD, icon: "ri-close-line" };
  if (result === "NOT_APPLICABLE") return { label: "N/A", className: "bg-secondary-100 text-secondary-500 border-secondary-200", icon: "ri-subtract-line" };
  return { ...UNKNOWN, label: result === "UNAVAILABLE" ? "Unavailable" : result };
}

export function signalSeverityMeta(severity: "low" | "medium" | "high"): BadgeMeta {
  if (severity === "high") return { label: "High", className: BAD, icon: "ri-alert-line" };
  if (severity === "medium") return { label: "Medium", className: "bg-accent-100 text-accent-900 border-accent-300", icon: "ri-error-warning-line" };
  return { label: "Low", className: "bg-secondary-100 text-secondary-700 border-secondary-200", icon: "ri-information-line" };
}
