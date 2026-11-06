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
import logging
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

-- Баннер профиля — картинка в шапке карточки профиля и настроек.
-- Хранится так же, как аватар (уже пересобранный сервером JPEG), но в
-- своей таблице, а не столбцом в users: строку users читает почти каждый
-- запрос (SELECT * при проверке входа), и баннер в десятки килобайт
-- таскался бы из базы вместе с ней каждый раз. Аккаунт удалили — баннер
-- уходит вместе с ним (ON DELETE CASCADE).
CREATE TABLE IF NOT EXISTS banners (
  user_id  INTEGER PRIMARY KEY,
  blob     BLOB    NOT NULL,
  at       INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
    # Номер шага времени последнего принятого кода из приложения. Без него
    # один и тот же код пускал повторно, пока не протухнет, — до полутора
    # минут. См. claim_totp_step.
    ("users", "totp_last_step", "INTEGER NOT NULL DEFAULT 0"),
    # Когда тайтл закрыли («досмотрен»). Без этого «Тайтлов закрыто» на
    # странице итогов года считало всё за всё время, хотя страница
    # обещает «с 1 января по сегодня». У записей, закрытых до появления
    # столбца, здесь ноль — для них итоги берут updated_at.
    ("library", "done_at", "INTEGER NOT NULL DEFAULT 0"),
    # Проверка новых серий. latest_ep — номер последней серии, который
    # источник назвал при последней проверке; checked_at — когда это было;
    # new_at — когда заметили серию, которой человек ещё не видел.
    # Отдельно от total_eps намеренно: total_eps — «сколько серий было,
    # когда человек смотрел», и уведомление горит, пока latest_ep больше.
    # Положи фоновая проверка новое число прямо в total_eps — колокольчик
    # гас бы сам, раньше, чем его кто-нибудь увидит.
    ("library", "latest_ep", "INTEGER NOT NULL DEFAULT 0"),
    ("library", "checked_at", "INTEGER NOT NULL DEFAULT 0"),
    ("library", "new_at", "INTEGER NOT NULL DEFAULT 0"),
    # Одна карточка на аниме (fix-21). cid — номер тайтла в справочнике
    # («mal:5114», запасной справочник — «al:…»), а не у источника видео:
    # key у каждого источника свой, и одно и то же аниме, открытое на
    # русском и на английском, лежало двумя строками. Пусто — запись
    # старая или сохранена без справочника. added_at — когда тайтл
    # впервые попал в список (при склейке остаётся самое раннее).
    ("library", "cid", "TEXT NOT NULL DEFAULT ''"),
    ("library", "added_at", "INTEGER NOT NULL DEFAULT 0"),
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
    """Отдельное соединение на поток: делить одно между потоками нельзя.

    Настройки соединения — каждая со своей причиной:
      * foreign_keys — без него SQLite не следит за связями, и удаление
        аккаунта оставило бы его сессии, список и журнал лежать сиротами;
      * busy_timeout — подождать, если базу держит другой процесс (ночная
        копия, консольная команда), а не упасть сразу;
      * secure_delete — удалённое затирается нулями, а не остаётся лежать
        в свободных страницах файла. Без него в копии базы (а копии уходят
        в телеграм) можно было бы найти данные удалённого аккаунта и
        старые секреты входа, хотя «удалено»;
      * trusted_schema=OFF — схема базы не может звать функции приложения:
        подложенный файл базы не исполнит ничего, кроме данных;
      * journal_size_limit — файл журнала (-wal) после большой записи
        ужимается обратно, а не остаётся раздутым навсегда.
    """
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
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("PRAGMA journal_size_limit=67108864")
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


# --------------------------------------------------------------------------
# Адреса обложек
# --------------------------------------------------------------------------
# Две беды, которые чинятся здесь, а не в каждом месте отдельно.
#
# 1. Склеенный адрес. Источники отдают картинку и адресом без схемы:
#    «//static.img-b.example/posters/1.jpg». Старая версия приклеивала его к
#    адресу сайта как есть и получала
#    «https://ru-b.example//static.img-b.example/posters/1.jpg» — такой
#    картинки нет, и в карточке оставалась одна буква. Правильный адрес —
#    «https://static.img-b.example/posters/1.jpg». Такие строки уже лежат
#    в базе у всех, кто успел добавить тайтл, поэтому их чиним и при
#    запуске (fix_stored_posters), и при каждой записи и выдаче.
#
# 2. Заглушка вместо обложки. Справочники для тайтлов без картинки отдают
#    не пустоту, а свою картинку-заглушку («missing», «default»). Браузер
#    её честно показывает — и запасные варианты уже не пробуются. Для нас
#    это то же самое, что «обложки нет».
#
# Вторая половина склеенного адреса должна быть похожа на имя сайта:
# с точкой и буквенным хвостом («static.img-b.example»), и после неё — «/».
# Иначе «https://site.ru//poster.jpg» (просто лишняя черта в пути)
# превратилось бы в «https://poster.jpg».
_GLUED = re.compile(
    r"^(https?:)//[^/\s?#]+//((?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?/[^\s]*)$", re.I)
_PLACEHOLDER = re.compile(
    r"/assets/globals/missing|/missing_(?:original|preview|main|x\d+)\b|"
    r"/cover/(?:small|medium|large|extralarge)/default\.(?:jpg|png)$|"
    r"cdn\.myanimelist\.net/(?:img/sp/icon/apple-touch-icon|images/qm_50)", re.I)


def is_placeholder(url: str) -> bool:
    """Картинка-заглушка справочника вместо настоящей обложки."""
    return bool(url) and bool(_PLACEHOLDER.search(str(url)))


def clean_poster(url: object) -> str:
    """Приводит адрес обложки в порядок. Пустая строка — «обложки нет».

    Идемпотентна: прогнать адрес дважды — то же самое, что один раз.
    Адрес без схемы («//host/путь») получает https. Чужие схемы
    (javascript:, data:) отсекает страница при показе (A.safeUrl), но
    и в базу им незачем: их тоже превращаем в пустоту.
    """
    if not isinstance(url, str):
        return ""
    u = url.strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    for _ in range(3):                  # склеено бывает и дважды
        m = _GLUED.match(u)
        if not m:
            break
        u = m.group(1) + "//" + m.group(2)
    low = u.lower()
    if ":" in low.split("/", 1)[0] and not low.startswith(("http://", "https://")):
        return ""                       # javascript:, data:, blob: и прочее
    if is_placeholder(u):
        return ""
    return u[:600]


def fix_stored_posters(conn: sqlite3.Connection | None = None) -> int:
    """Чинит уже сохранённые обложки. Возвращает, сколько строк исправлено.

    Запускается при каждом старте, но работы у неё бывает только в первый
    раз: отбор по LIKE берёт лишь подозрительные строки (склеенный адрес
    или заглушка), а починенная строка под него больше не попадает.
    Поэтому её можно звать сколько угодно раз — результат один.
    """
    conn = conn or connect()
    rows = conn.execute(
        "SELECT rowid, poster FROM library WHERE poster LIKE 'http%://%//%'"
        " OR poster LIKE '//%' OR poster LIKE '%missing%' OR poster LIKE '%/default.%'"
        " OR poster LIKE '%myanimelist.net/img/sp/icon/%' OR poster LIKE '%qm_50%'"
    ).fetchall()
    fixed = 0
    for r in rows:
        good = clean_poster(r["poster"])
        if good != r["poster"]:
            conn.execute("UPDATE library SET poster = ? WHERE rowid = ?", (good, r["rowid"]))
            fixed += 1
    return fixed


log = logging.getLogger("anime.store")

# Таблицы, где у каждой строки есть хозяин (users.id). Удаление аккаунта
# уносит их строки само (ON DELETE CASCADE) — но только если связи
# включены. Здесь — на случай базы, которую когда-то правили руками или
# старым кодом без foreign_keys: сироты убираются при запуске. Запросы
# записаны целиком, по одному на таблицу, — без сборки строк.
OWNED_TABLES = ("sessions", "library", "watch_log", "banners")
_DROP_ORPHANS = (
    "DELETE FROM sessions WHERE user_id NOT IN (SELECT id FROM users)",
    "DELETE FROM library WHERE user_id NOT IN (SELECT id FROM users)",
    "DELETE FROM watch_log WHERE user_id NOT IN (SELECT id FROM users)",
    "DELETE FROM banners WHERE user_id NOT IN (SELECT id FROM users)",
)


def check_integrity(conn: sqlite3.Connection | None = None) -> str:
    """Быстрая проверка файла базы (quick_check). "ok" — всё цело.

    Повреждение после отключения света иначе всплывало бы случайными
    ошибками в разных местах. Теперь о нём громко скажет журнал при
    запуске — и до того, как поверх битых страниц что-то запишется.
    """
    conn = conn or connect()
    rows = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()]
    return "ok" if rows == ["ok"] else "; ".join(str(r) for r in rows[:5])


