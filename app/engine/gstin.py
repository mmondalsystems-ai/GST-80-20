"""GSTIN structural validation and PAN cross-check.

A GSTIN is 15 characters: a 2-digit state code, the holder's 10-character PAN,
a 1-character entity number, the literal 'Z', and a checksum character. The
checksum is a modulus-36 weighted sum, so a mistyped digit is detectable
without contacting the GST portal -- which matters here because the server may
have no outbound internet access.
"""
import re

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

STATE_CODES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}


def checksum_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        v = ALPHABET.index(ch)
        f = v * (2 if i % 2 else 1)
        total += f // 36 + f % 36
    return ALPHABET[(36 - total % 36) % 36]


def validate(gstin: str, pan: str = "") -> tuple[str, str]:
    """Return (code, message). code: ok | empty | bad_format | bad_state |
    bad_checksum | pan_mismatch."""
    g = (gstin or "").strip().upper()
    if not g:
        return "empty", "No GSTIN on file"
    if len(g) != 15:
        return "bad_format", f"GSTIN must be 15 characters — this one has {len(g)}"
    if not GSTIN_RE.match(g):
        return "bad_format", "GSTIN does not match the required 15-character pattern"
    if g[:2] not in STATE_CODES:
        return "bad_state", f"'{g[:2]}' is not a valid state code"
    if checksum_char(g[:14]) != g[14]:
        return "bad_checksum", "Checksum character does not match — the GSTIN is mistyped"
    p = (pan or "").strip().upper()
    if p and PAN_RE.match(p) and g[2:12] != p:
        return "pan_mismatch", f"GSTIN contains PAN {g[2:12]} but the master records PAN {p}"
    return "ok", "Valid"
