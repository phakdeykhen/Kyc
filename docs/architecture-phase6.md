# Phase 6 — International passports and generic identity cards

Status: implemented on 5 October 2026.

## 1. Design

Phase 5 could read a foreign passport only through its MRZ. Phase 6 adds the visual
zone and covers identity and residence cards from countries without a dedicated adapter.

| Session type | Adapter | Sources |
| --- | --- | --- |
| `PASSPORT` | `GenericPassportAdapter` (extends `GenericMRZAdapter`) | printed data page + TD3 MRZ |
| `NATIONAL_ID` | `GenericIDAdapter` | labelled front + TD1/TD2 MRZ (back band) |
| `RESIDENCE_CARD` | `GenericResidenceCardAdapter` | same engine, own type and version |

* **Labels.** ICAO 9303 fixes the zones of a data page but not the wording, so labels are
  matched in English, French and Spanish ("Surname / Nom / Apellidos", "Sex / Sexe",
  "F/F"). Bilingual labels count as one label. At one position the exact, longest
  variant wins ("Given names" over "Given name").
* **Trust order.** The printed value stays canonical. The MRZ fills only what the printed
  zone lacks, and only from fields whose check digits validate. Every disagreement is an
  `MRZ_VISUAL_*_MISMATCH` review flag. A page in an unfamiliar language therefore
  becomes an MRZ-only reading, never a guess.
* **Portrait and script.** Passport visual OCR skips the ICAO portrait area and uses the
  English model (`CardLayout.ocr_languages`), so the Khmer model is never applied to Latin pages.
* **Generic cards.** A valid TD1/TD2 MRZ with an I/A/C document code identifies the card.
  A passport MRZ, or nothing readable, is `DOCUMENT_TYPE_MISMATCH` / `DOCUMENT_NOT_RECOGNIZED`
  (recapture). A front in a script we don't read (classification 0.4, REVIEW) can still
  be carried by a valid back MRZ.
* **Portraits (added during Phase 8–9 validation).** Passport pages declare the ICAO TD3
  portrait zone; generic cards the left of the front. The face service falls back to the whole side.
* **Nationality.** Compared only when printed as a code; demonyms are `NOT_COMPARED`. On
  Cambodian documents, any non-Cambodian wording remains a disagreement.

### Issuing-country check

`ISSUING_COUNTRY` maps the MRZ issuing state to ISO alpha-2 (`kyc/documents/iso3166.py`:
249 ISO codes plus ICAO's `D`, `GBD`/`GBN`/`GBO`/`GBP`/`GBS` and `RKS`). It compares
that with the session country: PASS when they match, REVIEW when they differ or the code
is unknown, NOT_APPLICABLE for organization issuers (UN, EU, stateless documents). With
a check-digit-valid MRZ, the stored `identity_documents.issuing_country` becomes the
MRZ evidence instead of the client's claim.

## 2. Files

```text
src/kyc/documents/adapters/generic_passport.py  + international passport layout
src/kyc/documents/adapters/generic_id.py        + generic ID / residence card adapters
src/kyc/documents/iso3166.py                    + alpha-3 ↔ alpha-2 and ICAO issuer codes
src/kyc/documents/adapters/khmer_label.py       ~ ocr_languages, label priority, nationality comparison
src/kyc/documents/khmer.py                      ~ bilingual sex values (M/H, F/F)
src/kyc/services/documents.py                   ~ per-layout OCR languages, ISSUING_COUNTRY
src/kyc/services/results.py, api/schemas.py     ~ issuing_country check, NOT_COMPARED
tests/test_generic_documents.py, tests/images.py ~ adapters, country codes, ID SPECIMEN renderer
```

## 3. Migration

None. The schema revision stays at Codex's `0004_phase8_9`.

## 4. Security concerns and limits

* The session country is a client claim. A mismatch is evidence for review, not a rejection.
* Generic adapters assume nothing about a country's security features: they read and
  cross-check, never authenticate.
* Label vocabularies are limited (EN/FR/ES). Real foreign documents have not been
  measured. Driving licences remain unsupported until a dedicated adapter exists.
