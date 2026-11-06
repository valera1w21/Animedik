"""Проход 16: новые серии — колокольчик узнаёт о них сам и быстро.

Что проверяется:

  * свежесть: для проверки новинок список серий берётся не старше
    четверти часа, а обычные страницы по-прежнему живут на трёхчасовом
    кэше;
  * фоновая проверка: один заход спрашивает каждый тайтл один раз, даже
    если он в списке у нескольких человек, и раскладывает ответ по базе —
    колокольчик потом отвечает из базы, не ходя к источнику;
  * онгоинг, который «досмотрели» до последней вышедшей серии, при
    выходе новой возвращается в «Не закончил» и попадает в колокольчик;
  * без спама: досмотренное до конца не звенит, давно закрытое не
    проверяется вовсе, одна и та же новинка не считается новой дважды;
  * бережность к источникам: не больше UPDATES_PARALLEL походов разом,
    не больше заданного числа тайтлов за заход, второй заход не стартует,
    пока идёт первый, ошибка источника не роняет проверку;
  * письма берут свежий ответ фоновой проверки и не спрашивают источник
    второй раз; «я видел» гасит уведомление без похода к источнику.

Источники подменяются: в сеть проверки не ходят.
"""
import asyncio
import logging
import os
import sys
import tempfile
import time

# Как и в check13: проверки написаны под личный режим, в демо-режиме
# настоящих источников нет. Фоновую проверку при этом не выключаем —
# наоборот, проверяем, что она запускается вместе с сервером.
os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p16.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass16")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient           # noqa: E402
from api import anime, mail, main, store            # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Zaliv-Pepel-2026"


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)


