"""Passwords, sessions, protection against guessing.

Everything to do with "who are you" is gathered here. The logic is kept
apart so that it can be tested independently of the web layer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import time
import unicodedata

# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
# PBKDF2 from the standard library: it needs no third-party package, and
# so it adds no further dependency that has to be kept up to date.
# 600,000 iterations is the OWASP recommendation for SHA-256 from 2023 on.
PBKDF2_ROUNDS = 600_000
SALT_BYTES = 16


def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS) -> str:
    """Returns a string of the form pbkdf2_sha256$rounds$salt$hash."""
    if not isinstance(password, str) or not password:
        raise ValueError("пустой пароль")
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return "pbkdf2_sha256${}${}${}".format(rounds, salt.hex(), dk.hex())


def verify_password(password: str, stored: str) -> bool:
    """Checks a password against the stored hash in constant time."""
    try:
        algo, rounds_s, salt_hex, hash_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, rounds)
    # compare_digest — so the hash cannot be guessed character by character from the response time
    return hmac.compare_digest(dk, expected)


# An empty password is checked against this so that a login that does not
# exist takes the same time to handle as one that does. Otherwise the
# response speed reveals which logins have been created.
_DUMMY_HASH = hash_password("dummy-password-for-timing", rounds=PBKDF2_ROUNDS)


def waste_time_like_a_real_check() -> None:
    verify_password("nope", _DUMMY_HASH)


# --------------------------------------------------------------------------
# Password requirements
# --------------------------------------------------------------------------
PASSWORD_MIN = 10
PASSWORD_MAX = 200

# The passwords that get tried first. The list is short, but it covers
# the most obvious options.
WEAK = {
    "password", "password1", "qwerty123", "123456789", "1234567890",
    "iloveyou", "admin12345", "letmein123", "welcome123", "anime12345",
    "qwertyuiop", "1qaz2wsx3edc", "adminadmin", "passw0rd12",
}


def password_problem(password: str) -> str | None:
    """Returns the text of the problem, or None if the password will do."""
    if not isinstance(password, str):
        return "Пароль должен быть строкой"
    if len(password) < PASSWORD_MIN:
        return f"Пароль короче {PASSWORD_MIN} символов"
    if len(password) > PASSWORD_MAX:
        return f"Пароль длиннее {PASSWORD_MAX} символов"
    if password.lower() in WEAK:
        return "Такой пароль подбирают первым же перебором"
    if len(set(password)) < 4:
        return "В пароле слишком мало разных символов"
    return None


def looks_like_email(value: str) -> bool:
    """A rough check of an email address.

    Checking an address for real is pointless: the only reliable way to
    learn that it works is to send a letter to it. Here we catch only an
    obvious typo and keep control characters and spaces out of the
    field, since a letter's headers can be spoiled with those.
    """
    value = (value or "").strip()
    if not 5 <= len(value) <= 120:
        return False
    if any(ch.isspace() or not ch.isprintable() for ch in value):
        return False
    if value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain:
        return False
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    return all(c.isalnum() or c in ".-_+" for c in local) and \
        all(c.isalnum() or c in ".-" for c in domain)


def safe_for_log(value: str) -> str:
    """Removes everything that could spoil a line in the log.

    Only this value reaches the log and the keys of the attempt counter:
    otherwise a newline inside a login could be used to append a forged
    entry to the log.
    """
    return "".join(ch for ch in str(value or "") if ch.isprintable())[:64]


LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30})[a-z0-9]$")


def normalize_login(login: str) -> str:
    """Brings a login to one form, so that Valera and valera are one person.

    Stray spaces and newlines at the edges are removed — copying from a
    messenger often brings them along.

    Inner characters are NOT touched. The temptation to cut out the "bad"
    ones is strong, but it is a mistake: `vale<newline>ra` would then
    quietly turn into `valera` and let a person into someone else's
    account. Strings like that must be rejected by the login_problem
    check, not silently repaired by this function.
    """
    if not isinstance(login, str):
        return ""
    # NFKC folds together lookalike characters such as full-width Latin letters
    return unicodedata.normalize("NFKC", login).strip().lower()[:64]


def login_problem(login: str) -> str | None:
    login = normalize_login(login)
    if not login:
        return "Пустой логин"
    if not LOGIN_RE.match(login):
        return (
            "Логин: латиница, цифры, точка, дефис и подчёркивание. "
            "От 3 до 32 символов, начинается и кончается буквой или цифрой."
        )
    return None


# --------------------------------------------------------------------------
# Session tokens
# --------------------------------------------------------------------------
SESSION_BYTES = 32           # 256 бит случайности — подобрать невозможно
SESSION_IDLE = 14 * 24 * 3600   # две недели без действий — и сессия мертва
SESSION_MAX_LIFE = 90 * 24 * 3600   # даже активная сессия живёт не дольше
GUEST_TTL = 60 * 60          # гость живёт час


def new_token() -> str:
    return secrets.token_urlsafe(SESSION_BYTES)


def pepper() -> str:
    """The server's secret string.

    It is read on every call rather than once at import: that way it can
    be substituted in tests, and changing the string in the settings
    closes every session at once without restarting the process.
    """
    return os.getenv("SESSION_PEPPER", "")


def token_fingerprint(token: str) -> str:
    """We store the token's fingerprint in the database, not the token.

    If the database is stolen, its contents cannot be used to sign in:
    the token cannot be recovered from the fingerprint.
    """
    return hashlib.sha256((pepper() + (token or "")).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Limiting sign-in attempts
# --------------------------------------------------------------------------
class LoginGuard:
    """Counts failed attempts and slows guessing down.

    We count by login and by address separately: otherwise an attacker
    either tries many passwords against one login or one password
    against many logins, and one of the counters lets them through.

    What matters is that entries remove themselves. An entry used to be
    cleaned only at the next call with the same key — and guessing with
    a hundred thousand different logins left a hundred thousand entries
    in memory forever. A slow leak that after a month of work would have
    turned into eaten memory.
    """

    WINDOW = 15 * 60      # окно, за которое помним попытки
    MAX_PER_LOGIN = 8
    MAX_PER_IP = 25
    LOCK_SECONDS = 15 * 60
    MAX_KEYS = 20_000     # потолок на всякий случай
    SWEEP_EVERY = 60      # как часто проходим по всему словарю

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._last_sweep = time.time()

    def _sweep(self, now: float, force: bool = False) -> None:
        """Removes everything expired in one go, not entry by entry.

        force=True arrives when the dictionary has already outgrown its
        ceiling. Without it the cleanup ran once a minute — and during
        that minute guessing could stuff in as many entries as it liked.
        The hole was exactly in the gap between cleanups.
        """
        if not force and now - self._last_sweep < self.SWEEP_EVERY:
            return
        self._last_sweep = now
        for key in [k for k, v in self._fails.items()
                    if not v or now - max(v) >= self.WINDOW]:
            del self._fails[key]
        for key in [k for k, until in self._locked.items() if until <= now]:
            del self._locked[key]
        # If the dictionary swelled anyway — we keep the freshest
        # entries. Forgetting somebody's attempt is not frightening:
        # guessing will run into the limit on the nginx side. Eaten
        # memory is worse.
        if len(self._fails) > self.MAX_KEYS:
            fresh = sorted(self._fails.items(), key=lambda kv: -max(kv[1]))
            self._fails = dict(fresh[: self.MAX_KEYS // 2])
        if len(self._locked) > self.MAX_KEYS:
            fresh = sorted(self._locked.items(), key=lambda kv: -kv[1])
            self._locked = dict(fresh[: self.MAX_KEYS // 2])

    def _clean(self, key: str, now: float) -> list[float]:
        items = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
        if items:
            self._fails[key] = items
        else:
            self._fails.pop(key, None)
        return items

    def size(self) -> tuple[int, int]:
        """For observation: how many entries are in memory right now."""
        return len(self._fails), len(self._locked)

    def locked_for(self, login: str, ip: str) -> int:
        """How many seconds one still may not try. 0 — one may."""
        now = time.time()
        over = len(self._fails) >= self.MAX_KEYS or len(self._locked) >= self.MAX_KEYS
        self._sweep(now, force=over)
        worst = 0
        for key in (f"l:{login}", f"i:{ip}"):
            until = self._locked.get(key, 0)
            if until > now:
                worst = max(worst, int(until - now))
            elif key in self._locked:
                del self._locked[key]
        return worst

    def note_failure(self, login: str, ip: str) -> None:
        now = time.time()
        # The ceiling is checked on every entry: between scheduled
        # cleanups the dictionary must not have time to swell.
        over = len(self._fails) >= self.MAX_KEYS or len(self._locked) >= self.MAX_KEYS
        self._sweep(now, force=over)
        for key, limit in ((f"l:{login}", self.MAX_PER_LOGIN), (f"i:{ip}", self.MAX_PER_IP)):
            items = self._clean(key, now)
            items.append(now)
            self._fails[key] = items
            if len(items) >= limit:
                self._locked[key] = now + self.LOCK_SECONDS
                self._fails.pop(key, None)

    def note_success(self, login: str, ip: str) -> None:
        self._fails.pop(f"l:{login}", None)
        self._locked.pop(f"l:{login}", None)

    def reset(self) -> None:
        self._fails.clear()
        self._locked.clear()
        self._last_sweep = 0.0


class RateLimiter:
    """A simple rate limit: no more than N calls per period.

    Like the sign-in attempt counter, it cleans itself in one go rather
    than key by key: otherwise memory grows with every new visitor and
    never shrinks.
    """

    MAX_KEYS = 20_000
    SWEEP_EVERY = 60

    def __init__(self, limit: int, period: int) -> None:
        self.limit = limit
        self.period = period
        self._hits: dict[str, list[float]] = {}
        self._last_sweep = time.time()

    def _sweep(self, now: float, force: bool = False) -> None:
        if not force and now - self._last_sweep < self.SWEEP_EVERY:
            return
        self._last_sweep = now
        for key in [k for k, v in self._hits.items()
                    if not v or now - max(v) >= self.period]:
            del self._hits[key]
        if len(self._hits) > self.MAX_KEYS:
            fresh = sorted(self._hits.items(), key=lambda kv: -max(kv[1]))
            self._hits = dict(fresh[: self.MAX_KEYS // 2])

    def allow(self, key: str) -> bool:
        now = time.time()
        self._sweep(now, force=len(self._hits) >= self.MAX_KEYS)
        items = [t for t in self._hits.get(key, []) if now - t < self.period]
        if len(items) >= self.limit:
            self._hits[key] = items
            return False
        items.append(now)
        self._hits[key] = items
        return True

    def size(self) -> int:
        return len(self._hits)

    def reset(self) -> None:
        self._hits.clear()
        self._last_sweep = 0.0


# --------------------------------------------------------------------------
# Protection against forged requests from other sites (CSRF)
# --------------------------------------------------------------------------
def csrf_for(session_token: str) -> str:
    """A form marker computed from the session token.

    This used to be simply a random number: the server put it in a cookie
    and then compared the cookie with the header. The weak spot of such a
    scheme is the cookie. Anyone who managed to set a `csrf` cookie in
    the browser (a neighbour on the domain, an access point over http, an
    old infected tab) set both halves of the check at once and passed it.

    Now the marker is a signature of the session fingerprint, and forging
    it without knowing SESSION_PEPPER is impossible. A planted cookie
    with someone else's value will not agree with the session and will be
    rejected. Nothing needs storing: the value is always recomputed from
    the token itself.
    """
    mac = hmac.new(
        ("csrf|" + pepper()).encode("utf-8"),
        token_fingerprint(session_token).encode("ascii"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac)[:32].decode("ascii")


def csrf_ok(from_cookie: str | None, from_header: str | None,
            session_token: str | None = None) -> bool:
    """We check both that the cookie matches the header and the marker's own signature."""
    if not from_cookie or not from_header:
        return False
    if len(from_cookie) < 16 or len(from_cookie) > 200:
        return False
    if not hmac.compare_digest(from_cookie, from_header):
        return False
    if session_token is None:
        # With no session there is nothing to compare against: such
        # requests are cut off by the permission check anyway, and never
        # reach a write to the database.
        return True
    return hmac.compare_digest(from_cookie, csrf_for(session_token))


# --------------------------------------------------------------------------
# The visitor's address
# --------------------------------------------------------------------------
def valid_ip(value: str) -> str:
    """Returns the address if it really is one, otherwise an empty string.

    Needed so that an arbitrary string sent from outside does not land in
    the attempt counter's key: otherwise every request looks like a new
    visitor.
    """
    value = (value or "").strip()
    if not value or len(value) > 45:
        return ""
    # nginx may send the address with a port — we cut it off
    if value.count(":") == 1 and "." in value:
        value = value.split(":")[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""
