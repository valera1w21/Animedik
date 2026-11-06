"""Проход 19: «нет источников» у тайтла, который у источников есть.

Живой случай с сервера владельца: «Старшая школа DxD» (Shikimori 11617).
/api/resolve отвечал 404 «Эту часть ни один источник не отдал», /api/where —
here: [] у всех восьми. А тайтл лежит у RU-B, RU-A и RU-D —
под другим русским именем, «Демоны старшей школы», а у RU-E как «Высшая
школа DxD [ТВ-1] …». Справочник это имя знает (synonyms), но сайт его не
спрашивал.

Здесь проверяется:
  * разбор строк источников: скобки, «[ТВ-2]», «(второй сезон)», «18 серия»,
    «Завершен (все серии)», «Русское / English»;
  * второй сезон не совпадает с первым как точный, «Монстр»≠«Монстры»;
  * синонимы из справочника доезжают до резолва (от страницы и сами);
  * из одноимённых частей выбирается нужная — по сезону и году;
  * /api/where и переключение источника (only=1) тоже видят синонимы;
  * тайтл, выпавший из кэша источника, находится заново по синонимам.

Сети нет: справочник и источники подменены. Строки — настоящие.
"""
import asyncio
import logging
import os
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p19.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass19")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
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
    anime._cache.clear()
    catalog._cache.clear()
    catalog._ALT_INDEX.clear()


# Настоящие строки источников (см. описание задачи).
AV_S1 = "Демоны старшей школы / High School DxD [1-12 из 12]"
AV_S2 = "Демоны старшей школы (второй сезон) / High School DxD New [1-12 из 12]"
HR_S1 = "Высшая школа DxD [ТВ-1] 1 сезон, 18 серия"
HR_S2 = "Высшая школа DxD [ТВ-2] Завершен (все серии)"

# Ответ Shikimori GraphQL для 11617 — как на живом сервере.
NODE_S1 = {
    "id": "11617", "name": "High School DxD", "russian": "Старшая школа DxD",
    "licenseNameRu": None, "english": "High School DxD", "japanese": "ハイスクールD×D",
    "synonyms": ["Демоны старшей школы", "Старшая школа: Демоны против падших",
                 "Highschool DxD"],
    "franchise": "high_school_dxd", "kind": "tv", "rating": "r_plus",
    "episodes": 12, "episodesAired": 12, "status": "released", "score": "7.3",
    "airedOn": {"year": 2012, "date": "2012-01-06"},
    "poster": {"mainUrl": "https://shikimori.one/x.jpg", "originalUrl": None},
}
# Второй сезон. Синонимы здесь — для проверки, не снимок с сервера.
NODE_S2 = dict(NODE_S1, id="15451", name="High School DxD New",
               russian="Старшая школа DxD: Новая", english="High School DxD New",
               japanese="ハイスクールD×D NEW", synonyms=["Highschool DxD New"],
               airedOn={"year": 2013, "date": "2013-07-07"})


def row(key, title, year=None, eps=None):
    return {"key": key, "title": title, "poster": "", "year": year,
            "genres": "", "episodes_total": eps}


# Что лежит у источников. Поиск у них — «что-нибудь похожее»: отдаём всё,
# где встретилось хоть одно слово запроса длиннее трёх букв.
SHELF = {
    "ru_a": [row("al-1", "Демоны старшей школы", 2012, 12)],
    "ru_b": [row("ya-1", "Демоны старшей школы", 2012, 12),
                    row("ya-2", "Демоны старшей школы 2", 2013, 12)],
    "ru_c": [],
    "ru_d": [row("av-1", AV_S1, 2012), row("av-2", AV_S2, 2013)],
    "ru_e": [row("hr-1", HR_S1, 2012), row("hr-2", HR_S2, 2013)],
}
ASKED = []


async def fake_try_source(source, q):
    ASKED.append((source, q))
    words = [w for w in anime.normalize_title(q).split() if len(w) > 3]
    out = []
    for r in SHELF.get(source, []):
        t = " ".join(anime.title_variants(r["title"]))
        if any(w in t for w in words):
            out.append(dict(r, match=round(anime.relevance(q, r["title"]), 3)))
    out.sort(key=lambda r: -r["match"])
    anime.cache_put(f"search:{source}:{q.strip().lower()}", out)
    return out


async def fake_episodes(source, key, title):
    return [object()] * 12


GQL_CALLS = []


async def fake_gql(query, variables=None):
    GQL_CALLS.append((variables or {}).get("s"))
    s = ((variables or {}).get("s") or "").lower()
    if "dxd" in s or "демоны" in s:
        return {"animes": [NODE_S1, NODE_S2]}
    return {"animes": []}