def drop_orphans(conn: sqlite3.Connection | None = None) -> int:
    """Убирает строки, чей хозяин уже удалён. Сколько убрано."""
    conn = conn or connect()
    dropped = 0
    for sql in _DROP_ORPHANS:
        dropped += max(0, conn.execute(sql).rowcount)
    return dropped


def seal_plain_secrets(conn: sqlite3.Connection | None = None) -> int:
    """Шифрует секреты входа по коду, сохранённые до шифра (см. security.seal).

    Выполняется при каждом запуске, но работа бывает только в первый раз:
    зашифрованные записи начинаются с security.SEAL_PREFIX и под отбор не
    попадают."""
    conn = conn or connect()
    rows = conn.execute(
        "SELECT id, totp_secret FROM users WHERE totp_secret <> '' AND totp_secret NOT LIKE ?",
        (security.SEAL_PREFIX + "%",)).fetchall()
    for r in rows:
        conn.execute("UPDATE users SET totp_secret = ? WHERE id = ?",
                     (security.seal(r["totp_secret"]), r["id"]))
    return len(rows)


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    health = check_integrity(conn)
    if health != "ok":
        log.error("БАЗА ПОВРЕЖДЕНА (%s): %s. Восстановите её из резервной копии "
                  "(python -m api.admin backup делает такие копии).", DB_PATH, health)
    _add_missing_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_library_cid ON library(user_id, cid)")
    # Старым записям «когда добавлен» неизвестно — лучшее, что о них
    # знаем, это последнее изменение.
    conn.execute("UPDATE library SET added_at = updated_at WHERE added_at = 0")
    orphans = drop_orphans(conn)
    if orphans:
        log.warning("убраны строки удалённых аккаунтов: %d", orphans)
    sealed = seal_plain_secrets(conn)
    if sealed:
        log.info("секреты входа по коду зашифрованы: %d", sealed)
    fix_stored_posters(conn)
    merge_duplicates(conn)
    # Рядом с базой появляются файлы журнала — их закрываем тоже,
    # иначе часть данных окажется доступна через них.
    for suffix in ("", "-wal", "-shm", "-journal"):
        _lock_down(DB_PATH + suffix)


def backup(path: str) -> None:
    """Копия базы средствами самой SQLite (backup API).

    Простое копирование файла (cp, cat) при включённом WAL снимает
    устаревшее состояние: последние записи ещё лежат в anime.db-wal и в
    копию не попадают, а если копировать во время записи — копия бывает
    и вовсе битой. backup API копирует согласованный снимок, не
    останавливая сайт. Файл копии закрыт так же, как сама база (600).
    """
    target = os.path.abspath(path)
    if target == os.path.abspath(DB_PATH):
        raise ValueError("копию нельзя писать поверх самой базы")
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    # Файл копии создаём САМИ и сразу с правами 600. Раньше его создавал
    # sqlite3.connect — с правами по умолчанию (644, «читать можно всем»),
    # а 600 ставились только в конце, после копирования. Всё время
    # копирования (а при обрыве — навсегда) копия с отпечатками паролей и
    # сессий лежала открытой для любого пользователя системы. SQLite уже
    # существующему файлу права не меняет, и свой временный журнал рядом
    # (copy.db-journal) он заводит с теми же правами, что у файла.
    os.close(os.open(target, os.O_WRONLY | os.O_CREAT, 0o600))
    _lock_down(target)
    dst = sqlite3.connect(target)
    try:
        connect().backup(dst)
    finally:
        dst.close()
    _lock_down(target)


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


# Какие поля пользователя читаются при каждом запросе. Раньше здесь была
# звёздочка — и вместе со строкой каждый запрос тащил из базы аватар
# (десяток килобайт картинки), хотя нужен был только ответ «есть ли он».
# Теперь картинка читается одна, отдельно (get_avatar), а сюда приходит
# признак has_avatar.
USER_SELECT = (
    "SELECT id, login, pass_hash, role, display_name, avatar_color, settings,"
    " created_at, pass_changed, disabled, totp_secret, totp_on, backup_codes,"
    " email, mail_new_eps, totp_last_step, avatar_blob IS NOT NULL AS has_avatar"
    " FROM users")


def get_user_by_login(login: str) -> sqlite3.Row | None:
    login = security.normalize_login(login)
    return connect().execute(USER_SELECT + " WHERE login = ?", (login,)).fetchone()


def get_user(user_id: int) -> sqlite3.Row | None:
    return connect().execute(USER_SELECT + " WHERE id = ?", (int(user_id),)).fetchone()


def list_users() -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT id, login, role, display_name, created_at, disabled, avatar_color,"
        " avatar_blob IS NOT NULL AS has_avatar FROM users ORDER BY id"
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


def set_banner(user_id: int, blob: bytes | None) -> None:
    """Баннер профиля. None — убрать."""
    with tx() as conn:
        if blob:
            conn.execute(
                "INSERT INTO banners (user_id, blob, at) VALUES (?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET blob = excluded.blob, at = excluded.at",
                (int(user_id), blob, int(time.time())))
        else:
            conn.execute("DELETE FROM banners WHERE user_id = ?", (int(user_id),))


