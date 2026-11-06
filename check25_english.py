"""Проход 25: английский язык аниме и субтитры отдельными файлами.

Три варианта языка аниме: ru, endub (английский дубляж) и ensub
(японская речь с английским текстом). Оба английских — у английского
источника (подменный EN-D): у него на тайтл два варианта, sub и dub;
разница — что открыть первым. У русского источника (подменный RU-E) есть
«Оригинал (+субтитры)»: текст отдельными файлами, и английский среди них
бывает.

Отдельные файлы (.vtt, .srt, .ass) идут через наш сервер: страница
получает номер, сервер по номеру отдаёт только то, что сам получил от
источника, и всегда в WebVTT.

Сети нет: источники (fake_sources.py) и скачивание файлов подменены.
"""
import asyncio
import logging
import os
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p25.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass25")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient                 # noqa: E402
from api import anime, main, store, subtitles     # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Long-Passw0rd-125"


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


# ---------------------------------------------------------- подменные источники
class V:
    def __init__(self, q):
        self.url, self.quality, self.type = "https://cdn.example/v%d.m3u8" % q, q, "m3u8"


class P:
    def __init__(self, title, subs=None):
        self.title = title
        self.subtitles = subs or []

    async def a_get_videos(self):
        return [V(720), V(480)]


class Ep:
    def __init__(self, n, players):
        self.ordinal = n
        self._p = players

    async def a_get_sources(self):
        return self._p


def players_for(source):
    if source == "en_d":
        return [P(fake_sources.SUB_LABEL), P(fake_sources.DUB_LABEL)]
    if source == "ru_e":
        return [P("Оригинал (+субтитры)", [
            {"label": "Русский", "lang": "ru", "url": "https://subs.example/ru.vtt"},
            {"label": "English", "lang": "en", "url": "https://subs.example/en.srt"},
            # Свой адрес и домашняя сеть наружу не уходят никогда.
            {"label": "Local", "lang": "en", "url": "http://127.0.0.1/x.vtt"},
            {"label": "LAN", "lang": "en", "url": "http://192.168.1.5/x.vtt"}]),
            P("KotoDub")]
    return [P("KotoDub"), P("Субтитры Luna")]


async def fake_eps(source, key, title):
    return [Ep(n, players_for(source)) for n in range(1, 4)]


FETCHED = []


async def fake_vtt(meta):
    FETCHED.append(meta["url"])
    return subtitles.to_vtt(b"1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i> there\r\n")


