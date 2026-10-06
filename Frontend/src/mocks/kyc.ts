import type {
  Country,
  DocumentTypeOption,
  KYCResult,
  PipelineStep,
  ReasonCodeMeta,
  SessionSummary,
} from "@/types/kyc";

export const countries: Country[] = [
  { code: "KH", name: "Cambodia", flag: "🇰🇭", region: "Southeast Asia" },
  { code: "INTL", name: "International / Other", flag: "🌍", region: "Global" },
];

export const documentTypes: DocumentTypeOption[] = [
  {
    id: "KH_NATIONAL_ID",
    label: "Cambodia National ID",
    family: "NATIONAL_ID",
    country: "KH",
    sides: ["FRONT", "BACK"],
    mrz: false,
    qr: true,
    nfc: false,
    adapter: "CambodiaNationalIDAdapter",
    description:
      "Khmer National ID card. Captures both sides with QR/barcode verification.",
    locales: "Khmer + Latin",
  },
  {
    id: "KH_PASSPORT",
    label: "Cambodia Passport",
    family: "PASSPORT",
    country: "KH",
    sides: ["FRONT"],
    mrz: true,
    qr: false,
    nfc: true,
    adapter: "CambodiaPassportAdapter",
    description:
      "Cambodian ePassport with ICAO 9303 MRZ and optional NFC chip read.",
    locales: "Khmer + Latin",
  },
  {
    id: "KH_NSSF",
    label: "Cambodia NSSF Card",
    family: "NSSF",
    country: "KH",
    sides: ["FRONT"],
    mrz: false,
    qr: true,
    nfc: false,
    adapter: "CambodiaNSSFAdapter",
    description:
      "National Social Security Fund identity card with QR verification.",
    locales: "Khmer + Latin",
  },
  {
    id: "PASSPORT",
    label: "International Passport / MRZ",
    family: "PASSPORT",
    country: "INTL",
    sides: ["FRONT"],
    mrz: true,
    qr: false,
    nfc: true,
    adapter: "GenericPassportAdapter",
    description:
      "Generic ICAO 9303 passport. MRZ-driven parsing with check-digit validation.",
    locales: "Latin",
  },
  {
    id: "NATIONAL_ID",
    label: "Foreign National ID / Residence Card",
    family: "RESIDENCE_CARD",
    country: "INTL",
    sides: ["FRONT", "BACK"],
    mrz: false,
    qr: false,
    nfc: false,
    adapter: "GenericIDAdapter",
    description:
      "Non-Cambodian national ID or residence permit handled by the generic ID adapter.",
    locales: "Latin",
  },
];

export const pipelineSteps: PipelineStep[] = [
  { id: "capture", label: "Image normalization", detail: "Perspective + orientation correction", icon: "ri-crop-2-line" },
  { id: "classify", label: "Document classification", detail: "Country, family, side resolution", icon: "ri-file-list-2-line" },
  { id: "quality", label: "Quality assessment", detail: "Blur, glare, coverage, shadow", icon: "ri-focus-3-line" },
  { id: "ocr", label: "OCR field extraction", detail: "Khmer + Latin text regions", icon: "ri-scan-2-line" },
  { id: "mrz", label: "MRZ parsing", detail: "ICAO 9303 check-digit validation", icon: "ri-barcode-box-line" },
  { id: "barcode", label: "QR / barcode decode", detail: "Format + checksum verification", icon: "ri-qr-scan-2-line" },
  { id: "face", label: "Portrait extraction", detail: "Reference biometric template", icon: "ri-user-received-2-line" },
  { id: "liveness", label: "Liveness / anti-spoof", detail: "Presentation-attack detection", icon: "ri-shield-user-line" },
  { id: "match", label: "1:1 face comparison", detail: "Document portrait vs live face", icon: "ri-user-follow-line" },
  { id: "fraud", label: "Fraud signal analysis", detail: "Tamper, replay, duplicate checks", icon: "ri-alarm-warning-line" },
  { id: "risk", label: "Risk engine decision", detail: "Deterministic policy evaluation", icon: "ri-scales-3-line" },
];

export const livenessChallenges: string[] = [
  "Slowly turn your head to the LEFT",
  "Slowly turn your head to the RIGHT",
  "Look UP toward the top of the frame",
  "Slowly nod your head DOWN",
  "Slowly tilt your head toward your shoulder",
];

export const reasonCodes: ReasonCodeMeta[] = [
  { code: "DOCUMENT_VALID", label: "Document is valid and unexpired", severity: "info" },
  { code: "FACE_MATCH", label: "Face match above threshold", severity: "info" },
  { code: "LIVENESS_PASS", label: "Liveness challenge passed", severity: "info" },
  { code: "MRZ_VALID", label: "MRZ check digits valid", severity: "info" },
  { code: "LOW_OCR_CONFIDENCE", label: "Low OCR confidence on one or more fields", severity: "warning" },
  { code: "FIELD_MISMATCH", label: "Cross-source field disagreement", severity: "warning" },
  { code: "FACE_SCORE_BORDERLINE", label: "Face match score near threshold", severity: "warning" },
  { code: "LIVENESS_FAILED", label: "Liveness / anti-spoof failed", severity: "critical" },
  { code: "EXPIRED_DOCUMENT", label: "Document is expired", severity: "critical" },
  { code: "FACE_MISMATCH", label: "Face match below threshold", severity: "critical" },
  { code: "HIGH_RISK_TAMPER_SIGNAL", label: "High-risk tamper signal detected", severity: "critical" },
];

