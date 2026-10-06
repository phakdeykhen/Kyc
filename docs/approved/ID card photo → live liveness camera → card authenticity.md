Add an advanced identity verification flow to my existing KYC system.

Do NOT rebuild the existing KYC system.

The new requirement is:

1. User takes a photo of their ID card/passport.
2. System detects and validates the document.
3. System extracts the portrait/photo printed on the ID.
4. User opens the live camera.
5. System performs liveness verification.
6. System captures the best real face frame from the liveness session.
7. System compares the ID portrait against the live face.
8. System independently checks whether the document/card appears genuine.
9. Risk Engine combines all evidence.
10. Final result is PASS / REVIEW / FAIL.

# REQUIRED FLOW

User
↓
Take ID Card Photo
↓
Document Quality Check
↓
Document Detection
↓
Perspective Correction
↓
Document Classification
↓
OCR / MRZ / QR / Barcode
↓
Extract ID Portrait
↓
Document Authenticity Checks
↓
Open Live Camera
↓
Face Detection
↓
Liveness Challenge
↓
Select Best Live Face Frame
↓
Face Alignment
↓
ID Face Embedding
+
Live Face Embedding
↓
1:1 Face Comparison
↓
Cross-Check Everything
↓
Risk Engine
↓
PASS / REVIEW / FAIL

# PART 1 — TAKE ID PHOTO

When the user photographs the ID card, check:

- document fully visible
- four corners visible
- image not blurry
- no major glare
- adequate brightness
- document large enough
- acceptable perspective
- correct side
- only one document
- no obvious screenshot
- no obvious screen replay
- acceptable resolution

If quality is bad:

DO NOT continue.

Return instructions such as:

MOVE_CLOSER
SHOW_ALL_CORNERS
HOLD_STILL
REDUCE_GLARE
MORE_LIGHT
CENTER_DOCUMENT
RECAPTURE

# PART 2 — DETECT AND CROP DOCUMENT

Detect the card/passport boundaries.

Perform perspective correction.

Example:

Camera image

```
  /-------------/
 /     ID      /
/-------------/

        ↓
```

+-----------------------+
\| Corrected ID document |
+-----------------------+

Never perform OCR or face extraction from a heavily tilted original when a corrected version can be generated.

Keep the original image as evidence.

# PART 3 — CHECK DOCUMENT TYPE

Detect:

KH_NATIONAL_ID
KH_NSSF
KH_PASSPORT
PASSPORT
NATIONAL_ID
DRIVING_LICENSE
RESIDENCE_CARD
UNKNOWN

Return:

country
document_type
document_side
version
classification_confidence

If expected:

KH_NATIONAL_ID

but detected:

KH_NSSF

return:

WRONG_DOCUMENT

Do not silently continue.

# PART 4 — EXTRACT ID CARD FACE

Locate the portrait printed on the document.

Example:

+--------------------------------+
\| KINGDOM OF CAMBODIA            |
\|                                |
\|  +---------+                   |
\|  |         |   NAME            |
\|  |  FACE   |   DOB             |
\|  |         |   ID NUMBER       |
\|  +---------+                   |
\|                                |
+--------------------------------+

```
        ↓

  Crop ID portrait

        ↓

  Face detector

        ↓

  Face quality

        ↓

  Face landmarks

        ↓

  Alignment

        ↓

  Template A
```

Check:

- exactly one portrait
- face found
- face sufficiently large
- portrait not heavily blurred
- portrait not fully covered by glare
- eyes/nose/mouth landmarks usable

If the portrait is unusable:

DOCUMENT_PORTRAIT_UNUSABLE

Then:

RECAPTURE

instead of doing a weak face comparison.

# PART 5 — LIVE CAMERA

Do NOT allow the user to upload a gallery selfie as the primary liveness proof.

Open a live camera session.

Detect:

- one face only
- face centered
- appropriate distance
- eyes visible
- adequate light
- low blur
- reasonable head angle
- no major occlusion

Instructions:

CENTER_FACE
MOVE_CLOSER
MOVE_BACK
MORE_LIGHT
LOOK_AT_CAMERA
HOLD_STILL
REMOVE_OCCLUSION

# PART 6 — LIVENESS

Face matching and liveness MUST remain separate.

The user should receive a random challenge.

Examples:

TURN_LEFT
TURN_RIGHT
LOOK_UP
LOOK_STRAIGHT

