"""анимеДик — the web application.

How access works, in a nutshell:

  * There is no sign-up. None at all. An endpoint that creates an account
    on request from a browser does not exist in this file — there is
    nothing to look for. Accounts are created by the owner: with a
    console command or from their own account page.
  * An ordinary user signs in with login and password, gets a session,
    and their data lives in the database on the server.
  * A guest gets a temporary pass for an hour. Their session lives only
    in the process's memory and writes nothing to the database. Once
    they leave, everything is gone.
"""

from __future__ import annotations

import asyncio
import calendar
import contextlib
import hashlib
import io
import logging
import os
import re
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import anime, catalog, mail, security, store, twofa

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("anime")

WEB_DIR = os.getenv("WEB_DIR", os.path.join(os.path.dirname(__file__), "..", "web"))
WEB_DIR = os.path.abspath(WEB_DIR)

# Cookies are marked Secure if the site is open over https. In local
# testing over http that is switched off by a variable, otherwise the
# browser will not keep the cookie.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") == "1"
SESSION_COOKIE = "sid"
CSRF_COOKIE = "csrf"

# The API schema is not served: there is no reason to publish a list of endpoints.
SHOW_DOCS = os.getenv("SHOW_DOCS") == "1"

@contextlib.asynccontextmanager
async def lifespan(application: FastAPI):
    """What we do at start-up and at shutdown.

    This used to hold the deprecated startup and shutdown handlers via
    the FastAPI event decorator. They are declared deprecated and in
    coming FastAPI versions will simply stop being called — meaning that
    one day after an upgrade the database would silently fail to be
    created and the cleanup would not start. Lifespan does the same thing
    and is not going anywhere.
    """
    store.init()
    dropped = store.purge_old_sessions()
    if dropped:
        log.info("удалено просроченных сессий: %d", dropped)
    if store.count_admins() == 0:
        log.warning(
            "В базе нет ни одного администратора. Создайте его командой:\n"
            "  python -m api.admin add ВАШ_ЛОГИН --admin"
        )
    if not security.pepper():
        log.warning(
            "SESSION_PEPPER не задан. Отпечатки сессий и метки форм считаются "
            "без секрета сервера. Для рабочего сайта задайте его в .env: "
            "openssl rand -hex 32"
        )
    task = asyncio.create_task(housekeeping())
    application.state.housekeeper = task
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="anime",
    version="3.1",
    docs_url="/docs" if SHOW_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if SHOW_DOCS else None,
    lifespan=lifespan,
)

login_guard = security.LoginGuard()
api_limit = security.RateLimiter(limit=120, period=60)      # общий поток
search_limit = security.RateLimiter(limit=20, period=60)    # обращения к источникам
guest_limit = security.RateLimiter(limit=6, period=3600)    # выдача гостевых пропусков
write_limit = security.RateLimiter(limit=90, period=60)     # запись прогресса
# Episodes and players go out to other people's sites too, only with a
# cache. The shared limit of 120 requests a minute is too loose here: a
# hundred cache misses means a hundred calls to the outside from our
# address, and the source bans exactly us for it.
source_limit = security.RateLimiter(limit=40, period=60)
# Checking a password takes 600,000 rounds — about half a second of
# processor time per call. With no separate limit, a password change can
# be called 120 times a minute and occupy the server with that arithmetic
# alone, while holding just one working sign-in.
pass_limit = security.RateLimiter(limit=8, period=300)
# Parsing a picture is an expensive operation too, and in terms of memory
# the most expensive of them all put together.
avatar_limit = security.RateLimiter(limit=10, period=300)


# --------------------------------------------------------------------------
# Heavy arithmetic goes to a separate thread
# --------------------------------------------------------------------------
# PBKDF2 is deliberately slow: that is how it was designed, to make
# guessing unprofitable. But called right here, it stops the WHOLE server
# for as long as it computes — asyncio runs handlers in a single thread.
# One sign-in froze the page for everyone else, and a dozen wrong
# passwords in a row laid the site out completely, paying no heed to any
# rate limit. Now the computation goes to a separate thread, and the
# event loop stays free.
async def verify_password(password: str, stored: str) -> bool:
    return await asyncio.to_thread(security.verify_password, password, stored)


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(security.hash_password, password)


async def waste_time_like_a_real_check() -> None:
    await asyncio.to_thread(security.waste_time_like_a_real_check)


# ==========================================================================
# Guest sessions — in memory only
# ==========================================================================
class GuestSessions:
    """An hour-long pass. Deliberately not in the database.

    A guest saves nothing: restart the server and there are no guests.
    It also means a guest physically cannot write a single row into
    anyone else's data, even if they find a hole in the checks.
    """

    MAX_ALIVE = 200      # больше двухсот гостей разом не пускаем
    MAX_PER_IP = 8       # и не больше восьми живых пропусков с одного адреса

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def _sweep(self) -> None:
        now = time.time()
        dead = [k for k, v in self._items.items() if v["expires"] <= now]
        for k in dead:
            del self._items[k]

    def create(self, ip: str = "?") -> tuple[str, dict] | None:
        self._sweep()
        if len(self._items) >= self.MAX_ALIVE:
            return None
        # A second line of defence after the rate limit: even if someone
        # manages to ask for passes faster than allowed, all two hundred
        # places will not go to one address and ordinary guests will not
        # find themselves locked out.
        if sum(1 for v in self._items.values() if v.get("ip") == ip) >= self.MAX_PER_IP:
            return None
        token = security.new_token()
        data = {
            "kind": "guest",
            "name": "guest",
            "ip": ip,
            "created": time.time(),
            "expires": time.time() + security.GUEST_TTL,
        }
        self._items[security.token_fingerprint(token)] = data
        return token, data

    def get(self, token: str) -> dict | None:
        if not token:
            return None
        self._sweep()
        return self._items.get(security.token_fingerprint(token))

    def drop(self, token: str) -> None:
        self._items.pop(security.token_fingerprint(token), None)

    def count(self) -> int:
        self._sweep()
        return len(self._items)


guests = GuestSessions()


# ==========================================================================
# Who came in
# ==========================================================================
class Caller:
    """A wrapper over "who is it that sent this request"."""

    def __init__(self, kind: str, user: Any = None, guest: dict | None = None,
                 token: str = "") -> None:
        self.kind = kind            # 'user', 'admin' или 'guest'
        self.user = user
        self.guest = guest
        self.token = token

    @property
    def is_guest(self) -> bool:
        return self.kind == "guest"

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def user_id(self) -> int:
        return int(self.user["id"]) if self.user is not None else 0

    @property
    def name(self) -> str:
        if self.user is not None:
            return self.user["display_name"] or self.user["login"]
        return "guest"


TRUST_PROXY = os.getenv("TRUST_PROXY") == "1"


def client_ip(request: Request) -> str:
    """The visitor's address.

    Behind a reverse proxy the real address arrives in X-Forwarded-For.
    The FIRST element of the chain used to be taken from here — and that
    was a hole. nginx does not replace the header, it appends its own
    address at the end: if a visitor sent `X-Forwarded-For: 1.2.3.4`,
    what reached us was `1.2.3.4, the-real-address`. The first element is
    exactly what the visitor made up. By substituting a new value every
    time, they looked like a new person and completely bypassed both the
    sign-in attempt limit and the guest pass limit.

    We take the last element — our own nginx appends it, and it cannot be
    forged. We also check that it is an address at all rather than an
    arbitrary string.
    """
    if TRUST_PROXY:
        # X-Real-IP is REPLACED by nginx in full on every request
        # (proxy_params puts $remote_addr there). Nothing of one's own can
        # be appended to it — so we ask for it first rather than for
        # X-Forwarded-For. It is also insurance: even if someone later
        # rolls the nginx setting back, the hole will not open by itself.
        real = security.valid_ip(request.headers.get("x-real-ip", ""))
        if real:
            return real
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            # We take the last value: the proxy appended it. The first
            # is what the visitor made up.
            for candidate in reversed(fwd.split(",")):
                ip = security.valid_ip(candidate)
                if ip:
                    return ip
    direct = request.client.host if request.client else ""
    return security.valid_ip(direct) or "?"


def ua_hash(request: Request) -> str:
    ua = request.headers.get("user-agent", "")[:300]
    return hashlib.sha256(ua.encode("utf-8", "ignore")).hexdigest()[:32]


async def whoami(request: Request) -> Caller | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    guest = guests.get(token)
    if guest is not None:
        return Caller("guest", guest=guest, token=token)
    user = store.session_user(token)
    if user is not None:
        return Caller("admin" if user["role"] == "admin" else "user",
                      user=user, token=token)
    return None


async def need_any(request: Request) -> Caller:
    """We let in both a user and a guest. For reading the catalogue."""
    who = await whoami(request)
    if who is None:
        raise HTTPException(status_code=401, detail="Нужно войти")
    if not api_limit.allow(f"{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком много запросов")
    return who


async def need_user(request: Request) -> Caller:
    """Registered only. A guest does not get here."""
    who = await need_any(request)
    if who.is_guest:
        raise HTTPException(
            status_code=403,
            detail="Гостевой доступ только на просмотр. За аккаунтом — к администратору.",
        )
    return who


async def need_admin(request: Request) -> Caller:
    """Administrator only. For everyone else the endpoint does not exist.

    We answer 404 rather than 403: differing response codes give away by
    themselves that there is something at this address. A guest would get
    a 403 from the "not a guest" check and would already know the admin
    page exists — so the role check is done here rather than on top of
    need_user.
    """
    who = await whoami(request)
    if who is None or who.is_guest or not who.is_admin:
        raise HTTPException(status_code=404, detail="Не найдено")
    if not api_limit.allow(f"{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком много запросов")
    return who


