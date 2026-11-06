"""Проход 22: 18+ — жанровый поиск «этти», подсказка и «Рандом».

Настройка «Показывать 18+» была, но найти с ней что-то новое было нельзя:
поиск идёт по названию, а названий этти никто наизусть не знает. Теперь:

* запрос «этти» / «ecchi» / «эччи» / «18+» — список самого популярного
  в жанре Ecchi (справочник Shikimori, жанр 9, censored: true);
* только тому, кто сам включил 18+; без него — пустая выдача и подсказка,
  где включить; гостю — подсказка, что гостям нельзя;
* хентай (rx) в списке этти не появляется никогда — даже если справочник
  его вернёт и даже у того, кому хентай включён (жанр «хентай» — проход 24);
* «Рандом» тянет из жанра только при включённом 18+.

Сети нет: справочники подменены.
"""
import asyncio
import logging
import os
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p22.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass22")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient          # noqa: E402
from api import catalog, main, store                # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Long-Passw0rd-123"


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
    for lim in (main.login_guard, main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.catalog_limit):
        lim.reset()


def login(name):
    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": name, "password": PASS})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


def guest():
    reset()
    g = TestClient(main.app)
    r = g.post("/api/auth/guest", json={})
    g.headers["X-CSRF-Token"] = r.json()["csrf"]
    return g


def node(i, ru, rating, kind="tv", franchise="", year=2012):
    return {"id": str(i), "name": "N" + str(i), "russian": ru, "licenseNameRu": None,
            "english": None, "japanese": None, "synonyms": [], "franchise": franchise,
            "kind": kind, "rating": rating, "episodes": 12, "episodesAired": 12,
            "status": "released", "score": "7", "airedOn": {"year": year, "date": f"{year}-01-01"},
            "poster": {"mainUrl": None, "originalUrl": None}}


# Жанр Ecchi по популярности. Справочник с censored: true хентай не отдаёт,
# но если отдаст — мы всё равно не покажем.
GENRE_P1 = [node(11617, "Старшая школа DxD", "r_plus", franchise="high_school_dxd"),
            node(15451, "Старшая школа DxD: Новая", "r_plus", franchise="high_school_dxd", year=2013),
            node(20, "Монстр за партой", "r", franchise="monstr"),
            node(666, "Хентай-подлог", "rx", franchise="xxx"),
            node(21, "Клип этти", "r_plus", kind="music", franchise="klip")]
GENRE_P2 = [node(22, "Пляжный эпизод", "pg_13", franchise="plyazh")]
SEARCH = [node(1, "Школьные дни обычные", "pg_13", franchise="shkola")]
GQL = []
GQL_DOWN = [False]


async def fake_gql(query, variables=None):
    GQL.append(query)
    if GQL_DOWN[0]:
        return None
    if "genre:" in query:
        return {"p1": GENRE_P1, "p2": GENRE_P2}
    return {"animes": SEARCH}


def titles(items):
    return [i.get("title") for i in items]


