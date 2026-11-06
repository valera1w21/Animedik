"""Проход 29: скорость — выбор аниме и «Рандом» (fix-21).

Хозяин: «когда выбираешь аниме и в Рандоме — чтобы время загрузки было
намного меньше, насколько возможно». Что изменилось и что здесь проверяем:

  * резолв (часть → источник): поиск у источников — разом, а не по
    очереди (по запросу на источник, как и раньше); списки серий
    кандидатов — первый один, остальные разом;
  * готовый резолв помнится (RESOLVE_TTL), одинаковые резолвы, идущие
    разом (заранее из окна частей и по нажатию), склеиваются в один;
  * после резолва сервер прогревает серию (плееры и ссылки), а /api/videos
    помнит удачный ответ (VIDEOS_TTL) — страница просмотра берёт готовое;
  * «Рандом»: сервер держит запас готовых карточек и досыпает его в фоне
    одним потоком (нажатие не шлёт к справочнику своего запроса рядом);
  * страница: части франшизы, поиск и резолв — с памятью и заранее;
    список серий — сразу при открытии плеера; обложка видео не задерживает.

Сети нет: источники и справочник подменены, задержки — свои.
"""
import asyncio
import logging
import os
import sys
import tempfile
import time

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p29.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["UPDATES_EVERY_MIN"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass29")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import httpx                                        # noqa: E402
from api import anime, catalog, main, store         # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PASS = "Bystro-Bystro-2026"
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


class Patch:
    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


def reset():
    main.login_guard.reset()
    for lim in (main.api_limit, main.search_limit, main.guest_limit, main.write_limit,
                main.source_limit, main.pass_limit, main.avatar_limit, main.catalog_limit):
        lim.reset()


# Источники: у каждого своя задержка поиска; «Тайтл» есть только у тех,
# что перечислены в HAS.
LAG = 0.3
HAS = {}
CALLS = []


async def fake_src(source, q):
    CALLS.append(("search", source, q))
    await asyncio.sleep(LAG)
    return [{"key": source + ":1", "title": t} for t in HAS.get(source, ())
            if anime.relevance(q, t) >= 0.9]


class Video:
    def __init__(self, url):
        self.url, self.quality, self.type = url, 720, "m3u8"


class Player:
    def __init__(self, src):
        self.title, self.url, self.subtitles, self.src = "RU-A", "https://p.test/" + src, [], src

    async def a_get_videos(self):
        CALLS.append(("links", self.src))
        await asyncio.sleep(LAG)
        return [Video("https://cdn.test/%s.m3u8" % self.src)]


class Ep:
    def __init__(self, src, n):
        self.ordinal, self.title, self.src = n, "Серия %d" % n, src

    async def a_get_sources(self):
        CALLS.append(("players", self.src, self.ordinal))
        await asyncio.sleep(LAG)
        return [Player(self.src)]


EPS = {}                # источник → сколько серий отдаёт


async def fake_eps(source, key, title):
    CALLS.append(("eps", source))
    await asyncio.sleep(LAG)
    return [Ep(source, n) for n in range(1, EPS.get(source, 12) + 1)]


def calls(kind):
    return [c for c in CALLS if c[0] == kind]


async def client():
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                          base_url="http://test")
    reset()
    r = await c.post("/api/auth/login", json={"login": "hozyain", "password": PASS, "code": ""})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