def get_banner(user_id: int) -> bytes | None:
    row = connect().execute(
        "SELECT blob FROM banners WHERE user_id = ?", (int(user_id),)
    ).fetchone()
    return row["blob"] if row else None


def banner_at(user_id: int) -> int:
    """Когда поставлен баннер (0 — его нет). Сам баннер не читается:
    страница по этому числу понимает и «есть ли», и «сменился ли»."""
    row = connect().execute(
        "SELECT at FROM banners WHERE user_id = ?", (int(user_id),)
    ).fetchone()
    return int(row["at"]) if row else 0


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
def set_totp(user_id: int, secret: str, on: bool, backup: str = "",
             keep_token: str | None = None) -> int:
    """Секрет приложения-аутентификатора и запасные коды.

    Выключение стирает секрет, а не просто снимает галочку: иначе он
    остался бы лежать в базе неизвестно зачем.

    keep_token — при включении закрыть все ОСТАЛЬНЫЕ входы этого человека,
    оставив только тот, из которого включали. Код ставят, чтобы закрыть
    дверь; раньше все уже открытые сессии — в том числе чужая, если пароль
    утёк, — оставались жить ещё две недели, и включённая защита их не
    касалась вовсе. Возвращает, сколько входов закрыто.

    Номер последнего шага при каждой смене обнуляется: он относится
    к старому секрету и для нового ничего не значит.
    """
    closed = 0
    with tx() as conn:
        if on:
            # Секрет кладётся зашифрованным (security.seal): база уходит в
            # копии, а ключ — нет, он только в .env на сервере.
            conn.execute(
                "UPDATE users SET totp_secret = ?, totp_on = 1, backup_codes = ?,"
                " totp_last_step = 0 WHERE id = ?",
                (security.seal(secret), backup, int(user_id)),
            )
            if keep_token is not None:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND fp <> ?",
                    (int(user_id), security.token_fingerprint(keep_token)),
                )
                closed = cur.rowcount
        else:
            conn.execute(
                "UPDATE users SET totp_secret = '', totp_on = 0, backup_codes = '',"
                " totp_last_step = 0 WHERE id = ?",
                (int(user_id),),
            )
    return closed


def totp_secret(user: sqlite3.Row | dict | None) -> str:
    """Секрет входа по коду открытым текстом — или "", если его нет.

    Запись, которая не расшифровывается (сменили SESSION_PEPPER, файл
    базы подправлен руками), — это не «кода нет»: вход по коду у человека
    включён, и пускать без кода нельзя. Поэтому здесь пусто, и код просто
    не подойдёт, а журнал громко скажет, что делать."""
    if not user:
        return ""
    try:
        return security.unseal(user["totp_secret"] or "")
    except security.Unsealable as exc:
        log.error("секрет входа по коду у %s не расшифровался (%s). Если меняли "
                  "SESSION_PEPPER — выключите вход по коду командой "
                  "python -m api.admin 2fa-off ЛОГИН и включите заново.",
                  security.safe_for_log(user["login"]), exc)
        return ""


def claim_totp_step(user_id: int, step: int) -> bool:
    """Отмечает шаг времени как использованный. False — уже был такой или новее.

    Одним запросом с условием, а не «прочитать, сравнить, записать»: два
    входа с одним кодом, пришедшие одновременно, иначе оба прочитали бы
    старое значение и оба прошли бы.
    """
    with tx() as conn:
        cur = conn.execute(
            "UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_last_step < ?",
            (int(step), int(user_id), int(step)),
        )
        return cur.rowcount == 1


def use_backup_code(user_id: int, code_hash: str) -> bool:
    """Тратит запасной код. Каждый работает ровно один раз.

    Чтение и запись — внутри одной транзакции (BEGIN IMMEDIATE). Раньше
    список читался до неё: два входа с одним и тем же кодом, пришедшие
    одновременно, оба видели код в списке, оба его «тратили» — и оба
    входили. Одноразовый код срабатывал дважды. Теперь второй ждёт, пока
    первый допишет, и видит список уже без кода.
    """
    with tx() as conn:
        row = conn.execute(
            "SELECT backup_codes FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
        if not row or not row["backup_codes"]:
            return False
        left = [x for x in row["backup_codes"].split(",") if x]
        if code_hash not in left:
            return False
        left.remove(code_hash)
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
        "SELECT key, source, title, poster, year, total_eps, mailed_ep,"
        " latest_ep, checked_at"
        " FROM library WHERE user_id = ? AND status = 'watching' AND key <> ''"
        " ORDER BY updated_at DESC",
        (int(user_id),),
    ).fetchall()
    return [_with_clean_poster(r) for r in rows]


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


def is_last_admin(user: sqlite3.Row) -> bool:
    """Последний ли это ДЕЙСТВУЮЩИЙ администратор.

    count_admins считает только включённых. Раньше проверку «последний
    администратор» применяли к любому админу, в том числе к уже
    выключенному: при одном живом админе и одном выключенном выключенного
    нельзя было ни удалить, ни «выключить ещё раз» — он сам по себе не
    входил в подсчёт, но запрет всё равно срабатывал. Убрать выключенного
    действующих админов не убавляет, поэтому запрет нужен только для
    включённого.
    """
    if user is None or user["role"] != "admin" or user["disabled"]:
        return False
    return count_admins() <= 1


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
    found = session_lookup(token)
    return found[0] if found else None


def session_lookup(token: str) -> tuple[sqlite3.Row, int] | None:
    """То же, что session_user, но ещё и с моментом создания сессии.

    Момент создания нужен веб-части, чтобы продлевать куку в браузере
    и не продлить её дальше предельного срока жизни сессии.
    """
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
    return user, int(row["created_at"])


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
#
# done_at и latest_ep нужны карточке: «досмотрено три дня назад» и
# «вышла новая серия» видны сразу, без отдельного запроса.
LIBRARY_FIELDS = ("key", "source", "title", "title_en", "poster", "year", "genres",
                  "total_eps", "watched_ep", "position", "status", "updated_at",
                  "done_at", "latest_ep", "cid", "added_at")


def library(user_id: int) -> list[dict]:
    rows = connect().execute(
        "SELECT key, source, title, title_en, poster, year, genres, total_eps,"
        " watched_ep, position, status, updated_at, done_at, latest_ep, cid, added_at"
        " FROM library WHERE user_id = ? ORDER BY updated_at DESC",
        (int(user_id),),
    ).fetchall()
    return [_with_clean_poster(r) for r in rows]


def _with_clean_poster(row: sqlite3.Row) -> dict:
    """Строка списка с обложкой, приведённой в порядок (см. clean_poster).

    База чинится при запуске, но отдаём всё равно через ту же чистку:
    строку мог записать сервер старой версии, пока новый ещё не стартовал.
    """
    out = dict(row)
    if "poster" in out:
        out["poster"] = clean_poster(out["poster"])
    return out


