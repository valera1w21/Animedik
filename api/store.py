"""Data storage: SQLite.

Why SQLite rather than a cloud database: the data sits on your own
server, no access keys go to the browser, and there is nothing to steal.
One file, which lands whole in the virtual machine's backup.

Every query carrying data goes through parameters (the ? sign) only. A
string is glued into SQL in exactly one place: when adding missing
columns while upgrading the database. There is no other way — a column
name cannot be passed as a parameter in any database — and there the
names are checked against a pattern, and only those listed in a constant
in the code are substituted. That closes SQL injection as a class.
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

# Columns that the first versions did not have. They are added one at a
# time and only if they are not there yet: your database already holds
# data, and recreating it because of a new field is not allowed.
LATER_COLUMNS = [
    # signing in with a code from an app
    ("users", "totp_secret", "TEXT NOT NULL DEFAULT ''"),
    ("users", "totp_on", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "backup_codes", "TEXT NOT NULL DEFAULT ''"),
    # letters about new episodes
    ("users", "email", "TEXT NOT NULL DEFAULT ''"),
    ("users", "mail_new_eps", "INTEGER NOT NULL DEFAULT 0"),
    # Which episode we have already written about. Deliberately separate
    # from total_eps: the bell goes out when the person opens the title,
    # while a letter must go out once per episode whether or not they
    # visited the site. One field for both would mean either repeat
    # letters or a silent bell.
    ("library", "mailed_ep", "INTEGER NOT NULL DEFAULT 0"),
    # The title's Latin name. Needed so the library can show names in
    # the site's language: at the sources they are Russian only.
    ("library", "title_en", "TEXT NOT NULL DEFAULT ''"),
]


def _lock_down(path: str) -> None:
    """Closes the file to outsiders: only the owner may read it.

    The database holds password and session fingerprints. The default
    permissions (644) let any user of the system read it — and inside a
    container that also means any process, should it happen to run under
    another name. We set 600.
    """
    try:
        if os.path.exists(path):
            os.chmod(path, 0o600)
    except OSError:
        # On some file systems changing permissions is impossible.
        # That is no reason to fall over: access is limited by the
        # container anyway.
        pass


def connect() -> sqlite3.Connection:
    """A separate connection per thread: one cannot be shared between them."""
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
    """Adds columns that appeared after the first versions.

    Without this, upgrading the site would require deleting the database
    — that is, losing every library and every watch mark. ALTER TABLE ADD
    COLUMN is cheap in SQLite and does not rewrite the table.

    The only place in the whole file where a piece of SQL is assembled
    from a string. There is no other way: a table name and a column name
    cannot be passed as a parameter (the ? sign) in any database —
    parameters exist for values only. So there are two lines of defence
    here:

      * only what is listed in LATER_COLUMNS above gets substituted —
        that is a constant in the code, and nothing from outside reaches
        it;
      * and every name is still checked against a strict pattern. If
        someone one day decides to build that list from data, the query
        will not run, it will fail with a clear error.
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
    # Journal files appear next to the database — we close those too,
    # otherwise part of the data would be reachable through them.
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
    """The library has hit its ceiling."""


def now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def check_new_user(login: str, password: str, role: str) -> str:
    """Checks everything that can be checked before computing the hash.

    Kept apart from the write itself, because computing the hash takes
    almost half a second of processor time. The web layer computes it in
    a separate thread so as not to stop the whole server for that long —
    and the checks should run earlier and more cheaply.
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
    """Creates an account from a ready hash. Login and role must be checked."""
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
    """Creates an account, computing the hash right here. For the console command."""
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
    """Sets a ready hash. The hash is computed outside — see check_new_user."""
    if not pass_hash:
        raise ValueError("пустой хэш пароля")
    with tx() as conn:
        conn.execute(
            "UPDATE users SET pass_hash = ?, pass_changed = ? WHERE id = ?",
            (pass_hash, now(), int(user_id)),
        )
        # changing the password cuts off every session, other people's included
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))


def set_password(user_id: int, password: str) -> None:
    """Changes the password, computing the hash right here. For the console command."""
    problem = security.password_problem(password)
    if problem:
        raise ValueError(problem)
    set_password_hash(user_id, security.hash_password(password))


def set_profile(user_id: int, *, display_name: str | None = None,
                avatar_color: str | None = None) -> None:
    """Changes the name and the colour of the circle.

    The queries are written out in full, with no string assembly: that
    way it is obvious nothing reaches the SQL except text written in
    advance.
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
    """Saves the settings as one line of JSON.

    The line used to be simply cut down to 4000 characters. Truncated
    JSON is not JSON: at the next read the parse failed, get_settings
    silently returned an empty dict, and the person lost every setting at
    once without learning anything about it. Now an over-long value is
    rejected instead of spoiled.
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
# Sign-in codes and mail
# --------------------------------------------------------------------------
def set_totp(user_id: int, secret: str, on: bool, backup: str = "") -> None:
    """The authenticator app's secret and the backup codes.

    Switching it off erases the secret rather than merely clearing a
    checkbox: otherwise it would go on lying in the database for no
    reason anyone could name.
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
    """Spends a backup code. Each works exactly once."""
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
    """Remembers which episode a letter has already been written about."""
    with tx() as conn:
        conn.execute(
            "UPDATE library SET mailed_ep = ? WHERE user_id = ? AND key = ?",
            (max(0, min(10000, int(episode))), int(user_id), str(key)[:200]),
        )