async def scenario():
    order = main.fallback_for("ru")
    c = await client()

    # ==================================================================
    group("1. Резолв: источники — разом, порядок — прежний")
    HAS.clear()
    HAS[order[-1]] = {"Тайтл"}                       # есть только у последнего
    CALLS.clear()
    anime._cache.clear()
    reset()
    t = time.monotonic()
    r = await c.get("/api/resolve", params={"title": "Тайтл", "lang": "ru", "warm": "0"})
    took = time.monotonic() - t
    check("нашёлся у последнего источника", r.status_code == 200
          and r.json().get("source") == order[-1], r.text[:160])
    searched = {x[1] for x in calls("search")}
    check("каждый основной источник спрошен ровно раз",
          searched == set(order) and len([x for x in calls("search") if x[2] == "Тайтл"])
          == len(order), calls("search"))
    check("время — как у одного поиска, а не у %d подряд" % len(order),
          took < LAG * (len(order) - 1) + 2 * LAG, round(took, 2))

    HAS.clear()
    HAS[order[0]] = {"Тайтл"}
    HAS[order[1]] = {"Тайтл"}
    anime._cache.clear()
    CALLS.clear()
    reset()
    r = await c.get("/api/resolve", params={"title": "Тайтл", "lang": "ru", "warm": "0"})
    check("есть у двух — берётся первый по порядку (как раньше)",
          r.json().get("source") == order[0], r.text[:120])
    check("серии проверены только у лучшего (он полный)", len(calls("eps")) == 1, calls("eps"))

    # Огрызок у первого: остальные кандидаты проверяются разом.
    HAS.clear()
    for s in order[:3]:
        HAS[s] = {"Тайтл"}
    EPS.clear()
    EPS[order[0]] = 2
    anime._cache.clear()
    CALLS.clear()
    reset()
    t = time.monotonic()
    r = await c.get("/api/resolve", params={"title": "Тайтл", "lang": "ru", "episodes": 24,
                                            "warm": "0"})
    took = time.monotonic() - t
    check("огрызок (2 из 24) не выбран", r.json().get("source") in order[1:3], r.text[:120])
    check("проверок серий — не больше PROBE_LIMIT", len(calls("eps")) <= main.PROBE_LIMIT,
          calls("eps"))
    check("остальные кандидаты проверены разом (поиск + 2 шага, а не 3)",
          took < LAG * 3 + LAG * 0.8, round(took, 2))
    EPS.clear()

    # ==================================================================
    group("2. Резолв помнится и склеивается")
    HAS.clear()
    HAS[order[0]] = {"Другой тайтл"}
    anime._cache.clear()
    CALLS.clear()
    reset()
    q = {"title": "Другой тайтл", "lang": "ru", "warm": "0"}
    a, b = await asyncio.gather(c.get("/api/resolve", params=q), c.get("/api/resolve", params=q))
    check("два одинаковых резолва разом — оба ответили", a.status_code == b.status_code == 200,
          (a.status_code, b.status_code))
    check("…а к источникам сходили один раз", len([x for x in calls("search")
                                                   if x[1] == order[0]]) == 1, calls("search"))
    CALLS.clear()
    t = time.monotonic()
    r = await c.get("/api/resolve", params=q)
    check("повторный резолв — из памяти, без источников и мгновенно",
          r.status_code == 200 and not CALLS and time.monotonic() - t < LAG, CALLS)
    left = anime._cache.get("resolve:" + __import__("json").dumps(
        ["Другой тайтл", "", "", 0, "ru", "", "", False, False], ensure_ascii=False))
    check("память резолва — RESOLVE_TTL (полчаса)", left and left[2] == main.RESOLVE_TTL == 1800,
          left and left[2])
    HAS.clear()
    anime._cache.clear()
    reset()
    r = await c.get("/api/resolve", params={"title": "Нигде нет", "lang": "ru", "warm": "0"})
    check("«нигде нет» не запоминается", r.status_code == 404
          and not [k for k in anime._cache if k.startswith("resolve:")], r.status_code)

    # ==================================================================
    group("3. Прогрев серии после резолва и память /api/videos")
    HAS.clear()
    HAS[order[0]] = {"Прогрев"}
    anime._cache.clear()
    CALLS.clear()
    reset()
    r = await c.get("/api/resolve", params={"title": "Прогрев", "lang": "ru", "ep": 3})
    found = r.json()
    for _ in range(40):
        if calls("links"):
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)
    check("после резолва сервер сам спросил плееры и ссылки нужной серии (3)",
          calls("players") and calls("players")[0][2] == 3 and calls("links"), CALLS)
    CALLS.clear()
    t = time.monotonic()
    reset()
    v = await c.get("/api/videos", params={"key": found["key"], "ordinal": 3,
                                           "source": found["source"], "lang": "ru"})
    check("страница просмотра получила готовое: без источников и сразу",
          v.status_code == 200 and v.json().get("videos") and not CALLS
          and time.monotonic() - t < LAG, (v.status_code, CALLS))
    CALLS.clear()
    reset()
    v = await c.get("/api/videos", params={"key": found["key"], "ordinal": 2,
                                           "source": found["source"], "lang": "ru"})
    check("другая серия — к источнику, как обычно", v.status_code == 200
          and calls("players"), CALLS)
    CALLS.clear()
    reset()
    await c.get("/api/videos", params={"key": found["key"], "ordinal": 2,
                                       "source": found["source"], "lang": "ru"})
    check("…и тоже запомнилась", not CALLS, CALLS)
    check("ссылки помнятся недолго (VIDEOS_TTL — пять минут)", main.VIDEOS_TTL == 300)
    reset()
    r = await c.get("/api/videos", params={"key": found["key"], "ordinal": 2,
                                           "source": "nosuch", "lang": "ru"})
    check("проверка источника — по-прежнему до памяти", r.status_code == 400, r.status_code)
    await c.aclose()


# «Рандом»: настоящий catalog.random_anime, подменён только справочник.
RCALLS = []


async def fake_get(url, params=None):
    await catalog._pause()
    RCALLS.append(url)
    await asyncio.sleep(LAG)
    if url.rstrip("/").endswith("/animes"):
        i = int((params or {}).get("page") or 1)
        return [{"id": 5000 + i, "name": "R%d" % i, "russian": "Случайное %d" % i,
                 "kind": "tv", "aired_on": "2015-01-01", "episodes": 12}]
    return {"rating": "pg_13", "description": "Описание. " * 5, "genres": []}


