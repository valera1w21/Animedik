"""анимеДик — веб-приложение.

Устройство доступа в двух словах:

  * Регистрации нет. Совсем. Ручки, которая создаёт аккаунт по запросу
    из браузера, в этом файле не существует — искать нечего.
    Аккаунты заводит владелец: командой в консоли или из своего кабинета.
  * Обычный пользователь входит логином и паролем, получает сессию,
    его данные лежат в базе на сервере.
  * Гость получает временный пропуск на час. Его сессия живёт только
    в памяти процесса и ничего не пишет в базу. Ушёл — всё исчезло.
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

# Куки помечаются Secure, если сайт открыт по https. На локальной проверке
# по http это выключается переменной, иначе браузер куку не сохранит.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") == "1"
SESSION_COOKIE = "sid"
CSRF_COOKIE = "csrf"

# Схема API наружу не отдаётся: незачем публиковать список ручек.
SHOW_DOCS = os.getenv("SHOW_DOCS") == "1"

@contextlib.asynccontextmanager
async def lifespan(application: FastAPI):
    """Что делаем при запуске и при остановке.

    Раньше здесь стояли устаревшие обработчики startup и shutdown
    через декоратор событий FastAPI. Они объявлены
    устаревшими и в следующих версиях FastAPI просто перестанут вызываться —
    то есть однажды после обновления база молча не создалась бы, а уборка
    не запустилась. Lifespan делает то же самое и никуда не денется.
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
# Серии и плееры тоже ходят на чужие сайты, просто с кэшем. Общего лимита
# в 120 запросов в минуту тут мало: сотня промахов мимо кэша — это сотня
# обращений наружу с нашего адреса, за что источник банит именно нас.
source_limit = security.RateLimiter(limit=40, period=60)
# Проверка пароля считается 600 000 раундов — это примерно полсекунды
# процессорного времени на один вызов. Без отдельного ограничения смену
# пароля можно звать 120 раз в минуту и занимать сервер одной этой
# арифметикой, имея всего один действующий вход.
pass_limit = security.RateLimiter(limit=8, period=300)
# Разбор картинки — тоже дорогая операция, и по памяти дороже всего
# остального вместе взятого.
avatar_limit = security.RateLimiter(limit=10, period=300)


# --------------------------------------------------------------------------
# Тяжёлая арифметика — в отдельный поток
# --------------------------------------------------------------------------
# PBKDF2 намеренно медленный: так его и задумывали, чтобы перебор был
# невыгоден. Но вызванный прямо здесь, он останавливает ВЕСЬ сервер на
# время подсчёта — asyncio выполняет обработчики в одном потоке. Один вход
# замораживал страницу у всех остальных, а десяток неверных паролей подряд
# укладывал сайт целиком, не считаясь ни с какими ограничениями частоты.
# Теперь подсчёт уходит в отдельный поток, а цикл событий остаётся свободным.
async def verify_password(password: str, stored: str) -> bool:
    return await asyncio.to_thread(security.verify_password, password, stored)


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(security.hash_password, password)


async def waste_time_like_a_real_check() -> None:
    await asyncio.to_thread(security.waste_time_like_a_real_check)


# ==========================================================================
# Гостевые сессии — только в памяти
# ==========================================================================
class GuestSessions:
    """Пропуска на час. Сознательно не в базе.

    Гость ничего не сохраняет: перезапустили сервер — гостей нет.
    Заодно это значит, что гость физически не может записать ни строки
    в чужие данные, даже если найдёт дыру в проверках.
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
        # Второй рубеж после ограничения частоты: даже если кто-то сумеет
        # запрашивать пропуска быстрее положенного, все двести мест одному
        # адресу не достанутся и обычные гости не окажутся заперты снаружи.
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
# Кто пришёл
# ==========================================================================
class Caller:
    """Обёртка над «кем является тот, кто прислал запрос»."""

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
    """Адрес посетителя.

    За обратным прокси настоящий адрес приходит в X-Forwarded-For. Раньше
    отсюда брался ПЕРВЫЙ элемент цепочки — и это была дыра. nginx не заменяет
    заголовок, а дописывает свой адрес в конец: если посетитель прислал
    `X-Forwarded-For: 1.2.3.4`, до нас доходило `1.2.3.4, настоящий-адрес`.
    Первый элемент — ровно то, что придумал сам посетитель. Подставляя каждый
    раз новое значение, он выглядел как новый человек и полностью обходил
    и ограничение попыток входа, и лимит гостевых пропусков.

    Берём последний элемент — его дописывает наш nginx, подделать его нельзя.
    Заодно проверяем, что это вообще адрес, а не произвольная строка.
    """
    if TRUST_PROXY:
        # X-Real-IP nginx ЗАМЕНЯЕТ целиком на каждом запросе (proxy_params
        # ставит туда $remote_addr). Дописать в него своё значение нельзя —
        # поэтому спрашиваем в первую очередь его, а не X-Forwarded-For.
        # Это ещё и страховка: даже если кто-то потом откатит настройку
        # nginx, дыра сама собой не откроется.
        real = security.valid_ip(request.headers.get("x-real-ip", ""))
        if real:
            return real
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            # Берём последнее значение: его дописал прокси. Первое —
            # то, что придумал сам посетитель.
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
    """Пускаем и пользователя, и гостя. Для чтения каталога."""
    who = await whoami(request)
    if who is None:
        raise HTTPException(status_code=401, detail="Нужно войти")
    if not api_limit.allow(f"{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком много запросов")
    return who


async def need_user(request: Request) -> Caller:
    """Только зарегистрированный. Гость сюда не попадёт."""
    who = await need_any(request)
    if who.is_guest:
        raise HTTPException(
            status_code=403,
            detail="Гостевой доступ только на просмотр. За аккаунтом — к администратору.",
        )
    return who


async def need_admin(request: Request) -> Caller:
    """Только администратор. Всем остальным ручка не существует.

    Отвечаем именно 404, а не 403: разные коды ответа сами по себе выдают,
    что по этому адресу что-то есть. Гость получал бы 403 от проверки
    «не гость» и уже знал бы о существовании админки — поэтому проверку
    роли делаем здесь, а не поверх need_user.
    """
    who = await whoami(request)
    if who is None or who.is_guest or not who.is_admin:
        raise HTTPException(status_code=404, detail="Не найдено")
    if not api_limit.allow(f"{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком много запросов")
    return who


def guard_csrf(request: Request) -> None:
    """Для всего, что меняет данные, требуем совпадения куки и заголовка."""
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
# Общие заголовки безопасности
# ==========================================================================
CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https: http:; "     # обложки приходят с чужих доменов
    "media-src 'self' https: http: blob:; "   # видео тоже, плюс blob для потоков
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "                      # шрифт лежит у нас
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
        # Обычно сюда не доходит: ошибки маршрутов FastAPI превращает
        # в ответ раньше. Но если такое всё же случится, отдаём нормальный
        # ответ, а не пробрасываем исключение дальше — иначе на нём не
        # окажется ни одного защитного заголовка, выставленного ниже.
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
    except Exception as exc:                       # noqa: BLE001
        # Ни одна внутренняя ошибка не должна улететь в браузер трейсбеком
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
    # страницы приложения не кэшируем: иначе после выхода их видно кнопкой «назад»
    path = request.url.path
    if path.startswith("/api/") or path.endswith(".html") or path in ("/", "/watch", "/stats"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/static/"):
        # Скриптам и стилям кэш нужен, но обязательно с переспросом.
        #
        # Раньше здесь не стояло ничего, и браузер решал сам: без явного
        # указания он берёт срок «на глазок», от времени последней правки
        # файла. Разметка при этом приходит с no-store, то есть всегда
        # свежая. Получалось худшее сочетание: после обновления сайта
        # новая страница работала со старым кодом, пока человек не сделает
        # жёсткое обновление. Ошибки в таком виде невозможно ни повторить,
        # ни объяснить — «у меня не работает, а у тебя работает».
        #
        # no-cache не запрещает кэш: он требует каждый раз переспросить,
        # не изменился ли файл. Не изменился — придёт короткий ответ 304,
        # и файл возьмётся из кэша. Трафика это почти не добавляет.
        response.headers["Cache-Control"] = "no-cache"
    return response


# Сколько тайтлов проверяем ради писем за один заход уборки. Ограничение
# не про нас, а про источники: сотня запросов подряд с одного адреса —
# верный способ получить бан по IP.
MAIL_MAX_TITLES = 40


async def last_episode_number(source: str, key: str, title: str) -> int:
    """Номер последней серии у источника. Ноль — значит не узнали."""
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
    """Рассылает письма о вышедших сериях.

    Живёт в уборке, а не в ручке /api/updates, ровно по одной причине:
    письмо должно приходить, когда человека на сайте НЕТ. Иначе оно
    сообщало бы новость тому, кто и так только что её увидел.

    Отметка о том, что письмо ушло, хранится отдельно от той, по которой
    гаснет колокольчик. Одно поле на двоих означало бы либо повторные
    письма каждый час, либо колокольчик, который молчит, потому что
    «уже написали».
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
            # Ноль означает «мы ещё не считали», а не «серий не было».
            # Без этой развилки первое же включение писем присылало бы
            # письмо про каждый тайтл в списке разом.
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
            # Отмечаем только при успехе: если почта отвалилась, письмо
            # должно уйти в следующий заход, а не пропасть насовсем.
            if ok:
                store.set_mailed_ep(user["id"], row["key"], last)
                sent += 1
    return sent


