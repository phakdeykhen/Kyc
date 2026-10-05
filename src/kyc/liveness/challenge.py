"""Unpredictable, single-use liveness challenges.

A challenge is a frontal baseline followed by three distinct head movements in random
order (24 possible sequences), plus a 256-bit nonce. Only the nonce's SHA-256 is stored.
A pre-recorded video cannot anticipate the sequence, and an expired or used challenge is
refused. Blinking is deliberately not used (spec §14).
"""

from dataclasses import dataclass
import hashlib
import secrets

MOVES = ("TURN_LEFT", "TURN_RIGHT", "LOOK_UP", "LOOK_DOWN")
BASELINE = "LOOK_STRAIGHT"
INSTRUCTIONS = {
    "LOOK_STRAIGHT": "Look straight at the camera.",
    "TURN_LEFT": "Slowly turn your head to your left.",
    "TURN_RIGHT": "Slowly turn your head to your right.",
    "LOOK_UP": "Tilt your head up slightly.",
    "LOOK_DOWN": "Tilt your head down slightly.",
}


@dataclass(frozen=True)
class IssuedChallenge:
    steps: tuple[str, ...]
    nonce: str
    nonce_hash: str


def nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def issue(rng: secrets.SystemRandom | None = None, moves: int = 3) -> IssuedChallenge:
    rng = rng or secrets.SystemRandom()
    sequence = rng.sample(MOVES, moves)
    nonce = secrets.token_urlsafe(32)
    return IssuedChallenge(steps=(BASELINE, *sequence), nonce=nonce, nonce_hash=nonce_hash(nonce))
