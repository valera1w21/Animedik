"""Хранилище данных: SQLite.

Почему SQLite, а не облачная база: данные лежат на вашем сервере, ключей
доступа в браузер не уходит, воровать нечего. Один файл, который целиком
попадает в резервную копию виртуалки.

Все запросы с данными — только через параметры (знак ?). Строка в SQL
склеивается ровно в одном месте: при добавлении недостающих столбцов
во время обновления базы. Иначе никак — имя столбца параметром не
передаётся ни в одной базе, — и там имена сверяются с образцом, а
подставляются только те, что перечислены константой в коде.
Внедрение SQL это закрывает как класс.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from . import security

DB_PATH = os.getenv("DB_PATH", "/data/anime.db")

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  login         TEXT    NOT NULL UNIQUE,
  pass_hash     TEXT    NOT NULL,
  role          TEXT    NOT NULL DEFAULT 'user',   -- 'admin' или 'user'
  display_name  TEXT    NOT NULL DEFAULT '',
  avatar_color  TEXT    NOT NULL DEFAULT '#84CBB6',
  avatar_blob   BLOB,
  settings      TEXT    NOT NULL DEFAULT '{}',
  created_at    INTEGER NOT NULL,
  pass_changed  INTEGER NOT NULL,
  disabled      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
  fp          TEXT PRIMARY KEY,          -- отпечаток токена, не сам токен
  user_id     INTEGER NOT NULL,
  created_at  INTEGER NOT NULL,
  last_seen   INTEGER NOT NULL,
  ua_hash     TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS library (
  user_id     INTEGER NOT NULL,
  key         TEXT    NOT NULL,          -- source:id тайтла
  source      TEXT    NOT NULL DEFAULT '',
  title       TEXT    NOT NULL DEFAULT '',
  poster      TEXT    NOT NULL DEFAULT '',
  year        INTEGER,
  genres      TEXT    NOT NULL DEFAULT '',
  total_eps   INTEGER NOT NULL DEFAULT 0,
  watched_ep  INTEGER NOT NULL DEFAULT 0,
  position    INTEGER NOT NULL DEFAULT 0,   -- секунда, на которой остановились
  status      TEXT    NOT NULL DEFAULT 'watching',
  updated_at  INTEGER NOT NULL,
  PRIMARY KEY (user_id, key),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS watch_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id  INTEGER NOT NULL,
  key      TEXT    NOT NULL,
  title    TEXT    NOT NULL DEFAULT '',
  genres   TEXT    NOT NULL DEFAULT '',
  ep       INTEGER NOT NULL DEFAULT 0,
  seconds  INTEGER NOT NULL DEFAULT 0,
  at       INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_log_user_at ON watch_log(user_id, at);

-- Объявление администратора: строка, которую видят все. Таблица на одну
-- запись, потому что объявление одно; ключ нужен, чтобы не заводить
-- отдельную таблицу под каждую такую мелочь в будущем.
CREATE TABLE IF NOT EXISTS site (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  at    INTEGER NOT NULL DEFAULT 0
);
"""

# Столбцы, которых не было в первых версиях. Добавляются по одному и
# только если их ещё нет: база у вас уже с данными, и пересоздавать её
# из-за нового поля нельзя.
LATER_COLUMNS = [
    # вход по коду из приложения
    ("users", "totp_secret", "TEXT NOT NULL DEFAULT ''"),
    ("users", "totp_on", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "backup_codes", "TEXT NOT NULL DEFAULT ''"),
    # письма о новых сериях
    ("users", "email", "TEXT NOT NULL DEFAULT ''"),
    ("users", "mail_new_eps", "INTEGER NOT NULL DEFAULT 0"),
    # О какой серии уже писали. Отдельно от total_eps намеренно: колокольчик
    # гаснет, когда человек открыл тайтл, а письмо должно уйти один раз на
    # серию независимо от того, заходил он на сайт или нет. Одно поле на
    # двоих означало бы либо повторные письма, либо молчащий колокольчик.
    ("library", "mailed_ep", "INTEGER NOT NULL DEFAULT 0"),
    # Латинское имя тайтла. Нужно, чтобы список умел показывать
    # названия на языке сайта: у источников они только русские.
    ("library", "title_en", "TEXT NOT NULL DEFAULT ''"),
]


