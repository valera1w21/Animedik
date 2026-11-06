"""Проход 18: язык сайта отдельно от языка аниме; фильтр 18+.

Язык. Раньше переключатель RU/EN в шапке менял всё сразу: включил
английские меню — и поиск уходил к англоязычному источнику. Теперь две
настройки: ui_lang (меню, кнопки, описания) и anime_lang (озвучка и
субтитры, по нему выбираются источники): ru, endub (английский дубляж)
или ensub (японская дорожка с английским текстом).

18+. Рейтинг Shikimori r_plus (этти, «Старшая школа DxD») скрыт, пока
человек сам не включит «Показывать 18+»; гостю — никогда. rx (хентай) —
только при включённых 18+ и «Хентай» (это проверяет проход 24); здесь —
что без «Хентай» его не видно нигде. Фильтр — на выходе, а не в общем
кэше: один человек не должен решать, что увидит другой.

Сети нет: справочник и источники подменены.
"""
import asyncio
import logging
import os
import re
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p18.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass18")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
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


def node(i, ru, rating, kind="tv", franchise="", year=2012, related=None):
    n = {"id": str(i), "name": "N" + str(i), "russian": ru, "licenseNameRu": None,
         "english": None, "japanese": None, "synonyms": [], "franchise": franchise,
         "kind": kind, "rating": rating, "episodes": 12, "episodesAired": 12,
         "status": "released", "score": "7", "airedOn": {"year": year, "date": f"{year}-01-01"},
         "poster": {"mainUrl": None, "originalUrl": None}}
    if related is not None:
        n["related"] = related
    return n


# Поиск «школа»: этти, обычный тайтл и хентай (его справочник с censored
# не отдаёт, но проверяем, что и отдай он — мы бы не показали).
SEARCH = [node(11617, "Старшая школа DxD", "r_plus", franchise="high_school_dxd"),
          node(1, "Школьные дни обычные", "pg_13", franchise="shkola"),
          node(666, "Школа хентая", "rx", franchise="xxx"),
          # Франшиза, где главная часть взрослая, а OVA — нет.
          node(70, "Смешанная школа", "r_plus", franchise="mixed", year=2010),
          node(71, "Смешанная школа OVA", "pg_13", kind="ova", franchise="mixed", year=2011)]
FRANCHISE = [node(11617, "Старшая школа DxD", "r_plus", franchise="high_school_dxd",
                  related=[{"relationKind": "SEQUEL", "anime": {"id": "15451"}}]),
             node(15451, "Старшая школа DxD: Новая", "r_plus", franchise="high_school_dxd",
                  year=2013, related=[{"relationKind": "PREQUEL", "anime": {"id": "11617"}}]),
             node(99, "Старшая школа DxD: спешл без рейтинга", "pg_13", kind="special",
                  franchise="high_school_dxd", year=2013,
                  related=[{"relationKind": "SIDE_STORY", "anime": {"id": "11617"}}]),
             node(667, "Хентай-вставка", "rx", kind="ova", franchise="high_school_dxd", year=2014)]
GQL = []


async def fake_gql(query, variables=None):
    GQL.append(query[:40])
    if "franchise:" in query:
        return {"p1": FRANCHISE, "p2": []}
    return {"animes": SEARCH}


def titles(items):
    return [i.get("title") for i in items]


