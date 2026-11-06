"""Проход 26: английский строгий, прокладка видео, сброс кэша стилей,
подвал и правила.

Сети нет: источники подменены (fake_sources.py: английские EN-A…EN-E и
русские RU-A…RU-H, своих имён у публичной версии нет).

* порядок и выбор английских источников (anime.EN_ORDER, sources_for,
  menu_sources, pick_source) — на подменных источниках;
* строгий английский: русский источник при endub/ensub — отказ 412;
  русские варианты из списка выкинуты; ensub — только японская речь с
  английским текстом (текст файлами, но не английский, — не годится);
  нет английского — 404 с понятным текстом; /api/subs и /api/resolve не
  уходят к русским и не берут не тот вариант;
* /api/hls: подпись, подделка, внутренние адреса, переписывание
  плейлиста, отдача кусков;
* страницы отдают свои .css/.js с ?v=<отпечаток>, /pravila не кэшируется;
* подвал в праздник — плотная панель; правила — на непрозрачном листе.
"""
import asyncio
import logging
import os
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p26.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass26")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import httpx                                                    # noqa: E402
from fastapi.testclient import TestClient                       # noqa: E402
from api import anime, hls, main, store, subtitles              # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Long-Passw0rd-126"


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
                main.write_limit, main.source_limit, main.catalog_limit, main.hls_limit):
        lim.reset()


def login(name):
    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": name, "password": PASS})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


class Patch:
    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)

    def __exit__(self, *a):
        setattr(self.obj, self.name, self.old)


# --------------------------------------------- подменные источники для /api/videos
class V:
    def __init__(self, q, referer=""):
        self.url, self.quality, self.type = f"https://cdn.example/v{q}.m3u8", q, "m3u8"
        self.referer = referer


class P:
    def __init__(self, title, subs=None, referer=""):
        self.title = title
        self.subtitles = subs or []
        self._ref = referer

    async def a_get_videos(self):
        return [V(1080, self._ref)]


class Ep:
    def __init__(self, n, players):
        self.ordinal = n
        self._p = players

    async def a_get_sources(self):
        return self._p


PLAYERS = {}


async def fake_eps(source, key, title):
    return [Ep(n, PLAYERS.get(source) or []) for n in range(1, 4)]