def guard_csrf(request: Request) -> None:
    """For everything that changes data we require the cookie and the header to agree."""
    if csrf_exempt(request):
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("x-csrf-token")
    session = request.cookies.get(SESSION_COOKIE) or None
    if not security.csrf_ok(cookie, header, session):
        raise HTTPException(status_code=403, detail="Запрос отклонён: не сходится метка формы")


def csrf_exempt(request: Request) -> bool:
    return request.method in ("GET", "HEAD", "OPTIONS")


# ==========================================================================
# Common security headers
# ==========================================================================
CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https: http:; "     # обложки приходят с чужих доменов
    "media-src 'self' https: http: blob:; "   # видео тоже, плюс blob для потоков
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "script-src 'self'; "                     # только наши файлы, никаких вставок
    "worker-src 'self' blob:; "               # он же работает в отдельном потоке
    "connect-src 'self' https: http:; "       # куски видео качаются с чужих доменов
    "frame-ancestors 'none'; "                # нас нельзя вставить в чужой фрейм
    "base-uri 'none'; form-action 'self'; object-src 'none'"
)


@app.middleware("http")
async def secure_headers(request: Request, call_next):
    try:
        response = await call_next(request)
    except HTTPException as exc:
        # Normally it does not get here: FastAPI turns routing errors
        # into a response earlier. But if it ever does, we return a proper
        # response rather than letting the exception through — otherwise
        # it would carry none of the protective headers set below.
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
    except Exception as exc:                       # noqa: BLE001
        # No internal error may fly to the browser as a traceback
        log.exception("необработанная ошибка: %s", exc)
        response = JSONResponse({"detail": "Внутренняя ошибка"}, status_code=500)

    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=15552000; includeSubDomains"
    # we do not cache application pages: otherwise they are visible via the "back" button after signing out
    path = request.url.path
    if path.startswith("/api/") or path.endswith(".html") or path in ("/", "/watch", "/stats"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/static/"):
        # Scripts and styles need a cache, but always with a re-ask.
        #
        # There used to be nothing here, and the browser decided for
        # itself: with no explicit instruction it picks a lifetime "by
        # eye", from the file's last modification time. The markup
        # meanwhile arrives with no-store, that is, always fresh. The
        # worst combination came out: after a site upgrade a new page ran
        # with old code until the person forced a reload. Errors of that
        # kind can be neither reproduced nor explained — "it does not work
        # for me but it works for you".
        #
        # no-cache does not forbid caching: it requires asking each time
        # whether the file has changed. If it has not, a short 304 answer
        # comes back and the file is taken from the cache. That adds
        # almost no traffic.
        response.headers["Cache-Control"] = "no-cache"
    return response


# How many titles we check for the sake of letters in one cleanup pass.
# The limit is not about us, it is about the sources: a hundred requests
# in a row from one address is a sure way to earn an IP ban.
MAIL_MAX_TITLES = 40


async def last_episode_number(source: str, key: str, title: str) -> int:
    """The number of the latest episode at the source. Zero means we did not find out."""
    try:
        episodes = await anime.find_episodes(source, key, title)
    except Exception:                              # noqa: BLE001
        return 0
    last = 0
    for ep in episodes:
        try:
            last = max(last, int(ep.ordinal))
        except (TypeError, ValueError):
            continue
    return last


async def mail_new_episodes() -> int:
    """Sends out letters about episodes that came out.

    It lives in the cleanup rather than in the /api/updates endpoint for
    exactly one reason: a letter should arrive when the person is NOT on
    the site. Otherwise it would report news to someone who has just seen
    it anyway.

    The mark that a letter went out is stored separately from the one the
    bell goes out by. One field for both would mean either repeat letters
    every hour or a bell that stays silent because "we already wrote".
    """
    if not mail.enabled():
        return 0

    sent = 0
    for user in store.mail_subscribers():
        rows = store.watching_for_mail(user["id"])[:MAIL_MAX_TITLES]
        for row in rows:
            if row["source"] not in anime.SOURCES:
                continue
            last = await last_episode_number(row["source"], row["key"], row["title"])
            if not last:
                continue
            known = int(row["mailed_ep"] or 0)
            # Zero means "we have not counted yet", not "there were no
            # episodes". Without that fork, the very first time letters
            # were switched on would send a letter about every title in
            # the library at once.
            if known <= 0:
                store.set_mailed_ep(user["id"], row["key"], last)
                continue
            if last <= known:
                if last != known:
                    store.set_mailed_ep(user["id"], row["key"], last)
                continue

            about = ""
            try:
                found = await catalog.about(row["title"])
                about = (found or {}).get("about", "")
            except Exception:                      # noqa: BLE001
                pass

            ok = await asyncio.to_thread(
                mail.send_episode, user["email"],
                display_name=user["display_name"] or user["login"],
                title=row["title"], episode=last,
                released=time.strftime("%d.%m.%Y"),
                about=about, poster=row["poster"] or "",
                watch_url=(mail.SITE_URL + "/watch?key=" + row["key"]) if mail.SITE_URL else "",
            )
            # We mark it only on success: if the mail fell away, the
            # letter should go out on the next pass rather than vanish
            # for good.
            if ok:
                store.set_mailed_ep(user["id"], row["key"], last)
                sent += 1
    return sent


async def housekeeping() -> None:
    """Cleanup once an hour.

    Expired sessions used to be removed only at start-up. A server that
    runs for months would pile up dead records and never get rid of them.
    A trifle that in six months becomes a noticeable database file.
    """
    while True:
        try:
            await asyncio.sleep(3600)
            dropped = store.purge_old_sessions()
            if dropped:
                log.info("уборка: удалено просроченных сессий %d", dropped)

            # Unfinished set-ups of sign-in by code. A person could tick
            # the box, see the QR and change their mind — the draft stays
            # in memory forever. Each is small, but they accumulate from
            # visitors, that is, without a ceiling.
            edge = time.time() - 900
            stale = [uid for uid, (_, born) in pending_2fa.items() if born < edge]
            for uid in stale:
                pending_2fa.pop(uid, None)
            if stale:
                log.debug("уборка: выброшено незавершённых настроек кода %d", len(stale))

            sent = await mail_new_episodes()
            if sent:
                log.info("уборка: отправлено писем о новых сериях %d", sent)

            fails, locks = login_guard.size()
            if fails or locks:
                log.debug("счётчик попыток: записей %d, блокировок %d", fails, locks)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                   # noqa: BLE001
            # the cleanup must not bring the server down under any circumstances
            log.warning("уборка споткнулась: %s: %s", type(exc).__name__, exc)


# ==========================================================================
# Request models
# ==========================================================================
class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    # The code from the authenticator app. Asked only of those who
    # switched such a sign-in on; everyone else neither needs the field
    # nor sees it.
    code: str = Field(default="", max_length=32)


class PasswordIn(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=1, max_length=200)


class PasswordCheckIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


# An unfinished set-up of sign-in by code: the secret is created, but the
# person has not yet proved the app accepted it. Putting that in the
# database is not allowed — it is not an account setting, it is a draft.
# It lives in memory and goes stale.
pending_2fa: dict[int, tuple[str, float]] = {}


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=40)
    avatar_color: str | None = Field(default=None, max_length=9)


class SettingsIn(BaseModel):
    """What can be configured at all.

    Cover size, sorting and "show finished" have been taken out of the
    account page: the library is now one list with no tabs, and nobody
    will reconfigure picture size a second time. The fields are deleted
    here too — otherwise they would quietly pile up in the database while
    affecting nothing.

    autonext stayed: the switch lives right on the watch page, under the
    "next episode" button, which is where it is needed. The automatic
    mark at 90% now simply always works — a separate setting for it was
    redundant.
    """
    lang: str | None = Field(default=None, max_length=5)
    depth: str | None = Field(default=None, max_length=10)
    accent: str | None = Field(default=None, max_length=10)
    logo: int | None = Field(default=None, ge=1, le=3)
    autonext: bool | None = None


class ProgressIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    source: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=300)
    # The title's Latin name: the library shows names in the site's
    # language, while the sources know them in Russian only.
    title_en: str = Field(default="", max_length=300)
    poster: str = Field(default="", max_length=600)
    year: int | None = Field(default=None, ge=1900, le=2200)
    genres: str = Field(default="", max_length=300)
    total_eps: int = Field(default=0, ge=0, le=10000)
    watched_ep: int = Field(default=0, ge=0, le=10000)
    position: int = Field(default=0, ge=0, le=24 * 3600)
    status: str = Field(default="watching", max_length=20)


class NewUserIn(BaseModel):
    login: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=200)
    role: str = Field(default="user", max_length=10)
    display_name: str = Field(default="", max_length=40)


