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
import json
import logging
import mimetypes
import os
import random
import re
import time
import urllib.parse
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request as StarletteRequest


def _log_level() -> tuple[int, str]:
    """Уровень журнала из LOG_LEVEL — любыми буквами («info», «Debug»).

    Раньше строка уходила в logging как есть, и «info» маленькими
    буквами роняла запуск всего сайта (ValueError: Unknown level).
    Неизвестное слово — не повод не запускаться: берём INFO, а о
    неизвестном слове говорим в журнале (вторым значением)."""
    raw = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = logging.getLevelName(raw)
    if isinstance(level, int):
        return level, ""
    return logging.INFO, raw


# Журнал настраивается ДО импорта своих модулей: они могут написать
# в журнал уже при импорте (например, mail.py о неверном SMTP_PORT).
_LEVEL, _BAD_LEVEL = _log_level()
logging.basicConfig(level=_LEVEL)
log = logging.getLogger("anime")
if _BAD_LEVEL:
    log.warning("LOG_LEVEL=%r — такого уровня нет, пишем как INFO. Бывают: "
                "DEBUG, INFO, WARNING, ERROR, CRITICAL.", _BAD_LEVEL[:20])

from . import (anime, catalog, errors, helper, hls, mail,  # noqa: E402
               netguard, security, store, subtitles, twofa)

# Сторож исходящих запросов — до первого обращения к чужим сайтам: все
# клиенты httpx в процессе (наши и внутри библиотек источников) ходят
# только в интернет, а не в домашнюю сеть. Подробно — api/netguard.py.
netguard.install()

# Когда тайтл выпал из кэша источника, его ищут заново по названию. Под
# названием из справочника источник его может не знать — тогда пусть
# спросит синонимы у справочника (см. anime.find_anime).
anime.ALT_LOOKUP[0] = catalog.alt_names
# Источник хентая (если его подключат) берёт только то, что показал наш
# справочник (rx без признаков несовершеннолетних): проверка — через
# anime.RX_CLEARED.
anime.RX_CLEARED[0] = catalog.rx_cleared

WEB_DIR =os.getenv("WEB_DIR", os.path.join(os.path.dirname(__file__), "..", "web"))
WEB_DIR = os.path.abspath(WEB_DIR)

# Куки помечаются Secure, если сайт открыт по https. На локальной проверке
# по http это выключается переменной, иначе браузер куку не сохранит.
#
# Выключает только явное «нет»: 0, false, no или off (любыми буквами).
# Раньше включала только строка «1», и любое другое слово — «true», «yes»,
# «on» — молча ВЫКЛЮЧАЛО Secure у кук и HSTS на рабочем сайте. Ошибка в
# сторону «защита выключена» хуже, чем в сторону «включена»: на https
# включённая защита ничего не ломает, а выключенную никто не заметит.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no", "off")
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
    # Секретная строка сервера — до всего остального. На ней держатся
    # отпечатки сессий, метки форм, подписи адресов видео и шифр секретов
    # входа по коду. Строка-образец из .env.example известна всем, кто
    # видел репозиторий: с ней любой подписал бы прокладке видео свой
    # адрес. На рабочем сайте (COOKIE_SECURE=1) с такой строкой сервер не
    # запускается вовсе — лучше честная остановка с понятной причиной в
    # журнале, чем сайт, который только выглядит защищённым.
    problem = security.pepper_problem()
    if problem:
        if COOKIE_SECURE:
            log.critical("SESSION_PEPPER: %s. Сайт не запущен. Сгенерируйте строку: "
                         "openssl rand -hex 32 — и впишите её в .env.", problem)
            raise RuntimeError("SESSION_PEPPER: " + problem)
        log.warning("SESSION_PEPPER: %s. Для проверки по http это допустимо, для "
                    "рабочего сайта — нет: openssl rand -hex 32", problem)
    store.init()
    dropped = store.purge_old_sessions()
    if dropped:
        log.info("удалено просроченных сессий: %d", dropped)
    if store.count_admins() == 0:
        log.warning(
            "В базе нет ни одного администратора. Создайте его командой:\n"
            "  python -m api.admin add ВАШ_ЛОГИН --admin"
        )
    # Личная версия без библиотек источников запускается и выглядит
    # живой, а видео не отдаёт ни по одному тайтлу. Раньше это всплывало
    # только как «Ни один источник сейчас не отвечает» — ровно то, что
    # пишут, когда чужие сайты лежат, — и искать причину шли не туда.
    missing = anime.missing_libraries()
    if missing:
        log.error(
            "MODE=private, но не установлены библиотеки источников: %s. "
            "Видео работать не будет. Поставьте их:\n"
            "  pip install -r api/requirements-private.txt\n"
            "или запустите с MODE=demo.",
            ", ".join(anime.LIBRARIES[m] for m in missing),
        )
    # Личный режим без единого источника: сайт работает (каталог, полка,
    # админка), а ручки видео отвечают 501. Говорим об этом сразу, чтобы
    # не искать «почему не играет» там, где ничего не сломано.
    if anime.no_sources():
        log.warning("MODE=private, но источники видео не подключены (api/anime.py, "
                    "SOURCES): видео отвечает 501. Для витрины запустите с MODE=demo.")
    # Помощник в пузыре «?». Одна строка в журнал — и всё: без ключа он
    # просто выключен, это не ошибка.
    if anime.DEMO:
        log.info("помощник выключен: в демонстрационном режиме он недоступен")
    elif helper.enabled():
        log.info("помощник включён: %s, модель %s", helper.BASE_URL, helper.MODEL)
    else:
        log.info("помощник выключен: в .env не задан HELPER_API_KEY")
    task = asyncio.create_task(housekeeping())
    application.state.housekeeper = task
    # Проверка новых серий — своя задача, а не часть часовой уборки:
    # ей нужен шаг в четверть часа, а уборке чаще раза в час незачем.
    watcher = asyncio.create_task(episode_watch()) if REFRESH_EVERY > 0 else None
    application.state.episode_watcher = watcher
    try:
        yield
    finally:
        task.cancel()
        if watcher is not None:
            watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if watcher is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


app = FastAPI(
    title="anime",
    version="3.1",
    docs_url="/docs" if SHOW_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if SHOW_DOCS else None,
    lifespan=lifespan,
)


# --------------------------------------------------------------------------
# Потолок на размер тела запроса
# --------------------------------------------------------------------------
# Самое большое, что сайт принимает честно, — баннер (700 КБ) и история
# разговора с помощником (до 250 КБ). Всё больше мегабайта — не наше.
#
# Раньше потолок держал только nginx (client_max_body_size). Но защита
# на одном рубеже — это не защита: зайди запрос мимо nginx (с той же
# машины, при правке конфига), и приложение читало бы в память любое
# тело целиком — FastAPI собирает JSON полностью, прежде чем проверить
# хоть одно поле. Теперь тело считается по кускам прямо при чтении и
# обрывается ответом 413, как только вышло за предел, — и заявленная
# длина (Content-Length) проверяется ещё до первого байта.
BODY_MAX = 1024 * 1024
RANGE_MAX_LEN = 200
RANGE_MAX_PARTS = 8


class BodyLimit:
    """Слой ASGI: считает байты тела запроса и обрывает лишнее."""

    def __init__(self, application: Any, limit: int = BODY_MAX) -> None:
        self.app = application
        self.limit = limit

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or []
        # Заголовок Range с сотнями кусков. Отдача статики (Starlette
        # 0.41, StaticFiles) разбирает такой заголовок за время, растущее
        # как квадрат числа кусков (PYSEC-2026-1942, исправлено в Starlette
        # 0.49), — и для этого даже не нужно входить: /static открыт всем.
        # Плееру и браузеру хватает одного куска; странный Range просто
        # выбрасываем, и файл уходит целиком — это всегда правильный ответ.
        if any(k == b"range" and (len(v) > RANGE_MAX_LEN or v.count(b",") > RANGE_MAX_PARTS)
               for k, v in headers):
            scope = dict(scope)
            scope["headers"] = [(k, v) for k, v in headers if k != b"range"]
            headers = scope["headers"]
        declared = dict(headers).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.limit:
            asked = StarletteRequest(scope)
            response = JSONResponse(
                errors.payload("Слишком большой запрос", 413,
                               errors.ui_lang(asked.headers, asked.cookies)),
                status_code=413)
            await response(scope, receive, send)
            return
        seen = [0]

        async def limited() -> dict:
            message = await receive()
            if message.get("type") == "http.request":
                seen[0] += len(message.get("body") or b"")
                if seen[0] > self.limit:
                    # Исключение уходит туда, где тело читают (разбор JSON
                    # или request.stream() в ручке), и FastAPI превращает
                    # его в обычный ответ 413.
                    raise HTTPException(status_code=413, detail="Слишком большой запрос")
            return message

        await self.app(scope, limited, send)


# Объявлен первым: среди слоёв приложения он самый внутренний, и ответ
# 413 проходит через secure_headers ниже — с защитными заголовками.
app.add_middleware(BodyLimit)


@app.exception_handler(RequestValidationError)
async def validation_failed(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Ответ 422 без эха присланного.

    Стандартный ответ FastAPI на кривое тело кладёт в ошибку и само
    присланное значение (поле input) — например, слишком длинный пароль
    целиком, — плюс служебные ссылки на документацию pydantic. Странице
    это не нужно (она показывает общую фразу, см. app.js), а пароль,
    вернувшийся в ответе, может осесть в журнале прокси или в истории
    отладчика браузера. Оставляем только где ошибка и какая."""
    problems = [{"loc": [str(x)[:40] for x in (e.get("loc") or ())][:6],
                 "msg": str(e.get("msg") or "")[:200],
                 "type": str(e.get("type") or "")[:60]}
                for e in exc.errors()[:20]]
    return JSONResponse({"detail": problems, "detail_en": errors.by_status(422)},
                        status_code=422)


def error_lang(request: Request) -> str:
    """Язык сайта у того, кто прислал запрос (см. api/errors.py)."""
    return errors.ui_lang(request.headers, request.cookies)


@app.exception_handler(StarletteHTTPException)
async def http_failed(request: Request, exc: StarletteHTTPException) -> Response:
    """Ответ с ошибкой — как у FastAPI, плюс английский текст.

    detail — на языке сайта (заголовок X-UI-Lang или кука lang, иначе
    по-русски), detail_en — по-английски всегда. Перевод и правила — в
    api/errors.py. Заголовки ошибки (Retry-After, X-Need-Code, Allow)
    уходят как раньше."""
    headers = getattr(exc, "headers", None)
    if exc.status_code in (204, 304) or exc.status_code < 200:
        # Ответы, у которых тела быть не может (так делает и FastAPI).
        return Response(status_code=exc.status_code, headers=headers)
    return JSONResponse(errors.payload(exc.detail, exc.status_code, error_lang(request)),
                        status_code=exc.status_code, headers=headers)

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
# Неверные коды из приложения при ВЕРНОМ пароле — на аккаунт, откуда бы
# ни шли (см. auth_login).
CODE_FAILS_MAX = 5
CODE_WINDOW = 15 * 60
code_fails = security.RateLimiter(limit=10_000, period=CODE_WINDOW)
# Разбор картинки — тоже дорогая операция, и по памяти дороже всего
# остального вместе взятого.
avatar_limit = security.RateLimiter(limit=10, period=300)
# Помощник ходит к чужой модели по бесплатному ключу, у которого свой
# дневной предел. Каждому — 30 вопросов в час (администратору тоже:
# отдельного лимита ему незачем), и всем вместе — 500 в сутки, чтобы
# один увлёкшийся не оставил без помощника остальных и не сжёг ключ.
helper_limit = security.RateLimiter(limit=30, period=3600)
helper_day_limit = security.RateLimiter(limit=500, period=24 * 3600)


# --------------------------------------------------------------------------
# Тяжёлая арифметика — в отдельный поток
# --------------------------------------------------------------------------
# PBKDF2 намеренно медленный: так его и задумывали, чтобы перебор был
# невыгоден. Но вызванный прямо здесь, он останавливает ВЕСЬ сервер на
# время подсчёта — asyncio выполняет обработчики в одном потоке. Один вход
# замораживал страницу у всех остальных, а десяток неверных паролей подряд
# укладывал сайт целиком, не считаясь ни с какими ограничениями частоты.
# Теперь подсчёт уходит в отдельный поток, а цикл событий остаётся свободным.
#
# Но и в отдельных потоках подсчёт не бесплатен: каждый занимает ядро на
# полсекунды. Перебор с сотни адресов (у каждого свои 25 попыток до
# блокировки) запускал бы сотни подсчётов разом — все ядра заняты, и
# сайт стоит у всех, хотя ни одна защита формально не нарушена. Поэтому
# разом считается не больше HASH_PARALLEL паролей, остальные ждут своей
# очереди, а когда очередь длиннее HASH_QUEUE_MAX — честное «сервер
# занят» (503) вместо бесконечного ожидания. Обычному входу очередь не
# мешает: людей на сайте единицы.
HASH_PARALLEL = 2
HASH_QUEUE_MAX = 32
_hash_gate: dict[str, Any] = {"loop": None, "sem": None, "busy": 0}


async def _hashing(fn: Any, *args: Any) -> Any:
    loop = asyncio.get_running_loop()
    if _hash_gate["loop"] is not loop:
        # Замок привязан к циклу событий; у сервера он один, в проверках
        # у каждого клиента свой — тогда заводим заново.
        _hash_gate.update(loop=loop, sem=asyncio.Semaphore(HASH_PARALLEL), busy=0)
    if _hash_gate["busy"] >= HASH_QUEUE_MAX:
        raise HTTPException(status_code=503,
                            detail="Сервер сейчас занят проверкой паролей. Попробуйте через минуту.")
    _hash_gate["busy"] += 1
    try:
        async with _hash_gate["sem"]:
            return await asyncio.to_thread(fn, *args)
    finally:
        _hash_gate["busy"] -= 1


async def verify_password(password: str, stored: str) -> bool:
    return await _hashing(security.verify_password, password, stored)


async def hash_password(password: str) -> str:
    return await _hashing(security.hash_password, password)


async def waste_time_like_a_real_check() -> None:
    await _hashing(security.waste_time_like_a_real_check)


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
    found = store.session_lookup(token)
    if found is not None:
        user, created = found
        # Запоминаем для продления куки — см. slide_session_cookie.
        request.state.session_slide = (token, created)
        return Caller("admin" if user["role"] == "admin" else "user",
                      user=user, token=token)
    return None


def within(limiter: security.RateLimiter, tag: str, who: Caller, cost: int = 1) -> bool:
    """Проверка лимита частоты для того, кто пришёл.

    Раньше ключом служило начало токена сессии. Но сессий у одного
    аккаунта бывает до двадцати, и каждая получала свой счётчик: двадцать
    входов — двадцатикратный лимит, в том числе на обращения к чужим
    сайтам, за которые банят наш адрес. Теперь человек считается по номеру
    аккаунта, сколько бы у него ни было входов.

    У гостя аккаунта нет, поэтому два счётчика сразу: свой у пропуска и
    общий у адреса, с которого пропуск выдан. Пропусков с одного адреса
    бывает до восьми, и без общего счётчика они давали бы восьмикратный
    лимит. Адрес берём тот, что запомнили при выдаче пропуска, — его не
    подменить заголовком в каждом запросе.
    """
    if who.is_guest:
        ip = (who.guest or {}).get("ip") or "?"
        gid = security.token_fingerprint(who.token)[:16]
        return (limiter.allow(f"{tag}:gip:{ip}", cost)
                and limiter.allow(f"{tag}:g:{gid}", cost))
    return limiter.allow(f"{tag}:u:{who.user_id}", cost)


def uncached_lookups(sources: list[str], names: tuple[str, ...] | list[str]) -> int:
    """Сколько поисков у источников пройдёт мимо кэша. Не меньше одного.

    Это цена вызова для лимита search_limit у ручек, которые за один раз
    спрашивают много источников: /api/resolve и /api/where перебирают до
    четырёх источников по двум названиям, /api/subs — до трёх. Раньше
    каждая списывала одно обращение, а ходила наружу до одиннадцати раз:
    лимит «двадцать в минуту» на деле пропускал за двести.

    Считаем только то, чего нет в кэше: повторное открытие того же тайтла
    наружу не ходит и стоить дорого не должно. Ключ — тот же, что
    в try_source. Если там его однажды поменяют, здесь всё будет считаться
    промахом: лимит станет строже, но не дырявее.
    """
    words = [n.strip() for n in names if n and n.strip()]
    misses = sum(1 for s in dict.fromkeys(sources) for q in words
                 if anime.cache_get(f"search:{s}:{q.lower()}") is None)
    return max(1, misses)


async def need_any(request: Request) -> Caller:
    """Пускаем и пользователя, и гостя. Для чтения каталога."""
    who = await whoami(request)
    if who is None:
        raise HTTPException(status_code=401, detail="Нужно войти")
    if not within(api_limit, "any", who):
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
    if not within(api_limit, "any", who):
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


def origin_ok(request: Request) -> bool:
    """Запрос, меняющий данные, пришёл с нашего же сайта?

    Браузер к каждому POST дописывает заголовок Origin — адрес страницы,
    с которой запрос отправлен, — и подделать его из скрипта нельзя.
    Сравниваем его с Host (nginx передаёт его как есть). Чужой адрес или
    «null» (страница в песочнице, переадресация с чужого сайта) — отказ
    ещё до разбора тела. Это второй рубеж поверх метки формы: у входа и
    гостевого пропуска метки нет, и их держала только проверка «тело —
    JSON»; теперь держит и этот. Запрос без Origin (curl, старые
    браузеры, свои скрипты) пропускается — его защищает метка формы.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    try:
        parts = urllib.parse.urlsplit(origin.strip())
        o_host, o_port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return False
    if not o_host:
        return False                      # «null» или мусор
    host = request.headers.get("host", "").strip().lower()
    h_host, h_port = host, None
    if host.startswith("["):
        h_host, _, rest = host[1:].partition("]")
        h_port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else None
    elif host.count(":") == 1:
        h_host, _, p = host.partition(":")
        h_port = int(p) if p.isdigit() else None
    if o_host != h_host:
        return False
    return o_port is None or h_port is None or o_port == h_port


# ==========================================================================
# Общие заголовки безопасности
# ==========================================================================
# Чужие адреса — только https. Раньше здесь стояло ещё и «http:», но на
# https-сайте браузер такие запросы всё равно не пускает (смешанное
# содержимое: картинки и видео он сам переводит на https, а запросы
# скриптов просто блокирует), так что разрешение только вводило в
# заблуждение. Всё, что требует особого обращения (Referer у хостинга,
# субтитры), и так идёт через наш адрес — /api/hls и /api/subtitle.
CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "          # обложки приходят с чужих доменов
    "media-src 'self' https: blob:; "         # видео тоже, плюс blob для потоков
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "                      # шрифт лежит у нас
    "script-src 'self'; "                     # только наши файлы, никаких вставок
    "worker-src 'self' blob:; "               # он же работает в отдельном потоке
    "connect-src 'self' https:; "             # куски видео качаются с чужих доменов
    "frame-ancestors 'none'; "                # нас нельзя вставить в чужой фрейм
    "base-uri 'none'; form-action 'self'; object-src 'none'"
)


# ==========================================================================
# Админка для чужих не существует; кука сессии продлевается
# ==========================================================================
ADMIN_PREFIX = "/api/admin"

# Как часто переотправляем куку сессии. Чаще незачем: срок у неё две
# недели, а Set-Cookie на каждом запросе — это лишний заголовок в каждом
# ответе и лишняя работа браузеру.
COOKIE_SLIDE_EVERY = 24 * 3600
_cookie_sent: dict[str, float] = {}


def note_cookie_sent(token: str) -> None:
    """Запоминает, что куку этой сессии только что выдали заново."""
    now = time.time()
    if len(_cookie_sent) > 20_000:
        # Сессий у нас единицы, но потолок нужен на всякий случай: записи
        # старше суток всё равно ничего не решают.
        for fp in [k for k, at in _cookie_sent.items() if now - at >= COOKIE_SLIDE_EVERY]:
            del _cookie_sent[fp]
    _cookie_sent[security.token_fingerprint(token)] = now


def slide_session_cookie(request: Request, response: Response) -> None:
    """Продлевает куку сессии в браузере, пока человек пользуется сайтом.

    Раньше кука получала срок жизни один раз — при входе, две недели. А
    сессия в базе скользящая: каждый заход отодвигает её конец. Через две
    недели после входа браузер молча выбрасывал куку посреди работы, хотя
    сервер считал сессию живой, — и человека выкидывало на страницу входа
    без всякой причины.

    Теперь кука переотправляется с новым сроком, но не чаще раза в сутки
    и никогда не дальше предельного срока жизни сессии (SESSION_MAX_LIFE):
    иначе браузер хранил бы куку, которую сервер уже не примет.
    """
    slide = getattr(request.state, "session_slide", None)
    if not slide:
        return
    token, created = slide
    # Ручка сама поставила или стёрла куку (вход, выход, смена пароля) —
    # её решение главнее, не перебиваем.
    for raw in response.headers.getlist("set-cookie"):
        if raw.startswith(SESSION_COOKIE + "="):
            return
    now = time.time()
    if now - _cookie_sent.get(security.token_fingerprint(token), 0) < COOKIE_SLIDE_EVERY:
        return
    max_age = int(min(security.SESSION_IDLE, created + security.SESSION_MAX_LIFE - now))
    if max_age <= 0:
        return
    set_session_cookies(response, token, max_age)
    note_cookie_sent(token)


