"""Проход 32: база данных (аудит серверной части).

Что проверяется:
  * настройки соединения (связи, затирание удалённого, ожидание блокировки);
  * удаление аккаунта не оставляет ни одной строки-сироты, а затёртое
    действительно пропадает из файла базы (и из копий);
  * проверка целостности при запуске и копия через backup API;
  * горячие запросы идут по индексам (EXPLAIN QUERY PLAN);
  * разовая склейка дублей (one_card_v1) на хитрых случаях: ремейки с
    одним названием, «мост» без номера справочника, русское и английское
    имя одного аниме, «В планы» поверх начатого, обрыв посреди склейки;
  * консольные команды 2fa-off и backup.

Сети нет.
"""
import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

WORK = tempfile.mkdtemp()
os.environ["MODE"] = os.environ.get("MODE", "private")
os.environ["DB_PATH"] = os.path.join(WORK, "p32.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["UPDATES_EVERY_MIN"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass32")
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from api import security, store, twofa             # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PASS = "Baza-Dannykh-2026"
FAILS = []
G = [""]


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def one(sql, args=()):
    return store.connect().execute(sql, args).fetchone()


def lib_row(uid, key):
    r = one("SELECT * FROM library WHERE user_id = ? AND key = ?", (uid, key))
    return dict(r) if r else None


def put(uid, key, title, *, cid="", year=None, watched=0, position=0, status="watching",
        source="ru_a", title_en="", total=0, updated=None, added=None):
    """Строка полки напрямую, мимо склейки save_progress — так, как их
    оставили старые версии сайта."""
    now = int(time.time())
    updated = now if updated is None else updated
    store.connect().execute(
        "INSERT INTO library (user_id, key, source, title, title_en, poster, year, genres,"
        " total_eps, watched_ep, position, status, updated_at, done_at, cid, added_at)"
        " VALUES (?,?,?,?,?,'',?,'',?,?,?,?,?,?,?,?)",
        (uid, key, source, title, title_en, year, total, watched, position, status, updated,
         updated if status == "done" else 0, cid, added or updated))