# ==========================================================================
# Sign in, sign out, "who am I"
# ==========================================================================
def set_session_cookies(response: Response, token: str, max_age: int) -> str:
    # The form marker is computed from the token itself: planting your
    # own value in the cookie and passing the check is no longer possible.
    csrf = security.csrf_for(token)
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=max_age, httponly=True, secure=COOKIE_SECURE,
        samesite="lax", path="/",
    )
    # this cookie the script must read — it is half of the protection against forged requests
    response.set_cookie(
        CSRF_COOKIE, csrf,
        max_age=max_age, httponly=False, secure=COOKIE_SECURE,
        samesite="lax", path="/",
    )
    return csrf


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@app.post("/api/auth/login")
async def auth_login(body: LoginIn, request: Request, response: Response):
    ip = client_ip(request)
    login = security.normalize_login(body.login)
    # The same key in every branch. The first branch used to count
    # attempts by one value and the rest by another, and the counters
    # lived apart.
    key = security.safe_for_log(login)

    # We check the block FIRST. The login-shape check used to stand
    # ahead of it and honestly spent half a second on a dummy hash for
    # every obviously junk login — that is, guessing that was already
    # blocked still occupied the server with work.
    wait = login_guard.locked_for(key, ip)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Слишком много попыток. Попробуйте через {wait // 60 + 1} мин.",
            headers={"Retry-After": str(wait)},
        )

    # A login of the wrong shape goes no further. We answer with the same
    # phrase as for a wrong password: an outsider must not be able to tell
    # from the text of the answer where exactly they went wrong.
    if security.login_problem(login) is not None:
        await waste_time_like_a_real_check()
        login_guard.note_failure(key, ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    user = store.get_user_by_login(login)
    if user is None or user["disabled"]:
        # we spend as much time as a real check would take,
        # otherwise the response speed shows which logins exist
        await waste_time_like_a_real_check()
        login_guard.note_failure(key, ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    if not await verify_password(body.password, user["pass_hash"]):
        login_guard.note_failure(key, ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    # The password is right. If sign-in by code is on — we do not issue a session yet.
    if user["totp_on"] and user["totp_secret"]:
        if not body.code:
            # A separate response code so the page understands: the
            # password is accepted, only the code is needed. There is no
            # session yet — without the code the account cannot be entered
            # even with the right password.
            raise HTTPException(
                status_code=401,
                detail="Введите код из приложения",
                headers={"X-Need-Code": "1"},
            )
        ok = twofa.verify(user["totp_secret"], body.code)
        if not ok:
            # It may also be a backup code — one of those issued when it was switched on.
            ok = store.use_backup_code(user["id"], twofa.hash_backup(body.code))
        if not ok:
            login_guard.note_failure(key, ip)
            raise HTTPException(status_code=401, detail="Код не подошёл")

    login_guard.note_success(key, ip)
    token = security.new_token()
    store.create_session(user["id"], token, ua_hash(request))
    csrf = set_session_cookies(response, token, security.SESSION_IDLE)
    log.info("вход: %s (%s)", security.safe_for_log(login), user["role"])
    return {"ok": True, "csrf": csrf, "me": me_payload(
        Caller("admin" if user["role"] == "admin" else "user", user=user))}


@app.post("/api/auth/guest")
async def auth_guest(request: Request, response: Response):
    ip = client_ip(request)
    if not guest_limit.allow(f"g:{ip}"):
        raise HTTPException(status_code=429, detail="Слишком часто. Попробуйте позже.")
    made = guests.create(ip)
    if made is None:
        raise HTTPException(status_code=503, detail="Сейчас слишком много гостей. Загляните позже.")
    token, data = made
    csrf = set_session_cookies(response, token, security.GUEST_TTL)
    return {"ok": True, "csrf": csrf,
            "me": me_payload(Caller("guest", guest=data, token=token))}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    """Signing out. We ask for the form marker only from someone who has a session.

    There used to be no check here at all, and the cookies were erased on
    any call — even if no session came with the request. Another site
    could not read the data, but it could send a request here with a form
    and throw the person out of their account for no reason. A trifle,
    but one fixed by a single line.

    If there is no session — we quietly answer "done" and erase nothing:
    otherwise signing out after an expired cookie would run into a 403.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return {"ok": True}
    guard_csrf(request)
    guests.drop(token)
    store.drop_session(token)
    clear_session_cookies(response)
    return {"ok": True}


@app.post("/api/auth/logout-all")
async def auth_logout_all(request: Request, response: Response,
                          who: Caller = Depends(need_user)):
    guard_csrf(request)
    n = store.drop_all_sessions(who.user_id)
    clear_session_cookies(response)
    return {"ok": True, "closed": n}


def me_payload(who: Caller) -> dict:
    if who.is_guest:
        left = max(0, int(who.guest["expires"] - time.time()))
        return {
            "kind": "guest", "role": "guest", "name": "guest",
            "display_name": "guest", "avatar_color": "#D9A97F",
            "has_avatar": False, "expires_in": left,
            "settings": {}, "can_edit": False,
        }
    u = who.user
    return {
        "kind": who.kind, "role": u["role"],
        "name": u["login"], "display_name": u["display_name"] or u["login"],
        "avatar_color": u["avatar_color"],
        "has_avatar": bool(u["avatar_blob"]),
        "expires_in": None,
        "settings": store.get_settings(u["id"]),
        "can_edit": True,
        "sessions": store.count_sessions(u["id"]),
        # Sign-in by code, and letters. The secret itself never leaves
        # for the outside — only "on or off".
        "totp_on": bool(u["totp_on"]),
        "email": u["email"] or "",
        "mail_new_eps": bool(u["mail_new_eps"]),
        "mail_ready": mail.enabled(),
    }


@app.get("/api/me")
async def api_me(request: Request):
    who = await whoami(request)
    if who is None:
        return JSONResponse({"kind": "anon"}, status_code=401)
    # Every call reaches into the database for the session. With no
    # limit, that can be used to load the disk in a loop while holding
    # just one working sign-in.
    if not api_limit.allow(f"{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком много запросов")
    return me_payload(who)


@app.post("/api/me/profile")
async def api_profile(body: ProfileIn, request: Request,
                      who: Caller = Depends(need_user)):
    guard_csrf(request)
    color = body.avatar_color
    if color is not None and not _is_hex_color(color):
        raise HTTPException(status_code=400, detail="Цвет должен быть вида #RRGGBB")
    name = body.display_name
    if name is not None:
        name = _clean_text(name, 40)
        if not name:
            raise HTTPException(status_code=400, detail="Имя не может быть пустым")
    store.set_profile(who.user_id, display_name=name, avatar_color=color)
    return {"ok": True}


@app.post("/api/me/password")
async def api_password(body: PasswordIn, request: Request, response: Response,
                       who: Caller = Depends(need_user)):
    guard_csrf(request)
    # Its own limit on top of the common one: every call is two or three
    # tenths of a second of pure computation, and guessing the current
    # password through this endpoint must be exactly as unprofitable as
    # through the sign-in page.
    if not pass_limit.allow(f"p:{who.user_id}"):
        raise HTTPException(status_code=429,
                            detail="Слишком часто. Попробуйте через несколько минут.")
    # We check the requirements for the new password BEFORE verifying the
    # current one: that is pure arithmetic over the string sent in, it is
    # free and gives nothing away.
    if body.new == body.current:
        # Otherwise a password change "succeeds", cuts off every session
        # and changes nothing: the person is signed out everywhere and
        # cannot tell what for.
        raise HTTPException(status_code=400, detail="Новый пароль совпадает с текущим")
    problem = security.password_problem(body.new)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if not await verify_password(body.current, who.user["pass_hash"]):
        raise HTTPException(status_code=403, detail="Текущий пароль не подошёл")
    new_hash = await hash_password(body.new)
    store.set_password_hash(who.user_id, new_hash)   # заодно обрывает все сессии
    clear_session_cookies(response)
    return {"ok": True, "relogin": True}


@app.post("/api/me/settings")
async def api_settings(body: SettingsIn, request: Request,
                       who: Caller = Depends(need_user)):
    guard_csrf(request)
    data = store.get_settings(who.user_id)
    incoming = {k: v for k, v in body.model_dump().items() if v is not None}
    # allow-lists: what is not listed never reaches the database
    # Allow-lists: we accept exactly those values that have buttons in
    # the interface. Otherwise the database ends up with a value no button
    # corresponds to, and the account page stops saving.
    allowed = {
        "lang": {"ru", "en"},
        "depth": {"deep", "mid"},
        "accent": {"mint", "sky", "lilac", "sand"},
        "logo": {1, 2, 3},
    }
    for key, values in allowed.items():
        if key in incoming and incoming[key] not in values:
            raise HTTPException(status_code=400, detail=f"Недопустимое значение: {key}")
    data.update(incoming)
    # We keep in the database only the keys the application knows today:
    # otherwise junk from old versions piles up in the record and one day
    # stops fitting into the space allotted.
    data = {k: v for k, v in data.items() if k in SettingsIn.model_fields}
    try:
        store.set_settings(who.user_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "settings": data}


# --------------------------------------------------------------------------
# Avatar
# --------------------------------------------------------------------------
AVATAR_MAX_BYTES = 300 * 1024      # присланное изображение
AVATAR_SIDE = 128
# A ceiling on the canvas size. It is not about the file's weight: a
# compressed 300-kilobyte picture unfolds in memory into anything at all.
# The previous fifty million pixels is 150 megabytes for one picture plus
# as much again for the conversion to RGB. A dozen such requests in a row
# and the process has nothing left to breathe with. For a 128×128 circle
# a quarter of that is plenty: 4096×4096 is already more than any phone
# camera gives.
AVATAR_MAX_PIXELS = 4096 * 4096


@app.post("/api/me/avatar")
async def api_avatar(request: Request, who: Caller = Depends(need_user)):
    guard_csrf(request)
    if not avatar_limit.allow(f"a:{who.user_id}"):
        raise HTTPException(status_code=429,
                            detail="Слишком часто. Попробуйте через несколько минут.")

    # First we look at the declared size and only then read.
    # Otherwise a huge file lands whole in the server's memory first, and
    # only afterwards do we say "too big".
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")

    # We read in chunks and break off as soon as the limit is passed:
    # the header cannot be trusted, it may be absent or it may lie.
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > AVATAR_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Файл слишком большой")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        store.set_avatar(who.user_id, None)
        return {"ok": True, "removed": True}

    # Parsing and re-encoding is tens of milliseconds and tens of
    # megabytes of work. Into a separate thread for the same reason as
    # computing the hash: otherwise the whole server stands still for it.
    data = await asyncio.to_thread(_decode_image, raw)
    store.set_avatar(who.user_id, data)
    return {"ok": True}


def _decode_image(raw: bytes) -> bytes:
    """We rebuild the picture from scratch.

    Somebody else's file is never handed on as it is: anything at all can
    be hidden inside it. We open it, crop it to a square and save it with
    our own encoder — what comes out is a knowingly clean JPEG of the
    right size.
    """
    try:
        from PIL import Image
    except ImportError:
        raise HTTPException(status_code=500, detail="На сервере не установлен обработчик картинок")

    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()                       # быстрая проверка целостности
        img = Image.open(io.BytesIO(raw))  # verify() «закрывает» файл, открываем снова
        if img.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("формат " + str(img.format))
        # Protection against a "bomb": a giant canvas in a tiny file. We
        # ask for the size BEFORE the conversion to RGB — up to this line
        # the picture is not yet unfolded in memory, while afterwards it
        # would already be unfolded in full, and the check would be late
        # by exactly the expense it saves us from.
        if img.width * img.height > AVATAR_MAX_PIXELS:
            raise ValueError("слишком большой холст")
        img = img.convert("RGB")
        side = min(img.width, img.height)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((AVATAR_SIDE, AVATAR_SIDE), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue()
    except HTTPException:
        raise
    except Exception as exc:               # noqa: BLE001
        log.info("плохая картинка: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=400, detail="Не удалось прочитать изображение")


@app.get("/api/me/avatar")
async def api_avatar_get(who: Caller = Depends(need_any)):
    if who.is_guest:
        raise HTTPException(status_code=404, detail="Не найдено")
    blob = store.get_avatar(who.user_id)
    if not blob:
        raise HTTPException(status_code=404, detail="Не найдено")
    return Response(
        content=blob,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=60",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ==========================================================================
# The library and progress
# ==========================================================================
@app.get("/api/library")
async def api_library(who: Caller = Depends(need_any)):
    if who.is_guest:
        # we show a guest an empty shelf — they never see anyone else's data
        return {"items": [], "guest": True}
    return {"items": store.library(who.user_id), "guest": False}


@app.post("/api/library/progress")
async def api_progress(body: ProgressIn, request: Request,
                       who: Caller = Depends(need_user)):
    guard_csrf(request)
    if not write_limit.allow(f"w:{who.user_id}"):
        raise HTTPException(status_code=429, detail="Слишком часто")
    if body.status not in store.ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail="Неизвестный статус")
    try:
        saved = store.save_progress(who.user_id, body.model_dump())
    except store.LibraryFull as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, **saved}


@app.post("/api/library/watched")
async def api_watched(body: ProgressIn, request: Request,
                      who: Caller = Depends(need_user)):
    guard_csrf(request)
    if not write_limit.allow(f"w:{who.user_id}"):
        raise HTTPException(status_code=429, detail="Слишком часто")
    # The same check as in /progress. It used not to be here: an unknown
    # status quietly turned into "watching", and an error in the client
    # went unnoticed for months.
    if body.status not in store.ALLOWED_STATUS:
        raise HTTPException(status_code=400, detail="Неизвестный статус")
    try:
        store.save_progress(who.user_id, body.model_dump())
    except store.LibraryFull as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    store.log_watch(who.user_id, body.key, body.title, body.genres,
                    body.watched_ep, body.position)
    return {"ok": True}


class PosterIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    source: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=300)


@app.post("/api/library/poster")
async def api_poster(body: PosterIn, request: Request,
                     who: Caller = Depends(need_user)):
    """Fills in a missing cover for a title in the library.

    The picture arrives in the application together with the card from
    the search. But a title can land in the library another way — from a
    link, from a bookmark, or saved by an older version — and then it
    never had a cover: there was nowhere to ask for it again.
    """
    guard_csrf(request)
    if body.source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    if not source_limit.allow(f"p:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    poster = await anime.find_poster(body.source, body.key, body.title)
    if poster:
        store.set_poster(who.user_id, body.key, poster)
    return {"ok": True, "poster": poster}


# How many titles we check in one pass and how many network calls we make
# at once. The limit is not about us, it is about the sources: a hundred
# requests in a row from one address is a sure way for us specifically to
# earn an IP ban.
UPDATES_MAX_TITLES = 24
UPDATES_PARALLEL = 4


@app.get("/api/updates")
async def api_updates(who: Caller = Depends(need_user)):
    """What in the library has managed to gain a new episode.

    The point is exactly one: to show the bell only when there is
    something to show. It used to list everything you are watching — that
    is, it rang always and about nothing. A notification that arrives
    constantly stops being noticed within a week.

    We compare the number of the latest episode at the source with what
    we remembered last time. More means a new one is out. The episode
    list is taken from the same cache as the watch page uses, so repeat
    visits are almost free.
    """
    if not source_limit.allow(f"u:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")

    rows = [r for r in store.library(who.user_id)
            if r["status"] == "watching" and r["source"] in anime.SOURCES
            and r["key"]][:UPDATES_MAX_TITLES]

    gate = asyncio.Semaphore(UPDATES_PARALLEL)

    async def one(row: dict) -> dict | None:
        async with gate:
            try:
                episodes = await anime.find_episodes(row["source"], row["key"], row["title"])
            except Exception:                      # noqa: BLE001
                # The source is silent — that is not news about an episode, it is just silence.
                return None
        last = 0
        for ep in episodes:
            try:
                last = max(last, int(ep.ordinal))
            except (TypeError, ValueError):
                continue
        known = int(row["total_eps"] or 0)

        # Zero means "we have never counted yet", not "there were no
        # episodes". Without that fork any freshly added title was
        # immediately declared new: "there were 0, now there are 26". The
        # bell rang exactly when the person had just seen everything
        # anyway. We simply remember the number and stay silent — we will
        # compare next time.
        if known <= 0:
            if last:
                store.set_known_eps(who.user_id, row["key"], last)
            return None

        if last <= known:
            # We remember a decrease too: a source may have re-posted
            # the title in pieces, and without this it would ring about a
            # new episode forever.
            if last and last != known:
                store.set_known_eps(who.user_id, row["key"], last)
            return None
        return {
            "key": row["key"], "source": row["source"], "title": row["title"],
            "poster": row["poster"], "year": row["year"], "genres": row["genres"],
            "watched_ep": row["watched_ep"], "position": row["position"],
            "was": known, "now": last, "fresh": last - known,
        }

    found = [x for x in await asyncio.gather(*[one(r) for r in rows]) if x]
    return {"items": found, "checked": len(rows)}


@app.post("/api/updates/seen")
async def api_updates_seen(body: PosterIn, request: Request,
                           who: Caller = Depends(need_user)):
    """"I have seen that a new episode is out" — puts out the notification for a title.

    Without it the bell would stay lit until the person watched up to the
    very latest episode — that is, for weeks.
    """
    guard_csrf(request)
    if body.source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    try:
        episodes = await anime.find_episodes(body.source, body.key, body.title)
    except HTTPException:
        raise
    last = 0
    for ep in episodes:
        try:
            last = max(last, int(ep.ordinal))
        except (TypeError, ValueError):
            continue
    if last:
        store.set_known_eps(who.user_id, body.key, last)
    return {"ok": True, "now": last}


@app.delete("/api/library/{key:path}")
async def api_library_delete(key: str, request: Request,
                             who: Caller = Depends(need_user)):
    guard_csrf(request)
    store.remove_from_library(who.user_id, key[:200])
    return {"ok": True}


@app.get("/api/stats/year")
async def api_stats(
    tz: int = Query(0, ge=-840, le=840, description="сдвиг пояса в минутах"),
    who: Caller = Depends(need_user),
):
    # The start of the year is by the visitor's zone too, otherwise the
    # first hours of the first of January fall into last year for some
    # people and not for others.
    shift = tz * 60
    year = time.strftime("%Y", time.gmtime(time.time() + shift))
    start = calendar.timegm(time.strptime(year + "-01-01", "%Y-%m-%d")) - shift
    return store.year_stats(who.user_id, start, tz)


# ==========================================================================
# Administrator
# ==========================================================================
@app.get("/api/admin/users")
async def admin_users(who: Caller = Depends(need_admin)):
    rows = store.list_users()
    return {"users": [
        {"id": r["id"], "login": r["login"], "role": r["role"],
         "display_name": r["display_name"], "created_at": r["created_at"],
         "disabled": bool(r["disabled"]),
         "sessions": store.count_sessions(r["id"])}
        for r in rows
    ], "guests_now": guests.count()}


@app.post("/api/admin/users")
async def admin_create(body: NewUserIn, request: Request,
                       who: Caller = Depends(need_admin)):
    guard_csrf(request)
    if body.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Роль бывает user или admin")
    # The name is cleaned the same way as on the account page. It used to
    # go into the database as it was: an administrator could accidentally
    # paste a newline or an invisible character into it, and afterwards
    # that name behaved strangely in the log and in the header.
    display_name = _clean_text(body.display_name, 40)
    try:
        login = store.check_new_user(body.login, body.password, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # The hash is computed outside the event loop — see verify_password above.
    pass_hash = await hash_password(body.password)
    try:
        uid = store.create_user_prehashed(login, pass_hash, body.role, display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:                       # noqa: BLE001
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="Такой логин уже занят")
        raise
    log.info("админ %s создал аккаунт %s", who.name, body.login)
    return {"ok": True, "id": uid}


@app.post("/api/admin/users/{user_id}/disable")
async def admin_disable(user_id: int, request: Request,
                        who: Caller = Depends(need_admin)):
    guard_csrf(request)
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Нет такого пользователя")
    # Switching yourself off is not allowed, for exactly the reason
    # deleting yourself is not. The check used to be on deletion only:
    # while there are two administrators, the "switch off" button on your
    # own row quietly closed your own way in and cut off your own session.
    if int(user_id) == who.user_id:
        raise HTTPException(status_code=409, detail="Себя выключить нельзя")
    if target["role"] == "admin" and store.count_admins() <= 1:
        raise HTTPException(status_code=409, detail="Это последний администратор")
    store.set_disabled(user_id, True)
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/enable")
async def admin_enable(user_id: int, request: Request,
                       who: Caller = Depends(need_admin)):
    guard_csrf(request)
    if store.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="Нет такого пользователя")
    store.set_disabled(user_id, False)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete(user_id: int, request: Request,
                       who: Caller = Depends(need_admin)):
    guard_csrf(request)
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Нет такого пользователя")
    if int(user_id) == who.user_id:
        raise HTTPException(status_code=409, detail="Себя удалить нельзя")
    if target["role"] == "admin" and store.count_admins() <= 1:
        raise HTTPException(status_code=409, detail="Это последний администратор")
    store.delete_user(user_id)
    return {"ok": True}


# ==========================================================================
# The anime catalogue
# ==========================================================================
@app.get("/api/sources")
async def api_sources(
    lang: str = Query("ru", max_length=2, description="язык, на котором открыт сайт"),
    who: Caller = Depends(need_any),
):
    """Sources that speak the same language as the site.

    All nine used to be served in a jumble, and an English-speaking
    visitor had eight Russian sites in their menu. Choosing one means
    getting a Russian dub nobody asked for.
    """
    allowed = set(anime.sources_for(lang))
    return [{"id": sid, **meta} for sid, meta in anime.SOURCES.items()
            if sid in allowed]


# The order to walk if the chosen source is silent. Sites go down from
# time to time — then we try the next one rather than showing the person
# an error.
#
# In the public version the list is empty: there are no external sources.
# If you plug in your own (see api/anime.py), list them here — first the
# one you trust most.
FALLBACK_ORDER: list[str] = []

# The same walk for the English site language.
FALLBACK_ORDER_EN: list[str] = []


def fallback_for(lang: str) -> list[str]:
    """The walk order for the language the site is open in."""
    if anime.DEMO:
        # There is nothing to walk: one source, and it serves both languages.
        return list(anime.SOURCES)
    return FALLBACK_ORDER_EN if lang == "en" else FALLBACK_ORDER


async def try_source(source: str, q: str) -> list | None:
    """Searches at one source. A source's silence is not an error, it is None.

    The titles returned are sorted by how well they answer the query: at
    sources this is "show me something similar", and the order in which
    they hand back what they found has nothing to do with the query.
    """
    key = f"search:{source}:{q.strip().lower()}"
    cached = anime.cache_get(key)
    if cached is not None:
        return cached
    try:
        extractor = anime.get_extractor(source)
        results = await extractor.a_search(q)
    except Exception as exc:                       # noqa: BLE001
        log.info("источник %s не ответил: %s: %s", source, type(exc).__name__, str(exc)[:120])
        return None
    # A title with no identifying number cannot be opened anyway: the
    # watch page quietly throws you back to the main page on an empty
    # key. Such cards used to reach the results and looked broken.
    packed = [p for p in (anime.pack(source, r) for r in results) if p["key"]]
    for p in packed:
        p["match"] = round(anime.relevance(q, p["title"]), 3)
    packed.sort(key=lambda p: -p["match"])
    for r in results:
        # An empty number means the source gave nothing by which the
        # title could be recognised again. Such a record must not be put
        # in the cache: every title like it would stick together under
        # one key and substitute for one another.
        rk = anime.stable_key(r)
        if rk:
            anime.cache_put(f"raw:{source}:{rk}", r)
    anime.cache_put(key, packed)
    return packed


@app.get("/api/search")
async def api_search(
    request: Request,
    q: str = Query(..., min_length=2, max_length=100),
    source: str = Query("", max_length=40),
    any_source: bool = Query(True, description="искать дальше, если выбранный молчит"),
    lang: str = Query("ru", max_length=2, description="язык, на котором открыт сайт"),
    who: Caller = Depends(need_any),
):
    if not search_limit.allow(f"s:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком частый поиск, подождите минуту")
    # The source must speak the site's language. If empty — we take the
    # one that is the main one for this language: that way a link with no
    # source works in both languages and never leads to a foreign dub.
    if not source:
        source = anime.default_source(lang)
    if source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    if anime.source_lang(source) != ("en" if lang == "en" else "ru"):
        source = anime.default_source(lang)

    order = [source]
    if any_source:
        order += [s for s in fallback_for(lang) if s != source]

    tried: list[str] = []
    answered = False          # хоть кто-то вообще ответил
    # The best of the unalike: if nobody found anything proper, we will
    # show at least this rather than emptiness. An empty screen for a
    # query that found something somewhere looks like a breakdown.
    weak: tuple[str, list] | None = None

    for name in order:
        tried.append(name)
        rows = await try_source(name, q)
        if rows is None:
            continue          # источник молчит — пробуем следующий
        answered = True
        if not rows:
            continue

        # The walk used to end here, at the very first non-empty answer.
        # Because of that "Атака титанов" ran forever into source A's
        # single junk answer, although the next source in the list knew
        # the right title. Now an unalike answer does not count as an
        # answer.
        good = [r for r in rows if r.get("match", 1) >= anime.MIN_RELEVANCE]
        if good:
            return {"source": name, "tried": tried, "items": good}
        if weak is None:
            weak = (name, rows)

    if weak is not None:
        name, rows = weak
        return {"source": name, "tried": tried, "items": rows, "weak": True}

    # This fork used to be missing: an empty answer from every source
    # ended in the same 502 as a complete network failure. A person
    # searched for a name that does not exist and got "no source is
    # responding" — a message that lies and makes people fix what is not
    # broken.
    if answered:
        return {"source": source, "tried": tried, "items": []}

    raise HTTPException(
        status_code=502,
        detail="Ни один источник сейчас не отвечает. Попробуйте через несколько минут.",
    )


@app.get("/api/episodes")
async def api_episodes(
    key: str = Query(..., max_length=200),
    source: str = Query("source-a", max_length=40),
    title: str = Query("", max_length=300),
    who: Caller = Depends(need_any),
):
    if source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    if not source_limit.allow(f"e:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    found = await anime.find_episodes(source, key, title)
    rows = []
    for ep in found:
        try:
            rows.append({"ordinal": int(ep.ordinal),
                         "title": getattr(ep, "title", "") or ""})
        except (TypeError, ValueError):
            continue
    if not rows:
        raise HTTPException(status_code=502, detail="Источник вернул серии в неизвестном виде")
    return rows


# --------------------------------------------------------------------------
# Dubs
# --------------------------------------------------------------------------
# What a source tells about a dub, and why none of it was visible.
#
# A player object has a `title` field, and it holds exactly what is
# needed: "Озвучка источник A", "Озвучка JAM", "Озвучка студия озвучки",
# "Субтитры крупный сервис". But the code read `name` — it has no field
# by that name at all, getattr returned an empty string, and in the "Dub"
# menu every line said the word "плеер". A choice between eight identical
# "players" is not a choice, it is a lottery.
#
# Second: the same dub arrives several times over. At source C one
# episode of "Магическая битва" comes with fifty-four players — that is
# thirty-seven dubs spread across different video hosts.
#
# Third, and the most expensive: links can only be learned by asking the
# host, and each such question is about seven seconds. Asking all
# fifty-four means making a person wait a minute in front of an empty
# player. Even ten in parallel is ten seconds.
#
# So we ask about exactly one dub: the one the person is going to watch.
# The names of the rest are known at once and for free — they arrived
# with the list of players — and the "Dub" menu is complete. We go for
# links for another dub only when it has been chosen.

# How many dubs we walk through if the first ones do not answer.
DUB_PROBES = 4
# How many hosts we try within one dub. Usually the first one answers.
HOSTS_PER_DUB = 2

_DUB_PREFIX = re.compile(r"^\s*(?:озвучка|дубляж|voice|dub)\s*[:\-–—]?\s*", re.I)
_SUB_PREFIX = re.compile(r"^\s*(?:субтитры|сабы|sub(?:title)?s?)\s*[:\-–—]?\s*", re.I)

# "Оригинал (+субтитры)" is the Japanese track with text over it.
#
# That is what source D calls it, and until now we did not see it: the
# rule above looks for the word "субтитры" at the START of the string,
# while here it is in brackets at the end. Yet it is exactly what people
# look for when they ask for "the original with text": "Атака титанов",
# "Наруто", "Магическая битва" and "Ван-Пис" all have that option at
# source D, while Russian anime sites have only a foreign dub over the
# Japanese.
_ORIG_SUB = re.compile(r"(?:ориг|origin|japan|яп\.)", re.I)
_HAS_SUB = re.compile(r"(?:субтитр|саб[ыов]|\bsubs?\b|subtitle)", re.I)


# These words in the name show that the text is English. The list is
# short and will most likely never fire: a live check of four titles at
# three sources found not one English variant — every subtitle track is
# Russian, the work of Russian fan-subtitle teams and Russian tracks from
# a large service. But if an English variant ever appears, it will be
# recognised rather than passed off as Russian.
_EN_SUB = re.compile(r"\b(?:eng|english|en[-_ ]?sub)\b", re.I)


def is_sub_track(name: str) -> bool:
    """Is this a variant with text over the original track?

    Two kinds: "Субтитры крупный сервис" — that is how anime sites write
    it — and "Оригинал (+субтитры)", which is how source D writes it.
    Only the first used to be seen, because we looked for the word at the
    start of the string.
    """
    text = (name or "").strip()
    if not text:
        return False
    if _SUB_PREFIX.match(text):
        return True
    return bool(_HAS_SUB.search(text) and _ORIG_SUB.search(text))


def sub_lang(name: str) -> str:
    """What language this text is in. Empty if it is not subtitles at all."""
    if not is_sub_track(name):
        return ""
    return "en" if _EN_SUB.search(name or "") else "ru"


def dub_name(src: Any, source: str) -> tuple[str, bool]:
    """Whose dub this is, and whether it is a dub at all.

    The word "Озвучка" at the start of every line is noise: the list is
    called "Озвучка" anyway, and there is no reason to repeat it ten
    times. "Субтитры", though, must not be removed: that is not
    decoration but the difference between listening and reading, and a
    person must see it before clicking, not after.
    """
    raw = str(getattr(src, "title", "") or getattr(src, "name", "") or "").strip()
    if not raw:
        # Sources with a single dub of their own give it no name — it is
        # known anyway, it is them.
        return (anime.SOURCES[source]["label"], False)
    if is_sub_track(raw):
        # "Оригинал (+субтитры)" we leave as it is: every word in that
        # name matters — both that the track is Japanese and that there is
        # text over it.
        return (raw, True)
    return (_DUB_PREFIX.sub("", raw).strip() or raw, False)


def pack_links(videos: Any) -> list[dict]:
    """One player's links: only the working ones, best quality first."""
    links = []
    for v in videos or []:
        url = getattr(v, "url", "")
        if not url:
            continue
        try:
            quality = int(getattr(v, "quality", 0) or 0)
        except (TypeError, ValueError):
            quality = 0
        links.append({"quality": quality, "url": url,
                      "type": str(getattr(v, "type", ""))})
    links.sort(key=lambda x: -x["quality"])
    seen, out = set(), []                          # один адрес приходит дважды
    for link in links:
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        out.append(link)
    return out


@app.get("/api/videos")
async def api_videos(
    key: str = Query(..., max_length=200),
    ordinal: int = Query(..., ge=0, le=100000),
    source: str = Query("source-a", max_length=40),
    title: str = Query("", max_length=300),
    dub: str = Query("", max_length=120, description="какую озвучку открыть"),
    lang: str = Query("ru", max_length=2, description="язык, на котором смотрят"),
    who: Caller = Depends(need_any),
):
    if source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    if not source_limit.allow(f"v:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    found = await anime.find_episodes(source, key, title)

    def same(e) -> bool:
        try:
            return int(e.ordinal) == ordinal
        except (TypeError, ValueError):
            return False

    episode = next((e for e in found if same(e)), None)
    if episode is None:
        raise HTTPException(status_code=404, detail="Такой серии нет")
    try:
        players = await episode.a_get_sources()
    except Exception as exc:                       # noqa: BLE001
        raise anime.upstream_error(exc, "Не удалось получить плееры")

    # We sort them into dubs, keeping the source's order.
    order: list[str] = []
    by_dub: dict[str, dict] = {}
    for player in players:
        name, is_sub = dub_name(player, source)
        if name not in by_dub:
            by_dub[name] = {"name": name, "sub": is_sub, "hosts": []}
            order.append(name)
        by_dub[name]["hosts"].append(player)
    if not order:
        raise HTTPException(status_code=502, detail="Ни один плеер не отдал видео")

    # Dubs first, subtitles after: people want to listen more often than
    # to read.
    #
    # In English it is the other way round. An English dub exists, but
    # people come for it less often than for the original Japanese track
    # with text: that is what the English-language source was added here
    # for. Opening the dub first means substituting for the original,
    # which nobody asked for.
    #
    # The original places are remembered in advance: sort rearranges the
    # very list one would have to search for a position in, and by the
    # second comparison index() fails with "not in list".
    subs_first = lang == "en"
    place = {name: i for i, name in enumerate(order)}
    order.sort(key=lambda n: (by_dub[n]["sub"] != subs_first, place[n]))

    async def links_of(name: str) -> list[dict]:
        """One dub's links: the first host that answers."""
        for host in by_dub[name]["hosts"][:HOSTS_PER_DUB]:
            try:
                links = pack_links(await host.a_get_videos())
            except Exception as exc:               # noqa: BLE001
                log.info("плеер не отдал видео: %s", type(exc).__name__)
                continue
            # A player with not a single link is not a player. Source B
            # hands them over faithfully — two of them for "Наруто 1:
            # Книга искусств ниндзя" — and there is not one video inside:
            # its players work only from CIS addresses. Such an empty
            # player used to reach the page, which showed a dash instead
            # of a quality and said nothing. Silence where nothing is
            # going to work reads as "the site is broken".
            if links:
                return links
        return []

    # If a particular one was asked for — we give only it. If not — we
    # take the first that responds.
    wanted = [dub] if dub and dub in by_dub else order[:DUB_PROBES]
    chosen, videos = "", []
    for name in wanted:
        videos = await links_of(name)
        if videos:
            chosen = name
            break

    if not videos:
        raise HTTPException(status_code=502, detail="Ни один плеер не отдал видео")

    return {
        # The full list is known at once and for free: the names arrived
        # together with the list of players, there is nobody to ask for
        # them. lang says what language the text is in: the page uses it
        # to label honestly what the person is going to get.
        "dubs": [{"name": n, "sub": by_dub[n]["sub"], "lang": sub_lang(n)}
                 for n in order],
        "chosen": chosen,
        "videos": videos,
    }


# ==========================================================================
# The catalogue: description and a random anime
# ==========================================================================
# A separate, stricter counter: behind these endpoints stands somebody
# else's open catalogue. Exceed its limits and the server's address gets
# banned, and the catalogue falls away for everyone at once.
catalog_limit = security.RateLimiter(limit=20, period=60)


@app.get("/api/about")
async def api_about(
    title: str = Query(..., min_length=2, max_length=120),
    lang: str = Query("ru", max_length=2, description="язык, на котором показываем"),
    who: Caller = Depends(need_any),
):
    """A short description of the title with no spoilers.

    The sources the video comes from give no descriptions at all — which
    is why a stub saying "we do not show a description" used to stand
    under the player. Now it is taken from an open catalogue.
    """
    if not catalog_limit.allow(f"c:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    found = await catalog.about(title, "en" if lang == "en" else "ru")
    if not found:
        return {"found": False}
    return {"found": True, **found}


@app.get("/api/random")
async def api_random(who: Caller = Depends(need_any)):
    """A random anime from the catalogue — not from your library."""
    if not catalog_limit.allow(f"r:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    for _ in range(3):          # пустая страница попадается редко, но бывает
        found = await catalog.random_anime()
        if found:
            return {"found": True, **found}
    raise HTTPException(status_code=502,
                        detail="Каталог сейчас не отвечает. Попробуйте ещё раз.")


# ==========================================================================
# Searching by franchise
# ==========================================================================
# What happens here and why search is arranged in two steps.
#
# The search box used to hit the video site directly, and "наруто"
# brought back what that site considers similar: at source A — "Наруто
# Ураганные хроники" and "Боруто", without "Наруто" itself; at source B —
# twenty-one lines in a jumble, where the second season stands after the
# film about Boruto. There is no telling from such a list what to watch
# first.
#
# Now the first step goes to the catalogue and answers the question "what
# anime is this": one "Наруто" card instead of twenty-one lines. The
# second step — on a click — shows all the franchise's parts by year. And
# only the third, once a part is chosen, goes to the video sources for a
# concrete link (/api/resolve).
#
# The split matters for this: the catalogue knows what a franchise is but
# not where the video lies. The sources know where the video is but not
# that "Наруто" and "Ураганные хроники" are one story. We used to ask the
# second for what only the first knows.


@app.get("/api/find")
async def api_find(
    q: str = Query(..., min_length=2, max_length=100),
    lang: str = Query("ru", max_length=2, description="язык, на котором открыт сайт"),
    who: Caller = Depends(need_any),
):
    """Search through the catalogue: one card per franchise.

    Language affects nothing here: the catalogue gives both names at
    once, and which to show is the page's decision. The parameter is
    accepted so as not to refuse an honest request — the other search
    endpoints do expect a language, and sending it to all of them is
    easier than remembering the exception.
    """
    if not catalog_limit.allow(f"f:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком частый поиск, подождите минуту")
    cards = await catalog.search_franchises(q)
    if cards is None:
        # The catalogue is silent. Not an error: the site can search
        # without it, straight at the video sources — worse, but it
        # works. The page makes that fork, so we tell it what happened.
        return {"items": [], "catalog": False}
    return {"items": cards, "catalog": True}


@app.get("/api/franchise")
async def api_franchise(
    id: str = Query(..., min_length=1, max_length=120),
    who: Caller = Depends(need_any),
):
    """Every part of a franchise in order of release."""
    if not catalog_limit.allow(f"p:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    parts = await catalog.franchise_parts(id)
    if parts is None:
        raise HTTPException(status_code=502, detail="Справочник сейчас не отвечает")
    return {"id": id, "items": parts}


@app.get("/api/related")
async def api_related(
    title: str = Query(..., min_length=2, max_length=200),
    who: Caller = Depends(need_any),
):
    """Parts of the same story as the title that is open.

    Needed by the watch page. Search answers the question "what to
    watch", while this one answers what arises once you are in the
    player: which season this is, what came before it and what comes
    after.

    There used to be no answer anywhere. A title landed on the shelf as a
    separate record with one name, and the only way to learn it had a
    second season and three films was to remember it yourself and search
    by hand.
    """
    if not catalog_limit.allow(f"rel:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    parts = await catalog.related_parts(title)
    if parts is None:
        # The catalogue is silent. For the watch page that is a trifle:
        # the block simply will not appear, and the player works as it
        # worked. Bringing down the whole page over it would be out of
        # proportion.
        return {"items": [], "catalog": False}
    return {"items": parts, "catalog": True}


# How closely the name at the source must match for the walk to stop and
# go no further.
#
# The bar stands where only an exact match clears it. The temptation to
# lower it for the sake of speed is there — but that is exactly how it
# came about that for "Наруто" the walk stopped at the first source
# holding "Наруто: Ураганные хроники", and the second season opened
# instead of the first. An extra second of waiting is cheaper than the
# wrong title.
GOOD_ENOUGH = 0.9

# How many times during one resolve we are allowed to go to a source for
# an episode list.
#
# The check is needed because of this. An exact name match does not yet
# mean the title opens. At source C "Наруто: Ураганные хроники" sits
# under exactly the same name as in the catalogue — a match of one — and
# the episode list is not given out at all: the parser trips over the
# double episode "57-58" and falls over. Resolve happily returned that
# title, the person clicked the second part of "Наруто" and landed in a
# player saying "episodes did not load". Meanwhile those very
# "Ураганные хроники" come through perfectly at source A and source B —
# nobody was asking them.
#
# The request can hardly be called redundant: the watch page asks for
# episodes as its very first act, and the answer is already in the cache.
# A ceiling of three attempts keeps the worst case — when it opens
# nowhere — within reason.
PROBE_LIMIT = 3

# What share of the episodes the catalogue promises a source must have
# posted to count as suitable.
#
# Without this check "Ван-Пис" opened at source A, which has seven
# episodes out of one thousand one hundred and seventy-four posted.
# Formally everything is right: the name matched exactly, the episodes
# came through, there is no error. And the person got "episode 1 of 7" of
# a series that has been running for twenty-six years. At sources C and B
# it lies in full — they simply were not asked, because the walk stopped
# at the first one that worked.
#
# The bar is deliberately low. Sources lag behind the catalogue by an
# episode or two constantly, for "Блич" the difference is fourteen
# episodes out of three hundred and sixty-six, and there is nothing to
# find fault with there. What has to be filtered out is not a lag but a
# stub.
ENOUGH_SHARE = 0.6


@app.get("/api/resolve")
async def api_resolve(
    title: str = Query(..., min_length=2, max_length=200),
    title_en: str = Query("", max_length=200),
    source: str = Query("", max_length=40),
    episodes: int = Query(0, ge=0, le=10000,
                          description="сколько серий обещает справочник"),
    lang: str = Query("ru", max_length=2, description="язык, на котором открыт сайт"),
    who: Caller = Depends(need_any),
):
    """Looks for the chosen part at the video sources.

    The catalogue gives a name but no links to episodes — only the
    sources know those. Here the name turns into a pair, "source + the
    title's number", with which the watch page opens.

    The order of the walk: first the Russian name at every source, then
    the Latin one. Russian first is no accident — the sources are
    Russian-language, and that is the form the name lies in there. The
    Latin one saves the day where the translation diverged:
    "Судьба/Ночь схватки" against "Fate/stay night".
    """
    if not source:
        source = anime.default_source(lang)
    if source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    # Opening a Russian dub for someone watching the site in English is
    # not a "fallback", it is a substitution. We go to a source in their
    # language.
    if anime.source_lang(source) != ("en" if lang == "en" else "ru"):
        source = anime.default_source(lang)
    if not search_limit.allow(f"rs:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")

    order = [source] + [s for s in fallback_for(lang) if s != source]
    names = [t for t in (title.strip(), title_en.strip()) if t]
    # The length is checked by the request parsing itself, but two spaces
    # pass it, and after trimming nothing is left. Further down the code
    # an empty list of names would knock over max() on an empty sequence
    # — that is, an internal server error in answer to a malformed
    # request.
    if not names:
        raise HTTPException(status_code=400, detail="Пустое название")

    strong: list[tuple[float, str, dict]] = []     # точное совпадение
    weak: list[tuple[float, str, dict]] = []       # похоже, но не то же
    seen: set[tuple[str, str]] = set()
    probes = [0]                                   # сколько раз ходили за сериями
    answered = False
    # The best of those that actually opened: (match, source, title, episodes).
    live: tuple[float, str, dict, int] | None = None

    def enough(found) -> bool:
        """Whether we can stop at this and go nowhere else."""
        if not found:
            return False
        if probes[0] >= PROBE_LIMIT:
            return True
        if not episodes:                           # справочник не знает, сколько их
            return True
        return found[3] >= episodes * ENOUGH_SHARE

    async def probe(pool: list) -> None:
        """Checks the selected ones one at a time until it finds one complete enough."""
        nonlocal live
        pool.sort(key=lambda c: -c[0])
        while pool and probes[0] < PROBE_LIMIT:
            score, name, row = pool.pop(0)
            probes[0] += 1
            try:
                count = len(await anime.find_episodes(name, row["key"], row["title"]))
            except HTTPException:
                count = 0
            except Exception as exc:               # noqa: BLE001
                log.info("серии не отдались: %s: %s", name, type(exc).__name__)
                count = 0
            if not count:
                continue
            if live is None or count > live[3]:
                live = (score, name, row, count)
            if enough(live):
                return

    for q in names:
        for name in order:
            rows = await try_source(name, q)
            if rows is None:
                continue
            answered = True
            for row in rows:
                mark = (name, str(row["key"]))
                if mark in seen:                   # тот же тайтл со второго названия
                    continue
                seen.add(mark)
                score = max(anime.relevance(t, row["title"]) for t in names)
                if score >= GOOD_ENOUGH:
                    strong.append((score, name, row))
                elif score >= anime.MIN_RELEVANCE:
                    weak.append((score, name, row))
            # An exact match has turned up — we check it at once, and if
            # it is alive and complete we walk no further over other
            # people's sites.
            if strong:
                await probe(strong)
                if enough(live):
                    break
        if enough(live):
            break

    # No exact ones were found, all of them turned out empty or too
    # scanty — we look at the similar ones.
    if not enough(live):
        await probe(weak)

    if live is None:
        if not answered:
            raise HTTPException(
                status_code=502,
                detail="Ни один источник сейчас не отвечает. Попробуйте через несколько минут.",
            )
        raise HTTPException(
            status_code=404,
            detail="Эту часть ни один источник не отдал. Попробуйте другую часть "
                   "или другой источник в панели под плеером.",
        )

    score, name, row, count = live
    return {
        "source": name,
        "key": row["key"],
        "title": row["title"],
        "poster": row.get("poster") or "",
        "year": row.get("year"),
        "genres": row.get("genres") or "",
        # The number from the episode list, not from the search card: the
        # card often has none at all, while here the list is already in
        # hand.
        "episodes_total": count or row.get("episodes_total") or 0,
        "match": round(score, 3),
        # The match is inexact: something similar was found, but not the
        # same thing. The page needs this in order to warn rather than
        # quietly open the wrong title.
        "exact": score >= GOOD_ENOUGH,
    }


# --------------------------------------------------------------------------
# Subtitles
# --------------------------------------------------------------------------
# For whom and what for.
#
# A dub is not the only way to watch. Many prefer the original Japanese
# track with text over it to any dubbing, and until now the site could
# not do that at all: the default source, source A, gives exactly one dub
# of its own and never gives subtitles. So a person opening any title
# never saw subtitles in principle — although next door, at source C,
# they lie for almost everything.
#
# A live check across eight titles:
#
#   source A      at none of them          (one dub of its own, and that is all)
#   source C      at seven out of eight    several subtitle teams
#   source B      at two out of eight
#
# Hence the rule: subtitles are looked for not at the current source but
# at every one in turn. That is expensive — each source costs a search,
# an episode list and a player list — so it runs only on a click and only
# once: the sources' answers are cached.

# How many sources we walk in search of subtitles.
SUB_PROBES = 3

# Where to look for a subtitle track and in what order. In the public
# version the list is empty — it is filled in along with plugging in
# sources.
SUB_ORDER: list[str] = []

# The same for the English site language.
SUB_ORDER_EN: list[str] = []


def is_subs(name: str) -> bool:
    """These are subtitles, not a dub."""
    return is_sub_track(name)


@app.get("/api/subs")
async def api_subs(
    title: str = Query(..., min_length=2, max_length=200),
    title_en: str = Query("", max_length=200),
    ordinal: int = Query(1, ge=0, le=100000),
    skip: str = Query("", max_length=40, description="источник, где уже смотрели"),
    lang: str = Query("ru", max_length=2, description="язык, на котором смотрят"),
    who: Caller = Depends(need_any),
):
    """Looks for a variant with subtitles at any source.

    It answers "where exactly": the source, the title's number there and
    the name of the variant. Opening it afterwards is something the
    ordinary watch page can do.

    found=false is not an error. There may be no subtitles for this
    episode anywhere, and saying so outright is more honest than showing
    emptiness.
    """
    if not search_limit.allow(f"sub:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")

    names = [t for t in (title.strip(), title_en.strip()) if t]
    if not names:
        raise HTTPException(status_code=400, detail="Пустое название")

    # The source that certainly has no subtitles we do not look at twice.
    wanted = list(anime.SOURCES) if anime.DEMO else (
        SUB_ORDER_EN if lang == "en" else SUB_ORDER)
    order = [s for s in wanted if s != skip]
    tried: list[str] = []

    for source in order[:SUB_PROBES]:
        tried.append(source)
        # 1. Does this source have the title.
        ask = names
        best = None
        for q in ask:
            rows = await try_source(source, q)
            if not rows:
                continue
            for row in rows:
                score = max(anime.relevance(t, row["title"]) for t in ask)
                if score >= GOOD_ENOUGH and (best is None or score > best[0]):
                    best = (score, row)
            if best:
                break
        if best is None:
            continue

        row = best[1]
        # 2. Does it have this episode.
        try:
            episodes = await anime.find_episodes(source, row["key"], row["title"])
        except HTTPException:
            continue
        except Exception as exc:                   # noqa: BLE001
            log.info("субтитры: серии не отдались у %s: %s", source, type(exc).__name__)
            continue

        def same(e) -> bool:
            try:
                return int(e.ordinal) == ordinal
            except (TypeError, ValueError):
                return False

        episode = next((e for e in episodes if same(e)), None)
        if episode is None:
            episode = episodes[0] if episodes else None
        if episode is None:
            continue

        # 3. Are there subtitles among the variants.
        try:
            players = await episode.a_get_sources()
        except Exception as exc:                   # noqa: BLE001
            log.info("субтитры: плееры не отдались у %s: %s", source, type(exc).__name__)
            continue

        for player in players:
            name, sub = dub_name(player, source)
            if not sub:
                continue
            return {
                "found": True,
                "source": source,
                "key": row["key"],
                "title": row["title"],
                "dub": name,
                "lang": sub_lang(name),
                "poster": row.get("poster") or "",
                "year": row.get("year"),
                "genres": row.get("genres") or "",
                "episodes_total": len(episodes),
                "tried": tried,
            }

    return {"found": False, "tried": tried}


@app.get("/api/where")
async def api_where(
    title: str = Query(..., min_length=2, max_length=200),
    title_en: str = Query("", max_length=200),
    lang: str = Query("ru", max_length=2, description="язык, на котором открыт сайт"),
    who: Caller = Depends(need_any),
):
    """Which of the sources actually has this title.

    The "Where the video comes from" menu listed all eight sources in a
    row. Half of them do not know this title at all: you switch — and get
    a banner saying "this anime is not at this source". A list where half
    the lines lead to a dead end forces people to go through them by hand
    to find out what the site could have found out itself.

    The sources that take part in the walk are checked. The rest stay in
    the list unchecked: narrow sources answer rarely and slowly, and
    there is no reason to hold a person for extra seconds because of
    them. The page will honestly split the list into "it is here" and
    "not checked".

    Requests to sources are cached, so opening the menu again costs
    nothing.
    """
    if not search_limit.allow(f"w:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")

    names = [t for t in (title.strip(), title_en.strip()) if t]
    if not names:
        raise HTTPException(status_code=400, detail="Пустое название")

    async def has_it(source: str) -> bool:
        for q in names:
            rows = await try_source(source, q)
            if not rows:
                continue
            for row in rows:
                if max(anime.relevance(t, row["title"]) for t in names) >= GOOD_ENOUGH:
                    return True
        return False

    checked = list(fallback_for(lang))
    answers = await asyncio.gather(*(has_it(s) for s in checked),
                                   return_exceptions=True)
    here = [s for s, ok in zip(checked, answers) if ok is True]
    return {
        "here": here,
        "checked": checked,
        # Everything we did not get to: the page knows the list, but let
        # the server decide — the sources live here.
        # Only our own: another language in this list is an invitation to
        # choose something the person will not understand.
        "unknown": [s for s in anime.sources_for(lang) if s not in checked],
    }


# ==========================================================================
# The administrator's announcement
# ==========================================================================
class NewsIn(BaseModel):
    text: str = Field(default="", max_length=store.NEWS_MAX)
    # The English version of the same announcement. There is nothing to
    # translate it by machine with, and no reason to: an announcement is
    # written by a person, and sending it to someone else's translator
    # means handing outside a text that never asked to go there.
    text_en: str = Field(default="", max_length=store.NEWS_MAX)


@app.get("/api/news")
async def api_news(who: Caller = Depends(need_any)):
    """What to show everyone at the top of the page. Empty means nothing."""
    return store.get_news()


@app.post("/api/admin/news")
async def admin_news_set(body: NewsIn, request: Request,
                         who: Caller = Depends(need_admin)):
    guard_csrf(request)
    text = _clean_lines(body.text, store.NEWS_MAX)
    text_en = _clean_lines(body.text_en, store.NEWS_MAX)
    if not text:
        raise HTTPException(status_code=400, detail="Пустое объявление")
    store.set_news(text, text_en)
    log.info("админ %s обновил объявление", who.name)
    return {"ok": True, **store.get_news()}


@app.delete("/api/admin/news")
async def admin_news_clear(request: Request, who: Caller = Depends(need_admin)):
    guard_csrf(request)
    store.clear_news()
    return {"ok": True}


# ==========================================================================
# Signing in with a code from an app
# ==========================================================================
class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)


@app.post("/api/me/2fa/start")
async def twofa_start(request: Request, who: Caller = Depends(need_user)):
    """Prepares the secret and the picture with the code. Until confirmed, not switched on.

    The secret is created anew on every tick of the box: if a person
    started the set-up, changed their mind and started again, the old
    secret must not stay usable.
    """
    guard_csrf(request)
    if not pass_limit.allow(f"t:{who.user_id}"):
        raise HTTPException(status_code=429, detail="Слишком часто. Попробуйте позже.")
    secret = twofa.new_secret()
    # We put it in temporary storage in memory rather than in the
    # database: until the code is confirmed this is not an account
    # setting yet, it is a draft.
    pending_2fa[who.user_id] = (secret, time.time())
    uri = twofa.otpauth_uri(secret, who.user["login"])
    try:
        png = await asyncio.to_thread(twofa.qr_png, uri)
    except ImportError:
        raise HTTPException(status_code=500,
                            detail="На сервере не установлен генератор QR-кодов")
    import base64 as _b64
    return {
        "secret": secret,
        "qr": "data:image/png;base64," + _b64.b64encode(png).decode("ascii"),
    }


@app.post("/api/me/2fa/enable")
async def twofa_enable(body: CodeIn, request: Request,
                       who: Caller = Depends(need_user)):
    """Switches sign-in by code on — only if the code really does agree."""
    guard_csrf(request)
    if not pass_limit.allow(f"t:{who.user_id}"):
        raise HTTPException(status_code=429, detail="Слишком часто. Попробуйте позже.")
    draft = pending_2fa.get(who.user_id)
    if not draft or time.time() - draft[1] > 600:
        pending_2fa.pop(who.user_id, None)
        raise HTTPException(status_code=409,
                            detail="Настройка устарела. Снимите галочку и начните заново.")
    secret = draft[0]
    if not twofa.verify(secret, body.code):
        raise HTTPException(status_code=400, detail="Код не подошёл. Проверьте цифры.")
    codes = twofa.new_backup_codes()
    store.set_totp(who.user_id, secret, True,
                   ",".join(twofa.hash_backup(c) for c in codes))
    pending_2fa.pop(who.user_id, None)
    log.info("вход по коду включён: %s", security.safe_for_log(who.user["login"]))
    # The backup codes are shown exactly once: only their fingerprints
    # lie in the database, and there is nowhere to recover the list from
    # afterwards.
    return {"ok": True, "backup": codes}


@app.post("/api/me/2fa/disable")
async def twofa_disable(body: PasswordCheckIn, request: Request,
                        who: Caller = Depends(need_user)):
    """Switches sign-in by code off. We ask for the password.

    Otherwise anyone who sits down at an unlocked laptop removes the
    protection with one click — and it was put up against exactly that.
    """
    guard_csrf(request)
    if not pass_limit.allow(f"t:{who.user_id}"):
        raise HTTPException(status_code=429, detail="Слишком часто. Попробуйте позже.")
    if not await verify_password(body.password, who.user["pass_hash"]):
        raise HTTPException(status_code=403, detail="Пароль не подошёл")
    store.set_totp(who.user_id, "", False)
    pending_2fa.pop(who.user_id, None)
    return {"ok": True}


class MailIn(BaseModel):
    email: str = Field(default="", max_length=120)
    want: bool = False


@app.post("/api/me/mail")
async def api_mail_prefs(body: MailIn, request: Request,
                         who: Caller = Depends(need_user)):
    """Letters about new episodes: the address and the consent."""
    guard_csrf(request)
    email = body.email.strip()[:120]
    if body.want:
        if not security.looks_like_email(email):
            raise HTTPException(status_code=400, detail="Не похоже на адрес почты")
    store.set_mail_prefs(who.user_id, email, body.want)
    return {"ok": True, "email": email, "want": body.want,
            "sending": mail.enabled()}


@app.get("/api/mode")
async def api_mode():
    """Which mode the site is running in.

    Deliberately open, with no sign-in: the page must show the banner
    about demonstration mode before the person signs in anywhere. Nothing
    beyond a single word can be learned from here.
    """
    return {"demo": anime.DEMO}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ==========================================================================
# Serving the pages
# ==========================================================================
def _is_hex_color(value: str) -> bool:
    if not isinstance(value, str) or len(value) not in (4, 7):
        return False
    if value[0] != "#":
        return False
    return all(c in "0123456789abcdefABCDEF" for c in value[1:])


def _clean_text(value: str, limit: int) -> str:
    """We remove control characters and trim the length."""
    value = "".join(ch for ch in (value or "") if ch.isprintable())
    return value.strip()[:limit]


def _clean_lines(value: str, limit: int) -> str:
    """The same, but keeping the newlines.

    The announcement needs them: an administrator writes it in
    paragraphs, and the banner can show them. Without this a "line\nbreak"
    stuck together into "linebreak" — the control character was thrown
    out along with the break.

    There is no danger in a newline here: the text is shown through
    textContent, that is, as text and not as markup. We remove only the
    other control characters and extra blank lines in a row.
    """
    lines = [
        "".join(ch for ch in line if ch.isprintable()).rstrip()
        for line in (value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    out: list[str] = []
    for line in lines:
        # we leave no more than one blank line in a row
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()[:limit]


PAGES = {"/": "index.html", "/watch": "watch.html", "/stats": "stats.html"}


@app.get("/{page}")
async def page(page: str):
    """We serve only the pages listed in advance.

    That rules out directory traversal: the file name is not assembled
    from what the visitor sent.
    """
    name = PAGES.get("/" + page)
    if name is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return FileResponse(os.path.join(WEB_DIR, name))


@app.get("/")
async def root():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


# Static files: this folder only, StaticFiles itself does not let anything out of it
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