def mail_subscribers() -> list[sqlite3.Row]:
    """Who needs letters about new episodes at all."""
    return connect().execute(
        "SELECT id, login, display_name, email FROM users"
        " WHERE mail_new_eps = 1 AND email <> '' AND disabled = 0"
    ).fetchall()


def watching_for_mail(user_id: int) -> list[dict]:
    """Titles we watch for the sake of the letters."""
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
# The administrator's announcement
# --------------------------------------------------------------------------
# How many letters fit into an announcement.
#
# The banner hangs over the main page for everyone at once, and a long
# text in it turns into a wall people stop reading on the second day.
# Three hundred letters is about fifty words: "the server restarts at
# 23:00, your library is not going anywhere" and a sentence and a half
# in reserve.
NEWS_MAX = 300


def get_news() -> dict:
    """The announcement in two languages.

    The English text is a separate record. There is nothing to translate
    it automatically with: a dictionary is no help here — the admin
    writes in living language, and running an announcement through
    somebody else's translator means sending them a text that is none of
    their business. So the English version is a separate field, filled in
    by the same person. Not filled in — we show the Russian one: your own
    announcement, even in the wrong language, is more use than emptiness.
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
# Sessions
# --------------------------------------------------------------------------
# How many devices at once. There used to be no ceiling at all: every
# sign-in added a row, and rows were removed only after two weeks had
# passed. A person who signs in from a phone ten times a day would gather
# three hundred live sessions in a month — and any one of them would do
# for signing in.
MAX_SESSIONS_PER_USER = 20


def create_session(user_id: int, token: str, ua_hash: str = "") -> None:
    ts = now()
    with tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (fp, user_id, created_at, last_seen, ua_hash)"
            " VALUES (?,?,?,?,?)",
            (security.token_fingerprint(token), int(user_id), ts, ts, ua_hash),
        )
        # Extra ones are closed starting from those unused the longest.
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND fp NOT IN ("
            "  SELECT fp FROM sessions WHERE user_id = ?"
            "  ORDER BY last_seen DESC LIMIT ?"
            ")",
            (int(user_id), int(user_id), MAX_SESSIONS_PER_USER),
        )


def session_user(token: str) -> sqlite3.Row | None:
    """Returns the user by token, extending the session on the way."""
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
    # we do not write to the database at every sneeze: once in five minutes is enough
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
    """How many sign-ins are still alive.

    We count only those one could sign in with right now. Expired rows
    used to be counted too — the ones that lie in the database until the
    next cleanup but let nobody in any more. The account page said
    "active sign-ins: 7" while one worked: a person pressed "sign out
    everywhere" and could not tell what exactly had closed.
    """
    ts = now()
    row = connect().execute(
        "SELECT COUNT(*) AS n FROM sessions"
        " WHERE user_id = ? AND last_seen > ? AND created_at > ?",
        (int(user_id), ts - security.SESSION_IDLE, ts - security.SESSION_MAX_LIFE),
    ).fetchone()
    return int(row["n"])


def purge_old_sessions() -> int:
    """Removes sessions that can no longer be used to sign in.

    The second condition used to be missing: a session older than the
    maximum lifetime was rejected at the check, but never disappeared
    from the database — the row went on lying there until two weeks of
    idleness passed over it. A quiet growth of the database file out of
    nothing.
    """
    ts = now()
    with tx() as conn:
        cur = conn.execute(
            "DELETE FROM sessions WHERE last_seen < ? OR created_at < ?",
            (ts - security.SESSION_IDLE, ts - security.SESSION_MAX_LIFE),
        )
        return cur.rowcount


# --------------------------------------------------------------------------
# The library
# --------------------------------------------------------------------------
ALLOWED_STATUS = ("watching", "done", "hold", "later", "dropped")

# Ceilings per person. An ordinary one has room to spare, but a script
# posting records in a loop would, with no ceiling, swell the database
# file until the disk ran out.
MAX_LIBRARY_PER_USER = 5_000
MAX_LOG_PER_USER = 20_000


def library_count(user_id: int) -> int:
    row = connect().execute(
        "SELECT COUNT(*) AS n FROM library WHERE user_id = ?", (int(user_id),)
    ).fetchone()
    return int(row["n"])


# What exactly goes to the browser. There used to be an asterisk here,
# and the user's internal number rode out along with the shelf — a field
# the client needs for nothing at all. We list the fields by name: a
# column added to the table will not leak outside by itself.
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


# The same title is named differently at different sources, and the
# discrepancies are of two kinds.
#
# Small ones: "Наруто Ураганные хроники" against "Наруто: Ураганные
# хроники", "ё" in one place and "е" in another. Normalising to a common
# form removes those.
#
# The large one: Russian sources call a title in Russian, an
# English-language one in Latin letters. "Скитальцы" and "Drifters" are
# one anime, but by name they are two different ones, and two cards with
# the same cover appeared in the library one after another. So it is not
# one field that is compared but both: the new record's Russian name
# against the old one's Russian and Latin names, and the other way
# round. Any one of the four matching means it is one title.
_NORM = "lower(replace(replace(replace(replace({0},'ё','е'),':',''),'-',' '),'  ',' '))"


def _same_title_sql() -> str:
    """The condition "this is the same title" — by Russian name or by Latin.

    Assembled by a function rather than written out as a string for
    exactly one reason: four comparisons by hand take a screen and a
    half, and a typo in one of them would silently switch off half the
    check. Only column names from the code are substituted — nothing from
    outside reaches here.
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