def run():
    store.init()
    store.create_user("tester", PASS, "admin", "tester")
    store.create_user("friend", PASS, "user", "friend")

    # ==================================================================
    group("1. Две настройки языка хранятся в аккаунте")
    c = login("tester")
    r = c.post("/api/me/settings", json={"ui_lang": "en"})
    check("ui_lang=en принят", r.status_code == 200, r.text[:80])
    s = c.get("/api/me").json()["settings"]
    check("ui_lang читается обратно", s.get("ui_lang") == "en", s)
    check("старое поле lang — зеркало ui_lang", s.get("lang") == "en", s)
    check("anime_lang=ru принят",
          c.post("/api/me/settings", json={"anime_lang": "ru"}).status_code == 200)
    check("anime_lang=en (старое имя) отклонён",
          c.post("/api/me/settings", json={"anime_lang": "en"}).status_code == 400)
    check("anime_lang=xx отклонён",
          c.post("/api/me/settings", json={"anime_lang": "xx"}).status_code == 400)
    s = c.get("/api/me").json()["settings"]
    check("язык аниме не сдвинулся от языка сайта", s.get("anime_lang") == "ru"
          and s.get("ui_lang") == "en", s)
    for mode in ("endub", "ensub", "ru"):
        r = c.post("/api/me/settings", json={"anime_lang": mode})
        check(f"anime_lang={mode} принят и читается обратно", r.status_code == 200
              and r.json()["settings"].get("anime_lang") == mode, r.text[:100])
    check("ui_lang=de отклонён", c.post("/api/me/settings", json={"ui_lang": "de"}).status_code == 400)
    # Запись, сделанная до разделения: в базе только lang.
    uid = store.get_user_by_login("friend")["id"]
    store.set_settings(uid, {"lang": "en"})
    f = login("friend")
    check("старая запись: ui_lang берётся из lang",
          f.get("/api/me").json()["settings"].get("ui_lang") == "en")
    store.set_settings(uid, {})
    check("пустые настройки остаются пустыми", login("friend").get("/api/me").json()["settings"] == {})
    g = guest()
    check("гость настройки в аккаунт не пишет (у него их нет)",
          g.post("/api/me/settings", json={"ui_lang": "en"}).status_code == 403)
    check("адалт-флаг принимается только логическим",
          c.post("/api/me/settings", json={"adult": "maybe"}).status_code == 422)

    # ==================================================================
    group("2. Источники выбираются по языку аниме, не сайта")
    c = login("tester")
    ru = [x["id"] for x in c.get("/api/sources?lang=ru").json()]
    endub = [x["id"] for x in c.get("/api/sources?lang=endub").json()]
    ensub = [x["id"] for x in c.get("/api/sources?lang=ensub").json()]
    check("ru — только русские источники", ru and "en_d" not in ru
          and all(anime.source_lang(s) == "ru" for s in ru), ru)
    check("endub — только английские, в порядке anime.EN_ORDER", endub == ["en_a", "en_c", "en_d", "en_e"], endub)
    check("ensub — только английские и без «только дубляж» (RU-E больше нет)",
          ensub == ["en_a", "en_b", "en_c", "en_d"], ensub)
    check("мусор вместо языка — русские",
          [x["id"] for x in c.get("/api/sources?lang=xx").json()] == ru)
    asked = []

    async def spy(source, q):
        asked.append(source)
        return []

    with Patch(main, "try_source", spy):
        reset()
        c.get("/api/search", params={"q": "Наруто", "lang": "ru"})
        check("/api/search с lang=ru ходит только к русским", asked and all(
            anime.source_lang(s) == "ru" for s in asked), asked)
        for mode in ("endub", "ensub"):
            asked.clear()
            reset()
            c.get("/api/search", params={"q": "Naruto", "lang": mode})
            check(f"/api/search с lang={mode} — только к англоязычным",
                  asked and all(anime.source_lang(s) == "en" for s in asked), asked)
            asked.clear()
            reset()
            c.get("/api/resolve", params={"title": "Наруто", "title_en": "Naruto",
                                          "lang": mode, "alt": "x"})
            check(f"/api/resolve с lang={mode} — тоже, и RU-E сам не берёт",
                  asked and all(anime.source_lang(s) == "en" for s in asked), asked)
            asked.clear()
            reset()
            c.get("/api/where", params={"title": "Наруто", "lang": mode})
            check(f"/api/where с lang={mode} — тоже",
                  asked and all(anime.source_lang(s) == "en" for s in asked), asked)
        asked.clear()
        reset()
        c.get("/api/where", params={"title": "Наруто", "lang": "ru"})
        check("/api/where с lang=ru — без англоязычного", asked and "en_d" not in asked, asked)
    check("anime.anime_mode: en — старое имя ensub, мусор — русский",
          [anime.anime_mode(x) for x in ("en", "", None, "ru", "xx", "ENDUB", "ensub")]
          == ["ensub", "ru", "ru", "ru", "ru", "endub", "ensub"])
    check("anime.anime_lang: язык источников для выбора",
          [anime.anime_lang(x) for x in ("ru", "endub", "ensub", "en", "xx")]
          == ["ru", "en", "en", "en", "ru"])
    check("anime.anime_track: что открывать первым",
          [anime.anime_track(x) for x in ("ru", "endub", "ensub")] == ["dub", "dub", "sub"])
    check("RU-E при английском не берётся даже руками (ни ensub, ни endub)",
          main.pick_source("ru_e", "ensub") == "en_a"
          and main.pick_source("ru_e", "endub") == "en_a"
          and main.pick_source("ru_e", "ru") == "ru_e")

    # Страница: всё, что идёт к источникам, шлёт A.animeLang(), а не язык сайта.
    src_calls = re.compile(r"/api/(search|resolve|where|subs|videos|sources)\b")
    bad = []
    for name in ("index.js", "watch.js", "app.js"):
        lines = open(os.path.join(HERE, "web", name), encoding="utf-8").read().split("\n")
        for i, line in enumerate(lines):
            if "langCode()" not in line or "lang=" not in line:
                continue
            ctx = "\n".join(lines[max(0, i - 8):i + 1])
            last = re.findall(r"/api/\w+", ctx)
            if last and src_calls.match(last[-1]):
                bad.append(f"{name}:{i + 1} {last[-1]}")
    check("в JS нет запросов к источникам с языком сайта", not bad, bad)
    js = open(os.path.join(HERE, "web", "app.js"), encoding="utf-8").read()
    check("есть A.animeLang и источник по умолчанию от него",
          "animeLang: animeLang" in js and "function defaultSource() { return ''; }" in js)

    # Шапка: переключателя RU/EN нет нигде; язык сайта — на входе и в кабинете.
    for page in ("index", "watch", "stats", "pravila"):
        html = open(os.path.join(HERE, "web", page + ".html"), encoding="utf-8").read()
        check(f"{page}.html: нет RU/EN в шапке", "lang-seg" not in html)
    idx = open(os.path.join(HERE, "web", "index.html"), encoding="utf-8").read()
    gate = idx[idx.index('id="gate"'):idx.index('id="app"')]
    check("на странице входа есть «Язык сайта»", "data-set-lang" in gate and "Язык сайта" in gate)
    check("в кабинете два блока: язык сайта и язык аниме",
          "Язык сайта — меню и кнопки" in idx and "Язык аниме — озвучка и субтитры в плеере" in idx)
    check("в кабинете три языка аниме, английские включены",
          all(f'data-anime-lang="{m}"' in idx for m in ("ru", "endub", "ensub"))
          and re.search(r'data-anime-lang="en(?:dub|sub)"[^>]*disabled', idx) is None)
    check("на странице просмотра — кнопка языка", 'id="lang-btn"' in open(
        os.path.join(HERE, "web", "watch.html"), encoding="utf-8").read())
    check("в кабинете переключатель 18+", 'id="s-adult"' in idx and "без порнографии" in idx)

    # ==================================================================
    group("3. 18+: поиск по справочнику")
    catalog._cache.clear()
    with Patch(catalog, "_ask_shiki_gql", fake_gql), Patch(catalog, "MIN_GAP", 0):
        c = login("tester")
        c.post("/api/me/settings", json={"adult": False})
        reset()
        off = c.get("/api/find", params={"q": "школа"}).json()["items"]
        check("по умолчанию этти не видно", "Старшая школа DxD" not in titles(off), titles(off))
        check("обычное видно", "Школьные дни обычные" in titles(off), titles(off))
        check("хентай не видно", "Школа хентая" not in titles(off))
        mixed = [x for x in off if x["id"] == "mixed"]
        check("смешанная франшиза видна, но под невзрослой частью",
              mixed and mixed[0]["title"] == "Смешанная школа OVA", mixed)
        check("служебное поле safe наружу не уходит", all("safe" not in x for x in off))

        r = c.post("/api/me/settings", json={"adult": True})
        check("18+ включается", r.status_code == 200 and r.json()["settings"]["adult"] is True)
        n_gql = len(GQL)
        reset()
        on = c.get("/api/find", params={"q": "школа"}).json()["items"]
        check("включил — этти видно", "Старшая школа DxD" in titles(on), titles(on))
        check("включил — хентай всё равно нет", "Школа хентая" not in titles(on))
        check("смешанная франшиза — под главной частью",
              [x["title"] for x in on if x["id"] == "mixed"] == ["Смешанная школа"])
        check("ответ из общего кэша (справочник второй раз не спрашивали)", len(GQL) == n_gql)

        f = login("friend")
        other = f.get("/api/find", params={"q": "школа"}).json()["items"]
        check("кэш не протекает: у друга 18+ выключено — этти не видно",
              "Старшая школа DxD" not in titles(other), titles(other))
        g = guest()
        gg = g.get("/api/find", params={"q": "школа"}).json()["items"]
        check("гостю этти не видно", "Старшая школа DxD" not in titles(gg))

        # ==============================================================
        group("4. 18+: части франшизы и «эта история целиком»")
        c = login("tester")
        on = c.get("/api/franchise", params={"id": "high_school_dxd"}).json()["items"]
        check("включено: оба сезона в списке",
              {"Старшая школа DxD", "Старшая школа DxD: Новая"} <= set(titles(on)), titles(on))
        check("включено: хентай-вставки нет", "Хентай-вставка" not in titles(on))
        f = login("friend")
        off = f.get("/api/franchise", params={"id": "high_school_dxd"}).json()["items"]
        check("выключено: остался только невзрослый спешл", titles(off) ==
              ["Старшая школа DxD: спешл без рейтинга"], titles(off))
        check("…и порядок построен без убранных частей (нет висящих привязок)",
              all(p.get("anchor") in (None, "") for p in off), off)
        rel = f.get("/api/related", params={"title": "Старшая школа DxD"}).json()["items"]
        check("/api/related тоже фильтрует", "Старшая школа DxD" not in titles(rel), titles(rel))
        cached = catalog._cache_get("fr:high_school_dxd")
        check("в общем кэше части лежат целиком (фильтр — на выходе)",
              cached and any(p["rating"] == "r_plus" for p in cached))

    # ==================================================================
    group("5. 18+: «Рандом»")
    picks = iter([{"title": "Этти", "rating": "r_plus"}, {"title": "Обычное", "rating": "pg_13"}] * 4)

    async def fake_random(genre=None):
        return dict(next(picks))

    with Patch(catalog, "random_anime", fake_random):
        f = login("friend")
        r = f.get("/api/random").json()
        check("выключено: этти пропущено, выдано обычное", r.get("title") == "Обычное", r)
        c = login("tester")
        r = c.get("/api/random").json()
        check("включено: этти может выпасть", r.get("title") == "Этти", r)
    hentai = iter([{"title": "Хентай", "rating": "rx"}] * 5 + [{"title": "Ок", "rating": "g"}])

    async def fake_random2(genre=None):
        return dict(next(hentai))

    with Patch(catalog, "random_anime", fake_random2):
        c = login("tester")
        r = c.get("/api/random")
        check("rx не выпадает даже со включённым 18+", r.json().get("title") != "Хентай",
              (r.status_code, r.text[:80]))

    # ==================================================================
    group("6. Запасной справочник AniList")
    media = [{"id": 1, "title": {"romaji": "Ecchi Show"}, "format": "TV", "genres": ["Ecchi"],
              "isAdult": False, "seasonYear": 2012},
             {"id": 2, "title": {"romaji": "Adult Show"}, "format": "TV", "genres": [],
              "isAdult": True},
             {"id": 3, "title": {"romaji": "Kids Show"}, "format": "TV", "genres": ["Comedy"],
              "isAdult": False}]

    async def fake_anilist(query, variables):
        return {"Page": {"media": media}}

    with Patch(catalog, "_ask_anilist", fake_anilist), Patch(catalog, "MIN_GAP", 0):
        got = asyncio.run(catalog._anilist_search("show"))
    by = {p["title"]: p for p in got}
    check("isAdult отброшен (хентай не включён)", "Adult Show" not in by, list(by))
    check("жанр Ecchi — это 18+", by.get("Ecchi Show", {}).get("adult") is True, by.get("Ecchi Show"))
    check("обычное — не 18+", by.get("Kids Show", {}).get("adult") is False)
    check("pack_anilist: Ecchi → r_plus",
          catalog.pack_anilist({"title": {"romaji": "E"}, "genres": ["Ecchi"]})["rating"] == "r_plus")
    rx = catalog.pack_part(node(5, "X", "rx"))
    check("pack_part: rx упаковывается с пометкой hentai (прячется на выходе)",
          rx is not None and rx["hentai"] is True and rx["adult"] is True, rx)
    check("…и visible без «Хентай» его не пропускает",
          catalog.visible([rx], True) == [] and catalog.visible([rx], True, True) == [rx])
    lolinode = node(6, "Y", "rx")
    lolinode["genres"] = [{"name": "Hentai", "russian": "Хентай"}, {"name": "Kids", "russian": "Детское"}]
    check("pack_part: rx с признаками несовершеннолетних не упаковывается вовсе",
          catalog.pack_part(lolinode) is None)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 18 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 18 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