def _lock_down(path: str) -> None:
    """Закрывает файл от посторонних: читать может только владелец.

    В базе лежат отпечатки паролей и сессий. Права по умолчанию (644)
    позволяют прочитать её любому пользователю системы — а внутри
    контейнера это ещё и любой процесс, если он вдруг запустится от
    другого имени. Ставим 600.
    """
    try:
        if os.path.exists(path):
            os.chmod(path, 0o600)
    except OSError:
        # На некоторых файловых системах смена прав невозможна.
        # Это не повод падать: доступ и так ограничен контейнером.
        pass


def connect() -> sqlite3.Connection:
    """Отдельное соединение на поток: делить одно между потоками нельзя."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        folder = os.path.dirname(DB_PATH)
        if folder:
            os.makedirs(folder, mode=0o700, exist_ok=True)
            try:
                os.chmod(folder, 0o700)
            except OSError:
                pass
        conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
        _lock_down(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


_PLAIN_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_PLAIN_DECL = re.compile(r"^[A-Za-z0-9_' ]+$")


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Дописывает столбцы, появившиеся после первых версий.

    Без этого обновление сайта требовало бы удалить базу — то есть
    потерять все списки и отметки о просмотре. ALTER TABLE ADD COLUMN
    в SQLite дешёвый и не переписывает таблицу.

    Единственное место во всём файле, где кусок SQL собирается строкой.
    Иначе нельзя: имя таблицы и имя столбца параметром (знаком ?)
    не передаются ни в одной базе — параметры существуют только для
    значений. Поэтому здесь два рубежа:

      * подставляется только то, что перечислено в LATER_COLUMNS выше —
        это константа в коде, снаружи в неё ничего не попадает;
      * и каждое имя всё равно проверяется по строгому образцу. Если
        кто-то однажды решит собрать этот список из данных, запрос
        не выполнится, а упадёт с понятной ошибкой.
    """
    for table, column, decl in LATER_COLUMNS:
        if not (_PLAIN_NAME.match(table) and _PLAIN_NAME.match(column)
                and _PLAIN_DECL.match(decl)):
            raise ValueError(f"недопустимое имя в схеме: {table}.{column}")
        have = {r["name"] for r in conn.execute("PRAGMA table_info(" + table + ")")}
        if column not in have:
            conn.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + decl)


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    # Рядом с базой появляются файлы журнала — их закрываем тоже,
    # иначе часть данных окажется доступна через них.
    for suffix in ("", "-wal", "-shm", "-journal"):
        _lock_down(DB_PATH + suffix)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


class LibraryFull(Exception):
    """Список дошёл до потолка."""


def now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------
# Пользователи
# --------------------------------------------------------------------------
def check_new_user(login: str, password: str, role: str) -> str:
    """Проверяет всё, что можно проверить до подсчёта хэша.

    Отделено от самой записи, потому что подсчёт хэша занимает почти
    полсекунды процессорного времени. Веб-часть считает его в отдельном
    потоке, чтобы не останавливать на это время весь сервер, — а проверки
    должны отработать раньше и дешевле.
    """
    login = security.normalize_login(login)
    problem = security.login_problem(login)
    if problem:
        raise ValueError(problem)
    problem = security.password_problem(password)
    if problem:
        raise ValueError(problem)
    if role not in ("admin", "user"):
        raise ValueError("роль бывает admin или user")
    return login


def create_user_prehashed(login: str, pass_hash: str, role: str = "user",
                          display_name: str = "") -> int:
    """Заводит аккаунт по готовому хэшу. Логин и роль обязаны быть проверены."""
    login = security.normalize_login(login)
    if role not in ("admin", "user"):
        raise ValueError("роль бывает admin или user")
    if not pass_hash:
        raise ValueError("пустой хэш пароля")
    ts = now()
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO users (login, pass_hash, role, display_name, created_at, pass_changed)"
            " VALUES (?,?,?,?,?,?)",
            (login, pass_hash, role, display_name or login, ts, ts),
        )
        return int(cur.lastrowid)