def run():
    store.init()
    store.create_user("hozyain", "Zaliv-Pepel-2026", "admin", "hozyain")

    # ==================================================================
    group("1. Разбор строк источников")
    v = anime.title_variants
    check("RU-D: два имени, счётчик вон", v(AV_S1) == ["демоны старшей школы", "high school dxd"],
          v(AV_S1))
    check("RU-D: «(второй сезон)» — у русского имени, «New» — у английского",
          v(AV_S2) == ["демоны старшей школы s2", "high school dxd new"], v(AV_S2))
    check("RU-E: «[ТВ-1] 1 сезон, 18 серия» — это просто название", v(HR_S1) == ["высшая школа dxd"],
          v(HR_S1))
    check("RU-E: «[ТВ-2] Завершен (все серии)» — второй сезон", v(HR_S2) == ["высшая школа dxd s2"],
          v(HR_S2))
    check("«[OVA-2]» остаётся видом, а не шумом", v("Тайтл [OVA-2]") == ["тайтл ova"], v("Тайтл [OVA-2]"))
    check("«[SP 1-5 из 5]» — спешл", v("Тайтл [SP 1-5 из 5]") == ["тайтл sp"], v("Тайтл [SP 1-5 из 5]"))
    check("«2 сезон», «(второй сезон)», «[ТВ-2]» и «2» — одно и то же",
          len({anime.normalize_title(x) for x in
               ("Магическая битва 2 сезон", "Магическая битва (второй сезон)",
                "Магическая битва [ТВ-2]", "Магическая битва 2")}) == 1)
    check("«Наруто (ТВ)» — по-прежнему просто «Наруто»", anime.relevance("Наруто", "Наруто (ТВ)") == 1.0)
    check("«Наруто / Naruto [1-220 из 220]» — по-прежнему точно",
          anime.relevance("Наруто", "Наруто / Naruto [1-220 из 220]") == 1.0)
    check("«86» и «Моб Психо 100» не становятся сезонами",
          anime.normalize_title("86") == "86" and anime.normalize_title("Моб Психо 100") == "моб психо 100")

    # ==================================================================
    group("2. Сезоны не путаются")
    G_ = main.GOOD_ENOUGH
    check("синоним первого сезона ↔ RU-D первый сезон — точно",
          anime.relevance("Демоны старшей школы", AV_S1) >= G_)
    check("латинское имя ↔ RU-D первый сезон — точно (по второй половине)",
          anime.relevance("High School DxD", AV_S1) >= G_)
    check("первый сезон ↔ «(второй сезон)» — НЕ точно",
          anime.relevance("Демоны старшей школы", AV_S2) < G_, anime.relevance("Демоны старшей школы", AV_S2))
    check("«High School DxD» ↔ «… New» — НЕ точно", anime.relevance("High School DxD", AV_S2) < G_)
    check("«High School DxD New» ↔ второй сезон — точно", anime.relevance("High School DxD New", AV_S2) >= G_)
    check("RU-E [ТВ-2] не точен для первого", anime.relevance("Высшая школа DxD", HR_S2) < G_)
    check("RU-E [ТВ-1] точен для «Высшая школа DxD»", anime.relevance("Высшая школа DxD", HR_S1) >= G_)
    check("«Монстр» ↔ «Монстры» — по-прежнему не точно", anime.relevance("Монстр", "Монстры") < G_)
    check("«Наруто» ↔ «Наруто: Ураганные хроники» — по-прежнему не точно",
          anime.relevance("Наруто", "Наруто: Ураганные хроники") < G_)

    # ==================================================================
    group("3. Справочник отдаёт синонимы и рейтинг")
    reset()
    part = catalog.pack_part(NODE_S1)
    check("синонимы: сначала русские, потом латинские",
          part["alt"] == ["Демоны старшей школы", "Старшая школа: Демоны против падших",
                          "Highschool DxD"], part["alt"])
    check("японское имя источникам не отдаётся", "ハイスクールD×D" not in part["alt"])
    check("рейтинг r_plus — это 18+", part["rating"] == "r_plus" and part["adult"] is True)
    check("в GraphQL-поля добавлены synonyms/licenseNameRu/english/japanese/rating",
          all(f in catalog.GQL_FIELDS for f in
              ("synonyms", "licenseNameRu", "english", "japanese", "rating")))
    names = asyncio.run(catalog.alt_names("Старшая школа DxD", "High School DxD", 2012))
    check("по названию из памяти — синонимы", names[:2] == ["Демоны старшей школы",
                                                         "Старшая школа: Демоны против падших"], names)
    catalog._ALT_INDEX.clear()
    with Patch(catalog, "_ask_shiki_gql", fake_gql), Patch(catalog, "MIN_GAP", 0):
        names = asyncio.run(catalog.alt_names("Старшая школа DxD", "", None))
    check("памяти нет — один поиск по справочнику", "Демоны старшей школы" in names, names)

    # ==================================================================
    group("4. Резолв «Старшей школы DxD»")
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": "hozyain", "password": "Zaliv-Pepel-2026"})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    base = {"title": "Старшая школа DxD", "title_en": "High School DxD", "episodes": 12,
            "year": 2012, "lang": "ru"}
    with Patch(main, "try_source", fake_try_source), \
            Patch(anime, "find_episodes", fake_episodes), \
            Patch(catalog, "_ask_shiki_gql", fake_gql), Patch(catalog, "MIN_GAP", 0):
        reset()
        GQL_CALLS.clear()
        r = c.get("/api/resolve", params=dict(base, alt="Демоны старшей школы|Highschool DxD",
                                              source="ru_a"))
        j = r.json()
        check("с синонимами от страницы — нашлось, точно",
              r.status_code == 200 and j.get("exact") is True, (r.status_code, j))
        check("…у RU-A, первый сезон", j.get("source") == "ru_a" and j.get("key") == "al-1", j)
        check("синонимы от страницы — справочник не спрашивали", not GQL_CALLS, GQL_CALLS)

        reset()
        GQL_CALLS.clear()
        r = c.get("/api/resolve", params=dict(base, source="ru_a"))
        j = r.json()
        check("без синонимов — сервер спросил справочник сам и нашёл",
              r.status_code == 200 and j.get("key") == "al-1" and j.get("exact"), (r.status_code, j))
        check("…ровно одним поиском", len(GQL_CALLS) == 1, GQL_CALLS)

        reset()
        r = c.get("/api/resolve", params=dict(base, source="ru_b",
                                              alt="Демоны старшей школы"))
        j = r.json()
        check("RU-B: первый сезон, а не «Демоны старшей школы 2»",
              j.get("key") == "ya-1" and j.get("exact"), j)

        reset()
        ASKED.clear()
        r = c.get("/api/resolve", params=dict(base, source="ru_d", only=1,
                                              alt="Демоны старшей школы"))
        j = r.json()
        check("RU-D (only=1): первый сезон", j.get("source") == "ru_d"
              and j.get("key") == "av-1" and j.get("exact"), j)
        check("only=1 — других источников не спрашивали",
              {s for s, _ in ASKED} == {"ru_d"}, ASKED)

        reset()
        s2 = {"title": "Старшая школа DxD: Новая", "title_en": "High School DxD New",
              "episodes": 12, "year": 2013, "source": "ru_d", "only": 1,
              "alt": "Highschool DxD New"}
        r = c.get("/api/resolve", params=s2)
        j = r.json()
        check("второй сезон («New») → RU-D второй сезон, точно",
              j.get("key") == "av-2" and j.get("exact"), j)

        reset()
        # Две записи с одинаковым именем у одного источника: решает год.
        SHELF["ru_c"] = [row("ag-2006", "Хеллсинг", 2006), row("ag-2001", "Хеллсинг", 2001)]
        r = c.get("/api/resolve", params={"title": "Хеллсинг", "year": 2001,
                                          "source": "ru_c", "only": 1})
        check("одноимённые части различаются по году", r.json().get("key") == "ag-2001", r.json())
        SHELF["ru_c"] = []

        reset()
        r = c.get("/api/where", params={"title": "Старшая школа DxD", "title_en": "High School DxD",
                                        "alt": "Демоны старшей школы"})
        j = r.json()
        check("/api/where: синонимы находят тайтл у источников",
              {"ru_a", "ru_b", "ru_d"} <= set(j.get("here") or []), j)

        reset()
        r = c.get("/api/where", params={"title": "Старшая школа DxD", "title_en": "High School DxD"})
        check("/api/where без синонимов — сервер узнаёт их сам",
              "ru_d" in (r.json().get("here") or []), r.json())

        # Синонимы — новые походы к источникам, и они списываются с лимита.
        reset()
        main.search_limit.allow("rs:u:1", main.search_limit.limit - 1)
        r = c.get("/api/resolve", params=dict(base, source="ru_a",
                                              alt="Демоны старшей школы"))
        check("на синонимы не хватило лимита — 429, а не «нигде нет»",
              r.status_code == 429, (r.status_code, r.text[:80]))

    # ==================================================================
    group("5. Тайтл выпал из кэша источника — находится по синонимам")
    reset()

    class Item:
        def __init__(self, key, title):
            self.data = {"id": key}
            self.title = title

        async def a_get_anime(self):
            return "anime:" + self.data["id"]

    class Extractor:
        async def a_search(self, q):
            return [Item("av-1", AV_S1)] if "демоны" in q.lower() else []

    with Patch(anime, "get_extractor", lambda s: Extractor()), \
            Patch(catalog, "_ask_shiki_gql", fake_gql), Patch(catalog, "MIN_GAP", 0):
        got = asyncio.run(anime.find_anime("ru_d", "av-1", "Старшая школа DxD"))
    check("поиск по названию справочника не нашёл — нашёл по синониму", got == "anime:av-1", got)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 19 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 19 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
