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
    # Ещё по десятку из начала любых списков утёкших паролей — тех, что
    # длиннее десяти знаков и потому проходили проверку длины.
    "password123", "password12", "qwerty12345", "1q2w3e4r5t", "1q2w3e4r5t6y",
    "qwertyuiop123", "zaq12wsxcde3", "12345678910", "0987654321", "1111111111",
    "asdfghjkl1", "йцукенгшщз", "qwerty123456", "iloveyou123", "123123123123",
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
    """Приводит логин к единому виду, чтобы Hozyain и hozyain были одним человеком.

    Лишние пробелы и переводы строк по краям убираются — их часто приносит
    копирование из мессенджера.

    Внутренние символы НЕ трогаем. Соблазн вырезать «плохие» велик, но это
    ошибка: тогда `hozy<перевод строки>ain` тихо превратится в `hozyain`
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


# Самая короткая строка, которую сервер считает секретом. openssl rand
# -hex 32 даёт 64 знака; шестнадцать — нижняя граница, которую уже не
# подобрать перебором по подписанным адресам видео.
PEPPER_MIN = 16
# Строки-образцы и заглушки: их знает каждый, кто видел репозиторий или
# любой учебник. С ними подпись адресов и метки форм ничего не стоят.
# Только слова: в строке из openssl rand -hex 32 (цифры и a–f) ни одно из
# них случайно не встретится, а «123456» встретилось бы раз на миллион
# запусков — и рабочий сервер не поднялся бы на ровном месте.
_PEPPER_PLACEHOLDERS = ("замените", "change", "secret", "example", "pepper",
                        "password", "пароль", "qwerty")


def pepper_problem(value: str | None = None) -> str | None:
    """Что не так с SESSION_PEPPER — или None, если строка годится."""
    value = pepper() if value is None else value
    if not value:
        return "строка не задана"
    low = value.lower()
    if any(word in low for word in _PEPPER_PLACEHOLDERS):
        return "это строка-образец из примера, её знают все"
    if len(value) < PEPPER_MIN:
        return f"строка короче {PEPPER_MIN} знаков"
    if len(set(value)) < 8:
        return "в строке слишком мало разных знаков"
    return None


# --------------------------------------------------------------------------
# Шифр для секретов в базе
# --------------------------------------------------------------------------
# Секрет приложения-аутентификатора лежал в базе открытым текстом. А база
# покидает сервер: ночные копии, копия по команде /backup в телеграм.
# Кто получил файл, получал и второй ключ от каждого аккаунта с кодом —
# оставалось подобрать пароль, и вход по коду ничего не добавлял.
#
# Теперь секрет зашифрован ключом, выведенным из SESSION_PEPPER, а она
# лежит только в .env на сервере — не в базе и не в копиях. Шифр собран
# из стандартных частей, без сторонних пакетов: поток ключа — HMAC-SHA256
# от (случайная метка, номер блока), подлинность — отдельный HMAC поверх
# всего (encrypt-then-MAC). Подменить или подправить запись в базе,
# не зная строки, нельзя: подпись не сойдётся.
#
# Цена: сменили SESSION_PEPPER — сохранённые секреты больше не
# расшифровываются, и вход по коду придётся включить заново. Для
# администратора на этот случай есть команда python -m api.admin 2fa-off.
SEAL_PREFIX = "s1:"


def _seal_keys(purpose: str) -> tuple[bytes, bytes]:
    base = ("seal|" + purpose + "|" + pepper()).encode("utf-8")
    return (hmac.new(base, b"enc", hashlib.sha256).digest(),
            hmac.new(base, b"mac", hashlib.sha256).digest())


def _stream(key: bytes, nonce: bytes, size: int) -> bytes:
    out = bytearray()
    block = 0
    while len(out) < size:
        out += hmac.new(key, nonce + block.to_bytes(4, "big"), hashlib.sha256).digest()
        block += 1
    return bytes(out[:size])


def seal(text: str, purpose: str = "totp") -> str:
    """Зашифровать строку для хранения в базе. Пустая остаётся пустой."""
    if not text:
        return ""
    enc_key, mac_key = _seal_keys(purpose)
    nonce = secrets.token_bytes(16)
    plain = text.encode("utf-8")
    body = bytes(a ^ b for a, b in zip(plain, _stream(enc_key, nonce, len(plain))))
    tag = hmac.new(mac_key, nonce + body, hashlib.sha256).digest()[:16]
    return SEAL_PREFIX + base64.urlsafe_b64encode(nonce + body + tag).decode("ascii")


class Unsealable(ValueError):
    """Запись не расшифровывается: строку сервера сменили или запись подделана."""


def unseal(stored: str, purpose: str = "totp") -> str:
    """Расшифровать то, что сделал seal. Старая запись открытым текстом
    (до шифра) отдаётся как есть — её зашифрует store.init при запуске."""
    if not stored:
        return ""
    if not stored.startswith(SEAL_PREFIX):
        return stored
    try:
        raw = base64.urlsafe_b64decode(stored[len(SEAL_PREFIX):].encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise Unsealable("запись повреждена") from None
    if len(raw) < 16 + 16:
        raise Unsealable("запись повреждена")
    nonce, body, tag = raw[:16], raw[16:-16], raw[-16:]
    enc_key, mac_key = _seal_keys(purpose)
    if not hmac.compare_digest(hmac.new(mac_key, nonce + body, hashlib.sha256).digest()[:16], tag):
        raise Unsealable("подпись не сходится — SESSION_PEPPER меняли?")
    plain = bytes(a ^ b for a, b in zip(body, _stream(enc_key, nonce, len(body))))
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        raise Unsealable("запись повреждена") from None


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

    Как было и почему так нельзя. Раньше счётчиков было два: по логину и по
    адресу, и восемь неверных паролей к логину запирали его на четверть
    часа — для всех, откуда бы человек ни пришёл. Блокировка проверяется
    ДО пароля, иначе она не бережёт процессор. Вместе это значило: любой,
    кто знает логин владельца, раз в пятнадцать минут шлёт восемь мусорных
    паролей откуда угодно — и владелец не может войти никогда, даже с верным
    паролем. Защита от взлома сама стала способом выгнать хозяина.

    Как теперь. Три счётчика, и запирают они разное:

      * логин + адрес — 8 ошибок, и ЭТА ПАРА заперта на 15 минут. Перебор
        пароля с одного адреса упирается сюда. Владельца, который заходит
        с другого адреса, это не касается никак;
      * адрес — 25 ошибок, и заперт адрес целиком. Это против «один пароль
        ко многим логинам»: пар много, а адрес один;
      * логин целиком, откуда бы ни шли, — порог намного выше (60) и не
        запирание, а короткая пауза в минуту. Это против перебора одного
        логина с сотни адресов. И пауза не касается адресов, с которых
        в этот аккаунт уже входили с верным паролем: с них владелец
        заходит как обычно, даже пока чужие адреса упираются в паузу.

    Чего это не решает: если злоумышленник сидит за тем же адресом, что
    и владелец (общий Wi-Fi, один провайдерский адрес на дом), он может
    запереть эту пару. Различить их по адресу тогда нельзя в принципе.

    Важно, что записи убираются сами. Раньше запись чистилась только при
    следующем обращении по тому же ключу — и перебор с сотней тысяч разных
    логинов оставлял в памяти сотню тысяч записей навсегда. Медленная течь,
    которая через месяц работы превратилась бы в съеденную память.
    """

    WINDOW = 15 * 60      # окно, за которое помним попытки
    MAX_PER_PAIR = 8      # логин + адрес: перебор пароля с одного места
    MAX_PER_IP = 25       # адрес: один пароль ко многим логинам
    MAX_PER_LOGIN = 60    # логин отовсюду сразу: перебор с многих адресов
    LOCK_SECONDS = 15 * 60
    LOGIN_PAUSE = 60      # общий счётчик логина — только короткая пауза
    KNOWN_SECONDS = 30 * 24 * 3600   # сколько помним адрес удачного входа
    MAX_KEYS = 20_000     # потолок на всякий случай
    SWEEP_EVERY = 60      # как часто проходим по всему словарю

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        # «логин|адрес» → когда отсюда последний раз вошли с верным паролем.
        # Живёт в памяти: после перезапуска сервера забывается, и это не
        # страшно — до первого удачного входа действует только общая пауза.
        self._known: dict[str, float] = {}
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
        for key in [k for k, at in self._known.items() if now - at >= self.KNOWN_SECONDS]:
            del self._known[key]
        # Если словарь всё равно распух — оставляем самые свежие записи.
        # Забыть чужую попытку не страшно: перебор упрётся в ограничение
        # на стороне nginx. Съеденная память страшнее.
        if len(self._fails) > self.MAX_KEYS:
            fresh = sorted(self._fails.items(), key=lambda kv: -max(kv[1]))
            self._fails = dict(fresh[: self.MAX_KEYS // 2])
        if len(self._locked) > self.MAX_KEYS:
            fresh = sorted(self._locked.items(), key=lambda kv: -kv[1])
            self._locked = dict(fresh[: self.MAX_KEYS // 2])
        # Удачные входы снаружи не наплодить — для записи нужен верный
        # пароль. Потолок здесь просто для порядка.
        if len(self._known) > self.MAX_KEYS:
            fresh = sorted(self._known.items(), key=lambda kv: -kv[1])
            self._known = dict(fresh[: self.MAX_KEYS // 2])

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

    def _over(self) -> bool:
        return (len(self._fails) >= self.MAX_KEYS or len(self._locked) >= self.MAX_KEYS
                or len(self._known) >= self.MAX_KEYS)

    def locked_for(self, login: str, ip: str) -> int:
        """Сколько секунд ещё нельзя пробовать. 0 — можно."""
        now = time.time()
        self._sweep(now, force=self._over())
        keys = [f"p:{login}|{ip}", f"i:{ip}"]
        # Общая пауза по логину не касается адреса, с которого в этот
        # аккаунт уже входили по-настоящему. Иначе перебор с чужих адресов
        # снова запирал бы снаружи самого владельца — ровно то, от чего
        # счётчики и разделены.
        if f"{login}|{ip}" not in self._known:
            keys.append(f"l:{login}")
        worst = 0
        for key in keys:
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
        self._sweep(now, force=self._over())
        for key, limit, lock in (
                (f"p:{login}|{ip}", self.MAX_PER_PAIR, self.LOCK_SECONDS),
                (f"i:{ip}", self.MAX_PER_IP, self.LOCK_SECONDS),
                (f"l:{login}", self.MAX_PER_LOGIN, self.LOGIN_PAUSE)):
            items = self._clean(key, now)
            items.append(now)
            self._fails[key] = items
            if len(items) >= limit:
                self._locked[key] = now + lock
                self._fails.pop(key, None)

    def is_known(self, login: str, ip: str) -> bool:
        """Входили ли в этот аккаунт с этого адреса с верным паролем
        (и кодом, если он включён) за последние KNOWN_SECONDS."""
        at = self._known.get(f"{login}|{ip}")
        return at is not None and time.time() - at < self.KNOWN_SECONDS

    def note_success(self, login: str, ip: str) -> None:
        # Снимаем только своё: счётчик пары и её блокировку. Общий счётчик
        # логина не трогаем — иначе каждый вход владельца обнулял бы
        # подсчёт чужого перебора, и тот мог бы идти дальше с чистого листа.
        self._fails.pop(f"p:{login}|{ip}", None)
        self._locked.pop(f"p:{login}|{ip}", None)
        self._known[f"{login}|{ip}"] = time.time()

    def reset(self) -> None:
        self._fails.clear()
        self._locked.clear()
        self._known.clear()
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

    def allow(self, key: str, cost: int = 1) -> bool:
        """cost — сколько обращений списать разом.

        Нужно ручкам, которые за один вызов ходят к источникам по многу раз:
        считать их одним обращением значит разрешать вдесятеро больше
        запросов наружу, чем задумано лимитом.
        """
        now = time.time()
        self._sweep(now, force=len(self._hits) >= self.MAX_KEYS)
        cost = max(1, int(cost))
        items = [t for t in self._hits.get(key, []) if now - t < self.period]
        if len(items) + cost > self.limit:
            self._hits[key] = items
            return False
        items.extend([now] * cost)
        self._hits[key] = items
        return True

    def count(self, key: str) -> int:
        """Сколько обращений по ключу в текущем окне — не списывая нового."""
        now = time.time()
        return sum(1 for t in self._hits.get(key, ()) if now - t < self.period)

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
