"""Проход 36: источник только для хентая, своя дверь сторожа, проверка тегов.

Что проверяется (сети нет: источник хентая подменён — RX-A из
fake_sources.py с разборщиком прямо здесь):

* сторож: дверь для своего сервиса-источника открыта ровно на узел и порт
  и только внутри trusted_service; строковая проверка адресов (прокладка
  видео) туда по-прежнему не пускает;
* справочник: общая проверка тегов minors_tag и память «какой хентай
  справочник показал чистым» (rx_cleared);
* сайт: источник хентая (rx_only) не попадает ни в обычные списки, ни в
  меню; всем, кому хентай не включён, он «неизвестный» во всех ручках;
  резолв rx=1, серии и /api/videos с ним; тайтл, которого справочник не
  показывал, — «нигде нет», к источнику не идём.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import time

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p36.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass36")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient                          # noqa: E402
from api import anime, catalog, main, netguard, store             # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Long-Passw0rd-136"


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


def login(name):
    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": name, "password": PASS})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


def run_async(coro):
    return asyncio.run(coro)


def timed(fn, *a):
    t = time.monotonic()
    out = fn(*a)
    return out, time.monotonic() - t


# ------------------------------------------- подменный источник хентая (RX-A)
# Свой «сайт» здесь не нужен: разборщик отвечает сам, как будто нашёл
# один тайтл из двух серий с английскими субтитрами файлом. Видео — HLS с
# Referer, значит, пойдёт через нашу прокладку /api/hls.
ASKED = []


class RxVideo:
    def __init__(self, q):
        self.url = f"https://cdn.rx-a.example/{q}/index.m3u8"
        self.quality, self.type, self.referer = q, "m3u8", "https://rx-a.example/"


class RxSource:
    title = fake_sources.SUB_LABEL

    def __init__(self, n):
        self.subtitles = [{"label": "English", "lang": "en", "codec": "vtt",
                           "url": f"https://rx-a.example/subs/77-{n}.vtt",
                           "referer": "https://rx-a.example/"}]

    async def a_get_videos(self):
        return [RxVideo(1080), RxVideo(720)]


class RxEpisode:
    def __init__(self, n):
        self.ordinal, self.title = n, f"Серия {n}"

    async def a_get_sources(self):
        return [RxSource(self.ordinal)]


class RxAnime:
    async def a_get_episodes(self):
        return [RxEpisode(1), RxEpisode(2)]


class RxHit:
    title = "Clean Night"
    data = {"id": "77", "episodes_total": 2}

    async def a_get_anime(self):
        return RxAnime()


class RxExtractor:
    async def a_search(self, query):
        ASKED.append(query)
        return [RxHit()] if query.strip().lower() in ("clean night", "чистая ночь") else []


# ==========================================================================
def part_netguard():
    group("1. Сторож: одна дверь для своего сервиса")
    check("service_target: http://my-service:4000", netguard.service_target("http://my-service:4000")
          == ("my-service", 4000))
    check("service_target: порт по умолчанию", netguard.service_target("https://svc.lan")
          == ("svc.lan", 443))
    check("service_target: служебный порт — нет", netguard.service_target("http://x:22") is None)
    check("service_target: логин в адресе — нет",
          netguard.service_target("http://u:p@my-service:4000") is None)
    check("service_target: не http — нет", netguard.service_target("file:///etc/passwd") is None)
    check("строкой адрес своего сервиса не «интернет» (прокладка туда не пойдёт)",
          not netguard.url_ok("http://my-service:4000/watch/x"))

    import httpcore
    guarded, _ = netguard.backends()
    calls = []

    class Inner:
        async def connect_tcp(self, host, port, **kw):
            calls.append((host, port))
            return "ok"

    async def fake_resolve(host, port):
        return ["172.18.0.5"]

    saved = netguard.RESOLVE[0]
    netguard.RESOLVE[0] = fake_resolve
    try:
        b = guarded(Inner())

        async def go(host, port):
            try:
                return await b.connect_tcp(host, port, timeout=1)
            except httpcore.ConnectError:
                return "blocked"
        check("без двери my-service:4000 закрыт", run_async(go("my-service", 4000)) == "blocked")

        async def inside(host, port):
            with netguard.trusted_service("http://my-service:4000"):
                return await go(host, port)
        check("в двери my-service:4000 открыт", run_async(inside("my-service", 4000)) == "ok"
              and calls[-1] == ("my-service", 4000), calls)
        check("в двери другой порт того же узла закрыт",
              run_async(inside("my-service", 80)) == "blocked")
        check("в двери другой узел домашней сети закрыт",
              run_async(inside("router", 4000)) == "blocked")
        check("в двери 127.0.0.1 закрыт", run_async(inside("127.0.0.1", 4000)) == "blocked")

        async def after():
            with netguard.trusted_service("http://my-service:4000"):
                pass
            return await go("my-service", 4000)
        check("после блока дверь снова закрыта", run_async(after()) == "blocked")
    finally:
        netguard.RESOLVE[0] = saved


# ==========================================================================
def part_catalog():
    group("2. Справочник: общая проверка тегов и память чистого хентая")
    for t in ("Loli", "lolicon", "Shotacon", "school_girl", "School-Girls", "Schoolgirl",
              "Young Girl", "Child", "children", "Kids", "Kid", "Elementary", "Лоли",
              "Школьница", "Дети", "underage"):
        check(f"тег «{t}» — признак", catalog.minors_tag(t))
    for t in ("Childhood Friends", "Kidnapping", "Vanilla", "Big Breasts", "Детектив",
              "Teacher", "Shotaro", "", None, 42):
        check(f"тег «{t}» — не признак", not catalog.minors_tag(t))
    check("minors_flagged зовёт ту же проверку тегов",
          catalog.minors_flagged(["School-Girl"]) and catalog.minors_flagged([{"name": "Young"}]))
    _, took = timed(catalog.minors_tag, "a " * 100000)
    check("длинный «тег» разбирается быстро", took < 0.5, round(took, 3))

    catalog._RX_OK.clear()
    catalog._RX_BAD.clear()
    node = {"id": 1, "name": "Clean Night", "russian": "Чистая ночь", "kind": "ova",
            "rating": "rx", "genres": [{"name": "Hentai", "russian": "Хентай"}],
            "synonyms": ["CN Alt"], "english": "Clean Night EN"}
    check("pack_part чистого rx", (catalog.pack_part(node) or {}).get("hentai") is True)
    check("…его имена запомнены чистыми",
          catalog.rx_known_clean(["Clean Night"]) is True
          and catalog.rx_known_clean(["Чистая ночь"]) is True
          and catalog.rx_known_clean(["CN Alt"]) is True)
    bad = dict(node, id=2, name="Bad Night", russian="Плохая ночь", synonyms=["Clean Night"],
               genres=[{"name": "Hentai"}, {"name": "Loli"}])
    check("pack_part rx с тегом Loli — None", catalog.pack_part(bad) is None)
    check("…имя, общее с отброшенным, — «нет» (плохое сильнее)",
          catalog.rx_known_clean(["Clean Night"]) is False)
    check("…и само отброшенное — «нет»", catalog.rx_known_clean(["Bad Night"]) is False)
    check("незнакомое имя — «не знаем»", catalog.rx_known_clean(["Who"]) is None)

    calls = []

    async def fake_found(q, hentai=False):
        calls.append((q, hentai))
        if q == "Fresh Rx":
            return [{"title": "Fresh Rx", "title_en": "Fresh Rx", "hentai": True,
                     "rating": "rx", "alt": []}]
        if q == "Plain Show":
            return [{"title": "Plain Show", "title_en": "", "rating": "", "alt": []}]
        return []

    async def fake_al(q, hentai=False):
        return None
    saved = catalog._shiki_found, catalog._anilist_search
    catalog._shiki_found, catalog._anilist_search = fake_found, fake_al
    try:
        check("rx_cleared: справочник показал чистый rx — да",
              run_async(catalog.rx_cleared("Fresh Rx")) is True)
        check("…справочник спрашивался без цензуры", ("Fresh Rx", True) in calls, calls)
        check("rx_cleared: не хентай — нет", run_async(catalog.rx_cleared("Plain Show")) is False)
        check("rx_cleared: не нашлось — нет", run_async(catalog.rx_cleared("Nothing Here")) is False)
        check("rx_cleared: пустое имя — нет", run_async(catalog.rx_cleared(" ")) is False)
        check("rx_cleared: отброшенное — нет без похода в сеть",
              run_async(catalog.rx_cleared("Bad Night")) is False
              and ("Bad Night", True) not in calls)
    finally:
        catalog._shiki_found, catalog._anilist_search = saved


# ==========================================================================
def part_site():
    group("6. Сайт: RX-A только для хентая и только тем, кому он включён")
    for mode in ("ru", "ensub", "endub", "en"):
        check(f"RX-A нет в sources_for({mode})", "rx_a" not in anime.sources_for(mode))
        check(f"RX-A нет в меню ({mode})", "rx_a" not in anime.menu_sources(mode))
    check("в английском порядке: en_a, en_b, en_c…",
          anime.EN_ORDER[:3] == ["en_a", "en_b", "en_c"], anime.EN_ORDER)
    check("EN-B есть только в «English — субтитры»",
          "en_b" in anime.sources_for("ensub") and "en_b" not in anime.sources_for("endub"))
    first, _, _ = main.rx_groups(main.RX_ANY)
    check("проверка «есть ли хентай»: RX-A первым", first[:1] == ["rx_a"], first)
    first_en, spare_en, _ = main.rx_groups("ensub")
    check("…а в обычной проверке языка его нет", "rx_a" not in first_en + spare_en)

    store.init()
    store.create_user("plain", PASS, "user", "plain")
    store.create_user("hxuser", PASS, "user", "hxuser")
    plain = login("plain")
    hx = login("hxuser")
    reset()
    hx.post("/api/me/settings", json={"adult": True, "hentai": True})
    reset()
    st = hx.get("/api/me").json().get("settings") or {}
    check("у hx хентай включён", st.get("adult") is True and st.get("hentai") is True, st)

    def ids(c, lang):
        reset()
        return [x["id"] for x in c.get("/api/sources", params={"lang": lang}).json()]
    check("/api/sources без хентая — RX-A нет", "rx_a" not in ids(plain, "ensub"))
    check("/api/sources с хентаем, ensub — RX-A есть (rx_only)", "rx_a" in ids(hx, "ensub"))
    reset()
    row = [x for x in hx.get("/api/sources", params={"lang": "ensub"}).json() if x["id"] == "rx_a"]
    check("…с пометкой rx_only и подписью 18+", row and row[0].get("rx_only") is True
          and "18+" in row[0]["label"], row)
    check("/api/sources с хентаем, ru — RX-A нет", "rx_a" not in ids(hx, "ru"))

    reset()
    r = plain.get("/api/episodes", params={"key": "77", "source": "rx_a", "title": "Clean Night"})
    check("/api/episodes RX-A без хентая — 400", r.status_code == 400, r.status_code)
    reset()
    r = plain.get("/api/videos", params={"key": "77", "ordinal": 1, "source": "rx_a",
                                         "title": "Clean Night", "lang": "ensub"})
    check("/api/videos RX-A без хентая — 400", r.status_code == 400, r.status_code)
    reset()
    r = plain.post("/api/library/poster", json={"key": "77", "source": "rx_a", "title": "Clean Night"})
    check("/api/library/poster RX-A без хентая — 400", r.status_code == 400, r.status_code)
    reset()
    r = plain.get("/api/resolve", params={"title": "Clean Night", "source": "rx_a", "only": 1,
                                          "lang": "ensub"})
    check("/api/resolve source=rx_a без хентая — 400", r.status_code == 400, r.status_code)
    reset()
    r = hx.get("/api/resolve", params={"title": "Clean Night", "source": "rx_a", "only": 1,
                                       "lang": "ensub"})
    check("/api/resolve source=rx_a без rx=1 — 400 и у hx", r.status_code == 400, r.status_code)
    reset()
    r = plain.get("/api/search", params={"q": "Clean Night", "source": "rx_a", "lang": "ensub"})
    check("/api/search source=rx_a уходит к обычным английским, не к RX-A",
          r.status_code in (200, 502) and (r.status_code != 200 or r.json().get("source") != "rx_a"),
          r.text[:100])

    # Резолв хентая у hx: RX-A подменён, справочник «показал» Clean Night.
    catalog._RX_OK.clear()
    catalog._RX_BAD.clear()
    catalog._rx_mark(["Clean Night", "Чистая ночь"], True)
    anime._cache.clear()
    main._rx_known.clear()
    saved = anime.EXTRACTORS["rx_a"], anime._extractors.pop("rx_a", None)
    anime.EXTRACTORS["rx_a"] = RxExtractor
    try:
        reset()
        r = hx.get("/api/resolve", params={"title": "Чистая ночь", "title_en": "Clean Night",
                                           "rx": 1, "lang": "ensub", "warm": 0})
        out = r.json() if r.status_code == 200 else {}
        check("резолв rx=1 при ensub — RX-A", out.get("source") == "rx_a" and out.get("key") == "77"
              and out.get("alang") == "ensub" and not out.get("other_lang"), r.text[:200])
        reset()
        r = hx.get("/api/episodes", params={"key": "77", "source": "rx_a", "title": "Clean Night"})
        check("/api/episodes у hx — серии", r.status_code == 200
              and [x["ordinal"] for x in r.json()] == [1, 2], r.text[:120])
        reset()
        r = hx.get("/api/videos", params={"key": "77", "ordinal": 1, "source": "rx_a",
                                          "title": "Clean Night", "lang": "ensub"})
        out = r.json() if r.status_code == 200 else {}
        check("/api/videos у hx: «Subtitles (English)», поток через прокладку",
              out.get("chosen") == fake_sources.SUB_LABEL
              and any(v["url"].startswith("/api/hls?") for v in out.get("videos") or []), r.text[:200])
        check("…субтитры через /api/subtitle", any(s["src"].startswith("/api/subtitle/")
                                                   for s in out.get("subs") or []), out.get("subs"))
        reset()
        r = plain.get("/api/videos", params={"key": "77", "ordinal": 1, "source": "rx_a",
                                             "title": "Clean Night", "lang": "ensub"})
        check("тот же ответ из памяти не уходит тому, у кого хентай выключен",
              r.status_code == 400, r.status_code)
        reset()
        r = hx.get("/api/resolve", params={"title": "Unknown Rx", "title_en": "Unknown Rx",
                                           "source": "rx_a", "only": 1, "rx": 1, "lang": "ensub"})
        check("резолв на RX-A тайтла, которого справочник не показывал, — 404",
              r.status_code == 404, r.status_code)
        check("источник хентая спрашивался только о том, что показал справочник",
              ASKED and all(q in ("Clean Night", "Чистая ночь") for q in ASKED), ASKED)
    finally:
        anime.EXTRACTORS["rx_a"] = saved[0]
        anime._extractors.pop("rx_a", None)
        if saved[1] is not None:
            anime._extractors["rx_a"] = saved[1]


def run():
    part_netguard()
    part_catalog()
    part_site()
    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 36 — ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("ПРОХОД 36 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