def run():
    store.init()
    store.create_user("tester", PASS, "admin", "tester")
    store.create_user("friend", PASS, "user", "friend")

    # ==================================================================
    group("1. Какие запросы считаются жанром")
    for q in ("этти", "Этти", "ЭТТИ", "ecchi", "Ecchi", "эччи", "18+", "аниме этти",
              "этти аниме", "жанр ecchi", " этти! "):
        check(f"«{q}» — жанр ecchi", catalog.genre_query(q) == "ecchi", catalog.genre_query(q))
    for q in ("наруто", "18", "эти", "школа этти дней", ""):
        check(f"«{q}» — не жанр", catalog.genre_query(q) is None, catalog.genre_query(q))
    for q in ("хентай", "hentai"):
        check(f"«{q}» — жанр hentai, не ecchi", catalog.genre_query(q) == "hentai",
              catalog.genre_query(q))

    catalog._cache.clear()
    with Patch(catalog, "_ask_shiki_gql", fake_gql), Patch(catalog, "MIN_GAP", 0):
        # ==============================================================
        group("2. 18+ выключено — подсказка вместо списка")
        f = login("friend")
        GQL.clear()
        r = f.get("/api/find", params={"q": "этти"})
        body = r.json()
        check("ответ 200", r.status_code == 200, r.status_code)
        check("выдача пустая", body.get("items") == [], body)
        check("подсказка «включите 18+»", body.get("hint") == "adult_off", body)
        check("catalog:true — страница не уходит искать «этти» у источников",
              body.get("catalog") is True)
        check("справочник про жанр даже не спрашивали",
              not any("genre:" in q for q in GQL), len(GQL))
        g = guest()
        gb = g.get("/api/find", params={"q": "ecchi"}).json()
        check("гостю — своя подсказка, списка нет", gb.get("hint") == "guest" and gb["items"] == [], gb)

        # ==============================================================
        group("3. 18+ включено — список жанра")
        c = login("tester")
        check("18+ включается",
              c.post("/api/me/settings", json={"adult": True}).json()["settings"]["adult"] is True)
        GQL.clear()
        reset()
        r = c.get("/api/find", params={"q": "этти"})
        body = r.json()
        items = body.get("items") or []
        check("ответ 200 и пометка жанра", r.status_code == 200 and body.get("genre") == "ecchi", body)
        check("подсказки нет", "hint" not in body, body)
        check("этти в списке", "Старшая школа DxD" in titles(items), titles(items))
        check("и не только r_plus: «R» в жанре тоже показан",
              "Монстр за партой" in titles(items), titles(items))
        check("вторая страница тоже вошла", "Пляжный эпизод" in titles(items), titles(items))
        check("хентай не показан, даже когда справочник его отдал",
              "Хентай-подлог" not in titles(items), titles(items))
        # Даже у того, кому включён хентай: просили этти, а не хентай.
        c.post("/api/me/settings", json={"hentai": True})
        reset()
        hx = c.get("/api/find", params={"q": "этти"}).json()["items"]
        check("…и у того, кому хентай включён, в «этти» его тоже нет",
              "Хентай-подлог" not in titles(hx) and "Старшая школа DxD" in titles(hx), titles(hx))
        c.post("/api/me/settings", json={"hentai": False})
        check("клипы (music) отброшены, как и в обычном поиске", "Клип этти" not in titles(items))
        check("одна карточка на франшизу",
              titles(items).count("Старшая школа DxD") == 1
              and "Старшая школа DxD: Новая" not in titles(items), titles(items))
        check("порядок — по популярности, как ответил справочник",
              titles(items)[:2] == ["Старшая школа DxD", "Монстр за партой"], titles(items))
        dxd = [x for x in items if x["title"] == "Старшая школа DxD"]
        check("у карточки r_plus — пометка adult (для значка 18+)",
              dxd and dxd[0].get("adult") is True and dxd[0].get("rating") == "r_plus", dxd)
        q = next((x for x in GQL if "genre:" in x), "")
        check('запрос к справочнику: жанр 9', 'genre: "9"' in q, q[:120])
        check("…с censored: true на обеих страницах", q.count("censored: true") == 2, q[:200])
        check("…по популярности", "order: popularity" in q)
        n = len(GQL)
        for other in ("ecchi", "эччи", "18+"):
            reset()
            again = c.get("/api/find", params={"q": other}).json()
            check(f"«{other}» — тот же список", titles(again["items"]) == titles(items),
                  titles(again["items"]))
        check("повторные запросы — из кэша", len(GQL) == n, len(GQL) - n)
        reset()
        fr = c.get("/api/franchise", params={"id": "high_school_dxd"})
        check("карточка жанра открывается списком частей", fr.status_code == 200
              and "Старшая школа DxD" in titles(fr.json()["items"]), fr.text[:120])

        # кэш общий, фильтр на выходе: друг по-прежнему без списка
        f = login("friend")
        fb = f.get("/api/find", params={"q": "этти"}).json()
        check("кэш не протекает: другу — снова подсказка", fb["items"] == []
              and fb.get("hint") == "adult_off", fb)

        # выключил — снова подсказка
        c.post("/api/me/settings", json={"adult": False})
        reset()
        ob = c.get("/api/find", params={"q": "этти"}).json()
        check("выключил 18+ — снова подсказка", ob["items"] == [] and ob.get("hint") == "adult_off", ob)
        c.post("/api/me/settings", json={"adult": True})

        # обычный поиск не задет
        reset()
        nb = c.get("/api/find", params={"q": "школа"}).json()
        check("обычный поиск работает как раньше", titles(nb["items"]) == ["Школьные дни обычные"]
              and "genre" not in nb, nb)

    # ==================================================================
    group("4. Справочник молчит")
    catalog._cache.clear()
    media = [{"id": 1, "title": {"romaji": "Ecchi Show"}, "format": "TV", "genres": ["Ecchi"],
              "isAdult": False, "seasonYear": 2012},
             {"id": 2, "title": {"romaji": "Adult Show"}, "format": "TV", "genres": ["Ecchi"],
              "isAdult": True}]
    asked = []

    async def fake_anilist(query, variables):
        asked.append((query, variables))
        return {"Page": {"media": media}}

    GQL_DOWN[0] = True
    with Patch(catalog, "_ask_shiki_gql", fake_gql), Patch(catalog, "_ask_anilist", fake_anilist), \
            Patch(catalog, "MIN_GAP", 0):
        c = login("tester")
        body = c.get("/api/find", params={"q": "этти"}).json()
        check("запасной справочник AniList: жанр Ecchi", asked and asked[0][1] == {"g": "Ecchi"}
              and "isAdult: false" in asked[0][0], asked[:1])
        check("из него — этти", titles(body["items"]) == ["Ecchi Show"], titles(body["items"]))
        check("isAdult (хентай) отброшен", "Adult Show" not in titles(body["items"]))

    async def dead_anilist(query, variables):
        return None

    catalog._cache.clear()
    with Patch(catalog, "_ask_shiki_gql", fake_gql), Patch(catalog, "_ask_anilist", dead_anilist), \
            Patch(catalog, "MIN_GAP", 0):
        reset()
        r = login("tester").get("/api/find", params={"q": "этти"})
        check("оба молчат — честное 502, а не «ничего не нашлось»", r.status_code == 502, r.status_code)
    GQL_DOWN[0] = False

    # ==================================================================
    group("5. «Рандом»")
    calls = []

    async def fake_random(genre=None):
        calls.append(genre)
        if genre == "ecchi":
            return {"title": "Этти-сериал", "rating": "r_plus"}
        return {"title": "Обычное", "rating": "pg_13"}

    with Patch(catalog, "random_anime", fake_random), Patch(main, "ADULT_PICK_SHARE", 1.0):
        f = login("friend")
        r = f.get("/api/random").json()
        check("18+ выключено: из жанра не тянет никогда", "ecchi" not in calls
              and r.get("title") == "Обычное", (calls, r))
        calls.clear()
        c = login("tester")
        r = c.get("/api/random").json()
        check("18+ включено: может выпасть этти", calls[:1] == ["ecchi"]
              and r.get("title") == "Этти-сериал", (calls, r))
        calls.clear()
        g = guest()
        r = g.get("/api/random").json()
        check("гость: из жанра не тянет", "ecchi" not in calls, calls)

    async def rx_random(genre=None):
        if genre:
            return {"title": "Хентай", "rating": "rx"}
        return {"title": "Ок", "rating": "g"}

    with Patch(catalog, "random_anime", rx_random), Patch(main, "ADULT_PICK_SHARE", 1.0):
        r = login("tester").get("/api/random").json()
        check("rx из жанра не выпадает — берётся обычное", r.get("title") == "Ок", r)

    got = []

    async def fake_get(url, params=None):
        got.append((url, dict(params or {})))
        return []

    async def no_anilist(query, variables):
        got.append(("anilist", variables))
        return None

    catalog._cache.clear()
    with Patch(catalog, "_get", fake_get), Patch(catalog, "_ask_anilist", no_anilist), \
            Patch(catalog, "MIN_GAP", 0):
        asyncio.run(catalog.random_anime("ecchi"))
    shiki = [p for u, p in got if u != "anilist"]
    check("random_anime('ecchi'): справочник спрошен с жанром 9 и censored",
          shiki and shiki[0].get("genre") == "9" and shiki[0].get("censored") == "true", got)
    check("…и без запасного AniList (там жанра нет — возьмётся обычное)",
          not any(u == "anilist" for u, _ in got), got)

    # ==================================================================
    group("6. Страница")
    js = open(os.path.join(HERE, "web", "index.js"), encoding="utf-8").read()
    html = open(os.path.join(HERE, "web", "index.html"), encoding="utf-8").read()
    css = open(os.path.join(HERE, "web", "app.css"), encoding="utf-8").read()
    check("подсказка про настройку есть в поиске",
          "res.hint === 'adult_off'" in js and "Показывать 18+" in js)
    check("значок 18+ на карточках и частях", js.count("ageBadge(") >= 3 and ".agebadge" in css)
    check("в настройках понятная подпись раздела", 'data-ru="18+ (этти)"' in html)
    check("переключателя 18+ в карточке профиля нет",
          html[html.index('id="ucard"'):html.index('id="veil-set"')].count("s-adult") == 0)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 22 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 22 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
