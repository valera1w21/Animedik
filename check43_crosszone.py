"""Проход 43 (сведение аудита 3 ноября 2026): правки «между зонами».

Четыре проверяющих работали каждый в своих файлах и записали, что нужно
поправить у соседей. Здесь — проверки к этим правкам. Сети нет: всё,
что ходит наружу, подменено.

Группы:
  1. Субтитры (api/subtitles.py): злой файл не разбирается часами
     (разбор линейный), UTF-16 читается, «рисунки» ASS не выводятся
     строкой координат, надпись сверху ({\\an8}) остаётся сверху,
     «-->» внутри реплики становится стрелкой.
  2. /api/videos (api/main.py, videos_in): общий срок на всю раздачу
     серии — зависший хостинг не держит человека до обрыва nginx.
  3. /api/videos: русский язык аниме + английский источник — 412, как и
     обратный случай (страница сама уйдёт к источникам своего языка).
  4. Аватар (api/main.py, _open_image): PNG с гигантским холстом
     отсекается по заголовку ДО того, как Pillow начнёт его открывать.
  5. «Рандом» (api/main.py, запас карточек): недавно выданная карточка
     не выдаётся снова (раньше — примерно раз на двадцать серий из пяти
     нажатий; из-за этого check29 изредка падал).
  6. Регрессия: у <video> нет crossorigin (с ним видео с хостингов без
     разрешающего заголовка не запускается) — ни в разметке, ни из кода.

Запускается при любом MODE: сам ставит MODE=private до импорта.
"""
import asyncio
import io
import logging
import os
import sys
import tempfile
import threading
import time

os.environ["MODE"] = "private"
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "p43.db"))
os.environ.setdefault("COOKIE_SECURE", "0")
os.environ.setdefault("SESSION_PEPPER", "pass43-pepper-0123456789")
os.environ.pop("EN_F_URL", None)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from api import anime, subtitles  # noqa: E402
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
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)[:300]) if extra and not ok else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def timed(fn, *a):
    t = time.perf_counter()
    out = fn(*a)
    return out, time.perf_counter() - t