def plan(sql):
    return " | ".join(r[3] for r in store.connect().execute("EXPLAIN QUERY PLAN " + sql).fetchall())


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "hozyain")
    uid = store.get_user_by_login("hozyain")["id"]
    conn = store.connect()

    # ==================================================================
    group("1. Настройки соединения")
    pr = {name: conn.execute("PRAGMA " + name).fetchone()[0] for name in
          ("foreign_keys", "secure_delete", "trusted_schema", "journal_mode",
           "busy_timeout", "journal_size_limit")}
    check("связи включены (foreign_keys)", pr["foreign_keys"] == 1, pr)
    check("удалённое затирается (secure_delete)", pr["secure_delete"] == 1, pr)
    check("схема не зовёт функции приложения (trusted_schema=OFF)", pr["trusted_schema"] == 0, pr)
    check("журнал WAL, ожидание блокировки 5 с, потолок файла журнала",
          pr["journal_mode"] == "wal" and pr["busy_timeout"] == 5000
          and pr["journal_size_limit"] == 64 * 1024 * 1024, pr)
    for table in store.OWNED_TABLES:
        fks = conn.execute("PRAGMA foreign_key_list(" + table + ")").fetchall()
        check(f"{table}: связь с users и ON DELETE CASCADE",
              any(f["table"] == "users" and f["on_delete"] == "CASCADE" for f in fks),
              [dict(f) for f in fks])

    # ==================================================================
    group("2. Удаление аккаунта: ни одной сироты, и данные стёрты из файла")
    victim = store.create_user("udalyaemyy", PASS, "user", "STIRAEMOE-IMYA-777")
    store.create_session(victim, security.new_token())
    store.save_progress(victim, {"key": "ru_a:1", "title": "SEKRETNYY-TAYTL-888",
                                 "status": "watching", "watched_ep": 2})
    store.log_watch(victim, "ru_a:1", "SEKRETNYY-TAYTL-888", "", 2, 100)
    store.set_banner(victim, b"\xff\xd8BANNER-BYTES")
    store.set_avatar(victim, b"\xff\xd8AVATAR-BYTES")
    store.set_settings(victim, {"adult": True})
    store.set_totp(victim, "JBSWY3DPEHPK3PXP", True, "hash1,hash2")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.delete_user(victim)
    left = {t: one("SELECT COUNT(*) AS n FROM " + t + " WHERE user_id = ?", (victim,))["n"]
            for t in store.OWNED_TABLES}
    check("в sessions, library, watch_log, banners ничего не осталось",
          not any(left.values()), left)
    check("проверка связей (foreign_key_check) чистая",
          conn.execute("PRAGMA foreign_key_check").fetchall() == [])
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    path = os.environ["DB_PATH"]
    data = open(path, "rb").read()
    if os.path.exists(path + "-wal"):
        data += open(path + "-wal", "rb").read()
    check("имя, название и картинки удалённого стёрты из самого файла базы",
          not any(m in data for m in (b"STIRAEMOE-IMYA-777", b"SEKRETNYY-TAYTL-888",
                                       b"BANNER-BYTES", b"AVATAR-BYTES")))

    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("INSERT INTO sessions (fp, user_id, created_at, last_seen) VALUES ('sir', 99999, 1, 1)")
    raw.execute("INSERT INTO library (user_id, key, updated_at) VALUES (99999, 'x', 1)")
    raw.execute("INSERT INTO watch_log (user_id, key, at) VALUES (99999, 'x', 1)")
    raw.execute("INSERT INTO banners (user_id, blob, at) VALUES (99999, x'00', 1)")
    raw.close()
    check("сироты от старых версий убираются при запуске", store.drop_orphans() == 4)

    # ==================================================================
    group("3. Целостность и копия базы")
    check("quick_check при запуске: ok", store.check_integrity() == "ok")
    store.save_progress(uid, {"key": "ru_a:fresh", "title": "Свежая запись",
                              "status": "watching", "watched_ep": 1})
    raw_copy = os.path.join(WORK, "cp.db")
    shutil.copy(path, raw_copy)
    in_raw = sqlite3.connect(raw_copy).execute(
        "SELECT COUNT(*) FROM library WHERE key = 'ru_a:fresh'").fetchone()[0]
    print(f"  ~ для сравнения: простое копирование файла видит свежую запись: {bool(in_raw)}"
          " (она ещё в anime.db-wal)")
    copy = os.path.join(WORK, "backup", "copy.db")
    store.backup(copy)
    b = sqlite3.connect(copy)
    check("копия через backup API цела и со свежей записью",
          b.execute("PRAGMA quick_check").fetchone()[0] == "ok"
          and b.execute("SELECT COUNT(*) FROM library WHERE key = 'ru_a:fresh'").fetchone()[0] == 1)
    b.close()
    check("копия закрыта от посторонних (600)",
          stat.S_IMODE(os.stat(copy).st_mode) == 0o600, oct(os.stat(copy).st_mode))
    try:
        store.backup(path)
        check("копию поверх самой базы не пишет", False)
    except ValueError:
        check("копию поверх самой базы не пишет", True)

    # ==================================================================
    group("4. Горячие запросы идут по индексам")
    hot = {
        "сессия по токену (каждый запрос)": ("SELECT * FROM sessions WHERE fp = 'x'", "sessions USING INDEX"),
        "пользователь по номеру (каждый запрос)": (store.USER_SELECT + " WHERE id = 1", "INTEGER PRIMARY KEY"),
        "пользователь по логину (вход)": (store.USER_SELECT + " WHERE login = 'v'", "USING INDEX"),
        "полка человека": ("SELECT key FROM library WHERE user_id = 1 ORDER BY updated_at DESC",
                           "library USING INDEX"),
        "повтор серии в журнале": ("SELECT 1 FROM watch_log WHERE user_id = 1 AND key = 'k' AND ep = 1"
                                   " AND at > 0 LIMIT 1", "idx_log_user_at"),
        "итоги года": ("SELECT title FROM watch_log WHERE user_id = 1 AND at >= 0", "idx_log_user_at"),
        "входы человека": ("SELECT COUNT(*) FROM sessions WHERE user_id = 1 AND last_seen > 0"
                           " AND created_at > 0", "idx_sessions_user"),
        "новые серии человека": (store.CHECK_ONE.replace("?", "0", 1).replace("?", "0"),
                                 "library USING INDEX"),
    }
    for name, (sql, want) in hot.items():
        p = plan(sql)
        check(name, want in p and "SCAN library" not in p and "SCAN watch_log" not in p, p)

    # ==================================================================
    group("5. Разовая склейка дублей: хитрые случаи")
    store.create_user("merge", PASS, "user")
    mid = store.get_user_by_login("merge")["id"]
    t = int(time.time()) - 10_000
    # Ремейк и оригинал с одним названием — и запись без номера посередине.
    put(mid, "a:hxh1999", "Hunter x Hunter", cid="mal:136", year=1999, watched=62, total=62,
        status="done", updated=t + 100)
    put(mid, "b:hxh2011", "Hunter x Hunter", cid="mal:11061", year=2011, watched=30, total=148,
        updated=t + 200)
    put(mid, "c:hxh", "Hunter x Hunter", watched=3, updated=t + 300)
    # Одно аниме по-русски и по-английски, номер общий.
    put(mid, "ru_a:jjk", "Магическая битва", title_en="Jujutsu Kaisen", cid="mal:40748",
        year=2020, watched=5, total=24, updated=t + 400, added=t)
    put(mid, "en_a:jjk", "Jujutsu Kaisen (TV)", source="en_a", cid="mal:40748",
        year=2020, watched=7, total=47, updated=t + 500, added=t + 450)
    store.log_watch(mid, "ru_a:jjk", "Магическая битва", "", 5, 60)
    # Номер общий, названия разные совсем.
    put(mid, "a:aot", "Атака титанов", cid="mal:16498", watched=10, updated=t + 600)
    put(mid, "b:aot", "Shingeki no Kyojin", cid="mal:16498", watched=2, updated=t + 700)
    # Без номера, одно название, годы разные — разные аниме.
    put(mid, "a:hel", "Хеллсинг", year=2001, watched=13, updated=t + 800)
    put(mid, "b:hel", "Хеллсинг", year=2006, watched=4, updated=t + 900)
    # «В планы» свежее, начатое — старее.
    put(mid, "a:op", "Ван-Пис", cid="mal:21", status="later", updated=t + 1000)
    put(mid, "b:op", "One Piece", cid="mal:21", watched=500, position=100, updated=t + 950)
    conn.execute("DELETE FROM site WHERE key = 'one_card_v1'")

    calls = [0]
    real_fold = store.fold_twins

    def broken(*a, **k):
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("обрыв посреди склейки")
        return real_fold(*a, **k)

    before = conn.execute("SELECT COUNT(*) FROM library WHERE user_id = ?", (mid,)).fetchone()[0]
    store.fold_twins = broken
    try:
        store.merge_duplicates()
        check("обрыв посреди склейки — ошибка видна", False)
    except RuntimeError:
        check("обрыв посреди склейки — ошибка видна", True)
    finally:
        store.fold_twins = real_fold
    check("после обрыва база как была (одна транзакция), отметки нет",
          conn.execute("SELECT COUNT(*) FROM library WHERE user_id = ?", (mid,)).fetchone()[0] == before
          and one("SELECT 1 FROM site WHERE key = 'one_card_v1'") is None)

    dropped = store.merge_duplicates()
    keys = {r["key"] for r in conn.execute("SELECT key FROM library WHERE user_id = ?", (mid,))}
    check("ремейк и оригинал не склеены, запись без номера — сама по себе",
          {"a:hxh1999", "b:hxh2011", "c:hxh"} <= keys, sorted(keys))
    check("прогресс оригинала и ремейка цел",
          lib_row(mid, "a:hxh1999")["watched_ep"] == 62 and lib_row(mid, "a:hxh1999")["status"] == "done"
          and lib_row(mid, "b:hxh2011")["watched_ep"] == 30)
    jjk = lib_row(mid, "en_a:jjk")
    check("русское и английское — одна карточка, ключ свежей",
          jjk is not None and "ru_a:jjk" not in keys, sorted(keys))
    check("имя русское, латинское рядом, прогресс дальний, добавлен — раньший",
          jjk and jjk["title"] == "Магическая битва" and jjk["title_en"] == "Jujutsu Kaisen"
          and jjk["watched_ep"] == 7 and jjk["added_at"] == t, jjk)
    check("журнал просмотров переехал под общий ключ",
          one("SELECT COUNT(*) AS n FROM watch_log WHERE user_id = ? AND key = 'en_a:jjk'",
              (mid,))["n"] == 1)
    check("один номер, разные названия — склеены",
          "a:aot" not in keys and lib_row(mid, "b:aot")["watched_ep"] == 10, sorted(keys))
    check("одно название, годы 2001 и 2006 — не склеены", {"a:hel", "b:hel"} <= keys)
    op = lib_row(mid, "a:op")
    check("«В планы» не отменяет начатое: серия 500, «смотрю»",
          op and op["status"] == "watching" and op["watched_ep"] == 500 and "b:op" not in keys, op)
    check("склеено ровно три пары", dropped == 3, dropped)
    check("повторный запуск ничего не делает", store.merge_duplicates() == 0)

    # ==================================================================
    group("6. Сохранение с полки: тот же «мост» и «В планы»")
    store.create_user("save", PASS, "user")
    sid = store.get_user_by_login("save")["id"]
    put(sid, "a:hxh1999", "Hunter x Hunter", cid="mal:136", year=1999, watched=62, total=62,
        status="done", updated=t)
    put(sid, "b:hxh2011", "Hunter x Hunter", cid="mal:11061", year=2011, watched=30, total=148,
        updated=t + 10)
    store.save_progress(sid, {"key": "ru-b:hxh", "title": "Hunter x Hunter", "status": "watching",
                              "watched_ep": 1, "source": "ru_b"})
    keys = {r["key"] for r in conn.execute("SELECT key FROM library WHERE user_id = ?", (sid,))}
    check("запись без номера и года не съела ни оригинал, ни ремейк",
          {"a:hxh1999", "b:hxh2011", "ru-b:hxh"} <= keys
          and lib_row(sid, "a:hxh1999")["watched_ep"] == 62
          and lib_row(sid, "b:hxh2011")["watched_ep"] == 30, sorted(keys))
    store.save_progress(sid, {"key": "en_a:hxh11", "title": "Hunter x Hunter (2011)",
                              "title_en": "Hunter x Hunter", "status": "watching",
                              "watched_ep": 31, "cid": "mal:11061", "source": "en_a"})
    keys = {r["key"] for r in conn.execute("SELECT key FROM library WHERE user_id = ?", (sid,))}
    check("запись с номером склеилась ровно со своим (ремейк), оригинал цел",
          "b:hxh2011" not in keys and "a:hxh1999" in keys
          and lib_row(sid, "en_a:hxh11")["watched_ep"] == 31, sorted(keys))

    put(sid, "src1:test", "Тестовый тайтл", watched=5, position=100, updated=t + 20)
    put(sid, "src2:test", "Тестовый тайтл", watched=3, position=50, updated=t + 30)
    out = store.save_progress(sid, {"key": "src1:test", "title": "Тестовый тайтл", "status": "later"})
    row = lib_row(sid, "src1:test")
    check("«В планы» по начатой записи не затирает её серию прогрессом близнеца",
          row["watched_ep"] == 5 and row["position"] == 100 and row["status"] == "watching"
          and out["status"] == "watching", row)

    # ==================================================================
    group("7. Секреты входа по коду и запасные коды")
    store.create_user("kod", PASS, "user")
    kid = store.get_user_by_login("kod")["id"]
    conn.execute("UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXP', totp_on = 1 WHERE id = ?", (kid,))
    store.init()
    row = store.get_user(kid)
    check("при запуске открытый секрет зашифрован",
          row["totp_secret"].startswith(security.SEAL_PREFIX)
          and store.totp_secret(row) == "JBSWY3DPEHPK3PXP")
    check("повторный запуск не шифрует второй раз", store.seal_plain_secrets() == 0)
    check("признак аватара — без чтения картинки",
          "avatar_blob" not in store.get_user(kid).keys() and "has_avatar" in store.get_user(kid).keys())

    # ==================================================================
    group("8. Консольные команды")
    env = dict(os.environ)
    store.create_session(kid, security.new_token())
    r = subprocess.run([sys.executable, "-m", "api.admin", "2fa-off", "kod"], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=120)
    row = store.get_user(kid)
    check("2fa-off выключает вход по коду и закрывает входы",
          r.returncode == 0 and not row["totp_on"] and not row["totp_secret"]
          and store.count_sessions(kid) == 0, (r.returncode, r.stdout[-200:], r.stderr[-300:]))
    target = os.path.join(WORK, "cli", "night.db")
    r = subprocess.run([sys.executable, "-m", "api.admin", "backup", target], cwd=HERE, env=env,
                       capture_output=True, text=True, timeout=120)
    check("backup пишет согласованную копию",
          r.returncode == 0 and os.path.exists(target)
          and sqlite3.connect(target).execute("PRAGMA quick_check").fetchone()[0] == "ok",
          (r.returncode, r.stderr[-300:]))
    codes = twofa.new_backup_codes()
    store.set_totp(kid, "JBSWY3DPEHPK3PXP", True, ",".join(twofa.hash_backup(c) for c in codes))
    check("запасной код — один раз",
          store.use_backup_code(kid, twofa.hash_backup(codes[0]))
          and not store.use_backup_code(kid, twofa.hash_backup(codes[0])))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 32: НЕ ПРОШЛИ {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 32 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