export const recentSessions: SessionSummary[] = [
  { id: "sess_9f2ka71b", reference: "APP-20481", applicant: "Sok Chanthy", country: "KH", documentType: "KH_NATIONAL_ID", level: "STANDARD", status: "VERIFIED", decision: "PASS", createdAt: "2026-10-05 09:12", duration: "48s" },
  { id: "sess_74hqp0ze", reference: "APP-20480", applicant: "Vann Molika", country: "KH", documentType: "KH_PASSPORT", level: "ENHANCED", status: "MANUAL_REVIEW", decision: "REVIEW", createdAt: "2026-10-05 08:57", duration: "1m 12s" },
  { id: "sess_2b8xlc93", reference: "APP-20479", applicant: "Dara Vichea", country: "KH", documentType: "KH_NSSF", level: "BASIC", status: "VERIFIED", decision: "PASS", createdAt: "2026-10-05 08:40", duration: "39s" },
  { id: "sess_5m1vt7wq", reference: "APP-20478", applicant: "Elena Petrova", country: "INTL", documentType: "PASSPORT", level: "STANDARD", status: "REJECTED", decision: "FAIL", createdAt: "2026-10-05 08:21", duration: "57s" },
  { id: "sess_8c3nr04p", reference: "APP-20477", applicant: "Lim Sovannarith", country: "KH", documentType: "KH_NATIONAL_ID", level: "STANDARD", status: "VERIFIED", decision: "PASS", createdAt: "2026-10-05 08:05", duration: "44s" },
  { id: "sess_1d7wy62k", reference: "APP-20476", applicant: "Nguyen Thi Mai", country: "INTL", documentType: "NATIONAL_ID", level: "STANDARD", status: "MANUAL_REVIEW", decision: "REVIEW", createdAt: "2026-10-05 07:48", duration: "1m 03s" },
  { id: "sess_63qez18m", reference: "APP-20475", applicant: "Chan Ratha", country: "KH", documentType: "KH_PASSPORT", level: "ENHANCED", status: "VERIFIED", decision: "PASS", createdAt: "2026-10-05 07:30", duration: "51s" },
];

export const defaultResult: KYCResult = {
  sessionId: "sess_9f2ka71b",
  reference: "APP-20481",
  applicant: "Sok Chanthy",
  status: "VERIFIED",
  country: "KH",
  documentType: "KH_NATIONAL_ID",
  level: "STANDARD",
  completedAt: "2026-10-05 09:13",
  document: {
    country: "KH",
    type: "NATIONAL_ID",
    documentNumberMasked: "******1234",
    expiryStatus: "VALID",
  },
  identity: {
    fullName: "SOK CHANTHY",
    fullNameLocal: "សុខ ចាន់ធី",
    dateOfBirth: "1999-01-01",
    nationality: "KH",
    sex: "F",
    address: "Phnom Penh, Khan Chamkarmon",
  },
  checks: {
    documentQuality: "PASS",
    mrz: "NOT_APPLICABLE",
    barcode: "PASS",
    nfc: "NOT_APPLICABLE",
    faceMatch: "PASS",
    liveness: "PASS",
    fraud: "PASS",
  },
  fields: [
    { field: "full_name_km", label: "Full name (Khmer)", rawValue: "សុខ ចាន់ធី", normalizedValue: "សុខ ចាន់ធី", confidence: 0.97 },
    { field: "full_name_en", label: "Full name (Latin)", rawValue: "SOK CHANTHY", normalizedValue: "SOK CHANTHY", confidence: 0.99 },
    { field: "document_number", label: "Document number", rawValue: "0123 4567 891234", normalizedValue: "01234567891234", confidence: 0.96 },
    { field: "date_of_birth", label: "Date of birth", rawValue: "01/01/1999", normalizedValue: "1999-01-01", confidence: 0.98 },
    { field: "sex", label: "Sex", rawValue: "F", normalizedValue: "F", confidence: 0.95 },
    { field: "expiry_date", label: "Expiry date", rawValue: "14/08/2031", normalizedValue: "2031-08-14", confidence: 0.94 },
  ],
  decision: {
    result: "PASS",
    reasonCodes: ["DOCUMENT_VALID", "MRZ_VALID", "FACE_MATCH", "LIVENESS_PASS"],
    faceMatchScore: 0.93,
    livenessScore: 0.98,
  },
};

export const riskBreakdown: { label: string; value: number }[] = [
  { label: "Document authenticity", value: 96 },
  { label: "Field consistency", value: 94 },
  { label: "Face similarity", value: 93 },
  { label: "Liveness confidence", value: 98 },
];