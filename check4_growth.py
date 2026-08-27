"""Pass 4: what piles up over months and goes off one day.

Mistakes like these are invisible on a test bench in five minutes — they
show themselves after half a year of work. So we check them separately
and deliberately.
"""
import io
import os
import sys
import tempfile
import time
import logging

# The public version's checks run in demonstration mode: there is no
# other here. External video sources are not part of this repository, and
# the only working source is free video (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p4.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass4"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import main, security, store, anime       # noqa: E402

# The output here is in English, while the Windows console lives in cp1251
# by default: the very first arrow or tick knocked the whole run over with
# a UnicodeEncodeError, and the checks broke off halfway, never reaching
# the point. We ask the stream to work in utf-8; where it is utf-8 anyway,
# the line changes nothing.
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
    group("The sign-in attempt counter does not grow forever")
    g = security.LoginGuard()
    for i in range(30_000):
        g.note_failure(f"login-{i}", f"10.0.{i % 255}.{i // 255 % 255}")
    fails, locks = g.size()
    check("no more entries than the ceiling", fails <= g.MAX_KEYS, fails)
    check("no more blocks than the ceiling", locks <= g.MAX_KEYS, locks)
    print(f"       after 30,000 attempts in memory: {fails} entries, {locks} blocks")

    # old entries must leave by themselves
    g2 = security.LoginGuard()
    g2.note_failure("staryy", "1.2.3.4")
    g2._fails["l:staryy"] = [time.time() - g2.WINDOW - 100]
    g2._last_sweep = 0
    g2.locked_for("kto-to", "9.9.9.9")
    check("an expired entry was removed", "l:staryy" not in g2._fails, list(g2._fails))

    # ==================================================================
    group("The rate limiter does not grow forever")
    rl = security.RateLimiter(limit=5, period=60)
    for i in range(30_000):
        rl.allow(f"kluch-{i}")
    check("no more keys than the ceiling", rl.size() <= rl.MAX_KEYS, rl.size())
    print(f"       after 30,000 different keys in memory: {rl.size()}")

    rl2 = security.RateLimiter(limit=5, period=1)
    rl2.allow("a")
    rl2._hits["a"] = [time.time() - 100]
    rl2._last_sweep = 0
    rl2.allow("b")
    check("a stale key was removed", "a" not in rl2._hits, list(rl2._hits))

    # ==================================================================
    group("Guest passes do not pile up")
    gs = main.GuestSessions()
    made = 0
    # Every pass from a separate address: there is now a ceiling on the
    # number of live passes from one address as well, so "all from one" no
    # longer checks what this group intends.
    for i in range(gs.MAX_ALIVE + 50):
        if gs.create(f"10.0.{i // 256}.{i % 256}"):
            made += 1
    check("none are issued beyond the ceiling", made == gs.MAX_ALIVE, made)
    check("exactly the ceiling in memory", gs.count() == gs.MAX_ALIVE, gs.count())

    # Separately: one address cannot take every place and lock the rest out
    solo = main.GuestSessions()
    alone = 0
    for _ in range(solo.MAX_PER_IP + 20):
        if solo.create("203.0.113.7"):
            alone += 1
    check("one address does not take every place", alone == solo.MAX_PER_IP, alone)
    check("a pass is still issued from another address",
          solo.create("203.0.113.8") is not None)
    # expired ones must free their place
    for data in list(gs._items.values()):
        data["expires"] = time.time() - 1
    check("once it expired the counter went back to zero", gs.count() == 0, gs.count())
    check("the place is free again", gs.create() is not None)

    # ==================================================================
    group("The library has a ceiling")
    saved = store.MAX_LIBRARY_PER_USER
    store.MAX_LIBRARY_PER_USER = 20
    try:
        for i in range(20):
            store.save_progress(uid, {"key": f"t{i}", "title": f"Тайтл {i}"})
        check("up to the ceiling records are saved", store.library_count(uid) == 20,
              store.library_count(uid))
        try:
            store.save_progress(uid, {"key": "лишний", "title": "Лишний"})
            check("beyond the ceiling it is rejected", False, "the record went through")
        except store.LibraryFull:
            check("beyond the ceiling it is rejected", True)
        # updating an existing one must always work
        store.save_progress(uid, {"key": "t5", "title": "Тайтл 5", "position": 999})
        row = [x for x in store.library(uid) if x["key"] == "t5"][0]
        check("updating an existing one goes through", row["position"] == 999, row["position"])
        # after a deletion the place is freed
        store.remove_from_library(uid, "t0")
        store.save_progress(uid, {"key": "novyy", "title": "Новый"})
        check("after a deletion the place was freed",
              any(x["key"] == "novyy" for x in store.library(uid)))
    finally:
        store.MAX_LIBRARY_PER_USER = saved

    group("The server's answer on overflow is clear")
    reset()
    c = C()
    login(c, "valera", "Zaliv-Pepel-2026")
    store.MAX_LIBRARY_PER_USER = store.library_count(uid)
    try:
        r = c.post("/api/library/progress", json={"key": "ещё-один", "title": "Ещё"})
        check("code 409, not 500", r.status_code == 409, r.status_code)
        check("there is a hint in the text", "Удалите" in r.text, r.text[:80])
    finally:
        store.MAX_LIBRARY_PER_USER = saved

    # ==================================================================
    group("The watch journal is trimmed")
    saved_log = store.MAX_LOG_PER_USER
    store.MAX_LOG_PER_USER = 30
    try:
        for i in range(50):
            store.log_watch(uid, "k", "Тайтл", "Драма", i, 60)
        n = store.connect().execute(
            "SELECT COUNT(*) AS n FROM watch_log WHERE user_id = ?", (uid,)
        ).fetchone()["n"]
        check("the journal was trimmed to the ceiling", n <= store.MAX_LOG_PER_USER + 1, n)
        # the fresh records must stay, the old ones leave
        first = store.connect().execute(
            "SELECT ep FROM watch_log WHERE user_id = ? ORDER BY id ASC LIMIT 1", (uid,)
        ).fetchone()["ep"]
        check("the fresh ones stayed, the old ones went", first > 0, f"the oldest episode is {first}")
    finally:
        store.MAX_LOG_PER_USER = saved_log

    # ==================================================================
    group("The search cache does not grow forever")
    anime._cache.clear()
    for i in range(anime.CACHE_MAX + 500):
        anime.cache_put(f"k{i}", {"a": i})
    check("the cache is within the ceiling", len(anime._cache) <= anime.CACHE_MAX + 10,
          len(anime._cache))

    # ==================================================================
    group("Expired sessions are removed")
    reset()
    s1 = C()
    login(s1, "valera", "Zaliv-Pepel-2026")
    for _ in range(5):
        cc = C()
        login(cc, "valera", "Zaliv-Pepel-2026")
    before = store.connect().execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    check("there are several sessions", before >= 5, before)
    old = store.now() - security.SESSION_IDLE - 100
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET last_seen = ?", (old,))
    dropped = store.purge_old_sessions()
    after = store.connect().execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    check("the cleanup deleted every expired one", after == 0, f"there were {before}, removed {dropped}")

    group("The cleanup starts by itself")
    src = io.open("api/main.py", encoding="utf-8").read()
    check("there is a background task", "async def housekeeping" in src)
    check("the task is created at start-up", "create_task(housekeeping())" in src)
    check("the task is cancelled at shutdown", "task.cancel()" in src)
    check("an error in the cleanup does not bring the server down", "уборка споткнулась" in src)

    # ==================================================================
    group("The time zone in the year in review")
    reset()
    t = C()
    login(t, "valera", "Zaliv-Pepel-2026")
    check("a shift is accepted", t.get("/api/stats/year?tz=180").status_code == 200)
    check("a zero shift is accepted", t.get("/api/stats/year?tz=0").status_code == 200)
    check("a negative one is accepted", t.get("/api/stats/year?tz=-300").status_code == 200)
    check("an impossible shift is rejected", t.get("/api/stats/year?tz=99999").status_code == 422)
    check("text instead of a number is rejected", t.get("/api/stats/year?tz=abc").status_code == 422)

    # the day is counted by the zone rather than by the server
    store.connect().execute("DELETE FROM watch_log WHERE user_id = ?", (uid,))
    # a record at 23:30 UTC
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
    check("by UTC this is 15 June", "2026-06-15" in d_utc, list(d_utc))
    check("by Israeli time it is already the 16th", "2026-06-16" in d_israel, list(d_israel))
    print("       that is, the calendar cells no longer slide by a day")

    # ==================================================================
    group("Database connections do not breed")
    import threading
    ids = set()

    def touch():
        ids.add(id(store.connect()))

    threads = [threading.Thread(target=touch) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    check("one connection per thread", len(ids) <= 8, len(ids))
    a, b = store.connect(), store.connect()
    check("within one thread the connection is reused", a is b)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"PASS 4 — failed: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("PASS 4 — every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