Generate a different sequence per session.

Example:

Challenge:

1. LOOK_STRAIGHT
2. TURN_RIGHT
3. LOOK_UP

The challenge must be:

random
short-lived
single-use
session-bound

Analyze several video frames.

Check:

- face tracking
- head movement
- expected pose
- continuity between frames
- same person across frames
- flat-photo indicators
- screen replay indicators
- video replay indicators
- virtual-camera/injection signals where supported

A single photo must NOT be sufficient to pass liveness.

# PART 7 — CHOOSE BEST LIVE FACE

Do not simply use the first frame.

From the successful liveness session select the best live frame.

Score frames using:

face sharpness
face size
lighting
pose
eye visibility
occlusion
landmark quality

Example:

Frame 1 = 0.61
Frame 2 = 0.82
Frame 3 = 0.93
Frame 4 = 0.78

Use:

Frame 3

for face comparison.

Create:

Template B

from this live frame.

# PART 8 — FACE ALIGNMENT

Before comparison, align both faces using the same method.

ID portrait:

detect
↓
5 facial landmarks
↓
align
↓
112x112
↓
face embedding model
↓
Template A

Live face:

detect
↓
same landmarks
↓
same alignment
↓
112x112
↓
same face embedding model
↓
Template B

Do not compare embeddings created using incompatible model versions.

Store:

model_name
model_version
alignment_version
quality_scores

# PART 9 — 1:1 FACE MATCH

Compare only:

ID portrait
vs
live person's face

This is:

1:1 verification

NOT:

1 identification

Use cosine similarity or the metric expected by the selected face model.

Return internally:

similarity_score

But never tell the customer:

"80% same person"

unless the score has a scientifically calibrated interpretation.

Use calibrated bands:

MATCH
BORDERLINE
NO_MATCH

Logic:

score >= MATCH_THRESHOLD
→ MATCH

score between thresholds
→ BORDERLINE

score < NO_MATCH_THRESHOLD
→ NO_MATCH

Thresholds MUST come from calibration data.

Do not invent them.

Until calibration is complete:

FACE_MATCH_UNCALIBRATED
→ REVIEW

# PART 10 — BIND LIVENESS TO FACE MATCH

This is critical.

Do not accept:

ID Person A
+
uploaded selfie Person A
+
liveness performed by Person B

The face observed throughout the liveness challenge must be the same face used for Template B.

Flow:

Liveness video
↓
Track face identity through all frames
↓
Challenge completed
↓
Choose best tracked frame
↓
Template B
↓
Compare against Template A

If face identity changes during liveness:

LIVENESS_IDENTITY_CHANGED
→ FAIL

# PART 11 — CHECK CARD AUTHENTICITY

Face matching does NOT prove the ID itself is genuine.

Run document checks independently.

Document authenticity pipeline:

ID image
↓
Quality
↓
Classification
↓
Template/Layout
↓
OCR
↓
MRZ
↓
QR/Barcode
↓
Security checks
↓
Manipulation analysis
↓
Cross-check engine

Check where supported:

- expected card dimensions
- expected layout
- expected portrait position
- expected text regions
- expected logos
- expected field structure
- font/style anomalies
- text alignment
- inconsistent spacing
- portrait replacement
- pasted regions
- metadata indicating editing
- compression inconsistencies
- screenshot indicators
- print/screen reproduction
- moiré patterns
- QR data
- barcode data
- MRZ validity
- NFC data for passport

Do NOT fake unsupported checks.

Return:

SUPPORTED
NOT_SUPPORTED
PASS
REVIEW
FAIL

per document check.

# PART 12 — OCR AND DOCUMENT CROSS-CHECK

Extract:

full name
Khmer name
Latin name
date of birth
sex
document number
expiry
nationality
address
other supported fields

Compare available independent sources:

OCR
↔
MRZ
↔
QR
↔
Barcode
↔
NFC

Example:

OCR DOB:
1998-03-05

MRZ DOB:
1998-03-05

→ MATCH

If:

OCR:
1998-03-05

MRZ:
1998-08-05

→

FIELD_MISMATCH
→ REVIEW

Never silently replace one value with another.

# PART 13 — PASSPORT NFC

For ePassport:

Passport camera
↓
MRZ
↓
NFC read
↓
DG1
DG2
SOD
Certificates
↓
Server verification

