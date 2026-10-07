# ID sample check — 7 October 2026

Reviewed all 10 JPEG files in `ID`: six Khmer ID fronts, two Khmer ID backs, and one NSSF front/back pair.

The six photographed Khmer ID fronts are internally consistent in their machine-readable data: all 24 manually transcribed check digits pass, and none of their encoded expiry dates has passed as of the review date. The NSSF card’s printed Latin name and date of birth match the Khmer ID front photographed on 6 October at 23:39:48. These checks do not authenticate the cards with their issuers.

The NSSF QR code decodes to an HTTPS link on `verify.gov.kh`. The verification link was not accessible through the web tool, so no government verification result was obtained. A QR link on the expected domain alone does not establish genuineness.

## Local app findings

The capture gate in the tested version accepts 4 of 10 photos and requests recapture for 6. Running the downstream OCR for diagnosis nevertheless extracts all five compared fields correctly from 3 of the 6 Khmer ID fronts. The compared fields are document number, Latin name, date of birth, sex and expiry date; this count does not assess Khmer-script name or address accuracy.

| File | Side | Capture gate | Classified as | OCR fields matching manual transcription | Finding |
| --- | --- | --- | --- | --- | --- |
| [KhmerID/photo_1_2026-05-19_17-14-41.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/KhmerID/photo_1_2026-05-19_17-14-41.jpg>) | FRONT | ACCEPTED | KH_NATIONAL_ID/FRONT | 5/5 | All five compared fields match; the OCR-derived MRZ check requests review although the manually read MRZ passes. |
| [KhmerID/photo_2_2026-05-19_17-14-41.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/KhmerID/photo_2_2026-05-19_17-14-41.jpg>) | BACK | ACCEPTED | KH_NATIONAL_ID/BACK | — | Back detected at low confidence; no identity text expected on this side. |
| [KhmerID/photo_2026-09-29_14-49-17.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/KhmerID/photo_2026-09-29_14-49-17.jpg>) | FRONT | RECAPTURE | UNKNOWN/UNKNOWN | 0/5 | Scan/CamScanner watermark and strong whitening are visible. The detector/OCR loses the readable card; this is not evidence of forgery. |
| [KhmerID/photo_2026-09-29_14-51-04.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/KhmerID/photo_2026-09-29_14-51-04.jpg>) | FRONT | RECAPTURE | KH_NATIONAL_ID/FRONT | 5/5 | All five compared fields match despite a MULTIPLE_DOCUMENTS capture flag on a photo that visibly shows one card. |
| [KhmerID/photo_2026-10-06_23-39-48.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/KhmerID/photo_2026-10-06_23-39-48.jpg>) | FRONT | RECAPTURE | KH_NATIONAL_ID/BACK | 0/5 | The card is fully visible, but the detector uses the larger background area and extraction fails. |
| [KhmerID/photo_2026-10-06_23-39-53.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/KhmerID/photo_2026-10-06_23-39-53.jpg>) | BACK | RECAPTURE | KH_NATIONAL_ID/BACK | — | The gate flags cropping/coverage even though the whole card is visible within a larger background. |
| [KhmerID/photo_2026-10-07_10-26-41.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/KhmerID/photo_2026-10-07_10-26-41.jpg>) | FRONT | ACCEPTED | KH_NATIONAL_ID/FRONT | 4/5 | Latin name, birth date, sex and expiry match; the printed number is misread and correctly flagged against the valid MRZ. |
| [KhmerID/photo_2026-10-07_10-27-01.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/KhmerID/photo_2026-10-07_10-27-01.jpg>) | FRONT | ACCEPTED | KH_NATIONAL_ID/FRONT | 5/5 | Sideways photo is handled; all five compared fields match; missing/low-confidence Khmer fields require review. |
| [NSSF/photo_2026-10-06_23-39-58.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/NSSF/photo_2026-10-06_23-39-58.jpg>) | FRONT | RECAPTURE | KH_NSSF/FRONT | N/A (no expiry printed) | Birth date is read; member number, Latin name and sex extraction fail. The QR decodes. The photographed member number uses 7-7-1 digits with hyphens (15 total), incompatible with the adapter’s labelled 6–12-digit rule. |
| [NSSF/photo_2026-10-06_23-40-12.jpg](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/ID/NSSF/photo_2026-10-06_23-40-12.jpg>) | BACK | RECAPTURE | UNKNOWN/UNKNOWN | — | Back not recognized after preprocessing; capture gate requests recapture. |

## Scope and references

This diagnostic used the existing capture decoder, quality gate, preprocessing, Tesseract Khmer/English OCR, numeric refinement, dedicated MRZ reader, card adapters and barcode decoder. OCR was run even when the capture gate rejected a photo to identify where extraction fails; the live upload flow would stop at that gate. Adapter BARCODE/PORTRAIT validation entries in the JSON are placeholders for later service checks, not final barcode or biometric results.

Two likely Khmer front/back pairs and the NSSF pair were tested together using filename timing and visible capture context. The back images contain no unique identifier proving their pairing. Four Khmer fronts have no matching back supplied, so complete two-sided sessions cannot be assessed from those files.

Original images were unchanged. Application source changes appeared elsewhere in the workspace during this review. App diagnostics describe the modules loaded by the diagnostic process and may differ from subsequent edits. This check did not edit application source. No KYC sessions, database writes, face comparisons or liveness checks were performed. The report omits raw identity numbers, names, addresses, OCR text and the private QR link.

MRZ check digits were calculated using [ICAO Doc 9303 Part 3](https://www.icao.int/sites/default/files/publications/DocSeries/9303_p3_cons_en.pdf) and the TD1 layout in [Part 5](https://www.icao.int/sites/default/files/publications/DocSeries/9303_p5_cons_en.pdf). The government describes Verify.gov.kh as its standard-QR document verification service on the [Digital Government Committee products page](https://dgc.gov.kh/en/product).

Machine-readable results: [id-sample-check-2026-10-07.json](</Users/mac/Documents/1.1 OPS Sulotion/6-KYC/artifacts/id-sample-check-2026-10-07.json>).
