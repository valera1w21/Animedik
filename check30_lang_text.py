"""Проход 30: английская страница — английские название, описание и жанры.

Хозяин прислал скриншот: сайт по-английски, а на странице просмотра
«Демоны старшей школы», описание «Иссэй Хёдо — классический
старшеклассник…» и жанры «Сёнэн, Этти, Комедия» — всё по-русски; по-
английски только подписи интерфейса.

Причина: справка по-английски (catalog._about_en) спрашивает сначала
AniList, а когда он молчит или не находит — отдавала русскую карточку
Shikimori целиком. Страница же название брала из полки и адреса (они
русские), а жанры — из источника видео (тоже русские).

Теперь:
  * у карточки Shikimori есть английские жанры (поле name у Shikimori) и
    официальное английское название (english);
  * английская справка без AniList: название и жанры — английские, описание
    — у Jikan (MyAnimeList); английского описания нет нигде — русское с
    пометкой языка (about_lang), страница подписывает «Only in Russian»;
    пустым описание не бывает;
  * /api/about?lang=en не отдаёт русских жанров ни при каком пути;
  * кэш — свой у каждого языка;
  * страница просмотра: название, жанры и описание — на языке сайта;
    «Рандом» — английские жанры от сервера; значки 18+ от жанров не зависят.

Сети нет: справочники подменены.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p30.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["UPDATES_EVERY_MIN"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass30")
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

PASS = "Po-Angliyski-2026"
FAILS = []
G = [""]
CYR = __import__("re").compile(r"[а-яё]", __import__("re").I)


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
    for lim in (main.api_limit, main.search_limit, main.catalog_limit, main.source_limit):
        lim.reset()


BRIEF = {"id": 8074, "name": "High School DxD", "russian": "Демоны старшей школы",
         "kind": "tv", "score": "7.3", "aired_on": "2012-01-06", "episodes": 12}
FULL = {"id": 8074, "rating": "r_plus", "english": ["High School DxD"],
        "description": "Иссэй Хёдо — классический старшеклассник, мечтающий о гареме.",
        "genres": [{"name": "Shounen", "russian": "Сёнэн"}, {"name": "Ecchi", "russian": "Этти"},
                   {"name": "Comedy", "russian": "Комедия"}]}
JIKAN_HIT = {"data": [{"title": "High School DxD", "title_english": "High School DxD",
                       "type": "TV", "year": 2012, "rating": "R+ - Mild Nudity",
                       "synopsis": "Issei Hyoudou is a perverted high schooler.",
                       "genres": [{"name": "Comedy"}, {"name": "Ecchi"}]}]}
STATE = {"jikan": True, "anilist": None}
ASKED = []


async def fake_get(url, params=None):
    ASKED.append(url)
    if url.startswith(catalog.JIKAN):
        return JIKAN_HIT if STATE["jikan"] else {"data": []}
    if url.rstrip("/").endswith("/animes"):
        return [dict(BRIEF)]
    if url.rstrip("/").split("/")[-1].isdigit():
        return dict(FULL)
    return None


async def fake_anilist(query, variables):
    return STATE["anilist"]


def about(c, lang, title="Демоны старшей школы"):
    reset()
    return c.get("/api/about", params={"title": title, "lang": lang, "title_en": "High School DxD",
                                       "year": "2012"}).json()


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "hozyain")
    c = TestClient(main.app)
    reset()
    r = c.post("/api/auth/login", json={"login": "hozyain", "password": PASS, "code": ""})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]

    with Patch(catalog, "_get", fake_get), Patch(catalog, "_ask_anilist", fake_anilist), \
            Patch(catalog, "MIN_GAP", 0), Patch(catalog, "JIKAN_GAP", 0):
        # ==============================================================
        group("1. AniList молчит — английская справка всё равно английская")
        catalog._cache.clear()
        STATE.update(jikan=True, anilist=None)
        en = about(c, "en")
        check("нашлось", en.get("found") is True, en)
        check("название — английское", en.get("title") == "High School DxD", en.get("title"))
        check("описание — английское (Jikan)",
              en.get("about", "").startswith("Issei Hyoudou") and en.get("about_lang") == "en",
              en.get("about"))
        check("жанры — английские, без единого русского слова",
              en.get("genres") and not any(CYR.search(g) for g in en["genres"])
              and "Ecchi" in en["genres"], en.get("genres"))
        check("рейтинг на месте (18+ решается по рейтингу, не по жанрам)",
              en.get("rating") == "r_plus", en.get("rating"))

        # ==============================================================
        group("2. Русская страница — как была")
        ru = about(c, "ru")
        check("название русское", ru.get("title") == "Демоны старшей школы", ru.get("title"))
        check("описание русское", ru.get("about", "").startswith("Иссэй") and ru.get("about_lang")
              == "ru", ru.get("about"))
        check("жанры русские", ru.get("genres") == ["Сёнэн", "Этти", "Комедия"], ru.get("genres"))
        keys = [k for k in catalog._cache if k.startswith("about:")]
        check("кэш у языков свой", any(k.startswith("about:en:") for k in keys)
              and any(k.startswith("about:ru:") for k in keys), keys)

        # ==============================================================
        group("3. Английского описания нет нигде — русское с пометкой, не пусто")
        catalog._cache.clear()
        STATE.update(jikan=False, anilist=None)
        en = about(c, "en")
        check("описание не пустое — русское", en.get("about", "").startswith("Иссэй"), en)
        check("и помечено как русское (страница подпишет «Only in Russian»)",
              en.get("about_lang") == "ru", en.get("about_lang"))
        check("название и жанры при этом английские",
              en.get("title") == "High School DxD" and en.get("genres")
              and not any(CYR.search(g) for g in en["genres"]), (en.get("title"), en.get("genres")))

        # ==============================================================
        group("4. AniList ответил — его английское")
        catalog._cache.clear()
        STATE["anilist"] = {"Media": {"id": 1, "idMal": 8074, "title": {
            "romaji": "High School DxD", "english": "High School DxD"},
            "seasonYear": 2012, "episodes": 12, "format": "TV",
            "genres": ["Action", "Comedy", "Ecchi"],
            "description": "Issei is <i>a</i> high schooler.", "isAdult": False}}
        en = about(c, "en")
        check("описание и жанры от AniList", en.get("about", "").startswith("Issei is a")
              and en.get("genres") == ["Action", "Comedy", "Ecchi"], (en.get("about"), en.get("genres")))
        STATE["anilist"] = None

        # ==============================================================
        group("5. «Рандом»: английские жанры и название приходят с сервера")
        pick = catalog.pack_shiki(BRIEF, FULL)
        check("genres_en и официальное английское название у карточки",
              pick.get("genres_en") == ["Shounen", "Ecchi", "Comedy"]
              and pick.get("title_eng") == "High School DxD", pick)
        check("русские жанры — как были", pick.get("genres") == ["Сёнэн", "Этти", "Комедия"])
        jk = catalog.pack_jikan(JIKAN_HIT["data"][0])
        check("Jikan: английские жанры рядом с русскими",
              jk.get("genres_en") == ["Comedy", "Ecchi"] and jk.get("genres")
              and all(CYR.search(g) for g in jk["genres"]), jk)

    # ==================================================================
    group("6. Страница")
    w = open(os.path.join(HERE, "web", "watch.js"), encoding="utf-8").read()
    ix = open(os.path.join(HERE, "web", "index.js"), encoding="utf-8").read()
    ap = open(os.path.join(HERE, "web", "app.js"), encoding="utf-8").read()
    check("плеер: название — на языке сайта (headName, английское из справочника)",
          "function headName()" in w and "var name = headName();" in w and "st.enTitle = r.title;" in w)
    check("плеер: жанры — из справочника на языке сайта, русские на английской не показываются",
          "function paintTags()" in w and "st.aboutTags = { year: r.year || '', genres: r.genres.slice() };" in w
          and "!(A.langCode() === 'en' && /[а-яё]/i.test(g))" in w)
    check("плеер: русское описание на английской странице — с подписью «Only in Russian»",
          "'Only in Russian'" in w)
    check("смена языка сбрасывает английское название и жанры",
          "st.aboutTags = null;\n    st.enTitle = '';" in w)
    check("«Рандом»: genres_en от сервера, русское описание — с подписью",
          "if (it.genres_en && it.genres_en.length) return it.genres_en;" in ix
          and "'Only in Russian'" in ix)
    check("название на английском: официальное английское, если есть",
          "row.title_eng || row.title_en" in ap)
    badge = ap[ap.index("function ageBadge(it)"):]
    badge = badge[:badge.index("\n  }\n")]
    check("значки 18+ / хентай — по рейтингу, не по названиям жанров",
          "genres" not in badge and "it.rating === 'rx'" in badge, badge[:80])

    print()
    if FAILS:
        print("ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("Все проверки прохода 30 прошли.")


if __name__ == "__main__":
    run()