DG1 contains identity data.

DG2 contains the passport portrait.

If NFC is cryptographically verified:

Prefer:

DG2 portrait

instead of the printed passport portrait for face comparison.

Flow:

Verified DG2 portrait
↓
Template A

Live liveness face
↓
Template B

A ↔ B

This gives stronger identity evidence.

# PART 14 — FINAL THREE MAJOR CHECKS

The Risk Engine must treat these as separate evidence:

DOCUMENT AUTHENTICITY

FACE MATCH

LIVENESS

For example:

Document:
PASS

Face:
MATCH

Liveness:
PASS

→ potentially PASS

Document:
REVIEW

Face:
MATCH

Liveness:
PASS

→ REVIEW

Document:
PASS

Face:
NO_MATCH

Liveness:
PASS

→ FAIL

Document:
PASS

Face:
MATCH

Liveness:
FAIL

→ FAIL

Document:
PASS

Face:
BORDERLINE

Liveness:
PASS

→ REVIEW

# PART 15 — DECISION MATRIX

Use logic similar to:

Document Genuine
+
Face Match
+
Liveness Pass
+
No serious fraud signal

→ PASS

Document uncertain
+
Face Match
+
Liveness Pass

→ REVIEW

Document Genuine
+
Borderline Face

→ REVIEW

Confirmed Face Mismatch

→ FAIL

Liveness Attack

→ FAIL

Confirmed document tampering

→ FAIL

Low OCR confidence

→ RECAPTURE or REVIEW

Unknown authenticity evidence

→ REVIEW

Never:

UNKNOWN → PASS

# PART 16 — RESULT FORMAT

Return structured internal evidence similar to:

{
"document": {
"type": "KH_NATIONAL_ID",
"quality": "PASS",
"classification": "PASS",
"ocr": "PASS",
"authenticity": "REVIEW"
},
"biometric": {
"document_portrait_quality": "PASS",
"live_face_quality": "PASS",
"face_match": "MATCH",
"face_match_calibrated": true
},
"liveness": {
"result": "PASS",
"attack_detected": false
},
"cross_check": {
"result": "PASS"
},
"risk": {
"decision": "REVIEW",
"reasons": [
"DOCUMENT_AUTHENTICITY_UNVERIFIED"
]
}
}

Do not expose internal raw thresholds to the end user.

# PART 17 — USER EXPERIENCE

User-facing flow should be simple:

1. Scan your ID
2. Hold still
3. Checking your document
4. Look at the camera
5. Follow face movement instructions
6. Checking your face
7. Verification completed

Possible final messages:

Verification successful

We need to review your information

Please take your ID photo again

Face could not be verified

Liveness verification failed

Do not expose complicated internal security details.

# PART 18 — STORAGE

Do not unnecessarily retain liveness video.

Prefer:

process frames
↓
extract necessary evidence
↓
store result
↓
discard raw frames

Store only what policy requires:

face comparison result
model version
quality
decision band
liveness result
attack reason
document evidence
audit information

Biometric templates must be encrypted.

Never expose biometric templates through public APIs.

# PART 19 — SECURITY

Prevent an attacker from replacing:

document photo
selfie
liveness frames
face templates

Bind everything to:

organization_id
session_id
capture ID
nonce/challenge
timestamp
session token

A capture belonging to Session A must never be usable in Session B.

# PART 20 — FINAL TARGET

The finished verification should look conceptually like:

```
            ID CARD
               │
   ┌───────────┴────────────┐
   │                        │
```

Document verification     ID portrait
│                        │
│                  Template A
│                        │
│                        │
│                  FACE MATCH
│                        ▲
│                        │
│                  Template B
│                        │
│                   Best frame
│                        │
│                    LIVENESS
│                        │
└────────────┐      LIVE CAMERA
│          │
▼          ▼

```
                RISK ENGINE

          PASS / REVIEW / FAIL
```

The three requirements must remain independent:

1. Is the document genuine?
2. Is this a live person?
3. Is the live person the same person shown on the document?

Only combine those answers at the Risk Engine.

Do not claim that a face match proves the document is genuine.

Do not claim that liveness proves identity.

Do not claim that readable OCR proves authenticity.

Start by reviewing the existing document portrait extraction, selfie, liveness, face-match, and document-authenticity code. Reuse what already works and implement only missing or weak components.