# Один и тот же тайтл у разных источников называется по-разному, и
# расхождений два рода.
#
# Мелкие: «Наруто Ураганные хроники» против «Наруто: Ураганные хроники»,
# где-то «ё», где-то «е», у английских — хвост «(TV)» или «(Dub)». Их
# снимает приведение к общему виду (name_key).
#
# Крупное: русские источники зовут тайтл по-русски, англоязычные —
# латиницей. «Скитальцы» и «Drifters» — одно аниме, но по названиям это
# два разных, и в списке появлялись две карточки подряд с одной и той же
# обложкой. Поэтому сравнивается не одно поле, а оба: русское и латинское
# имя новой записи с обоими именами старой. Совпало любое — это один тайтл.
#
# И главное (fix-21): по названию склейка угадывает, а номер справочника
# (cid) знает точно. Есть он у обеих записей — решает только он: равны —
# одно аниме, разные — разные, как бы они ни назывались (ремейк «Hunter x
# Hunter» и оригинал называются одинаково, а номера у них разные).
_TAGS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_NOT_WORD = re.compile(r"[^0-9a-zа-я]+")
_CID = re.compile(r"^(?:mal|al):\d{1,12}$")

# Язык источника по его имени: ru или en. Заполняет anime.py (store его не
# импортирует — anime сам импортирует store). Нужен склейке: тайтл, открытый
# на русском и на английском источнике, — это ровно тот случай, когда
# названия и число серий у источников расходятся сильнее всего.
SOURCE_LANG: dict[str, str] = {}


def clean_cid(value: object) -> str:
    """Номер тайтла в справочнике в виде «mal:123» / «al:456» или пусто."""
    v = str(value or "").strip().lower()
    return v if _CID.match(v) else ""


def name_key(text: object) -> str:
    """Название для сравнения: строчные, ё → е, без скобок «(TV)», «[Dub]»
    и без знаков. «Jujutsu Kaisen (TV)» и «JUJUTSU KAISEN» — одно и то же."""
    t = str(text or "").lower().replace("ё", "е")
    t = _TAGS.sub(" ", t)
    return " ".join(_NOT_WORD.sub(" ", t).split())


def _names(row) -> set[str]:
    return {k for k in (name_key(row["title"]), name_key(row["title_en"])) if k}


def is_twin(new, old) -> bool:
    """Одно ли это аниме — две записи полки (новая и старая).

      * номер справочника у обеих — решает он один;
      * иначе нужно общее имя (русское или латинское с любой стороны);
      * и подходящий год (TWIN_YEAR_GAP). Число серий сверяется только
        у двух записей одного языка без номера: у русского и английского
        источника серий бывает разное число (один кладёт два сезона одной
        записью), а номер справочника у одной из записей и так говорит,
        что это не догадка по случайному созвучию.
    """
    a, b = clean_cid(new["cid"]), clean_cid(old["cid"])
    if a and b:
        return a == b
    if not (_names(new) & _names(old)):
        return False
    ny, oy = new["year"], old["year"]
    if ny and oy and abs(int(ny) - int(oy)) > TWIN_YEAR_GAP:
        return False
    if a or b or SOURCE_LANG.get(new["source"], "") != SOURCE_LANG.get(old["source"], ""):
        return True
    return same_show(ny, _num(new["total_eps"]), oy, _num(old["total_eps"]))


def _is_cyrillic(text: str) -> bool:
    """Есть ли в названии русские буквы.

    Нужно, чтобы выбрать, какое из двух имён одного тайтла считать
    основным. Русское информативнее для русского сайта, а латинское
    всё равно хранится рядом и показывается, когда сайт открыт
    по-английски.
    """
    return any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in (text or ""))

# Удаляем близнецов поимённо, по ключам, которые прошли проверку
# same_show. Раньше здесь стояло «удалить всё с тем же названием», и
# под нож шли ремейки: «Hunter x Hunter» 1999 года, досмотренный до
# 62 из 62, бесследно исчезал, стоило открыть версию 2011 года на
# другом источнике, — название-то одно.
DROP_TWIN = "DELETE FROM library WHERE user_id = ? AND key = ?"

# Насколько могут расходиться годы у одного и того же тайтла. Источники
# пишут то год анонса, то год первой серии, то год зимнего сезона,
# начавшегося в декабре, — на единицу расходятся постоянно.
TWIN_YEAR_GAP = 1
# Во сколько раз может расходиться число серий. Источники отстают друг
# от друга на серию-две, а у онгоингов и больше; ремейк же обычно другой
# длины вовсе (62 против 148).
TWIN_EPS_RATIO = 1.5


def same_show(new_year: int | None, new_total: int,
              old_year: int | None, old_total: int) -> bool:
    """Одинаковое название — это ещё не один и тот же тайтл.

    Ремейки и перезапуски называются так же, как оригинал: «Hunter x
    Hunter» 1999 и 2011, «Fruits Basket» 2001 и 2019, «Тетрадь смерти»
    и одноимённый фильм. Поэтому кроме названия сверяем то, что известно:
      * год — если он есть у обеих записей, расходиться больше чем на
        TWIN_YEAR_GAP нельзя;
      * число серий — если оно есть у обеих, расходиться больше чем в
        TWIN_EPS_RATIO раз нельзя.
    Неизвестное не мешает: источники часто не отдают ни того, ни другого,
    а склейка записей одного тайтла — то, ради чего всё это затевалось.
    Ошибиться в сторону «разные» безопаснее: будут две карточки вместо
    одной, но ничего не пропадёт.
    """
    if new_year and old_year and abs(int(new_year) - int(old_year)) > TWIN_YEAR_GAP:
        return False
    if new_total > 0 and old_total > 0:
        big, small = max(new_total, old_total), min(new_total, old_total)
        if big > small * TWIN_EPS_RATIO:
            return False
    return True