@app.middleware("http")
async def session_edges(request: Request, call_next):
    """Две вещи, которые нужно сделать до и после любой ручки.

    До: всё под /api/admin для не-администратора отвечает 404 — сразу,
    до разбора адреса и тела. Раньше 404 отдавала проверка need_admin, но
    FastAPI успевает ответить раньше неё: 405 на чужой метод и 422 на
    кривое тело. Посторонний по этим кодам видел, что админка есть и где
    именно лежат её ручки. Теперь для него за этим адресом нет ничего,
    чем бы он туда ни стучал. Ответ тот же, что и на любой
    несуществующий адрес под /api. Администратор проходит дальше как
    раньше: метку формы и всё прочее проверяют сами ручки.

    После: продлеваем куку сессии, если пора (slide_session_cookie).
    """
    path = request.url.path
    if path == ADMIN_PREFIX or path.startswith(ADMIN_PREFIX + "/"):
        who = await whoami(request)
        if who is None or not who.is_admin:
            return JSONResponse(errors.payload("Not Found", 404, error_lang(request)),
                                status_code=404)
    if path.startswith("/api/") and not csrf_exempt(request) and not origin_ok(request):
        return JSONResponse(errors.payload("Запрос отклонён", 403, error_lang(request)),
                            status_code=403)
    response = await call_next(request)
    slide_session_cookie(request, response)
    return response