async def roulette():
    group("4. «Рандом»: запас готовых карточек")
    main._random_pool[""].clear()
    main._random_pool["ecchi"].clear()
    catalog._cache.clear()
    with Patch(catalog, "_get", fake_get), Patch(catalog, "MIN_GAP", 0.05), \
            Patch(main, "ADULT_PICK_SHARE", 0.0):
        c = await client()
        check("запас — только у настоящего справочника", main._pool_on())
        t = time.monotonic()
        r = await c.get("/api/random")
        first = time.monotonic() - t
        check("первое нажатие (запас пуст) — карточка", r.status_code == 200
              and r.json().get("found"), r.text[:100])
        for _ in range(100):
            if len(main._random_pool[""]) >= main.RANDOM_POOL:
                break
            await asyncio.sleep(0.05)
        check("запас досыпан в фоне до RANDOM_POOL", len(main._random_pool[""]) == main.RANDOM_POOL,
              len(main._random_pool[""]))
        RCALLS.clear()
        reset()
        t = time.monotonic()
        r = await c.get("/api/random")
        took = time.monotonic() - t
        # Обращения к справочнику после ответа — это уже досыпка запаса в фоне.
        check("следующее нажатие — из запаса: мгновенно (справочник ответа не держит)",
              r.json().get("found") and took < LAG, (round(took, 3), RCALLS))
        check("…и в разы быстрее первого", took * 4 < first, (round(took, 3), round(first, 3)))
        names = [r.json().get("title")]
        for _ in range(4):
            reset()
            names.append((await c.get("/api/random")).json().get("title"))
        check("карточки подряд не повторяются", len(set(names)) == len(names), names)
        # Нажатие на пустой запас не шлёт своего запроса рядом с досыпкой.
        main._random_pool[""].clear()
        for _ in range(100):
            task = main._random_fill.get("")
            if task is None or task.done():
                break
            await asyncio.sleep(0.05)
        RCALLS.clear()
        reset()
        rs = await asyncio.gather(c.get("/api/random"), c.get("/api/random"))
        check("два нажатия на пустой запас — обе карточки, разные",
              all(x.json().get("found") for x in rs)
              and rs[0].json()["title"] != rs[1].json()["title"], [x.text[:60] for x in rs])
        check("справочник при этом спрошен одним потоком (по 2 запроса на карточку)",
              len(RCALLS) <= 2 * (main.RANDOM_POOL + 2), len(RCALLS))
        reset()
        w = await c.get("/api/random", params={"warm": "1"})
        check("warm=1 — сразу, без карточки", w.status_code == 200 and w.json() == {
            "found": False, "warm": True}, w.text)
        await c.aclose()
    main._random_pool[""].clear()


def static():
    group("5. Страница: заранее и с памятью")
    ix = open(os.path.join(HERE, "web", "index.js"), encoding="utf-8").read()
    w = open(os.path.join(HERE, "web", "watch.js"), encoding="utf-8").read()
    check("части франшизы — с памятью и заранее (наведение, первая карточка выдачи)",
          "function getFranchise(id)" in ix and "getFranchise(fr.id)\n      .then" in ix
          and "a.addEventListener('pointerenter', soon);" in ix
          and "prefetchFranchise(rows[0]);" in ix)
    check("«Начни отсюда» ищется у источников, пока читают список",
          "prefetchResolve(startPart(parts));" in ix and "var resolveMemo = {};" in ix)
    check("с какой серии откроют — серверу для прогрева",
          "'&ep=' + encodeURIComponent(mine.watched_ep)" in ix)
    check("«Рандом»: запас досыпается заранее (наведение, простой)",
          "A.api.get('/api/random?warm=1')" in ix and "function warmRoulette()" in ix)
    check("«Смотреть» в «Рандоме»: поиск выпавшего — заранее и с памятью",
          "function findFor(q)" in ix and "findFor(it.title).catch" in ix
          and "    findFor(q)\n      .then(function (res) {" in ix)
    check("плеер: список серий — сразу при открытии", "if (!guessedSource) startEpisodesEarly();" in w
          and "earlyEps.url === url ? earlyEps.promise" in w)
    check("плеер: обложка не задерживает видео",
          ".then(ensurePoster)" not in w and "        ensurePoster();\n" in w)


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "hozyain")
    with Patch(main, "try_source", fake_src), Patch(anime, "find_episodes", fake_eps), \
            Patch(catalog, "alt_names", _no_alt):
        asyncio.run(scenario())
    asyncio.run(roulette())
    static()
    print()
    if FAILS:
        print("ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("Все проверки прохода 29 прошли.")


async def _no_alt(*a, **k):
    return []


if __name__ == "__main__":
    run()
