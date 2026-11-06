"""Проход 17: обложки и описания «любой ценой».

Что проверяется:

  * склеенные адреса обложек («https://ru-b.example//static.img-b.example/…»)
    чинятся: и те, что уже лежат в базе (при запуске), и новые — при
    записи и при выдаче; заглушки справочников («missing», «default»)
    считаются «обложки нет»; починка повторно ничего не меняет;
  * /api/poster ищет обложку по цепочке Shikimori → AniList → Jikan,
    выбирает тайтл по названию и году, чужое не берёт; промах помнит
    недолго, сбой сети — не помнит вовсе; к Jikan ходит строго по одному;
  * найденная обложка ложится только в свою запись списка и только
    если название записи похоже на искомое;
  * /api/about: Shikimori по точному, упрощённому, латинскому имени и по
    году; потом AniList и Jikan с пометкой about_lang="en"; found:false —
    только если описания нет нигде; сбой сети в кэш не попадает;
  * страница: прямых записей в style.backgroundImage больше нет, футер
    прижат к низу на всех четырёх страницах.

Сети в проверке нет: все три справочника подменяются заглушками.
"""
import asyncio
import io
import logging
import os
import re
import sys
import tempfile
import time

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p17.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass17")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)
logging.getLogger("anime.catalog").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient           # noqa: E402
from api import anime, catalog, main, store         # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Oblozhka-Lyuboy-2026"


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)


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
    for lim in (main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.pass_limit,
                main.avatar_limit, main.catalog_limit, main.poster_limit):
        lim.reset()


def C():
    return TestClient(main.app)


def login(c, user):
    r = c.post("/api/auth/login", json={"login": user, "password": PASS, "code": ""})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def user(name):
    if store.get_user_by_login(name) is None:
        store.create_user(name, PASS, "user")
    return store.get_user_by_login(name)["id"]


def raw_row(uid, key, title, poster, title_en=""):
    """Запись мимо save_progress — так, как её оставила старая версия."""
    with store.tx() as conn:
        conn.execute("DELETE FROM library WHERE user_id = ? AND key = ?", (uid, key))
        conn.execute(
            "INSERT INTO library (user_id,key,source,title,title_en,poster,updated_at)"
            " VALUES (?,?,?,?,?,?,?)", (uid, key, "ru_b", title, title_en, poster,
                                        store.now()))


def poster_of(uid, key):
    r = store.connect().execute("SELECT poster FROM library WHERE user_id = ? AND key = ?",
                                (uid, key)).fetchone()
    return r and r["poster"]


# --------------------------------------------------------------------------
# Поддельные справочники
# --------------------------------------------------------------------------
class World:
    """Три справочника в одном: что знают, сколько раз спросили, лежат ли."""

    def __init__(self):
        self.shiki_rest = {}     # запрос -> список кратких карточек
        self.shiki_full = {}     # id -> подробная карточка
        self.shiki_gql = {}      # запрос -> список частей (GraphQL)
        self.al_one = {}         # запрос -> Media
        self.al_page = {}        # запрос -> [Media]
        self.jikan = {}          # запрос -> [запись MAL]
        self.down = set()        # {"shiki", "anilist", "jikan"}
        self.calls = []

    def n(self, who):
        return sum(1 for c in self.calls if c[0] == who)

    async def get(self, url, params=None):
        params = params or {}
        if url == catalog.SHIKI + "/animes":
            self.calls.append(("shiki", params.get("search")))
            if "shiki" in self.down:
                return None
            return list(self.shiki_rest.get(params.get("search"), []))
        if url.startswith(catalog.SHIKI + "/animes/"):
            self.calls.append(("shiki-full", url))
            if "shiki" in self.down:
                return None
            return self.shiki_full.get(url.rsplit("/", 1)[1])
        if url == catalog.JIKAN + "/anime":
            self.calls.append(("jikan", params.get("q")))
            if "jikan" in self.down:
                return None
            return {"data": list(self.jikan.get(params.get("q"), []))}
        raise AssertionError("неожиданный адрес " + url)

    async def gql(self, query, variables=None):
        s = (variables or {}).get("s")
        self.calls.append(("shiki-gql", s))
        if "shiki" in self.down:
            return None
        return {"animes": list(self.shiki_gql.get(s, []))}

    async def anilist(self, query, variables):
        s = variables.get("s")
        self.calls.append(("anilist", s))
        if "anilist" in self.down:
            return None
        if "Page(" in query:
            return {"Page": {"media": list(self.al_page.get(s, []))}}
        return {"Media": self.al_one.get(s)}