def create_user(login: str, password: str, role: str = "user",
                display_name: str = "") -> int:
    """Заводит аккаунт, считая хэш прямо здесь. Для консольной команды."""
    login = check_new_user(login, password, role)
    return create_user_prehashed(login, security.hash_password(password),
                                 role, display_name)


def get_user_by_login(login: str) -> sqlite3.Row | None:
    login = security.normalize_login(login)
    return connect().execute(
        "SELECT * FROM users WHERE login = ?", (login,)
    ).fetchone()


def get_user(user_id: int) -> sqlite3.Row | None:
    return connect().execute(
        "SELECT * FROM users WHERE id = ?", (int(user_id),)
    ).fetchone()


def list_users() -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT id, login, role, display_name, created_at, disabled FROM users ORDER BY id"
    ).fetchall()


def set_password_hash(user_id: int, pass_hash: str) -> None:
    """Ставит готовый хэш. Хэш считается снаружи — см. check_new_user."""
    if not pass_hash:
        raise ValueError("пустой хэш пароля")
    with tx() as conn:
        conn.execute(
            "UPDATE users SET pass_hash = ?, pass_changed = ? WHERE id = ?",
            (pass_hash, now(), int(user_id)),
        )
        # смена пароля обрывает все сессии, включая чужие
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))


def set_password(user_id: int, password: str) -> None:
    """Меняет пароль, считая хэш прямо здесь. Для консольной команды."""
    problem = security.password_problem(password)
    if problem:
        raise ValueError(problem)
    set_password_hash(user_id, security.hash_password(password))


def set_profile(user_id: int, *, display_name: str | None = None,
                avatar_color: str | None = None) -> None:
    """Меняет имя и цвет кружка.

    Запросы записаны целиком, без сборки строк: так очевидно, что в SQL
    не попадает ничего, кроме заранее написанного текста.
    """
    if display_name is None and avatar_color is None:
        return
    with tx() as conn:
        if display_name is not None:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (display_name[:40], int(user_id)),
            )
        if avatar_color is not None:
            conn.execute(
                "UPDATE users SET avatar_color = ? WHERE id = ?",
                (avatar_color[:9], int(user_id)),
            )


def set_avatar(user_id: int, blob: bytes | None) -> None:
    with tx() as conn:
        conn.execute("UPDATE users SET avatar_blob = ? WHERE id = ?", (blob, int(user_id)))


def get_avatar(user_id: int) -> bytes | None:
    row = connect().execute(
        "SELECT avatar_blob FROM users WHERE id = ?", (int(user_id),)
    ).fetchone()
    return row["avatar_blob"] if row else None


SETTINGS_MAX_CHARS = 4000


def set_settings(user_id: int, data: dict) -> None:
    """Сохраняет настройки одной строкой JSON.

    Раньше строка просто обрезалась до 4000 символов. Обрезанный JSON — это
    не JSON: при следующем чтении разбор падал, get_settings молча возвращал
    пустой словарь, и человек терял все настройки разом, ничего об этом
    не узнав. Теперь слишком длинное значение отклоняется, а не портится.
    """
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > SETTINGS_MAX_CHARS:
        raise ValueError("Настройки не помещаются в отведённое место")
    with tx() as conn:
        conn.execute("UPDATE users SET settings = ? WHERE id = ?",
                     (text, int(user_id)))


def get_settings(user_id: int) -> dict:
    row = connect().execute(
        "SELECT settings FROM users WHERE id = ?", (int(user_id),)
    ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["settings"])
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


# --------------------------------------------------------------------------
# Вход по коду и почта
# --------------------------------------------------------------------------
def set_totp(user_id: int, secret: str, on: bool, backup: str = "") -> None:
    """Секрет приложения-аутентификатора и запасные коды.

    Выключение стирает секрет, а не просто снимает галочку: иначе он
    остался бы лежать в базе неизвестно зачем.
    """
    with tx() as conn:
        if on:
            conn.execute(
                "UPDATE users SET totp_secret = ?, totp_on = 1, backup_codes = ? WHERE id = ?",
                (secret, backup, int(user_id)),
            )
        else:
            conn.execute(
                "UPDATE users SET totp_secret = '', totp_on = 0, backup_codes = '' WHERE id = ?",
                (int(user_id),),
            )