# The order of the values: user_id, key, then six for the condition —
# title (twice: compared with the Russian name and with the Latin one),
# then title_en four times (the "not empty" check and the comparison
# itself, twice each).
TWIN_NAMES = (
    "SELECT title, title_en FROM library WHERE user_id = ? AND key <> ?"
    " AND " + _same_title_sql()
)


def _is_cyrillic(text: str) -> bool:
    """Whether the name has Russian letters in it.

    Needed to choose which of a title's two names counts as the main
    one. The Russian one carries more for a Russian site, while the Latin
    one is stored alongside anyway and shown when the site is open in
    English.
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
        # The same title from another source is not a new record.
        #
        # Every source has its own key: "Наруто" from source A and
        # "Наруто" from source C are two different keys and therefore two
        # rows in the library. A person who watched one episode and
        # switched source got two identical "Наруто" in the list, then
        # three.
        #
        # We compare by the name reduced to a common form: case, ё/е and
        # punctuation differ between sources constantly ("Наруто
        # Ураганные хроники" against "Наруто: Ураганные хроники"), while
        # the title is one and the same.
        if title:
            # We carry the Latin name over from the old record if the
            # new one has none. It comes from the catalogue and is not
            # always known: the source does not have that name at all.
            # Losing it while moving to another source means showing the
            # Russian name again to a person reading in English.
            title_en = row[4]
            twin_args = (int(user_id), key, title, title,
                         title_en, title_en, title_en, title_en)

            # We gather names from every twin, not only from our own.
            #
            # Otherwise the link between the languages breaks. An example
            # from life: "Скитальцы" was saved with the Latin name
            # Drifters, then the same title was opened at an
            # English-language source, which knows only "Drifters". The
            # records merged, but the Russian name left with the old row,
            # and the next Russian record no longer found its relatives:
            # two cards in the library again, "Drifters" and "Скитальцы",
            # with one cover.
            #
            # So we take the best of both: the Russian name as the main
            # one, the Latin one alongside.
            for old in conn.execute(TWIN_NAMES, twin_args):
                if not title_en and old["title_en"]:
                    title_en = old["title_en"]
                if old["title"] and _is_cyrillic(old["title"]) and not _is_cyrillic(title):
                    title = old["title"]
            row = row[:3] + (title, title_en) + row[5:]
            # We remove EVERY twin, not the first one that turns up.
            #
            # More than two of them pile up: "Ван-Пис" had time to visit
            # source A, source B and source C twice — four rows with one
            # name. Deleting one left the rest in place, and the library
            # cleaned itself one record per viewing.
            conn.execute(DROP_TWINS, twin_args)
        # We count only when adding something new: updating an existing
        # record does not move the ceiling.
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
    """Sets the cover image without touching anything else.

    A separate function rather than save_progress, for exactly one
    reason: save_progress also updates updated_at, and the library is
    sorted by it. Filling in a missing picture is not the same as
    watching an episode; a title must not jump to the top of the library
    because of an edit like that.
    """
    with tx() as conn:
        conn.execute(
            "UPDATE library SET poster = ? WHERE user_id = ? AND key = ?",
            (str(poster)[:600], int(user_id), str(key)[:200]),
        )


def set_known_eps(user_id: int, key: str, total_eps: int) -> None:
    """Remembers how many episodes the title had last time.

    "A new episode is out" is computed from that number: if the source
    gives more, one is out. We leave updated_at alone here too — an
    episode appearing must not move the title in the library by itself.
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
    """Records that an episode was watched — the year's totals are counted from this."""
    with tx() as conn:
        conn.execute(
            "INSERT INTO watch_log (user_id,key,title,genres,ep,seconds,at)"
            " VALUES (?,?,?,?,?,?,?)",
            (int(user_id), str(key)[:200], str(title)[:300], str(genres)[:300],
             max(0, min(10000, int(ep or 0))),
             max(0, min(24 * 3600, int(seconds or 0))), now()),
        )
        # We keep the journal within reason. Twenty thousand records is
        # more than ten years of daily watching; older than that is not
        # needed even for the year's totals, while the database file
        # would grow forever.
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
    """Counts the year's totals.

    tz_offset_min is the visitor's time zone shift in minutes. It is
    needed because the server lives in UTC while the person may be in
    Israel. Without the shift an episode watched at one in the morning
    would land on yesterday's calendar day, and the cells on the totals
    page would slide by twenty-four hours.
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