def world_patches(w):
    return [Patch(catalog, "_get", w.get), Patch(catalog, "_ask_shiki_gql", w.gql),
            Patch(catalog, "_ask_anilist", w.anilist), Patch(catalog, "MIN_GAP", 0),
            Patch(catalog, "JIKAN_GAP", 0)]


class Many:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.__enter__()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.__exit__(*exc)


def brief(id_, russian, name, year, image="/system/animes/original/%s.jpg"):
    return {"id": id_, "russian": russian, "name": name, "aired_on": "%d-04-03" % year,
            "kind": "tv", "score": "8.1", "episodes": 12,
            "image": {"original": image % id_ if "%s" in image else image}}


def full(text, genres=("Экшен",)):
    return {"description": text, "genres": [{"russian": g} for g in genres]}


def part(id_, russian, name, year, poster="https://shikimori.one/uploads/poster/%s.jpg"):
    return {"id": id_, "russian": russian, "name": name, "franchise": "x", "kind": "tv",
            "episodes": 12, "status": "released", "score": 8.0,
            "airedOn": {"year": year, "date": "%d-01-01" % year},
            "poster": {"mainUrl": poster % id_ if "%s" in poster else poster}
            if poster else None}


def media(romaji, english, year, about="", cover="https://s4.anilist.co/cover/xl/%s.jpg"):
    return {"id": abs(hash(romaji)) % 10000, "title": {"romaji": romaji, "english": english,
                                                      "native": ""},
            "seasonYear": year, "episodes": 12, "format": "TV", "genres": ["Drama"],
            "description": about, "isAdult": False, "averageScore": 75,
            "startDate": {"year": year, "month": 1, "day": 1},
            "coverImage": {"extraLarge": cover % romaji.replace(" ", "_") if "%s" in cover else cover,
                           "large": "https://s4.anilist.co/cover/l/x.jpg", "medium": ""}}


def mal(title, english, year, synopsis="",
        image="https://cdn.myanimelist.net/images/anime/%s.webp"):
    img = image % title.replace(" ", "_") if "%s" in image else image
    return {"mal_id": 1, "title": title, "title_english": english, "year": year,
            "type": "TV", "episodes": 12, "score": 7.9, "synopsis": synopsis,
            "genres": [{"name": "Comedy"}],
            "images": {"webp": {"large_image_url": img, "image_url": img},
                       "jpg": {"large_image_url": img}}}