def use_backup_code(user_id: int, code_hash: str) -> bool:
    """Тратит запасной код. Каждый работает ровно один раз."""
    row = connect().execute(
        "SELECT backup_codes FROM users WHERE id = ?", (int(user_id),)
    ).fetchone()
    if not row or not row["backup_codes"]:
        return False
    left = [x for x in row["backup_codes"].split(",") if x]
    if code_hash not in left:
        return False
    left.remove(code_hash)
    with tx() as conn:
        conn.execute("UPDATE users SET backup_codes = ? WHERE id = ?",
                     (",".join(left), int(user_id)))
    return True


def set_mailed_ep(user_id: int, key: str, episode: int) -> None:
    """Запоминает, о какой серии уже написали письмо."""
    with tx() as conn:
        conn.execute(
            "UPDATE library SET mailed_ep = ? WHERE user_id = ? AND key = ?",
            (max(0, min(10000, int(episode))), int(user_id), str(key)[:200]),
        )


def mail_subscribers() -> list[sqlite3.Row]:
    """Кому вообще нужно слать письма о новых сериях."""
    return connect().execute(
        "SELECT id, login, display_name, email FROM users"
        " WHERE mail_new_eps = 1 AND email <> '' AND disabled = 0"
    ).fetchall()


def watching_for_mail(user_id: int) -> list[dict]:
    """Тайтлы, за которыми следим ради писем."""
    rows = connect().execute(
        "SELECT key, source, title, poster, year, total_eps, mailed_ep"
        " FROM library WHERE user_id = ? AND status = 'watching' AND key <> ''"
        " ORDER BY updated_at DESC",
        (int(user_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def set_mail_prefs(user_id: int, email: str | None, want: bool | None) -> None:
    with tx() as conn:
        if email is not None:
            conn.execute("UPDATE users SET email = ? WHERE id = ?",
                         (str(email)[:120], int(user_id)))
        if want is not None:
            conn.execute("UPDATE users SET mail_new_eps = ? WHERE id = ?",
                         (1 if want else 0, int(user_id)))


# --------------------------------------------------------------------------
# Объявление администратора
# --------------------------------------------------------------------------
# Сколько букв помещается в объявление.
#
# Плашка висит поверх главной страницы у всех сразу, и длинный текст в
# ней превращается в стену, которую перестают читать на второй день.
# Триста букв — это примерно пятьдесят слов: «сервер перезапустится в
# 23:00, список никуда не денется» и ещё полтора предложения запаса.
NEWS_MAX = 300


def get_news() -> dict:
    """Объявление на двух языках.

    Английский текст лежит отдельной записью. Автоматически переводить
    нечем: словарь тут не поможет — админ пишет живым языком, а гонять
    объявление через чужой переводчик значит отправлять туда текст,
    который его не касается. Поэтому английская версия — отдельное
    поле, и его заполняет тот же человек. Не заполнил — покажем
    русский: своё объявление, пусть и не на том языке, полезнее пустоты.
    """
    rows = {r["key"]: r for r in connect().execute(
        "SELECT key, value, at FROM site WHERE key IN ('news', 'news_en')"
    )}
    ru = rows.get("news")
    en = rows.get("news_en")
    if not ru or not ru["value"]:
        return {"text": "", "text_en": "", "at": 0}
    return {
        "text": ru["value"],
        "text_en": (en["value"] if en else "") or "",
        "at": int(ru["at"]),
    }


def set_news(text: str, text_en: str = "") -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO site (key, value, at) VALUES ('news', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, at = excluded.at",
            (str(text)[:NEWS_MAX], now()),
        )
        conn.execute(
            "INSERT INTO site (key, value, at) VALUES ('news_en', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, at = excluded.at",
            (str(text_en)[:NEWS_MAX], now()),
        )


def clear_news() -> None:
    with tx() as conn:
        conn.execute("DELETE FROM site WHERE key IN ('news', 'news_en')")


def set_disabled(user_id: int, disabled: bool) -> None:
    with tx() as conn:
        conn.execute("UPDATE users SET disabled = ? WHERE id = ?",
                     (1 if disabled else 0, int(user_id)))
        if disabled:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))


def delete_user(user_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))