# ==========================================================================
def part_subtitles():
    group("1. Субтитры: злые и необычные файлы")
    ass_head = ("[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

    # Тысячи «{» без «}». Раньше каждая «{» заставляла искать «}» до конца
    # строки: 40 000 знаков — почти секунда, файл 3 МБ — около часа.
    evil = ass_head + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,," + "{" * 60_000 + "\n"
    _, sec = timed(subtitles.to_vtt, evil.encode())
    check("ASS: 60 000 «{» без «}» — быстрее 0,3 с", sec < 0.3, f"{sec:.2f} с")
    evil = "1\n00:00:01,000 --> 00:00:02,000\n" + "<i" * 40_000 + "\n"
    _, sec = timed(subtitles.to_vtt, evil.encode())
    check("SRT: 40 000 «<i» без «>» — быстрее 0,3 с", sec < 0.3, f"{sec:.2f} с")

    # Обычные теги по-прежнему снимаются.
    out = subtitles.to_vtt(b"1\n00:00:01,000 --> 00:00:02,000\n<i>\xd0\x9f\xd1\x80\xd0\xb8"
                           b"\xd0\xb2\xd0\xb5\xd1\x82</i> <font color=\"#fff\">\xd0\xbc\xd0\xb8\xd1\x80</font>\n")
    check("SRT: <i> и <font …> сняты, текст цел", "Привет мир" in out, out)
    out = subtitles.to_vtt((ass_head + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,"
                            "{\\i1}Привет{\\i0}\\Nмир\n").encode())
    check("ASS: теги {\\i1} сняты, \\N — перенос", "Привет\nмир" in out, out)

    # UTF-16 с BOM — так сохраняет «Блокнот» и Aegisub на Windows.
    srt = "1\n00:00:01,000 --> 00:00:02,000\nПривет из UTF-16\n"
    for bom, enc in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        out = subtitles.to_vtt(bom + srt.encode(enc))
        check(f"SRT в {enc} с BOM читается", "Привет из UTF-16" in out and "00:00:01.000 -->" in out, out)

    # «Рисунок» ASS: {\p1} и дальше координаты — это фигура, а не текст.
    out = subtitles.to_vtt((ass_head +
                            "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,{\\an7\\p1}m 0 0 l 100 0 100 100 0 100{\\p0}\n"
                            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Текст\n").encode())
    check("ASS: рисунок {\\p1} не выводится строкой координат", "m 0 0 l" not in out and "Текст" in out, out)

    # Надпись сверху. VTT умеет это настройкой реплики line:…
    out = subtitles.to_vtt((ass_head +
                            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\an8}Вывеска\n"
                            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Реплика\n").encode())
    cue = [x for x in out.split("\n") if "-->" in x]
    check("ASS: {\\an8} — реплика сверху (line:…), обычная — без настроек",
          len(cue) == 2 and "line:" in cue[0] and "line:" not in cue[1], cue)

    # «-->» внутри текста: в VTT это разделитель времени, его нельзя.
    out = subtitles.to_vtt("1\n00:00:01,000 --> 00:00:02,000\nВторая --> стрелка\n".encode())
    check("SRT: «-->» внутри реплики стал «→»", "Вторая → стрелка" in out, out)
    out = subtitles.to_vtt("1\n00:00:01,000 --> 00:00:02,000\nA < B & C\n".encode())
    check("SRT: «<» и «&» в тексте экранированы", "A &lt; B &amp; C" in out, out)


# ==========================================================================
class HangingHost:
    title = "Озвучка Висящая"

    async def a_get_videos(self):
        await asyncio.sleep(3600)


class OkHost:
    def __init__(self, title):
        self.title = title

    async def a_get_videos(self):
        class V:
            url, quality, type, referer = "https://cdn.example/ok/1.m3u8", 720, "m3u8", ""
        return [V()]


class Ep:
    def __init__(self, players, hang=False):
        self.ordinal, self.title, self._players, self.hang = 1, "", players, hang

    async def a_get_sources(self):
        if self.hang:
            await asyncio.sleep(3600)
        return self._players


class Hung:
    """Ответа не дождались (запрос висит дальше в своём потоке)."""
    status_code = "завис"
    text = ""

    def json(self):
        return {}


def part_videos():
    from fastapi.testclient import TestClient

    from api import main
    c = TestClient(main.app)
    main.guest_limit.reset()
    c.post("/api/auth/guest", json={})

    def get(**params):
        for lim in (main.api_limit, main.source_limit):
            lim.reset()
        params.setdefault("title", "x")
        # Отдельным потоком с потолком 15 с: без общего срока запрос висел
        # бы час (подменный хостинг «спит» 3600 с), и проверка не кончилась бы.
        box = {}

        def go():
            box["r"] = c.get("/api/videos", params=params)
        t = time.perf_counter()
        th = threading.Thread(target=go, daemon=True)
        th.start()
        th.join(15)
        return box.get("r", Hung()), time.perf_counter() - t

    group("2. /api/videos: общий срок на раздачу серии")
    check("срок задан и не длиннее общего срока источников (60 с)",
          0 < getattr(main, "VIDEOS_BUDGET", 999) <= anime.BUDGET, getattr(main, "VIDEOS_BUDGET", None))
    saved = getattr(main, "VIDEOS_BUDGET", None)
    main.VIDEOS_BUDGET = 2.0
    try:
        anime.cache_put("eps:ru_b:h1", [Ep([HangingHost(), HangingHost()])])
        r, sec = get(key="h1", ordinal=1, source="ru_b", lang="ru")
        check("все хостинги висят — ответ 504 за срок, а не через 120 с обрыва nginx",
              r.status_code == 504 and sec < 6, (r.status_code, round(sec, 1)))
        anime.cache_put("eps:ru_b:h2", [Ep([], hang=True)])
        r, sec = get(key="h2", ordinal=1, source="ru_b", lang="ru")
        check("висит сам список плееров — 504 за срок", r.status_code == 504 and sec < 6,
              (r.status_code, round(sec, 1)))
        anime.cache_put("eps:ru_b:h3", [Ep([OkHost("Озвучка Живая")])])
        r, sec = get(key="h3", ordinal=1, source="ru_b", lang="ru")
        check("живой хостинг отвечает как раньше", r.status_code == 200
              and r.json().get("chosen") == "Живая", (r.status_code, r.text[:200]))
    finally:
        if saved is not None:
            main.VIDEOS_BUDGET = saved

    group("3. /api/videos: язык аниме и язык источника")
    anime.cache_put("eps:en_a:e1", [Ep([OkHost("Subtitles (English)")])])
    r, _ = get(key="e1", ordinal=1, source="en_a", lang="ru")
    check("русский язык + английский источник — 412", r.status_code == 412, (r.status_code, r.text[:200]))
    r, _ = get(key="e1", ordinal=1, source="en_a", lang="ensub")
    check("английский язык + английский источник — как раньше 200", r.status_code == 200,
          (r.status_code, r.text[:200]))
    r = c.get("/api/videos", params={"key": "e1", "ordinal": 1, "source": "en_a", "lang": "ru",
                                     "title": "x"}, headers={"X-UI-Lang": "en"})
    check("текст отказа переведён на английский", r.status_code in (412, 429)
          and not any("а" <= ch <= "я" for ch in r.json().get("detail", "").lower()),
          r.json().get("detail"))


# ==========================================================================
def part_avatar():
    group("4. Аватар: гигантский холст отсекается по заголовку PNG")
    import struct
    import zlib

    from PIL import Image

    from api import main

    def png(w, h):
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"\x00" * 10)) + chunk(b"IEND", b""))

    opened = []
    real_open = Image.open

    def spy(*a, **kw):
        opened.append(1)
        return real_open(*a, **kw)
    Image.open = spy
    try:
        try:
            main._open_image(png(20000, 20000))
            check("холст 20000×20000 — отказ", False, "принят")
        except ValueError:
            check("холст 20000×20000 — отказ", True)
        except Exception as exc:                   # noqa: BLE001
            check("холст 20000×20000 — отказ", True, type(exc).__name__)
        check("…и Pillow его даже не открывал (отказ по заголовку IHDR)", not opened, len(opened))
        opened.clear()
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (200, 10, 10)).save(buf, "PNG")
        img = main._open_image(buf.getvalue())
        check("обычный PNG 64×48 открывается как раньше", img.size == (64, 48), img.size)
    finally:
        Image.open = real_open


# ==========================================================================
def part_random():
    group("5. «Рандом»: недавно выданное не повторяется")
    import random as rnd

    from api import catalog, main

    # Справочник подменён: страница N — тайтл «Случайное N». Номера страниц
    # идут по кругу 1, 2, 3, 1, 2, 3, 4, 5, … — повтор гарантирован.
    seq = iter([1, 2, 3, 1, 2, 3, 1, 2, 3] + list(range(4, 200)))

    async def fake_get(url, params=None):
        if url.rstrip("/").endswith("/animes"):
            i = int((params or {}).get("page") or 1)
            return [{"id": 7000 + i, "name": "R%d" % i, "russian": "Случайное %d" % i,
                     "kind": "tv", "aired_on": "2015-01-01", "episodes": 12}]
        return {"rating": "pg_13", "description": "Описание. " * 5, "genres": []}

    saved = (catalog._get, catalog.MIN_GAP, catalog.random.randint)
    catalog._get, catalog.MIN_GAP = fake_get, 0.0
    catalog.random.randint = lambda a, b: next(seq)
    main._random_pool[""].clear()
    getattr(main, "_random_recent", {"": []})[""].clear()
    catalog._cache.clear()

    async def go():
        names = []
        for _ in range(6):
            main._random_refill("")
            task = main._random_fill.get("")
            if task is not None:
                await task
            got = main._random_take("", True)
            names.append(got and got.get("title"))
        return names
    try:
        names = asyncio.run(go())
    finally:
        catalog._get, catalog.MIN_GAP, catalog.random.randint = saved
        main._random_pool[""].clear()
        catalog._cache.clear()
        rnd.seed()
    check("шесть нажатий подряд — шесть разных карточек",
          None not in names and len(set(names)) == len(names), names)


# ==========================================================================
def part_crossorigin():
    group("6. Регрессия: <video> без crossorigin")
    import re
    with open(os.path.join(HERE, "web", "watch.html"), encoding="utf-8") as f:
        html = re.sub(r"<!--.*?-->", "", f.read(), flags=re.S)
    tags = re.findall(r"<video\b[^>]*>", html, flags=re.I)
    check("в watch.html ровно один <video>", len(tags) == 1, tags)
    check("…и без crossorigin", tags and "crossorigin" not in tags[0].lower(), tags)
    for name in ("watch.js", "app.js"):
        with open(os.path.join(HERE, "web", name), encoding="utf-8") as f:
            code = re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)
        check(f"{name} не ставит crossOrigin видео", not re.search(r"crossorigin", code, flags=re.I))


def run():
    part_subtitles()
    part_videos()
    part_avatar()
    part_random()
    part_crossorigin()
    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 43 — ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("ПРОХОД 43 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    code = run()
    sys.stdout.flush()
    # os._exit, а не sys.exit: зависшие запросы (до правки) остаются в своих
    # потоках, и обычный выход ждал бы их час.
    os._exit(code)
