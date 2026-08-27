"""Signing in with a code from an authenticator app (TOTP).

RFC 6238, the very standard Google Authenticator, Aegis, 1Password and
the rest work by. It is implemented here in full — thirty lines of
arithmetic, and there is no reason to drag in a dependency for them that
would then have to be updated and watched.

Why a code from an app rather than a letter to an email address: a
letter travels through someone else's server, lands in spam and locks
you out exactly when getting in matters most. An app works without a
network and depends on nobody.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

# Time step and tolerance. One step either way is ±30 seconds: enough
# for a person to type the digits over, and small enough that a code
# someone glanced at goes stale quickly.
STEP = 30
WINDOW = 1

SECRET_BYTES = 20          # 160 бит, как советует стандарт
ISSUER = "anime Dick"


def new_secret() -> str:
    """The secret as base32 — apps accept nothing else."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    part = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{part % 1_000_000:06d}"


def verify(secret: str, code: str, at: float | None = None) -> bool:
    """Checks the six-digit code.

    Compared through compare_digest: an ordinary string comparison
    leaves the loop at the first character that differs, and from the
    response time the code can be guessed one digit at a time.
    """
    if not secret or not code:
        return False
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:
        return False
    now = int((at if at is not None else time.time()) // STEP)
    for shift in range(-WINDOW, WINDOW + 1):
        try:
            if hmac.compare_digest(_code_at(secret, now + shift), code):
                return True
        except Exception:                            # noqa: BLE001
            return False
    return False


def otpauth_uri(secret: str, login: str) -> str:
    """The string the app reads out of the QR code."""
    label = quote(f"{ISSUER}:{login}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}"
            f"&issuer={quote(ISSUER, safe='')}&algorithm=SHA1&digits=6&period={STEP}")


def qr_png(data: str) -> bytes:
    """The QR code as a picture.

    Drawn here rather than through someone else's service like
    api.qrserver.com: sending them the link means sending them the
    secret to someone's account. Doing that for convenience is not
    acceptable under any circumstances.
    """
    import qrcode
    from qrcode.image.pil import PilImage
    import io

    img = qrcode.make(data, box_size=6, border=2, image_factory=PilImage)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# --------------------------------------------------------------------------
# One-time codes in case the phone is lost
# --------------------------------------------------------------------------
BACKUP_COUNT = 8


def new_backup_codes() -> list[str]:
    """Backup codes: each usable once.

    Without them losing a phone means losing the account — and sorting
    it out would take a console on the server.
    """
    return ["-".join((secrets.token_hex(2), secrets.token_hex(2)))
            for _ in range(BACKUP_COUNT)]


def hash_backup(code: str) -> str:
    """Backup codes are not stored in the open either."""
    pepper = os.getenv("SESSION_PEPPER", "")
    return hashlib.sha256((pepper + "backup|" + code.strip().lower()).encode()).hexdigest()