def _num(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _started(row) -> bool:
    """Начинали ли смотреть запись по-настоящему.

    Страница просмотра кладёт тайтл в список уже при открытии — с первой
    серией и нулевой секундой. Это ещё не «смотрю»: человек мог открыть
    страницу, посмотреть описание и решить отложить. Начатым считаем
    досмотренное, вторую и дальше серию или хоть одну секунду.
    """
    return (row["status"] == "done" or int(row["watched_ep"] or 0) > 1
            or int(row["position"] or 0) > 0)


def fold_twins(base: dict, others) -> dict:
    """Склеивает записи одного аниме в одну (base — самая свежая).

    Правила одни для сохранения и для разовой склейки старых дублей:
      * имя — русское, если оно есть хоть у одной; латинское — рядом
        (в title_en), чтобы карточка умела называться на языке сайта;
      * прогресс — у записи, где досмотрено дальше: её серия, секунда и
        статус (досмотренное не становится «смотрю» оттого, что тайтл
        открыли на другом источнике); «В планы» не отменяет начатое;
      * номер справочника — свой, иначе первый известный;
      * отметка о письмах и последняя известная серия — наибольшие;
      * добавлен — самое раннее.
    """
    out = dict(base)
    for old in others:
        if not out.get("title_en") and old["title_en"]:
            out["title_en"] = old["title_en"]
        if old["title"] and _is_cyrillic(old["title"]) and not _is_cyrillic(out["title"]):
            if not out.get("title_en") and out["title"]:
                out["title_en"] = out["title"]
            out["title"] = old["title"]
        elif out["title"] and _is_cyrillic(out["title"]) and old["title"] \
                and not _is_cyrillic(old["title"]) and not out.get("title_en"):
            out["title_en"] = old["title"]
        old_watched = _num(old["watched_ep"])
        if out["status"] == "later":
            if old["status"] in ALLOWED_STATUS and old["status"] != "later" and _started(old):
                out["watched_ep"], out["position"] = old_watched, _num(old["position"])
                out["status"] = old["status"]
                out["done_at"] = _num(old["done_at"]) if "done_at" in old.keys() else 0
        elif old_watched > _num(out["watched_ep"]):
            out["watched_ep"], out["position"] = old_watched, _num(old["position"])
            if old["status"] in ALLOWED_STATUS:
                out["status"] = old["status"]
                out["done_at"] = _num(old["done_at"]) if "done_at" in old.keys() else 0
        if not out.get("cid") and clean_cid(old["cid"]):
            out["cid"] = clean_cid(old["cid"])
        out["mailed_ep"] = max(_num(out.get("mailed_ep")), _num(old["mailed_ep"]))
        added = _num(old["added_at"]) or _num(old["updated_at"])
        if added and (not out.get("added_at") or added < out["added_at"]):
            out["added_at"] = added
        if not out.get("poster") and old["poster"]:
            out["poster"] = old["poster"]
    return out


def one_cid(new, twins: list, near=()) -> list:
    """Близнецы, которых можно склеить с new, не смешав два разных аниме.

    Дыра была такая. У записи без номера справочника (старая, или
    сохранена с источника, который номера не знает) и без года близнецом
    считается всё с тем же названием — и «Hunter x Hunter» 1999 года
    (mal:136), и ремейк 2011 года (mal:11061). Склейка забирала обоих в
    одну карточку: два РАЗНЫХ аниме становились одним, прогресс одного из
    них пропадал, а сама запись удалялась из списка.

    near — записи с тем же названием, которые близнецами не признаны
    (например, с другим номером). По ним видно, что одноимённых аниме в
    списке несколько.

    Правило: если вокруг (new, близнецы, одноимённые) встречается больше
    одного разного номера справочника, запись без номера — загадка: к
    какому из них она относится, не узнать. Тогда клеим только записи с
    ТЕМ ЖЕ номером, что у new (а у new без номера — только записи без
    номера), остальное не трогаем. Две карточки вместо одной —
    неудобство, а пропавший прогресс — потеря.
    """
    mine = clean_cid(new["cid"])
    cids = {mine} | {clean_cid(t["cid"]) for t in twins} | {clean_cid(o["cid"]) for o in near}
    cids.discard("")
    if len(cids) <= 1:
        return list(twins)
    return [t for t in twins if clean_cid(t["cid"]) == mine]


# Что читается из полки, чтобы найти и склеить близнецов.
TWIN_FIELDS =("SELECT key, cid, source, title, title_en, poster, year, total_eps,"
               " watched_ep, position, status, done_at, mailed_ep, latest_ep, new_at,"
               " checked_at, updated_at, added_at FROM library")


def _rekey_log(conn: sqlite3.Connection, user_id: int, old_keys: list[str],
               key: str, title: str) -> None:
    """Журнал просмотров склеенных записей — под общий ключ и общее имя.

    Иначе итоги года считали бы одно аниме двумя («Магическая битва» и
    «Jujutsu Kaisen (TV)»), а повтор той же серии на другом источнике
    сошёл бы за новый просмотр (см. REWATCH_GAP)."""
    for old in old_keys:
        conn.execute("UPDATE watch_log SET key = ?, title = ? WHERE user_id = ? AND key = ?",
                     (key, title, int(user_id), old))


def merge_duplicates(conn: sqlite3.Connection | None = None) -> int:
    """Разовая склейка дублей, накопившихся до fix-21. Сколько строк ушло.

    Раньше одно аниме, открытое на русском и на английском источнике,
    становилось двумя карточками: ключ у источников разный, а склейка по
    названию спотыкалась о «Jujutsu Kaisen (TV)» против «Магической
    битвы». Здесь те же правила, что при сохранении (is_twin, fold_twins):
    остаётся самая свежая запись (её источник и ключ), прогресс — где
    досмотрено дальше, добавлен — самое раннее. Выполняется один раз:
    отметка в таблице site. Всё — одной транзакцией: оборвётся — база
    останется как была, и при следующем запуске склейка начнётся заново.
    """
    conn = conn or connect()
    if conn.execute("SELECT 1 FROM site WHERE key = 'one_card_v1'").fetchone():
        return 0
    dropped = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        users = [r["user_id"] for r in conn.execute("SELECT DISTINCT user_id FROM library")]
        for uid in users:
            rows = conn.execute(TWIN_FIELDS + " WHERE user_id = ? ORDER BY updated_at DESC",
                                (uid,)).fetchall()
            # Кандидаты — только с общим именем или номером: сравнивать всех
            # со всеми на пяти тысячах записей незачем.
            buckets: dict[str, list[int]] = {}
            for i, r in enumerate(rows):
                for k in _names(r) | ({"#" + clean_cid(r["cid"])} if clean_cid(r["cid"]) else set()):
                    buckets.setdefault(k, []).append(i)
            parent = list(range(len(rows)))

            def find(i: int) -> int:
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i
            for idx in buckets.values():
                for x in range(len(idx)):
                    for y in range(x + 1, len(idx)):
                        i, j = idx[x], idx[y]
                        if find(i) != find(j) and is_twin(rows[i], rows[j]):
                            parent[find(j)] = find(i)
            groups: dict[int, list[int]] = {}
            for i in range(len(rows)):
                groups.setdefault(find(i), []).append(i)
            for members in groups.values():
                if len(members) < 2:
                    continue
                members.sort()                       # по свежести: первая — самая свежая
                base, rest = rows[members[0]], [rows[m] for m in members[1:]]
                # Защита от склейки «по цепочке» разных аниме: каждая
                # запись должна быть близнецом самой свежей напрямую.
                rest = [r for r in rest if is_twin(base, r)]
                # И от «моста»: запись без номера справочника похожа на
                # две записи с РАЗНЫМИ номерами (см. one_cid).
                rest = one_cid(base, rest, [rows[m] for m in members])
                if not rest:
                    continue
                m = fold_twins({k: base[k] for k in base.keys()}, rest)
                conn.execute(
                    "UPDATE library SET cid = ?, title = ?, title_en = ?, poster = ?,"
                    " watched_ep = ?, position = ?, status = ?, done_at = ?, mailed_ep = ?,"
                    " latest_ep = MAX(latest_ep, ?), new_at = MAX(new_at, ?), added_at = ?"
                    " WHERE user_id = ? AND key = ?",
                    (m.get("cid") or "", m["title"], m.get("title_en") or "", m.get("poster") or "",
                     _num(m["watched_ep"]), _num(m["position"]), m["status"],
                     _num(m.get("done_at")) if m["status"] == "done" else 0,
                     _num(m.get("mailed_ep")),
                     max(_num(r["latest_ep"]) for r in rest),
                     max(_num(r["new_at"]) for r in rest),
                     _num(m.get("added_at")), uid, base["key"]))
                for r in rest:
                    conn.execute(DROP_TWIN, (uid, r["key"]))
                _rekey_log(conn, uid, [r["key"] for r in rest] + [base["key"]], base["key"], m["title"])
                dropped += len(rest)
        conn.execute("INSERT OR REPLACE INTO site (key, value, at) VALUES ('one_card_v1', ?, ?)",
                     (str(dropped), now()))
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return dropped


def save_progress(user_id: int, item: dict) -> dict:
    key = str(item.get("key", ""))[:200]
    if not key:
        raise ValueError("нет ключа тайтла")
    status = str(item.get("status", "watching"))
    if status not in ALLOWED_STATUS:
        status = "watching"
    # «Планирую» — это «ещё не начинал». Пришла настоящая отметка о
    # просмотре (серия или секунда) — тайтл уже смотрят, и он уходит из
    # планов в «Не закончил», даже если клиент по ошибке прислал «later».
    if status == "later" and (_num(item.get("watched_ep")) > 0 or _num(item.get("position")) > 0):
        status = "watching"

    def as_int(name: str, lo: int, hi: int) -> int:
        try:
            v = int(item.get(name, 0) or 0)
        except (TypeError, ValueError):
            v = 0
        return max(lo, min(hi, v))

    title = str(item.get("title", ""))[:300]
    stamp = now()
    new = {
        "key": key, "source": str(item.get("source", ""))[:40],
        "cid": clean_cid(item.get("cid")),
        "title": title, "title_en": str(item.get("title_en", ""))[:300],
        "poster": clean_poster(item.get("poster", "")),
        "year": as_int("year", 0, 2200) or None,
        "genres": str(item.get("genres", ""))[:300],
        "total_eps": as_int("total_eps", 0, 10000),
        "watched_ep": as_int("watched_ep", 0, 10000),
        "position": as_int("position", 0, 24 * 3600),
        "status": status,
        # done_at: когда закрыли. Ставится тут для новой записи; для уже
        # существующей решает ON CONFLICT ниже — см. там.
        "done_at": stamp if status == "done" else 0,
        "mailed_ep": 0, "added_at": stamp,
    }
    with tx() as conn:
        # Тот же тайтл с другого источника — не новая запись.
        #
        # Ключ у каждого источника свой: «Наруто» с RU-A и «Наруто»
        # с RU-B — два разных ключа и, значит, две строки в
        # списке. Человек, посмотревший одну серию и переключивший
        # источник (или язык аниме), получал в списке два одинаковых
        # «Наруто», потом три. Близнецы — по номеру справочника, а без
        # него по имени (is_twin); склеиваются по fold_twins: русское имя
        # основным, латинское рядом, прогресс — где досмотрено дальше.
        # «В планы» по записи, которую уже начали смотреть (см. _started),
        # её не трогает: иначе нажатие стёрло бы серию, на которой человек
        # остановился. Только открытую страницу (серия 1, секунда 0) план
        # заменяет.
        #
        # Проверка стоит ДО склейки близнецов. Раньше — после, и при
        # близнеце с другого источника выходило так: склейка уже взяла
        # прогресс близнеца и поменяла статус, проверка «это план?»
        # больше не срабатывала, и прогресс близнеца (серия 3) молча
        # затирал прогресс самой записи (серия 5).
        cur = conn.execute(
            "SELECT status, watched_ep, position, title FROM library"
            " WHERE user_id = ? AND key = ?",
            (int(user_id), key),
        ).fetchone()
        if cur and status == "later" and cur["status"] != "later" and _started(cur):
            return {"key": key, "status": cur["status"], "title": cur["title"]}
        twins = []
        if title or new["cid"]:
            near = []
            for old in conn.execute(TWIN_FIELDS + " WHERE user_id = ? AND key <> ?",
                                    (int(user_id), key)):
                if is_twin(new, old):
                    twins.append(old)
                elif _names(new) & _names(old):
                    near.append(old)
            # Запись без номера справочника не должна склеивать два разных
            # аниме с одним названием (ремейк и оригинал) — см. one_cid.
            twins = one_cid(new, twins, near)
            new = fold_twins(new, twins)
            # Убираем ВСЕХ близнецов, а не первого попавшегося: «Ван-Пис»
            # успевал побывать на четырёх источниках разом.
            for old in twins:
                conn.execute(DROP_TWIN, (int(user_id), old["key"]))
            if twins:
                _rekey_log(conn, int(user_id), [old["key"] for old in twins] + [key],
                           key, new["title"])
        status = new["status"]
        # Считаем только когда добавляем новое: обновление существующей
        # записи потолок не двигает.
        exists = cur is not None
        if not exists:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM library WHERE user_id = ?", (int(user_id),)
            ).fetchone()["n"]
            if count >= MAX_LIBRARY_PER_USER:
                raise LibraryFull(
                    f"В списке уже {MAX_LIBRARY_PER_USER} тайтлов — это предел. "
                    "Удалите что-нибудь ненужное."
                )
        row = (int(user_id), key, new["source"], new["title"], new.get("title_en") or "",
               new.get("poster") or "", new["year"], new["genres"], new["total_eps"],
               _num(new["watched_ep"]), _num(new["position"]), status, stamp,
               _num(new.get("done_at")) if status == "done" else 0,
               new.get("cid") or "", _num(new.get("added_at")) or stamp)
        conn.execute(
            "INSERT INTO library (user_id,key,source,title,title_en,poster,year,genres,"
            " total_eps,watched_ep,position,status,updated_at,done_at,cid,added_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(user_id,key) DO UPDATE SET"
            "  source=excluded.source, title=excluded.title,"
            "  title_en=CASE WHEN excluded.title_en <> '' THEN excluded.title_en"
            "                ELSE library.title_en END,"
            # Пустая обложка старую не затирает. Страница просмотра шлёт
            # то, что знает сама, а знает она не всегда: открыли тайтл по
            # ссылке без &poster= — и найденная раньше картинка пропадала.
            "  poster=CASE WHEN excluded.poster <> '' THEN excluded.poster"
            "             ELSE library.poster END,"
            # Номер справочника тоже: запись из поиска по источнику его
            # не знает, а затирать известный пустым незачем.
            "  cid=CASE WHEN excluded.cid <> '' THEN excluded.cid ELSE library.cid END,"
            "  added_at=CASE WHEN library.added_at > 0 AND library.added_at < excluded.added_at"
            "                THEN library.added_at ELSE excluded.added_at END,"
            "  year=excluded.year, genres=excluded.genres, total_eps=excluded.total_eps,"
            "  watched_ep=excluded.watched_ep, position=excluded.position,"
            "  status=excluded.status, updated_at=excluded.updated_at,"
            # Момент закрытия ставится, когда статус СТАНОВИТСЯ «done», и
            # дальше не двигается: повторная отметка последней серии того
            # же тайтла не должна переносить его в итоги другого года.
            # Ушёл из «done» — время стирается: закроют снова, будет новое.
            # Старые записи без времени (закрыты до появления столбца)
            # сохраняют updated_at — лучшее, что о них известно.
            "  done_at=CASE"
            "    WHEN excluded.status <> 'done' THEN 0"
            "    WHEN library.status = 'done' THEN"
            "      CASE WHEN library.done_at > 0 THEN library.done_at"
            "           ELSE library.updated_at END"
            "    WHEN excluded.done_at > 0 THEN excluded.done_at"
            "    ELSE excluded.updated_at END",
            row,
        )
        # Отметку о письмах переносим с близнецов: иначе первая же
        # проверка сочла бы тайтл новым и промолчала бы до следующей
        # серии (см. mail_new_episodes).
        if _num(new.get("mailed_ep")):
            conn.execute(
                "UPDATE library SET mailed_ep = MAX(mailed_ep, ?)"
                " WHERE user_id = ? AND key = ?",
                (_num(new["mailed_ep"]), int(user_id), key),
            )
    return {"key": key, "status": status, "title": new["title"]}


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
            (clean_poster(poster), int(user_id), str(key)[:200]),
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


