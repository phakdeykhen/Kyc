# Phase 11 — ePassport NFC mobile architecture

Status: implemented on 5 October 2026.

## 1. Design (spec §11)

Browsers cannot reach a passport chip (Web NFC is NDEF-only), so the **mobile app**
performs chip communication. The app does not decide anything. It uploads what the chip
returned, and the **server re-verifies everything** against its own trust anchors and
its own nonce. Designed around ICAO Doc 9303 Parts 10–12.

```
NFC_REQUIRED (level DOCUMENT_FACE_LIVENESS_NFC only, after liveness)
  mobile:  passport camera → MRZ (doc no, DOB, expiry) → PACE or BAC access keys
  → POST /v1/kyc/{id}/nfc/challenge          8-byte Active Authentication nonce, 120 s, single use
  mobile:  read EF.SOD, DG1, DG2, DG15 → INTERNAL AUTHENTICATE(nonce)
  → POST /v1/kyc/{id}/nfc                    read_status, access_protocol, challenge_id, aa_signature, sod, dg1, dg2, dg15
  server:  Passive Authentication → Active Authentication → DG1 vs printed page → chip portrait vs selfie
  → NFCResult (evidence only) → NFC_ACCEPTED → PROCESSING
```

If the device has no NFC or it is switched off, the app reports `NOT_SUPPORTED`,
`NOT_AVAILABLE` or `FAILED` instead of chip files. `NOT_AVAILABLE` and `FAILED` are
retryable within the attempt limit; `NOT_SUPPORTED`, or exhausting attempts, records
the outcome and moves on.

### Passive Authentication (`kyc/nfc/sod.py`)

Each step is recorded separately, and a chip passes only if all five hold:

1. EF.SOD (tag 77) parses as CMS SignedData containing an LDSSecurityObject.
2. The signed `messageDigest` equals the hash of that LDSSecurityObject.
3. The Document Signer (DSC) signature over the signed attributes verifies
   (RSA PKCS#1 v1.5, RSA-PSS, ECDSA).
4. The DSC chains to a CSCA in the trust store, by subject **and** signature, and is
   within its validity period. A signer from another country reusing a trusted name fails.
5. Every uploaded data group hashes to the value the SOD lists for it.

### Active Authentication (`kyc/nfc/active_auth.py`)

The chip signs the server's fresh 8-byte nonce with the private key matching DG15.
Copied chip data cannot do this, so this step detects clones. RSA uses ISO/IEC 9796-2
scheme 1, and ECDSA uses plain r‖s. Nonces are stored per session, consumed on first
use, voided when a newer one is issued, and expire after `NFC_CHALLENGE_TTL_SECONDS`.
A signature sent without a server challenge is ignored
(`ACTIVE_AUTHENTICATION_WITHOUT_SERVER_CHALLENGE`), because a replayed answer proves nothing.
Chip Authentication (EAC/PACE-CAM) is not verified server-side and is recorded as such.

### Statuses (`kyc/nfc/verify.py`)

| Status | Meaning |
| --- | --- |
| `NFC_NOT_SUPPORTED` | no chip, or no NFC on the device (client report) |
| `NFC_NOT_AVAILABLE` | NFC present but not usable now (client report, retryable) |
| `NFC_FAILED` | unreadable, SOD unparseable, hash/signature mismatch (tampering), or Active Authentication failed (clone) |
| `NFC_READ` | chip read, but authenticity not established (e.g. CSCA not trusted, data group not in SOD) |
| `NFC_VERIFIED` | Passive Authentication passed, MRZ readable, Active Authentication did not fail |

`NFC_VERIFIED` means the data was signed by a trusted issuer and has not been altered.
It does **not** mean the passport or its holder is genuine. The result maps it to
`checks.nfc = PASS` as evidence only; `decision` stays null until the Phase 13 risk engine.

### Cross-checks

- **Chip vs printed page:** DG1 is compared with the OCR'd visual zone (number, DOB,
  expiry, sex, name) and with the printed MRZ's check-digit-protected data lines. The
  unprotected TD3 name line is compared via the printed name only, so OCR noise there is
  not a mismatch. A genuine chip from **another** passport is `NFC_VERIFIED` but raises
  `CHIP_DOCUMENT_MISMATCH`.