def videos(c, source, mode, **kw):
    reset()
    # Ответы /api/videos помнятся (fix-21, main.VIDEOS_TTL), а подмена
    # источников здесь меняется от вопроса к вопросу.
    anime._cache.clear()
    p = {"key": "k", "ordinal": 1, "source": source, "title": "Jujutsu Kaisen", "lang": mode}
    p.update(kw)
    return c.get("/api/videos", params=p)


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "Хозяин")
    store.create_user("drug", PASS, "user", "Друг")

    # ==================================================================
    group("1. Источники и порядок")
    # EN-B (проход 36) — вторым; EN-F — только если задан EN_F_URL.
    check("английские — пять, порядок одним списком (anime.EN_ORDER)",
          anime.EN_ORDER == ["en_a", "en_b", "en_c", "en_d", "en_e"],
          anime.EN_ORDER)
    check("все английские — lang=en", all(anime.source_lang(s) == "en" for s in anime.EN_ORDER))
    check("ensub — без «только дубляж» (EN-E)",
          anime.sources_for("ensub") == ["en_a", "en_b", "en_c", "en_d"])
    check("endub — все, у кого есть дубляж (EN-B — только субтитры)",
          anime.sources_for("endub") == [s for s in anime.EN_ORDER if s != "en_b"])
    check("в меню при английском нет ни одного русского (и RU-E тоже)",
          all(anime.source_lang(s) == "en" for m in ("ensub", "endub", "en")
              for s in anime.menu_sources(m)))
    check("основной английский — первый в списке", anime.default_source("ensub") == "en_a"
          and main.pick_source("", "endub") == "en_a")
    check("русский источник руками при английском не берётся",
          main.pick_source("ru_a", "endub") == "en_a"
          and main.pick_source("ru_e", "ensub") == "en_a")
    check("перебор при английском — только английские",
          all(anime.source_lang(s) == "en" for s in main.fallback_for("ensub") + main.SUB_ORDER_EN))

    # ==================================================================
    group("4. /api/videos: строгий английский")
    old_eps = anime.find_episodes
    anime.find_episodes = fake_eps
    try:
        c = login("drug")
        PLAYERS["ru_a"] = [P("Котодаб")]
        PLAYERS["ru_e"] = [P("Оригинал (+субтитры)", [{"label": "Русский", "lang": "ru",
                                                          "url": "https://s.example/ru.vtt"}])]
        for mode in ("endub", "ensub", "en"):
            r = videos(c, "ru_a", mode)
            check(f"{mode}: русский источник — отказ 412, ни одной ссылки",
                  r.status_code == 412 and "videos" not in r.text, r.status_code)
        r = videos(c, "ru_e", "ensub")
        check("ensub: RU-E (русские субтитры) — тоже 412", r.status_code == 412, r.status_code)
        r = videos(c, "ru_a", "ru")
        check("ru: русский источник как раньше", r.status_code == 200 and r.json()["chosen"] == "Котодаб")

        # Английский источник, у которого в списке оказались и русские варианты.
        PLAYERS["en_a"] = [P("Котодаб"), P("Субтитры Луна"), P(fake_sources.SUB_LABEL),
                              P(fake_sources.DUB_LABEL)]
        r = videos(c, "en_a", "endub").json()
        check("endub: первым английский дубляж", r.get("chosen") == fake_sources.DUB_LABEL, r.get("chosen"))
        check("endub: русские варианты в список не попали",
              [d["name"] for d in r["dubs"]] == [fake_sources.DUB_LABEL, fake_sources.SUB_LABEL]
              and all(d["audio"] in ("en", "ja") for d in r["dubs"]), r["dubs"])
        r = videos(c, "en_a", "ensub").json()
        check("ensub: только японская речь с английским текстом",
              [(d["name"], d["lang"], d["audio"]) for d in r["dubs"]] == [(fake_sources.SUB_LABEL, "en", "ja")]
              and r.get("chosen") == fake_sources.SUB_LABEL, r["dubs"])
        r = videos(c, "en_a", "endub", dub="Котодаб").json()
        check("попросили русскую озвучку по имени — не дали", r.get("chosen") == fake_sources.DUB_LABEL,
              r.get("chosen"))

        PLAYERS["en_a"] = [P("Котодаб"), P("Субтитры Луна")]
        r = videos(c, "en_a", "ensub")
        check("английского варианта нет — 404 с понятным текстом",
              r.status_code == 404 and "Not available in English yet" in r.json()["detail"]
              and "На английском этого пока нигде нет" in r.json()["detail"], r.text[:120])

        PLAYERS["en_a"] = [P(fake_sources.DUB_LABEL)]
        r = videos(c, "en_a", "ensub")
        check("ensub, а есть только дубляж — не подсовываем дубляж: 404", r.status_code == 404)
        r = videos(c, "en_a", "endub")
        check("endub, только дубляж — открывается", r.status_code == 200)

        PLAYERS["en_a"] = [P(fake_sources.SUB_LABEL, [{"label": "Russian", "lang": "ru",
                                                    "url": "https://s.example/r.vtt"}])]
        r = videos(c, "en_a", "ensub")
        check("ensub: текст файлами, но только русский — не отдаём (502, без ссылок)",
              r.status_code == 502 and "videos" not in r.text, (r.status_code, r.text[:100]))

        PLAYERS["en_a"] = [P(fake_sources.SUB_LABEL, [{"label": "English", "lang": "en",
                                                    "url": "https://s.example/e.vtt"}],
                                referer="https://host-a.example/")]
        r = videos(c, "en_a", "ensub").json()
        check("ensub: английский файл субтитров доходит до плеера",
              [s["lang"] for s in r.get("subs") or []] == ["en"], r.get("subs"))
        url = (r.get("videos") or [{}])[0].get("url", "")
        check("поток с Referer — через нашу прокладку /api/hls", url.startswith("/api/hls?u="), url)
        check("адрес хостинга наружу не ушёл", "cdn.example" not in url)

        PLAYERS["en_d"] = [P(fake_sources.SUB_LABEL), P(fake_sources.DUB_LABEL)]
        r = videos(c, "en_d", "ensub").json()
        url = (r.get("videos") or [{}])[0].get("url", "")
        check("поток без Referer — напрямую, как раньше", url.startswith("https://cdn.example/"), url)
    finally:
        anime.find_episodes = old_eps

    # ==================================================================
    group("5. /api/subs и /api/resolve при английском")
    asked = []

    async def spy_try(source, q):
        asked.append(source)
        return [{"key": "k-" + source, "title": "Jujutsu Kaisen", "poster": "", "year": None,
                 "genres": "", "episodes_total": 3,
                 "langs": ["sub"] if source == "en_a" else ["sub", "dub"]}]

    async def eps_ru_sub(source, key, title):
        if source == "en_a":
            return [Ep(1, [P("Субтитры Луна")])]       # русский текст на «английском»
        return [Ep(1, [P(fake_sources.SUB_LABEL)])]

    with Patch(main, "try_source", spy_try), Patch(anime, "find_episodes", eps_ru_sub):
        c = login("drug")
        r = c.get("/api/subs", params={"title": "Магическая битва", "title_en": "Jujutsu Kaisen",
                                       "lang": "ensub"}).json()
        check("/api/subs: только английские источники", asked and
              all(anime.source_lang(s) == "en" for s in asked), asked)
        check("/api/subs: русский текст у английского источника пропущен, взят английский",
              r.get("found") and r.get("source") == anime.sources_for("ensub")[1]
              and r.get("lang") == "en", r)
        asked.clear()
        reset()
        r = c.get("/api/resolve", params={"title": "Магическая битва", "title_en": "Jujutsu Kaisen",
                                          "lang": "endub"})
        check("/api/resolve endub: тайтл, у которого только субтитры, не выбран",
              r.status_code == 200 and r.json()["source"] != "en_a", r.text[:150])
        check("/api/resolve: русские не спрашивались",
              all(anime.source_lang(s) == "en" for s in asked), asked)

    async def nothing(source, q):
        return []

    with Patch(main, "try_source", nothing):
        reset()
        r = login("drug").get("/api/resolve", params={"title": "Нет такого", "lang": "ensub"})
        check("/api/resolve: нигде нет — 404 «Not available in English yet»",
              r.status_code == 404 and "Not available in English yet" in r.json()["detail"], r.text[:120])

    # ==================================================================
    group("6. /api/hls: прокладка")
    link = hls.proxied("https://cdn.example/a/master.m3u8", "https://host-a.example/")
    q = dict(x.split("=", 1) for x in link.split("?", 1)[1].split("&"))
    check("подпись сходится", hls.unpack(q["u"], q["r"], q["s"])
          == ("https://cdn.example/a/master.m3u8", "https://host-a.example/"))
    check("подделанная подпись — нет", hls.unpack(q["u"], q["r"], "0" * 32) is None)
    other = hls._b64("https://evil.example/x")
    check("чужой адрес со старой подписью — нет", hls.unpack(other, q["r"], q["s"]) is None)
    inner = hls.proxied("http://127.0.0.1:8080/api/admin/users", "https://x.example/")
    qi = dict(x.split("=", 1) for x in inner.split("?", 1)[1].split("&"))
    check("внутренний адрес не открывается даже подписанный",
          hls.unpack(qi["u"], qi["r"], qi["s"]) is None)
    check("без Referer — адрес как есть", hls.proxied("https://a.example/x.m3u8", "")
          == "https://a.example/x.m3u8")
    pl = hls.rewrite_playlist('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:4,\nseg1.ts\n'
                              'https://other.example/seg2.ts\n', "https://cdn.example/a/720/index.m3u8",
                              "https://host-a.example/")
    lines = [x for x in pl.split("\n") if x]
    check("плейлист: каждый адрес — через нас, ключ тоже",
          lines[3].startswith("/api/hls?") and lines[4].startswith("/api/hls?")
          and 'URI="/api/hls?' in lines[1] and "cdn.example" not in pl, pl)
    seg_q = dict(x.split("=", 1) for x in lines[3].split("?", 1)[1].split("&"))
    check("относительный адрес собран от плейлиста",
          hls.unpack(seg_q["u"], seg_q["r"], seg_q["s"])[0] == "https://cdn.example/a/720/seg1.ts")

    got_ref = []

    def handler(request):
        got_ref.append(request.headers.get("referer"))
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text="#EXTM3U\n#EXTINF:4,\nseg1.ts\n",
                                  headers={"content-type": "application/vnd.apple.mpegurl"})
        return httpx.Response(200, content=b"\x47" * 1000, headers={"content-type": "video/mp2t"})

    def fake_client(referer):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 headers={"Referer": referer} if referer else {})

    with Patch(hls, "client", fake_client):
        c = login("drug")
        r = c.get(link)
        check("плейлист отдан переписанным", r.status_code == 200
              and r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
              and "/api/hls?" in r.text and "seg1.ts" not in r.text, r.text[:100])
        check("хостинг спрошен с нужным Referer", got_ref[-1] == "https://host-a.example/", got_ref)
        seg = [x for x in r.text.split("\n") if x.startswith("/api/hls")][0]
        r2 = c.get(seg)
        check("кусок видео отдан как есть", r2.status_code == 200 and r2.content == b"\x47" * 1000
              and r2.headers["content-type"].startswith("video/mp2t"))
        check("подделка — 404", c.get(link[:-4] + "0000").status_code == 404)
        check("без входа — нельзя", TestClient(main.app).get(link).status_code in (401, 403))

    # ==================================================================
    group("8. Страницы: сброс кэша, подвал, правила")
    c = login("drug")
    for path, js_name in (("/", "index.js"), ("/watch", "watch.js"), ("/stats", "stats.js"),
                          ("/pravila", "pravila.js")):
        r = c.get(path)
        css_v, js_v = main.asset_version("app.css"), main.asset_version(js_name)
        check(f"{path}: app.css и {js_name} с ?v=<отпечаток>, страница no-store",
              f"/static/app.css?v={css_v}" in r.text and f"/static/{js_name}?v={js_v}" in r.text
              and r.headers.get("cache-control") == "no-store" and len(css_v) == 12, path)
    css_v = main.asset_version("app.css")
    r = c.get(f"/static/app.css?v={css_v}")
    check("файл по своему отпечатку — кэш надолго", "immutable" in r.headers.get("cache-control", ""))
    r = c.get("/static/app.css?v=000000000000")
    check("чужой отпечаток — с переспросом (no-cache)", r.headers.get("cache-control") == "no-cache")
    r = c.get("/static/app.css")
    check("без отпечатка — с переспросом", r.headers.get("cache-control") == "no-cache")
    css = open(os.path.join(HERE, "web", "app.css"), encoding="utf-8").read()
    check("подвал в праздник — плотный фон, линия сверху, без прозрачного градиента",
          'html[data-holiday] footer{' in css and "color-mix(in srgb,var(--bg) 90%,transparent)" in css
          and "62%,transparent) 70%" not in css)
    check("подвал всегда над задником и со своим фоном",
          "html[data-page] footer{flex:none;position:relative;z-index:1;background:var(--bg)}" in css)
    check("правила — на непрозрачном листе, и страховка для старой разметки",
          ".pravila-list{background:var(--surface)" in css and ".pravila:not(:has(.pravila-list))" in css)
    html = open(os.path.join(HERE, "web", "pravila.html"), encoding="utf-8").read()
    check("в разметке правил лист есть", 'class="pravila-list"' in html)

    group("9. Страница просмотра, админка, помощник")
    wjs = open(os.path.join(HERE, "web", "watch.js"), encoding="utf-8").read()
    whtml = open(os.path.join(HERE, "web", "watch.html"), encoding="utf-8").read()
    ijs = open(os.path.join(HERE, "web", "index.js"), encoding="utf-8").read()
    ihtml = open(os.path.join(HERE, "web", "index.html"), encoding="utf-8").read()
    check("412 — уходим к английским источникам, а не играем русское",
          "err.status === 412" in wjs and "relocate({ otherLang: true" in wjs)
    check("нет английского — прямо так и пишем, кнопка «Смотреть на русском»",
          "function noEnglish()" in wjs and "Not available in English yet" in wjs
          and 'id="w-notice-ru"' in whtml and "switchAnimeLang('ru')" in wjs)
    check("плеер считает /api/hls потоком HLS и ходит к нему с cookie входа",
          "function isOurHls(" in wjs and "|| isOurHls(url)" in wjs
          and "credentials: isOurHls(url) ? 'same-origin' : 'omit'" in wjs)
    check("кнопки проверки чужих источников в админке нет (в публичной версии их нет)",
          'id="cab-encheck"' not in ihtml and "/api/admin/english-check" not in ijs)
    for kb in ("helper_kb.txt", "helper_kb_en.txt"):
        t = open(os.path.join(HERE, "api", kb), encoding="utf-8").read()
        check(f"{kb}: про строгий английский",
              "Английский строгий" in t or "English is strict" in t)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 26 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 26 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