def count_admins() -> int:
    row = connect().execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0"
    ).fetchone()
    return int(row["n"])


# --------------------------------------------------------------------------
# Сессии
# --------------------------------------------------------------------------
# Сколько устройств одновременно. Раньше потолка не было совсем: каждый вход
# добавлял строку, а убирались они только по истечении двух недель. Человек,
# который заходит с телефона по десять раз в день, за месяц набирал бы триста
# живых сессий — и любая из них годилась бы для входа.
MAX_SESSIONS_PER_USER = 20


def create_session(user_id: int, token: str, ua_hash: str = "") -> None:
    ts = now()
    with tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (fp, user_id, created_at, last_seen, ua_hash)"
            " VALUES (?,?,?,?,?)",
            (security.token_fingerprint(token), int(user_id), ts, ts, ua_hash),
        )
        # Лишние закрываем, начиная с самых давно не использованных.
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND fp NOT IN ("
            "  SELECT fp FROM sessions WHERE user_id = ?"
            "  ORDER BY last_seen DESC LIMIT ?"
            ")",
            (int(user_id), int(user_id), MAX_SESSIONS_PER_USER),
        )


def session_user(token: str) -> sqlite3.Row | None:
    """Возвращает пользователя по токену, попутно продлевая сессию."""
    if not token:
        return None
    fp = security.token_fingerprint(token)
    conn = connect()
    row = conn.execute("SELECT * FROM sessions WHERE fp = ?", (fp,)).fetchone()
    if not row:
        return None
    ts = now()
    if ts - row["last_seen"] > security.SESSION_IDLE:
        drop_session(token)
        return None
    if ts - row["created_at"] > security.SESSION_MAX_LIFE:
        drop_session(token)
        return None
    user = get_user(row["user_id"])
    if not user or user["disabled"]:
        drop_session(token)
        return None
    # не пишем в базу на каждый чих: раз в пять минут достаточно
    if ts - row["last_seen"] > 300:
        with tx() as c:
            c.execute("UPDATE sessions SET last_seen = ? WHERE fp = ?", (ts, fp))
    return user


def drop_session(token: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM sessions WHERE fp = ?",
                     (security.token_fingerprint(token),))


def drop_all_sessions(user_id: int) -> int:
    with tx() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))
        return cur.rowcount


def count_sessions(user_id: int) -> int:
    """Сколько входов ещё живы.

    Считаем только те, по которым можно войти прямо сейчас. Раньше сюда
    попадали и просроченные строки — те, что лежат в базе до ближайшей
    уборки, но уже никого не пускают. В кабинете было написано
    «активных входов: 7», хотя работал один: человек нажимал «выйти везде»
    и не понимал, что именно закрылось.
    """
    ts = now()
    row = connect().execute(
        "SELECT COUNT(*) AS n FROM sessions"
        " WHERE user_id = ? AND last_seen > ? AND created_at > ?",
        (int(user_id), ts - security.SESSION_IDLE, ts - security.SESSION_MAX_LIFE),
    ).fetchone()
    return int(row["n"])


def purge_old_sessions() -> int:
    """Убирает сессии, по которым войти уже нельзя.

    Второе условие раньше отсутствовало: сессия старше предельного срока
    жизни отклонялась при проверке, но из базы не пропадала никогда —
    строка оставалась лежать, пока по ней не пройдёт двухнедельный
    простой. Тихий рост файла базы на ровном месте.
    """
    ts = now()
    with tx() as conn:
        cur = conn.execute(
            "DELETE FROM sessions WHERE last_seen < ? OR created_at < ?",
            (ts - security.SESSION_IDLE, ts - security.SESSION_MAX_LIFE),
        )
        return cur.rowcount


# --------------------------------------------------------------------------
# Библиотека
# --------------------------------------------------------------------------
ALLOWED_STATUS = ("watching", "done", "hold", "later", "dropped")

# Потолки на человека. Обычному хватает с огромным запасом, а вот скрипт,
# который в цикле шлёт записи, без потолка раздул бы файл базы до отказа диска.
MAX_LIBRARY_PER_USER = 5_000
MAX_LOG_PER_USER = 20_000