- **Chip portrait vs selfie:** only for a verified chip. DG2 (JPEG/JPEG 2000) is embedded
  with SFace, stored as an encrypted `CHIP_PORTRAIT` template, and compared with the
  selfie under the same uncalibrated policy as Phase 9 (best result REVIEW). An unverified
  chip never becomes a face reference.

### Trust store

`NFC_CSCA_TRUST_STORE` is a folder of CSCA certificates (PEM/DER), for example extracted
from the ICAO PKD master list. Its version is a hash of the sorted certificate fingerprints,
and every result records it. Without a trust store, chips can be read but never reach
`NFC_VERIFIED`.

### Privacy

Raw chip files are parsed in memory and **not stored**. `nfc_results` keeps statuses, the
per-step Passive Authentication outcome, per-DG hash states, consistency states, reason
codes and the trust-store version. It does not keep MRZ values, names or images. Uploads
are capped by `MAX_NFC_BYTES` (512 KiB per file) at the middleware and per file.

## 2. Files

```
src/kyc/nfc/lds.py                BER-TLV, DG1 MRZ, DG2 portrait, DG15 public key
src/kyc/nfc/sod.py                Passive Authentication (5 recorded steps)
src/kyc/nfc/active_auth.py        ISO 9796-2 RSA and plain ECDSA verification
src/kyc/nfc/trust.py              CSCA trust store with fingerprint version
src/kyc/nfc/verify.py             status mapping
src/kyc/services/nfc.py           challenge issuance, submission, cross-checks, evidence, state change
src/kyc/api/routes.py             ~ POST /nfc/challenge, POST /nfc
src/kyc/services/results.py       ~ nfc, chip_* checks and NFC_* review flags
src/kyc/db/models.py              ~ NFCChallenge; nfc_results.active_authentication
migrations/versions/0006_phase11.py   + nfc_challenges (forced RLS), active_authentication column
src/kyc/web/capture/capture.js    ~ NFC_REQUIRED hands off to the mobile app
scripts/nfc_simulator.py          + fictional test PKI and synthetic chips for development
tests/nfc_fixtures.py, tests/test_nfc.py   + parser, PA/AA, status and API tests
```

## 3. Validation (5 October 2026)

- **300 tests: 300 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6 and native
  face models ([artifacts/phase11-tests.txt](../artifacts/phase11-tests.txt)).
- Live HTTP E2E as the restricted `kyc_app` role
  ([artifacts/phase11-live-e2e.json](../artifacts/phase11-live-e2e.json)):

| Case | Outcome |
| --- | --- |
| NFC switched off | `NFC_NOT_AVAILABLE`, retry allowed, session stays `NFC_REQUIRED` |
| Genuine chip, same passport | `NFC_VERIFIED`; PA ✓ AA ✓; all 6 consistency checks MATCH; chip face 0.87 → REVIEW (uncalibrated) |
| Clone (correct data, wrong AA key) | `NFC_FAILED`, `ACTIVE_AUTHENTICATION_FAILED`, no face reference |
| DG1 edited after signing | `NFC_FAILED`, `DATA_GROUP_HASH_MISMATCH` |
| Genuine chip of another passport | `NFC_VERIFIED` + `CHIP_DOCUMENT_MISMATCH`; chip face 0.15 |
| Signer not in trust store | `NFC_READ`, `DOCUMENT_SIGNER_NOT_TRUSTED` |
| Superseded challenge replayed | 409 `CHALLENGE_ALREADY_USED` |
| Storage | no identity values or raw chip bytes in `nfc_results`; 0 rows visible to `kyc_app` without tenant context |

The same run re-tested Phase 10 against real YuNet/SFace. A printed photo was moved,
rotated and perspective-tilted for each challenge step. All 5 attempts ended
`CHALLENGE_NOT_COMPLETED`, the session stayed `LIVENESS_REQUIRED`, the 6th challenge
got 429 and a reused challenge got 409. No frames were retained.

## 4. Limits

- No physical chip has been read. Chips are synthetic and signed by a fictional test CSCA.
  The mobile SDK (JMRTD on Android, Core NFC on iOS) is specified, not built; it ships in Phase 16.
- No production CSCA master list is provisioned, and CRLs/revocation are not checked yet.
- Chip Authentication and Terminal Authentication (EAC) are not verified server-side.
- For the E2E, NFC sessions were advanced past liveness with a database update, because
  a photo cannot pass liveness and no live head was filmed.
- Face comparison remains uncalibrated, so chip-portrait matches are at best REVIEW.