# --------------------------------------------------------------------------
# Новые серии
# --------------------------------------------------------------------------
# Досмотренное тоже проверяем — но недолго и всё реже.
#
# Страница просмотра закрывает тайтл, как только досмотрена последняя
# ВЫШЕДШАЯ серия. У онгоинга это значит «догнал», а не «закончил»: через
# неделю выйдет следующая, а тайтл лежит в «Закончил смотреть», и о новой
# серии никто не скажет. Поэтому закрытое недавно проверяется как обычное.
#
# Но и проверять вечно всё, что когда-то досмотрено, нельзя: за год
# набегают сотни тайтлов, которые давно вышли целиком, и каждый раз —
# поход к чужому сайту ради ответа «ничего нового». Сколько серий будет
# всего, в базе не записано, поэтому решает время:
#   * первые DONE_FAST дней после закрытия — каждый заход (серии выходят
#     раз в неделю, и следующая придёт как раз в это окно);
#   * дальше до DONE_SLOW дней — не чаще раза в DONE_SLOW_EVERY: перерыв
#     в выпуске, перенос, вторая половина сезона;
#   * потом — никогда. Тайтл, у которого два месяца ничего не выходило,
#     скорее всего закончился.
DONE_FAST = 10 * 86400
DONE_SLOW = 60 * 86400
DONE_SLOW_EVERY = 6 * 3600