# Объявлен ПОСЛЕ session_edges намеренно: последний объявленный слой —
# внешний, и защитные заголовки ниже достаются в том числе ответу 404
# от session_edges.
@app.middleware("http")
async def secure_headers(request: Request, call_next):
    try:
        response = await call_next(request)
    except HTTPException as exc:
        # Обычно сюда не доходит: ошибки маршрутов FastAPI превращает
        # в ответ раньше. Но если такое всё же случится, отдаём нормальный
        # ответ, а не пробрасываем исключение дальше — иначе на нём не
        # окажется ни одного защитного заголовка, выставленного ниже.
        response = JSONResponse(errors.payload(exc.detail, exc.status_code, error_lang(request)),
                                status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
    except Exception as exc:                       # noqa: BLE001
        # Ни одна внутренняя ошибка не должна улететь в браузер трейсбеком
        log.exception("необработанная ошибка: %s", exc)
        response = JSONResponse(errors.payload("Внутренняя ошибка", 500, error_lang(request)),
                                status_code=500)

    # Ручка могла поставить свою политику, строже общей (прокладка видео,
    # файлы субтитров — см. PROXY_CSP): её не перебиваем.
    if "content-security-policy" not in response.headers:
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
    if path.startswith("/api/") or path.endswith(".html") or path in PAGES:
        # Все страницы из PAGES, включая /pravila. Раньше список был
        # написан руками («/», «/watch», «/stats»), и правила в него не
        # попали: без Cache-Control браузер сам решал, сколько держать
        # страницу, и держал старую — с текстом без листа-подложки.
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/static/") and _fresh_version(path, request.query_params.get("v")):
        # Адрес с ?v=<отпечаток файла>: у нового файла будет новый
        # адрес, поэтому этот можно держать в кэше сколько угодно.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
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

# Новые серии. Раз в REFRESH_EVERY секунд сервер сам спрашивает источники
# о том, что у людей в списке, и записывает ответ в базу. Колокольчик
# потом читает готовое — мгновенно и без похода к чужим сайтам.
#
# Четверть часа — компромисс: серия видна на сайте минут через 15–20
# после выхода, а источник получает от нас один запрос на тайтл в
# четверть часа, а не по запросу на каждое открытие страницы.
# UPDATES_EVERY_MIN=0 выключает фоновую проверку совсем.
try:
    REFRESH_EVERY = max(0, int(os.getenv("UPDATES_EVERY_MIN", "15"))) * 60
except ValueError:
    REFRESH_EVERY = 15 * 60
# Первый заход — не сразу после запуска: при перезапуске сервер и так
# занят, а люди как раз заходят обратно.
REFRESH_FIRST_DELAY = 60
# Сколько разных тайтлов спрашиваем за один заход. Один и тот же тайтл у
# пяти человек — один запрос, а не пять. Не успели всех — остальные
# первыми в следующий раз (очередь по времени последней проверки).
REFRESH_MAX_TITLES = 80
# Сколько ждём один тайтл целиком: поиск, карточка и список серий.
CHECK_BUDGET = 30.0
# Насколько свежим должно быть число из базы, чтобы письмо поверило ему
# и не спрашивало источник само: два шага фоновой проверки.
MAIL_TRUST_CHECK = 30 * 60


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
        try:
            rows = store.watching_for_mail(user["id"])[:MAIL_MAX_TITLES]
        except Exception as exc:                   # noqa: BLE001
            log.warning("письма: не прочитался список пользователя %s: %s: %s",
                        user["id"], type(exc).__name__, exc)
            continue
        for row in rows:
            # Каждая строка — отдельно. Раньше любая ошибка в одной из
            # них (например, перевод строки в названии ронял сборку
            # письма) обрывала весь заход: все, кто шёл в очереди после,
            # писем не получали, а отметка «уже писали» не ставилась —
            # и так каждый час, пока строку не поправят руками.
            try:
                sent += await _mail_one(user, row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:               # noqa: BLE001
                log.warning("письма: тайтл %r пропущен: %s: %s",
                            str(row["key"])[:80], type(exc).__name__, exc)
    return sent


def watch_link(row: Any, episode: int = 0) -> str:
    """Ссылка «Смотреть» из письма.

    Раньше в ней был только key, и тот без кодирования. Страница
    просмотра без источника берёт основной для языка — RU-A, — и
    тайтл с RU-B или RU-C открывался там с ошибкой «тайтл выпал
    из памяти» (409): у RU-A такого ключа нет. А ключ с & или # в
    нём просто обрезался браузером. Теперь в ссылке всё, что нужно
    странице: ключ, источник, название и серия, — и всё закодировано.
    """
    if not mail.SITE_URL:
        return ""
    params = {"key": row["key"], "source": row["source"] or "",
              "title": mail.one_line(row["title"])}
    if episode:
        params["ep"] = int(episode)
    return mail.SITE_URL + "/watch?" + urllib.parse.urlencode(params)


async def _mail_one(user: Any, row: Any) -> int:
    """Проверяет один тайтл и, если вышла новая серия, пишет. 1 — письмо ушло."""
    if row["source"] not in anime.SOURCES:
        return 0
    # Фоновая проверка (refresh_updates) уже спрашивала источник не больше
    # получаса назад — берём её ответ и второй раз к чужому сайту не идём.
    # Число старое или его нет (проверка выключена, тайтл только что
    # добавлен) — спрашиваем сами, как раньше.
    last = 0
    checked = int(row.get("checked_at") or 0) if hasattr(row, "get") else 0
    if checked and store.now() - checked <= MAIL_TRUST_CHECK:
        last = int(row.get("latest_ep") or 0)
    if not last:
        last = await last_episode_number(row["source"], row["key"], row["title"])
    if not last:
        return 0
    known = int(row["mailed_ep"] or 0)
    # Ноль означает «мы ещё не считали», а не «серий не было».
    # Без этой развилки первое же включение писем присылало бы
    # письмо про каждый тайтл в списке разом.
    if known <= 0:
        store.set_mailed_ep(user["id"], row["key"], last)
        return 0
    if last <= known:
        if last != known:
            store.set_mailed_ep(user["id"], row["key"], last)
        return 0

    about = ""
    try:
        found = await catalog.about(row["title"])
        about = (found or {}).get("about", "")
    except Exception:                              # noqa: BLE001
        pass

    ok = await asyncio.to_thread(
        mail.send_episode, user["email"],
        display_name=user["display_name"] or user["login"],
        title=mail.one_line(row["title"]), episode=last,
        released=time.strftime("%d.%m.%Y"),
        about=about, poster=row["poster"] or "",
        watch_url=watch_link(row, last),
    )
    # Отмечаем только при успехе: если почта отвалилась, письмо
    # должно уйти в следующий заход, а не пропасть насовсем.
    if ok:
        store.set_mailed_ep(user["id"], row["key"], last)
        return 1
    return 0


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
# Новые серии: фоновая проверка
# ==========================================================================
# Один тайтл — один поход к источнику, даже если о нём спрашивают сразу
# двое: фоновая проверка и колокольчик открытой страницы. Второй ждёт
# ответа первого, а не идёт к чужому сайту ещё раз.
# (Раньше этот словарь назывался _inflight — так же, как словарь ниже для
# shared(). Второе определение молча заменяло первое, и оба вида задач
# жили в одном словаре; работало, но только потому, что ключи разного вида.)
_episode_inflight: dict[tuple[str, str], asyncio.Task] = {}
# Заход фоновой проверки уже идёт — второй не начинаем. Обычно так не
# бывает, но медленные источники могут растянуть заход дольше шага.
_refresh_busy = False


async def _ask_last_episode(source: str, key: str, title: str) -> int:
    """Номер последней серии по свежему списку. 0 — не узнали."""
    try:
        with anime.budget(CHECK_BUDGET):
            episodes = await anime.fresh_episodes(source, key, title, anime.FRESH_TTL)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                       # noqa: BLE001
        # Источник молчит — это не новость о серии, а просто тишина.
        log.info("новые серии: %s/%s не ответил: %s", source, str(key)[:60],
                 type(exc).__name__)
        return 0
    last = 0
    for ep in episodes or []:
        try:
            last = max(last, int(ep.ordinal))
        except (TypeError, ValueError, AttributeError):
            continue
    return last


async def fresh_last_episode(source: str, key: str, title: str) -> int:
    """То же, но одновременные вопросы об одном тайтле склеиваются в один."""
    loop = asyncio.get_running_loop()
    name = (source, str(key))
    task = _episode_inflight.get(name)
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(_ask_last_episode(source, key, title))
        _episode_inflight[name] = task

        def forget(t: asyncio.Task, name: tuple[str, str] = name) -> None:
            if _episode_inflight.get(name) is t:
                _episode_inflight.pop(name, None)
        task.add_done_callback(forget)
    # shield: если ушёл тот, кто спросил первым (закрыл страницу), ответ
    # всё равно нужен второму — общий поход не отменяем.
    return await asyncio.shield(task)


async def check_titles(rows: list[dict], limit: int) -> dict:
    """Спрашивает источники о записях rows и раскладывает ответы по базе.

    Записи одного тайтла (тот же источник и ключ) у разных людей
    спрашиваются один раз. Больше UPDATES_PARALLEL походов разом не
    бывает, больше limit разных тайтлов за раз — тоже.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if row.get("source") not in anime.SOURCES or not row.get("key"):
            continue
        groups.setdefault((row["source"], row["key"]), []).append(row)
    picked = list(groups.items())[:max(0, limit)]
    gate = asyncio.Semaphore(UPDATES_PARALLEL)
    result = {"checked": len(picked), "new": 0, "reopened": 0, "silent": 0}

    async def one(name: tuple[str, str], group: list[dict]) -> None:
        async with gate:
            last = await fresh_last_episode(name[0], name[1], group[0].get("title") or "")
        if not last:
            result["silent"] += 1
        for row in group:
            try:
                what = store.apply_episode_count(row["user_id"], row["key"], last)
            except Exception as exc:               # noqa: BLE001
                log.warning("новые серии: не записалось %r: %s: %s",
                            str(row["key"])[:60], type(exc).__name__, exc)
                continue
            if what:
                result["new"] += 1
            if what == "reopened":
                result["reopened"] += 1

    await asyncio.gather(*(one(n, g) for n, g in picked))
    return result


async def refresh_updates(limit: int = REFRESH_MAX_TITLES) -> dict:
    """Один заход фоновой проверки: все люди, все тайтлы, которые пора проверить."""
    global _refresh_busy
    if _refresh_busy:
        return {"skipped": True}
    _refresh_busy = True
    try:
        return await check_titles(store.rows_to_check(), limit)
    finally:
        _refresh_busy = False


async def episode_watch() -> None:
    """Фоновая проверка новых серий, раз в REFRESH_EVERY секунд.

    Ни одна ошибка здесь не должна ронять сервер или останавливать
    проверку насовсем: записали в журнал — и ждём следующего шага.
    """
    await asyncio.sleep(REFRESH_FIRST_DELAY)
    while True:
        try:
            got = await refresh_updates()
            if got.get("new"):
                log.info("новые серии: проверено %s, новинок %s, вернулось в просмотр %s",
                         got.get("checked"), got.get("new"), got.get("reopened"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:                   # noqa: BLE001
            log.warning("проверка новых серий споткнулась: %s: %s",
                        type(exc).__name__, exc)
        await asyncio.sleep(REFRESH_EVERY or 900)


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
    # Язык сайта и язык аниме — две разные настройки (см. anime.ANIME_LANGS).
    #   ui_lang    — меню, кнопки, описания из справочника;
    #   anime_lang — озвучка и субтитры: по нему выбираются источники.
    # lang — старое имя ui_lang. Принимается от страниц, открытых до
    # обновления, и хранится зеркалом ui_lang.
    lang: str | None = Field(default=None, max_length=5)
    ui_lang: str | None = Field(default=None, max_length=5)
    anime_lang: str | None = Field(default=None, max_length=5)
    # Показывать аниме 18+ (рейтинг r_plus — этти). По умолчанию выключено.
    adult: bool | None = None
    # Хентай (рейтинг rx). По умолчанию выключено и работает только вместе
    # с adult: выключил 18+ — выключился и хентай (см. api_settings), а
    # гостю не показывается никогда (см. hentai_ok).
    hentai: bool | None = None
    # Точки на фоне (растр в стиле манги). По умолчанию включены; кому
    # мешают — выключает в «Вид».
    dots: bool | None = None
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


# Откуда принимаем обложку, которую прислала страница (/api/library/progress
# и /api/library/watched). Страница берёт её из адреса /watch?poster=…, а
# адрес может прислать кто угодно: ссылка с настоящим ключом тайтла и
# чужой «картинкой» клала в список пиксель, и чужой сайт узнавал адрес и
# время каждого открытия главной. Теперь — только https и только сайты,
# откуда обложки берёт сам сервер: справочники (Shikimori, AniList,
# MyAnimeList), сайты источников видео (их адреса — в anime.SOURCES) и
# известные хранилища картинок источников (если картинки источника лежат
# не на его основном адресе — допишите сайт сюда). Остальное — пустая
# строка: страница покажет заглушку и сама найдёт обложку через /api/poster.
POSTER_SITES = (
    "shikimori.one", "shikimori.me",     # справочник Shikimori (catalog.SHIKI_HOST)
    "anilist.co",                        # AniList: картинки на s4.anilist.co
    "myanimelist.net",                   # Jikan/MyAnimeList: cdn.myanimelist.net
)


def _site(host: str) -> str:
    """Последние две части имени: «s4.anilist.co» → «anilist.co»."""
    return ".".join(host.lower().rstrip(".").split(".")[-2:])


POSTER_HOSTS = frozenset(POSTER_SITES) | frozenset(
    _site(urllib.parse.urlsplit(str(src.get("base") or "")).hostname or "")
    for table in (anime.SOURCES, anime.PRIVATE_SOURCES) for src in table.values()
) - {""}


def allowed_poster(url: object) -> str:
    """Адрес обложки, если он с известного сайта по https, иначе ""."""
    u = store.clean_poster(url)
    if not u:
        return ""
    try:
        parts = urllib.parse.urlsplit(u)
        port = parts.port
    except ValueError:
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host or port not in (None, 443) \
            or parts.username is not None or parts.password is not None:
        return ""
    if any(host == d or host.endswith("." + d) for d in POSTER_HOSTS):
        return u
    return ""


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
    # Номер тайтла в справочнике («mal:5114»). По нему одно аниме — одна
    # карточка, на каком бы языке и с какого источника его ни смотрели
    # (store.is_twin). Чужое значение не ломает ничего: store.clean_cid
    # пропускает только «mal:<цифры>» и «al:<цифры>».
    cid: str = Field(default="", max_length=40)

    @field_validator("title", "title_en")
    @classmethod
    def _one_line(cls, value: str) -> str:
        # Название уходит в тему письма о новой серии, а заголовок письма
        # с переводом строки не собирается вовсе (см. mail.one_line). Да и
        # в списке перевод строки посреди названия ничего хорошего не даёт.
        return mail.one_line(value)

    @field_validator("poster")
    @classmethod
    def _known_poster(cls, value: str) -> str:
        # Чужую картинку не сохраняем (см. POSTER_SITES) — молча, без
        # отказа: прогресс важнее обложки, а обложку страница найдёт сама.
        return allowed_poster(value)


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
    #
    # Раз блокировка стоит до пароля, она обязана зависеть от адреса:
    # иначе чужой перебор запирает и самого владельца. Как устроены
    # счётчики — в security.LoginGuard.
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
        # Перебор кода тем, кто уже знает пароль. Обычные счётчики входа
        # считают по адресам и с сотни адресов пропускают десятки попыток
        # в минуту — при шести цифрах это реальный шанс за несколько дней.
        # Свой счётчик на аккаунт: CODE_FAILS_MAX неверных кодов за
        # CODE_WINDOW — и коды для этого аккаунта не принимаются, пока
        # окно не сдвинется. С адреса, откуда в аккаунт уже входили
        # (login_guard.is_known), владелец заходит как обычно.
        if (code_fails.count(f"c:{user['id']}") >= CODE_FAILS_MAX
                and not login_guard.is_known(key, ip)):
            raise HTTPException(status_code=429,
                                detail="Слишком много неверных кодов. Попробуйте через 15 минут.",
                                headers={"Retry-After": str(CODE_WINDOW)})
        # Каждый код пускает один раз. Подошёл по цифрам, но его шаг
        # времени уже был использован, — это повтор: подсмотренный или
        # перехваченный код. Раньше им можно было входить снова и снова,
        # пока он не протухнет.
        step = twofa.matched_step(store.totp_secret(user), body.code)
        ok = bool(step) and store.claim_totp_step(user["id"], step)
        if not ok:
            # Может быть и запасной код — тот, что выдали при включении.
            ok = store.use_backup_code(user["id"], twofa.hash_backup(body.code))
        if not ok:
            login_guard.note_failure(key, ip)
            code_fails.allow(f"c:{user['id']}")
            if code_fails.count(f"c:{user['id']}") == CODE_FAILS_MAX:
                # Пароль у этого человека знает кто-то ещё — иначе до кода
                # дело бы не дошло. Строка в журнале — сигнал сменить пароль.
                log.warning("вход по коду: %s — %d неверных кодов подряд при верном пароле. "
                            "Похоже, пароль знает посторонний: смените его.",
                            security.safe_for_log(login), CODE_FAILS_MAX)
            if step:
                raise HTTPException(
                    status_code=401,
                    detail="Этот код уже использован. Дождитесь следующего в приложении.")
            raise HTTPException(status_code=401, detail="Код не подошёл")

    login_guard.note_success(key, ip)
    # Прежний вход этого браузера (если был) закрываем: новая сессия его
    # заменяет. Раньше старый токен оставался рабочим ещё две недели —
    # кука в браузере переписывалась, а строка в базе (или гостевой
    # пропуск в памяти) жила дальше, и кто успел его скопировать, мог им
    # пользоваться.
    previous = request.cookies.get(SESSION_COOKIE, "")
    if previous:
        guests.drop(previous)
        store.drop_session(previous)
    token = security.new_token()
    store.create_session(user["id"], token, ua_hash(request))
    csrf = set_session_cookies(response, token, security.SESSION_IDLE)
    note_cookie_sent(token)
    log.info("вход: %s (%s)", security.safe_for_log(login), user["role"])
    return {"ok": True, "csrf": csrf, "me": me_payload(
        Caller("admin" if user["role"] == "admin" else "user", user=user))}


@app.post("/api/auth/guest")
async def auth_guest(request: Request, response: Response):
    """Гостевой пропуск.

    Метки формы здесь быть не может — до входа её просто нет. Раньше из-за
    этого ручка не проверяла ничего: чужой сайт отправлял сюда обычную
    HTML-форму, браузер получал гостевую куку и молча менял ею сессию
    человека. Тот оказывался гостем посреди просмотра и не понимал почему.

    Поэтому две проверки:
      * тело обязано быть JSON. Наша страница так и шлёт, а HTML-форма
        с чужого сайта не умеет: она отправляет только текст или поля
        формы. Отправить JSON на чужой адрес из скрипта браузер без
        разрешения сервера не даст — а мы его не даём;
      * кто уже вошёл в аккаунт, гостевой пропуск не получает: иначе
        его кука перезаписалась бы гостевой. У гостя, который уже гость,
        просто возвращаем его же пропуск, нового не выдаём.
    """
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=403, detail="Запрос отклонён")
    who = await whoami(request)
    if who is not None and not who.is_guest:
        raise HTTPException(status_code=409,
                            detail="Вы уже вошли в аккаунт. Чтобы зайти гостем, сначала выйдите.")
    if who is not None:
        return {"ok": True, "csrf": security.csrf_for(who.token), "me": me_payload(who)}
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


def settings_view(data: dict) -> dict:
    """Настройки для страницы. Записи до разделения языков знают только
    lang — им ui_lang достаётся от него же."""
    data = dict(data or {})
    if data.get("lang") and not data.get("ui_lang"):
        data["ui_lang"] = data["lang"]
    return data


def adult_ok(who: Caller) -> bool:
    """Показывать ли этому человеку 18+ (r_plus). Гостю — никогда: у него
    нет настроек, а значит, и согласия."""
    if who.is_guest or who.user is None:
        return False
    return store.get_settings(who.user_id).get("adult") is True


def hentai_ok(who: Caller) -> bool:
    """Показывать ли этому человеку хентай (rx).

    Только вошедшему (не гостю) и только если у него включены ОБА
    переключателя — 18+ и «Хентай». Решает сервер по настройкам из базы:
    что бы ни прислала страница, гость хентай не получит, а записанное в
    базу «hentai: true» без «adult: true» не значит ничего."""
    if who.is_guest or who.user is None:
        return False
    s = store.get_settings(who.user_id)
    return s.get("adult") is True and s.get("hentai") is True


def me_payload(who: Caller) -> dict:
    if who.is_guest:
        left = max(0, int(who.guest["expires"] - time.time()))
        return {
            "kind": "guest", "role": "guest", "name": "guest",
            "display_name": "guest", "avatar_color": "#D9A97F",
            "has_avatar": False, "banner_at": 0, "expires_in": left,
            "settings": {}, "can_edit": False,
        }
    u = who.user
    return {
        "kind": who.kind, "role": u["role"],
        "name": u["login"], "display_name": u["display_name"] or u["login"],
        "avatar_color": u["avatar_color"],
        "has_avatar": bool(u["has_avatar"]),
        # Баннер: время, когда его поставили (0 — нет). Страница берёт
        # его и как признак, и как метку для адреса картинки.
        "banner_at": store.banner_at(u["id"]),
        "expires_in": None,
        "settings": settings_view(store.get_settings(u["id"])),
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
    if not within(api_limit, "any", who):
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
    pending_2fa.pop(who.user_id, None)
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
        "ui_lang": {"ru", "en"},
        # Только те языки аниме, которые сайт умеет: ru, endub, ensub
        # (anime.ANIME_LANGS). Старое имя «en» сюда не проходит — его могла
        # прислать только страница до обновления, а там английский был
        # выключен.
        "anime_lang": set(anime.ANIME_LANGS),
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
    # Старое и новое имя языка сайта держим одинаковыми: страница старой
    # версии шлёт lang, новая — ui_lang.
    if "ui_lang" in incoming:
        incoming["lang"] = incoming["ui_lang"]
    elif "lang" in incoming:
        incoming["ui_lang"] = incoming["lang"]
    data.update(incoming)
    # Хентай — только поверх 18+. Выключил 18+ — хентай выключился тоже;
    # включить хентай при выключенном 18+ нельзя: он просто останется
    # выключенным, и страница увидит это в ответе.
    if data.get("adult") is not True and data.get("hentai"):
        data["hentai"] = False
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
IMAGE_FORMATS = ("JPEG", "PNG", "WEBP")


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
        img = _open_image(raw)
        from PIL import Image              # раз открылась — обработчик на месте
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


def _open_image(raw: bytes):
    """Открывает присланную картинку со всеми проверками и отдаёт её уже
    в RGB. Общая для аватара и баннера: проверки одни и те же, и второй
    их копии, которая однажды разойдётся с первой, быть не должно.

    Ошибка — ValueError (или любая ошибка разбора): вызывающий превращает
    её в «Не удалось прочитать изображение»."""
    try:
        from PIL import Image
    except ImportError:
        raise HTTPException(status_code=500, detail="На сервере не установлен обработчик картинок")
    # Разбирать — только этими тремя форматами. Без списка Pillow
    # пробует все известные ему (EPS, PSD, ICNS, FITS…), и чужой файл
    # доходил бы до разборщиков, которые нам не нужны вовсе: каждый лишний
    # разборщик — лишнее место для ошибки в самом Pillow. MPO — это JPEG
    # с телефона (несколько кадров), его открывает тот же разборщик JPEG.
    # Размер PNG виден прямо в заголовке (IHDR: ширина и высота — байты
    # 16–23). Смотрим его ДО Pillow: в Pillow до 13.0 анимированный PNG
    # заставлял Image.open() занять память под заявленный холст ещё до
    # проверки размера ниже (CVE-2026-112399) — 20000×20000 это ~1,6 ГБ,
    # и контейнер с потолком 1 ГБ падал. Обновлённый Pillow это закрыл;
    # эта строка — второй замок на случай старой .venv у себя на компьютере.
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if w * h > AVATAR_MAX_PIXELS:
            raise ValueError("слишком большой холст")
    img = Image.open(io.BytesIO(raw), formats=IMAGE_FORMATS)
    img.verify()                       # быстрая проверка целостности
    img = Image.open(io.BytesIO(raw), formats=IMAGE_FORMATS)  # verify() «закрывает» файл
    if img.format not in ("JPEG", "PNG", "WEBP", "MPO"):
        raise ValueError("формат " + str(img.format))
    # Защита от «бомбы»: гигантский холст при крошечном файле. Размер
    # спрашиваем ДО перевода в RGB — до этой строки картинка ещё не
    # развёрнута в память, а после была бы уже развёрнута целиком,
    # и проверка опаздывала бы ровно на тот расход, от которого спасает.
    if img.width * img.height > AVATAR_MAX_PIXELS:
        raise ValueError("слишком большой холст")
    return img.convert("RGB")


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


# --------------------------------------------------------------------------
# Баннер профиля
# --------------------------------------------------------------------------
# Картинка в шапке карточки профиля и настроек. Устроен как аватар: те же
# проверки (вид, размер, «бомба»), тот же лимит частоты, картинка всегда
# пересобирается сервером в чистый JPEG и отдаётся только самому хозяину.
# Страница присылает уже уменьшенную (до 960×320), но сервер на это не
# рассчитывает: обрезает до 3:1 и уменьшает сам.
BANNER_MAX_BYTES = 700 * 1024      # присланное изображение
BANNER_W, BANNER_H = 960, 320      # 3:1 — шапка карточки и настроек


@app.post("/api/me/banner")
async def api_banner(request: Request, who: Caller = Depends(need_user)):
    guard_csrf(request)
    if not avatar_limit.allow(f"b:{who.user_id}"):
        raise HTTPException(status_code=429,
                            detail="Слишком часто. Попробуйте через несколько минут.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > BANNER_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > BANNER_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Файл слишком большой")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        store.set_banner(who.user_id, None)
        return {"ok": True, "removed": True}
    data = await asyncio.to_thread(_decode_banner, raw)
    store.set_banner(who.user_id, data)
    return {"ok": True, "banner_at": store.banner_at(who.user_id)}


def _decode_banner(raw: bytes) -> bytes:
    """Как _decode_image, только кадр — полоса 3:1 по середине картинки.
    Маленькую картинку не растягиваем: пусть будет мельче, но не мылом."""
    try:
        img = _open_image(raw)
        from PIL import Image              # раз открылась — обработчик на месте
        w, h = img.width, img.height
        if w < 8 or h < 8:
            raise ValueError("слишком маленькая")
        if w / h > BANNER_W / BANNER_H:
            cw, ch = int(h * BANNER_W / BANNER_H), h
        else:
            cw, ch = w, int(w * BANNER_H / BANNER_W)
        left, top = (w - cw) // 2, (h - ch) // 2
        img = img.crop((left, top, left + cw, top + max(1, ch)))
        if img.width > BANNER_W:
            img = img.resize((BANNER_W, BANNER_H), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()
    except HTTPException:
        raise
    except Exception as exc:               # noqa: BLE001
        log.info("плохой баннер: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=400, detail="Не удалось прочитать изображение")


@app.get("/api/me/banner")
async def api_banner_get(who: Caller = Depends(need_any)):
    """Свой баннер — и только свой: чужих баннеров, как и чужих аватаров,
    сервер не отдаёт никому."""
    if who.is_guest:
        raise HTTPException(status_code=404, detail="Не найдено")
    blob = store.get_banner(who.user_id)
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
    items = store.library(who.user_id)
    # Подпись источника для карточки: «Мой источник», а не «moy_istochnik».
    # Её нет в базе — она из списка источников, поэтому дописывается тут.
    for it in items:
        it["source_label"] = anime.SOURCES.get(it["source"], {}).get("label", "") \
            or anime.PRIVATE_SOURCES.get(it["source"], {}).get("label", "")
    return {"items": items, "guest": False}


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
        saved = store.save_progress(who.user_id, body.model_dump())
    except store.LibraryFull as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # Повторная отметка той же серии в пределах store.REWATCH_GAP в журнал
    # не идёт: это не второй просмотр, а тот же самый, открытый заново
    # (перезагрузка, другое устройство). Иначе итоги года завышались.
    # Имя — то, под которым запись лежит в полке после склейки (русское,
    # если оно известно): одно аниме в итогах года — одной строкой, на
    # каком бы языке его ни смотрели.
    counted = store.log_watch(who.user_id, body.key, saved.get("title") or body.title,
                              body.genres, body.watched_ep, body.position)
    return {"ok": True, "counted": counted}


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
    rx_source_ok(body.source, who)
    if not within(source_limit, "p", who):
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

    Источники опрашивает фоновая проверка (refresh_updates) раз в
    четверть часа, и ответ здесь обычно берётся из базы — мгновенно.
    Сами спрашиваем только про то, что давно не проверялось: тайтл
    добавили только что, сервер недавно запустился, фоновая проверка
    выключена. Тогда — свежий список серий, не трёхчасовой кэш: иначе
    о серии узнавали вечером того дня, когда она вышла.
    """
    edge = store.now() - anime.FRESH_TTL
    stale = [r for r in store.user_rows_to_check(who.user_id)
             if int(r["checked_at"] or 0) < edge
             and r["source"] in anime.SOURCES][:UPDATES_MAX_TITLES]
    # Лимит — только на походы к источникам. Прочитать из базы готовое
    # можно всегда: лишний раз открытая вкладка не повод для «429».
    if stale and within(source_limit, "u", who):
        await check_titles(stale, UPDATES_MAX_TITLES)

    found = []
    for row in store.fresh_updates(who.user_id):
        known, last = int(row["total_eps"]), int(row["latest_ep"])
        found.append({
            "key": row["key"], "source": row["source"], "title": row["title"],
            "title_en": row["title_en"],
            "poster": row["poster"], "year": row["year"], "genres": row["genres"],
            "watched_ep": row["watched_ep"], "position": row["position"],
            "was": known, "now": last, "fresh": last - known,
            "new_at": int(row["new_at"] or 0),
        })
    return {"items": found, "checked": len(stale), "every": REFRESH_EVERY}


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
    rx_source_ok(body.source, who)
    # Тот же лимит, что у остальных ручек, которые ходят к источникам.
    # Раньше его здесь не было, и эта ручка оставалась лазейкой: сколько
    # угодно обращений к чужому сайту в минуту под видом «я видел серию».
    if not within(source_limit, "seen", who):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    row = store.library_row(who.user_id, body.key)
    # Номер новинки уже лежит в базе — его и запоминаем, к источнику не
    # ходим. Спрашиваем источник только если проверки ещё не было.
    last = int(row["latest_ep"] or 0) if row else 0
    if not last:
        try:
            episodes = await anime.find_episodes(body.source, body.key, body.title)
        except HTTPException:
            raise
        for ep in episodes:
            try:
                last = max(last, int(ep.ordinal))
            except (TypeError, ValueError):
                continue
    if last:
        store.mark_seen(who.user_id, body.key, last)
    return {"ok": True, "now": last}


@app.delete("/api/library/{key:path}")
async def api_library_delete(key: str, request: Request,
                             who: Caller = Depends(need_user)):
    guard_csrf(request)
    store.remove_from_library(who.user_id, key[:200])
    return {"ok": True}


# Русское имя жанра → английское (для итогов года, см. api_stats).
GENRE_EN = {ru.lower(): en for en, ru in catalog.GENRE_RU.items()}


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
    out = store.year_stats(who.user_id, start, tz)
    # Жанры в журнале — по-русски (их прислал источник). Английские имена —
    # из того же словаря, которым справочник переводит жанры AniList
    # (catalog.GENRE_RU, наоборот): {русский: английский}. Незнакомого
    # жанра в словаре нет — страница решает сама, что с ним делать.
    out["genres_en"] = {g: GENRE_EN[g.lower()] for g, _ in out["genres"]
                        if g.lower() in GENRE_EN}
    return out


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
         # Аватар в списке — тот же, что у человека в шапке: его цвет,
         # а не цвет по роли.
         "avatar_color": r["avatar_color"] or "#84CBB6",
         "has_avatar": bool(r["has_avatar"]),
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
    # В журнал — проверенный логин, а не присланная строка: в ней мог
    # быть перевод строки, и запись в журнале раздвоилась бы.
    log.info("админ %s создал аккаунт %s", security.safe_for_log(who.name),
             security.safe_for_log(login))
    return {"ok": True, "id": uid}


# Номер пользователя в адресе. Без границ число любой длины доходило до
# SQLite и падало там внутренней ошибкой (OverflowError → 500) вместо
# понятного «не тот номер».
USER_ID = Path(..., ge=1, le=2**62)


@app.post("/api/admin/users/{user_id}/disable")
async def admin_disable(request: Request, user_id: int = USER_ID,
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
    # Запрет только для включённого администратора: выключенный в подсчёт
    # и так не входит. Раньше уже выключенного нельзя было тронуть вовсе.
    if store.is_last_admin(target):
        raise HTTPException(status_code=409, detail="Это последний администратор")
    store.set_disabled(user_id, True)
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/enable")
async def admin_enable(request: Request, user_id: int = USER_ID,
                       who: Caller = Depends(need_admin)):
    guard_csrf(request)
    if store.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="Нет такого пользователя")
    store.set_disabled(user_id, False)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
async def admin_delete(request: Request, user_id: int = USER_ID,
                       who: Caller = Depends(need_admin)):
    guard_csrf(request)
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Нет такого пользователя")
    if int(user_id) == who.user_id:
        raise HTTPException(status_code=409, detail="Себя удалить нельзя")
    # Удаление выключенного администратора действующих не убавляет.
    if store.is_last_admin(target):
        raise HTTPException(status_code=409, detail="Это последний администратор")
    store.delete_user(user_id)
    return {"ok": True}


# ==========================================================================
# Каталог аниме
# ==========================================================================
ANIME_LANG_HELP = ("язык аниме — озвучки и субтитров, НЕ язык меню сайта: "
                   "ru, endub (английский дубляж) или ensub (японская дорожка "
                   "с английским текстом); en — старое имя ensub; остальное — ru")


@app.get("/api/sources")
async def api_sources(
    lang: str = Query("ru", max_length=5, description=ANIME_LANG_HELP),
    who: Caller = Depends(need_any),
):
    """Источники того же языка, что и аниме (не сайт!).

    Раньше отдавались все вперемешку, и в меню у англоязычного
    посетителя стояли русские сайты. Потом язык источников
    привязали к языку сайта — и человек, включивший английские меню, молча
    получал английскую озвучку. Теперь решает язык аниме (anime.anime_lang).
    """
    # В меню — источники языка и те, что подходят выбору отчасти (поле
    # "also", см. anime.menu_sources): их выбирают руками.
    allowed = anime.menu_sources(lang)
    # Источник хентая (anime.RX_ORDER) — только тем, у кого хентай включён,
    # и только при английском языке аниме (у него японская речь с
    # английским текстом). Страница просмотра держит его в меню лишь на
    # хентай-тайтле (watch.js), но знать о нём должна: иначе тайтл,
    # открытый с него, она сочла бы «источником не этого языка» и увела бы
    # искать заново.
    if hentai_ok(who) and anime.anime_lang(lang) == "en" and not anime.DEMO:
        allowed = allowed + [s for s in anime.RX_ORDER if s in anime.SOURCES and s not in allowed]
    return [{"id": sid, **{k: v for k, v in anime.SOURCES[sid].items() if k != "also"}}
            for sid in allowed]


def rx_source_ok(source: str, who: Caller) -> None:
    """Источник только для хентая (rx_only) — только тем, кому хентай включён.

    Остальным он «неизвестный» (как и любое чужое имя): ни списка серий,
    ни ссылок, ни обложек — даже по прямому адресу ручки."""
    if anime.rx_only(source) and not hentai_ok(who):
        raise HTTPException(status_code=400, detail="Неизвестный источник")


# Порядок перебора, если выбранный источник молчит. Сайты периодически
# ложатся или меняют адреса — тогда пробуем следующий, а не показываем
# человеку ошибку.
#
# Порядок стоит собирать по живой проверке. В личной версии один
# источник (RU-B) пришлось поднять на второе место: у него единственного
# нашлась «Атака титанов» со всеми сезонами, пока два других на тот же
# запрос дружно отвечали «Не издевайся, Нагаторо». А источник, который не
# вернул ни одного результата ни на один проверенный запрос, лучше убрать
# из перебора вовсе: каждое обращение к нему — потраченная секунда
# ожидания. Как выбор вручную он остаётся.
#
# Здесь пусто: источников в публичной версии нет (см. anime.SOURCES).
FALLBACK_ORDER: list[str] = []



def fallback_for(lang: str) -> list[str]:
    """Порядок перебора для языка аниме (или выбора целиком: endub/ensub —
    тогда без источников, у которых этого варианта нет)."""
    if anime.DEMO:
        # Перебирать нечего: источник один, и он для обоих языков.
        return list(anime.SOURCES)
    if anime.anime_lang(lang) == "en":
        return anime.sources_for(lang)
    return FALLBACK_ORDER


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
        # Срок на один поиск. Не ответил вовремя — значит молчит, как и
        # при любой другой беде: перебор пойдёт к следующему источнику.
        results = await anime.call(extractor.a_search(q), source)
    except anime.LibrariesMissing:
        # А вот это не молчание источника, а неустановленная библиотека.
        # Глотать её как None значило бы снова сказать человеку «ни один
        # источник не отвечает» — неправду, из-за которой ищут не там.
        raise
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


def budget_left(started: float) -> float:
    """Сколько осталось от общего срока запроса, начатого в started."""
    return anime.BUDGET - (time.monotonic() - started)


def pick_source(source: str, lang: str) -> str:
    """Проверяет присланный источник и выбирает, с какого начать.

    Сначала проверка, потом подстановка — именно в таком порядке. Раньше
    было наоборот: в демо-режиме любое имя сразу заменялось на демо-
    источник, и мусор вроде «../../os» проходил молча. Подмена тут ничем
    не грозила, но ручка, которая принимает что угодно, перестаёт
    сообщать об ошибках — и следующая ошибка уже не будет видна.

    Источник обязан говорить на языке сайта. Пустой — берём тот, что для
    этого языка основной: так ссылка без источника работает на обоих
    языках и никогда не приводит к чужой озвучке.

    В демо-режиме источник один, а браузер может прислать имя источника
    личной версии (сохранённое раньше). Такие имена — известные, просто
    не из этого режима: их не отклоняем, а меняем на демо-источник.
    Иначе запасной поиск (когда справочник не ответил) получал
    «Неизвестный источник».
    """
    if source and source not in anime.SOURCES and not (
            anime.DEMO and source in anime.PRIVATE_SOURCES):
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    if not source or anime.DEMO:
        return anime.default_source(lang)
    # lang может быть и выбором целиком (ensub): тогда годятся и источники
    # из меню (поле "also"), куда человек переключился сам.
    if source not in anime.menu_sources(lang):
        return anime.default_source(lang)
    return source


@app.get("/api/search")
async def api_search(
    request: Request,
    # Потолок как у названия в списке (300): страница ищет и по сохранённому
    # названию, а у ранобэ оно бывает длиннее сотни знаков. С прежним
    # потолком в 100 такой поиск получал отказ 422 ещё до сервера.
    q: str = Query(..., min_length=2, max_length=300),
    source: str = Query("", max_length=40),
    any_source: bool = Query(True, description="искать дальше, если выбранный молчит"),
    lang: str = Query("ru", max_length=5, description=ANIME_LANG_HELP),
    who: Caller = Depends(need_any),
):
    """Поиск прямо у источников видео. Рейтинга у них нет, поэтому 18+
    здесь не отсеивается: этот поиск — запасной,
    им пользуются, когда справочник молчит или не знает тайтла."""
    anime.need_sources()                       # личный режим без источников — 501
    if not within(search_limit, "s", who):
        raise HTTPException(status_code=429, detail="Слишком частый поиск, подождите минуту")
    mode = anime.anime_mode(lang)
    lang = anime.anime_lang(mode)
    source = pick_source(source, mode)

    order = [source] if source else []
    if any_source:
        order += [s for s in fallback_for(mode) if s != source]

    tried: list[str] = []
    answered = False          # хоть кто-то вообще ответил
    # Лучшее из непохожего: если ни у кого не нашлось толком, покажем хотя
    # бы это, а не пустоту. Пустой экран на запрос, который где-то что-то
    # нашёл, выглядит как поломка.
    weak: tuple[str, list] | None = None
    started = time.monotonic()

    for name in order:
        # Общий срок на весь перебор. Каждый источник ограничен своим
        # сроком, но четыре по двадцать секунд — это восемьдесят, а nginx
        # ждёт сто двадцать и потом обрывает запрос пустой ошибкой 504.
        # Вышел срок — отвечаем тем, что успели собрать.
        if budget_left(started) <= 0:
            break
        tried.append(name)
        with anime.budget(budget_left(started)):
            rows = await try_source(name, q)
        if rows is None:
            continue          # источник молчит — пробуем следующий
        answered = True
        if not rows:
            continue

        # Раньше перебор заканчивался здесь, на первом же непустом ответе.
        # Из-за этого «Атака титанов» навсегда упиралась в единственный
        # мусорный ответ RU-A, хотя следующий источник в списке знал
        # правильный тайтл. Теперь непохожий ответ не считается ответом.
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
    source: str = Query("", max_length=40),
    title: str = Query("", max_length=300),
    who: Caller = Depends(need_any),
):
    anime.need_sources()                       # личный режим без источников — 501
    if source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    rx_source_ok(source, who)
    if not within(source_limit, "e", who):
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
# «Озвучка RU-A», «Озвучка Yozh», «Озвучка Lis Project», «Субтитры
# Luna». А код читал `name` — поля с таким именем у него нет вовсе,
# getattr возвращал пустую строку, и в меню «Озвучка» у каждой строки
# стояло слово «плеер». Выбор из восьми одинаковых «плееров» — это не
# выбор, а лотерея.
#
# Второе: одна и та же озвучка приезжает по нескольку раз. У RU-B
# на одну серию «Магической битвы» приходит пятьдесят четыре плеера — это
# тридцать семь озвучек, разложенных по разным видеохостингам.
#
# Третье, и самое дорогое: узнать ссылки можно только спросив хостинг, а
# каждый такой вопрос — около семи секунд. Спросить все пятьдесят четыре
# значит заставить человека ждать минуту, глядя на пустой плеер. Даже
# десять параллельно — это десять секунд.
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
# Так это называется у RU-E, и до сих пор мы этого не видели: правило
# выше ищет слово «субтитры» в НАЧАЛЕ строки, а здесь оно в скобках на
# конце. Между тем это ровно то, что ищут, когда просят «оригинал и
# текст»: у «Атаки титанов», «Наруто», «Магической битвы» и «Ван-Пис»
# такой вариант у RU-E есть, а у русских аниме-сайтов — только чужая
# озвучка поверх японской.
_ORIG_SUB = re.compile(r"(?:ориг|origin|japan|яп\.)", re.I)
_HAS_SUB = re.compile(r"(?:субтитр|саб[ыов]|\bsubs?\b|subtitle)", re.I)


# По этим словам в названии видно, что текст английский. Список короткий
# и, скорее всего, никогда не сработает: живая проверка по четырём
# тайтлам у трёх источников не нашла ни одного английского варианта —
# все субтитры русские, работа русских команд (Meduza, Romashka,
# Sova) и русские дорожки Luna. Но если английский вариант
# когда-нибудь появится, он будет опознан, а не выдан за русский.
_EN_SUB = re.compile(r"\b(?:eng|english|en[-_ ]?sub)\b", re.I)


def is_sub_track(name: str) -> bool:
    """Это вариант с текстом поверх оригинальной дорожки?

    Два вида: «Субтитры Luna» — так пишут аниме-сайты, и
    «Оригинал (+субтитры)» — так пишет RU-E. Раньше видели только
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


_EN_AUDIO = re.compile(r"\b(?:english|eng)\b|англ", re.I)


def dub_audio(name: str, is_sub: bool, source: str) -> str:
    """На каком языке звучит вариант: ja (оригинал под текстом), en, ru."""
    if is_sub:
        return "ja"
    if anime.source_lang(source) == "en" or _EN_AUDIO.search(name or ""):
        return "en"
    return "ru"


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
        # Хостинг пускает только со «своей» страницы (Referer) — тогда
        # поток идёт через нашу прокладку (/api/hls, см. api/hls.py).
        url = hls.proxied(url, str(getattr(v, "referer", "") or ""))
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


# Ответ, когда английского нет. Текст на двух языках: страница показывает
# его как есть, а людям с английским сайтом «нигде нет» по-русски ни к чему.
NO_ENGLISH = ("На английском этого пока нигде нет · Not available in English yet")
# Код ответа «источник не того языка»: страница по нему не показывает
# ошибку, а ищет тайтл у источников нужного языка (relocate в watch.js).
# Свой код, а не 409: тот уже значит «тайтл выпал из памяти сервера».
LANG_MISMATCH = 412
# Общий срок /api/videos (см. videos_in). Тот же, что у остальных походов
# к источникам: nginx ждёт 120 с, человек — меньше.
VIDEOS_BUDGET = anime.BUDGET


def english_tracks(mode: str, source: str, by_dub: dict, order: list[str]) -> list[str]:
    """Что из вариантов серии можно отдать при английском языке аниме.

    Строго:
      * endub — английский дубляж; если его у серии нет — японская речь с
        АНГЛИЙСКИМ текстом (страница честно скажет, что дубляжа нет);
      * ensub — только японская речь с английским текстом.
    Русская озвучка и русский текст не проходят никогда.
    """
    en_sub = [n for n in order if by_dub[n]["sub"] and sub_lang(n) == "en"]
    if mode == "ensub":
        return en_sub
    # Дубляж — только тот, что назван английским («English dub»): источник
    # английский, но вариант с русским именем всё равно русский.
    en_dub = [n for n in order if not by_dub[n]["sub"] and _EN_AUDIO.search(n)]
    return en_dub + en_sub


# Ответы /api/videos — сколько помнить. Это последний шаг до картинки:
# список плееров серии (~секунда у источника) и ссылки первой озвучки
# (ещё секунда). Повторный заход в ту же серию (перезагрузка, «Продолжить»
# с телефона, вернулся из другой вкладки) раньше платил обе заново. Ссылки
# у хостингов подписаны на часы, пять минут — с большим запасом. Помним
# только удачный ответ.
VIDEOS_TTL = 5 * 60
# Сколько прогревов серии (warm_videos) идёт разом на весь сайт.
WARM_PARALLEL = 2
_inflight: dict[str, asyncio.Task] = {}
_warm_gate: dict[str, Any] = {"loop": None, "sem": None}


async def shared(key: str, make) -> Any:
    """Одинаковая работа, начатая разом, делается один раз: второй ждёт
    первого. Ушёл тот, кто начал, — работа доделывается (shield) и
    достаётся следующему. Задачи привязаны к циклу событий (у сервера он
    один; в проверках у каждого запроса свой — тогда начинаем заново)."""
    loop = asyncio.get_running_loop()
    task = _inflight.get(key)
    if task is None or task.done() or task.get_loop() is not loop:
        task = loop.create_task(make())
        _inflight[key] = task

        def forget(t: asyncio.Task, key: str = key) -> None:
            if _inflight.get(key) is t:
                _inflight.pop(key, None)
            if not t.cancelled():
                t.exception()                  # не «Task exception was never retrieved»
        task.add_done_callback(forget)
    return await asyncio.shield(task)


def videos_key(source: str, key: str, ordinal: int, dub: str, mode: str) -> str:
    return "videos:" + json.dumps([source, key, int(ordinal), dub, mode], ensure_ascii=False)


@app.get("/api/videos")
async def api_videos(
    key: str = Query(..., max_length=200),
    ordinal: int = Query(..., ge=0, le=100000),
    source: str = Query("", max_length=40),
    title: str = Query("", max_length=300),
    dub: str = Query("", max_length=200, description="какую озвучку открыть"),
    lang: str = Query("ru", max_length=5, description=ANIME_LANG_HELP),
    who: Caller = Depends(need_any),
):
    """Плееры и ссылки серии — с памятью (VIDEOS_TTL) и склейкой одинаковых
    запросов, в том числе с прогревом после резолва (warm_videos)."""
    anime.need_sources()                       # личный режим без источников — 501
    mode = anime.anime_mode(lang)
    # До памяти ответов: ответ, сохранённый для другого человека, тоже не
    # должен уйти тому, кому хентай не включён.
    rx_source_ok(source, who)
    ck = videos_key(source, key, ordinal, dub, mode)
    hit = anime.cache_get(ck)
    if hit is not None:
        return hit
    out = await shared(ck, lambda: videos_in(key=key, ordinal=ordinal, source=source,
                                             title=title, dub=dub, lang=lang, who=who))
    anime.cache_put(ck, out, VIDEOS_TTL)
    return out


# Сколько прогревов может ждать своей очереди. Раньше каждый резолв
# заводил задачу, и они копились без счёта: прогревов разом идёт два
# (WARM_PARALLEL), а резолвов в минуту — десятки, и при медленном
# источнике очередь росла, пока хватало памяти. Ссылок на задачи никто
# не держал, а asyncio хранит на них только слабые ссылки — задачу могло
# прибрать посреди работы. Теперь задачи лежат в _warm_tasks, и сверх
# потолка прогрев просто не заводится: это ускорение, а не обязанность.
WARM_PENDING_MAX = 8
_warm_tasks: set = set()


def spawn_warm(make) -> None:
    loop = asyncio.get_running_loop()
    for t in [t for t in _warm_tasks if t.done() or t.get_loop() is not loop]:
        _warm_tasks.discard(t)
    if len(_warm_tasks) >= WARM_PENDING_MAX:
        return
    task = loop.create_task(make())
    _warm_tasks.add(task)
    task.add_done_callback(_warm_tasks.discard)


async def warm_videos(source: str, key: str, title: str, mode: str, ep: int,
                      who: Caller) -> None:
    """Прогрев серии после резолва: пока страница просмотра грузится (или
    пока человек ещё читает список частей), сервер уже спрашивает у
    источника плееры и ссылки той серии, с которой страница начнёт. Та же
    работа, что сделала бы страница через секунду, — не лишняя; одна на
    тайтл, и не больше WARM_PARALLEL разом на весь сайт."""
    loop = asyncio.get_running_loop()
    if _warm_gate["loop"] is not loop:
        _warm_gate.update(loop=loop, sem=asyncio.Semaphore(WARM_PARALLEL))
    try:
        async with _warm_gate["sem"]:
            eps = await anime.find_episodes(source, key, title)
            nums = []
            for e in eps:
                try:
                    nums.append(int(e.ordinal))
                except (TypeError, ValueError):
                    continue
            if not nums:
                return
            ordinal = ep if ep in nums else nums[0]
            ck = videos_key(source, key, ordinal, "", mode)
            if anime.cache_get(ck) is not None:
                return
            out = await shared(ck, lambda: videos_in(key=key, ordinal=ordinal, source=source,
                                                     title=title, dub="", lang=mode, who=who))
            anime.cache_put(ck, out, VIDEOS_TTL)
    except Exception as exc:                       # noqa: BLE001
        log.info("прогрев серии не вышел: %s", type(exc).__name__)


async def videos_in(
    key: str = Query(..., max_length=200),
    ordinal: int = Query(..., ge=0, le=100000),
    source: str = Query("", max_length=40),
    title: str = Query("", max_length=300),
    dub: str = Query("", max_length=200, description="какую озвучку открыть"),
    lang: str = Query("ru", max_length=5, description=ANIME_LANG_HELP),
    who: Caller = Depends(need_any),
):
    mode = anime.anime_mode(lang)
    track = anime.anime_track(mode)
    lang = anime.anime_lang(mode)
    if source not in anime.SOURCES:
        raise HTTPException(status_code=400, detail="Неизвестный источник")
    rx_source_ok(source, who)
    strict = lang == "en" and not anime.DEMO
    # Английский язык аниме, а источник русский (тайтл сохранён с него,
    # или страница не дождалась списка источников). Раньше такой запрос
    # проходил — и поверх японской речи играла русская озвучка. Теперь
    # сервер отказывает, а страница ищет тайтл у английских источников.
    if strict and anime.source_lang(source) != "en":
        raise HTTPException(status_code=LANG_MISMATCH,
                            detail="Этот источник русский, а язык аниме — английский")
    # И обратное: язык аниме русский, а источник английский. Страница так
    # не спрашивает, но полагаться только на неё нельзя: старая вкладка
    # или ручной запрос получали английское видео при «русском» выборе.
    # Источник хентая (rx_only) — исключение по решению владельца: он
    # английский, а хентай показывается при любом языке.
    if not anime.DEMO and lang == "ru" and anime.source_lang(source) == "en" \
            and not anime.rx_only(source):
        raise HTTPException(status_code=LANG_MISMATCH,
                            detail="Этот источник английский, а язык аниме — русский")
    if not within(source_limit, "v", who):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    # Общий срок на всю раздачу серии: список серий, плееры и до
    # DUB_PROBES × HOSTS_PER_DUB хостингов подряд. Раньше срок был только у
    # списка серий, а плееры и хостинги ждались сколько угодно — висящий
    # хостинг держал человека до обрыва nginx через 120 с (аудит 3 ноября
    # 2026, C-11).
    with anime.budget(VIDEOS_BUDGET):
        return await _episode_videos(key, ordinal, source, title, dub, mode, track, strict)


async def _episode_videos(key: str, ordinal: int, source: str, title: str, dub: str,
                          mode: str, track: str, strict: bool) -> dict:
    """Озвучки и ссылки одной серии (тело videos_in — уже под общим сроком)."""
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
        players = await anime.call(episode.a_get_sources(), source)
    except HTTPException:
        raise
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
    if strict:
        order = english_tracks(mode, source, by_dub, order)
        if not order:
            raise HTTPException(status_code=404, detail=NO_ENGLISH)

    # Что ставить первым, решает выбор человека (anime.anime_track):
    # «English — субтитры» — текст поверх оригинала, остальные — озвучку.
    # Раньше английский был один и всегда открывал субтитры; дубляж был
    # только вторым пунктом меню.
    #
    # Исходные места запоминаем заранее: sort перекладывает тот же список,
    # по которому пришлось бы искать позицию, и уже на втором сравнении
    # index() падает с «нет в списке».
    subs_first = track == "sub"
    place = {name: i for i, name in enumerate(order)}
    order.sort(key=lambda n: (by_dub[n]["sub"] != subs_first, place[n]))

    # Отдельные файлы субтитров той озвучки, что ответила (см. subtitles.py).
    found_subs: list[dict] = []

    async def links_of(name: str) -> list[dict]:
        """Ссылки одной озвучки: первый хостинг, который ответит."""
        for host in by_dub[name]["hosts"][:HOSTS_PER_DUB]:
            try:
                # 504 от anime.call (срок вышел) — тоже Exception: ловится
                # ниже, и перебор идёт дальше; следующий вызов при
                # вышедшем сроке отказывает сразу, не ожидая.
                raw, subs = await anime.call(subtitles.videos_and_subs(source, host), source)
                links = pack_links(raw)
            except Exception as exc:               # noqa: BLE001
                log.info("плеер не отдал видео: %s", type(exc).__name__)
                continue
            # Строгий английский: вариант «с текстом», у которого текст
            # отдельными файлами, но среди них нет английского, — не тот.
            if strict and links and by_dub[name]["sub"] and subs and \
                    not any((s.get("lang") or subtitles.guess_lang(str(s.get("label") or ""))) == "en"
                            for s in subs):
                log.info("вариант %s: текст не английский — пропускаем", name)
                continue
            if links:
                found_subs[:] = subs
            # Плеер без единой ссылки не плеер. RU-C отдаёт их
            # исправно — два штуки на «Наруто 1: Книга искусств ниндзя», —
            # а видео внутри нет ни одного: его плееры работают только с
            # адресов СНГ. Такой пустой плеер доезжал до страницы, и она
            # показывала прочерк вместо качества и молчала. Молчание там,
            # где ничего не заработает, читается как «сайт сломался».
            if links:
                return links
        return []

    # Просили определённую — сначала она. Не просили — берём первую,
    # которая отзовётся.
    #
    # Раньше просимая озвучка была единственной попыткой: её хостинг
    # молчит — и весь ответ 502, хотя остальные озвучки этой серии
    # исправны. Страница по такому ответу прятала меню «Озвучка» целиком
    # и писала «на источнике этой серии нет» — неправду. Теперь после
    # неё пробуем остальные, как при обычном открытии, а в "chosen"
    # честно пишем, какая ответила на самом деле: не совпала с просимой —
    # значит, просимая не работает, и страница скажет об этом человеку.
    asked = dub if dub and dub in by_dub and dub in order else ""
    wanted = order[:DUB_PROBES]
    if asked:
        wanted = [asked] + [n for n in wanted if n != asked]
    chosen, videos = "", []
    for name in wanted:
        videos = await links_of(name)
        if videos:
            chosen = name
            break

    if not videos:
        # Никто не ответил, потому что вышел срок, — это «источник не
        # отвечает» (504), а не «плееров нет» (502).
        if anime.time_left() <= 0:
            raise HTTPException(status_code=504, detail=anime.TIMEOUT_DETAIL)
        if asked:
            raise HTTPException(
                status_code=502,
                detail=f"Озвучка «{asked[:80]}» сейчас не отвечает, и другие тоже",
            )
        if strict:
            raise HTTPException(status_code=502, detail="Английский вариант этой серии сейчас "
                                "не отвечает · The English version is not responding right now")
        raise HTTPException(status_code=502, detail="Ни один плеер не отдал видео")

    return {
        # Полный список известен сразу и бесплатно: названия приехали
        # вместе со списком плееров, спрашивать за них никого не нужно.
        # lang говорит, на каком языке текст: страница по нему честно
        # подписывает, что человек получит.
        # audio — на каком языке звучит: «ja» у субтитров (оригинал), «en»
        # у английского дубляжа, «ru» у русских озвучек. По нему страница
        # пишет «Озвучка EN» / «Субтитры EN» и говорит, если просили
        # английский дубляж, а у серии его нет.
        "dubs": [{"name": n, "sub": by_dub[n]["sub"], "lang": sub_lang(n),
                  "audio": dub_audio(n, by_dub[n]["sub"], source)}
                 for n in order],
        # Какая озвучка ответила. Может не совпасть с просимой в `dub`:
        # тогда просимая не отдала видео, и это запасная.
        "chosen": chosen,
        "videos": videos,
        # Субтитры отдельными файлами (если они есть): номер, подпись,
        # язык и адрес у нас — /api/subtitle/<номер>.vtt.
        "subs": subtitles.register(found_subs),
        # Что открывать первым просили: «dub» или «sub».
        "track": track,
    }


@app.get("/api/subtitle/{name}")
async def api_subtitle(name: str, who: Caller = Depends(need_any)):
    """Файл субтитров для плеера — всегда WebVTT.

    Номер выдаёт /api/videos (subtitles.register), и по нему отдаётся
    только то, что сервер сам получил от источника: адрес файла снаружи
    не приходит. Переделанный текст держится в кэше — повторное открытие
    серии в чужую сеть не ходит.
    """
    token = name[:-4] if name.endswith(".vtt") else name
    meta = subtitles.lookup(token)
    if not meta:
        raise HTTPException(status_code=404, detail="Субтитры устарели — откройте серию заново")
    text = anime.cache_get(f"subvtt:{token}")
    if text is None:
        if not within(source_limit, "t", who):
            raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
        try:
            text = await subtitles.fetch_vtt(meta)
        except Exception as exc:                   # noqa: BLE001
            raise anime.upstream_error(exc, "Файл субтитров не скачался")
        anime.cache_put(f"subvtt:{token}", text)
    # Текст — чужой (файл с сайта-источника), отдаётся с нашего адреса:
    # та же строгая политика, что у прокладки видео (PROXY_CSP ниже).
    return Response(content=text, media_type="text/vtt; charset=utf-8",
                    headers={"Content-Security-Policy": PROXY_CSP})


# ==========================================================================
# Прокладка для потоков, которым нужен Referer (см. api/hls.py)
# ==========================================================================
# Счётчик щедрый: плеер просит кусок видео каждые несколько секунд, а при
# перемотке — пачкой. Он не от нагрузки, а от бесконечного цикла.
hls_limit = security.RateLimiter(limit=900, period=60)

# Сколько потоков видео идёт через прокладку разом: у одного человека и на
# весь сайт. Каждый поток держит соединение с чужим хостингом; плееру
# хватает двух-трёх (плейлист и один-два куска впрок). Без потолка один
# скрипт открывал бы сотни медленных соединений и занимал всё, что есть у
# процесса, — у остальных видео бы встало.
HLS_PER_CALLER = 16
HLS_TOTAL = 128
# Сколько место в потолке может быть занято одним потоком. Обычно его
# освобождает сам ответ; но если человек закрыл вкладку раньше первого
# куска, освобождать бывает некому — тогда место освобождается само,
# по сроку. Иначе после шестнадцати таких закрытий видео у человека
# перестало бы открываться до перезапуска сервера.
HLS_SLOT_MAX_AGE = 30 * 60
_hls_open: dict[str, dict[int, float]] = {}
_hls_next = [0]


def _hls_busy(owner: str | None = None) -> int:
    """Сколько потоков открыто: у owner или у всех. Просроченные места
    при этом выбрасываются."""
    edge = time.time() - HLS_SLOT_MAX_AGE
    for who_ in list(_hls_open):
        slots = {k: v for k, v in _hls_open[who_].items() if v > edge}
        if slots:
            _hls_open[who_] = slots
        else:
            _hls_open.pop(who_, None)
    if owner is not None:
        return len(_hls_open.get(owner, {}))
    return sum(len(v) for v in _hls_open.values())


class _Slot:
    """Место в потолке потоков. release() можно звать сколько угодно раз."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        _hls_next[0] += 1
        self.id = _hls_next[0]
        _hls_open.setdefault(owner, {})[self.id] = time.time()

    def release(self) -> None:
        slots = _hls_open.get(self.owner)
        if slots is None:
            return
        slots.pop(self.id, None)
        if not slots:
            _hls_open.pop(self.owner, None)


def _hls_owner(who: Caller) -> str:
    if who.is_guest:
        return "g:" + security.token_fingerprint(who.token)[:16]
    return f"u:{who.user_id}"


# Ответ прокладки — чужие байты с нашего адреса. Своя политика для них
# строже общей: не исполнять ничего и не встраивать никуда (sandbox), даже
# если браузер когда-нибудь решит показать такой ответ страницей.
PROXY_CSP = "default-src 'none'; sandbox; frame-ancestors 'none'"


@app.get("/api/hls")
async def api_hls(
    u: str = Query(..., max_length=4000),
    r: str = Query("", max_length=1000),
    s: str = Query(..., max_length=64),
    who: Caller = Depends(need_any),
):
    """Плейлист или кусок видео с чужого хостинга — с нужным Referer.

    Адрес подписан сервером: браузер не может попросить сходить куда-то
    ещё. Плейлисты переписываются (каждый адрес внутри — тоже через нас),
    куски видео отдаются потоком, как пришли, но тип ответа — только из
    безопасного списка (hls.safe_media_type).
    """
    got = hls.unpack(u, r, s)
    if got is None:
        raise HTTPException(status_code=404, detail="Ссылка на видео устарела — откройте серию заново")
    if not within(hls_limit, "h", who):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    owner = _hls_owner(who)
    if _hls_busy(owner) >= HLS_PER_CALLER or _hls_busy() >= HLS_TOTAL:
        raise HTTPException(status_code=429, detail="Слишком много потоков видео разом")
    slot = _Slot(owner)
    url, ref = got
    client = hls.client(ref)
    resp = None

    async def cleanup() -> None:
        slot.release()
        if resp is not None:
            await resp.aclose()
        await client.aclose()

    try:
        resp = await client.send(client.build_request("GET", url), stream=True)
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Хостинг видео ответил {resp.status_code}")
        it = resp.aiter_bytes()
        try:
            first = await it.__anext__()
        except StopAsyncIteration:
            first = b""
        ctype = resp.headers.get("content-type", "")
        if hls.looks_like_playlist(url, ctype, first):
            buf = bytearray(first)
            async for chunk in it:
                buf += chunk
                if len(buf) > hls.MAX_PLAYLIST:
                    raise HTTPException(status_code=502, detail="Плейлист слишком большой")
            final_url = str(resp.url)
            await cleanup()
            try:
                # В отдельном потоке: переписать плейлист на 30 000 адресов
                # (потолок hls.MAX_PLAYLIST_ENTRIES) — полсекунды счёта
                # (подпись каждого адреса), и в цикле событий это полсекунды
                # стоящего сайта у всех. Обычный плейлист — 7 мс, но
                # размер задаёт чужой хостинг, не мы.
                text = await asyncio.to_thread(
                    hls.rewrite_playlist, bytes(buf).decode("utf-8", errors="replace"),
                    final_url, ref)
            except hls.PlaylistTooBig:
                raise HTTPException(status_code=502, detail="Плейлист слишком большой") from None
            return Response(text, media_type="application/vnd.apple.mpegurl",
                            headers={"Content-Security-Policy": PROXY_CSP})
    except HTTPException:
        await cleanup()
        raise
    except Exception as exc:                       # noqa: BLE001
        await cleanup()
        raise anime.upstream_error(exc, "Хостинг видео не ответил") from None

    async def body():
        try:
            if first:
                yield first
            async for chunk in it:
                yield chunk
        finally:
            await cleanup()

    headers = {"Content-Security-Policy": PROXY_CSP}
    # Длину переписываем только если хостинг отдал тело как есть: при
    # Content-Encoding (gzip, br) httpx распаковывает поток сам, и длина
    # хостинга — это длина сжатого тела, не нашего. Хостинг просят не
    # сжимать (hls.client), но слушаются не все; без длины ответ уйдёт
    # кусками (chunked) — плееру это всё равно.
    encoding = resp.headers.get("content-encoding", "").strip().lower()
    if resp.headers.get("content-length", "").isdigit() and encoding in ("", "identity"):
        headers["Content-Length"] = resp.headers["content-length"]
    # Уборка — ещё и фоновой задачей ответа: если человек закрыл вкладку
    # до первого куска, генератор body() не запустится вовсе, и его finally
    # не сработает, — а место в потолке и соединение освободить надо.
    return StreamingResponse(body(), media_type=hls.safe_media_type(ctype), headers=headers,
                             background=BackgroundTask(cleanup))


# ==========================================================================
# Справочник: описание и случайное аниме
# ==========================================================================
# Отдельный, более строгий счётчик: за этими ручками стоит чужой открытый
# каталог. Превысим его лимиты — забанят адрес сервера, и справочник
# отвалится сразу у всех.
catalog_limit = security.RateLimiter(limit=20, period=60)


def _year_param(year: str) -> int | None:
    """Год из адресной строки. Мусор («null», «2020г») — просто «не знаем»:
    год здесь подсказка для выбора среди одноимённых, а не условие."""
    year = (year or "").strip()[:4]
    return int(year) if year.isdigit() and 1900 <= int(year) <= 2200 else None


@app.get("/api/about")
async def api_about(
    title: str = Query(..., min_length=2, max_length=300),
    lang: str = Query("ru", max_length=2, description="язык САЙТА: на нём показываем описание"),
    title_en: str = Query("", max_length=300, description="латинское имя, если известно"),
    year: str = Query("", max_length=8, description="год выхода, если известен"),
    who: Caller = Depends(need_any),
):
    """Короткое описание тайтла без спойлеров.

    Источники, с которых берётся видео, описаний не дают вовсе — поэтому
    раньше под плеером стояла заглушка «описание не показываем». Теперь
    оно берётся из открытых справочников по цепочке (см. catalog.about):
    Shikimori → AniList → Jikan. Если описание пришло не на русском,
    в ответе about_lang="en" — страница подписывает его «на английском».
    """
    if not within(catalog_limit, "c", who):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    found = await catalog.about(title, "en" if lang == "en" else "ru",
                                title_en=title_en, year=_year_param(year),
                                hentai=hentai_ok(who))
    if not found:
        return {"found": False}
    if lang == "en":
        # Английская страница — английские жанры (у Shikimori свои имена,
        # catalog.pack_shiki → genres_en). Русских слов среди них не
        # оставляем ни при каком пути по справочникам.
        genres = found.get("genres_en") or found.get("genres") or []
        found = {**found, "genres": [g for g in genres if not _CYR_NAME.search(str(g))]}
    return {"found": True, "about_lang": "ru", **found}


# Поиск обложки по справочникам. Свой счётчик и списывается только за
# промах мимо кэша: на странице бывает три десятка карточек, и каждая
# может спросить, — повторные вопросы ничего не стоят и не должны
# упираться в лимит, а новые идут к трём чужим сайтам и должны.
poster_limit = security.RateLimiter(limit=30, period=60)


@app.get("/api/poster")
async def api_poster_find(
    title: str = Query("", max_length=300),
    title_en: str = Query("", max_length=300),
    year: str = Query("", max_length=8),
    key: str = Query("", max_length=200, description="запись своего списка, которой нужна обложка"),
    who: Caller = Depends(need_any),
):
    """Обложка «любой ценой»: Shikimori → AniList → Jikan (catalog.find_poster).

    Страница зовёт её, когда все свои ссылки на картинку не загрузились
    (см. A.bg в app.js). Ответ — {"poster": адрес или ""}.

    Если передан key и вошёл не гость, найденная обложка сразу ложится
    в эту запись списка — и в следующий раз карточка откроется с первой
    попытки, без похода сюда. Записываем только:
      * в свою собственную запись (UPDATE … WHERE user_id = вы);
      * адрес, который нашёл сервер, — от страницы приходит только
        название, подсунуть свою ссылку через эту ручку нельзя;
      * если название записи похоже на то, что искали: иначе, перепутав
        key, страница могла бы приклеить обложку чужого тайтла.
    Отдельная ручка для «сохрани обложку» поэтому не нужна.
    """
    title, title_en = title.strip(), title_en.strip()
    if len(title) < 2 and len(title_en) < 2:
        raise HTTPException(status_code=400, detail="Нужно название")
    when = _year_param(year)
    poster = catalog.poster_cached(title, title_en, when)
    if poster is None:
        if not within(poster_limit, "pl", who):
            raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
        poster = await catalog.find_poster(title, title_en, when)
    if poster and key and not who.is_guest:
        row = store.library_row(who.user_id, key)
        if row and max(anime.relevance(title or title_en, row["title"] or ""),
                       anime.relevance(title_en or title, row["title"] or "")) \
                >= anime.MIN_RELEVANCE:
            store.set_poster(who.user_id, key, poster)
    return {"poster": poster}


ADULT_PICK_SHARE = 0.25


# Запас готовых случайных карточек (fix-21: «в Рандоме — чтобы время
# загрузки было намного меньше»). Одна карточка — это два запроса к
# справочнику с его обязательной паузой (catalog.MIN_GAP): страница
# популярного и подробная карточка, секунда-две на каждое нажатие. Теперь
# сервер держит наготове RANDOM_POOL карточек и после каждой выдачи
# досыпает запас в фоне — по одной, теми же запросами и с той же паузой,
# так что справочник не видит ни одного лишнего обращения сверх тех, что
# всё равно случились бы по нажатию. Нажатие берёт готовое — мгновенно.
#
# В запасе только то, что дал настоящий справочник: подменённый (проверки)
# запасом не пользуется — у подмены свой порядок ответов.
RANDOM_POOL = 3
RANDOM_POOL_ECCHI = 2
_random_pool: dict[str, list] = {"": [], "ecchi": []}
_random_fill: dict[str, Any] = {}
# Что «Рандом» недавно выдавал. Страниц популярного 220 (этти — 60), и
# без памяти одна и та же карточка за пять нажатий выпадала примерно в
# одном случае из двадцати (с этти — чаще): проверка check29 «карточки
# подряд не повторяются» из-за этого иногда падала (сведение аудита
# 3 ноября 2026). Повтор досыпка теперь пропускает.
RANDOM_RECENT = 20
_random_recent: dict[str, list] = {"": [], "ecchi": []}
_REAL_RANDOM = catalog.random_anime


def _pool_on() -> bool:
    return catalog.random_anime is _REAL_RANDOM


def _random_refill(genre: str) -> None:
    """Досыпает запас в фоне, если он не полон и досыпка ещё не идёт."""
    if not _pool_on():
        return
    want = RANDOM_POOL_ECCHI if genre else RANDOM_POOL
    if len(_random_pool[genre]) >= want:
        return
    loop = asyncio.get_running_loop()
    task = _random_fill.get(genre)
    if task is not None and not task.done() and task.get_loop() is loop:
        return

    async def fill() -> None:
        misses = repeats = 0
        # repeats — свой счётчик: повтор не «справочник молчит», но и
        # бесконечно перебирать нельзя (у этти страниц всего 60).
        while len(_random_pool[genre]) < want and misses < 3 and repeats < 10:
            try:
                found = await catalog.random_anime(genre or None)
            except Exception:                      # noqa: BLE001
                found = None
            if not found:
                misses += 1
                continue
            titles = {x.get("title") for x in _random_pool[genre]}
            if found.get("title") in titles or found.get("title") in _random_recent[genre]:
                repeats += 1
                continue
            _random_pool[genre].append(found)
    _random_fill[genre] = loop.create_task(fill())


# Сколько нажатие ждёт карточку от досыпки, если запас пуст. Досыпка —
# единственный, кто ходит к справочнику за случайным: нажатие не шлёт свой
# запрос параллельно (он встал бы в общую очередь catalog._pause и ждал бы
# досыпку, а справочник получил бы лишнее), а берёт первую готовую.
RANDOM_WAIT = 12.0


async def _random_wait(genre: str, allow: bool) -> dict | None:
    """Карточка из запаса; пуст — ждём досыпку (не дольше RANDOM_WAIT)."""
    if not _pool_on():
        return None
    end = time.monotonic() + RANDOM_WAIT
    while True:
        found = _random_take(genre, allow)
        if found is not None:
            return found
        _random_refill(genre)
        task = _random_fill.get(genre)
        if time.monotonic() >= end or task is None or task.done():
            # Досыпка кончилась ничем (справочник молчит) — дальше по-старому.
            return _random_take(genre, allow)
        await asyncio.sleep(0.05)


def _random_take(genre: str, allow: bool) -> dict | None:
    """Первая карточка из запаса, которую можно показать этому человеку.
    Остальное остаётся в запасе для других (18+ — тем, у кого он включён)."""
    if not _pool_on():
        return None
    pool = _random_pool[genre]
    for i, found in enumerate(pool):
        if catalog.visible([found], allow, hentai_ok=False):
            recent = _random_recent[genre]
            recent.append(found.get("title"))
            del recent[:-RANDOM_RECENT]
            return pool.pop(i)
    return None


@app.get("/api/random")
async def api_random(who: Caller = Depends(need_any), warm: bool = Query(False)):
    """Случайное аниме из каталога — не из вашего списка.

    warm=1 — только досыпать запас (страница зовёт, когда кнопку «Рандом»
    вот-вот нажмут): ответ сразу, без карточки."""
    if warm:
        _random_refill("")
        if adult_ok(who):
            _random_refill("ecchi")
        return {"found": False, "warm": True}
    if not within(catalog_limit, "r", who):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    # Пустая страница попадается редко, но бывает. Попытки уходят и на
    # 18+: страницы случайного общие на всех и лежат в кэше целиком,
    # поэтому взрослое отсеивается здесь, на выходе, а не при складывании.
    allow = adult_ok(who)
    # С включённым 18+ рулетка иногда (примерно раз из четырёх) тянет из
    # жанра этти: в общем списке по популярности их почти нет, и без этого
    # настройка на «Рандом» не влияла бы никак. Выключено — никогда.
    #
    # Хентай рулетка не выбирает НИКОГДА, даже у того, кому он включён:
    # «реши за меня» — не то место, где он нужен. Отсюда hentai_ok=False
    # в обеих проверках (страницы случайного и так спрашиваются с цензурой).
    # Досыпаем запас ПОСЛЕ своего ответа: иначе досыпка встала бы в очередь
    # к справочнику (catalog._pause) впереди этого нажатия.
    if allow and random.random() < ADULT_PICK_SHARE:
        found = await _random_wait("ecchi", True)
        if found is None and not _pool_on():
            found = await catalog.random_anime("ecchi")
        _random_refill("ecchi")
        if found and catalog.visible([found], True, hentai_ok=False):
            _random_refill("")
            return {"found": True, **found}
    found = await _random_wait("", allow)
    if found is not None:
        _random_refill("")
        return {"found": True, **found}
    for _ in range(5):
        found = await catalog.random_anime()
        if found and catalog.visible([found], allow, hentai_ok=False):
            _random_refill("")
            return {"found": True, **found}
    raise HTTPException(status_code=502,
                        detail="Каталог сейчас не отвечает. Попробуйте ещё раз.")


# ==========================================================================
# Поиск по франшизам
# ==========================================================================
# Что тут происходит и почему поиск устроен в два шага.
#
# Раньше строка поиска била прямо в сайт с видео, и на «наруто» приходило
# то, что этот сайт считает похожим: у RU-A — «Наруто Ураганные
# хроники» и «Боруто», без самого «Наруто»; у RU-C — двадцать одна
# строка вперемешку, где второй сезон стоит после фильма про Боруто.
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
    lang: str = Query("ru", max_length=2, description="язык САЙТА (не аниме)"),
    who: Caller = Depends(need_any),
):
    """Поиск по справочнику: одна карточка на франшизу.

    Язык здесь ни на что не влияет: справочник отдаёт оба названия сразу,
    и какое показать, решает страница. Параметр принимается, чтобы не
    отвечать отказом на честный запрос — остальные ручки поиска язык
    ждут, и слать его во все разом проще, чем помнить исключение.
    """
    if not within(catalog_limit, "f", who):
        raise HTTPException(status_code=429, detail="Слишком частый поиск, подождите минуту")
    # «этти», «ecchi», «18+» — это жанр, а не название: список самого
    # популярного в жанре. Только тому, кто сам включил 18+; остальным —
    # пустая выдача с подсказкой, где включить (гостю — что нельзя).
    # «хентай», «hentai», «хент» — то же, но нужны оба переключателя:
    # подсказка hentai_off говорит, каких не хватает.
    genre = catalog.genre_query(q)
    adult, hentai = adult_ok(who), hentai_ok(who)
    if genre:
        allowed = hentai if genre == "hentai" else adult
        if not allowed:
            hint = "guest" if who.is_guest else ("hentai_off" if genre == "hentai" else "adult_off")
            return {"items": [], "catalog": True, "genre": genre, "hint": hint,
                    "adult": adult}
        cards = await catalog.genre_cards(genre)
        if cards is None:
            raise HTTPException(status_code=502, detail="Справочник сейчас не отвечает")
        if genre != "hentai":
            # Список этти — без хентая, даже у того, кому он включён: просили этти.
            return {"items": catalog.cards_for(cards, True, False),
                    "catalog": True, "genre": genre}
        # Хентай — только тот, что есть у источников на языке человека
        # (см. rx_cards). Страница пишет об этом строку под выдачей.
        cards, note = await rx_cards(cards, who)
        return {"items": catalog.cards_for(cards, True, True),
                "catalog": True, "genre": genre, "watchable": True, **note}
    cards = await catalog.search_franchises(q, hentai)
    if cards is None:
        # Справочник молчит. Не ошибка: сайт умеет искать и без него,
        # прямо у источников видео — хуже, но работает. Развилку делает
        # страница, ей и говорим, что случилось.
        return {"items": [], "catalog": False}
    if not hentai:
        # 18+ и хентай отсеиваются здесь, а не в кэше: кэш один на всех.
        return {"items": catalog.cards_for(cards, adult, False), "catalog": True}
    # У кого хентай включён — rx только тот, что можно посмотреть. У кого
    # нет — источники не спрашиваются вовсе (ветка выше).
    cards, note = await rx_cards(cards, who)
    return {"items": catalog.cards_for(cards, adult, True), "catalog": True, **note}