def library_count(user_id: int) -> int:
    row = connect().execute(
        "SELECT COUNT(*) AS n FROM library WHERE user_id = ?", (int(user_id),)
    ).fetchone()
    return int(row["n"])


# Что именно уходит в браузер. Раньше здесь стояла звёздочка, и вместе
# с полкой уезжал внутренний номер пользователя — поле, которое клиенту
# не нужно ни для чего. Перечисляем поля поимённо: добавится столбец
# в таблицу — он не утечёт наружу сам собой.
LIBRARY_FIELDS = ("key", "source", "title", "title_en", "poster", "year", "genres",
                  "total_eps", "watched_ep", "position", "status", "updated_at")


def library(user_id: int) -> list[dict]:
    rows = connect().execute(
        "SELECT key, source, title, title_en, poster, year, genres, total_eps,"
        " watched_ep, position, status, updated_at"
        " FROM library WHERE user_id = ? ORDER BY updated_at DESC",
        (int(user_id),),
    ).fetchall()
    return [dict(r) for r in rows]


# Один и тот же тайтл у разных источников называется по-разному, и
# расхождений два рода.
#
# Мелкие: «Наруто Ураганные хроники» против «Наруто: Ураганные хроники»,
# где-то «ё», где-то «е». Их снимает приведение к общему виду.
#
# Крупное: русские источники зовут тайтл по-русски, англоязычный —
# латиницей. «Скитальцы» и «Drifters» — одно аниме, но по названиям это
# два разных, и в списке появлялись две карточки подряд с одной и той же
# обложкой. Поэтому сравнивается не одно поле, а оба: русское имя новой
# записи с русским и латинским именем старой, и наоборот. Совпало любое
# из четырёх — это один тайтл.
_NORM = "lower(replace(replace(replace(replace({0},'ё','е'),':',''),'-',' '),'  ',' '))"


def _same_title_sql() -> str:
    """Условие «это тот же тайтл» — по русскому имени или по латинскому.

    Собирается функцией, а не пишется строкой, ровно по одной причине:
    четыре сравнения вручную занимают полтора экрана, и опечатка в одном
    из них молча выключила бы половину проверки. Подставляются только
    имена столбцов из кода — снаружи сюда не попадает ничего.
    """
    mine, theirs = _NORM.format("?"), _NORM.format("title")
    theirs_en = _NORM.format("title_en")
    return (
        "(" + theirs + " = " + mine
        + " OR (title_en <> '' AND " + theirs_en + " = " + mine + ")"
        + " OR (? <> '' AND " + theirs + " = " + _NORM.format("?") + ")"
        + " OR (? <> '' AND title_en <> '' AND " + theirs_en
        + " = " + _NORM.format("?") + "))"
    )


# Порядок значений: user_id, key, затем шесть по условию —
# title (дважды: сравнение с русским именем и с латинским),
# потом title_en четырежды (проверка «непустое» и само сравнение, дважды).
TWIN_NAMES = (
    "SELECT title, title_en FROM library WHERE user_id = ? AND key <> ?"
    " AND " + _same_title_sql()
)


def _is_cyrillic(text: str) -> bool:
    """Есть ли в названии русские буквы.

    Нужно, чтобы выбрать, какое из двух имён одного тайтла считать
    основным. Русское информативнее для русского сайта, а латинское
    всё равно хранится рядом и показывается, когда сайт открыт
    по-английски.
    """
    return any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in (text or ""))

DROP_TWINS = (
    "DELETE FROM library WHERE user_id = ? AND key <> ? AND " + _same_title_sql()
)