def run():
    store.init()
    uid = user("hozyain")
    misha = user("misha")

    # ==================================================================
    group("1. Адреса обложек приводятся в порядок")
    cp = store.clean_poster
    cases = [
        ("https://ru-b.example//static.img-b.example/posters/full/1636949176.jpg",
         "https://static.img-b.example/posters/full/1636949176.jpg"),
        ("https://ru-h.example//cache.ru-h.example/releases/74/poster.webp",
         "https://cache.ru-h.example/releases/74/poster.webp"),
        ("//static.img-b.example/posters/1.jpg", "https://static.img-b.example/posters/1.jpg"),
        ("https://a.ru//poster.jpg", "https://a.ru//poster.jpg"),
        ("https://a.ru//images/x.jpg", "https://a.ru//images/x.jpg"),
        ("https://shikimori.one/assets/globals/missing/main.png", ""),
        ("https://shikimori.one/assets/globals/missing_original.jpg", ""),
        ("https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/default.jpg", ""),
        ("https://cdn.myanimelist.net/img/sp/icon/apple-touch-icon-256.png", ""),
        ("javascript:alert(1)", ""),
        ("  ", ""),
        ("/static/x.png", "/static/x.png"),
        ("https://cdn.myanimelist.net/images/anime/1/1.webp",
         "https://cdn.myanimelist.net/images/anime/1/1.webp"),
    ]
    for raw, want in cases:
        got = cp(raw)
        check("%s → %s" % (raw[:60], want[:50] or "«нет»"), got == want, got)
    check("повторная чистка ничего не меняет", all(cp(cp(r)) == cp(r) for r, _ in cases))
    check("не строка — «нет обложки»", cp(None) == "" and cp(123) == "")

    check("источник: адрес без схемы → свой хост",
          anime.absolute("ru_b", "//static.img-b.example/p/1.jpg") == "https://static.img-b.example/p/1.jpg",
          anime.absolute("ru_b", "//static.img-b.example/p/1.jpg"))
    check("источник: путь от корня → адрес сайта",
          anime.absolute("ru_h", "/releases/1.jpg") == "https://ru-h.example/releases/1.jpg")
    check("источник: уже склеенный адрес чинится",
          anime.absolute("ru_b", "https://ru-b.example//static.img-b.example/p.jpg")
          == "https://static.img-b.example/p.jpg")
    check("справочник: заглушка Shikimori — «нет обложки»",
          catalog._poster_url("/assets/globals/missing/main.png") == ""
          and catalog.pack_shiki({"name": "X", "image": {"original": "/assets/globals/missing_original.jpg"}})["poster"] == "")
    check("справочник: заглушка в части франшизы — «нет обложки»",
          catalog.pack_part(part(1, "А", "A", 2000, "/assets/globals/missing/main.png"))["poster"] == "")

    # ==================================================================
    group("2. Уже сохранённые битые адреса чинятся при запуске")
    raw_row(uid, "y1", "Ковбой Бибоп",
            "https://ru-b.example//static.img-b.example/posters/full/1636949176.jpg")
    raw_row(uid, "d1", "Тетрадь смерти",
            "https://ru-h.example//cache.ru-h.example/releases/74/poster.webp")
    raw_row(uid, "m1", "Безымянная", "https://shikimori.one/assets/globals/missing/main.png")
    raw_row(uid, "ok", "Хорошая", "https://img.example/good.jpg")
    raw_row(uid, "rel", "Демо", "/static/demo.png")
    raw_row(misha, "y1", "Ковбой Бибоп", "https://ru-b.example//static.img-b.example/posters/2.jpg")
    store.init()                              # то же, что при старте сервера
    check("склеенный адрес RU-B — починен",
          poster_of(uid, "y1") == "https://static.img-b.example/posters/full/1636949176.jpg",
          poster_of(uid, "y1"))
    check("склеенный адрес RU-H — починен",
          poster_of(uid, "d1") == "https://cache.ru-h.example/releases/74/poster.webp",
          poster_of(uid, "d1"))
    check("заглушка — стала «нет обложки»", poster_of(uid, "m1") == "", poster_of(uid, "m1"))
    check("хороший адрес не тронут", poster_of(uid, "ok") == "https://img.example/good.jpg")
    check("путь без хоста не тронут", poster_of(uid, "rel") == "/static/demo.png")
    check("у другого человека тоже починено",
          poster_of(misha, "y1") == "https://static.img-b.example/posters/2.jpg", poster_of(misha, "y1"))
    check("второй запуск ничего не меняет", store.fix_stored_posters() == 0)

    raw_row(uid, "late", "Поздний", "https://ru-b.example//static.img-b.example/late.jpg")
    reset()
    c = C()
    check("вход", login(c, "hozyain").status_code == 200)
    items = {i["key"]: i for i in c.get("/api/library").json()["items"]}
    check("/api/library отдаёт чистый адрес, даже если база ещё не починена",
          items["late"]["poster"] == "https://static.img-b.example/late.jpg", items["late"]["poster"])

    r = c.post("/api/library/progress", json={
        "key": "sp1", "source": "ru_b", "title": "Сохранённый", "status": "watching",
        "poster": "https://ru-b.example//static.img-b.example/sp1.jpg"})
    check("запись прогресса чистит адрес",
          r.status_code == 200 and poster_of(uid, "sp1") == "https://static.img-b.example/sp1.jpg",
          poster_of(uid, "sp1"))
    c.post("/api/library/progress", json={"key": "sp1", "source": "ru_b",
                                          "title": "Сохранённый", "status": "watching",
                                          "poster": "", "watched_ep": 2})
    check("пустая обложка в новой записи старую не затирает",
          poster_of(uid, "sp1") == "https://static.img-b.example/sp1.jpg", poster_of(uid, "sp1"))
    store.set_poster(uid, "sp1", "https://shikimori.one/assets/globals/missing/main.png")
    check("set_poster тоже чистит (заглушка → пусто)", poster_of(uid, "sp1") == "")

    # ==================================================================
    group("3. Поиск обложки: Shikimori → AniList → Jikan")
    w = World()
    w.shiki_gql["Ковбой Бибоп"] = [part(1, "Ковбой Бибоп", "Cowboy Bebop", 1998),
                                   part(2, "Ковбой Бибоп: Фильм", "Cowboy Bebop: Movie", 2001)]
    w.shiki_gql["Хеллсинг"] = [part(10, "Хеллсинг", "Hellsing", 2001),
                               part(11, "Хеллсинг", "Hellsing Ultimate", 2006)]
    w.shiki_gql["Безымянная история"] = [part(20, "Безымянная история", "Nanashi no Monogatari",
                                              2020, "/assets/globals/missing/main.png")]
    w.al_page["Nanashi no Monogatari"] = [media("Nanashi no Monogatari", "Nameless Story", 2020)]
    w.jikan["Obscure Show"] = [mal("Obscure Show", "Obscure Show", 2011)]
    w.jikan["Placeholder Show"] = [mal("Placeholder Show", "", 2011,
                                       image="https://cdn.myanimelist.net/img/sp/icon/apple-touch-icon-256.png")]
    w.al_page["Wrong Name"] = [media("Completely Different", "Other", 2010)]
    catalog._cache.clear()
    with Many(world_patches(w)):
        got = asyncio.run(catalog.find_poster("Ковбой Бибоп", "", 1998))
        check("Shikimori нашёл — его обложка",
              got == "https://shikimori.one/uploads/poster/1.jpg", got)
        check("и дальше никуда не ходили", w.n("anilist") == 0 and w.n("jikan") == 0, w.calls)

        got = asyncio.run(catalog.find_poster("Хеллсинг", "", 2006))
        check("два одноимённых — выбран по году (2006)",
              got == "https://shikimori.one/uploads/poster/11.jpg", got)
        got = asyncio.run(catalog.find_poster("Хеллсинг", "", 2001))
        check("и по году 2001 — другой", got == "https://shikimori.one/uploads/poster/10.jpg", got)

        w.calls.clear()
        got = asyncio.run(catalog.find_poster("Безымянная история", "", 2020))
        check("у Shikimori заглушка → AniList по латинскому имени из Shikimori",
              got == "https://s4.anilist.co/cover/xl/Nanashi_no_Monogatari.jpg", got)
        check("AniList спрошен именно латинским именем",
              ("anilist", "Nanashi no Monogatari") in w.calls, w.calls)

        w.calls.clear()
        got = asyncio.run(catalog.find_poster("Совсем неизвестное", "Obscure Show", 2011))
        check("Shikimori и AniList молчат → Jikan (webp large)",
              got == "https://cdn.myanimelist.net/images/anime/Obscure_Show.webp", got)
        check("Jikan спрошен латинским именем", ("jikan", "Obscure Show") in w.calls, w.calls)

        got = asyncio.run(catalog.find_poster("Заглушка", "Placeholder Show", 2011))
        check("картинка-заглушка MAL не считается обложкой", got == "", got)

        got = asyncio.run(catalog.find_poster("Чужое", "Wrong Name", None))
        check("непохожее название — чужую обложку не берём", got == "", got)

        w.calls.clear()
        got = asyncio.run(catalog.find_poster("Только по-русски", "", None))
        check("только русское имя и Shikimori не знает — пусто", got == "", got)
        check("AniList и Jikan с русским именем не спрашивали (сверить нечем)",
              w.n("anilist") == 0 and w.n("jikan") == 0, w.calls)
        row = catalog._cache.get(catalog._poster_key("Только по-русски", "", None))
        check("промах помнится, но недолго",
              row is not None and row[1] == "" and row[2] == catalog.POSTER_MISS_TTL,
              row and row[2])
        before = len(w.calls)
        asyncio.run(catalog.find_poster("Только по-русски", "", None))
        check("повтор промаха наружу не ходит", len(w.calls) == before)
        check("poster_cached отличает «искали, нет» от «не искали»",
              catalog.poster_cached("Только по-русски") == ""
              and catalog.poster_cached("Никогда не искали") is None)

        w.down = {"shiki", "anilist", "jikan"}
        got = asyncio.run(catalog.find_poster("Триган", "Trigun", 1998))
        check("всё лежит — пусто", got == "", got)
        check("и сбой в кэш не попал", catalog.poster_cached("Триган", "Trigun", 1998) is None)
        w.down = set()
        w.shiki_gql["Триган"] = [part(3, "Триган", "Trigun", 1998)]
        got = asyncio.run(catalog.find_poster("Триган", "Trigun", 1998))
        check("справочник ожил — нашлось сразу",
              got == "https://shikimori.one/uploads/poster/3.jpg", got)

        # Своей записи у продолжения нет — годится обложка первой части:
        # та же история, и это лучше буквы.
        got = asyncio.run(catalog.find_poster("Ковбой Бибоп 2", "", None))
        check("у «… 2» своей нет — обложка первой части",
              got == "https://shikimori.one/uploads/poster/1.jpg", got)

    # ==================================================================
    group("4. К Jikan — строго по одному и не чаще шага")
    stamps = []
    running = [0, 0]

    async def slow_get(url, params=None):
        running[0] += 1
        running[1] = max(running[1], running[0])
        stamps.append(time.monotonic())
        await asyncio.sleep(0.02)
        running[0] -= 1
        return {"data": []}

    async def three():
        await asyncio.gather(*(catalog._ask_jikan("q%d" % i) for i in range(3)))

    catalog._jikan_last[0] = 0.0
    with Patch(catalog, "_get", slow_get), Patch(catalog, "JIKAN_GAP", 0.15):
        catalog._jikan_gate = asyncio.Lock()
        asyncio.run(three())
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    check("одновременно — не больше одного запроса", running[1] == 1, running[1])
    check("между запросами не меньше шага", gaps and min(gaps) >= 0.14, gaps)
    check("шаг по умолчанию укладывается в 60 в минуту и 3 в секунду",
          catalog.JIKAN_GAP >= 1.0, catalog.JIKAN_GAP)
    catalog._jikan_gate = asyncio.Lock()

    # ==================================================================
    group("5. /api/poster: вход, лимит, запись только в своё")
    reset()
    w = World()
    w.shiki_gql["Ковбой Бибоп"] = [part(1, "Ковбой Бибоп", "Cowboy Bebop", 1998)]
    catalog._cache.clear()
    anon = C()
    check("без входа — 401", anon.get("/api/poster", params={"title": "Ковбой Бибоп"}).status_code == 401)
    raw_row(uid, "y1", "Ковбой Бибоп", "https://dead.example/1.jpg")
    raw_row(misha, "y1", "Ковбой Бибоп", "https://dead.example/2.jpg")
    raw_row(uid, "other", "Совсем другое аниме", "https://dead.example/3.jpg")
    with Many(world_patches(w)):
        r = c.get("/api/poster", params={"title": "Ковбой Бибоп", "year": "1998", "key": "y1"})
        check("находит", r.status_code == 200
              and r.json() == {"poster": "https://shikimori.one/uploads/poster/1.jpg"}, r.text)
        check("и сразу кладёт в свою запись",
              poster_of(uid, "y1") == "https://shikimori.one/uploads/poster/1.jpg", poster_of(uid, "y1"))
        check("чужая запись с тем же ключом не тронута",
              poster_of(misha, "y1") == "https://dead.example/2.jpg", poster_of(misha, "y1"))
        r = c.get("/api/poster", params={"title": "Ковбой Бибоп", "year": "1998", "key": "other"})
        check("запись с непохожим названием не тронута",
              r.status_code == 200 and poster_of(uid, "other") == "https://dead.example/3.jpg",
              poster_of(uid, "other"))
        r = c.get("/api/poster", params={"title": "Ковбой Бибоп", "year": "null"})
        check("мусор вместо года — не ошибка", r.status_code == 200, r.status_code)
        check("пустое название — 400", c.get("/api/poster", params={"title": " "}).status_code == 400)

        calls = len(w.calls)
        with Patch(main.poster_limit, "limit", 2):
            main.poster_limit.reset()
            codes = [c.get("/api/poster", params={"title": "Ковбой Бибоп", "year": "1998"}).status_code
                     for _ in range(6)]
            check("ответы из кэша лимит не тратят", codes == [200] * 6, codes)
            check("и наружу не ходят", len(w.calls) == calls)
            misses = [c.get("/api/poster", params={"title": "Промах %d" % i}).status_code
                      for i in range(4)]
            check("промахи мимо кэша упираются в лимит", 429 in misses, misses)
        main.poster_limit.reset()

        g = C()
        rg = g.post("/api/auth/guest", json={})
        if rg.status_code == 200:
            raw_row(uid, "y1", "Ковбой Бибоп", "https://dead.example/1.jpg")
            r = g.get("/api/poster", params={"title": "Ковбой Бибоп", "year": "1998", "key": "y1"})
            check("гость обложку получает", r.status_code == 200 and r.json()["poster"], r.text)
            check("но ничего не записывает", poster_of(uid, "y1") == "https://dead.example/1.jpg")
        else:
            check("гостевой вход (для проверки записи)", False, rg.status_code)

    # ==================================================================
    group("6. Описание: вся цепочка справочников")
    reset()
    w = World()
    # точное название
    w.shiki_rest["Ковбой Бибоп"] = [brief(1, "Ковбой Бибоп", "Cowboy Bebop", 1998)]
    w.shiki_full["1"] = full("Охотники за головами летят по Солнечной системе.")
    # упрощённое название: «… 2» → «…»
    w.shiki_rest["Магическая битва"] = [brief(2, "Магическая битва", "Jujutsu Kaisen", 2020)]
    w.shiki_full["2"] = full("Школьник проглатывает палец демона.")
    # только по латинскому имени
    w.shiki_rest["Some Latin Show"] = [brief(3, "Латинское шоу", "Some Latin Show", 2015)]
    w.shiki_full["3"] = full("Описание, найденное по латинскому имени.")
    # одноимённые — по году
    w.shiki_rest["Хеллсинг"] = [brief(10, "Хеллсинг", "Hellsing", 2001),
                                brief(11, "Хеллсинг", "Hellsing Ultimate", 2006)]
    w.shiki_full["10"] = full("Сериал 2001 года.")
    w.shiki_full["11"] = full("OVA 2006 года.")
    # у Shikimori карточка без описания → AniList по её латинскому имени
    w.shiki_rest["Тихая история"] = [brief(20, "Тихая история", "Shizuka na Monogatari", 2019)]
    w.shiki_full["20"] = full("", genres=("Драма",))
    w.al_one["Shizuka na Monogatari"] = media("Shizuka na Monogatari", "Quiet Story", 2019,
                                              about="A quiet story about a quiet town.")
    # нигде нет, кроме Jikan
    w.jikan["Very Obscure"] = [mal("Very Obscure", "Very Obscure", 2003,
                                   synopsis="Only MyAnimeList knows this one. [Written by MAL Rewrite]")]
    # AniList отвечает чем-то чужим
    w.al_one["Strange One"] = media("Totally Unrelated", "Nope", 2012, about="Wrong text.")
    w.jikan["Strange One"] = [mal("Strange One", "", 2012, synopsis="The right text from MAL.")]
    catalog._cache.clear()
    with Many(world_patches(w)):
        a = asyncio.run(catalog.about("Ковбой Бибоп"))
        check("Shikimori по точному названию — русское описание",
              a and a["about"].startswith("Охотники") and a["about_lang"] == "ru", a)
        check("AniList при этом не спрашивали", w.n("anilist") == 0)

        a = asyncio.run(catalog.about("Магическая битва 2"))
        check("упрощённое название («… 2» → «…»)",
              a and a["about"].startswith("Школьник"), a and a["about"])

        a = asyncio.run(catalog.about("Неизвестно как по-русски", title_en="Some Latin Show"))
        check("по латинскому имени", a and a["about"].startswith("Описание, найденное"),
              a and a["about"])

        a = asyncio.run(catalog.about("Хеллсинг", year=2006))
        check("одноимённые — выбран по году", a and a["about"] == "OVA 2006 года.", a and a["about"])
        a = asyncio.run(catalog.about("Хеллсинг"))
        check("без года — первый, как отсортировал Shikimori",
              a and a["about"] == "Сериал 2001 года.", a and a["about"])

        w.calls.clear()
        a = asyncio.run(catalog.about("Тихая история"))
        check("русского нет → английское из AniList",
              a and a["about"].startswith("A quiet story") and a["about_lang"] == "en", a)
        check("название и жанры остались русские",
              a and a["title"] == "Тихая история" and a["genres"] == ["Драма"], a)
        check("AniList спрошен латинским именем из Shikimori",
              ("anilist", "Shizuka na Monogatari") in w.calls, w.calls)

        a = asyncio.run(catalog.about("Совсем никому не известное", title_en="Very Obscure"))
        check("ни Shikimori, ни AniList → Jikan",
              a and a["about"].startswith("Only MyAnimeList") and a["about_lang"] == "en", a)
        check("хвост «[Written by MAL Rewrite]» вычищен",
              a and "Written by" not in a["about"], a and a["about"])

        a = asyncio.run(catalog.about("Странное", title_en="Strange One"))
        check("чужая карточка AniList отброшена, взят Jikan",
              a and a["about"] == "The right text from MAL.", a and a["about"])

        a = asyncio.run(catalog.about("Тихая история", "en"))
        check("английская страница: AniList первым",
              a and a["about"].startswith("A quiet story"), a and a.get("about"))

        a = asyncio.run(catalog.about("Ничего такого нет"))
        check("нигде нет — None", a is None, a)
        row = catalog._cache.get(catalog._about_key("ru", "Ничего такого нет"))
        check("честное «нет» помнится недолго",
              row is not None and row[2] == catalog.NOT_FOUND_TTL, row and row[2])

        w.down = {"shiki", "anilist", "jikan"}
        a = asyncio.run(catalog.about("Упавшее", title_en="Fallen Show"))
        check("всё лежит — None", a is None)
        check("и в кэш не попало",
              catalog._cache.get(catalog._about_key("ru", "Упавшее", "Fallen Show")) is None)
        w.down = {"anilist"}
        w.jikan["Fallen Show"] = [mal("Fallen Show", "", 2001, synopsis="Back from MAL.")]
        a = asyncio.run(catalog.about("Упавшее", title_en="Fallen Show"))
        check("AniList лежит, Jikan ответил — описание есть", a and a["about"] == "Back from MAL.", a)
        check("найденное с описанием — запомнено, хоть по дороге и был сбой",
              catalog._cache.get(catalog._about_key("ru", "Упавшее", "Fallen Show")) is not None)
        w.down = set()

        # Ручка
        reset()
        r = c.get("/api/about", params={"title": "Тихая история"})
        j = r.json()
        check("/api/about: found и about_lang=en",
              r.status_code == 200 and j["found"] and j["about_lang"] == "en", j)
        r = c.get("/api/about", params={"title": "Ковбой Бибоп"})
        check("/api/about: русское — about_lang=ru", r.json().get("about_lang") == "ru", r.json())
        r = c.get("/api/about", params={"title": "Хеллсинг", "year": "2006"})
        check("/api/about передаёт год", r.json().get("about") == "OVA 2006 года.", r.json())
        r = c.get("/api/about", params={"title": "Совсем никому не известное",
                                        "title_en": "Very Obscure"})
        check("/api/about передаёт латинское имя", r.json().get("found") is True, r.json())
        r = c.get("/api/about", params={"title": "Ничего такого нет"})
        check("/api/about: found:false только когда нет нигде", r.json() == {"found": False},
              r.json())

    # ==================================================================
    group("7. Страница")
    idx = io.open("web/index.js", encoding="utf-8").read()
    app = io.open("web/app.js", encoding="utf-8").read()
    css = io.open("web/app.css", encoding="utf-8").read()
    direct = re.findall(r"style\.backgroundImage\s*=\s*'url", idx)
    check("в index.js нет прямых записей обложки в style.backgroundImage", not direct, direct)
    check("обложки ставятся через A.bg", idx.count("setPoster(") >= 9 and "A.bg(" in idx,
          idx.count("setPoster("))
    stats_js = io.open("web/stats.js", encoding="utf-8").read()
    check("на странице итогов прямых записей обложек тоже нет",
          "backgroundImage = 'url" not in stats_js)
    check("A.bg передаёт серверу key своей записи", "&key=" in app)
    check("A.bg спрашивает сервер не больше двух раз разом", "POSTER_ASK_AT_ONCE = 2" in app)
    check("подпись «Описание на английском» есть", "Описание на английском" in idx)
    for page, middle in (("index", "#app > main"), ("stats", "body > .wrap"),
                         ("watch", "body > .page"), ("pravila", "body > main")):
        check("футер внизу: %s" % page,
              'html[data-page="%s"] %s' % (page, middle) in css, page)
    check("колонка не ниже окна (dvh для телефонов)", "min-height:100dvh" in css)
    watch_html = io.open("web/watch.html", encoding="utf-8").read()
    check("у страницы просмотра есть футер", "<footer>" in watch_html)
    check("ссылки футера не уходят под кнопку «Связаться»",
          '[data-page="index"] footer .in{padding-right' in css)


run()
print("\n" + "=" * 60)
if FAILS:
    print("ПРОХОД 17 — не прошли: %d" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    sys.exit(1)
print("ПРОХОД 17 — обложки и описания находятся, футер внизу.")
