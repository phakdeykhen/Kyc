# Phase 7 — QR / barcode engine

Status: implemented on 5 October 2026.

## 1. Design (spec §10)

```
each captured side (original pixels)
  → zxing-cpp decode (QR, PDF417, Data Matrix, Aztec, linear)
  → payload parser: JWS | JSON | KEY_VALUE | AAMVA | ICAO_VDS | TEXT
  → signature verifier (trusted keys only)
  → field comparison with the printed document
  → BARCODE check + encrypted barcode_results rows
```

| Payload | `format_valid` | Fields | Signature |
| --- | --- | --- | --- |
| JWS compact | header + claims parse | claims mapped to canonical names | verified against `BARCODE_TRUST_STORE` (ES256/ES384/RS256/PS256/EdDSA) |
| JSON / key=value | parses | common aliases (`member`, `id`, `name`, `dob`, …) | none |
| AAMVA PDF417 | number present | DAQ/DCS/DAC/DBB/DBA/DBC | none |
| ICAO VDS (`0xDC`) | header byte | not decoded | present; needs CSCA trust list → not verifiable here |
| other text / URLs | `null` | none, so not comparable | none |

**Rules (spec §10):**
- A code that decodes proves nothing about authenticity. Decoding plus agreement with
  the printed fields is reported as `BARCODE_DATA_CONSISTENT`, a consistency result only.
- Signatures are verified only where a public verification mechanism exists: an
  operator-supplied trust store. Nothing is verified against keys taken from the code itself.
- `alg: none` → `BARCODE_UNSIGNED_ALG_NONE` → FAIL. A failed signature → FAIL (tamper
  signal). Any field disagreement → `BARCODE_VISUAL_<FIELD>_MISMATCH` → REVIEW.
- No code on the document → NOT_APPLICABLE, unless the layout sets `barcode_expected`
  (then REVIEW).

**Storage.** `barcode_results` (table from Phase 1) holds symbology, `decoded`,
`format_valid`, `signature_present`, `signature_valid` and the per-field consistency.
The raw payload is AES-GCM encrypted with the PII keyring. Associated data binds it to
the tenant, session, document, side and symbology.

**Phase 2 fix.** The glare score now ignores saturated pixels within ~2% of dark ink.
Printed white (QR modules, quiet zones) is not glare. Washed-out areas without ink still are.

## 2. Files

```text
src/kyc/barcode/engine.py      + zxing-cpp decoding
src/kyc/barcode/payload.py     + payload formats and field mapping
src/kyc/barcode/signatures.py  + trust store and JWS verification
src/kyc/barcode/evaluate.py    + BARCODE check and evidence
src/kyc/services/documents.py  ~ decode every side, persist barcode_results
src/kyc/documents/adapters/khmer_label.py ~ real parse_barcode/parse_qr; barcode_expected flag
src/kyc/engines/capture_quality.py        ~ glare excludes ink-adjacent white
src/kyc/core/config.py, main.py           ~ BARCODE_TRUST_STORE
scripts/bootstrap_local.py     ~ grant on barcode_results
tests/test_barcode.py, tests/images.py    ~ decode, formats, signatures; QR on NSSF SPECIMEN
```

## 3. Migration, dependencies, environment

- No migration: `barcode_results` has existed since Phase 1.
- New dependency: `zxing-cpp==3.1.1`, a self-contained wheel with no system libraries.
- Optional setting: `BARCODE_TRUST_STORE` (a JSON object of key id → PEM public key).
- Existing installations must rerun bootstrap to pick up the new grant.

## 4. Security concerns

* The payload is PII: it is encrypted and never logged. Check metadata holds only
  formats, states and MATCH/MISMATCH.
* Trust-store keys decide what counts as "signature valid". Manage that file like a
  certificate store (Phase 17).
* Unknown payloads stay unparsed rather than guessed. The Cambodian NSSF QR format used
  in tests is fictional.