def save_progress(user_id: int, item: dict) -> dict:
    key = str(item.get("key", ""))[:200]
    if not key:
        raise ValueError("нет ключа тайтла")
    status = str(item.get("status", "watching"))
    if status not in ALLOWED_STATUS:
        status = "watching"

    def as_int(name: str, lo: int, hi: int) -> int:
        try:
            v = int(item.get(name, 0) or 0)
        except (TypeError, ValueError):
            v = 0
        return max(lo, min(hi, v))

    title = str(item.get("title", ""))[:300]
    row = (
        int(user_id), key,
        str(item.get("source", ""))[:40],
        title,
        str(item.get("title_en", ""))[:300],
        str(item.get("poster", ""))[:600],
        as_int("year", 0, 2200) or None,
        str(item.get("genres", ""))[:300],
        as_int("total_eps", 0, 10000),
        as_int("watched_ep", 0, 10000),
        as_int("position", 0, 24 * 3600),
        status, now(),
    )
    with tx() as conn:
        # Тот же тайтл с другого источника — не новая запись.
        #
        # Ключ у каждого источника свой: «Наруто» с одного источника и
        # «Наруто» с другого — два разных ключа и, значит, две строки в
        # списке. Человек, посмотревший одну серию и переключивший
        # источник, получал в списке два одинаковых «Наруто», потом три.
        #
        # Сравниваем по названию, приведённому к общему виду: регистр,
        # ё/е и знаки препинания у источников расходятся постоянно
        # («Наруто Ураганные хроники» против «Наруто: Ураганные
        # хроники»), а тайтл при этом один и тот же.
        if title:
            # Латинское имя переносим со старой записи, если в новой его
            # нет. Оно приходит из справочника и известно не всегда: у
            # источника этого имени нет вовсе. Потерять его при переезде
            # на другой источник — значит снова показать русское название
            # человеку, читающему по-английски.
            title_en = row[4]
            twin_args = (int(user_id), key, title, title,
                         title_en, title_en, title_en, title_en)

            # Имена собираем со всех близнецов, а не только со своего.
            #
            # Иначе связь между языками рвётся. Пример из жизни:
            # «Скитальцы» сохранились с латинским именем Drifters, потом
            # тот же тайтл открыли на англоязычном источнике — он знает
            # только «Drifters». Записи слились, но русское имя ушло
            # вместе со старой строкой, и следующая русская запись уже
            # не находила родню: в списке снова две карточки, «Drifters»
            # и «Скитальцы», с одной обложкой.
            #
            # Поэтому берём лучшее из обоих: русское имя как основное,
            # латинское — рядом.
            for old in conn.execute(TWIN_NAMES, twin_args):
                if not title_en and old["title_en"]:
                    title_en = old["title_en"]
                if old["title"] and _is_cyrillic(old["title"]) and not _is_cyrillic(title):
                    title = old["title"]
            row = row[:3] + (title, title_en) + row[5:]
            # Убираем ВСЕХ близнецов, а не первого попавшегося.
            #
            # Их накапливается больше двух: «Ван-Пис» успевал побывать на
            # трёх-четырёх разных источниках — несколько строк с одним
            # названием. Удаление одной оставляло остальные на месте, и
            # список чистился по одной записи за просмотр.
            conn.execute(DROP_TWINS, twin_args)
        # Считаем только когда добавляем новое: обновление существующей
        # записи потолок не двигает.
        exists = conn.execute(
            "SELECT 1 FROM library WHERE user_id = ? AND key = ?",
            (int(user_id), key),
        ).fetchone()
        if not exists:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM library WHERE user_id = ?", (int(user_id),)
            ).fetchone()["n"]
            if count >= MAX_LIBRARY_PER_USER:
                raise LibraryFull(
                    f"В списке уже {MAX_LIBRARY_PER_USER} тайтлов — это предел. "
                    "Удалите что-нибудь ненужное."
                )
        conn.execute(
            "INSERT INTO library (user_id,key,source,title,title_en,poster,year,genres,"
            " total_eps,watched_ep,position,status,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(user_id,key) DO UPDATE SET"
            "  source=excluded.source, title=excluded.title,"
            "  title_en=CASE WHEN excluded.title_en <> '' THEN excluded.title_en"
            "                ELSE library.title_en END,"
            "  poster=excluded.poster,"
            "  year=excluded.year, genres=excluded.genres, total_eps=excluded.total_eps,"
            "  watched_ep=excluded.watched_ep, position=excluded.position,"
            "  status=excluded.status, updated_at=excluded.updated_at",
            row,
        )
    return {"key": key, "status": status}


