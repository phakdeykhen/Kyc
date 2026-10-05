"""Server-side ePassport chip verification (ICAO Doc 9303 Parts 10–12).

The mobile app reads the chip; the server never trusts the app's verdict. It re-checks
the chip data itself: Passive Authentication (SOD signature, Document Signer → CSCA
chain, data-group hashes) and Active Authentication against a server nonce.
"""
