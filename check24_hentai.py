"""Проход 24: настройка «Хентай».

Раньше rx (хентай) не показывался никому и никогда. Хозяин решил иначе:

* в «Просмотр» под «18+ (этти)» — переключатель «Хентай», по умолчанию
  выключен; включить его может любой вошедший, гостю — никогда (решает
  сервер, что бы ни прислала страница);
* хентай работает только поверх 18+: hentai_ok = вошёл И adult И hentai;
  выключил 18+ — хентай выключился тоже;
* у кого включён — rx есть в поиске, в частях франшизы, в «эта история
  целиком», в описании; запрос «хентай» / «hentai» / «хент» — список
  популярного в жанре Hentai (Shikimori, жанр 12, censored: false);
  у кого нет — подсказка, каких переключателей не хватает;
* тайтлы с признаками несовершеннолетних (minors_flagged) не
  показываются никогда, даже с включённым хентаем;
* «Рандом» хентай не выбирает никогда.

Заодно (раздел 11) — новый задник Хэллоуина: ночное небо с луной.

Раздел 12 — хентай показывается, только если его можно посмотреть: хоть
у одного источника он есть и серии отдаются. С fix-21 язык не важен
(«не обязательно на каком-то языке — просто чтобы был»): русская озвучка,
английский дубляж или английские субтитры — всё годится, а плеер
открывает его на том языке, где он есть (/api/resolve с rx=1), с
подписью «Есть только на …». Нет, молчат или не успели за RX_WAIT —
карточки нет (а проверка доделывается в фоне). Обычные тайтлы держат
строгое правило языка (fix-20).

Сети нет: справочники подменены.
"""
import asyncio
import logging
import os
import re
import sys
import tempfile
import time

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p24.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass24")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient          # noqa: E402
from api import anime, catalog, helper, main, store  # noqa: E402
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


def node(i, ru, rating, kind="tv", franchise="", year=2012, genres=None, synonyms=None,
         description=None, related=None):
    n = {"id": str(i), "name": "N" + str(i), "russian": ru, "licenseNameRu": None,
         "english": None, "japanese": None, "synonyms": synonyms or [], "franchise": franchise,
         "kind": kind, "rating": rating, "episodes": 2, "episodesAired": 2,
         "status": "released", "score": "7", "airedOn": {"year": year, "date": f"{year}-01-01"},
         "poster": {"mainUrl": None, "originalUrl": None}}
    if genres is not None:
        n["genres"] = [{"name": g, "russian": g, "kind": "genre"} for g in genres]
    if description is not None:
        n["description"] = description
    if related is not None:
        n["related"] = related
    return n


# Поиск «ночь». Справочник с censored: true хентай не отдаёт — подмена
# ведёт себя так же: rx только в ответе без цензуры.
SEARCH_SAFE = [node(1, "Ночной город", "pg_13", franchise="night_city"),
               node(2, "Ночь этти", "r_plus", franchise="night_ecchi")]
SEARCH_RX = SEARCH_SAFE + [
    node(900, "Ночной экспресс", "rx", kind="ova", franchise="night_express",
         genres=["Hentai"]),
    # Признаки несовершеннолетних — разными путями: тег, название, описание.
    node(901, "Ночь в детском лагере", "rx", kind="ova", franchise="bad_1",
         genres=["Hentai", "Kids"]),
    node(902, "Ночная лоли-история", "rx", kind="ova", franchise="bad_2", genres=["Hentai"]),
    node(903, "Ночной класс", "rx", kind="ova", franchise="bad_3", genres=["Hentai"],
         description="Героиня — [b]младшеклассница[/b], которая..."),
    node(904, "Ночной синоним", "rx", kind="ova", franchise="bad_4", genres=["Hentai"],
         synonyms=["Shotacon Night"]),
    # Франшиза, где главная часть — хентай, а есть и обычная.
    node(910, "Смешанная ночь", "rx", franchise="mixed_night", year=2010, genres=["Hentai"]),
    node(911, "Смешанная ночь: ТВ", "pg_13", kind="tv", franchise="mixed_night", year=2012),
]
# Франшиза обычного тайтла с хентай-OVA внутри.
FRANCHISE_SAFE = [node(1, "Ночной город", "pg_13", franchise="night_city")]
FRANCHISE_RX = FRANCHISE_SAFE + [
    node(950, "Ночной город: OVA 18+", "rx", kind="ova", franchise="night_city", year=2014,
         genres=["Hentai"]),
    node(951, "Ночной город: детская OVA", "rx", kind="ova", franchise="night_city", year=2015,
         genres=["Hentai", "Kids"])]
GENRE_RX = [node(800, "Популярный хентай", "rx", kind="ova", franchise="pop1", genres=["Hentai"]),
            node(801, "Второй хентай", "rx", kind="ova", franchise="pop2", genres=["Hentai"]),
            node(802, "Lolicon thing", "rx", kind="ova", franchise="pop3", genres=["Hentai"])]
GQL = []


async def fake_gql(query, variables=None):
    GQL.append(query)
    uncensored = "censored: false" in query
    if "franchise:" in query:
        return {"p1": FRANCHISE_RX if uncensored else FRANCHISE_SAFE, "p2": []}
    if "genre:" in query:
        return {"p1": GENRE_RX if 'genre: "12"' in query else [], "p2": []}
    return {"animes": SEARCH_RX if uncensored else SEARCH_SAFE}


def titles(items):
    return [i.get("title") for i in items]


# Источники видео — тоже подмена (раздел 12): у кого что лежит, кто молчит,
# кто отвечает медленно. Хентай показывается, только если он есть у
# источника на языке аниме человека (main.rx_cards).
AVAIL = {}           # источник → названия, которые у него есть
SILENT = set()       # источники, которые не отвечают вовсе
SLOW = {}            # источник → задержка ответа, секунд
NO_EPS = set()       # названия, у которых список серий пустой
SRC_CALLS = []


# Что есть у тайтла англоязычного источника (поле langs из поиска).
LANGS = {}