def set_poster(user_id: int, key: str, poster: str) -> None:
    """Проставляет обложку, не трогая ничего больше.

    Отдельная функция, а не save_progress, ровно по одной причине:
    save_progress обновляет ещё и updated_at, а по нему сортируется список.
    Дописать пропавшую картинку — не то же самое, что посмотреть серию;
    от такой правки тайтл не должен прыгать наверх списка.
    """
    with tx() as conn:
        conn.execute(
            "UPDATE library SET poster = ? WHERE user_id = ? AND key = ?",
            (str(poster)[:600], int(user_id), str(key)[:200]),
        )


def set_known_eps(user_id: int, key: str, total_eps: int) -> None:
    """Запоминает, сколько серий у тайтла было в прошлый раз.

    По этому числу считается «вышла новая серия»: если источник отдаёт
    больше, значит вышла. updated_at тоже не трогаем — выход серии
    не должен двигать тайтл в списке сам по себе.
    """
    with tx() as conn:
        conn.execute(
            "UPDATE library SET total_eps = ? WHERE user_id = ? AND key = ?",
            (max(0, min(10000, int(total_eps))), int(user_id), str(key)[:200]),
        )


def remove_from_library(user_id: int, key: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM library WHERE user_id = ? AND key = ?",
                     (int(user_id), str(key)[:200]))


def log_watch(user_id: int, key: str, title: str, genres: str,
              ep: int, seconds: int) -> None:
    """Записывает просмотр серии — из этого потом считаются итоги года."""
    with tx() as conn:
        conn.execute(
            "INSERT INTO watch_log (user_id,key,title,genres,ep,seconds,at)"
            " VALUES (?,?,?,?,?,?,?)",
            (int(user_id), str(key)[:200], str(title)[:300], str(genres)[:300],
             max(0, min(10000, int(ep or 0))),
             max(0, min(24 * 3600, int(seconds or 0))), now()),
        )
        # Держим журнал в разумных рамках. Двадцать тысяч записей — это
        # больше десяти лет ежедневного просмотра; старее уже не нужно
        # даже для итогов года, а файл базы рос бы вечно.
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM watch_log WHERE user_id = ?", (int(user_id),)
        ).fetchone()["n"]
        if count > MAX_LOG_PER_USER:
            conn.execute(
                "DELETE FROM watch_log WHERE id IN ("
                "  SELECT id FROM watch_log WHERE user_id = ?"
                "  ORDER BY at ASC LIMIT ?"
                ")",
                (int(user_id), count - MAX_LOG_PER_USER),
            )


def year_stats(user_id: int, year_start: int, tz_offset_min: int = 0) -> dict:
    """Считает итоги года.

    tz_offset_min — сдвиг часового пояса посетителя в минутах. Он нужен,
    потому что сервер живёт по UTC, а человек может быть в Израиле. Без
    сдвига серия, просмотренная в час ночи, попадала бы во вчерашний день
    календаря, и клетки на странице итогов ехали бы на сутки.
    """
    conn = connect()
    rows = conn.execute(
        "SELECT title, genres, ep, seconds, at FROM watch_log"
        " WHERE user_id = ? AND at >= ?",
        (int(user_id), int(year_start)),
    ).fetchall()
    shift = max(-14 * 60, min(14 * 60, int(tz_offset_min))) * 60
    total_seconds = sum(r["seconds"] for r in rows)
    days: dict[str, int] = {}
    genres: dict[str, int] = {}
    titles: dict[str, int] = {}
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(r["at"] + shift))
        days[day] = days.get(day, 0) + 1
        titles[r["title"]] = titles.get(r["title"], 0) + r["seconds"]
        for g in (r["genres"] or "").split(","):
            g = g.strip()
            if g:
                genres[g] = genres.get(g, 0) + r["seconds"]
    done = conn.execute(
        "SELECT COUNT(*) AS n FROM library WHERE user_id = ? AND status = 'done'",
        (int(user_id),),
    ).fetchone()["n"]
    return {
        "episodes": len(rows),
        "seconds": total_seconds,
        "days": days,
        "genres": sorted(genres.items(), key=lambda kv: -kv[1])[:8],
        "titles": sorted(titles.items(), key=lambda kv: -kv[1])[:5],
        "finished": int(done),
    }
