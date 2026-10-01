"""Пароли, сессии, защита от перебора.

Здесь собрано всё, что касается «кто ты такой». Логика вынесена отдельно,
чтобы её можно было проверить тестами независимо от веб-части.
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
# Пароли
# --------------------------------------------------------------------------
# PBKDF2 из стандартной библиотеки: не требует сторонних пакетов, а значит
# не добавляет ещё одну зависимость, которую надо обновлять.
# 600 000 итераций — рекомендация OWASP для SHA-256 на 2023+ год.
PBKDF2_ROUNDS = 600_000
SALT_BYTES = 16


def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS) -> str:
    """Возвращает строку вида pbkdf2_sha256$раунды$соль$хэш."""
    if not isinstance(password, str) or not password:
        raise ValueError("пустой пароль")
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return "pbkdf2_sha256${}${}${}".format(rounds, salt.hex(), dk.hex())


def verify_password(password: str, stored: str) -> bool:
    """Сверяет пароль с сохранённым хэшем за постоянное время."""
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
    # compare_digest — чтобы по времени ответа нельзя было подбирать хэш посимвольно
    return hmac.compare_digest(dk, expected)


# Пустой пароль сверяем с этим, чтобы несуществующий логин обрабатывался
# столько же времени, сколько существующий. Иначе по скорости ответа можно
# понять, какие логины заведены.
_DUMMY_HASH = hash_password("dummy-password-for-timing", rounds=PBKDF2_ROUNDS)


def waste_time_like_a_real_check() -> None:
    verify_password("nope", _DUMMY_HASH)


# --------------------------------------------------------------------------
# Требования к паролю
# --------------------------------------------------------------------------
PASSWORD_MIN = 10
PASSWORD_MAX = 200

# Пароли, которые перебирают в первую очередь. Список короткий, но самые
# очевидные варианты закрывает.
WEAK = {
    "password", "password1", "qwerty123", "123456789", "1234567890",
    "iloveyou", "admin12345", "letmein123", "welcome123", "anime12345",
    "qwertyuiop", "1qaz2wsx3edc", "adminadmin", "passw0rd12",
}


def password_problem(password: str) -> str | None:
    """Возвращает текст проблемы или None, если пароль годится."""
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
    """Грубая проверка адреса почты.

    Проверять адрес по-настоящему бессмысленно: единственный надёжный
    способ узнать, что он рабочий, — отправить туда письмо. Здесь мы
    ловим только явную опечатку и не пускаем в поле управляющие символы
    и пробелы, которыми можно испортить заголовки письма.
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
    """Убирает всё, чем можно испортить строку журнала.

    В журнал и в ключи счётчика попыток попадает только это значение:
    переводом строки внутри логина иначе можно было бы дописать
    поддельную запись в лог.
    """
    return "".join(ch for ch in str(value or "") if ch.isprintable())[:64]


LOGIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30})[a-z0-9]$")


def normalize_login(login: str) -> str:
    """Приводит логин к единому виду, чтобы Valera и valera были одним человеком.

    Лишние пробелы и переводы строк по краям убираются — их часто приносит
    копирование из мессенджера.

    Внутренние символы НЕ трогаем. Соблазн вырезать «плохие» велик, но это
    ошибка: тогда `vale<перевод строки>ra` тихо превратится в `valera`
    и пустит человека в чужой аккаунт. Такие строки должна отклонять
    проверка login_problem, а не молча исправлять эта функция.
    """
    if not isinstance(login, str):
        return ""
    # NFKC схлопывает похожие символы вроде полноширинных латинских букв
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
# Токены сессий
# --------------------------------------------------------------------------
SESSION_BYTES = 32           # 256 бит случайности — подобрать невозможно
SESSION_IDLE = 14 * 24 * 3600   # две недели без действий — и сессия мертва
SESSION_MAX_LIFE = 90 * 24 * 3600   # даже активная сессия живёт не дольше
GUEST_TTL = 60 * 60          # гость живёт час


def new_token() -> str:
    return secrets.token_urlsafe(SESSION_BYTES)


def pepper() -> str:
    """Секретная строка сервера.

    Читается при каждом обращении, а не один раз при импорте: так её можно
    подменить в тестах и так смена строки в настройках сразу закрывает все
    сессии, не требуя перезапуска процесса.
    """
    return os.getenv("SESSION_PEPPER", "")