@app.get("/api/franchise")
async def api_franchise(
    id: str = Query(..., min_length=1, max_length=120),
    who: Caller = Depends(need_any),
):
    """Все части франшизы в порядке просмотра (см. catalog.order_parts)."""
    if not within(catalog_limit, "p", who):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    parts = await catalog.franchise_parts(id, adult_ok(who), hentai_ok(who),
                                          rx_parts_filter(who))
    if parts is None:
        raise HTTPException(status_code=502, detail="Справочник сейчас не отвечает")
    return {"id": id, "items": parts}


@app.get("/api/related")
async def api_related(
    title: str = Query(..., min_length=2, max_length=300),
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
    if not within(catalog_limit, "rel", who):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    parts = await catalog.related_parts(title, adult_ok(who), hentai_ok(who),
                                        rx_parts_filter(who))
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
# что тайтл открывается. У RU-B «Наруто: Ураганные хроники» лежат
# под ровно тем же названием, что в справочнике, — совпадение единица, —
# а список серий не отдаётся вовсе: разборщик спотыкается на сдвоенной
# серии «57-58» и падает. Резолв радостно возвращал этот тайтл, человек
# нажимал вторую часть «Наруто» и попадал в плеер с надписью «серии не
# загрузились». При этом ровно те же «Ураганные хроники» прекрасно
# отдаются у RU-A и RU-C — их просто никто не спрашивал.
#
# Лишним запрос не назвать: страница просмотра просит серии первым же
# делом, и ответ уже лежит в кэше. Потолок в три попытки держит худший
# случай — когда не открывается ни у кого — в разумных пределах.
PROBE_LIMIT = 3

# Какую долю обещанных справочником серий источник должен выложить,
# чтобы считаться подходящим.
#
# Без этой проверки «Ван-Пис» открывался на RU-A, у которой выложено
# семь серий из тысячи ста семидесяти четырёх. Формально всё правильно:
# название совпало точно, серии отдались, ошибки нет. А человек получал
# «серия 1 из 7» у сериала, который идёт двадцать шестой год. У RU-B
# и RU-C он лежит целиком — их просто не спрашивали, потому что
# перебор останавливался на первом же работающем.
#
# Планка низкая намеренно. Источники отстают от справочника на серию-две
# постоянно, у «Блича» разница в четырнадцать серий из трёхсот
# шестидесяти шести, и придираться к этому не за что. Отсеять нужно не
# отставание, а огрызок.
ENOUGH_SHARE = 0.6


# Сколько других имён тайтла (синонимов из справочника) пробовать сверх
# основного названия и латинского. Каждое имя — поиск у каждого источника,
# так что потолок нужен: у «Ван-Пис» синонимов девять.
ALT_MAX = 3

# Сколько имён спрашивать у узких источников в запасном круге резолва
# (см. api_resolve): каждое имя — поиск у четырёх медленных сайтов.
SPARE_NAMES = 3
_CYR_NAME = re.compile(r"[а-яё]", re.I)


def split_alt(raw: str) -> list[str]:
    """Другие имена из запроса: через «|», каждое в одну строку."""
    out: list[str] = []
    for n in (raw or "").split("|"):
        n = " ".join(n.split())[:200]
        if len(n) >= 2 and n not in out:
            out.append(n)
    return out[:8]


async def other_names(title: str, title_en: str, alt: str, year: int | None,
                      hentai: bool = False) -> list[str]:
    """Под какими ещё именами искать тайтл, по порядку: русские синонимы и
    лицензионное название, потом латинское имя, потом латинские синонимы.

    Синонимы присылает страница (они есть у частей из справочника), а если
    не прислала — спрашиваем справочник сами (catalog.alt_names: сначала
    память, потом один поиск). Справочник молчит — обходимся без них.
    hentai — у кого он включён, справочник спрашивается без цензуры:
    иначе синонимов хентай-тайтла он не знает."""
    names = split_alt(alt)
    if not names:
        try:
            names = await asyncio.wait_for(
                catalog.alt_names(title, title_en, year, hentai), 15)
        except Exception as exc:                   # noqa: BLE001
            log.info("синонимы не нашлись: %s", type(exc).__name__)
            names = []
    seen = {anime.normalize_title(title)}
    ru: list[str] = []
    latin: list[str] = []
    for n in names:
        key = anime.normalize_title(n)
        if not key or key in seen:
            continue
        seen.add(key)
        (ru if _CYR_NAME.search(n) else latin).append(n)
    out = ru[:ALT_MAX]
    en = title_en.strip()
    if en and anime.normalize_title(en) not in {anime.normalize_title(title)} | {
            anime.normalize_title(n) for n in out}:
        out.append(en)
    en_key = anime.normalize_title(en)
    out += [n for n in latin if anime.normalize_title(n) != en_key][:2]
    return out


def part_fit(row: dict, year: int | None, episodes: int) -> float:
    """Поправка к совпадению названий: тот ли это год и то ли число серий.

    Нужна там, где названия неоднозначны: у «Старшей школы DxD» четыре
    сезона, и у источника они могут лежать под одинаковыми именами.
    Совпавший год чуть поднимает кандидата, год мимо на два и больше —
    заметно опускает (ниже планки точного совпадения: это уже, скорее
    всего, другая часть или ремейк). Число серий — только подсказка."""
    adj = 0.0
    ry = row.get("year")
    if year and isinstance(ry, int):
        diff = abs(ry - year)
        adj += 0.04 if diff == 0 else (0.0 if diff == 1 else -0.12)
    re_ = row.get("episodes_total")
    if episodes and isinstance(re_, int) and re_ > 0:
        if re_ == episodes:
            adj += 0.03
        elif abs(re_ - episodes) > max(2, episodes // 2):
            adj -= 0.05
    return adj


# Готовые ответы резолва. Резолв — самое долгое место на пути «нажал часть
# → смотрю»: поиски у четырёх источников по двум-трём именам и до трёх
# списков серий. А спрашивают одно и то же часто: окно частей заранее
# ищет «Начни отсюда» (index.js, prefetch), человек нажимает её через
# пару секунд; «Продолжить» с другого устройства; тот же тайтл у друга.
# Ответ «где лежит» живёт долго — источники не переезжают каждый час, —
# но не вечно: RESOLVE_TTL. Помним только найденное: «нигде нет» и сбои —
# нет (через минуту может найтись).
#
# Одинаковые резолвы, идущие разом (заранее и по нажатию), склеиваются в
# один (_resolve_inflight): нажатие ждёт уже начатый поиск, а не начинает
# второй — источники не получают двойной поток.
# Память — общий кэш ответов источников (anime.cache_*): то, что знают
# источники, и сбрасывается вместе.
RESOLVE_TTL = 30 * 60

# Хентай (fix-21): «чтобы реально работал, но не обязательно на каком-то
# языке — просто чтобы был». Не нашёлся на языке человека — ищем на
# остальных, по порядку. Только для хентая (rx=1 и hentai_ok): обычные
# тайтлы держат строгое правило языка (fix-20).
RX_MODES = ("ru", "endub", "ensub")
RX_NOWHERE = ("Этот хентай сейчас не отдаёт ни один источник ни на одном языке · "
              "No source has this right now, in any language")


@app.get("/api/resolve")
async def api_resolve(
    title: str = Query(..., min_length=2, max_length=300),
    title_en: str = Query("", max_length=300),
    source: str = Query("", max_length=40),
    episodes: int = Query(0, ge=0, le=10000,
                          description="сколько серий обещает справочник"),
    lang: str = Query("ru", max_length=5, description=ANIME_LANG_HELP),
    alt: str = Query("", max_length=1200, description="другие имена тайтла через «|»"),
    year: str = Query("", max_length=8, description="год выхода части"),
    only: bool = Query(False, description="искать только у source, без перебора"),
    rx: bool = Query(False, description="это хентай: можно искать на любом языке"),
    ep: int = Query(0, ge=0, le=100000, description="с какой серии откроют (прогрев)"),
    warm: bool = Query(True, description="прогреть серию после резолва"),
    who: Caller = Depends(need_any),
):
    """Резолв с памятью (resolve_cached), а для хентая — на любом языке.

    alang в ответе — на каком языке аниме нашлось (ru / endub / ensub);
    other_lang — не на языке человека (страница пишет «Есть только на …»
    и открывает плеер на этом языке, не меняя настройку)."""
    anime.need_sources()                       # личный режим без источников — 501
    mode = anime.anime_mode(lang)

    def warmed(out: dict, code: str) -> dict:
        # Прогрев — в фоне: ответ резолва его не ждёт.
        if warm and not only and out.get("source") in anime.SOURCES:
            spawn_warm(lambda: warm_videos(out["source"], str(out["key"]),
                                           str(out.get("title") or title), code, ep, who))
        return out
    # Источники хентая (anime.RX_ORDER): японская речь и
    # английский текст, то есть «English — субтитры». Спрашиваются только
    # о хентае (rx=1) и только у того, кому он включён.
    rx_ok = rx and hentai_ok(who) and not anime.DEMO
    rx_tried = False

    def rx_answer(out: dict) -> dict:
        return warmed({**out, "alang": RX_ALANG, "other_lang": mode != RX_ALANG}, RX_ALANG)
    if only and anime.rx_only(source):
        # Переключение источника на странице просмотра — на источник хентая.
        if not rx_ok:
            raise HTTPException(status_code=400, detail="Неизвестный источник")
        return rx_answer(await resolve_rx_cached([source], title, title_en, episodes,
                                                 alt, year, who))
    if rx_ok and anime.anime_lang(mode) == "en":
        # Английский язык аниме — сначала источник хентая: он только про хентай.
        rx_tried = True
        try:
            return rx_answer(await resolve_rx_cached(anime.RX_ORDER, title, title_en,
                                                     episodes, alt, year, who))
        except HTTPException:
            # Источник хентая молчит, не знает тайтла или кончился лимит —
            # дальше обычные источники, как было до него.
            pass
    try:
        out = await resolve_cached(title, title_en, source, episodes, mode, alt, year, only, who)
        return warmed({**out, "alang": mode}, mode)
    except HTTPException as exc:
        if not (rx and not only and exc.status_code == 404 and hentai_ok(who)):
            raise
        last = exc
    # Сначала язык, на котором тайтл нашла проверка поиска (rx_check).
    seen = _rx_where.get(rx_key({"title": title, "title_en": title_en,
                                 "year": _year_param(year)}, RX_ANY))
    known = seen[1] if seen and seen[0] > time.time() else ""
    modes = sorted(RX_MODES, key=lambda m: 0 if known and anime.anime_lang(m) == known else 1)
    for other in modes:
        if other == mode:
            continue
        try:
            out = await resolve_cached(title, title_en, "", episodes, other, alt, year,
                                       False, who)
        except HTTPException as exc:
            if exc.status_code != 404:
                last = exc
            continue
        return warmed({**out, "alang": other, "other_lang": True}, other)
    # Русский язык аниме, и ни по-русски, ни у английских сайтов нет —
    # последним источник хентая. Именно последним: человек выбрал русский, и русская
    # озвучка (или английский дубляж) у обычного источника ему ближе, чем
    # английский текст.
    if rx_ok and not rx_tried:
        try:
            return rx_answer(await resolve_rx_cached(anime.RX_ORDER, title, title_en,
                                                     episodes, alt, year, who))
        except HTTPException as exc:
            if exc.status_code != 404:
                last = exc
    if last.status_code == 404:
        raise HTTPException(status_code=404, detail=RX_NOWHERE)
    raise last


# На каком языке аниме играют источники хентая: японская речь,
# английский текст.
RX_ALANG = "ensub"


async def resolve_rx_cached(sources: list[str], title: str, title_en: str, episodes: int,
                            alt: str, year: str, who: Caller) -> dict:
    """resolve_rx с той же памятью и склейкой, что у обычного резолва."""
    key = "resolve:rx:" + json.dumps([list(sources), title.strip(), title_en.strip(), episodes,
                                      alt.strip(), year.strip()], ensure_ascii=False)
    hit = anime.cache_get(key)
    if hit is not None:
        return dict(hit)
    out = await shared(key, lambda: resolve_rx(sources, title, title_en, episodes, alt, year,
                                               who))
    anime.cache_put(key, dict(out), RESOLVE_TTL)
    return dict(out)


async def resolve_rx(sources: list[str], title: str, title_en: str, episodes: int,
                     alt: str, year: str, who: Caller) -> dict:
    """Ищет хентай у источников хентая (anime.RX_ORDER).

    Проще обычного резолва и строже его:
      * сначала — показывал ли этот тайтл наш справочник чистым
        (catalog.rx_cleared); нет — «нигде нет», к источнику не идём;
      * совпадение только точное (GOOD_ENOUGH), похожие не берутся вовсе:
        «похожий хентай» — это другой хентай;
      * имена — латинское, основное и до ALT_MAX синонимов.
    Ответ — того же вида, что у resolve_in."""
    sources = [s for s in sources if anime.rx_only(s) and s in anime.SOURCES]
    when = _year_param(year)
    if not sources or not await catalog.rx_cleared(title, title_en, when):
        raise HTTPException(status_code=404, detail=RX_NOWHERE)
    names = [n for n in dict.fromkeys([title_en.strip(), title.strip()]
                                      + split_alt(alt)[:ALT_MAX]) if len(n) >= 2]
    if not within(search_limit, "rs", who, cost=uncached_lookups(sources, names)):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    started = time.monotonic()
    answered = False
    for src in sources:
        for q in names:
            if budget_left(started) <= 0:
                raise HTTPException(status_code=504, detail=anime.TIMEOUT_DETAIL)
            with anime.budget(budget_left(started)):
                rows = await try_source(src, q)
            if rows is None:
                continue
            answered = True
            for row in rows:
                score = max(anime.relevance(t, row["title"]) for t in names) \
                    + part_fit(row, when, episodes)
                if score < GOOD_ENOUGH:
                    continue
                try:
                    with anime.budget(budget_left(started)):
                        count = len(await anime.find_episodes(src, row["key"], row["title"]))
                except HTTPException:
                    count = 0
                if count:
                    return {"source": src, "key": row["key"], "title": row["title"],
                            "poster": row.get("poster") or "", "year": row.get("year"),
                            "genres": row.get("genres") or "",
                            "episodes_total": count, "match": round(min(score, 1.0), 3),
                            "exact": True}
    if not answered:
        raise HTTPException(status_code=502, detail="Источник хентая сейчас не отвечает")
    raise HTTPException(status_code=404, detail=RX_NOWHERE)


async def resolve_cached(title: str, title_en: str, source: str, episodes: int, mode: str,
                         alt: str, year: str, only: bool, who: Caller) -> dict:
    key = "resolve:" + json.dumps([title.strip(), title_en.strip(), source, episodes, mode,
                                   alt.strip(), year.strip(), bool(only), hentai_ok(who)],
                                  ensure_ascii=False)
    hit = anime.cache_get(key)
    if hit is not None:
        return dict(hit)
    # shared: ушёл тот, кто начал (закрыл окно, не дождался), — поиск
    # доделывается и ложится в память для следующего.
    out = await shared(key, lambda: resolve_in(title=title, title_en=title_en, source=source,
                                               episodes=episodes, lang=mode, alt=alt,
                                               year=year, only=only, who=who))
    anime.cache_put(key, dict(out), RESOLVE_TTL)
    return dict(out)


async def resolve_in(
    title: str = Query(..., min_length=2, max_length=300),
    title_en: str = Query("", max_length=300),
    source: str = Query("", max_length=40),
    episodes: int = Query(0, ge=0, le=10000,
                          description="сколько серий обещает справочник"),
    lang: str = Query("ru", max_length=5, description=ANIME_LANG_HELP),
    alt: str = Query("", max_length=1200, description="другие имена тайтла через «|»"),
    year: str = Query("", max_length=8, description="год выхода части"),
    only: bool = Query(False, description="искать только у source, без перебора"),
    who: Caller = Depends(need_any),
):
    """Ищет выбранную часть у источников видео.

    Справочник даёт название, но не даёт ссылки на серии — их знают
    только источники. Здесь название превращается в пару «источник +
    номер тайтла», с которой открывается страница просмотра.

    Порядок перебора имён:
      1. русское название у всех источников;
      2. русские синонимы и лицензионное название («Старшая школа DxD»
         у RU-D, RU-B и RU-A лежит как «Демоны старшей школы»);
      3. латинское имя и латинские синонимы — там, где перевод разошёлся
         совсем: «Судьба/Ночь схватки» против «Fate/stay night».
    Синонимы спрашиваются только если первый шаг не дал точного живого
    совпадения: у большинства тайтлов название совпадает и так.

    only=1 — для переключения источника на странице просмотра: искать
    только там, куда переключают, но по тем же правилам (синонимы, сезон,
    год, проверка серий).
    """
    # Проверка и подстановка — как в поиске (см. pick_source). Источник
    # обязан говорить на языке аниме (не сайта): английские меню не значат
    # английскую озвучку.
    mode = anime.anime_mode(lang)
    lang = anime.anime_lang(mode)
    source = pick_source(source, mode)
    order = [source] if only else [source] + [s for s in fallback_for(mode) if s != source]
    # Английский вариант, который просили: «dub» или «sub». Источник
    # говорит в поиске, что у тайтла есть (поле langs); нет нужного —
    # тайтл с этого источника не годится.
    want_track = anime.anime_track(mode) if lang == "en" and not anime.DEMO else ""
    # Цена — сколько поисков пойдёт мимо кэша, а не один: см. uncached_lookups.
    # Синонимы досписываются отдельно, когда до них дойдёт очередь.
    if not within(search_limit, "rs", who,
                  cost=uncached_lookups(order, (title, title_en))):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")

    when = _year_param(year)
    # Длину проверяет сам разбор запроса, но два пробела её проходят, а
    # после обрезки не остаётся ничего. Дальше по коду пустой список
    # названий уронил бы max() на пустой последовательности — то есть
    # внутренней ошибкой сервера в ответ на кривой запрос.
    first = title.strip() or title_en.strip()
    # Англоязычный источник знает тайтлы под латинскими именами: искать у
    # него «Магическую битву» — потраченный поход, нужно «Jujutsu Kaisen».
    # При английском языке аниме начинаем с латинского.
    if lang == "en" and title_en.strip():
        first = title_en.strip()
    if not first:
        raise HTTPException(status_code=400, detail="Пустое название")
    prepaid = {first, title.strip(), title_en.strip()}    # уже списаны с лимита выше
    names = [first]               # с чем сравнивать найденное (растёт по ходу)
    queue = [first]               # что ещё спросить у источников
    # Начали с латинского — русское название сразу следом (оно уже
    # оплачено, см. prepaid). Иначе оно не попало бы никуда: синонимы
    # ниже строятся от него, но само оно в них не входит, а справочник
    # может и не ответить.
    if first != title.strip() and title.strip():
        names.append(title.strip())
        queue.append(title.strip())
    widened = False               # синонимы уже добавлены
    limited = False               # на синонимы не хватило лимита

    strong: list[tuple[float, str, dict]] = []     # точное совпадение
    weak: list[tuple[float, str, dict]] = []       # похоже, но не то же
    best_seen: dict[tuple[str, str], float] = {}
    probed: set[tuple[str, str]] = set()
    probes = [0]                                   # сколько раз ходили за сериями
    answered = False
    # Общий срок на весь резолв: поиски по нескольким названиям у четырёх
    # источников и до трёх списков серий. Каждый шаг ограничен своим
    # сроком, но вместе они легко выходили за 120 секунд nginx.
    started = time.monotonic()
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

    async def count_of(name: str, row: dict) -> int:
        try:
            with anime.budget(budget_left(started)):
                return len(await anime.find_episodes(name, row["key"], row["title"]))
        except HTTPException:
            return 0
        except Exception as exc:               # noqa: BLE001
            log.info("серии не отдались: %s: %s", name, type(exc).__name__)
            return 0

    async def probe(pool: list) -> None:
        """Проверяет отобранных, пока не найдёт достаточно полного.

        Первого (лучшего по совпадению) — одного: обычно он и подходит, и
        к остальным источникам за сериями ходить незачем. Не подошёл —
        остальных (не больше PROBE_LIMIT на весь резолв) разом, а не по
        очереди (fix-21): раньше «Ван-Пис» с огрызком на первом источнике
        ждал три списка серий подряд."""
        nonlocal live
        pool.sort(key=lambda c: -c[0])
        size = 1
        while pool and probes[0] < PROBE_LIMIT:
            batch = []
            while pool and len(batch) < size:
                score, name, row = pool.pop(0)
                mark = (name, str(row["key"]))
                if mark in probed:
                    continue
                probed.add(mark)
                batch.append((score, name, row))
            size = max(1, PROBE_LIMIT - probes[0] - len(batch))
            if not batch:
                continue
            probes[0] += len(batch)
            counts = await asyncio.gather(*[count_of(n, r) for _, n, r in batch])
            for (score, name, row), count in zip(batch, counts):
                if not count:
                    continue
                if live is None or count > live[3] or (count == live[3] and score > live[0]):
                    live = (score, name, row, count)
            if enough(live):
                return

    async def search_all(names_: list[str], q: str) -> dict:
        """Один поиск у каждого источника — все разом, а не по очереди.

        Раньше источники спрашивались друг за другом: четыре поиска по
        ~секунде — четыре секунды до первой серии (fix-21, «намного
        быстрее»). Разом — столько, сколько отвечает самый медленный, а
        каждому источнику по-прежнему ровно один запрос: вежливость та
        же, и цена для лимита та же (uncached_lookups и так считала всех).
        Разбор ответов — в прежнем порядке, по order."""
        async def one(name: str):
            with anime.budget(budget_left(started)):
                return await try_source(name, q)
        got = await asyncio.gather(*[one(n) for n in names_], return_exceptions=True)
        for r in got:
            # try_source глотает сбои источника сам; наверх летит только то,
            # о чём надо сказать человеку (нет библиотеки, 503) — как и раньше.
            if isinstance(r, BaseException):
                raise r
        return dict(zip(names_, got))

    def weigh(name: str, rows: list) -> None:
        for row in rows:
            if want_track and row.get("langs") and want_track not in row["langs"]:
                continue
            mark = (name, str(row["key"]))
            score = max(anime.relevance(t, row["title"]) for t in names) \
                + part_fit(row, when, episodes)
            if score <= best_seen.get(mark, -1.0):  # тот же тайтл со второго названия
                continue
            best_seen[mark] = score
            if score >= GOOD_ENOUGH:
                strong.append((score, name, row))
            elif score >= anime.MIN_RELEVANCE:
                weak.append((score, name, row))

    while queue:
        q = queue.pop(0)
        if q not in prepaid:
            # Каждое следующее имя — новые походы к источникам: списываем
            # их с лимита, когда до них дошло, а не заранее.
            if not within(search_limit, "rs", who, cost=uncached_lookups(order, [q])):
                limited = True
                break
        if budget_left(started) <= 0:
            break
        found = await search_all(order, q)
        for name in order:
            rows = found.get(name)
            if rows is None:
                continue
            answered = True
            weigh(name, rows)
        # Точное совпадение — проверяем, отдаются ли серии (лучшее первым).
        if strong:
            await probe(strong)
        if enough(live) or budget_left(started) <= 0:
            break
        if not queue and not widened:
            widened = True
            base = title.strip() or first
            more = await other_names(base, title_en if base != title_en.strip() else "",
                                     alt, when, hentai_ok(who))
            more = [n for n in more if anime.normalize_title(n) not in
                    {anime.normalize_title(x) for x in names}]
            names += more
            queue += more
            # Уже найденное могло совпасть с синонимом лучше, чем с
            # названием: пересчитываем по кэшу, в сеть это не ходит.
            for name in order:
                cached = anime.cache_get(f"search:{name}:{first.lower()}")
                if cached:
                    weigh(name, cached)

    # Точных не нашлось, все оказались пустыми или слишком куцыми —
    # смотрим похожие.
    if not enough(live):
        await probe(weak)

    # Запасной круг: узкие источники — те, что есть в SOURCES, но не в
    # FALLBACK_ORDER (в личной версии их было четыре: RU-E…RU-H).
    #
    # В обычный перебор они не входят — медленные и отвечают через раз.
    # Но тайтл, которого нет ни у одного основного, раньше получал «нет
    # источников», хотя у RU-E он мог лежать целиком: найти его можно
    # было, только если догадаться открыть меню источников на странице
    # просмотра и перебрать их руками. Теперь сайт делает это сам — и
    # только когда основные ничего не дали, так что обычный тайтл не ждёт
    # ни секунды лишней.
    #
    # Правила те же: те же имена (с синонимами), те же планки совпадения,
    # та же проверка серий и общий срок. Лимит обращений — тоже общий:
    # имён берём не больше трёх (русское название и русские синонимы идут
    # первыми — узкие источники русские), а если и на них не хватает,
    # спрашиваем одно основное название. Не хватает и на него — честное
    # «подождите минуту», а не «нигде нет»: через минуту найдётся.
    if live is None and not only and not limited and budget_left(started) > 0:
        spare = [s for s in anime.sources_for(mode) if s not in order]
        asks = names[:SPARE_NAMES]
        if spare and not within(search_limit, "rs", who,
                                cost=uncached_lookups(spare, asks)):
            asks = names[:1]
            if not within(search_limit, "rs", who, cost=uncached_lookups(spare, asks)):
                spare, limited = [], True
        if spare:
            # Свой запас проверок серий: основные могли истратить общий
            # на пустые тайтлы, а узким тоже надо дать открыться.
            probes[0] = 0
            strong.clear()
            weak.clear()
            for q in asks:
                for name in spare:
                    if budget_left(started) <= 0:
                        break
                    with anime.budget(budget_left(started)):
                        rows = await try_source(name, q)
                    if rows is None:
                        continue
                    answered = True
                    weigh(name, rows)
                    if strong:
                        await probe(strong)
                        if enough(live):
                            break
                if enough(live) or budget_left(started) <= 0:
                    break
            if not enough(live):
                await probe(weak)

    if live is None:
        if not answered:
            raise HTTPException(
                status_code=502,
                detail="Ни один источник сейчас не отвечает. Попробуйте через несколько минут.",
            )
        if limited:
            raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
        # Кто-то ответил, но общий срок вышел раньше, чем дошли до конца:
        # честнее сказать «не успели», чем «нигде нет».
        if budget_left(started) <= 0:
            raise HTTPException(status_code=504, detail=anime.TIMEOUT_DETAIL)
        if lang == "en" and not anime.DEMO:
            raise HTTPException(status_code=404, detail=NO_ENGLISH)
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
        "match": round(min(score, 1.0), 3),
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
# с текстом поверх многие предпочитают любому дубляжу, и до сих пор сайт
# этого не умел совсем: источник по умолчанию RU-A отдаёт ровно одну
# свою озвучку и субтитров не даёт никогда. То есть человек, открывший
# любой тайтл, субтитров не видел в принципе — хотя рядом, у RU-B,
# они лежат почти у всего.
#
# Живая проверка по восьми тайтлам:
#
#   RU-A   ни у одного        (одна своя озвучка, и всё)
#   RU-B   у семи из восьми   «Субтитры», «Субтитры Luna»,
#                             «Субтитры Meduza», «Субтитры Romashka»…
#   RU-C   у двух из восьми
#
# Отсюда правило: субтитры ищутся не у текущего источника, а у всех по
# очереди. Это дорого — на каждый источник уходит поиск, список серий и
# список плееров, — поэтому идёт только по нажатию и только один раз:
# ответы источников кэшируются.

# Сколько источников обходим в поисках субтитров.
SUB_PROBES = 3

# Где искать оригинальную дорожку с текстом, и в каком порядке.
#
# Первым стоит ставить источник, у которого есть вариант «Оригинал
# (+субтитры)» — японская дорожка, поверх которой идёт текст (в личной
# версии это RU-E: проверено на пяти тайтлах, нашёлся у четырёх). Русские
# аниме-сайты дают другое — чужую озвучку поверх японской, а отдельные
# субтитры только у части тайтлов. Такой источник может и не участвовать в
# обычном переборе (медленный, не всегда про аниме), но за субтитрами идти
# надо к нему. Здесь пусто: источников в публичной версии нет.
SUB_ORDER: list[str] = []

# То же самое при английском языке аниме. Английский текст — только у
# английских источников: русский источник с «Оригиналом (+субтитры)» здесь
# был, но среди его файлов чаще только русский текст, и человек получал
# русские субтитры. Теперь нет.
SUB_ORDER_EN = anime.EN_ORDER


def is_subs(name: str) -> bool:
    """Это субтитры, а не озвучка."""
    return is_sub_track(name)


@app.get("/api/subs")
async def api_subs(
    title: str = Query(..., min_length=2, max_length=300),
    title_en: str = Query("", max_length=300),
    ordinal: int = Query(1, ge=0, le=100000),
    skip: str = Query("", max_length=40, description="источник, где уже смотрели"),
    lang: str = Query("ru", max_length=5, description=ANIME_LANG_HELP),
    alt: str = Query("", max_length=1200, description="другие имена тайтла через «|»"),
    who: Caller = Depends(need_any),
):
    """Ищет вариант с субтитрами у любого источника.

    Отвечает «где именно»: источник, номер тайтла у него и название
    варианта. Открыть его дальше умеет обычная страница просмотра.

    found=false — это не ошибка. Субтитров к этой серии может не быть ни
    у кого, и сказать об этом прямо честнее, чем показать пустоту.
    """
    anime.need_sources()                       # личный режим без источников — 501
    mode = anime.anime_mode(lang)
    lang = anime.anime_lang(mode)
    names = [t for t in (title.strip(), title_en.strip()) if t]
    if not names:
        raise HTTPException(status_code=400, detail="Пустое название")
    # Другие имена (синонимы) — только если по основным у источника не
    # нашлось ничего. Узнаём их один раз на весь запрос.
    extra: list[str] | None = None
    limited = False

    # Тот источник, у которого субтитров точно нет, второй раз не смотрим.
    wanted = list(anime.SOURCES) if anime.DEMO else (
        SUB_ORDER_EN if lang == "en" else SUB_ORDER)
    # Только те, что есть в меню этого языка: найденное у источника не из
    # меню страница открыть не сможет — она сочтёт его чужим и уведёт
    # обратно (см. relocate в watch.js).
    allowed = anime.menu_sources(mode)
    order = [s for s in wanted if s != skip and s in allowed]
    tried: list[str] = []
    # Цена — сколько поисков пойдёт мимо кэша, а не один: см. uncached_lookups.
    # Стоит после разбора названий, потому что от них и зависит.
    if not within(search_limit, "sub", who,
                  cost=uncached_lookups(order[:SUB_PROBES], names)):
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")
    # Общий срок: на каждый источник уходят поиск, список серий и список
    # плееров, а источников три.
    started = time.monotonic()

    for source in order[:SUB_PROBES]:
        if budget_left(started) <= 0:
            break
        tried.append(source)
        # 1. Есть ли тайтл у этого источника.
        #
        # У англоязычного сайта названия латинские: искать «Магическую
        # битву» там бесполезно, нужно «Jujutsu Kaisen». Поэтому для него
        # порядок названий переворачиваем — сначала латинское.
        ask = list(reversed(names)) if anime.source_lang(source) == "en" else names
        best = None

        async def look(queries: list[str], compare: list[str]):
            found = None
            for q in queries:
                with anime.budget(budget_left(started)):
                    rows = await try_source(source, q)
                if not rows:
                    continue
                for row in rows:
                    score = max(anime.relevance(t, row["title"]) for t in compare)
                    if score >= GOOD_ENOUGH and (found is None or score > found[0]):
                        found = (score, row)
                if found:
                    break
            return found

        best = await look(ask, ask)
        if best is None and not limited and budget_left(started) > 0:
            if extra is None:
                extra = [n for n in await other_names(names[0], title_en, alt, None,
                                                      hentai_ok(who))
                         if n not in names]
            if extra and within(search_limit, "sub", who,
                                cost=uncached_lookups([source], extra)):
                best = await look(extra, names + extra)
            elif extra:
                limited = True
        if best is None:
            continue

        row = best[1]
        # 2. Есть ли у него эта серия.
        try:
            with anime.budget(budget_left(started)):
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

        # Только та серия, которую просили. Раньше, если её у источника
        # не было, брались субтитры ПЕРВОЙ серии и ответ был found=true:
        # страница открывала другой источник, а там — «такой серии нет»
        # (или, хуже, первая серия вместо десятой). Нет серии — нет и
        # субтитров к ней у этого источника; идём к следующему.
        episode = next((e for e in episodes if same(e)), None)
        if episode is None:
            continue

        # 3. Есть ли среди вариантов субтитры.
        try:
            with anime.budget(budget_left(started)):
                players = await anime.call(episode.a_get_sources(), source)
        except Exception as exc:                   # noqa: BLE001
            log.info("субтитры: плееры не отдались у %s: %s", source, type(exc).__name__)
            continue

        for player in players:
            name, sub = dub_name(player, source)
            if not sub:
                continue
            # Английский язык аниме — только английский текст. Русские
            # субтитры человеку, просившему английские, не находка.
            if lang == "en" and not anime.DEMO and sub_lang(name) != "en":
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
    title: str = Query(..., min_length=2, max_length=300),
    title_en: str = Query("", max_length=300),
    lang: str = Query("ru", max_length=5, description=ANIME_LANG_HELP),
    wide: bool = Query(False, description="проверить и узкие источники, если лимит позволяет"),
    alt: str = Query("", max_length=1200, description="другие имена тайтла через «|»"),
    who: Caller = Depends(need_any),
):
    """У кого из источников этот тайтл есть на самом деле.

    Меню «Откуда берётся видео» перечисляло все источники подряд.
    Половина из них этого тайтла не знает вовсе: переключаешься — и получаешь
    плашку «этого аниме нет на источнике». Список, где половина строк
    ведёт в тупик, заставляет перебирать их вручную, чтобы выяснить то,
    что сайт мог выяснить сам.

    Проверяются те источники, что участвуют в переборе. Остальные (узкие)
    остаются в списке непроверенными: они отвечают редко и медленно, и
    держать из-за них человека лишние секунды незачем. Страница честно разделит список
    на «здесь есть» и «не проверяли».

    Запросы к источникам кэшируются, поэтому повторное открытие меню
    ничего не стоит.

    wide=1 — страница спрашивает в фоне, заранее, и человек ответа не
    ждёт. Тогда проверяем и узкие источники тоже, чтобы в меню не
    оставалось строк «не проверяли», — но только если это укладывается
    в лимит обращений. Не укладывается — проверяем основные, как раньше.

    Источник, который не ответил вовсе (а не ответил «нет»), в checked
    не попадает, а уходит в silent: страница пишет «не ответил» и даёт
    его выбрать, вместо серой строки «тут этого нет», которая была бы
    неправдой.
    """
    anime.need_sources()                       # личный режим без источников — 501
    mode = anime.anime_mode(lang)
    lang = anime.anime_lang(mode)
    names = [t for t in (title.strip(), title_en.strip()) if t]
    narrow = list(fallback_for(mode))
    everyone = list(dict.fromkeys(narrow + anime.sources_for(mode)))
    # Цена — сколько поисков пойдёт мимо кэша, а не один: см. uncached_lookups.
    # Неудачная попытка лимит не списывает, так что широкую можно пробовать
    # первой и при отказе откатиться к узкой.
    if wide and len(everyone) > len(narrow) and within(
            search_limit, "w", who, cost=uncached_lookups(everyone, (title, title_en))):
        asked = everyone
    elif within(search_limit, "w", who,
                cost=uncached_lookups(narrow, (title, title_en))):
        asked = narrow
    else:
        raise HTTPException(status_code=429, detail="Слишком часто, подождите минуту")

    if not names:
        raise HTTPException(status_code=400, detail="Пустое название")

    async def has_it(source: str, queries: list[str], compare: list[str]) -> bool | None:
        """True — есть, False — ответил, но такого нет, None — молчит."""
        answered = False
        for q in queries:
            rows = await try_source(source, q)
            if rows is None:
                continue
            answered = True
            for row in rows:
                if max(anime.relevance(t, row["title"]) for t in compare) >= GOOD_ENOUGH:
                    return True
        return False if answered else None

    # Источники проверяются параллельно, и общий срок у них один: копия
    # контекста со сроком достаётся каждой задаче gather.
    with anime.budget():
        answers = await asyncio.gather(*(has_it(s, names, names) for s in asked),
                                       return_exceptions=True)
        # Второй круг — по синонимам, и только у тех, кто ответил «нет»:
        # «Старшая школа DxD» у RU-D лежит как «Демоны старшей школы».
        # Списывается с лимита отдельно; не хватает — остаёмся с первым кругом.
        missing = [s for s, ok in zip(asked, answers) if ok is False]
        if missing:
            extra = [n for n in await other_names(names[0], title_en, alt, None,
                                                  hentai_ok(who))
                     if n not in names]
            if extra and within(search_limit, "w", who,
                                cost=uncached_lookups(missing, extra)):
                more = await asyncio.gather(*(has_it(s, extra, names + extra) for s in missing),
                                            return_exceptions=True)
                found = {s for s, ok in zip(missing, more) if ok is True}
                answers = [True if s in found else ok for s, ok in zip(asked, answers)]
    here = [s for s, ok in zip(asked, answers) if ok is True]
    silent = [s for s, ok in zip(asked, answers) if ok is None or isinstance(ok, BaseException)]
    checked = [s for s in asked if s not in silent]
    return {
        "here": here,
        "checked": checked,
        "silent": silent,
        # Всё, до чего не дошли: список известен странице, но пусть
        # решает сервер — источники живут здесь.
        # Только свои: чужой язык в этом списке — приглашение выбрать
        # то, чего человек не поймёт.
        "unknown": [s for s in anime.sources_for(mode) if s not in asked],
    }


# ==========================================================================
# Хентай: показывается только то, что можно посмотреть
# ==========================================================================
# Справочник знает о хентае почти всё, а источники видео — малую часть.
# Пока rx показывался по одному справочнику, поиск выдавал длинный список,
# где большинство карточек вело в «Эту часть ни один источник не отдал».
# Хозяин: «на что нет озвучки — в поиске не надо, просто не показывай,
# сразу бросай».
#
# Поэтому rx (и только rx — у остального ничего не меняется, ни одного
# лишнего похода) перед выдачей проверяется у источников:
#
#   * «есть» — значит ровно то же, что для резолва (api_resolve): у
#     источника на языке аниме человека (настройка anime_lang → sources_for:
#     русские источники дают русские озвучки и русские субтитры,
#     англоязычный — английские) нашёлся тайтл с совпадением названия не
#     ниже GOOD_ENOUGH (с поправкой на год и число серий, part_fit), и
#     список серий у него отдался непустым. Сначала основные источники
#     (fallback_for), потом узкие — как в резолве. Имена — русское,
#     латинское, потом до ALT_MAX синонимов из справочника;
#   * поиск у источника — тот же try_source с тем же кэшем: нажал на
#     проверенную карточку — резолв берёт ответ из кэша, а не из сети;
#   * проверки идут параллельно, не больше RX_PARALLEL тайтлов разом и не
#     больше RX_PER_SOURCE обращений к одному источнику разом; ответ ждёт их
#     не дольше RX_WAIT. Не успели — карточка не показывается («сразу
#     бросай»), а проверка доделывается в фоне и попадает в память: при
#     следующем поиске тайтл появится, если он есть;
#   * память — по (язык, название, год): «есть» помнится сутки, «нет» —
#     шесть часов, «источники молчали» — четверть часа (это не «нет», а
#     «не узнали», и прятать тайтл на полдня из-за чужой профилактики
#     незачем).
#
# Ограничение: проверяется, что серии есть, но не какие в них дорожки.
# Плееры спрашиваются только при просмотре — по ещё одному обращению на
# тайтл это было бы слишком дорого. Язык обеспечивает сам источник: у
# русских сайтов других дорожек, кроме русских озвучек и субтитров, нет.
RX_WAIT = 8.0              # сколько ответ ждёт проверок, секунд
RX_TITLE_BUDGET = 45.0     # срок одной проверки целиком (уже в фоне)
# Срок одному источнику на все имена тайтла. Без него лежащий источник
# (каждое обращение — до CALL_TIMEOUT) держал бы каждое «нет» по минуте, и
# очередь проверок вставала бы за ним целиком.
RX_SOURCE_BUDGET = 12.0
RX_PARALLEL = 4            # тайтлов проверяется разом
RX_PER_SOURCE = 2          # обращений к одному источнику разом
RX_CHECK_MAX = 24          # новых проверок за один ответ
RX_INFLIGHT_MAX = 96       # проверок в работе на весь сайт
RX_YES_TTL = 24 * 3600
RX_NO_TTL = 6 * 3600
RX_SILENT_TTL = 15 * 60
RX_KNOWN_MAX = 5000
# Сколько точных совпадений у одного источника проверять на серии.
RX_PROBES = 2

_rx_known: dict[str, tuple[float, bool]] = {}      # ключ → (до какого времени, есть ли)
# Язык проверки «на любом» (fix-21): хозяин — «не обязательно на каком-то
# языке, просто чтобы был». Хентай показывается, если он есть хоть у одного
# источника: русского (озвучка или субтитры) или английского (дубляж или
# субтитры). Плеер откроет его там, где он есть (api_resolve, rx=1).
RX_ANY = "any"
# На каком языке проверка «на любом» нашла тайтл (ru / en): резолв с rx=1,
# не найдя хентай на языке человека, идёт сначала туда — а не перебирает
# все языки подряд, тратя лимит поиска на заведомо пустые.
_rx_where: dict[str, tuple[float, str]] = {}
_rx_inflight: dict[str, asyncio.Task] = {}
# Замки привязаны к циклу событий; у сервера он один, но в проверках
# каждый запрос идёт в своём — тогда замки заводятся заново.
_rx_gates: dict[str, Any] = {"loop": None}


def rx_key(item: dict, lang: str) -> str:
    """Ключ памяти проверки: язык, название и год. Один и тот же тайтл
    приходит и карточкой поиска, и частью франшизы — ответ у них общий."""
    names = "|".join(anime.normalize_title(str(item.get(f) or ""))
                     for f in ("title", "title_en"))
    return f"{lang}:{names}:{item.get('year') or ''}"


def rx_known(key: str) -> bool | None:
    """Что уже известно о тайтле: True / False, None — не проверяли."""
    row = _rx_known.get(key)
    if row is None:
        return None
    if row[0] < time.time():
        _rx_known.pop(key, None)
        return None
    return row[1]


def _rx_remember(key: str, ok: bool | None) -> None:
    now = time.time()
    if len(_rx_known) >= RX_KNOWN_MAX:
        for k in [k for k, v in _rx_known.items() if v[0] < now]:
            _rx_known.pop(k, None)
        if len(_rx_known) >= RX_KNOWN_MAX:
            for k, _ in sorted(_rx_known.items(), key=lambda kv: kv[1][0])[:RX_KNOWN_MAX // 2]:
                _rx_known.pop(k, None)
    ttl = RX_YES_TTL if ok else (RX_NO_TTL if ok is False else RX_SILENT_TTL)
    _rx_known[key] = (now + ttl, bool(ok))


def _rx_gate(source: str = "") -> asyncio.Semaphore:
    """Замок на тайтлы (без source) или на один источник."""
    loop = asyncio.get_running_loop()
    if _rx_gates["loop"] is not loop:
        _rx_gates.clear()
        _rx_gates["loop"] = loop
    name = "src:" + source if source else "titles"
    if name not in _rx_gates:
        _rx_gates[name] = asyncio.Semaphore(RX_PER_SOURCE if source else RX_PARALLEL)
    return _rx_gates[name]


async def _rx_on_source(source: str, queries: list[str], year: int | None,
                        episodes: int, track: str = "dub") -> bool | None:
    """Есть ли тайтл у одного источника. True — есть и серии отдаются,
    False — ответил, но такого нет, None — молчит."""
    with anime.budget(RX_SOURCE_BUDGET):
        return await _rx_on_source_in_time(source, queries, year, episodes, track)


async def _rx_on_source_in_time(source: str, queries: list[str], year: int | None,
                                episodes: int, track: str = "dub") -> bool | None:
    answered = False
    compare = list(queries)
    # Англоязычный источник ищет по латинскому имени (как в api_subs).
    if anime.source_lang(source) == "en":
        queries = sorted(queries, key=lambda n: bool(_CYR_NAME.search(n)))
    for q in queries:
        async with _rx_gate(source):
            rows = await try_source(source, q)
        if rows is None:
            continue
        answered = True
        strong = []
        for row in rows:
            # Англоязычный источник говорит сразу, что у тайтла есть: только
            # субтитры, только дубляж или оба (поле langs). Просили дубляж, а
            # его нет — тайтл на этом языке не посмотреть.
            langs = row.get("langs")
            if langs and track and track not in langs:
                continue
            score = max(anime.relevance(t, row["title"]) for t in compare) \
                + part_fit(row, year, episodes)
            if score >= GOOD_ENOUGH:
                strong.append((score, row))
        strong.sort(key=lambda c: -c[0])
        for _, row in strong[:RX_PROBES]:
            try:
                async with _rx_gate(source):
                    count = len(await anime.find_episodes(source, row["key"], row["title"]))
            except asyncio.CancelledError:
                raise
            except Exception:                      # noqa: BLE001
                count = 0
            if count:
                return True
    return False if answered else None


async def _rx_first_yes(checks: list) -> bool | None:
    """Первый «есть» решает всё, остальные отменяются. Все «нет» — False,
    кто-то промолчал — None."""
    if not checks:
        return False
    tasks = [asyncio.ensure_future(c) for c in checks]
    unsure = False
    try:
        for fut in asyncio.as_completed(tasks):
            try:
                ok = await fut
            except asyncio.CancelledError:
                raise
            except Exception:                      # noqa: BLE001
                ok = None                          # LibrariesMissing, срок, сбой
            if ok:
                return True
            if ok is None:
                unsure = True
        return None if unsure else False
    finally:
        for t in tasks:
            t.cancel()


def rx_groups(lang: str) -> tuple[list[str], list[str], str]:
    """Кого спрашивать: основные источники, узкие и какой вариант нужен.

    RX_ANY (fix-21) — все источники всех языков, любой вариант (русская
    озвучка, английский дубляж, английские субтитры). Сначала основные
    русские и английские, потом узкие."""
    if lang != RX_ANY:
        everyone = anime.sources_for(lang)
        first = [s for s in fallback_for(lang) if s in everyone]
        return first, [s for s in everyone if s not in first], anime.anime_track(lang)
    everyone = list(dict.fromkeys(anime.sources_for("ru") + anime.sources_for("endub")
                                  + anime.sources_for("ensub")))
    main_ = [s for s in dict.fromkeys(fallback_for("ru") + fallback_for("endub")
                                      + fallback_for("ensub")) if s in everyone]
    # Источники хентая (anime.RX_ORDER) — первыми: они только про него. Сюда rx_check
    # попадает лишь из rx_cards и rx_parts_filter, то есть для хентая из
    # нашего справочника и у того, кому он включён; источник хентая
    # вдобавок сам проверяет, что справочник этот тайтл показывал
    # (anime.RX_CLEARED).
    rx_first = [s for s in anime.RX_ORDER if s in anime.SOURCES]
    return rx_first + main_, [s for s in everyone if s not in main_], ""


async def rx_check(item: dict, lang: str) -> bool | None:
    """Можно ли посмотреть этот тайтл на языке lang (RX_ANY — на любом)."""
    title = str(item.get("title") or "").strip()
    title_en = str(item.get("title_en") or "").strip()
    names = [n for n in dict.fromkeys((title, title_en)) if len(n) >= 2]
    if not names:
        return False
    seen = {anime.normalize_title(x) for x in names}
    extra = [n for n in split_alt("|".join(str(a) for a in item.get("alt") or []))
             if anime.normalize_title(n) not in seen]
    queries = names + extra[:ALT_MAX]
    year = item.get("year") if isinstance(item.get("year"), int) else None
    episodes = item.get("episodes") if isinstance(item.get("episodes"), int) else 0
    first, spare, track = rx_groups(lang)
    unsure = False
    where: list[str] = []

    async def tagged(source: str) -> bool | None:
        ok = await _rx_on_source(source, queries, year, episodes, track)
        if ok:
            where.append(anime.source_lang(source))
        return ok
    with anime.budget(RX_TITLE_BUDGET):
        for group in (first, spare):
            ok = await _rx_first_yes([tagged(s) for s in group])
            if ok:
                if where and lang == RX_ANY:
                    if len(_rx_where) >= RX_KNOWN_MAX:
                        _rx_where.clear()
                    _rx_where[rx_key(item, lang)] = (time.time() + RX_YES_TTL, where[0])
                return True
            unsure = unsure or ok is None
    return None if unsure else False


async def _rx_task(key: str, item: dict, lang: str) -> bool | None:
    async with _rx_gate():
        known = rx_known(key)             # пока ждали очереди, мог ответить другой
        if known is not None:
            return known
        try:
            ok = await rx_check(item, lang)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                   # noqa: BLE001
            log.info("хентай: проверка %r не прошла: %s", key[:80], type(exc).__name__)
            ok = None
    _rx_remember(key, ok)
    return ok


async def rx_watchable(items: list[dict], lang: str) -> list[bool]:
    """Для каждого rx из items — можно ли его посмотреть на языке lang.

    Ответ — не позже RX_WAIT: что не успело провериться, в этом ответе
    считается «нет» и доделывается в фоне. Одинаковые проверки из разных
    запросов склеиваются в одну (как fresh_last_episode)."""
    loop = asyncio.get_running_loop()
    for k in [k for k, t in _rx_inflight.items() if t.done() or t.get_loop() is not loop]:
        _rx_inflight.pop(k, None)
    keys = [rx_key(i, lang) for i in items]
    out: list[bool | None] = [rx_known(k) for k in keys]
    waiting: dict[int, asyncio.Task] = {}
    started = 0
    for n, (key, item) in enumerate(zip(keys, items)):
        if out[n] is not None:
            continue
        task = _rx_inflight.get(key)
        if task is None:
            if started >= RX_CHECK_MAX or len(_rx_inflight) >= RX_INFLIGHT_MAX:
                continue                  # в этот раз не проверяем вовсе — спрячется
            started += 1
            task = loop.create_task(_rx_task(key, dict(item), lang))
            _rx_inflight[key] = task

            def forget(t: asyncio.Task, key: str = key) -> None:
                if _rx_inflight.get(key) is t:
                    _rx_inflight.pop(key, None)
            task.add_done_callback(forget)
        waiting[n] = task
    if waiting:
        # wait, а не wait_for: недождавшиеся задачи не отменяются, а
        # доделываются в фоне.
        done, _ = await asyncio.wait(set(waiting.values()), timeout=RX_WAIT)
        for n, task in waiting.items():
            if task in done and not task.cancelled() and task.exception() is None:
                out[n] = task.result()
    return [bool(x) for x in out]


def rx_pending(items: list[dict], lang: str) -> int:
    """Сколько из items ещё проверяется в фоне (для строки под выдачей)."""
    return sum(1 for i in items if rx_key(i, lang) in _rx_inflight
               and rx_known(rx_key(i, lang)) is None)


async def rx_cards(cards: list[dict] | None, who: Caller) -> tuple[list[dict], dict]:
    """Карточки поиска, где хентай-карточки, которых нет ни у одного
    источника ни на одном языке, заменены обычной частью той же франшизы
    (nox) или убраны. Вторым — что сказать странице: сколько спрятано и
    сколько ещё в фоне."""
    cards = list(cards or [])
    lang = RX_ANY
    rx = [c for c in cards if catalog.is_hentai(c)]
    oks = await rx_watchable(rx, lang) if rx else []
    bad = [c for c, ok in zip(rx, oks) if not ok]
    gone = {id(c) for c in bad}
    out = []
    for card in cards:
        if id(card) in gone:
            card = card.get("nox")
            if not card:
                continue
        out.append(card)
    return out, {"rx_checked": len(rx), "rx_hidden": len(bad),
                 "rx_pending": rx_pending(bad, lang)}


def rx_parts_filter(who: Caller):
    """Проверка для частей франшизы (catalog.franchise_parts) — у кого
    хентай включён. У остальных rx и так не виден, проверять нечего."""
    if not hentai_ok(who):
        return None
    lang = RX_ANY

    async def keep(parts: list[dict]) -> list[bool]:
        return await rx_watchable(parts, lang)
    return keep


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
async def twofa_start(body: PasswordCheckIn, request: Request,
                      who: Caller = Depends(need_user)):
    """Готовит секрет и картинку с кодом. Пока не подтверждён — не включён.

    Секрет создаётся заново на каждое нажатие галочки: если человек начал
    настройку, передумал и начал снова, старый секрет не должен остаться
    рабочим.

    Спрашиваем пароль — так же, как при выключении. Раньше включить вход
    по коду мог любой, кто подсел за незапертый ноутбук: две кнопки, свой
    телефон вместо приложения владельца — и владелец заперт снаружи
    собственного аккаунта. Выключение пароль спрашивало, а включение,
    которое запирает ничуть не хуже, — нет.

    И не даём начать заново поверх уже включённого: так подменялся секрет
    у работающей защиты. Сменить приложение — это выключить (с паролем)
    и включить снова.
    """
    guard_csrf(request)
    if not pass_limit.allow(f"t:{who.user_id}"):
        raise HTTPException(status_code=429, detail="Слишком часто. Попробуйте позже.")
    if who.user["totp_on"]:
        raise HTTPException(status_code=409,
                            detail="Вход по коду уже включён. Чтобы сменить приложение, "
                                   "сначала выключите его.")
    if not await verify_password(body.password, who.user["pass_hash"]):
        raise HTTPException(status_code=403, detail="Пароль не подошёл")
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
    if who.user["totp_on"]:
        pending_2fa.pop(who.user_id, None)
        raise HTTPException(status_code=409, detail="Вход по коду уже включён.")
    # Черновик появляется только в /2fa/start, а туда без пароля не
    # попасть. Значит, включить можно ровно тот секрет, который выдали
    # после проверки пароля, и никакой другой.
    draft = pending_2fa.get(who.user_id)
    if not draft or time.time() - draft[1] > 600:
        pending_2fa.pop(who.user_id, None)
        raise HTTPException(status_code=409,
                            detail="Настройка устарела. Снимите галочку и начните заново.")
    secret = draft[0]
    if not twofa.verify(secret, body.code):
        raise HTTPException(status_code=400, detail="Код не подошёл. Проверьте цифры.")
    codes = twofa.new_backup_codes()
    # Все остальные входы закрываются, остаётся только этот. Защиту ставят,
    # чтобы закрыть дверь, — а раньше уже открытые сессии, включая чужую,
    # если пароль утёк, жили дальше как ни в чём не бывало.
    #
    # Шаг времени этого кода как использованный не отмечаем: им лишь
    # доказали, что приложение настроено. Иначе человек, который тут же
    # входит на телефоне (его оттуда только что выкинуло), получал бы
    # «код уже использован» на те же самые цифры.
    closed = store.set_totp(who.user_id, secret, True,
                            ",".join(twofa.hash_backup(c) for c in codes),
                            keep_token=who.token)
    pending_2fa.pop(who.user_id, None)
    log.info("вход по коду включён: %s", security.safe_for_log(who.user["login"]))
    # Запасные коды показываем ровно один раз: в базе лежат только их
    # отпечатки, и восстановить список потом неоткуда.
    return {"ok": True, "backup": codes, "closed": closed}


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
    # Адрес проверяется всегда, а не только при согласии на письма. Раньше
    # при want=false в базу уходила любая строка — с переводами строк и
    # управляющими символами, — и потом она же стояла в кабинете и
    # ждала, когда галочку включат.
    if (body.want or email) and not security.looks_like_email(email):
        raise HTTPException(status_code=400, detail="Не похоже на адрес почты")
    store.set_mail_prefs(who.user_id, email, body.want)
    return {"ok": True, "email": email, "want": body.want,
            "sending": mail.enabled()}


# --------------------------------------------------------------------------
# Помощник в пузыре «?» (подробности — в api/helper.py)
# --------------------------------------------------------------------------
class HelperTurnIn(BaseModel):
    role: str = Field(max_length=10, pattern=r"^(user|assistant)$")
    content: str = Field(max_length=4000)


class HelperIn(BaseModel):
    # Длиннее 500 символов вопрос не принимается (см. helper.MESSAGE_MAX),
    # но проверяем это сами: тогда человек видит понятную фразу, а не 422.
    message: str = Field(min_length=1, max_length=4000)
    # История живёт только в браузере. Присылается целиком, а наружу
    # уходят последние helper.HISTORY_KEEP реплик.
    history: list[HelperTurnIn] = Field(default_factory=list, max_length=60)
    # Язык сайта у человека прямо сейчас (ru/en). Страница знает его
    # точнее базы: гость и только что переключивший язык в базе ещё не
    # записаны. Нет или непонятный — берём из настроек.
    lang: str | None = Field(default=None, max_length=5)


def helper_lang(who: Caller, asked: str | None) -> str:
    """На каком языке говорить помощнику: что прислала страница, иначе
    язык сайта из настроек, иначе русский."""
    asked = (asked or "").strip().lower()
    if asked in helper.LANGS:
        return asked
    if who is not None and not who.is_guest and who.user is not None:
        saved = settings_view(store.get_settings(who.user_id)).get("ui_lang")
        if saved in helper.LANGS:
            return saved
    return "ru"


def helper_state(who: Caller) -> str:
    if anime.DEMO:
        return "demo"
    if who.is_guest:
        return "guest"
    return "ok" if helper.enabled() else "off"


@app.get("/api/helper")
async def api_helper_state(who: Caller = Depends(need_any),
                           lang: str = Query("", max_length=5)):
    """Включён ли помощник для того, кто спрашивает. Администратору без
    ключа — подсказка, какую строку дописать в .env. Ответ — коды, а не
    фразы; на языке сайта (lang) только заглушка ключа в подсказке."""
    state = helper_state(who)
    out: dict[str, Any] = {"state": state}
    if state == "off" and who.is_admin:
        out["hint"] = "HELPER_API_KEY=" + helper.say(helper_lang(who, lang), "ваш-ключ", "your-key")
    return out


@app.post("/api/helper/ask")
async def api_helper_ask(body: HelperIn, request: Request,
                         who: Caller = Depends(need_user)):
    guard_csrf(request)
    # Все фразы отсюда человек видит в окошке помощника как есть — поэтому
    # на языке его сайта (say: русская и английская).
    lang = helper_lang(who, body.lang)
    say = helper.say
    state = helper_state(who)
    if state == "demo":
        raise HTTPException(status_code=503,
                            detail=say(lang, "В демонстрационном режиме помощник недоступен.",
                                       "The helper is not available in demo mode."))
    if state != "ok":
        raise HTTPException(status_code=503, detail=say(lang, "Помощник не настроен.",
                                                        "The helper is not set up."))
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail=say(lang, "Напишите вопрос.", "Type a question."))
    if len(message) > helper.MESSAGE_MAX:
        raise HTTPException(status_code=400,
                            detail=say(lang, f"Слишком длинно — уложитесь в {helper.MESSAGE_MAX} символов.",
                                       f"Too long — keep it under {helper.MESSAGE_MAX} characters."))
    if not within(helper_limit, "helper", who):
        raise HTTPException(status_code=429,
                            detail=say(lang, "Вы задали много вопросов подряд — помощник передохнёт. "
                                             "Попробуйте через час (до 30 вопросов в час).",
                                       "That's a lot of questions in a row — the helper needs a break. "
                                       "Try again in an hour (up to 30 questions an hour)."))
    if not helper_day_limit.allow("helper:all"):
        raise HTTPException(status_code=429,
                            detail=say(lang, "Помощник на сегодня устал — загляните завтра.",
                                       "The helper is worn out for today — come back tomorrow."))
    # Логин, имя и почта спрашивающего наружу не уходят, даже если он
    # сам впишет их в вопрос.
    u = who.user
    private = [str(u["login"] or ""), str(u["display_name"] or ""), str(u["email"] or "")]
    messages = helper.build_messages(
        message, [t.model_dump() for t in body.history], who.is_admin, private, lang)
    try:
        raw = await helper.ask_model(messages)
    except helper.HelperError as exc:
        raise HTTPException(status_code=429 if exc.busy else 502, detail=exc.text(lang))
    text, chips = helper.clean_reply(raw, who.is_admin, private)
    if not text and not chips:
        text = say(lang, "Я этого не знаю. Попробуйте спросить по-другому.",
                   "I don't know that. Try asking another way.")
    return {"reply": text, "chips": helper.chip_view(chips, lang)}


@app.get("/api/mode")
async def api_mode():
    """В каком режиме работает сайт.

    Открыта намеренно, без входа: страница должна показать плашку про
    демонстрационный режим ещё до того, как человек куда-то войдёт.
    Ничего, кроме одного слова, отсюда не узнать.
    """
    return {"demo": anime.DEMO}


@app.get("/api/health")
@app.head("/api/health")                  # HEAD — для сторожей доступности (см. page)
async def health():
    """Жив ли сайт — для HEALTHCHECK контейнера и сторожа.

    Открыта без входа и отвечает одним словом. Заодно трогает базу
    (SELECT 1 — мгновенно): раньше ответ был «ok» всегда, и сломанная
    или запертая база была не видна ни docker, ни сторожу. Причину пишем
    в журнал, а не в ответ: снаружи незачем знать, что у нас внутри."""
    try:
        store.connect().execute("SELECT 1").fetchone()
    except Exception as exc:                       # noqa: BLE001
        log.warning("проверка здоровья: база не отвечает: %s", type(exc).__name__)
        return JSONResponse({"status": "unavailable"}, status_code=503)
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
         "/pravila": "pravila.html",
         # Браузер сам спрашивает /favicon.ico у любого сайта, даже если
         # в странице указан другой значок, а iPhone так же ищет
         # /apple-touch-icon.png в корне. Отдаём настоящие файлы (ICO с
         # размерами 16/32/48 и PNG 180), иначе в журнале на каждый заход
         # висит 404. Остальные значки и манифест лежат в /static.
         "/favicon.ico": "favicon.ico",
         "/apple-touch-icon.png": "apple-touch-icon.png"}