# Когда закрыт тайтл. У записей, закрытых до появления done_at, там ноль —
# для них лучшее, что известно, это updated_at.
_DONE_TS = "(CASE WHEN done_at > 0 THEN done_at ELSE updated_at END)"

# Что проверять: всё, что смотрится, и недавно закрытое по правилам выше.
# Порядок значений: граница «недавно», граница «давно», граница «давно
# проверяли».
CHECK_WHERE = (
    "key <> '' AND (status = 'watching'"
    " OR (status = 'done' AND " + _DONE_TS + " > ?)"
    " OR (status = 'done' AND " + _DONE_TS + " > ? AND checked_at < ?))"
)
CHECK_FIELDS = ("SELECT user_id, key, source, title, total_eps, latest_ep, status,"
                " checked_at FROM library WHERE ")
CHECK_ALL = CHECK_FIELDS + CHECK_WHERE + (
    " AND user_id IN (SELECT id FROM users WHERE disabled = 0)"
    " ORDER BY checked_at ASC, updated_at DESC")
CHECK_ONE = CHECK_FIELDS + "user_id = ? AND " + CHECK_WHERE + " ORDER BY updated_at DESC"


def _check_bounds(at: int) -> tuple[int, int, int]:
    return at - DONE_FAST, at - DONE_SLOW, at - DONE_SLOW_EVERY


def rows_to_check(at: int | None = None) -> list[dict]:
    """Все записи всех людей, у которых пора спросить номер серии.

    Сначала те, что проверяли давнее всего: если за заход успеваем не
    всё, в следующий раз очередь дойдёт до остальных, а не до тех же.
    """
    at = now() if at is None else int(at)
    rows = connect().execute(CHECK_ALL, _check_bounds(at)).fetchall()
    return [dict(r) for r in rows]


def user_rows_to_check(user_id: int, at: int | None = None) -> list[dict]:
    """То же, но для одного человека — для колокольчика."""
    at = now() if at is None else int(at)
    params = (int(user_id), *_check_bounds(at))
    rows = connect().execute(CHECK_ONE, params).fetchall()
    return [dict(r) for r in rows]


def apply_episode_count(user_id: int, key: str, last: int,
                        at: int | None = None) -> str:
    """Записывает, сколько серий источник назвал сейчас. Что вышло — в ответе.

    "" — ничего нового (или не узнали, или эту новинку уже находили),
    "new" — только что замечена серия, которой человек ещё не видел,
    "reopened" — то же, и тайтл был закрыт, а теперь вернулся в
    «Не закончил».

    Правила те же, что были у колокольчика:
      * ноль серий — не новость, а молчание источника;
      * в первый раз (total_eps = 0) только запоминаем: только что
        добавленный тайтл — не новинка;
      * меньше, чем было, — тоже запоминаем: источник перевыложил тайтл
        кусками, и без этого он звенел бы «новой серией» вечно.
    """
    at = now() if at is None else int(at)
    last = max(0, min(10000, int(last or 0)))
    uid, key = int(user_id), str(key)[:200]
    with tx() as conn:
        row = conn.execute(
            "SELECT total_eps, latest_ep, status FROM library WHERE user_id = ? AND key = ?",
            (uid, key)).fetchone()
        if row is None:
            return ""
        if not last:
            conn.execute("UPDATE library SET checked_at = ? WHERE user_id = ? AND key = ?",
                         (at, uid, key))
            return ""
        known = int(row["total_eps"] or 0)
        if known <= 0 or last < known:
            conn.execute(
                "UPDATE library SET total_eps = ?, latest_ep = ?, checked_at = ?, new_at = 0"
                " WHERE user_id = ? AND key = ?", (last, last, at, uid, key))
            return ""
        if last == known:
            conn.execute(
                "UPDATE library SET latest_ep = ?, checked_at = ?, new_at = 0"
                " WHERE user_id = ? AND key = ?", (last, at, uid, key))
            return ""
        # Вышла новая. Время «заметили» ставим один раз на серию: повторная
        # проверка той же новинки не должна делать её снова «только что».
        fresh_now = last > int(row["latest_ep"] or 0)
        conn.execute(
            "UPDATE library SET latest_ep = ?, checked_at = ?,"
            " new_at = CASE WHEN ? OR new_at = 0 THEN ? ELSE new_at END"
            " WHERE user_id = ? AND key = ?",
            (last, at, 1 if fresh_now else 0, at, uid, key))
        if row["status"] == "done":
            # Догнал онгоинг, а он продолжился: тайтл снова незакончен.
            # updated_at не трогаем — порядок в списке это «что смотрел
            # последним», а не «где что вышло». done_at стираем по тем же
            # правилам, что и save_progress: закроют снова — будет новое.
            conn.execute(
                "UPDATE library SET status = 'watching', done_at = 0"
                " WHERE user_id = ? AND key = ? AND status = 'done'", (uid, key))
            return "reopened"
        # "new" — только когда номер вырос с прошлой проверки. Та же
        # новинка, найденная повторно, новостью уже не считается: иначе
        # журнал и всё, что на него смотрит, сообщали бы о ней каждые
        # четверть часа, пока человек не откроет тайтл.
        return "new" if fresh_now else ""


