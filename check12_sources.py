"""Проход 12: источники видео и справочник — сроки, кэш, сбои.

Всё, что здесь проверяется, когда-то ломалось молча: сайт не падал, а
отвечал неправду. Пустой список серий после сбоя сети лежал в кэше три
часа, повисший англоязычный сайт останавливал вход, письма переставали
уходить всем из-за одного кривого названия.

Сети в проверках нет: источники (fake_sources.py) и справочник
подменяются заглушками.

Набор проверяет личный режим: режим включается переменной окружения до
импорта приложения, поэтому он выставляется здесь же, что бы ни было
задано снаружи. Демо-режим для проверки источника подменяется на время.
"""
import asyncio
import logging
import os
import sys
import tempfile
import time
import urllib.parse

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p12.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass12"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi import HTTPException                   # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402
from api import anime, catalog, mail, main, store   # noqa: E402
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


def reset():
    for lim in (main.login_guard, main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.catalog_limit):
        lim.reset()


def client():
    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": "hozyain", "password": "Zaliv-Pepel-2026"})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


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
    def __init__(self, n, players=()):
        self.ordinal = n
        self.title = ""
        self._players = list(players)

    async def a_get_sources(self):
        return self._players


class Player:
    def __init__(self, title):
        self.title = title


def run():
    store.init()
    uid = store.create_user("hozyain", "Zaliv-Pepel-2026", "admin", "hozyain")

    # ==================================================================
    group("1. Источник сначала проверяется, потом подставляется")
    c = client()
    for bad in ("../../os", "subprocess", "os"):
        r = c.get("/api/search", params={"q": "test", "source": bad})
        check(f"личный режим: «{bad}» отклонён", r.status_code == 400, r.status_code)
    demo_sources = dict(anime.anime_demo.SOURCES)
    with Patch(anime, "DEMO", True), Patch(anime, "SOURCES", demo_sources):
        for bad in ("../../os", "subprocess"):
            r = c.get("/api/search", params={"q": "test", "source": bad})
            check(f"демо: «{bad}» отклонён", r.status_code == 400, r.status_code)
            r = c.get("/api/resolve", params={"title": "test", "source": bad})
            check(f"демо: резолв с «{bad}» отклонён", r.status_code == 400, r.status_code)
        check("демо: имя личного источника заменяется на демо",
              main.pick_source("ru_a", "ru") == anime.anime_demo.NAME)
        check("демо: пустое имя — демо-источник",
              main.pick_source("", "en") == anime.anime_demo.NAME)
    check("личный: пустое имя — основной для языка",
          main.pick_source("", "ru") == "ru_a" and main.pick_source("", "en") == "en_a")

    # ==================================================================
    group("3. Общий срок на запрос к источникам")

    class Slow:
        async def a_search(self, q):
            await asyncio.sleep(30)
            return []

    old_extractors = dict(anime._extractors)
    anime._cache.clear()
    with Patch(anime, "CALL_TIMEOUT", 0.4), Patch(anime, "BUDGET", 1.2):
        for name in anime.SOURCES:
            anime._extractors[name] = Slow()
        try:
            t0 = time.monotonic()
            try:
                asyncio.run(anime.find_episodes("ru_a", "k", "Наруто"))
                code = None
            except HTTPException as exc:
                code, detail = exc.status_code, exc.detail
            took = time.monotonic() - t0
            check("серии у молчащего источника: 504 вовремя",
                  code == 504 and took < 3, f"{code} за {took:.1f} с")
            check("и сказано по-русски, что источник не отвечает",
                  code == 504 and "не отвечает" in detail, code and detail)

            c = client()
            t0 = time.monotonic()
            r = c.get("/api/search", params={"q": "Наруто"})
            took = time.monotonic() - t0
            check("поиск по четырём молчащим источникам укладывается в общий срок",
                  r.status_code == 502 and took < 3, f"{r.status_code} за {took:.1f} с")
            check("и честно говорит, что источники не отвечают",
                  "не отвечает" in r.json().get("detail", ""), r.text[:100])
            t0 = time.monotonic()
            r = c.get("/api/resolve", params={"title": "Наруто", "title_en": "Naruto"})
            took = time.monotonic() - t0
            check("резолв тоже не висит", r.status_code in (502, 504) and took < 3,
                  f"{r.status_code} за {took:.1f} с")
        finally:
            anime._extractors.clear()
            anime._extractors.update(old_extractors)

    # ==================================================================
    group("4. Сбой при получении серий не превращается в «серий нет»")

    class Found:
        def __init__(self, eps, incomplete=False):
            self.eps, self.incomplete = eps, incomplete

        async def a_get_episodes(self):
            return self.eps

    anime._cache.clear()
    anime.cache_put("anime:ru_a:empty", Found([]))
    out = asyncio.run(anime.find_episodes("ru_a", "empty", ""))
    check("пустой список серий в кэш не кладётся",
          out == [] and anime.cache_get("eps:ru_a:empty") is None)
    anime.cache_put("anime:en_d:half", Found([Ep(1)], incomplete=True))
    asyncio.run(anime.find_episodes("en_d", "half", ""))
    row = anime._cache.get("eps:en_d:half")
    check("неполный список живёт в кэше недолго",
          row is not None and row[2] == anime.SHORT_TTL, row and row[2])

    # ==================================================================
    group("5. Сбой справочника не запоминается на сутки")
    calls = []

    async def dead_get(url, params=None):
        calls.append(url)
        return None

    async def dead_anilist(query, variables):
        calls.append("anilist")
        return None

    catalog._cache.clear()
    with Patch(catalog, "_get", dead_get), Patch(catalog, "_ask_anilist", dead_anilist), \
            Patch(catalog, "MIN_GAP", 0):
        check("справочники молчат — справки нет", asyncio.run(catalog.about("Наруто")) is None)
        check("и в кэше ничего не осталось", not catalog._cache, list(catalog._cache))
        before = len(calls)
        asyncio.run(catalog.about("Наруто"))
        check("следующий запрос снова идёт в справочник", len(calls) > before)
        asyncio.run(catalog.about("Naruto", "en"))
        check("английская справка после сбоя тоже не запомнилась", not catalog._cache,
              list(catalog._cache))
        asyncio.run(catalog.random_anime())
        check("случайная страница после сбоя не запомнилась", not catalog._cache,
              list(catalog._cache))

    async def empty_get(url, params=None):
        return []

    async def empty_anilist(query, variables):
        return {"Media": None, "Page": {"media": []}}

    with Patch(catalog, "_get", empty_get), Patch(catalog, "_ask_anilist", empty_anilist), \
            Patch(catalog, "MIN_GAP", 0):
        asyncio.run(catalog.about("Несуществующее"))
        row = catalog._cache.get(catalog._about_key("ru", "Несуществующее"))
        check("честное «не нашлось» запоминается, но ненадолго",
              row is not None and row[1] == {} and row[2] == catalog.NOT_FOUND_TTL,
              row and row[2])

    # ==================================================================
    group("6. Метка франшизы одна и та же в поиске и в списке частей")
    long_title = "Перерождение в слизь, или как я стал повелителем всего на свете " * 2
    node = {"id": 5, "name": "Long", "russian": long_title.strip(), "franchise": None,
            "kind": "tv", "episodes": 12, "status": "released", "score": 7.5,
            "airedOn": {"year": 2020}, "poster": {}}

    async def gql(query, variables=None):
        return {"animes": [node]}

    catalog._cache.clear()
    with Patch(catalog, "_ask_shiki_gql", gql):
        cards = asyncio.run(catalog.search_franchises("Перерождение в слизь"))
        fid = cards[0]["id"]
        check("метка длинного названия укорочена", len(fid) <= catalog.ID_MAX, len(fid))
        parts = asyncio.run(catalog.franchise_parts(fid))
        check("по этой метке части находятся", bool(parts), parts)
        rel = asyncio.run(catalog.related_parts(long_title))
        check("и под плеером (related) список не пустой", bool(rel), rel)
        # 15: вид в карточке франшизы — и метка, и русское название.
        check("15. в карточке франшизы есть kind (не только kind_ru)",
              cards[0].get("kind") == "tv" and cards[0].get("kind_ru") == "сериал",
              cards[0].get("kind"))
    other = catalog.short_id("title:" + long_title.lower() + "2")
    check("два длинных названия с общим началом не склеиваются",
          other != fid and len(other) <= catalog.ID_MAX)

    # ==================================================================
    group("7. Ключи кэша справки не пересекаются")
    check("ru «en:naruto» и en «naruto» — разные ключи",
          catalog._about_key("ru", "en:naruto") != catalog._about_key("en", "naruto"))

    # ==================================================================
    group("8. Упрощение названия режет только целые слова")
    for title, want in (("Mushoku Tensei", None), ("Persona", None),
                        ("Overlord IV", "Overlord"), ("Магическая битва 2", "Магическая битва"),
                        ("Магическая битва 0 Фильм", "Магическая битва"),
                        ("Атака титанов 3 сезон", "Атака титанов")):
        got = catalog.simpler_titles(title)
        ok = (got == [title]) if want is None else (want in got)
        check(f"«{title}»", ok, got)

    # ==================================================================
    group("9. Совпадение с началом названия — только целыми словами")
    check("«Монстр» и «Монстры» — не точное совпадение",
          anime.relevance("Монстр", "Монстры") < main.GOOD_ENOUGH,
          anime.relevance("Монстр", "Монстры"))
    check("«Наруто» и «Наруто: Ураганные хроники» — по-прежнему похоже",
          0.72 <= anime.relevance("Наруто", "Наруто: Ураганные хроники") < main.GOOD_ENOUGH)

    # ==================================================================
    group("10. Ремейк с тем же названием — не близнец")
    store.save_progress(uid, {"key": "hxh-1999", "source": "ru_c", "title": "Hunter x Hunter",
                              "year": 1999, "total_eps": 62, "watched_ep": 62, "status": "done"})
    store.save_progress(uid, {"key": "hxh-2011", "source": "ru_b",
                              "title": "Hunter x Hunter", "year": 2011, "total_eps": 148,
                              "watched_ep": 1})
    lib = {r["key"]: r for r in store.library(uid)}
    check("версия 1999 года осталась на месте", "hxh-1999" in lib, list(lib))
    check("и её прогресс не тронут",
          lib.get("hxh-1999", {}).get("watched_ep") == 62
          and lib.get("hxh-1999", {}).get("status") == "done")
    store.save_progress(uid, {"key": "naruto-a", "source": "ru_a", "title": "Наруто",
                              "year": 2002, "total_eps": 220, "watched_ep": 40,
                              "position": 300})
    r = store.save_progress(uid, {"key": "naruto-b", "source": "ru_b",
                                  "title": "Наруто", "year": 2002, "total_eps": 220,
                                  "watched_ep": 1})
    lib = {r["key"]: r for r in store.library(uid)}
    check("тот же тайтл с другого источника склеился", "naruto-a" not in lib, list(lib))
    check("и дальний прогресс не потерялся",
          lib.get("naruto-b", {}).get("watched_ep") == 40
          and lib.get("naruto-b", {}).get("position") == 300, lib.get("naruto-b"))

    # ==================================================================
    group("11. Перевод строки в названии не ломает письма")
    c = client()
    r = c.post("/api/library/progress", json={"key": "nl", "source": "ru_a",
                                              "title": "Плохое\nназвание\r\x00"})
    stored = {x["key"]: x for x in store.library(uid)}.get("nl", {}).get("title", "")
    check("название сохраняется одной строкой",
          r.status_code == 200 and stored == "Плохое название", repr(stored))

    sent_msgs = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent_msgs.append(msg)

    with Patch(mail, "SMTP_HOST", "smtp.test"), Patch(mail, "SMTP_FROM", "a@b.c"), \
            Patch(mail.smtplib, "SMTP", FakeSMTP):
        try:
            ok = mail.send_episode("x@y.z", display_name="Хозяин",
                                   title="Строка\nвторая", episode=3)
            boom = None
        except Exception as exc:                     # noqa: BLE001
            ok, boom = False, exc
        check("письмо с переводом строки в названии собирается и уходит",
              ok and boom is None and "\n" not in sent_msgs[-1]["Subject"], boom)

    # Одна плохая строка не останавливает рассылку остальным.
    store.connect().execute("UPDATE users SET email='v@x.y', mail_new_eps=1 WHERE id=?", (uid,))
    store.connect().commit()
    rows = [{"key": "bad", "source": "ru_a", "title": "Плохой", "poster": "",
             "mailed_ep": 1},
            {"key": "good", "source": "ru_a", "title": "Хороший", "poster": "",
             "mailed_ep": 1}]
    got = []

    def fake_send(to, **kw):
        if kw["title"] == "Плохой":
            raise ValueError("сломалось на сборке")
        got.append(kw)
        return True

    async def last(source, key, title):
        return 5

    with Patch(mail, "SMTP_HOST", "smtp.test"), Patch(mail, "SMTP_FROM", "a@b.c"), \
            Patch(mail, "SITE_URL", "https://site.test"), \
            Patch(store, "watching_for_mail", lambda user_id: rows), \
            Patch(main, "last_episode_number", last), Patch(mail, "send_episode", fake_send), \
            Patch(catalog, "about", lambda *a, **k: asyncio.sleep(0, None)):
        n = asyncio.run(main.mail_new_episodes())
    check("письмо после сломанной строки всё равно ушло", n == 1 and len(got) == 1, n)

    # ==================================================================
    group("12. Ссылка в письме ведёт на тот же источник")
    with Patch(mail, "SITE_URL", "https://site.test"):
        link = main.watch_link({"key": "a&b#c d", "source": "ru_b",
                                "title": "Атака титанов"}, 7)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    check("в ссылке ключ, источник, название и серия — и всё закодировано",
          q == {"key": ["a&b#c d"], "source": ["ru_b"],
                "title": ["Атака титанов"], "ep": ["7"]}, link)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "watch.js"),
              encoding="utf-8") as f:
        js = f.read()
    check("страница просмотра читает эти параметры",
          all(f"params.get('{p}')" in js for p in ("key", "source", "title", "ep")))

    # ==================================================================
    group("13. Субтитры — только к той серии, которую просили")

    async def fake_try(source, q):
        return [{"key": "k-" + source, "title": "Атака титанов", "poster": "",
                 "year": 2013, "genres": ""}]

    async def fake_eps(source, key, title):
        return [Ep(1, [Player("Субтитры Luna")]), Ep(2, [Player("Субтитры Luna")])]

    c = client()
    with Patch(main, "try_source", fake_try), Patch(anime, "find_episodes", fake_eps):
        r = c.get("/api/subs", params={"title": "Атака титанов", "ordinal": 9})
        check("серии нет — found:false", r.status_code == 200 and r.json()["found"] is False,
              r.text[:120])
        r = c.get("/api/subs", params={"title": "Атака титанов", "ordinal": 2})
        check("серия есть — found:true", r.status_code == 200 and r.json()["found"] is True,
              r.text[:120])

    # ==================================================================
    group("14. Длинные названия принимаются")
    long_q = "Р" * 250

    async def nothing(source, q):
        return []

    async def no_about(title, lang="ru"):
        return None

    async def no_eps(source, key, title):
        raise HTTPException(status_code=404, detail="нет")

    c = client()
    with Patch(main, "try_source", nothing), Patch(catalog, "about", no_about), \
            Patch(anime, "find_episodes", no_eps):
        for path, params in (("/api/search", {"q": long_q}),
                             ("/api/about", {"title": long_q}),
                             ("/api/resolve", {"title": long_q}),
                             ("/api/where", {"title": long_q}),
                             ("/api/subs", {"title": long_q}),
                             ("/api/videos", {"key": "k", "ordinal": 1, "dub": "Д" * 150})):
            reset()
            r = c.get(path, params=params)
            check(f"{path}: длинное значение не отклоняется", r.status_code != 422,
                  r.status_code)

    # ==================================================================
    group("16. Нет библиотек источников — так и сказано")
    real_find = anime.importlib.util.find_spec
    # Библиотек у публичной версии нет — подставляем две выдуманные,
    # как будто русский и английский источники стоят на них.
    fake_libs = {"nolib_ru_api": "nolib-ru-api", "nolib_en_api": "nolib-en-api"}
    saved_libs = dict(anime.LIBRARIES)
    anime.LIBRARIES.update(fake_libs)
    anime.SOURCES["ru_a"]["library"] = "nolib_ru_api"
    anime.SOURCES["en_a"]["library"] = "nolib_en_api"

    def no_libs(name, *a, **k):
        if name in anime.LIBRARIES:
            return None
        return real_find(name, *a, **k)

    old_extractors = dict(anime._extractors)
    anime._extractors.clear()
    anime._cache.clear()
    records = []

    class Catch(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Catch(level=logging.ERROR)
    logging.getLogger("anime").addHandler(handler)
    logging.getLogger("anime").setLevel(logging.ERROR)
    try:
        with Patch(anime.importlib.util, "find_spec", no_libs):
            check("проверка видит, чего не хватает",
                  anime.missing_libraries() == ["nolib_ru_api", "nolib_en_api"],
                  anime.missing_libraries())
            with TestClient(main.app):
                pass
            check("при запуске в лог пишется ошибка про requirements-private.txt",
                  any("requirements-private" in r.getMessage() for r in records))
            c = client()
            r = c.get("/api/episodes", params={"key": "k", "source": "ru_a", "title": "Т"})
            check("серии: 503 и понятный текст",
                  r.status_code == 503 and "requirements-private" in r.text, r.text[:120])
            reset()
            r = c.get("/api/search", params={"q": "Наруто"})
            check("поиск: не «источники молчат», а 503 про библиотеки",
                  r.status_code == 503 and "библиотеки" in r.text, r.text[:120])
            reset()
            r = c.get("/api/search", params={"q": "Naruto", "lang": "en"})
            check("англоязычный поиск — то же самое", r.status_code == 503, r.status_code)
    finally:
        logging.getLogger("anime").removeHandler(handler)
        logging.getLogger("anime").setLevel(logging.CRITICAL)
        anime._extractors.update(old_extractors)
        anime.LIBRARIES.clear()
        anime.LIBRARIES.update(saved_libs)
        anime.SOURCES["ru_a"].pop("library", None)
        anime.SOURCES["en_a"].pop("library", None)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 12 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 12 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