# ---------- Сброс кэша стилей и скриптов после обновления ----------
#
# Каждая страница ссылается на /static/app.css, /static/app.js и т. д.
# Если адрес файла не меняется, браузер (или прокси по дороге) может
# показать старую копию — так после обновления «ничего не поменялось».
# Поэтому при отдаче страницы к каждой ссылке на свой .css/.js
# дописывается ?v=<первые 12 знаков sha256 содержимого файла>. Поменялся
# файл — поменялся адрес, и старой копии взяться неоткуда.
#
# Отпечаток считается по содержимому, а кэшируется по (mtime, размер):
# после git pull без перезапуска сервера адреса тоже обновятся сами.
_ASSET_RE = re.compile(r'((?:href|src)=")/static/([A-Za-z0-9_.-]+\.(?:css|js))(")')
_asset_hash: dict[str, tuple[tuple[float, int], str]] = {}
_page_cache: dict[str, tuple[tuple, bytes]] = {}


def asset_version(name: str) -> str:
    """Отпечаток файла из web/ (12 hex) или "" — если файла нет."""
    full = os.path.join(WEB_DIR, name)
    try:
        st = os.stat(full)
    except OSError:
        return ""
    key = (st.st_mtime, st.st_size)
    got = _asset_hash.get(name)
    if got and got[0] == key:
        return got[1]
    with open(full, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()[:12]
    _asset_hash[name] = (key, digest)
    return digest


def _fresh_version(path: str, v: str | None) -> bool:
    """Запрошен ли файл /static/... по его нынешнему отпечатку."""
    if not v or not path.startswith("/static/"):
        return False
    name = path[len("/static/"):]
    if "/" in name or not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:css|js)", name):
        return False
    return asset_version(name) == v