async def fake_src(source, q):
    SRC_CALLS.append((source, q))
    if source in SLOW:
        await asyncio.sleep(SLOW[source])
    if source in SILENT:
        return None
    return [dict({"key": source + ":" + t, "title": t},
                 **({"langs": LANGS[t]} if t in LANGS else {}))
            for t in sorted(AVAIL.get(source, ())) if anime.relevance(q, t) >= 0.9]


async def fake_eps(source, key, title):
    return [] if title in NO_EPS else [1, 2]


def sources_reset(**avail):
    AVAIL.clear()
    AVAIL.update({k: set(v) for k, v in avail.items()})
    SILENT.clear()
    SLOW.clear()
    NO_EPS.clear()
    SRC_CALLS.clear()
    main._rx_known.clear()


def asked_n(title):
    """Сколько раз источники спрашивали про это название."""
    return sum(1 for _, q in SRC_CALLS if q == title)


def setting(client, **kw):
    reset()
    return client.post("/api/me/settings", json=kw)


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "hozyain")
    store.create_user("friend", PASS, "user", "friend")
    store.create_user("ecchi", PASS, "user", "ecchi")

    # ==================================================================
    group("1. Настройка: только поверх 18+ и только вошедшему")
    f = login("friend")
    s = f.get("/api/me").json()["settings"]
    check("по умолчанию хентай выключен", not s.get("hentai"), s)
    r = setting(f, hentai=True)
    check("при выключенном 18+ хентай не включается", r.status_code == 200
          and r.json()["settings"].get("hentai") is False, r.text[:120])
    check("hentai только логическим", setting(f, hentai="maybe").status_code == 422)
    r = setting(f, adult=True, hentai=True)
    check("18+ и хентай вместе — включаются", r.json()["settings"].get("adult") is True
          and r.json()["settings"].get("hentai") is True, r.json())
    r = setting(f, adult=False)
    check("выключил 18+ — хентай выключился тоже", r.json()["settings"].get("hentai") is False,
          r.json())
    r = setting(f, adult=True)
    check("включил 18+ обратно — хентай сам не включился",
          r.json()["settings"].get("hentai") is False, r.json())
    uid = store.get_user_by_login("friend")["id"]
    store.set_settings(uid, {"hentai": True})
    check("в базе hentai без adult — не значит ничего (hentai_ok)",
          not _hentai_ok_for("friend"), store.get_settings(uid))
    store.set_settings(uid, {})
    g = guest()
    check("гость настройку не пишет", g.post("/api/me/settings",
                                             json={"adult": True, "hentai": True}).status_code == 403)

    v = login("hozyain")
    setting(v, adult=True, hentai=True)
    e = login("ecchi")
    setting(e, adult=True)

    catalog._cache.clear()
    # Весь хентай из подмены справочника лежит у RU-A — разделы 2–4
    # про то, кому он виден, а не про источники (они — в разделе 12).
    sources_reset(ru_a={"Ночной экспресс", "Смешанная ночь", "Популярный хентай",
                             "Второй хентай", "Ночной город: OVA 18+"})
    with Patch(catalog, "_ask_shiki_gql", fake_gql), Patch(catalog, "MIN_GAP", 0), \
            Patch(main, "try_source", fake_src), Patch(anime, "find_episodes", fake_eps):
        # ==============================================================
        group("2. Поиск по названию")
        GQL.clear()
        reset()
        on = v.get("/api/find", params={"q": "ночь"}).json()["items"]
        check("хентай включён — rx в выдаче", "Ночной экспресс" in titles(on), titles(on))
        hx = [x for x in on if x["title"] == "Ночной экспресс"]
        check("у карточки пометка hentai и рейтинг rx (для значка)",
              hx and hx[0].get("hentai") is True and hx[0].get("rating") == "rx", hx)
        check("этти тоже на месте", "Ночь этти" in titles(on))
        check("справочник спрошен без цензуры и с жанрами/описанием",
              GQL and "censored: false" in GQL[-1] and "genres" in GQL[-1]
              and "description" in GQL[-1], GQL[-1][:160] if GQL else GQL)
        for bad in ("Ночь в детском лагере", "Ночная лоли-история", "Ночной класс",
                    "Ночной синоним"):
            check(f"признаки несовершеннолетних — «{bad}» не показан даже с хентаем",
                  bad not in titles(on), titles(on))
        mixed = [x for x in on if x["id"] == "mixed_night"]
        check("смешанная франшиза — под главной (хентай) частью",
              mixed and mixed[0]["title"] == "Смешанная ночь", mixed)
        check("служебные safe/nox наружу не уходят",
              all("safe" not in x and "nox" not in x for x in on))

        reset()
        ee = e.get("/api/find", params={"q": "ночь"}).json()["items"]
        check("только 18+ — rx нет", "Ночной экспресс" not in titles(ee)
              and "Ночь этти" in titles(ee), titles(ee))
        check("…и его выдача из своего запроса с цензурой",
              "censored: true" in GQL[-1], GQL[-1][:120])
        f = login("friend")
        ff = f.get("/api/find", params={"q": "ночь"}).json()["items"]
        check("без 18+ — ни rx, ни этти", titles(ff) == ["Ночной город"], titles(ff))
        g = guest()
        gg = g.get("/api/find", params={"q": "ночь"}).json()["items"]
        check("гость — ни rx, ни этти", titles(gg) == ["Ночной город"], titles(gg))

        # Кэш общий, но «с хентаем» и «без» — разные ключи; и фильтр на выходе
        # всё равно стоит: подложим в обычный кэш выдачу без цензуры.
        catalog._cache_put("find:подлог", catalog._cache_get("xfind:ночь"))
        reset()
        leak = e.get("/api/find", params={"q": "подлог"}).json()["items"]
        check("даже если rx попал в общий кэш — без «Хентай» его не видно",
              "Ночной экспресс" not in titles(leak) and "Смешанная ночь: ТВ" in titles(leak),
              titles(leak))
        reset()
        gleak = g.get("/api/find", params={"q": "подлог"}).json()["items"]
        check("…и гостю тоже", all(not x.get("hentai") and not x.get("adult") for x in gleak),
              titles(gleak))

        # ==============================================================
        group("3. Жанр «хентай»")
        for q in ("хентай", "Хентай", "hentai", "HENTAI", "хент", "аниме хентай", "жанр hentai"):
            check(f"«{q}» — жанр hentai", catalog.genre_query(q) == "hentai", catalog.genre_query(q))
        reset()
        fb = f.get("/api/find", params={"q": "хентай"}).json()
        check("без 18+: подсказка hentai_off, adult=false",
              fb.get("items") == [] and fb.get("hint") == "hentai_off" and fb.get("adult") is False, fb)
        reset()
        eb = e.get("/api/find", params={"q": "hentai"}).json()
        check("с 18+, но без «Хентай»: hentai_off, adult=true",
              eb.get("items") == [] and eb.get("hint") == "hentai_off" and eb.get("adult") is True, eb)
        reset()
        gb = g.get("/api/find", params={"q": "хент"}).json()
        check("гостю — подсказка guest", gb.get("items") == [] and gb.get("hint") == "guest", gb)
        GQL.clear()
        reset()
        vb = v.get("/api/find", params={"q": "хентай"}).json()
        check("с обоими — список", vb.get("genre") == "hentai" and "hint" not in vb
              and titles(vb["items"]) == ["Популярный хентай", "Второй хентай"], vb)
        q = next((x for x in GQL if "genre:" in x), "")
        check('справочник: жанр 12, censored: false на обеих страницах',
              'genre: "12"' in q and q.count("censored: false") == 2, q[:200])
        check("Lolicon в списке жанра выброшен", "Lolicon thing" not in titles(vb["items"]))
        reset()
        eb2 = e.get("/api/find", params={"q": "хентай"}).json()
        check("кэш жанра не протекает к тому, у кого хентай выключен",
              eb2.get("items") == [] and eb2.get("hint") == "hentai_off", eb2)

        # ==============================================================
        group("4. Части франшизы и «эта история целиком»")
        reset()
        vp = v.get("/api/franchise", params={"id": "night_city"}).json()["items"]
        check("с хентаем: OVA 18+ в списке частей", "Ночной город: OVA 18+" in titles(vp), titles(vp))
        check("детская OVA — нет", "Ночной город: детская OVA" not in titles(vp), titles(vp))
        reset()
        ep = e.get("/api/franchise", params={"id": "night_city"}).json()["items"]
        check("только 18+: хентай-OVA нет", titles(ep) == ["Ночной город"], titles(ep))
        reset()
        gp = g.get("/api/franchise", params={"id": "night_city"}).json()["items"]
        check("гость: хентай-OVA нет", titles(gp) == ["Ночной город"], titles(gp))
        reset()
        vr = v.get("/api/related", params={"title": "Ночной город"}).json()["items"]
        check("/api/related с хентаем — OVA есть", "Ночной город: OVA 18+" in titles(vr), titles(vr))
        reset()
        er = e.get("/api/related", params={"title": "Ночной город"}).json()["items"]
        check("/api/related без хентая — OVA нет", "Ночной город: OVA 18+" not in titles(er),
              titles(er))
        check("кэш частей «с хентаем» отдельно от обычного",
              catalog._cache_get("xfr:night_city") is not None
              and all(not p.get("hentai") for p in catalog._cache_get("fr:night_city") or []))

        # Выключил 18+ — хентай пропал отовсюду.
        setting(v, adult=False)
        reset()
        off = v.get("/api/find", params={"q": "ночь"}).json()["items"]
        check("выключил 18+ — rx пропал из поиска", "Ночной экспресс" not in titles(off), titles(off))
        reset()
        offg = v.get("/api/find", params={"q": "хентай"}).json()
        check("…и жанр снова за подсказкой", offg.get("hint") == "hentai_off", offg)
        setting(v, adult=True, hentai=True)

    # ==================================================================
    group("5. Функция minors_flagged")
    mf = catalog.minors_flagged
    check("тег Loli (любой регистр)", mf(["LOLI"]))
    check("тег словарём {name}", mf([{"name": "Primarily Child Cast"}]))
    check("тег по-русски с ё/е", mf([{"name": "x", "russian": "Сёта"}]) and mf(["сета"]))
    check("демография Kids / «Детское»", mf(["Kids"]) and mf([{"russian": "Детское"}]))
    check("Elementary School / Primarily Teen Cast", mf(["Elementary School"])
          and mf(["Primarily Teen Cast"]))
    check("название с lolicon", mf([], ["Super Lolicon Adventure"]))
    check("синоним с «лоли»", mf([], ["", "Лоли-тян"]))
    check("слово в описании", mf([], [], "She is a 12-year-old girl"))
    check("«младшеклассница» в описании", mf([], [], "Героиня — младшеклассница."))
    check("обычный хентай — не флаг", not mf(["Hentai", "Romance"], ["Night Express"],
                                            "Two adults at an office party."))
    check("имя Shotaro — не флаг", not mf([], ["Shotaro's Diary"]))
    check("18-летняя — не флаг", not mf([], [], "Ей 18-летняя... 18-year-old student"))
    check("пустое — не флаг (проверять нечего)", not mf())
    check("мусор вместо списков не роняет", not mf(None, None, None) and not mf("Loli", 5, 7))

    # ==================================================================
    group("6. Справочники упаковывают хентай только по просьбе")
    rxn = node(1, "X", "rx", genres=["Hentai"])
    check("pack_part: rx с пометкой hentai", (catalog.pack_part(rxn) or {}).get("hentai") is True)
    check("pack_part: rx с тегом Kids — None", catalog.pack_part(node(2, "Y", "rx",
                                                                       genres=["Kids"])) is None)
    check("pack_part: не rx с тегом Kids — как раньше (проверка только для хентая)",
          catalog.pack_part(node(3, "Z", "g", genres=["Kids"])) is not None)
    media = [{"id": 1, "title": {"romaji": "Adult Show"}, "format": "OVA", "genres": ["Hentai"],
              "isAdult": True, "tags": [{"name": "Office"}]},
             {"id": 2, "title": {"romaji": "Bad Show"}, "format": "OVA", "genres": ["Hentai"],
              "isAdult": True, "tags": [{"name": "Primarily Child Cast"}]},
             {"id": 3, "title": {"romaji": "Normal Show"}, "format": "TV", "genres": ["Comedy"],
              "isAdult": False}]
    asked = []

    async def fake_anilist(query, variables):
        asked.append(query)
        return {"Page": {"media": media}}

    with Patch(catalog, "_ask_anilist", fake_anilist):
        plain = asyncio.run(catalog._anilist_search("show"))
        full = asyncio.run(catalog._anilist_search("show", True))
    check("AniList без хентая: isAdult выброшен", titles(plain) == ["Normal Show"], titles(plain))
    check("AniList с хентаем: isAdult есть, с тегом детей — нет",
          titles(full) == ["Adult Show", "Normal Show"], titles(full))
    check("…запрос с хентаем — без isAdult: false и с тегами",
          "isAdult: false" not in asked[-1] and "tags { name }" in asked[-1], asked[-1][:160])
    check("…у части пометки hentai/adult", [p.get("hentai") for p in full] == [True, False]
          and full[0].get("adult") is True)
    check("pack_anilist: isAdult без хентая — None",
          catalog.pack_anilist({"title": {"romaji": "A"}, "isAdult": True}) is None)
    check("pack_anilist: isAdult с хентаем — rx",
          (catalog.pack_anilist({"title": {"romaji": "A"}, "isAdult": True}, True) or {})
          .get("rating") == "rx")
    check("pack_anilist: isAdult с тегом Loli — None даже с хентаем",
          catalog.pack_anilist({"title": {"romaji": "A"}, "isAdult": True,
                                "tags": [{"name": "Loli"}]}, True) is None)
    jk = {"title": "J", "rating": "Rx - Hentai", "genres": [{"name": "Hentai"}]}
    check("Jikan: «Rx - Hentai» → rx, без хентая — None",
          catalog.jikan_rating(jk) == "rx" and catalog.pack_jikan(jk) is None)
    check("Jikan: с хентаем — карточка", (catalog.pack_jikan(jk, True) or {}).get("rating") == "rx")
    jk2 = dict(jk, demographics=[{"name": "Kids"}])
    check("Jikan: демография Kids — None даже с хентаем", catalog.pack_jikan(jk2, True) is None)
    check("Jikan: «R+ - Mild Nudity» → r_plus",
          catalog.jikan_rating({"rating": "R+ - Mild Nudity"}) == "r_plus")
    sh = {"id": 5, "name": "S", "russian": "С"}
    full_rx = {"rating": "rx", "genres": [{"name": "Hentai", "russian": "Хентай"}],
               "description": "Взрослые коллеги."}
    check("Shikimori REST: rx без хентая — None", catalog.pack_shiki(sh, full_rx) is None)
    check("Shikimori REST: rx с хентаем — карточка",
          (catalog.pack_shiki(sh, full_rx, True) or {}).get("rating") == "rx")
    check("Shikimori REST: rx со словом в описании — None",
          catalog.pack_shiki(sh, dict(full_rx, description="Ей 10 лет, лоли..."), True) is None)

    # ==================================================================
    group("7. Описание (/api/about)")
    got = []

    async def fake_get(url, params=None):
        got.append((url, dict(params or {})))
        if url.endswith("/animes"):
            return [{"id": 77, "name": "Night Express", "russian": "Ночной экспресс",
                     "aired_on": "2016-01-01"}]
        return {"rating": "rx", "description": "Двое взрослых в поезде.",
                "genres": [{"name": "Hentai", "russian": "Хентай"}]}

    async def no_anilist(query, variables):
        return None

    catalog._cache.clear()
    with Patch(catalog, "_get", fake_get), Patch(catalog, "_ask_anilist", no_anilist), \
            Patch(catalog, "MIN_GAP", 0), Patch(catalog, "JIKAN_GAP", 0):
        reset()
        a = v.get("/api/about", params={"title": "Ночной экспресс"}).json()
        check("с хентаем: описание нашлось", a.get("found") is True
              and a.get("rating") == "rx", a)
        shiki = [p for u, p in got if u.endswith("/animes")]
        check("…Shikimori спрошен с censored=false", shiki and shiki[0].get("censored") == "false",
              shiki[:1])
        got.clear()
        reset()
        b = e.get("/api/about", params={"title": "Ночной экспресс"}).json()
        check("без хентая: rx-карточку не отдаём", b.get("found") is False
              or b.get("rating") != "rx", b)
        shiki = [p for u, p in got if u.endswith("/animes")]
        check("…и спрашиваем с цензурой", shiki and shiki[0].get("censored") == "true", shiki[:1])
        jik = [p for u, p in got if "jikan" in u]
        check("…Jikan — с sfw", all(p.get("sfw") == "true" for p in jik), jik)
        reset()
        gb = g.get("/api/about", params={"title": "Ночной экспресс"}).json()
        check("гость: rx-карточку не отдаём", gb.get("rating") != "rx", gb)

    # ==================================================================
    group("8. «Рандом» — хентай никогда")
    calls = []

    async def rx_random(genre=None):
        calls.append(genre)
        if genre:
            return {"title": "Хентай из жанра", "rating": "rx", "hentai": True}
        return {"title": "Хентай" if len(calls) < 3 else "Ок", "rating": "rx" if len(calls) < 3 else "g"}

    with Patch(catalog, "random_anime", rx_random), Patch(main, "ADULT_PICK_SHARE", 1.0):
        r = v.get("/api/random").json()
        check("с хентаем: rx не выпал ни из жанра, ни из общего списка",
              r.get("title") == "Ок" and r.get("rating") != "rx", (calls, r))
        check("жанр хентай рулеткой не спрашивается", "hentai" not in calls, calls)

    # ==================================================================
    group("9. Синонимы для резолва")
    seen = []

    async def spy_gql(query, variables=None):
        seen.append(query)
        return {"animes": []}

    catalog._cache.clear()
    catalog._ALT_INDEX.clear()          # имена из поиска выше уже запомнены
    with Patch(catalog, "_ask_shiki_gql", spy_gql), Patch(catalog, "MIN_GAP", 0):
        asyncio.run(catalog.alt_names("Ночной экспресс", "", None, True))
        asyncio.run(catalog.alt_names("Ночной экспресс", "", None))
    check("alt_names с хентаем — поиск без цензуры, без — с цензурой",
          len(seen) == 2 and "censored: false" in seen[0] and "censored: true" in seen[1],
          [x[:70] for x in seen])

    # ==================================================================
    group("10. Страница и помощник")
    html = open(os.path.join(HERE, "web", "index.html"), encoding="utf-8").read()
    js = open(os.path.join(HERE, "web", "index.js"), encoding="utf-8").read()
    app = open(os.path.join(HERE, "web", "app.js"), encoding="utf-8").read()
    css = open(os.path.join(HERE, "web", "app.css"), encoding="utf-8").read()
    grp = html[html.index('id="adult-grp"'):]
    grp = grp[:grp.index('<div class="grp">')]
    check("«Хентай» в той же группе, сразу под 18+",
          'id="s-adult"' in grp and 'id="s-hentai"' in grp
          and grp.index('id="s-adult"') < grp.index('id="s-hentai"'))
    check("описание переключателя", "Только для взрослых" in grp and 'data-ru="Хентай"' in grp)
    check("старой фразы «Хентай не показывается никогда» больше нет",
          "Хентай не показывается никогда" not in html + js)
    check("подтверждение — своим окном сайта, «Мне есть 18 лет» / «Отмена»",
          "A.ask(" in js and "Мне есть 18 лет" in js
          and not re.search(r"(?<![\w])confirm\s*\(", js))
    check("пояснения «Сначала включи 18+» и «Только для вошедших»",
          "Сначала включи 18+" in js and "Только для вошедших" in js)
    check("подсказка поиска про хентай и кнопка к настройке",
          "hentaiHint(" in js and "hentai_off" in js and "Доступно только вошедшим" in js
          and "hlpSettings('view', adultOn ? 's-hentai' : 's-adult')" in js)
    check("свой значок «18+ · хентай»", "agebadge hentai" in app and "18+ · хентай" in app
          and ".agebadge.hentai" in css)
    check("помощник знает про «Хентай»", "Хентай" in helper.load_kb()
          and "settings-hentai" in helper.TARGETS and "'settings-hentai'" in js)
    sp = helper.system_prompt(False)
    check("…и по-прежнему отказывается обсуждать откровенное",
          "откровенный контент и хентай" in sp and "Не обсуждай" in sp)

    # ==================================================================
    # Не про хентай, но в том же заходе: новый задник Хэллоуина.
    group("11. Хэллоуин: ночное небо с луной")
    hall = css[css.index("Хэллоуин: ночное небо с луной"):css.index("Сакура: переменные")]
    check("сцена собирается из своих слоёв: звёзды, луна, даль, туман, кладбище",
          all(x in app for x in ("'zvezdy'", "luna()", "kladbischeDal()", "'tuman t1'",
                                 "kladbische()", "'tuman t2'")))
    check("ни одной внешней картинки — только градиенты и SVG",
          "url(http" not in hall and "url(/" not in hall and ".png" not in hall)
    check("у всех четырёх тем свои цвета неба",
          all(f'html[data-theme="{t}"][data-holiday="halloween"]' in hall
              for t in ("noch", "ugol", "bumaga")) and 'html[data-holiday="halloween"]{' in hall)
    check("точки поверх неба — только когда они включены, тем же цветом и шагом",
          'html[data-holiday="halloween"]:not([data-dots="off"]) .prazdnik .zaliv::after' in hall
          and "var(--dots-c) 1px,transparent 1.6px) 0 0/9px 9px" in hall)
    dots = css[css.index("ТОЧКИ НА ФОНЕ"):]
    check("сам слой точек не тронут",
          "html:not([data-dots=\"off\"]) body{\n  background-image:radial-gradient(circle,var(--dots-c) 1px,"
          "transparent 1.6px);\n  background-size:9px 9px;background-attachment:fixed}" in dots)
    check("луна — в правом поле, не за краем окна (отступ считается от поля)",
          "--zazor:max(0px,calc((100vw - var(--shir,1112px)) / 2))" in hall
          and "max(var(--zazor) / 2,var(--disk) / 2 + 10px)" in hall)
    check("меньше движения — туман и звёзды стоят",
          ".tuman, .zvezdy::before, .zvezdy::after { animation: none; }" in css)
    check("окно праздника прежнее: 1 октября — 2 ноября",
          "if (м === 10 || (м === 11 && ч <= 2)) return 'halloween';" in app)
    check("деревья — со своим зерном, без Math.random",
          "function зерно(s)" in app and "Math.imul" not in app)

    # ==================================================================
    group("12. Хентай — только тот, что можно посмотреть")
    srch = [node(1, "Обычный экспресс", "pg_13", franchise="plain_x"),
            node(960, "Есть у источника", "rx", kind="ova", franchise="x_yes", genres=["Hentai"]),
            node(961, "Нет нигде", "rx", kind="ova", franchise="x_no", genres=["Hentai"]),
            node(962, "Только по-английски", "rx", kind="ova", franchise="x_en",
                 genres=["Hentai"]),
            node(963, "Медленный", "rx", kind="ova", franchise="x_slow", genres=["Hentai"]),
            node(964, "Под синонимом", "rx", kind="ova", franchise="x_syn", genres=["Hentai"],
                 synonyms=["Другое имя"]),
            node(965, "Без серий", "rx", kind="ova", franchise="x_noeps", genres=["Hentai"]),
            node(966, "Только субтитры", "rx", kind="ova", franchise="x_sub",
                 genres=["Hentai"]),
            node(970, "Смешанный экспресс", "rx", franchise="x_mixed", year=2010,
                 genres=["Hentai"]),
            node(971, "Смешанный экспресс: ТВ", "pg_13", franchise="x_mixed", year=2012)]
    genre = [node(1000 + n, "Хентай номер %d" % n, "rx", kind="ova", franchise="g%d" % n,
                  genres=["Hentai"]) for n in range(100)]
    more = [node(1200 + n, "Хентай поглубже %d" % n, "rx", kind="ova", franchise="m%d" % n,
                 genres=["Hentai"]) for n in range(3)]
    fr = [node(1, "Ночной город", "pg_13", franchise="night_city"),
          node(950, "Ночной город: OVA 18+", "rx", kind="ova", franchise="night_city", year=2014,
               genres=["Hentai"]),
          node(952, "Ночной город: Рассвет", "rx", kind="ova", franchise="night_city", year=2016,
               genres=["Hentai"])]
    gq = []

    async def gql12(query, variables=None):
        gq.append(query)
        unc = "censored: false" in query
        if "franchise:" in query:
            return {"p1": fr if unc else fr[:1], "p2": []}
        if "genre:" in query:
            if "p3:" in query:
                return {"p3": more, "p4": []}
            return {"p1": genre[:50], "p2": genre[50:]}
        return {"animes": srch if unc else srch[:1]}

    def find(client, q):
        reset()
        return client.get("/api/find", params={"q": q}).json()

    v = login("hozyain")
    catalog._cache.clear()
    sources_reset(ru_a={"Есть у источника", "Другое имя", "Без серий",
                             "Ночной город: Рассвет", "Хентай номер 3", "Хентай номер 70",
                             "Хентай поглубже 1"},
                  en_d={"Только по-английски"},
                  ru_b={"Медленный"})
    NO_EPS.add("Без серий")
    SLOW["ru_b"] = 1.5
    with Patch(catalog, "_ask_shiki_gql", gql12), Patch(catalog, "MIN_GAP", 0), \
            Patch(main, "try_source", fake_src), Patch(anime, "find_episodes", fake_eps), \
            Patch(main, "RX_WAIT", 0.6):
        r = find(v, "экспресс")
        got = titles(r["items"])
        check("rx, который есть у источника на русском, — показан", "Есть у источника" in got, got)
        check("rx без источников — спрятан", "Нет нигде" not in got, got)
        check("rx только у англоязычного источника — показан (язык не важен, fix-21)",
              "Только по-английски" in got, got)
        check("rx, найденный под синонимом из справочника, — показан",
              "Под синонимом" in got, got)
        check("rx, у которого серии не отдаются, — спрятан", "Без серий" not in got, got)
        check("медленный источник дольше срока — спрятан, а не ждём",
              "Медленный" not in got, got)
        check("обычный тайтл на месте", "Обычный экспресс" in got, got)
        check("хентай-франшиза без источников — карточкой обычной части",
              "Смешанный экспресс: ТВ" in got and "Смешанный экспресс" not in got, got)
        check("ответ говорит странице, что спрятано и что ещё проверяется",
              r.get("rx_checked") == 8 and r.get("rx_hidden") == 5
              and r.get("rx_pending", 0) >= 1, {k: r.get(k) for k in r if k != "items"})
        check("обычный тайтл у источников не проверялся", asked_n("Обычный экспресс") == 0
              and asked_n("Смешанный экспресс: ТВ") == 0, SRC_CALLS[:8])
        # «Нет» можно сказать, только когда ответили все, — медленный
        # источник держал и их. Без него проверки доходят до конца.
        SLOW.clear()
        find(v, "экспресс")
        # …и источники хентая (RX-A, проход 36): они спрашиваются первыми.
        everyone = set(anime.sources_for("ru")) | set(anime.sources_for("endub")) \
            | set(anime.sources_for("ensub")) | set(anime.RX_ORDER)
        check("«нет» — после всех источников всех языков (основные, потом узкие)",
              {s for s, q in SRC_CALLS if q == "Нет нигде"} == everyone,
              sorted({s for s, q in SRC_CALLS if q == "Нет нигде"}))
        n_yes, n_no = asked_n("Есть у источника"), asked_n("Нет нигде")
        r2 = find(v, "экспресс")
        check("повторный поиск — из памяти, источники не спрашиваются",
              asked_n("Есть у источника") == n_yes and asked_n("Нет нигде") == n_no
              and "Есть у источника" in titles(r2["items"]), (n_yes, n_no, SRC_CALLS[-4:]))
        key = main.rx_key({"title": "Нет нигде", "title_en": "N961", "year": 2012}, main.RX_ANY)
        yes = main.rx_key({"title": "Есть у источника", "title_en": "N960", "year": 2012},
                          main.RX_ANY)
        left_no = main._rx_known[key][0] - time.time()
        left_yes = main._rx_known[yes][0] - time.time()
        check("«нет» помнится ~6 часов, «есть» — ~сутки",
              5.9 * 3600 < left_no <= 6 * 3600 and 23.9 * 3600 < left_yes <= 24 * 3600,
              (left_no, left_yes))

        # Не у всех хентай включён — тогда источники не спрашиваются вовсе.
        SRC_CALLS.clear()
        e = login("ecchi")
        find(e, "экспресс")
        find(guest(), "экспресс")
        f = login("friend")
        find(f, "экспресс")
        check("без «Хентай» (18+, гость, обычный) — ни одного обращения к источникам",
              SRC_CALLS == [], SRC_CALLS[:4])

        # Источники молчат — это не «нет»: помним недолго.
        sources_reset(ru_a=set())
        SILENT.update(everyone)
        r = find(v, "экспресс")
        sk = main.rx_key({"title": "Есть у источника", "title_en": "N960", "year": 2012},
                         main.RX_ANY)
        left = main._rx_known.get(sk, (0, None))[0] - time.time()
        check("все молчат — rx спрятан, но помнится лишь четверть часа",
              "Есть у источника" not in titles(r["items"]) and 0 < left <= 15 * 60, left)

        # Язык аниме не важен (fix-21): при любом выборе видно и то, что
        # есть только у русских, и то, что только у английских (дубляж или
        # субтитры, поле langs у англоязычного источника).
        sources_reset(ru_a={"Есть у источника"},
                      en_d={"Только по-английски", "Только субтитры"})
        LANGS["Только субтитры"] = ["sub"]
        LANGS["Только по-английски"] = ["dub", "sub"]
        try:
            for mode in ("ensub", "endub", "ru"):
                reset()
                main._rx_known.clear()
                SRC_CALLS.clear()
                r = v.post("/api/me/settings", json={"anime_lang": mode})
                check(f"язык аниме переключён на {mode}", r.status_code == 200
                      and r.json()["settings"].get("anime_lang") == mode, r.text[:120])
                got = titles(find(v, "экспресс")["items"])
                check(f"{mode}: виден и английский, и русский, и только с субтитрами",
                      {"Только по-английски", "Есть у источника", "Только субтитры"} <= set(got),
                      got)
            check("память проверок одна на все языки (rx_key «any»)",
                  main.RX_ANY == "any" and main.rx_key({"title": "x"}, main.RX_ANY)
                  == main.rx_key({"title": "x"}, "any"))
        finally:
            LANGS.clear()
        reset()
        v.post("/api/me/settings", json={"anime_lang": "ru"})

        # Плеер: хентай, которого на вашем языке нет, находится на другом
        # (rx=1) — с пометкой, на каком. Без rx, без «Хентай» и у гостя —
        # строгое правило языка, как у всех тайтлов.
        sources_reset(en_d={"Только по-английски"})
        anime._cache.clear()

        def resolve(client, **kw):
            reset()
            q = {"title": "Только по-английски", "lang": "ru", "warm": "0"}
            q.update(kw)
            return client.get("/api/resolve", params=q)
        r = resolve(v, rx="1")
        check("rx=1: на русском нет — нашёлся на английском, ответ говорит на каком",
              r.status_code == 200 and r.json().get("other_lang") is True
              and r.json().get("alang") in ("endub", "ensub")
              and r.json().get("source") == "en_d", r.text[:200])
        anime._cache.clear()
        r = resolve(v)
        check("без rx (обычный тайтл) — строго: на русском нет — 404", r.status_code == 404,
              r.text[:120])
        anime._cache.clear()
        r = resolve(e, rx="1")
        check("без включённого «Хентай» rx=1 не обходит язык — 404", r.status_code == 404,
              r.text[:120])
        anime._cache.clear()
        r = resolve(guest(), rx="1")
        check("гостю — тоже 404", r.status_code == 404, r.text[:120])
        sources_reset(ru_a={"Есть у источника"})
        anime._cache.clear()
        r = resolve(v, rx="1", title="Есть у источника")
        check("есть на своём языке — свой язык, без пометки",
              r.status_code == 200 and r.json().get("alang") == "ru"
              and not r.json().get("other_lang"), r.text[:200])
        sources_reset()
        anime._cache.clear()
        # Три языка подряд у пустого тайтла — это много поисков; лимит
        # поиска здесь не проверяется (он — в проверках лимитов).
        with Patch(main.search_limit, "limit", 1000):
            r = resolve(v, rx="1", title="Нет нигде")
        check("нет ни на одном языке — 404 с понятным текстом",
              r.status_code == 404 and "ни на одном языке" in r.text, r.text[:160])
        wjs = open(os.path.join(HERE, "web", "watch.js"), encoding="utf-8").read()
        ajs = open(os.path.join(HERE, "web", "app.js"), encoding="utf-8").read()
        ijs = open(os.path.join(HERE, "web", "index.js"), encoding="utf-8").read()
        check("страница: язык только для этой страницы, настройка не меняется",
              "function setPageAnimeLang(code)" in ajs and "if (pageAnime) return pageAnime;" in ajs
              and "pageAnime = '';" in ajs)
        check("страница просмотра: rx и alang из адреса, только при включённом хентае",
              "RX && ALANG && hentaiAllowed()" in wjs and "A.setPageAnimeLang(ALANG);" in wjs)
        check("подпись «Есть только на английском / русском»",
              "'Есть только на русском' : 'Есть только на английском'" in wjs)
        check("сохранённый хентай с источника другого языка — остаётся там (rxStay)",
              "function rxStay(why)" in wjs and "if (!isRx()) return false;" in wjs)
        check("окно частей: rx=1 для хентая и язык найденного — в адрес плеера",
              "(p.hentai ? '&rx=1' : '')" in ijs and "alang: r.other_lang ? r.alang : ''" in ijs)

        # Части франшизы: хентай-OVA без источников в списке не видна.
        sources_reset(ru_a={"Ночной город: Рассвет"})
        reset()
        parts = titles(v.get("/api/franchise", params={"id": "night_city"}).json()["items"])
        check("части франшизы: rx без источников спрятан, с источником — на месте",
              "Ночной город: OVA 18+" not in parts and "Ночной город: Рассвет" in parts
              and "Ночной город" in parts, parts)
        reset()
        rel = titles(v.get("/api/related", params={"title": "Ночной город"}).json()["items"])
        check("/api/related — так же", "Ночной город: OVA 18+" not in rel
              and "Ночной город: Рассвет" in rel, rel)
        reset()
        SRC_CALLS.clear()
        ep = titles(e.get("/api/franchise", params={"id": "night_city"}).json()["items"])
        check("части франшизы без «Хентай» — без rx и без обращений к источникам",
              ep == ["Ночной город"] and SRC_CALLS == [], (ep, SRC_CALLS[:3]))

        # Жанр «хентай»: кандидатов больше (ещё две страницы), показаны
        # только те, что есть, и не больше RX_CHECK_MAX новых проверок за раз.
        sources_reset(ru_a={"Хентай номер 3", "Хентай номер 20", "Хентай номер 70",
                                 "Хентай поглубже 1"})
        gq.clear()
        r = find(v, "хентай")
        check("жанр: ещё две страницы отдельным запросом (p3, p4)",
              any("p3:" in x and "p4:" in x and 'genre: "12"' in x for x in gq), len(gq))
        got = titles(r["items"])
        check("жанр: из первых проверенных — только те, что есть",
              got == ["Хентай номер 3", "Хентай номер 20"], got)
        check("жанр: строка для страницы (watchable)", r.get("watchable") is True
              and r.get("rx_hidden", 0) > 0, {k: r.get(k) for k in r if k != "items"})
        checked = {q for _, q in SRC_CALLS}
        check("за один ответ — не больше RX_CHECK_MAX новых проверок",
              len(checked) <= main.RX_CHECK_MAX * 2 and "Хентай номер 70" not in checked,
              len(checked))
        for _ in range(4):
            r = find(v, "хентай")
        got = titles(r["items"])
        check("следующие поиски проверяют дальше — список растёт",
              "Хентай номер 70" in got and "Хентай поглубже 1" in got, got)
        sources_reset()
        r = find(v, "хентай")
        check("жанр, где ничего нет: пустой список с пометкой watchable",
              r.get("items") == [] and r.get("watchable") is True, {k: r.get(k) for k in r})

    # Медленный источник: проверка не отменяется, а доделывается в фоне —
    # следующий поиск его уже покажет. Нужен один живой цикл событий на
    # оба запроса, как у настоящего сервера (TestClient в with).
    catalog._cache.clear()
    sources_reset(ru_b={"Медленный"})
    SLOW["ru_b"] = 1.2
    with Patch(catalog, "_ask_shiki_gql", gql12), Patch(catalog, "MIN_GAP", 0), \
            Patch(main, "try_source", fake_src), Patch(anime, "find_episodes", fake_eps), \
            Patch(main, "RX_WAIT", 0.4), Patch(main, "RX_PER_SOURCE", 8), \
            TestClient(main.app) as live:
        reset()
        tok = live.post("/api/auth/login", json={"login": "hozyain", "password": PASS}).json()
        live.headers["X-CSRF-Token"] = tok["csrf"]
        r = find(live, "экспресс")
        check("медленный: в первом ответе спрятан, страница знает, что проверка идёт",
              "Медленный" not in titles(r["items"]) and r.get("rx_pending", 0) >= 1,
              {k: r.get(k) for k in r if k != "items"})
        time.sleep(2.5)
        before = asked_n("Медленный")
        r = find(live, "экспресс")
        check("…через пару секунд — показан, без нового похода к источникам",
              "Медленный" in titles(r["items"]) and asked_n("Медленный") == before,
              (titles(r["items"]), before, asked_n("Медленный")))

    # Страница: строка под выдачей.
    js = open(os.path.join(HERE, "web", "index.js"), encoding="utf-8").read()
    html = open(os.path.join(HERE, "web", "index.html"), encoding="utf-8").read()
    check("строка «Показан только хентай…» (любой язык) и пустой ответ",
          "Показан только хентай, который реально есть у источников — на любом языке" in js
          and "Сейчас этого нет ни у одного источника" in js
          and 'id="search-foot"' in html)
    check("пустой хентай не уходит искать к источникам напрямую",
          js.index("Сейчас этого нет ни у одного источника") < js.index("return doSourceSearch(q, seq);\n        }"))

    # ==================================================================
    group("13. Новый год и сакура — сцены; плашка профиля")
    app = open(os.path.join(HERE, "web", "app.js"), encoding="utf-8").read()
    css = open(os.path.join(HERE, "web", "app.css"), encoding="utf-8").read()
    html = open(os.path.join(HERE, "web", "index.html"), encoding="utf-8").read()
    check("Новый год: даль с домиком, ели в снегу, сияние, луна",
          all(x in app for x in ("zimaDal()", "zima()", "siyanie()", "function yolka(",
                                 "function domik(")))
    check("Сакура: гора, пагода, тории, деревья в цвету, фонари",
          all(x in app for x in ("sakuraDal()", "sakuraSad()", "function pagoda(",
                                 "function torii(", "function sakuraDerevo(", "function fonarik(")))
    check("ветки в правом углу больше нет (там луна)", "vetka" not in app and ".vetka" not in css)
    check("точки поверх неба — только когда включены, у обоих праздников",
          'html[data-holiday="newyear"]:not([data-dots="off"]) .prazdnik .zaliv::after' in css
          and 'html[data-holiday="sakura"]:not([data-dots="off"]) .prazdnik .zaliv::after' in css)
    check("подвал в праздник — плотная панель (общее правило для всех праздников)",
          'html[data-holiday] footer{' in css
          and 'linear-gradient(180deg,transparent,color-mix(in srgb,var(--bg) 62%' not in css)
    check("у всех тем свои цвета сцен", all(
        f'html[data-theme="{t}"][data-holiday="{h}"]' in css
        for t in ("noch", "ugol", "bumaga") for h in ("newyear", "sakura")))
    check("меньше движения — сияние, огоньки, дым и фонари стоят",
          ".siyanie, .ogonek, .fonar-svet { animation: none; }" in css)
    check("окна праздников прежние",
          "if ((м === 12 && ч >= 18) || (м === 1 && ч <= 15)) return 'newyear';" in app
          and "if ((м === 3 && ч >= 25) || (м === 4 && ч <= 15)) return 'sakura';" in app)
    check("плашка профиля: стрелка — две палочки, сходятся крестиком на месте",
          'class="wc1"' in html and 'class="wc2"' in html
          and '.who[aria-expanded="true"] .whocaret .wc1' in css
          and ".who[aria-expanded=\"true\"] .whocaret{transform:rotate(180deg)" not in css)
    opened = css[css.index('header .who[aria-expanded="true"],header .who[aria-expanded="true"]:hover{'):]
    check("открытая плашка не сдвигается",
          "transform:none" in opened[:opened.index("}")], opened[:opened.index("}")])

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 24 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 24 — все проверки пройдены.")
    return 0


def _hentai_ok_for(login_name):
    """main.hentai_ok для вошедшего — без запроса, по записи в базе."""
    user = store.get_user_by_login(login_name)

    class Who:
        is_guest = False
        user_id = user["id"]

    Who.user = user
    return main.hentai_ok(Who())


if __name__ == "__main__":
    sys.exit(run())