def fresh_updates(user_id: int) -> list[dict]:
    """Тайтлы, у которых вышла серия, которой человек ещё не видел."""
    rows = connect().execute(
        "SELECT key, source, title, title_en, poster, year, genres, watched_ep, position,"
        " total_eps, latest_ep, new_at FROM library"
        " WHERE user_id = ? AND status = 'watching' AND key <> ''"
        " AND total_eps > 0 AND latest_ep > total_eps"
        " ORDER BY new_at DESC, updated_at DESC",
        (int(user_id),),
    ).fetchall()
    return [_with_clean_poster(r) for r in rows]


def library_row(user_id: int, key: str) -> dict | None:
    row = connect().execute(
        "SELECT key, source, title, total_eps, latest_ep, status FROM library"
        " WHERE user_id = ? AND key = ?", (int(user_id), str(key)[:200])).fetchone()
    return dict(row) if row else None


def mark_seen(user_id: int, key: str, last: int) -> None:
    """«Видел, что вышла» — запоминаем номер, уведомление гаснет."""
    last = max(0, min(10000, int(last or 0)))
    with tx() as conn:
        conn.execute(
            "UPDATE library SET total_eps = ?, latest_ep = MAX(latest_ep, ?), new_at = 0"
            " WHERE user_id = ? AND key = ?", (last, last, int(user_id), str(key)[:200]))


def remove_from_library(user_id: int, key: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM library WHERE user_id = ? AND key = ?",
                     (int(user_id), str(key)[:200]))


# Сколько секунд повторная отметка той же серии не считается новым
# просмотром.
#
# Отметка «серия просмотрена» уходит с девяноста процентов, и секунда
# при этом сохраняется тоже — за девяностом. Открыли серию снова (вечером
# дотянуть титры, утром на другом устройстве) — страница начинает с того
# же места, и раньше первый же кадр отмечал серию ещё раз. В итогах года
# одна серия считалась два, три раза, и «часов просмотра» набегало
# больше, чем было на самом деле.
#
# Двенадцать часов — с запасом на вечер и следующее утро: перезагрузки,
# переход с телефона на компьютер, «досмотрю перед работой». А вот
# пересмотр на следующий день или через неделю — это уже настоящий
# второй просмотр, и он посчитается.
REWATCH_GAP = 12 * 3600


def log_watch(user_id: int, key: str, title: str, genres: str,
              ep: int, seconds: int) -> bool:
    """Записывает просмотр серии — из этого потом считаются итоги года.

    Возвращает False, если эту серию этого тайтла уже записали меньше
    REWATCH_GAP назад: тогда это та же самая отметка, а не новый просмотр.
    """
    key = str(key)[:200]
    ep = max(0, min(10000, int(ep or 0)))
    with tx() as conn:
        again = conn.execute(
            "SELECT 1 FROM watch_log WHERE user_id = ? AND key = ? AND ep = ?"
            " AND at > ? LIMIT 1",
            (int(user_id), key, ep, now() - REWATCH_GAP),
        ).fetchone()
        if again:
            return False
        conn.execute(
            "INSERT INTO watch_log (user_id,key,title,genres,ep,seconds,at)"
            " VALUES (?,?,?,?,?,?,?)",
            (int(user_id), key, str(title)[:300], str(genres)[:300], ep,
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
    return True


def year_stats(user_id: int, year_start: int, tz_offset_min: int = 0) -> dict:
    """Считает итоги года.

    tz_offset_min — сдвиг часового пояса посетителя в минутах. Он нужен,
    потому что сервер живёт по UTC, а человек может быть в Израиле. Без
    сдвига серия, просмотренная в час ночи, попадала бы во вчерашний день
    календаря, и клетки на странице итогов ехали бы на сутки.
    """
    conn = connect()
    rows = conn.execute(
        "SELECT key, title, genres, ep, seconds, at FROM watch_log"
        " WHERE user_id = ? AND at >= ?",
        (int(user_id), int(year_start)),
    ).fetchall()
    shift = max(-14 * 60, min(14 * 60, int(tz_offset_min))) * 60
    total_seconds = sum(r["seconds"] for r in rows)
    days: dict[str, int] = {}
    genres: dict[str, int] = {}
    titles: dict[str, int] = {}
    keys_of: dict[str, set] = {}
    for r in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(r["at"] + shift))
        days[day] = days.get(day, 0) + 1
        titles[r["title"]] = titles.get(r["title"], 0) + r["seconds"]
        keys_of.setdefault(r["title"], set()).add(r["key"])
        for g in (r["genres"] or "").split(","):
            g = g.strip()
            if g:
                genres[g] = genres.get(g, 0) + r["seconds"]
    # Закрытые В ЭТОМ ГОДУ, а не все когда-либо: страница пишет
    # «с 1 января по сегодня», и раньше это обещание здесь нарушалось.
    # Записи, закрытые до появления done_at, считаются по updated_at.
    done = conn.execute(
        "SELECT COUNT(*) AS n FROM library WHERE user_id = ? AND status = 'done'"
        " AND (CASE WHEN done_at > 0 THEN done_at ELSE updated_at END) >= ?",
        (int(user_id), int(year_start)),
    ).fetchone()["n"]
    top = sorted(titles.items(), key=lambda kv: -kv[1])[:5]
    return {
        "episodes": len(rows),
        "seconds": total_seconds,
        "days": days,
        "genres": sorted(genres.items(), key=lambda kv: -kv[1])[:8],
        "titles": top,
        # Латинские имена тайтлов года — для английской страницы итогов:
        # {название из журнала: латинское}. Журнал помнит только название,
        # под которым серию отметили, а латинское лежит в полке — ищем
        # по ключу записи, потом по названию. Нет — тайтла в словаре нет,
        # и страница покажет название как есть.
        "titles_en": _titles_en(conn, user_id, [t for t, _ in top], keys_of),
        "finished": int(done),
    }


def _titles_en(conn: sqlite3.Connection, user_id: int, titles: list[str],
               keys_of: dict[str, set]) -> dict[str, str]:
    """Латинские имена для titles из полки человека (см. year_stats)."""
    if not titles:
        return {}
    by_key: dict[str, str] = {}
    by_title: dict[str, str] = {}
    for row in conn.execute(
            "SELECT key, title, title_en FROM library WHERE user_id = ? AND title_en != ''",
            (int(user_id),)):
        by_key[row["key"]] = row["title_en"]
        by_title.setdefault(row["title"], row["title_en"])
    out: dict[str, str] = {}
    for title in titles:
        found = next((by_key[k] for k in sorted(keys_of.get(title) or ()) if k in by_key), "")
        found = found or by_title.get(title, "")
        if found:
            out[title] = found
    return out
