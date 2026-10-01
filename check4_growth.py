"""Проход 4: то, что копится месяцами и однажды выстреливает.

Такие ошибки не видны на тестовом стенде за пять минут — они проявляются
через полгода работы. Поэтому проверяем их отдельно и намеренно.
"""
import io
import os
import sys
import tempfile
import time
import logging

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p4.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass4"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import main, security, store, anime       # noqa: E402

# Вывод здесь на русском, а консоль Windows по умолчанию живёт в cp1251:
# первая же стрелка или галочка роняла весь запуск с UnicodeEncodeError,
# и проверки обрывались на середине, не дойдя до сути. Просим поток
# работать в utf-8; там, где он и так utf-8, строка ничего не меняет.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)


def C():
    return TestClient(main.app)


def login(c, u, p):
    r = c.post("/api/auth/login", json={"login": u, "password": p})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def reset():
    main.login_guard.reset()
    main.api_limit.reset()
    main.search_limit.reset()
    main.guest_limit.reset()
    main.write_limit.reset()


def run():
    store.init()
    reset()
    uid = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")

    # ==================================================================
    group("Счётчик попыток входа не растёт бесконечно")
    g = security.LoginGuard()
    for i in range(30_000):
        g.note_failure(f"login-{i}", f"10.0.{i % 255}.{i // 255 % 255}")
    fails, locks = g.size()
    check("записей не больше потолка", fails <= g.MAX_KEYS, fails)
    check("блокировок не больше потолка", locks <= g.MAX_KEYS, locks)
    print(f"       после 30 000 попыток в памяти: {fails} записей, {locks} блокировок")

    # старые записи должны уходить сами
    g2 = security.LoginGuard()
    g2.note_failure("staryy", "1.2.3.4")
    g2._fails["l:staryy"] = [time.time() - g2.WINDOW - 100]
    g2._last_sweep = 0
    g2.locked_for("kto-to", "9.9.9.9")
    check("просроченная запись убрана", "l:staryy" not in g2._fails, list(g2._fails))

    # ==================================================================
    group("Ограничитель частоты не растёт бесконечно")
    rl = security.RateLimiter(limit=5, period=60)
    for i in range(30_000):
        rl.allow(f"kluch-{i}")
    check("ключей не больше потолка", rl.size() <= rl.MAX_KEYS, rl.size())
    print(f"       после 30 000 разных ключей в памяти: {rl.size()}")

    rl2 = security.RateLimiter(limit=5, period=1)
    rl2.allow("a")
    rl2._hits["a"] = [time.time() - 100]
    rl2._last_sweep = 0
    rl2.allow("b")
    check("протухший ключ убран", "a" not in rl2._hits, list(rl2._hits))

    # ==================================================================
    group("Гостевые пропуска не копятся")
    gs = main.GuestSessions()
    made = 0
    # Каждый пропуск с отдельного адреса: теперь есть ещё и потолок
    # на количество живых пропусков с одного адреса, поэтому «все с одного»
    # больше не проверяет то, что задумано этой группой.
    for i in range(gs.MAX_ALIVE + 50):
        if gs.create(f"10.0.{i // 256}.{i % 256}"):
            made += 1
    check("сверх потолка не выдаются", made == gs.MAX_ALIVE, made)
    check("в памяти ровно потолок", gs.count() == gs.MAX_ALIVE, gs.count())

    # Отдельно: один адрес не может занять все места и запереть остальных
    solo = main.GuestSessions()
    alone = 0
    for _ in range(solo.MAX_PER_IP + 20):
        if solo.create("203.0.113.7"):
            alone += 1
    check("один адрес не забирает все места", alone == solo.MAX_PER_IP, alone)
    check("с другого адреса пропуск ещё выдаётся",
          solo.create("203.0.113.8") is not None)
    # просроченные должны освобождать место
    for data in list(gs._items.values()):
        data["expires"] = time.time() - 1
    check("после истечения счётчик обнулился", gs.count() == 0, gs.count())
    check("место снова свободно", gs.create() is not None)

    # ==================================================================
    group("Список тайтлов имеет потолок")
    saved = store.MAX_LIBRARY_PER_USER
    store.MAX_LIBRARY_PER_USER = 20
    try:
        for i in range(20):
            store.save_progress(uid, {"key": f"t{i}", "title": f"Тайтл {i}"})
        check("до потолка записи сохраняются", store.library_count(uid) == 20,
              store.library_count(uid))
        try:
            store.save_progress(uid, {"key": "лишний", "title": "Лишний"})
            check("сверх потолка отклонено", False, "запись прошла")
        except store.LibraryFull:
            check("сверх потолка отклонено", True)
        # обновление существующей должно работать всегда
        store.save_progress(uid, {"key": "t5", "title": "Тайтл 5", "position": 999})
        row = [x for x in store.library(uid) if x["key"] == "t5"][0]
        check("обновление существующей проходит", row["position"] == 999, row["position"])
        # после удаления место освобождается
        store.remove_from_library(uid, "t0")
        store.save_progress(uid, {"key": "novyy", "title": "Новый"})
        check("после удаления место освободилось",
              any(x["key"] == "novyy" for x in store.library(uid)))
    finally:
        store.MAX_LIBRARY_PER_USER = saved

    group("Ответ сервера при переполнении понятен")
    reset()
    c = C()
    login(c, "valera", "Zaliv-Pepel-2026")
    store.MAX_LIBRARY_PER_USER = store.library_count(uid)
    try:
        r = c.post("/api/library/progress", json={"key": "ещё-один", "title": "Ещё"})
        check("код 409, а не 500", r.status_code == 409, r.status_code)
        check("в тексте есть подсказка", "Удалите" in r.text, r.text[:80])
    finally:
        store.MAX_LIBRARY_PER_USER = saved

    # ==================================================================
    group("Журнал просмотра обрезается")
    saved_log = store.MAX_LOG_PER_USER
    store.MAX_LOG_PER_USER = 30
    try:
        for i in range(50):
            store.log_watch(uid, "k", "Тайтл", "Драма", i, 60)
        n = store.connect().execute(
            "SELECT COUNT(*) AS n FROM watch_log WHERE user_id = ?", (uid,)
        ).fetchone()["n"]
        check("журнал обрезан до потолка", n <= store.MAX_LOG_PER_USER + 1, n)
        # свежие записи должны остаться, старые уйти
        first = store.connect().execute(
            "SELECT ep FROM watch_log WHERE user_id = ? ORDER BY id ASC LIMIT 1", (uid,)
        ).fetchone()["ep"]
        check("остались свежие, ушли старые", first > 0, f"самая старая серия {first}")
    finally:
        store.MAX_LOG_PER_USER = saved_log

    # ==================================================================
    group("Кэш поиска не растёт бесконечно")
    anime._cache.clear()
    for i in range(anime.CACHE_MAX + 500):
        anime.cache_put(f"k{i}", {"a": i})
    check("кэш в пределах потолка", len(anime._cache) <= anime.CACHE_MAX + 10,
          len(anime._cache))

    # ==================================================================
    group("Просроченные сессии убираются")
    reset()
    s1 = C()
    login(s1, "valera", "Zaliv-Pepel-2026")
    for _ in range(5):
        cc = C()
        login(cc, "valera", "Zaliv-Pepel-2026")
    before = store.connect().execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    check("сессий несколько", before >= 5, before)
    old = store.now() - security.SESSION_IDLE - 100
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET last_seen = ?", (old,))
    dropped = store.purge_old_sessions()
    after = store.connect().execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    check("уборка удалила все просроченные", after == 0, f"было {before}, убрано {dropped}")

    group("Уборка запускается сама")
    src = io.open("api/main.py", encoding="utf-8").read()
    check("есть фоновая задача", "async def housekeeping" in src)
    check("задача создаётся при старте", "create_task(housekeeping())" in src)
    check("задача снимается при остановке", "task.cancel()" in src)
    check("ошибка уборки не роняет сервер", "уборка споткнулась" in src)

    # ==================================================================
    group("Часовой пояс в итогах года")
    reset()
    t = C()
    login(t, "valera", "Zaliv-Pepel-2026")
    check("сдвиг принимается", t.get("/api/stats/year?tz=180").status_code == 200)
    check("нулевой сдвиг принимается", t.get("/api/stats/year?tz=0").status_code == 200)
    check("отрицательный принимается", t.get("/api/stats/year?tz=-300").status_code == 200)
    check("невозможный сдвиг отклонён", t.get("/api/stats/year?tz=99999").status_code == 422)
    check("текст вместо числа отклонён", t.get("/api/stats/year?tz=abc").status_code == 422)

    # день считается по поясу, а не по серверу
    store.connect().execute("DELETE FROM watch_log WHERE user_id = ?", (uid,))
    # запись в 23:30 UTC
    mark = int(time.mktime(time.strptime("2026-06-15 23:30", "%Y-%m-%d %H:%M"))) if False else None
    import calendar as _cal
    at_utc = _cal.timegm(time.strptime("2026-06-15 23:30", "%Y-%m-%d %H:%M"))
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO watch_log (user_id,key,title,genres,ep,seconds,at)"
            " VALUES (?,?,?,?,?,?,?)",
            (uid, "tz", "Т", "Драма", 1, 600, at_utc))
    d_utc = store.year_stats(uid, 0, 0)["days"]
    d_israel = store.year_stats(uid, 0, 180)["days"]
    check("по UTC это 15 июня", "2026-06-15" in d_utc, list(d_utc))
    check("по израильскому — уже 16-е", "2026-06-16" in d_israel, list(d_israel))
    print("       то есть клетки календаря больше не едут на сутки")

    # ==================================================================
    group("Соединения с базой не плодятся")
    import threading
    ids = set()

    def touch():
        ids.add(id(store.connect()))

    threads = [threading.Thread(target=touch) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    check("на поток по одному соединению", len(ids) <= 8, len(ids))
    a, b = store.connect(), store.connect()
    check("в одном потоке соединение переиспользуется", a is b)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 4 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 4 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