async def housekeeping() -> None:
    """Уборка раз в час.

    Раньше просроченные сессии убирались только при запуске. Сервер,
    который работает месяцами, накапливал бы мёртвые записи и никогда
    от них не избавлялся. Мелочь, которая через полгода становится
    заметным файлом базы.
    """
    while True:
        try:
            await asyncio.sleep(3600)
            dropped = store.purge_old_sessions()
            if dropped:
                log.info("уборка: удалено просроченных сессий %d", dropped)

            # Незавершённые настройки входа по коду. Человек мог поставить
            # галочку, увидеть QR и передумать — черновик остаётся в памяти
            # навсегда. Каждый мелкий, но копятся они от посетителей,
            # то есть без потолка.
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
            # Уборка не должна ронять сервер ни при каких обстоятельствах
            log.warning("уборка споткнулась: %s: %s", type(exc).__name__, exc)


# ==========================================================================
# Модели запросов
# ==========================================================================
class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    # Код из приложения-аутентификатора. Спрашивается только у тех, кто
    # включил такой вход; всем остальным поле не нужно и не показывается.
    code: str = Field(default="", max_length=32)


class PasswordIn(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=1, max_length=200)


class PasswordCheckIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


# Незавершённая настройка входа по коду: секрет создан, но человек ещё не
# доказал, что приложение его приняло. В базу такое класть нельзя — это
# не настройка аккаунта, а черновик. Живёт в памяти и протухает.
pending_2fa: dict[int, tuple[str, float]] = {}


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=40)
    avatar_color: str | None = Field(default=None, max_length=9)


class SettingsIn(BaseModel):
    """Что вообще можно настроить.

    Из кабинета убраны размер обложек, сортировка и «показывать
    законченные»: список теперь один и без вкладок, а перенастраивать
    размер картинок никто не станет второй раз. Поля удалены и здесь —
    иначе они молча копились бы в базе, ни на что не влияя.

    autonext остался: переключатель живёт прямо на странице просмотра,
    под кнопкой «следующая серия», где он и нужен.
    Автоотметка на 90% теперь просто работает всегда — отдельная
    настройка на неё была лишней.
    """
    lang: str | None = Field(default=None, max_length=5)
    depth: str | None = Field(default=None, max_length=10)
    accent: str | None = Field(default=None, max_length=10)
    # Тема и праздник — из списка, а не любая строка. Значение уходит в
    # атрибут страницы, по которому выбирается набор цветов; принимать
    # сюда что попало незачем.
    theme: str | None = Field(default=None, max_length=16,
                              pattern=r"^(kak-seychas|noch|ugol|bumaga)$")
    holiday: str | None = Field(default=None, max_length=16,
                                pattern=r"^(|off|newyear|halloween|sakura)$")
    logo: int | None = Field(default=None, ge=1, le=3)
    autonext: bool | None = None


class ProgressIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    source: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=300)
    # Латинское имя тайтла: список показывает названия на языке сайта,
    # а источники знают их только по-русски.
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
# Вход, выход, «кто я»
# ==========================================================================
def set_session_cookies(response: Response, token: str, max_age: int) -> str:
    # Метка формы считается из самого токена: подставить в куку своё значение
    # и пройти проверку больше нельзя.
    csrf = security.csrf_for(token)
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=max_age, httponly=True, secure=COOKIE_SECURE,
        samesite="lax", path="/",
    )
    # эту куку скрипт читать обязан — она половина защиты от подделки запросов
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
    # Один и тот же ключ во всех ветках. Раньше первая ветка считала попытки
    # по одному значению, а остальные — по другому, и счётчики жили порознь.
    key = security.safe_for_log(login)

    # Блокировку проверяем ПЕРВОЙ. Раньше проверка вида логина стояла раньше
    # неё и на каждый заведомо мусорный логин честно тратила полсекунды
    # на подсчёт хэша-пустышки — то есть уже заблокированный перебор всё
    # равно занимал сервер работой.
    wait = login_guard.locked_for(key, ip)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Слишком много попыток. Попробуйте через {wait // 60 + 1} мин.",
            headers={"Retry-After": str(wait)},
        )

    # Логин неправильного вида дальше не пускаем. Отвечаем той же фразой,
    # что и при неверном пароле: посторонний не должен по тексту ответа
    # понимать, где именно он ошибся.
    if security.login_problem(login) is not None:
        await waste_time_like_a_real_check()
        login_guard.note_failure(key, ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    user = store.get_user_by_login(login)
    if user is None or user["disabled"]:
        # тратим столько же времени, сколько на настоящую проверку,
        # иначе по скорости ответа видно, какие логины существуют
        await waste_time_like_a_real_check()
        login_guard.note_failure(key, ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    if not await verify_password(body.password, user["pass_hash"]):
        login_guard.note_failure(key, ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    # Пароль верный. Если включён вход по коду — сессию пока не выдаём.
    if user["totp_on"] and user["totp_secret"]:
        if not body.code:
            # Отдельный код ответа, чтобы страница поняла: пароль принят,
            # нужен только код. Сессии при этом нет — до кода в аккаунт
            # не попасть даже с верным паролем.
            raise HTTPException(
                status_code=401,
                detail="Введите код из приложения",
                headers={"X-Need-Code": "1"},
            )
        ok = twofa.verify(user["totp_secret"], body.code)
        if not ok:
            # Может быть и запасной код — тот, что выдали при включении.
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
    """Выход. Метку формы спрашиваем только у того, у кого есть сессия.

    Раньше проверки здесь не было совсем, и куки стирались при любом
    обращении — даже если сессии в запросе не пришло. Чужой сайт не мог
    прочитать данные, но мог формой отправить сюда запрос и выкинуть
    человека из аккаунта на ровном месте. Мелочь, но чинится одной строкой.

    Если сессии нет — молча отвечаем «готово» и ничего не стираем:
    иначе выход после протухшей куки упирался бы в 403.
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
        # Вход по коду и письма. Сам секрет наружу не уходит никогда —
        # только «включено или нет».
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
    # Каждый вызов лезет в базу за сессией. Без ограничения этим можно
    # нагружать диск в цикле, имея всего один действующий вход.
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
    # Своё ограничение поверх общего: каждый вызов — две-три десятых секунды
    # чистого счёта, и подбирать текущий пароль через эту ручку должно быть
    # так же невыгодно, как через страницу входа.
    if not pass_limit.allow(f"p:{who.user_id}"):
        raise HTTPException(status_code=429,
                            detail="Слишком часто. Попробуйте через несколько минут.")
    # Требования к новому паролю проверяем ДО сверки текущего: это чистая
    # арифметика над присланной строкой, она бесплатна и не выдаёт ничего.
    if body.new == body.current:
        # Иначе смена пароля «проходит», рвёт все сессии и не меняет ничего:
        # человек выходит отовсюду и не понимает, за что.
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
    # белые списки: что не перечислено, до базы не доходит
    # Белые списки: принимаем ровно те значения, для которых есть кнопки
    # в интерфейсе. Иначе в базе оказывается значение, которому не
    # соответствует ни одна кнопка, и кабинет перестаёт сохраняться.
    allowed = {
        "lang": {"ru", "en"},
        "depth": {"deep", "mid"},
        "accent": {"mint", "sky", "lilac", "sand"},
        "logo": {1, 2, 3},
    }
    for key, values in allowed.items():
        if key in incoming and incoming[key] not in values:
            raise HTTPException(status_code=400, detail=f"Недопустимое значение: {key}")

    # Праздник назначает администратор. Остальным доступны два положения:
    # «по календарю» (пустая строка) и «выключить».
    #
    # Проверка именно здесь, а не только в разметке: спрятанная кнопка —
    # это просьба не нажимать, а не запрет. Разметку видно всегда, и
    # отправить такой запрос руками может кто угодно.
    if incoming.get("holiday") not in (None, "", "off") and not who.is_admin:
        raise HTTPException(status_code=403,
                            detail="Праздничное оформление выбирает администратор")
    data.update(incoming)
    # Оставляем в базе только те ключи, которые сегодня знает приложение:
    # иначе мусор из старых версий копится в записи и однажды перестаёт
    # влезать в отведённое место.
    data = {k: v for k, v in data.items() if k in SettingsIn.model_fields}
    try:
        store.set_settings(who.user_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "settings": data}


# --------------------------------------------------------------------------
# Аватар
# --------------------------------------------------------------------------
AVATAR_MAX_BYTES = 300 * 1024      # присланное изображение
AVATAR_SIDE = 128
# Потолок на размер холста. Дело не в весе файла: сжатая картинка на
# 300 килобайт разворачивается в памяти во что угодно. Прежние пятьдесят
# миллионов точек — это 150 мегабайт на одну картинку плюс столько же на
# перевод в RGB. Десяток таких запросов подряд — и процессу нечем дышать.
# Для кружка 128×128 хватает и вчетверо меньшего: 4096×4096 — это уже
# больше, чем даёт любая камера в телефоне.
AVATAR_MAX_PIXELS = 4096 * 4096


@app.post("/api/me/avatar")
async def api_avatar(request: Request, who: Caller = Depends(need_user)):
    guard_csrf(request)
    if not avatar_limit.allow(f"a:{who.user_id}"):
        raise HTTPException(status_code=429,
                            detail="Слишком часто. Попробуйте через несколько минут.")

    # Сначала смотрим заявленный размер и только потом читаем.
    # Иначе огромный файл сперва целиком окажется в памяти сервера,
    # и лишь затем мы скажем «слишком большой».
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")

    # Читаем по кускам и обрываем, как только вышли за предел:
    # заголовку доверять нельзя, его может не быть или он может врать.
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

    # Разбор и пересжатие — работа на десятки миллисекунд и десятки мегабайт.
    # В отдельный поток по той же причине, что и подсчёт хэша: иначе на это
    # время встаёт весь сервер.
    data = await asyncio.to_thread(_decode_image, raw)
    store.set_avatar(who.user_id, data)
    return {"ok": True}


def _decode_image(raw: bytes) -> bytes:
    """Пересобираем картинку заново.

    Чужой файл никогда не отдаём как есть: в него можно спрятать что угодно.
    Открываем, обрезаем до квадрата и сохраняем своим кодировщиком — на выходе
    заведомо чистый JPEG нужного размера.
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
        # Защита от «бомбы»: гигантский холст при крошечном файле. Размер
        # спрашиваем ДО перевода в RGB — до этой строки картинка ещё не
        # развёрнута в память, а после была бы уже развёрнута целиком,
        # и проверка опаздывала бы ровно на тот расход, от которого спасает.
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
# Библиотека и прогресс
# ==========================================================================
@app.get("/api/library")
async def api_library(who: Caller = Depends(need_any)):
    if who.is_guest:
        # гостю показываем пустую полку — чужих данных он не видит никогда
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
    # Та же проверка, что и в /progress. Раньше её тут не было: неизвестный
    # статус молча превращался в «смотрю», и ошибка в клиенте оставалась
    # незамеченной месяцами.
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
    """Дописывает пропавшую обложку тайтлу из списка.

    Картинка приезжает в приложение вместе с карточкой из поиска. Но
    попасть в список тайтл может и иначе — по ссылке, по закладке, или он
    сохранён старой версией, — и тогда обложки у него не было никогда:
    переспросить её было негде.
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


# Сколько тайтлов проверяем за один заход и сколько ходов в сеть делаем
# разом. Ограничение не про нас, а про источники: сто запросов подряд
# с одного адреса — верный способ получить бан по IP именно нам.
UPDATES_MAX_TITLES = 24
UPDATES_PARALLEL = 4


@app.get("/api/updates")
async def api_updates(who: Caller = Depends(need_user)):
    """Что из списка успело обзавестись новой серией.

    Смысл ровно один: показывать колокольчик только тогда, когда есть что
    показать. Раньше он перечислял всё, что вы смотрите, — то есть звенел
    всегда и ни о чём. Уведомление, которое приходит постоянно, перестают
    замечать за неделю.

    Сравниваем номер последней серии у источника с тем, что запомнили в
    прошлый раз. Больше — значит вышла новая. Список серий берётся из
    того же кэша, что и на странице просмотра, поэтому повторные заходы
    почти бесплатны.
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
                # Источник молчит — это не новость о серии, а просто тишина.
                return None
        last = 0
        for ep in episodes:
            try:
                last = max(last, int(ep.ordinal))
            except (TypeError, ValueError):
                continue
        known = int(row["total_eps"] or 0)

        # Ноль означает «мы ещё ни разу не считали», а не «серий не было».
        # Без этой развилки любой только что добавленный тайтл немедленно
        # объявлялся новинкой: «было 0, стало 26». Колокольчик звенел
        # ровно тогда, когда человек и так только что всё видел.
        # Просто запоминаем число и молчим — сравнивать будем в следующий раз.
        if known <= 0:
            if last:
                store.set_known_eps(who.user_id, row["key"], last)
            return None

        if last <= known:
            # Запоминаем и уменьшение тоже: источник мог перевыложить тайтл
            # кусками, и без этого он звенел бы новой серией вечно.
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
    """«Я видел, что вышла новая серия» — гасит уведомление по тайтлу.

    Без этого колокольчик горел бы до тех пор, пока человек не досмотрит
    до самой свежей серии, — то есть неделями.
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
    # Начало года — тоже по поясу посетителя, иначе первые часы первого
    # января у одних попадут в прошлый год, у других нет.
    shift = tz * 60
    year = time.strftime("%Y", time.gmtime(time.time() + shift))
    start = calendar.timegm(time.strptime(year + "-01-01", "%Y-%m-%d")) - shift
    return store.year_stats(who.user_id, start, tz)


# ==========================================================================
# Администратор
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
    # Имя чистим так же, как в кабинете. Раньше здесь оно уходило в базу как
    # есть: администратор мог случайно вставить в него перевод строки или
    # невидимый символ, и потом это имя странно вело себя в журнале и в шапке.
    display_name = _clean_text(body.display_name, 40)
    try:
        login = store.check_new_user(body.login, body.password, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Хэш считаем вне цикла событий — см. verify_password выше.
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
    # Себя выключать нельзя — ровно по той же причине, по которой нельзя
    # себя удалить. Раньше проверка стояла только на удалении: пока
    # администраторов двое, кнопка «выключить» на своей же строке молча
    # закрывала вход самому себе и обрывала собственную сессию.
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
# Каталог аниме
# ==========================================================================
@app.get("/api/sources")
async def api_sources(
    lang: str = Query("ru", max_length=2, description="язык, на котором открыт сайт"),
    who: Caller = Depends(need_any),
):
    """Источники того же языка, что и сайт.

    Если их не разделять, у англоязычного посетителя в меню оказываются
    и русские источники тоже. Выбрать такой — значит получить озвучку,
    о которой не просили.
    """
    allowed = set(anime.sources_for(lang))
    return [{"id": sid, **meta} for sid, meta in anime.SOURCES.items()
            if sid in allowed]


# Порядок перебора, если выбранный источник молчит. Сайты периодически
# ложатся или меняют адреса — тогда пробуем следующий, а не показываем
# человеку ошибку.
#
# В публичной версии список пуст: внешних источников нет. Если подключите
# свои (см. api/anime.py) — перечислите их здесь, сначала тот, которому
# доверяете больше.
FALLBACK_ORDER: list[str] = []

# Тот же перебор, но для английского сайта.
FALLBACK_ORDER_EN: list[str] = []


def fallback_for(lang: str) -> list[str]:
    """Порядок перебора для языка, на котором открыт сайт."""
    if anime.DEMO:
        # Перебирать нечего: источник один, и он для обоих языков.
        return list(anime.SOURCES)
    return FALLBACK_ORDER_EN if lang == "en" else FALLBACK_ORDER


async def try_source(source: str, q: str) -> list | None:
    """Ищет у одного источника. Молчание источника — не ошибка, а None.

    Возвращённые тайтлы отсортированы по тому, насколько они отвечают
    запросу: у источников это «покажи что-нибудь похожее», и порядок,
    в котором они отдают найденное, к запросу отношения не имеет.
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
    # Тайтл без опознавательного номера открыть всё равно нельзя: страница
    # просмотра по пустому ключу молча выкидывает обратно на главную.
    # Раньше такие карточки попадали в выдачу и выглядели сломанными.
    packed = [p for p in (anime.pack(source, r) for r in results) if p["key"]]
    for p in packed:
        p["match"] = round(anime.relevance(q, p["title"]), 3)
    packed.sort(key=lambda p: -p["match"])
    for r in results:
        # Пустой номер означает, что источник не дал ничего, по чему тайтл
        # можно узнать снова. Такую запись класть в кэш нельзя: все подобные
        # тайтлы слиплись бы в один ключ и подменяли друг друга.
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
    # Источник обязан говорить на языке сайта. Пустой — берём тот, что
    # для этого языка основной: так ссылка без источника работает на
    # обоих языках и никогда не приводит к чужой озвучке.
    # В демо-режиме источник один, а браузер подставляет имя по умолчанию
    # для личной версии. Без «or anime.DEMO» запасной поиск (когда
    # справочник не ответил) получал «Неизвестный источник».
    if not source or anime.DEMO:
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
    # Лучшее из непохожего: если ни у кого не нашлось толком, покажем хотя
    # бы это, а не пустоту. Пустой экран на запрос, который где-то что-то
    # нашёл, выглядит как поломка.
    weak: tuple[str, list] | None = None

    for name in order:
        tried.append(name)
        rows = await try_source(name, q)
        if rows is None:
            continue          # источник молчит — пробуем следующий
        answered = True
        if not rows:
            continue

        # Раньше перебор заканчивался здесь, на первом же непустом ответе.
        # Из-за этого запрос навсегда упирался в единственный мусорный
        # ответ первого источника, хотя следующий в списке знал правильный
        # тайтл. Теперь непохожий ответ не считается ответом.
        good = [r for r in rows if r.get("match", 1) >= anime.MIN_RELEVANCE]
        if good:
            return {"source": name, "tried": tried, "items": good}
        if weak is None:
            weak = (name, rows)

    if weak is not None:
        name, rows = weak
        return {"source": name, "tried": tried, "items": rows, "weak": True}

    # Раньше эта развилка отсутствовала: пустой ответ всех источников
    # заканчивался тем же 502, что и полный отказ сети. Человек искал
    # несуществующее название и получал «ни один источник не отвечает» —
    # сообщение, которое врёт и заставляет чинить то, что не сломано.
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
# Озвучки
# --------------------------------------------------------------------------
# Что источник рассказывает про озвучку и почему этого не было видно.
#
# У объекта плеера есть поле `title`, и в нём написано ровно то, что нужно:
# «Озвучка студии N», «Субтитры студии M». А код читал `name` — поля с
# таким именем у него нет вовсе, getattr возвращал пустую строку, и в
# меню «Озвучка» у каждой строки стояло слово «плеер». Выбор из
# нескольких одинаковых «плееров» — это не выбор, а лотерея.
#
# Второе: одна и та же озвучка приезжает по нескольку раз. У некоторых
# источников на одну серию приходят десятки плееров — это несколько
# озвучек, разложенных по разным видеохостингам.
#
# Третье, и самое дорогое: узнать ссылки можно только спросив хостинг, а
# каждый такой вопрос — несколько секунд. Спросить их все значит
# заставить человека ждать минуту, глядя на пустой плеер. Даже если
# спрашивать параллельно — всё равно заметная задержка.
#
# Поэтому спрашиваем ровно одну озвучку: ту, которую человек будет
# смотреть. Названия остальных известны сразу и бесплатно — они пришли
# вместе со списком плееров, — и в меню «Озвучка» список полный. За
# ссылками для другой озвучки идём, только когда её выбрали.

# Сколько озвучек перебираем, если первые не отвечают.
DUB_PROBES = 4
# Сколько хостингов пробуем внутри одной озвучки. Обычно отвечает первый.
HOSTS_PER_DUB = 2

_DUB_PREFIX = re.compile(r"^\s*(?:озвучка|дубляж|voice|dub)\s*[:\-–—]?\s*", re.I)
_SUB_PREFIX = re.compile(r"^\s*(?:субтитры|сабы|sub(?:title)?s?)\s*[:\-–—]?\s*", re.I)

# «Оригинал (+субтитры)» — японская дорожка с текстом поверх.
#
# У некоторых источников это называется именно так, а правило выше ищет
# слово «субтитры» в НАЧАЛЕ строки, тогда как здесь оно в скобках на
# конце. Между тем это ровно то, что ищут, когда просят «оригинал и
# текст»: у части источников такой вариант есть, а у других — только
# чужая озвучка поверх японской.
_ORIG_SUB = re.compile(r"(?:ориг|origin|japan|яп\.)", re.I)
_HAS_SUB = re.compile(r"(?:субтитр|саб[ыов]|\bsubs?\b|subtitle)", re.I)


# По этим словам в названии видно, что текст английский. Список короткий
# и, скорее всего, почти никогда не сработает: живая проверка не нашла
# ни одного английского варианта у источников, с которыми тестировалось —
# все субтитры были русские. Но если английский вариант когда-нибудь
# появится, он будет опознан, а не выдан за русский.
_EN_SUB = re.compile(r"\b(?:eng|english|en[-_ ]?sub)\b", re.I)


def is_sub_track(name: str) -> bool:
    """Это вариант с текстом поверх оригинальной дорожки?

    Два вида: «Субтитры студии N» — так пишут одни источники, и
    «Оригинал (+субтитры)» — так пишут другие. Раньше видели только
    первый, потому что искали слово в начале строки.
    """
    text = (name or "").strip()
    if not text:
        return False
    if _SUB_PREFIX.match(text):
        return True
    return bool(_HAS_SUB.search(text) and _ORIG_SUB.search(text))


def sub_lang(name: str) -> str:
    """На каком языке этот текст. Пусто — если это вообще не субтитры."""
    if not is_sub_track(name):
        return ""
    return "en" if _EN_SUB.search(name or "") else "ru"


def dub_name(src: Any, source: str) -> tuple[str, bool]:
    """Чья это озвучка и озвучка ли вообще.

    Слово «Озвучка» в начале каждой строки — шум: список и так называется
    «Озвучка», и повторять его десять раз незачем. А вот «Субтитры»
    убирать нельзя: это не оформление, а разница между «слушать» и
    «читать», и человек должен видеть её до нажатия, а не после.
    """
    raw = str(getattr(src, "title", "") or getattr(src, "name", "") or "").strip()
    if not raw:
        # Источники с одной своей озвучкой названия ей не дают — оно и
        # так известно, это они сами.
        return (anime.SOURCES[source]["label"], False)
    if is_sub_track(raw):
        # «Оригинал (+субтитры)» оставляем как есть: в этом названии
        # важно каждое слово — и что дорожка японская, и что текст поверх.
        return (raw, True)
    return (_DUB_PREFIX.sub("", raw).strip() or raw, False)


def pack_links(videos: Any) -> list[dict]:
    """Ссылки одного плеера: только рабочие, лучшее качество первым."""
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

    # Раскладываем по озвучкам, сохраняя порядок источника.
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

    # Озвучки вперёд, субтитры следом: слушать хотят чаще, чем читать.
    #
    # По-английски — наоборот. Английский дубляж существует, но за ним
    # приходят реже, чем за оригинальной японской дорожкой с текстом:
    # ради неё англоязычный источник сюда и добавлен. Открывать дубляж
    # первым значит подменять оригинал, о чём никто не просил.
    #
    # Исходные места запоминаем заранее: sort перекладывает тот же список,
    # по которому пришлось бы искать позицию, и уже на втором сравнении
    # index() падает с «нет в списке».
    subs_first = lang == "en"
    place = {name: i for i, name in enumerate(order)}
    order.sort(key=lambda n: (by_dub[n]["sub"] != subs_first, place[n]))

    async def links_of(name: str) -> list[dict]:
        """Ссылки одной озвучки: первый хостинг, который ответит."""
        for host in by_dub[name]["hosts"][:HOSTS_PER_DUB]:
            try:
                links = pack_links(await host.a_get_videos())
            except Exception as exc:               # noqa: BLE001
                log.info("плеер не отдал видео: %s", type(exc).__name__)
                continue
            # Плеер без единой ссылки не плеер. У некоторых источников он
            # отдаётся исправно, а видео внутри нет ни одного: их плееры
            # работают только с определённых адресов. Такой пустой плеер
            # доезжал до страницы, и она показывала прочерк вместо
            # качества и молчала. Молчание там, где ничего не заработает,
            # читается как «сайт сломался».
            if links:
                return links
        return []

    # Просили определённую — отдаём только её. Не просили — берём первую,
    # которая отзовётся.
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
        # Полный список известен сразу и бесплатно: названия приехали
        # вместе со списком плееров, спрашивать за них никого не нужно.
        # lang говорит, на каком языке текст: страница по нему честно
        # подписывает, что человек получит.
        "dubs": [{"name": n, "sub": by_dub[n]["sub"], "lang": sub_lang(n)}
                 for n in order],
        "chosen": chosen,
        "videos": videos,
    }


# ==========================================================================
# Справочник: описание и случайное аниме
# ==========================================================================
# Отдельный, более строгий счётчик: за этими ручками стоит чужой открытый
# каталог. Превысим его лимиты — забанят адрес сервера, и справочник
# отвалится сразу у всех.
catalog_limit = security.RateLimiter(limit=20, period=60)


@app.get("/api/about")
async def api_about(
    title: str = Query(..., min_length=2, max_length=120),
    lang: str = Query("ru", max_length=2, description="язык, на котором показываем"),
    who: Caller = Depends(need_any),
):
    """Короткое описание тайтла без спойлеров.

    Источники, с которых берётся видео, описаний не дают вовсе — поэтому
    раньше под плеером стояла заглушка «описание не показываем». Теперь
    оно берётся из открытого каталога.
    """
    if not catalog_limit.allow(f"c:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    found = await catalog.about(title, "en" if lang == "en" else "ru")
    if not found:
        return {"found": False}
    return {"found": True, **found}


@app.get("/api/random")
async def api_random(who: Caller = Depends(need_any)):
    """Случайное аниме из каталога — не из вашего списка."""
    if not catalog_limit.allow(f"r:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    for _ in range(3):          # пустая страница попадается редко, но бывает
        found = await catalog.random_anime()
        if found:
            return {"found": True, **found}
    raise HTTPException(status_code=502,
                        detail="Каталог сейчас не отвечает. Попробуйте ещё раз.")


# ==========================================================================
# Поиск по франшизам
# ==========================================================================
# Что тут происходит и почему поиск устроен в два шага.
#
# Раньше строка поиска била прямо в источник видео, и на «наруто»
# приходило то, что этот источник считает похожим: где-то «Наруто
# Ураганные хроники» и «Боруто» без самого «Наруто», где-то десятки
# строк вперемешку, где второй сезон стоит после фильма про Боруто.
# Понять по такому списку, что смотреть первым, нельзя.
#
# Теперь первый шаг идёт в справочник и отвечает на вопрос «что это за
# аниме»: одна карточка «Наруто» вместо двадцати одной строки. Второй
# шаг — по нажатию — показывает все части франшизы по годам. И только
# третий, когда часть выбрана, идёт к источникам видео за конкретной
# ссылкой (/api/resolve).
#
# Разделение важно вот чем: справочник знает, что такое франшиза, но не
# знает, где лежит видео. Источники знают, где видео, но не знают, что
# «Наруто» и «Ураганные хроники» — одна история. Раньше у второго
# спрашивали то, что знает только первый.


@app.get("/api/find")
async def api_find(
    q: str = Query(..., min_length=2, max_length=100),
    lang: str = Query("ru", max_length=2, description="язык, на котором открыт сайт"),
    who: Caller = Depends(need_any),
):
    """Поиск по справочнику: одна карточка на франшизу.

    Язык здесь ни на что не влияет: справочник отдаёт оба названия сразу,
    и какое показать, решает страница. Параметр принимается, чтобы не
    отвечать отказом на честный запрос — остальные ручки поиска язык
    ждут, и слать его во все разом проще, чем помнить исключение.
    """
    if not catalog_limit.allow(f"f:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком частый поиск, подождите минуту")
    cards = await catalog.search_franchises(q)
    if cards is None:
        # Справочник молчит. Не ошибка: сайт умеет искать и без него,
        # прямо у источников видео — хуже, но работает. Развилку делает
        # страница, ей и говорим, что случилось.
        return {"items": [], "catalog": False}
    return {"items": cards, "catalog": True}


@app.get("/api/franchise")
async def api_franchise(
    id: str = Query(..., min_length=1, max_length=120),
    who: Caller = Depends(need_any),
):
    """Все части франшизы по порядку выхода."""
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
    """Части той же истории, что и открытый тайтл.

    Нужна странице просмотра. Поиск отвечает на вопрос «что посмотреть»,
    а этот — на тот, который возникает уже в плеере: какой это сезон, что
    было до него и что после.

    Раньше ответа не было нигде. Тайтл попадал в полку отдельной записью
    с одним названием, и узнать, что у него есть второй сезон и три
    фильма, можно было только вспомнив об этом самому и поискав руками.
    """
    if not catalog_limit.allow(f"rel:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    parts = await catalog.related_parts(title)
    if parts is None:
        # Справочник молчит. Для страницы просмотра это мелочь: блок
        # просто не появится, а плеер работает как работал. Ронять из-за
        # него всю страницу было бы несоразмерно.
        return {"items": [], "catalog": False}
    return {"items": parts, "catalog": True}


# Насколько близко название у источника должно совпасть, чтобы перебор
# остановился и дальше никуда не ходил.
#
# Планка стоит там, где её проходит только точное совпадение. Соблазн
# опустить её ради скорости есть — но именно так и получалось, что на
# «Наруто» перебор останавливался на первом же источнике, у которого
# лежат «Наруто: Ураганные хроники», и открывался второй сезон вместо
# первого. Лишняя секунда ожидания дешевле, чем не тот тайтл.
GOOD_ENOUGH = 0.9

# Сколько раз за один резолв разрешено сходить к источнику за списком серий.
#
# Проверка нужна вот из-за чего. Точное совпадение названия ещё не значит,
# что тайтл открывается. У одного источника «Наруто: Ураганные хроники»
# лежат под ровно тем же названием, что в справочнике, — совпадение
# единица, — а список серий не отдаётся вовсе: разборщик спотыкается на
# сдвоенной серии «57-58» и падает. Резолв радостно возвращал этот
# тайтл, человек нажимал вторую часть «Наруто» и попадал в плеер с
# надписью «серии не загрузились». При этом ровно те же «Ураганные
# хроники» прекрасно отдаются у других источников — их просто никто не
# спрашивал.
#
# Лишним запрос не назвать: страница просмотра просит серии первым же
# делом, и ответ уже лежит в кэше. Потолок в три попытки держит худший
# случай — когда не открывается ни у кого — в разумных пределах.
PROBE_LIMIT = 3

# Какую долю обещанных справочником серий источник должен выложить,
# чтобы считаться подходящим.
#
# Без этой проверки «Ван-Пис» открывался у источника, где выложено семь
# серий из тысячи ста семидесяти четырёх. Формально всё правильно:
# название совпало точно, серии отдались, ошибки нет. А человек получал
# «серия 1 из 7» у сериала, который идёт двадцать шестой год. У других
# источников он лежит целиком — их просто не спрашивали, потому что
# перебор останавливался на первом же работающем.
#
# Планка низкая намеренно. Источники отстают от справочника на серию-две
# постоянно, у «Блича» разница в четырнадцать серий из трёхсот
# шестидесяти шести, и придираться к этому не за что. Отсеять нужно не
# отставание, а огрызок.
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
    """Ищет выбранную часть у источников видео.

    Справочник даёт название, но не даёт ссылки на серии — их знают
    только источники. Здесь название превращается в пару «источник +
    номер тайтла», с которой открывается страница просмотра.

    Порядок перебора: сначала русское название у всех источников, потом
    латинское. Русское первым не случайно — источники русскоязычные, и
    у них название лежит именно в этом виде. Латинское спасает там, где
    перевод разошёлся: «Судьба/Ночь схватки» против «Fate/stay night».
    """
    # В демо-режиме источник один, а браузер подставляет имя по умолчанию
    # для личной версии. Без «or anime.DEMO» запасной поиск (когда
    # справочник не ответил) получал «Неизвестный источник».
    if not source or anime.DEMO:
        source = anime.default_source(lang)
    if source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    # Открывать русскую озвучку тому, кто смотрит сайт по-английски, —
    # это не «запасной вариант», а подмена. Уходим к источнику его языка.
    if anime.source_lang(source) != ("en" if lang == "en" else "ru"):
        source = anime.default_source(lang)
    if not search_limit.allow(f"rs:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")

    order = [source] + [s for s in fallback_for(lang) if s != source]
    names = [t for t in (title.strip(), title_en.strip()) if t]
    # Длину проверяет сам разбор запроса, но два пробела её проходят, а
    # после обрезки не остаётся ничего. Дальше по коду пустой список
    # названий уронил бы max() на пустой последовательности — то есть
    # внутренней ошибкой сервера в ответ на кривой запрос.
    if not names:
        raise HTTPException(status_code=400, detail="Пустое название")

    strong: list[tuple[float, str, dict]] = []     # точное совпадение
    weak: list[tuple[float, str, dict]] = []       # похоже, но не то же
    seen: set[tuple[str, str]] = set()
    probes = [0]                                   # сколько раз ходили за сериями
    answered = False
    # Лучший из тех, кто реально открылся: (совпадение, источник, тайтл, серий).
    live: tuple[float, str, dict, int] | None = None

    def enough(found) -> bool:
        """Можно ли на этом остановиться и никуда больше не ходить."""
        if not found:
            return False
        if probes[0] >= PROBE_LIMIT:
            return True
        if not episodes:                           # справочник не знает, сколько их
            return True
        return found[3] >= episodes * ENOUGH_SHARE

    async def probe(pool: list) -> None:
        """Проверяет отобранных по одному, пока не найдёт достаточно полного."""
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
            # Появилось точное совпадение — проверяем его сразу, и если
            # оно живое и полное, дальше по чужим сайтам не ходим.
            if strong:
                await probe(strong)
                if enough(live):
                    break
        if enough(live):
            break

    # Точных не нашлось, все оказались пустыми или слишком куцыми —
    # смотрим похожие.
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
        # Число из списка серий, а не из карточки поиска: в карточке его
        # часто нет вовсе, а здесь список уже на руках.
        "episodes_total": count or row.get("episodes_total") or 0,
        "match": round(score, 3),
        # Совпадение неточное: нашлось похожее, но не то же самое.
        # Странице это нужно, чтобы предупредить, а не молча открыть
        # не тот тайтл.
        "exact": score >= GOOD_ENOUGH,
    }


# --------------------------------------------------------------------------
# Субтитры
# --------------------------------------------------------------------------
# Кому и зачем.
#
# Озвучка — не единственный способ смотреть. Оригинальную японскую дорожку
# с текстом поверх многие предпочитают любому дубляжу, а источник по
# умолчанию может вообще не давать субтитров — отдаёт одну свою озвучку,
# и всё. То есть человек, открывший любой тайтл, субтитров не видел в
# принципе — хотя у другого источника они лежат почти у всего.
#
# Поэтому правило: субтитры ищутся не у текущего источника, а у всех по
# очереди. Это дорого — на каждый источник уходит поиск, список серий и
# список плееров, — поэтому идёт только по нажатию и только один раз:
# ответы источников кэшируются.

# Сколько источников обходим в поисках субтитров.
SUB_PROBES = 3

# Где искать оригинальную дорожку с текстом, и в каком порядке.
#
# В публичной версии список пуст: внешних источников нет. Если подключите
# свои — перечислите здесь сначала те, у которых чаще встречается вариант
# «Оригинал (+субтитры)».
SUB_ORDER: list[str] = []

# То же самое, но когда сайт открыт по-английски.
SUB_ORDER_EN: list[str] = []


def is_subs(name: str) -> bool:
    """Это субтитры, а не озвучка."""
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
    """Ищет вариант с субтитрами у любого источника.

    Отвечает «где именно»: источник, номер тайтла у него и название
    варианта. Открыть его дальше умеет обычная страница просмотра.

    found=false — это не ошибка. Субтитров к этой серии может не быть ни
    у кого, и сказать об этом прямо честнее, чем показать пустоту.
    """
    if not search_limit.allow(f"sub:{who.kind}:{who.token[:16]}"):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")

    names = [t for t in (title.strip(), title_en.strip()) if t]
    if not names:
        raise HTTPException(status_code=400, detail="Пустое название")

    # Тот источник, у которого субтитров точно нет, второй раз не смотрим.
    wanted = list(anime.SOURCES) if anime.DEMO else (
        SUB_ORDER_EN if lang == "en" else SUB_ORDER)
    order = [s for s in wanted if s != skip]
    tried: list[str] = []

    for source in order[:SUB_PROBES]:
        tried.append(source)
        # 1. Есть ли тайтл у этого источника.
        #
        # У англоязычного источника названия латинские: искать
        # «Магическую битву» там бесполезно, нужно «Jujutsu Kaisen».
        # Поэтому для него порядок названий переворачиваем — сначала
        # латинское.
        ask = list(reversed(names)) if anime.source_lang(source) == "en" else names
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
        # 2. Есть ли у него эта серия.
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

        # 3. Есть ли среди вариантов субтитры.
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
    """У кого из источников этот тайтл есть на самом деле.

    Меню «Откуда берётся видео» перечисляло все источники подряд. Часть
    из них этого тайтла может не знать вовсе: переключаешься — и получаешь
    плашку «этого аниме нет на источнике». Список, где часть строк ведёт
    в тупик, заставляет перебирать их вручную, чтобы выяснить то, что
    сайт мог выяснить сам.

    Проверяются те источники, что участвуют в переборе. Узкие или
    медленные источники остаются в списке непроверенными — держать из-за
    них человека лишние секунды незачем. Страница честно разделит список
    на «здесь есть» и «не проверяли».

    Запросы к источникам кэшируются, поэтому повторное открытие меню
    ничего не стоит.
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
        # Всё, до чего не дошли: список известен странице, но пусть
        # решает сервер — источники живут здесь.
        # Только свои: чужой язык в этом списке — приглашение выбрать
        # то, чего человек не поймёт.
        "unknown": [s for s in anime.sources_for(lang) if s not in checked],
    }


# ==========================================================================
# Объявление администратора
# ==========================================================================
class NewsIn(BaseModel):
    text: str = Field(default="", max_length=store.NEWS_MAX)
    # Английская версия того же объявления. Переводить машиной нечем и
    # незачем: объявление пишет человек, и отправлять его в чужой
    # переводчик — значит отдать наружу текст, который туда не просился.
    text_en: str = Field(default="", max_length=store.NEWS_MAX)


@app.get("/api/news")
async def api_news(who: Caller = Depends(need_any)):
    """Что показать всем вверху страницы. Пусто — значит ничего."""
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
# Вход по коду из приложения
# ==========================================================================
class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)


@app.post("/api/me/2fa/start")
async def twofa_start(request: Request, who: Caller = Depends(need_user)):
    """Готовит секрет и картинку с кодом. Пока не подтверждён — не включён.

    Секрет создаётся заново на каждое нажатие галочки: если человек начал
    настройку, передумал и начал снова, старый секрет не должен остаться
    рабочим.
    """
    guard_csrf(request)
    if not pass_limit.allow(f"t:{who.user_id}"):
        raise HTTPException(status_code=429, detail="Слишком часто. Попробуйте позже.")
    secret = twofa.new_secret()
    # Кладём во временное хранилище в памяти, а не в базу: пока код не
    # подтверждён, это ещё не настройка аккаунта, а черновик.
    pending_2fa[who.user_id] = (secret, time.time())
    uri = twofa.otpauth_uri(secret, who.user["login"])
    # Картинка необязательна: ключ можно ввести в приложение руками.
    # Раньше здесь была ошибка 500 — из-за отсутствующей библиотеки
    # рисования переставала работать вся защита входа.
    qr = await asyncio.to_thread(twofa.qr_data_uri, uri)
    return {"secret": secret, "qr": qr}


@app.post("/api/me/2fa/enable")
async def twofa_enable(body: CodeIn, request: Request,
                       who: Caller = Depends(need_user)):
    """Включает вход по коду — только если код действительно сходится."""
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
    # Запасные коды показываем ровно один раз: в базе лежат только их
    # отпечатки, и восстановить список потом неоткуда.
    return {"ok": True, "backup": codes}


@app.post("/api/me/2fa/disable")
async def twofa_disable(body: PasswordCheckIn, request: Request,
                        who: Caller = Depends(need_user)):
    """Выключает вход по коду. Спрашиваем пароль.

    Иначе любой, кто подсел за незапертый ноутбук, снимает защиту одним
    нажатием — а она ставилась ровно от такого.
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
    """Письма о новых сериях: адрес и согласие."""
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
    """В каком режиме работает сайт.

    Открыта намеренно, без входа: страница должна показать плашку про
    демонстрационный режим ещё до того, как человек куда-то войдёт.
    Ничего, кроме одного слова, отсюда не узнать.
    """
    return {"demo": anime.DEMO}


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ==========================================================================
# Отдача страниц
# ==========================================================================
def _is_hex_color(value: str) -> bool:
    if not isinstance(value, str) or len(value) not in (4, 7):
        return False
    if value[0] != "#":
        return False
    return all(c in "0123456789abcdefABCDEF" for c in value[1:])


def _clean_text(value: str, limit: int) -> str:
    """Убираем управляющие символы и обрезаем длину."""
    value = "".join(ch for ch in (value or "") if ch.isprintable())
    return value.strip()[:limit]


def _clean_lines(value: str, limit: int) -> str:
    """То же, но с сохранением переводов строки.

    Для объявления они нужны: администратор пишет его абзацами, а плашка
    умеет их показывать. Без этого «перевод\\nстроки» слипался в
    «переводстроки» — управляющий символ выбрасывался вместе с разрывом.

    Опасности в переводе строки здесь нет: текст показывается через
    textContent, то есть как текст, а не как разметка. Убираем только
    остальные управляющие символы и лишние пустые строки подряд.
    """
    lines = [
        "".join(ch for ch in line if ch.isprintable()).rstrip()
        for line in (value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    out: list[str] = []
    for line in lines:
        # больше одной пустой строки подряд не оставляем
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()[:limit]


PAGES = {"/": "index.html", "/watch": "watch.html", "/stats": "stats.html",
         "/pravila": "pravila.html"}


@app.get("/{page}")
async def page(page: str):
    """Отдаём только заранее перечисленные страницы.

    Так исключён обход каталога: имя файла не собирается из того,
    что прислал посетитель.
    """
    name = PAGES.get("/" + page)
    if name is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return FileResponse(os.path.join(WEB_DIR, name))


@app.get("/")
async def root():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


# Статика: только эта папка, StaticFiles сам не выпускает за её пределы
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
