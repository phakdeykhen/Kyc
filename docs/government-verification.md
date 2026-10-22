# Government verification after scanning

After the document is processed, the applicant’s scan flow and result page show a
**Government verification** panel. The reviewer’s case page shows the same panel.
When an encrypted scanned QR contains a supported `https://verify.gov.kh/verify/…`
record link, **Open government verification** opens that record in a new tab.

The panel says **Not checked** until an official verification result is available.
Finding a QR link or opening it does not authenticate the document, change the
KYC decision, or add an authenticity source to the risk engine. Documents without
a supported government QR show **Unavailable**; that is not a forgery finding.

## API and access

`GET /v1/kyc/{session_id}/government-verification` returns:

```json
{
  "provider": "VERIFY_GOV_KH",
  "status": "LINK_AVAILABLE",
  "verified": false,
  "verification_url": "https://verify.gov.kh/verify/example-record",
  "portal_url": "https://verify.gov.kh/"
}
```

Statuses are `PENDING`, `LINK_AVAILABLE`, `LINK_RESTRICTED`, `NO_OFFICIAL_QR`,
`UNAVAILABLE`, `LINK_EXPIRED`, and `ERASED`. The same object appears as `government_verification`
in the server result and reviewer case responses.

- A session client token can read only its own session’s link.
- API keys require `sessions:read`; the private link additionally requires
  `results:identity`. Otherwise the status is `LINK_RESTRICTED` and the URL is null.
- Reviewers need `VIEW_IDENTITY` to see the private link. Auditor responses omit it.
- The payload remains encrypted in the existing barcode store; no schema migration
  is needed. Erasure, retention expiry and recapture remove access to the old link.
- Links must use HTTPS, the exact `verify.gov.kh` host, the supported record path,
  and an optional single 64-character hexadecimal `key`. Redirect parameters,
  credentials, lookalike hosts and unexpected ports are rejected.
- The server does not fetch scanned URLs. The browser opens an accepted link only
  when the user clicks, without sending an opener or referrer.

Python clients expose `government_verification(session_id)` and TypeScript clients
expose `governmentVerification(sessionId)` on both server and session clients.

## Automatic government confirmation

This implements the official QR handoff, not an automatic government lookup.
Automatic confirmation requires an authorized government verification API and
documented responses that can be matched to the scanned document. No such API
credentials or integration contract were provided for this project. A successful
HTTP response or a trusted-domain URL alone must not be treated as verification.

The [Digital Government Committee](https://dgc.gov.kh/en/product) describes
Verify.gov.kh as the government’s verification service for documents bearing its
standard QR code. The supported URL format was also checked locally against the
NSSF sample in `ID/NSSF`; the private sample link is not included in this document.