def run():
    store.init()
    store.create_user("hozyain", PASS, "user", "hozyain")

    group("перевод в WebVTT")
    srt = ("1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i>, <b>world</b>\r\n\r\n"
           "2\r\n00:01:02,05 --> 00:01:03,900\r\nTwo <script>x</script>\r\nlines\r\n").encode("utf-8")
    v = subtitles.to_vtt(srt)
    check("SRT: заголовок WEBVTT", v.startswith("WEBVTT\n"), v[:20])
    check("SRT: запятая в времени → точка", "00:00:01.000 --> 00:00:02.500" in v, v)
    check("SRT: доли секунды дополняются до трёх знаков", "00:01:02.050 --> 00:01:03.900" in v, v)
    check("SRT: теги оформления сняты, остальное экранировано",
          "Hello, world" in v and "<script>" not in v and "&lt;script&gt;" in v, v)
    check("SRT в cp1251 читается", "Привет" in subtitles.to_vtt(
        "1\n00:00:01,000 --> 00:00:02,000\nПривет\n".encode("cp1251")))
    ass = ("[Script Info]\nTitle: x\n\n[V4+ Styles]\nFormat: Name, Fontname\n\n[Events]\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           "Dialogue: 0,0:00:05.20,0:00:07.00,Default,,0,0,0,,{\\i1}Wait,{\\i0} what?\\NNo way\n"
           "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,First\n"
           "Comment: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,hidden\n").encode("utf-8")
    v = subtitles.to_vtt(ass)
    check("ASS: реплики по времени, запятые в тексте целы, \\N — перенос",
          v.index("First") < v.index("Wait, what?") and "Wait, what?\nNo way" in v, v)
    check("ASS: время h:mm:ss.cc → hh:mm:ss.mmm", "00:00:05.200 --> 00:00:07.000" in v, v)
    check("ASS: Comment не попадает", "hidden" not in v)
    check("VTT отдаётся как есть", subtitles.to_vtt(b"\xef\xbb\xbfWEBVTT\n\n00:00.000 --> 00:01.000\nhi")
          .startswith("WEBVTT"))

    check("язык по подписи", [subtitles.guess_lang(x) for x in ("Английский", "Українська", "Japanese")]
          == ["en", "uk", "ja"])
    check("язык по коду важнее подписи", subtitles.guess_lang("Русский", "en") == "en")

    group("номера вместо адресов")
    reg = subtitles.register([
        {"label": "English", "lang": "en", "url": "https://s.example/a.vtt"},
        {"label": "English", "lang": "en", "url": "https://s.example/a.vtt"},
        {"label": "x", "url": "http://localhost/a.vtt"},
        {"label": "x", "url": "http://10.0.0.1/a.vtt"},
        {"label": "x", "url": "file:///etc/passwd"},
    ])
    check("повтор и внутренние адреса отброшены", len(reg) == 1, reg)
    check("наружу — только номер, подпись, язык и наш адрес",
          set(reg[0]) == {"id", "label", "lang", "src"}
          and reg[0]["src"] == "/api/subtitle/" + reg[0]["id"] + ".vtt", reg)
    check("кривой номер не ищется", subtitles.lookup("../../etc") is None
          and subtitles.lookup("A" * 32) is None)

    anime.find_episodes, old_eps = fake_eps, anime.find_episodes
    subtitles.fetch_vtt, old_fetch = fake_vtt, subtitles.fetch_vtt
    try:
        c = login("hozyain")

        group("/api/videos: что открыть первым")
        r = c.get("/api/videos", params={"key": "k", "ordinal": 1, "source": "en_d",
                                         "title": "Oreimo", "lang": "ensub"}).json()
        check("ensub — субтитры первыми", r.get("chosen") == fake_sources.SUB_LABEL
              and r.get("track") == "sub", r.get("chosen"))
        check("ensub строгий: в списке только японская речь с английским текстом",
              [(d["name"], d["sub"], d["lang"], d["audio"]) for d in r["dubs"]]
              == [(fake_sources.SUB_LABEL, True, "en", "ja")], r["dubs"])
        r = c.get("/api/videos", params={"key": "k", "ordinal": 1, "source": "en_d",
                                         "title": "Oreimo", "lang": "endub"}).json()
        check("endub — дубляж первым", r.get("chosen") == fake_sources.DUB_LABEL
              and r.get("track") == "dub", r.get("chosen"))
        r = c.get("/api/videos", params={"key": "k", "ordinal": 1, "source": "ru_a",
                                         "title": "x", "lang": "ru"}).json()
        check("ru — русская озвучка первой, audio=ru", r.get("chosen") == "KotoDub"
              and r["dubs"][0]["audio"] == "ru", r.get("dubs"))
        check("у вшитых субтитров отдельных файлов нет", r.get("subs") == [], r.get("subs"))

        group("/api/videos и /api/subtitle: файлы RU-E")
        rr = c.get("/api/videos", params={"key": "k", "ordinal": 1, "source": "ru_e",
                                          "title": "Oreimo", "lang": "ensub"})
        check("RU-E при ensub — отказ 412 (русский источник при английском)",
              rr.status_code == 412, rr.status_code)
        r = c.get("/api/videos", params={"key": "k", "ordinal": 1, "source": "ru_e",
                                         "title": "Oreimo", "lang": "ru",
                                         "dub": "Оригинал (+субтитры)"}).json()
        got = r.get("subs") or []
        check("две дорожки (ru, en), внутренние адреса отброшены",
              sorted(s["lang"] for s in got) == ["en", "ru"], got)
        check("адрес источника наружу не ушёл", "subs.example" not in str(got), got)
        en = [s for s in got if s["lang"] == "en"][0]
        FETCHED.clear()
        r1 = c.get(en["src"])
        check("файл отдаётся как text/vtt", r1.status_code == 200
              and r1.headers["content-type"].startswith("text/vtt")
              and r1.text.startswith("WEBVTT") and "Hello there" in r1.text, r1.text[:80])
        check("скачан именно тот файл, что дал источник", FETCHED == ["https://subs.example/en.srt"],
              FETCHED)
        c.get(en["src"])
        check("второй раз — из кэша, без похода в сеть", len(FETCHED) == 1, FETCHED)
        check("неизвестный номер — 404", c.get("/api/subtitle/" + "0" * 32 + ".vtt").status_code == 404)
        check("мусор вместо номера — 404", c.get("/api/subtitle/..%2F..%2Fetc").status_code == 404)
        g = TestClient(main.app)
        check("без входа — нельзя", g.get(en["src"]).status_code in (401, 403))
    finally:
        anime.find_episodes = old_eps
        subtitles.fetch_vtt = old_fetch

    group("поиск EN-D знает, что у тайтла есть")

    class Res:
        # Так выглядит результат поиска английского разборщика: в data
        # лежит, какие варианты у тайтла есть (langs).
        title = "Naruto"
        data = {"id": "/anime/naruto", "langs": ["sub"]}

    packed = anime.pack("en_d", Res())
    check("langs доходит до карточки", packed.get("langs") == ["sub"], packed)

    async def only_sub(source, q):
        return [{"key": "k1", "title": "Naruto", "poster": "", "year": None, "genres": "",
                 "episodes_total": 12, "langs": ["sub"]}]

    old, old_eps = main.try_source, anime.find_episodes
    main.try_source, anime.find_episodes = only_sub, fake_eps
    try:
        dub = asyncio.run(main._rx_on_source_in_time("en_d", ["Naruto"], None, 0, "dub"))
        sub = asyncio.run(main._rx_on_source_in_time("en_d", ["Naruto"], None, 0, "sub"))
    finally:
        main.try_source, anime.find_episodes = old, old_eps
    check("только субтитры: при endub — не считается «можно посмотреть»", dub is not True, dub)
    check("при ensub — считается", sub is True, sub)

    group("страница")
    html = open(os.path.join(HERE, "web", "watch.html"), encoding="utf-8").read()
    js = open(os.path.join(HERE, "web", "watch.js"), encoding="utf-8").read()
    check("кнопка языка и CC в панели под плеером",
          all(f'id="{i}"' in html for i in ("lang-btn", "m-l", "sel-cc", "cc-btn", "m-c", "wbar")))
    check("дорожки ставятся через <track> с нашего адреса",
          "createElement('track')" in js and "kind = 'subtitles'" in js)
    check("клавиша C — субтитры (и С в русской раскладке)", "k === 'c' || k === 'с'" in js)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 25 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 25 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