class Patch:
    """Временная подмена атрибута: вернётся на место при выходе из with."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


class Ep:
    def __init__(self, n):
        self.ordinal = n
        self.title = ""


class Source:
    """Поддельный источник: сколько серий у какого тайтла и сколько раз спросили."""

    def __init__(self, default=12):
        self.default = default
        self.eps = {}
        self.calls = []
        self.fail = set()
        self.delay = 0.0
        self.now_running = 0
        self.max_running = 0

    async def find_episodes(self, source, key, title):
        self.calls.append((source, key))
        self.now_running += 1
        self.max_running = max(self.max_running, self.now_running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if key in self.fail:
                raise RuntimeError("источник лежит")
            return [Ep(n) for n in range(1, self.eps.get(key, self.default) + 1)]
        finally:
            self.now_running -= 1


def C():
    return TestClient(main.app)


def reset():
    main.login_guard.reset()
    for lim in (main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.pass_limit,
                main.avatar_limit, main.catalog_limit):
        lim.reset()


def login(c, user="hozyain", pwd=PASS):
    r = c.post("/api/auth/login", json={"login": user, "password": pwd, "code": ""})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def user(login_):
    if store.get_user_by_login(login_) is None:
        store.create_user(login_, PASS, "user")
    return store.get_user_by_login(login_)["id"]


def put(uid, key, total, watched=None, status="watching", source="ru_a", title=None):
    store.save_progress(uid, {"key": key, "source": source, "title": title or ("Тайтл " + key),
                              "total_eps": total, "watched_ep": total if watched is None else watched,
                              "status": status})


def row(uid, key):
    return dict(store.connect().execute(
        "SELECT total_eps, latest_ep, status, done_at, checked_at, new_at FROM library"
        " WHERE user_id = ? AND key = ?", (uid, key)).fetchone())


def sql(q, *args):
    with store.tx() as conn:
        conn.execute(q, args)


def wipe():
    sql("DELETE FROM library")


def run():
    store.init()
    hozyain = store.get_user_by_login("hozyain")
    if hozyain is None:
        store.create_user("hozyain", PASS, "admin")
    uid = store.get_user_by_login("hozyain")["id"]
    misha = user("misha")

    # ==================================================================
    group("Свежесть: новинки — по свежему списку, страницы — по кэшу")
    anime._cache.clear()
    have = {"n": 12}

    class FakeAnime:
        incomplete = False

        async def a_get_episodes(self):
            return [Ep(n) for n in range(1, have["n"] + 1)]

    async def fake_find_anime(source, key, title):
        return FakeAnime()

    with Patch(anime, "find_anime", fake_find_anime):
        first = asyncio.run(anime.find_episodes("ru_a", "fresh1", "Т"))
        check("первый список — 12 серий", len(first) == 12, len(first))
        have["n"] = 13
        again = asyncio.run(anime.find_episodes("ru_a", "fresh1", "Т"))
        check("страница по-прежнему берёт кэш", len(again) == 12, len(again))
        young = asyncio.run(anime.fresh_episodes("ru_a", "fresh1", "Т"))
        check("список моложе четверти часа годится и для новинок", len(young) == 12, len(young))

        born, value, ttl = anime._cache["eps:ru_a:fresh1"]
        anime._cache["eps:ru_a:fresh1"] = (born - 20 * 60, value, ttl)
        page = asyncio.run(anime.find_episodes("ru_a", "fresh1", "Т"))
        check("через 20 минут страница всё ещё на трёхчасовом кэше", len(page) == 12, len(page))
        fresh = asyncio.run(anime.fresh_episodes("ru_a", "fresh1", "Т"))
        check("а проверка новинок спросила источник заново и увидела 13-ю",
              len(fresh) == 13, len(fresh))
        after = asyncio.run(anime.find_episodes("ru_a", "fresh1", "Т"))
        check("свежий ответ лёг в общий кэш — страница тоже видит 13-ю",
              len(after) == 13, len(after))
        check("срок свежести — не больше 20 минут", anime.FRESH_TTL <= 20 * 60, anime.FRESH_TTL)
        check("обычный кэш остался трёхчасовым", anime.CACHE_TTL == 3 * 3600, anime.CACHE_TTL)

        # У ru_e серии лежат в самой карточке тайтла: выбрасываем и её.
        anime.cache_put("anime:ru_e:h1", "карточка")
        anime.cache_put("eps:ru_e:h1", [Ep(1)])
        for name in ("anime:ru_e:h1", "eps:ru_e:h1"):
            b, v, t = anime._cache[name]
            anime._cache[name] = (b - 3600, v, t)
        asyncio.run(anime.fresh_episodes("ru_e", "h1", "Т"))
        check("у источника с сериями в карточке карточка тоже освежается",
              anime._cache.get("anime:ru_e:h1", (0, None))[1] != "карточка")
    anime._cache.clear()

    # ==================================================================
    group("Фоновая проверка: один заход")
    wipe()
    src = Source(default=12)
    put(uid, "k1", 12, watched=5)
    put(misha, "k1", 12, watched=12)
    put(uid, "k2", 24, watched=3)
    with Patch(anime, "find_episodes", src.find_episodes):
        got = asyncio.run(main.refresh_updates())
        check("заход прошёл", not got.get("skipped") and got.get("checked") == 2, got)
        check("тайтл у двоих — один вопрос источнику",
              src.calls.count(("ru_a", "k1")) == 1, src.calls)
        check("ничего не вышло — новинок нет", got.get("new") == 0, got)
        check("проверка записана", row(uid, "k1")["checked_at"] > 0 and row(uid, "k1")["latest_ep"] == 12,
              row(uid, "k1"))

        src.eps["k1"] = 13
        src.calls.clear()
        sql("UPDATE library SET checked_at = 0")
        got = asyncio.run(main.refresh_updates())
        check("вышла 13-я — замечена у обоих", got.get("new") == 2, got)
        check("в базе latest_ep = 13, total_eps не тронут",
              row(uid, "k1")["latest_ep"] == 13 and row(uid, "k1")["total_eps"] == 12, row(uid, "k1"))
        check("время новинки записано", row(uid, "k1")["new_at"] > 0, row(uid, "k1"))

        reset()
        a = C()
        login(a)
        src.calls.clear()
        r = a.get("/api/updates").json()
        hit = [x for x in r["items"] if x["key"] == "k1"]
        check("колокольчик видит новинку", len(hit) == 1, r["items"])
        if hit:
            check("с номерами: было 12, стало 13",
                  hit[0]["was"] == 12 and hit[0]["now"] == 13 and hit[0]["fresh"] == 1, hit[0])
        check("и ответил из базы, не спрашивая источник", src.calls == [], src.calls)

        sql("UPDATE library SET checked_at = 0 WHERE key = 'k2'")
        got = asyncio.run(main.refresh_updates())
        check("та же новинка второй раз новостью не считается", got.get("new") == 0, got)

        lib = [x for x in a.get("/api/library").json()["items"] if x["key"] == "k1"][0]
        check("карточка получает latest_ep и подпись источника",
              lib.get("latest_ep") == 13 and lib.get("source_label") == "RU-A", lib)

        # «Я видел» — гаснет, к источнику не ходим.
        src.calls.clear()
        reset()
        r = a.post("/api/updates/seen", json={"key": "k1", "source": "ru_a", "title": "Тайтл k1"})
        check("«видел» отвечает номером новинки", r.status_code == 200 and r.json()["now"] == 13, r.text)
        check("и не ходит к источнику", src.calls == [], src.calls)
        r = a.get("/api/updates").json()
        check("после «видел» уведомление погасло",
              not any(x["key"] == "k1" for x in r["items"]), r["items"])
        check("у второго человека оно горит по-прежнему",
              row(misha, "k1")["latest_ep"] == 13 and row(misha, "k1")["total_eps"] == 12,
              row(misha, "k1"))

    # ==================================================================
    group("Онгоинг «досмотрен» — вышла серия — вернулся в «Не закончил»")
    wipe()
    src = Source(default=12)
    put(uid, "ong", 12, status="done")
    check("закрыт и время закрытия есть", row(uid, "ong")["status"] == "done"
          and row(uid, "ong")["done_at"] > 0, row(uid, "ong"))
    with Patch(anime, "find_episodes", src.find_episodes):
        got = asyncio.run(main.refresh_updates())
        check("недавно закрытое проверяется", ("ru_a", "ong") in src.calls, src.calls)
        check("та же серия — остаётся закрытым", row(uid, "ong")["status"] == "done", row(uid, "ong"))

        src.eps["ong"] = 13
        sql("UPDATE library SET checked_at = 0")
        got = asyncio.run(main.refresh_updates())
        r_ = row(uid, "ong")
        check("вышла 13-я — тайтл снова «смотрю»", r_["status"] == "watching", r_)
        check("время закрытия стёрто", r_["done_at"] == 0, r_)
        check("заход сообщил о возвращении", got.get("reopened") == 1, got)
        reset()
        a = C()
        login(a)
        r = a.get("/api/updates").json()
        check("и он в колокольчике", any(x["key"] == "ong" and x["now"] == 13 for x in r["items"]),
              r["items"])
        lib = {x["key"]: x for x in a.get("/api/library").json()["items"]}
        check("в списке — среди незаконченных", lib["ong"]["status"] == "watching", lib["ong"])

    # ==================================================================
    group("Без спама: давно закрытое не проверяется вечно")
    wipe()
    src = Source(default=20)
    now = store.now()
    put(uid, "old", 12, status="done")
    put(uid, "mid", 12, status="done")
    put(uid, "mid2", 12, status="done")
    put(uid, "cold", 12, status="hold")
    sql("UPDATE library SET done_at = ? WHERE key = 'old'", now - 100 * 86400)
    sql("UPDATE library SET done_at = ?, checked_at = ? WHERE key = 'mid'",
        now - 30 * 86400, now - 3600)
    sql("UPDATE library SET done_at = ?, checked_at = ? WHERE key = 'mid2'",
        now - 30 * 86400, now - 7 * 3600)
    with Patch(anime, "find_episodes", src.find_episodes):
        asyncio.run(main.refresh_updates())
    asked = [k for _, k in src.calls]
    check("закрытое 100 дней назад не спрашиваем", "old" not in asked, asked)
    check("закрытое месяц назад и проверенное час назад — тоже", "mid" not in asked, asked)
    check("закрытое месяц назад и не проверенное 7 часов — спрашиваем", "mid2" in asked, asked)
    check("отложенное не проверяем", "cold" not in asked, asked)
    check("давно закрытое осталось закрытым", row(uid, "old")["status"] == "done", row(uid, "old"))

    # Только что добавленное — не новинка; уменьшение запоминается.
    wipe()
    src = Source(default=12)
    put(uid, "zero", 0, watched=1)
    put(uid, "less", 30, watched=2)
    with Patch(anime, "find_episodes", src.find_episodes):
        got = asyncio.run(main.refresh_updates())
    check("только что добавленный не звенит", got.get("new") == 0 and row(uid, "zero")["total_eps"] == 12,
          (got, row(uid, "zero")))
    check("источник отдал меньше — число запомнено, не звенит",
          row(uid, "less")["total_eps"] == 12, row(uid, "less"))

    # ==================================================================
    group("Бережно к источникам")
    wipe()
    src = Source(default=5)
    src.delay = 0.03
    for n in range(12):
        put(uid, "p%02d" % n, 5, watched=1)
    with Patch(anime, "find_episodes", src.find_episodes):
        got = asyncio.run(main.refresh_updates(limit=5))
        check("за заход — не больше заданного числа тайтлов", len(src.calls) == 5, len(src.calls))
        check("разом — не больше UPDATES_PARALLEL",
              src.max_running <= main.UPDATES_PARALLEL, src.max_running)
        first = {k for _, k in src.calls}
        src.calls.clear()
        asyncio.run(main.refresh_updates(limit=5))
        second = {k for _, k in src.calls}
        check("следующий заход берёт тех, до кого не дошла очередь",
              not (first & second) and len(second) == 5, (sorted(first), sorted(second)))

        src.calls.clear()
        with Patch(main, "_refresh_busy", True):
            got = asyncio.run(main.refresh_updates())
        check("пока идёт заход, второй не начинается",
              got.get("skipped") is True and not src.calls, got)

        # Два одновременных вопроса об одном тайтле — один поход.
        src.calls.clear()

        async def twice():
            return await asyncio.gather(main.fresh_last_episode("ru_a", "p00", "Т"),
                                        main.fresh_last_episode("ru_a", "p00", "Т"))
        anime._cache.clear()
        both = asyncio.run(twice())
        check("одновременные вопросы склеены в один", both == [5, 5] and len(src.calls) == 1,
              (both, src.calls))

        # Источник лежит — проверка не падает, остальные проверены.
        wipe()
        put(uid, "ok1", 5, watched=1)
        put(uid, "bad", 5, watched=1)
        src.calls.clear()
        src.eps["ok1"] = 6
        src.fail.add("bad")
        got = asyncio.run(main.refresh_updates())
        check("ошибка источника не роняет заход", got.get("checked") == 2 and got.get("silent") == 1, got)
        check("а соседний тайтл проверен", row(uid, "ok1")["latest_ep"] == 6, row(uid, "ok1"))
        check("молчание источника — не новость", row(uid, "bad")["latest_ep"] == 0, row(uid, "bad"))

        # /api/updates при исчерпанном лимите отвечает из базы, а не 429.
        sql("UPDATE library SET checked_at = 0")
        reset()
        a = C()
        login(a)
        # Выбираем лимит до дна тем же ключом, каким его тратит ручка.
        while main.source_limit.allow("u:u:%s" % uid):
            pass
        calls_before = len(src.calls)
        codes = []
        for _ in range(5):
            sql("UPDATE library SET checked_at = 0")
            codes.append(a.get("/api/updates").status_code)
        check("колокольчик не отвечает «слишком часто»", set(codes) == {200}, sorted(set(codes)))
        check("а к источнику при исчерпанном лимите не ходили",
              len(src.calls) == calls_before, len(src.calls) - calls_before)
        main.source_limit.reset()

    # ==================================================================
    group("Фоновая задача: живёт с сервером, не падает от ошибок")
    runs = {"n": 0}

    async def flaky():
        runs["n"] += 1
        if runs["n"] == 1:
            raise RuntimeError("сломалось")
        return {"checked": 0, "new": 0}

    async def spin():
        task = asyncio.create_task(main.episode_watch())
        await asyncio.sleep(0.25)
        alive = not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return alive

    with Patch(main, "REFRESH_FIRST_DELAY", 0), Patch(main, "REFRESH_EVERY", 0.05), \
            Patch(main, "refresh_updates", flaky):
        alive = asyncio.run(spin())
    check("после ошибки задача жива и продолжает", alive and runs["n"] >= 2, runs)

    with C() as c:
        w = main.app.state.episode_watcher
        check("при запуске сервера фоновая проверка запущена",
              w is not None and not w.done(), w)
    check("при остановке — снята", w.done(), w)

    # ==================================================================
    group("Письма берут свежий ответ фоновой проверки")
    asked = []

    async def no_source(source, key, title):
        asked.append(key)
        return 99

    sent = []

    def fake_send(to, **kw):
        sent.append(kw)
        return True

    fresh_row = {"key": "m1", "source": "ru_a", "title": "Письмо", "poster": "",
                 "mailed_ep": 12, "latest_ep": 13, "checked_at": store.now() - 60}
    old_row = dict(fresh_row, key="m2", checked_at=store.now() - 5 * 3600)
    wipe()
    put(uid, "m1", 12)
    put(uid, "m2", 12)

    async def nothing(*a, **k):
        return None

    with Patch(main, "last_episode_number", no_source), Patch(mail, "send_episode", fake_send), \
            Patch(main.catalog, "about", nothing):
        n1 = asyncio.run(main._mail_one({"email": "a@b.c", "display_name": "В", "login": "v",
                                         "id": uid}, fresh_row))
        n2 = asyncio.run(main._mail_one({"email": "a@b.c", "display_name": "В", "login": "v",
                                         "id": uid}, old_row))
    check("свежая проверка — источник не спрошен", "m1" not in asked, asked)
    check("и письмо о 13-й ушло", n1 == 1 and sent and sent[0]["episode"] == 13, sent[:1])
    check("старая проверка — источник спрошен, как раньше", "m2" in asked and n2 == 1, asked)


run()
print("\n" + "=" * 60)
if FAILS:
    print("ПРОХОД 16 — не прошли: %d" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    sys.exit(1)
print("ПРОХОД 16 — новые серии: колокольчик узнаёт о них сам и вовремя.")
