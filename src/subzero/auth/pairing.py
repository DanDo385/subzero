"""One-time pairing codes, shared by device pairing and bridge-session pairing.

Codes are short enough to read off one screen and type into another: eight
characters from an alphabet without look-alikes (no 0/O, 1/I). They are only
ever one factor. ``subzero pair-device`` codes are typed by the operator who
ran the command; bridge-session codes are bound to one session and one browser
and are redeemed only by that session's authenticated publisher.
"""

from __future__ import annotations

import hashlib
import secrets

PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 8


def make_pairing_code() -> str:
    """A fresh random code, e.g. ``K7QF3MZP``."""
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def normalize_pairing_code(raw: str) -> str:
    """Accept what a person types: any case, with spaces or a dash in the middle."""
    return "".join(ch for ch in raw.upper() if ch not in " -\t")


def hash_pairing_code(code: str) -> str:
    """What is stored and compared; the code itself is never written down."""
    return hashlib.sha256(normalize_pairing_code(code).encode("utf-8")).hexdigest()
