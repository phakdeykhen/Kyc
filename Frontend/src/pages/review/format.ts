import { checkMeta, signalSeverityMeta, statusMeta, type BadgeMeta } from "@/lib/badges";
import type { CheckValue, SessionStatus } from "@/api/types";

export function checkBadge(value: CheckValue): BadgeMeta {
  return checkMeta(value);
}

export function sessionStatusBadge(status: SessionStatus): BadgeMeta {
  return statusMeta(status);
}

export function severityBadge(severity: "HIGH" | "MEDIUM" | "LOW"): BadgeMeta {
  return signalSeverityMeta(severity.toLowerCase() as "high" | "medium" | "low");
}

export const CHECK_LABELS: Record<string, string> = {
  document_quality: "Document photo quality",
  document_classification: "Document type",
  document_data: "Document data",
  expiry: "Expiry",
  cross_check: "Cross-check (OCR, MRZ, barcode, chip)",
  fraud: "Fraud analysis",
  portrait_quality: "Document portrait",
  face_quality: "Selfie quality",
  face_match: "Face match (1:1)",
  liveness: "Liveness",
  nfc: "ePassport chip",
  nfc_status: "Chip status",
  mrz: "MRZ",
  barcode: "QR / barcode",
  issuing_country: "Issuing country",
};

export function humanize(code: string): string {
  const text = code.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function since(iso: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(iso)) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} d`;
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
