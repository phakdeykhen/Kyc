import type { DocumentSide, DocumentTypeCode, VerificationLevel } from "@/api/types";

// Display text for codes the API returns. The API stays the source of truth for which document
// types, sides and countries exist (GET /v1/document-types, /v1/countries); this only names them.

export const DOCUMENT_LABELS: Record<string, { label: string; description: string }> = {
  KH_NATIONAL_ID: { label: "Cambodia National ID", description: "Khmer national ID card, front and back (MRZ on the back)." },
  KH_PASSPORT: { label: "Cambodia Passport", description: "Cambodian ePassport data page (ICAO MRZ, optional chip)." },
  KH_NSSF: { label: "Cambodia NSSF card", description: "National Social Security Fund card, front and back." },
  PASSPORT: { label: "Passport (any country)", description: "ICAO 9303 passport read from its MRZ." },
  NATIONAL_ID: { label: "National ID card", description: "Foreign national ID card, front and back." },
  RESIDENCE_CARD: { label: "Residence card", description: "Residence permit card, front and back." },
  DRIVING_LICENSE: { label: "Driving licence", description: "Driving licence, front and back." },
  UNKNOWN: { label: "Unknown document", description: "" },
};

export function documentLabel(type: DocumentTypeCode | string): string {
  return DOCUMENT_LABELS[type]?.label ?? type;
}

export const SIDE_LABELS: Record<DocumentSide, string> = { FRONT: "Front", BACK: "Back", DATA_PAGE: "Data page" };

export const LEVELS: { id: VerificationLevel; label: string; steps: string; checks: string }[] = [
  { id: "DOCUMENT_ONLY", label: "Document only", steps: "Document", checks: "Quality, classification, data, expiry, cross-check, fraud" },
  { id: "DOCUMENT_FACE", label: "Document + face", steps: "Document → selfie", checks: "Adds portrait, selfie quality and 1:1 face match" },
  { id: "DOCUMENT_FACE_LIVENESS", label: "Document + face + liveness", steps: "Document → selfie → liveness", checks: "Adds active liveness (head-movement challenge)" },
  { id: "DOCUMENT_FACE_LIVENESS_NFC", label: "With ePassport chip", steps: "Document → selfie → liveness → chip", checks: "Adds chip Passive + Active Authentication (mobile app)" },
];

export const DEFAULT_LEVEL: VerificationLevel = "DOCUMENT_FACE_LIVENESS";

const REGION_NAMES = typeof Intl !== "undefined" && "DisplayNames" in Intl
  ? new Intl.DisplayNames(["en"], { type: "region" }) : null;

export function countryName(code: string): string {
  try {
    return REGION_NAMES?.of(code) ?? code;
  } catch {
    return code;
  }
}

export function countryFlag(code: string): string {
  if (!/^[A-Z]{2}$/.test(code)) return "🌍";
  return String.fromCodePoint(...[...code].map((char) => 0x1f1a5 + char.charCodeAt(0)));
}

// Instruction codes from the quality gates (document, selfie, liveness), as plain sentences.
export const INSTRUCTION_TEXT: Record<string, string> = {
  MOVE_CLOSER: "Move closer so it fills the frame.",
  MOVE_BACK: "Move back so all four corners are visible.",
  CENTER_DOCUMENT: "Center the document inside the frame.",
  HOLD_STILL: "Hold still, the photo is blurry.",
  REDUCE_GLARE: "Tilt slightly or move away from direct light to remove glare.",
  MORE_LIGHT: "Find more light.",
  LESS_LIGHT: "Too bright. Move out of direct light.",
  AVOID_SHADOW: "Remove the shadow across the document.",
  ALIGN_DOCUMENT: "Hold the phone parallel to the document.",
  ONE_DOCUMENT_ONLY: "Show only one document.",
  USE_CONTRASTING_BACKGROUND: "Place the document on a plain, darker surface.",
  USE_HIGHER_RESOLUTION: "Use a higher-resolution camera or photo.",
  CAPTURE_OTHER_SIDE: "That image was already used for another side. Turn the document over.",
  RETAKE_PHOTO: "Please take the photo again.",
  CENTER_FACE: "Center your face inside the oval.",
  REMOVE_OCCLUSION: "Remove anything covering your face and keep both eyes visible.",
  FACE_CAMERA: "Look straight at the camera with your head upright.",
  ONE_FACE_ONLY: "Only your face should be visible in the photo.",
  SHOW_FACE: "Make sure your whole face is visible in the photo.",
  RETAKE_SELFIE: "Please take another selfie.",
  RECAPTURE_DOCUMENT: "Retake your document photo so its portrait is clear and unobstructed.",
  RECAPTURE_DOCUMENT_WITH_CLEAR_PORTRAIT: "Retake your document photo so its portrait is clear and unobstructed.",
  REDUCE_LIGHT: "Move out of direct bright light.",
  EVEN_LIGHTING: "Use even light across your face and avoid strong shadows.",
  FOLLOW_EACH_INSTRUCTION: "Follow each instruction as it appears: move your head clearly, then hold still.",
  ONLY_YOU_IN_FRAME: "Make sure only your face is in view.",
  LOOK_STRAIGHT: "Start by looking straight at the camera.",
};

export function instructionText(code: string): string {
  return INSTRUCTION_TEXT[code] ?? code.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}

export const QUALITY_LABELS: Record<string, string> = {
  blur_score: "Sharpness", glare_score: "No glare", brightness_score: "Exposure", shadow_score: "Even light",
  document_coverage: "Coverage", perspective_score: "Alignment", resolution_score: "Resolution",
};

export const SCOPE_LABELS: Record<string, string> = {
  "sessions:write": "Create sessions, upload evidence, issue client tokens, request a decision",
  "sessions:read": "Read session status and results (identity masked)",
  "results:identity": "See unmasked identity fields in results",
  "webhooks:manage": "Manage webhook endpoints and read deliveries",
  "keys:manage": "Create, list and revoke API keys",
  "data:erase": "Erase a session's personal and biometric data",
};

export function newIdempotencyKey(): string {
  // crypto.randomUUID needs a secure context; getRandomValues works on a plain-http LAN address too.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `console-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

/** Sides the person must capture; mirrors the server's document requirements (passports: data page). */
export function requiredSides(type: DocumentTypeCode | string): DocumentSide[] {
  return type === "KH_PASSPORT" || type === "PASSPORT" ? ["DATA_PAGE"] : ["FRONT", "BACK"];
}

/** Statuses where the person has nothing left to do (a case in review waits for staff, not for them). */
export const APPLICANT_FINISHED = new Set(["VERIFIED", "REJECTED", "EXPIRED", "MANUAL_REVIEW"]);