def render_page(name: str) -> bytes:
    """HTML страницы с ?v=… у ссылок на свои стили и скрипты."""
    full = os.path.join(WEB_DIR, name)
    st = os.stat(full)
    with open(full, "rb") as fh:
        raw = fh.read().decode("utf-8")
    names = sorted(set(m.group(2) for m in _ASSET_RE.finditer(raw)))
    key = (st.st_mtime, st.st_size) + tuple(asset_version(n) for n in names)
    got = _page_cache.get(name)
    if got and got[0] == key:
        return got[1]

    def _sub(m: re.Match) -> str:
        v = asset_version(m.group(2))
        return m.group(1) + "/static/" + m.group(2) + (("?v=" + v) if v else "") + m.group(3)

    body = _ASSET_RE.sub(_sub, raw).encode("utf-8")
    _page_cache[name] = (key, body)
    return body


def _page_response(name: str) -> Response:
    if not name.endswith(".html"):
        return FileResponse(os.path.join(WEB_DIR, name))
    return Response(render_page(name), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


# HEAD разрешён явно: FastAPI (в отличие от чистого Starlette) не добавляет
# его к GET сам, и на «HEAD /» сайт отвечал 405. Так ходят сторожа
# доступности (UptimeRobot и подобные) и проверки ссылок; тело для HEAD
# uvicorn отбрасывает сам, заголовки остаются те же, что у GET.
@app.api_route("/{page}", methods=["GET", "HEAD"])
async def page(page: str):
    """Отдаём только заранее перечисленные страницы.

    Так исключён обход каталога: имя файла не собирается из того,
    что прислал посетитель.
    """
    name = PAGES.get("/" + page)
    if name is None:
        raise HTTPException(status_code=404, detail="Не найдено")
    return _page_response(name)


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return _page_response("index.html")


# Типы для значков и манифеста. Их угадывают по таблице системы, а в
# тонком образе Docker её может не быть: тогда манифест ушёл бы как
# «двоичный файл», и браузер бы его не принял (X-Content-Type-Options:
# nosniff запрещает ему догадываться самому).
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/x-icon", ".ico")

# Статика: только эта папка, StaticFiles сам не выпускает за её пределы
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