def token_fingerprint(token: str) -> str:
    """В базе храним не сам токен, а его отпечаток.

    Если базу украдут, войти по её содержимому будет нельзя: из отпечатка
    токен не восстановить.
    """
    return hashlib.sha256((pepper() + (token or "")).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Ограничение попыток входа
# --------------------------------------------------------------------------
class LoginGuard:
    """Считает неудачные попытки и притормаживает перебор.

    Считаем отдельно по логину и по адресу: иначе злоумышленник либо
    перебирает пароли к одному логину, либо один пароль ко многим логинам,
    и один из счётчиков его пропускает.

    Важно, что записи убираются сами. Раньше запись чистилась только при
    следующем обращении по тому же ключу — и перебор с сотней тысяч разных
    логинов оставлял в памяти сотню тысяч записей навсегда. Медленная течь,
    которая через месяц работы превратилась бы в съеденную память.
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
        """Убирает всё просроченное целиком, а не по одной записи.

        force=True приходит, когда словарь уже перерос потолок. Без этого
        уборка шла раз в минуту — и за эту минуту перебор успевал набить
        сколько угодно записей. Дыра была ровно в промежутке между уборками.
        """
        if not force and now - self._last_sweep < self.SWEEP_EVERY:
            return
        self._last_sweep = now
        for key in [k for k, v in self._fails.items()
                    if not v or now - max(v) >= self.WINDOW]:
            del self._fails[key]
        for key in [k for k, until in self._locked.items() if until <= now]:
            del self._locked[key]
        # Если словарь всё равно распух — оставляем самые свежие записи.
        # Забыть чужую попытку не страшно: перебор упрётся в ограничение
        # на стороне nginx. Съеденная память страшнее.
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
        """Для наблюдения: сколько записей сейчас в памяти."""
        return len(self._fails), len(self._locked)

    def locked_for(self, login: str, ip: str) -> int:
        """Сколько секунд ещё нельзя пробовать. 0 — можно."""
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
        # Потолок проверяем на каждой записи: между плановыми уборками
        # словарь не должен успевать раздуться.
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
    """Простое ограничение частоты: не больше N обращений за период.

    Как и счётчик попыток входа, чистит себя целиком, а не по одному ключу:
    иначе память растёт от каждого нового посетителя и никогда не убывает.
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
# Защита от подделки запросов с чужих сайтов (CSRF)
# --------------------------------------------------------------------------
def csrf_for(session_token: str) -> str:
    """Метка формы, вычисленная из токена сессии.

    Раньше здесь было просто случайное число: сервер клал его в куку и потом
    сверял куку с заголовком. Слабое место такой схемы — кука. Кто угодно,
    кто сумел поставить браузеру куку `csrf` (сосед по домену, точка доступа
    на http, старая заражённая вкладка), задавал обе половины проверки сразу
    и проходил её.

    Теперь метка — это подпись от отпечатка сессии, и подделать её, не зная
    SESSION_PEPPER, нельзя. Подставленная кука с чужим значением не сойдётся
    с сессией и будет отклонена. Хранить ничего не нужно: значение всегда
    пересчитывается из самого токена.
    """
    mac = hmac.new(
        ("csrf|" + pepper()).encode("utf-8"),
        token_fingerprint(session_token).encode("ascii"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac)[:32].decode("ascii")


def csrf_ok(from_cookie: str | None, from_header: str | None,
            session_token: str | None = None) -> bool:
    """Проверяем и совпадение куки с заголовком, и подпись самой метки."""
    if not from_cookie or not from_header:
        return False
    if len(from_cookie) < 16 or len(from_cookie) > 200:
        return False
    if not hmac.compare_digest(from_cookie, from_header):
        return False
    if session_token is None:
        # Без сессии сверять не с чем: такие запросы всё равно отсекает
        # проверка прав, до записи в базу они не доходят.
        return True
    return hmac.compare_digest(from_cookie, csrf_for(session_token))


# --------------------------------------------------------------------------
# Адрес посетителя
# --------------------------------------------------------------------------
def valid_ip(value: str) -> str:
    """Возвращает адрес, если это действительно адрес, иначе пустую строку.

    Нужно, чтобы в ключ счётчика попыток не попадала произвольная строка,
    присланная снаружи: иначе каждый запрос выглядит как новый посетитель.
    """
    value = (value or "").strip()
    if not value or len(value) > 45:
        return ""
    # nginx может прислать адрес с портом — отрезаем
    if value.count(":") == 1 and "." in value:
        value = value.split(":")[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""
