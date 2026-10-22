import type { DocumentCaptureState, DocumentSide } from "@/api/types";
import { INSTRUCTION_TEXT } from "@/lib/catalog";

const REASONS: Record<string, string> = {
  DOCUMENT_NOT_RECOGNIZED: "We couldn't recognize the document in these photos.",
  DOCUMENT_TYPE_MISMATCH: "These photos did not match the document type requested for this check.",
  CRITICAL_FIELD_UNREADABLE: "Some required text on the document could not be read.",
  FRONT_SIDE_EXPECTED: "The front photo could not be recognized as the front of the document.",
  BACK_SIDE_EXPECTED: "The back photo could not be recognized as the back of the document.",
  DATA_PAGE_SIDE_EXPECTED: "The passport photo could not be recognized as its data page.",
  CAPTURES_INCOMPLETE: "One or more required document photos are missing.",
  REFERENCE_FACE_UNAVAILABLE: "The portrait on your document was not clear enough for the face check.",
  DOCUMENT_CROPPED: "Part of the document is outside the photo.",
  DOCUMENT_TOO_SMALL: "The document is too small in the photo to read clearly.",
  DOCUMENT_NOT_FOUND: "The document could not be located in the photo.",
  DOCUMENT_NOT_DETECTED: "The document could not be located in the photo.",
  DOCUMENT_TOO_FAR: "The document is too small in the photo to read clearly.",
  DOCUMENT_TOO_CLOSE: "The document is too close to the camera.",
  RESOLUTION_TOO_LOW: "The photo does not show enough detail to read the document.",
  PERSPECTIVE_DISTORTED: "The document is viewed at too much of an angle.",
  DOCUMENT_SHAPE_UNEXPECTED: "The document edges could not be read clearly.",
  MULTIPLE_DOCUMENTS: "More than one document is visible in the photo.",
  TOO_DARK: "The document photo is too dark.",
  TOO_BRIGHT: "The document photo is too bright.",
  IMAGE_BLURRY: "The document photo is too blurry to read clearly.",
  OVERALL_QUALITY_LOW: "The photo is not clear enough to read the document.",
  DOCUMENT_BLURRY: "The document photo is too blurry to read clearly.",
  BLUR_DETECTED: "The document photo is too blurry to read clearly.",
  GLARE_DETECTED: "Reflections cover part of the document.",
  SHADOW_DETECTED: "A shadow covers part of the document.",
  SAME_IMAGE_FOR_MULTIPLE_SIDES: "The same photo was used for more than one side.",
  OCR_ENGINE_UNAVAILABLE: "The document reading service is temporarily unavailable.",
  DOCUMENT_PROCESSING_UNAVAILABLE: "The document reading service is temporarily unavailable.",
  DOCUMENT_ADAPTER_UNAVAILABLE: "The service cannot read this document type right now.",
  DOCUMENT_PROCESSING_FAILED: "The service could not finish reading your document.",
};

const INSTRUCTIONS: Record<string, string> = {
  CAPTURE_CORRECT_DOCUMENT: "Photograph the original document type requested above.",
  SHOW_ALL_DOCUMENT_EDGES: "Keep the whole document and all four edges inside the photo.",
  CAPTURE_FRONT: "Photograph the front of the document.",
  CAPTURE_BACK: "Turn the document over and photograph the back.",
  CAPTURE_DATA_PAGE: "Photograph the passport page with your portrait and personal details.",
  RETRY_DOCUMENT_PROCESSING: "Retry the document check using the photos already received.",
  CONTACT_SUPPORT: "If this continues, contact the service that sent your verification link.",
};

/** Unknown diagnostic codes stay out of applicant copy. The fallback is intentionally general. */
export function documentReasonMessages(codes: string[]): string[] {
  return [...new Set(codes.map((code) => REASONS[code]).filter((message): message is string => !!message))];
}

export function documentInstructionMessages(codes: string[]): string[] {
  return [...new Set(codes.map((code) => INSTRUCTIONS[code] ?? INSTRUCTION_TEXT[code])
    .filter((message): message is string => !!message))];
}

export function acceptedDocumentSides(capture: Pick<DocumentCaptureState, "sides"> | null | undefined): Record<string, boolean> {
  return Object.fromEntries(Object.entries(capture?.sides ?? {}).map(([side, value]) => [side, value === "ACCEPTED"]));
}

export function nextDocumentSide(sides: DocumentSide[], accepted: Record<string, boolean>): DocumentSide | null {
  return sides.find((side) => !accepted[side]) ?? null;
}
