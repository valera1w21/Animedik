"""Проход 39: живой обход сайта в браузере — кнопки, языки, вид, плеер.

До этого прохода настоящим браузером проверялись отдельные места
(check33_frontend.py). Здесь — обход целиком: каждая кнопка, ссылка,
переключатель и пункт меню на всех четырёх страницах, «глупые» способы
нажимать (двойной щелчок, очень длинные строки, эмодзи и иврит, обрыв
сети посреди сохранения, медленный ответ), языки (все надписи и
подсказки меняются), кодировки (субтитры в cp1251 и utf-8 с BOM, русское
имя файла), внешний вид (прокрутка вбок, сдвиг раскладки при загрузке,
контраст, праздники не грузят процессор) и сам плеер (поток играет, звук,
смена серии не оставляет второго видео, отметка на 90%, секунда
восстанавливается, субтитры включаются кнопкой и клавишей).

Как устроен стенд. Поднимается настоящий сервер (тем же Python, что и
остальные проверки) со своей временной базой и тремя аккаунтами (админ,
обычный, плюс гость по кнопке). Всё, что ходит к чужим сайтам, подменено
на стороне сервера:
  * справочник (Shikimori / AniList / Jikan) — заранее заготовленные
    ответы про франшизу «Наруто» (три части: сериал, фильм, продолжение)
    и одну часть 18+;
  * источники видео — в личном режиме подставной источник с тремя
    озвучками (обычная, «Субтитры Luna» с файлами текста и одна
    «мёртвая»), видео — ролики из testdata/frontend; в демо-режиме —
    родной демонстрационный источник, а адреса фильмов Blender браузер
    подменяет теми же роликами (чужих сайтов отсюда не видно);
  * файлы субтитров — .srt в cp1251 и .vtt в utf-8 с BOM, через
    настоящий /api/subtitle;
  * помощник — крошечная подставная «модель» на 127.0.0.1, отвечает
    текстом с HTML внутри и одной кнопкой «Открыть».

Снимки экрана (только для глаз проверяющего) — в $FE_SHOTS, если задан.
Прогон занимает несколько минут. Playwright нужен тому Python, что
запускает файл (или PW_PYTHON — тогда браузерная часть уйдёт туда).
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
FIX = os.path.join(HERE, "testdata", "frontend")
MODE = os.environ.get("MODE", "private").strip().lower() or "private"
DEMO = MODE == "demo"
SHOTS = os.environ.get("FE_SHOTS", "")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]


def group(n):
    G[0] = n
    print("\n== " + n + " ==", flush=True)


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)[:700]) if extra else ""),
          flush=True)
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


PASS = "Zhivoy-Obhod-2026"
XSS = '<img src=x onerror="window.__xss=1">'

# ==========================================================================
# Сервер с подставными чужими сайтами. Запускается отдельным процессом.
# ==========================================================================
LAUNCHER = r'''
import asyncio, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- подставная модель помощника: отвечает тем, что лежит в FE_HELPER_REPLY ---
class FakeModel(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("content-length") or 0)
        self.rfile.read(size)
        reply = os.environ.get("FE_HELPER_REPLY") or "Ок."
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

model = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
threading.Thread(target=model.serve_forever, daemon=True).start()
os.environ["HELPER_API_KEY"] = "fe-live-key"
os.environ["HELPER_BASE_URL"] = "http://127.0.0.1:%d/v1" % model.server_address[1]
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

sys.path.insert(0, os.environ["FE_REPO"])
from api import main, security, store, anime, catalog, subtitles
import fake_sources                                 # noqa: E402
fake_sources.install()
import uvicorn

DEMO = anime.DEMO
PASS, XSS = os.environ["FE_PASS"], os.environ["FE_XSS"]
FIX = os.path.join(os.environ["FE_REPO"], "testdata", "frontend")

for name in list(vars(main)):
    if isinstance(getattr(main, name), security.RateLimiter):
        setattr(main, name, security.RateLimiter(limit=10**6, period=60))
main.login_guard = security.LoginGuard()
async def _quiet(*a, **k):
    return {}
main.check_titles = _quiet

# ------------------------------------------------------------ справочник
# Узлы в том виде, в каком их отдаёт GraphQL Shikimori. Три части «Наруто»
# плюс одна этти-часть (r_plus) и одна хентай-часть (rx) для проверок 18+.
def node(i, russian, name, kind, year, eps, rating="pg_13", status="released",
         franchise="naruto", related=None, english=None, poster=True, score=7.9):
    return {"id": i, "name": name, "russian": russian, "licenseNameRu": russian,
            "english": english or name, "japanese": None, "synonyms": [],
            "franchise": franchise, "kind": kind, "rating": rating, "episodes": eps,
            "episodesAired": eps, "status": status, "score": score,
            "airedOn": {"year": year, "date": "%d-10-03" % year},
            "poster": {"mainUrl": "https://img.test/%d.jpg" % i if poster else None,
                       "originalUrl": None},
            "related": related or [],
            "genres": [{"name": "Action", "russian": "Экшен", "kind": "genre"},
                       {"name": "Shounen", "russian": "Сёнэн", "kind": "genre"}],
            "description": "История ниндзя из деревни Коноха."}

NODES = [
    node(20, "Наруто", "Naruto", "tv", 2002, 220,
         related=[{"relationKind": "sequel", "anime": {"id": 1735}},
                  {"relationKind": "side_story", "anime": {"id": 442}}]),
    node(442, "Наруто: Фильм", "Naruto the Movie", "movie", 2004, 1,
         related=[{"relationKind": "parent_story", "anime": {"id": 20}}]),
    node(1735, "Наруто: Ураганные хроники", "Naruto: Shippuuden", "tv", 2007, 500,
         related=[{"relationKind": "prequel", "anime": {"id": 20}}]),
    # Этти-часть: видна только тому, кто включил 18+.
    node(9001, "Наруто: Горячие источники", "Naruto: Hot Springs", "ova", 2009, 2,
         rating="r_plus", related=[{"relationKind": "side_story", "anime": {"id": 20}}]),
]
# Отдельная франшиза с ивритом, эмодзи и очень длинным названием: по ней
# проверяется, что раскладка не ломается.
LONG = "Очень длинное название аниме, которое не помещается ни в одну строку карточки и даже в две, " \
       "потому что его придумали специально для проверки переносов 😀 הגיבור של הסיפור"
WEIRD = [
    node(7001, "הגיבור — Герой 😀", "HaGibor", "tv", 2015, 12, franchise="weird"),
    node(7002, LONG, "Very Long Title", "movie", 2016, 1, franchise="weird"),
]
# Хентай — франшиза отдельная, чтобы он не попадал в «Наруто».
RX = [node(8001, "Сад удовольствий", "Pleasure Garden", "ova", 2010, 2, rating="rx",
           franchise="garden")]

def match(nodes, q):
    q = (q or "").strip().lower()
    out = [n for n in nodes if q and (q in (n["russian"] or "").lower() or q in (n["name"] or "").lower())]
    if not out and q:
        # Запрос на иврите / с эмодзи — отдаём странные карточки.
        if any(ord(c) > 0x5d0 and ord(c) < 0x5ea for c in q) or any(ord(c) > 0x1f000 for c in q):
            return list(WEIRD)
    return out

async def fake_gql(query, variables=None):
    variables = variables or {}
    if "animes(search" in query:
        hentai = "censored: false" in query
        pool = NODES + WEIRD + (RX if hentai else [])
        found = match(pool, variables.get("s"))
        return {"animes": found}
    if 'franchise: "' in query:
        fid = query.split('franchise: "')[1].split('"')[0]
        hentai = "censored: false" in query
        if fid == "naruto":
            return {"p1": list(NODES), "p2": []}
        if fid == "weird":
            return {"p1": list(WEIRD), "p2": []}
        if fid == "garden" and hentai:
            return {"p1": list(RX), "p2": []}
        return {"p1": [], "p2": []}
    if 'genre: "9"' in query:
        return {"p1": [n for n in NODES if n["rating"] == "r_plus"], "p2": []}
    if 'genre: "12"' in query:
        return {"p1": list(RX), "p2": []}
    return None
catalog._ask_shiki_gql = fake_gql

# REST-справочник: описание, обложка, случайное.
BRIEF = {"id": 20, "name": "Naruto", "russian": "Наруто", "kind": "tv", "score": "7.9",
         "aired_on": "2002-10-03", "episodes": 220, "image": {"original": "/system/animes/original/20.jpg"}}
FULL = {"id": 20, "rating": "pg_13", "english": ["Naruto"],
        "description": "Наруто Удзумаки — шумный подросток из деревни Коноха, мечтающий стать Хокаге.",
        "genres": [{"name": "Action", "russian": "Экшен"}, {"name": "Shounen", "russian": "Сёнэн"}]}
JIKAN = {"data": [{"title": "Naruto", "title_english": "Naruto", "type": "TV", "year": 2002,
                   "rating": "PG-13 - Teens 13 or older",
                   "synopsis": "Naruto Uzumaki, a loud ninja from Konoha, dreams of becoming Hokage.",
                   "genres": [{"name": "Action"}, {"name": "Shounen"}]}]}

async def fake_get(url, params=None):
    if url.startswith(catalog.JIKAN):
        return JIKAN
    if url.rstrip("/").endswith("/animes"):
        return [dict(BRIEF)]
    if url.rstrip("/").split("/")[-1].isdigit():
        return dict(FULL)
    return None
catalog._get = fake_get
async def fake_anilist(query, variables):
    return None
catalog._ask_anilist = fake_anilist
catalog.MIN_GAP = 0
catalog.JIKAN_GAP = 0

_rand = [0]
async def fake_random(genre=None):
    _rand[0] += 1
    pick = catalog.pack_shiki(BRIEF, FULL)
    pick["title"] = "Наруто" if _rand[0] % 2 else XSS + " случайный"
    return pick
catalog.random_anime = fake_random

# ------------------------------------------------------------ источники
class V:
    def __init__(self, url, q, t):
        self.url, self.quality, self.type = url, q, t

class Src:
    def __init__(self, title, videos, subs=None, dead=False):
        self.title, self._v, self.subtitles, self._dead = title, videos, subs or [], dead
    async def a_get_videos(self):
        if self._dead:
            raise RuntimeError("хостинг лежит")
        return [V(*x) for x in self._v]

HLS = [("https://media.test/hls/master.m3u8", 720, "m3u8"), ("https://media.test/clip.webm", 480, "webm")]
SUBS = [{"label": "Русский", "lang": "ru", "url": "https://subs.test/ru.srt", "codec": "", "referer": ""},
        {"label": "English", "lang": "en", "url": "https://subs.test/en.vtt", "codec": "vtt", "referer": ""}]

class Ep:
    def __init__(self, n, key):
        self.ordinal, self.title, self._key = n, "", key
    async def a_get_sources(self):
        if self._key == "mock:movie":
            return [Src("RU-A", HLS)]
        return [Src("RU-A", HLS), Src("Озвучка Yozh", HLS, dead=True),
                Src("Субтитры Luna", HLS, subs=SUBS)]

class An:
    def __init__(self, key, eps):
        self._key, self._eps = key, eps
        self.title, self.url, self.data = key, key, {"id": key}
    async def a_get_episodes(self):
        return [Ep(n, self._key) for n in self._eps]

TITLES = [("mock:naruto", "Наруто", 2002, [1, 2, 3]),
          ("mock:movie", "Наруто: Фильм", 2004, [1]),
          ("mock:shippuden", "Наруто: Ураганные хроники", 2007, [370, 371, 372]),
          ("mock:springs", "Наруто: Горячие источники", 2009, [1, 2]),
          ("mock:weird", "הגיבור — Герой 😀", 2015, [1, 2])]

class Search:
    def __init__(self, key, title, year, eps):
        self.title, self.url, self.thumbnail = title, key, "https://img.test/%s.jpg" % key.split(":")[1]
        self.data = {"id": key, "year": year, "genres": "Экшен,Сёнэн", "episodes_total": len(eps)}
        self._eps = eps
    async def a_get_anime(self):
        return An(self.url, self._eps)

class Extractor:
    async def a_search(self, q):
        await asyncio.sleep(0.05)
        q = (q or "").lower()
        out = [Search(*t) for t in TITLES if q and (q in t[1].lower() or t[1].lower() in q)]
        return out

if DEMO:
    # Демо-источник выбирает ролик по названию (хэш): «Наруто» попадает на
    # «Tears of Steel» с одной серией. Проходу нужны три серии — прибиваем
    # выбор к «Big Buck Bunny» (три серии, поток HLS и обычный файл).
    # Сам источник при этом настоящий: серии, озвучки, адреса — его.
    from api import anime_demo
    _real_pick = anime_demo.pick_clip
    def _pick(query):
        text = (query or "").strip().lower()
        if text in ("наруто", "naruto"):
            return anime_demo.CLIPS[0]
        # продолжению — другой ролик, иначе у двух частей один ключ и
        # «В планы» у второй молча упирается в уже начатую первую
        if "shippuuden" in text or "ураганные" in text:
            return anime_demo.CLIPS[2]
        return _real_pick(query)
    anime_demo.pick_clip = _pick
else:
    _real_get_extractor = anime.get_extractor
    def fake_extractor(source):
        if source not in anime.SOURCES:
            return _real_get_extractor(source)
        if source not in anime._extractors:
            anime._extractors[source] = Extractor()
        return anime._extractors[source]
    anime.get_extractor = fake_extractor

# Файлы субтитров: .srt в cp1251 и готовый .vtt с BOM — через настоящий
# subtitles.to_vtt, только без сети.
SRT_CP1251 = ("1\r\n00:00:00,500 --> 00:00:04,000\r\nПривет, <i>мир</i> — ёж и Ёлка\r\n\r\n"
              "2\r\n00:00:04,200 --> 00:00:06,000\r\nВторая реплика --> стрелка\r\n").encode("cp1251")
VTT_BOM = ("﻿WEBVTT\n\n00:00:00.500 --> 00:00:04.000\nHello English cue\n\n"
           "00:00:04.200 --> 00:00:06.000\nSecond cue\n").encode("utf-8")
async def fake_fetch(meta):
    url = meta.get("url", "")
    if url.endswith("ru.srt"):
        return subtitles.to_vtt(SRT_CP1251)
    if url.endswith("en.vtt"):
        return subtitles.to_vtt(VTT_BOM)
    raise RuntimeError("нет такого файла")
subtitles.fetch_vtt = fake_fetch

# ------------------------------------------------------------ аккаунты
store.init()
admin = store.create_user("hozyain", PASS, "admin", "Хозяин")
friend = store.create_user("friend", PASS, "user", "Друг")
evil = store.create_user("evil", PASS, "user", (XSS + " evil")[:40])
src = "demo" if DEMO else "ru_a"
NARUTO = "bunny" if DEMO else "mock:naruto"
items = [
    {"key": NARUTO, "source": src, "title": "Наруто", "title_en": "Naruto",
     "poster": "https://img.test/naruto.jpg", "year": 2002, "genres": "Экшен,Сёнэн",
     "total_eps": 3, "watched_ep": 2, "position": 125, "status": "watching", "cid": "mal:20"},
    {"key": "mock:movie", "source": src, "title": "Наруто: Фильм", "title_en": "Naruto the Movie",
     "poster": "", "year": 2004, "genres": "Экшен", "total_eps": 1, "watched_ep": 1,
     "status": "done", "cid": "mal:442"},
    {"key": "mock:weird", "source": src, "title": "הגיבור — Герой 😀", "title_en": "HaGibor",
     "poster": "https://img.test/broken.png", "year": 2015, "genres": "Экшен", "total_eps": 2,
     "watched_ep": 1, "position": 30, "status": "watching"},
    {"key": "mock:later", "source": src, "title": "Отложенное на потом", "title_en": "Later One",
     "poster": "", "year": 2020, "genres": "", "total_eps": 12, "status": "later"},
]
for uid in (admin, friend):
    for it in items:
        store.save_progress(uid, dict(it))
    for ep in (1, 2):
        store.log_watch(uid, NARUTO, "Наруто", "Экшен,Сёнэн", ep, 1400)
store.set_news("Объявление для всех: " + XSS, "")
uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ["FE_PORT"]), log_level="warning")
'''


def free_port():
    import random
    ports = list(range(8201, 8250))
    random.shuffle(ports)
    for p in ports:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self, helper_reply=None):
        self.tmp = tempfile.mkdtemp(prefix="check39-")
        self.log = open(os.path.join(self.tmp, "server.log"), "w")
        self.helper_reply = helper_reply
        for _ in range(3):
            if self._start():
                return
        raise RuntimeError("сервер не запустился:\n" + open(self.log.name).read()[-3000:])

    def _start(self):
        self.port = free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        env = dict(os.environ)
        env.update({"MODE": MODE, "SESSION_PEPPER": "check39-pepper-0123456789", "COOKIE_SECURE": "0",
                    "DB_PATH": os.path.join(self.tmp, "t-%d.db" % self.port),
                    "UPDATES_EVERY_MIN": "0", "FE_REPO": HERE,
                    "FE_PASS": PASS, "FE_XSS": XSS, "FE_PORT": str(self.port),
                    "FE_HELPER_REPLY": self.helper_reply or
                    ("Ответ помощника. " + XSS + " [[open:settings-look]] [[open:javascript:alert(1)]]")})
        py = os.environ.get("FE_SERVER_PY") or sys.executable
        self.proc = subprocess.Popen([py, "-c", LAUNCHER], cwd=HERE, env=env,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        end = time.time() + 60
        while time.time() < end:
            if self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(self.base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        time.sleep(0.5)
                        return self.proc.poll() is None
            except Exception:                              # noqa: BLE001
                time.sleep(0.2)
        self.proc.kill()
        return False

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ==========================================================================
# Браузер: общие помощники
# ==========================================================================
INIT = r"""
window.__csp = [];
document.addEventListener('securitypolicyviolation', function (e) {
  window.__csp.push(e.violatedDirective + ' ' + e.blockedURI);
});
window.__rej = [];
window.addEventListener('unhandledrejection', function (e) {
  window.__rej.push(String(e.reason && (e.reason.message || e.reason)));
});
window.__probe = function (wantEn, allowed) {
  var CYR = /[А-Яа-яЁё]/;
  function shown(n) {
    if (!n || !n.getClientRects().length) return false;
    for (var x = n; x && x !== document.body; x = x.parentElement) {
      var s = getComputedStyle(x);
      if (x.hidden || x.getAttribute('aria-hidden') === 'true' || s.visibility === 'hidden' ||
          s.display === 'none') return false;
    }
    return true;
  }
  function ok(t) { return allowed.some(function (a) { return t.indexOf(a) >= 0 || a.indexOf(t) === 0; }); }
  var out = { cyr: [], noname: [], dialogs: [], xssNodes: 0, junk: [],
              wide: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  var w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), t;
  while ((t = w.nextNode())) {
    var v = t.nodeValue.trim();
    if (!v || !shown(t.parentElement)) continue;
    if (wantEn && CYR.test(v) && !ok(v)) out.cyr.push(v.slice(0, 80));
    if (/undefined|\[object Object\]|\{\{|NaN\b|null\b/.test(v)) out.junk.push(v.slice(0, 80));
  }
  document.querySelectorAll('[aria-label],[title],[placeholder],[alt]').forEach(function (n) {
    if (!shown(n)) return;
    ['aria-label', 'title', 'placeholder', 'alt'].forEach(function (a) {
      var v = n.getAttribute(a);
      if (!v) return;
      if (wantEn && CYR.test(v) && !ok(v)) out.cyr.push('@' + a + ' ' + (n.id || n.className) + ': ' + v.slice(0, 60));
      if (/undefined|\[object Object\]/.test(v)) out.junk.push('@' + a + ' ' + v.slice(0, 60));
    });
  });
  document.querySelectorAll('button, a[href], [role=button], [role=switch], [role=tab], input, textarea').forEach(function (n) {
    if (!shown(n) || n.type === 'hidden' || n.type === 'file') return;
    var name = (n.getAttribute('aria-label') || n.getAttribute('title') || '').trim();
    var lb = n.getAttribute('aria-labelledby');
    if (!name && lb) name = lb.split(/\s+/).map(function (id) { var x = document.getElementById(id); return x ? x.textContent : ''; }).join('').trim();
    if (!name && n.id && document.querySelector('label[for="' + n.id + '"]')) name = 'label';
    if (!name && n.closest('label')) name = 'label';
    if (!name) name = (n.textContent || '').trim();
    if (!name) out.noname.push(n.tagName + '#' + n.id + '.' + n.className);
  });
  document.querySelectorAll('[role=dialog]').forEach(function (d) {
    if (!shown(d)) return;
    var lb = d.getAttribute('aria-labelledby'), name = d.getAttribute('aria-label') || '';
    if (lb) name = lb.split(/\s+/).map(function (id) { var x = document.getElementById(id); return x ? x.textContent : ''; }).join('').trim();
    if (!name) out.dialogs.push(d.id || d.className);
  });
  out.xssNodes = document.querySelectorAll('img[src="x"], img[src$="/x"]').length;
  return out;
};
"""

# Что законно остаётся русским на английской странице: имена людей и
# названия аниме, у которых в подставном справочнике нет английского
# (итоги года пишут названия как в журнале).
EN_ALLOWED = ["Хозяин", "Друг", "Наруто", "Герой", "Очень длинное", "Отложенное", "Русский",
              "Язык сайта · Site language", "Язык сайта / Site language", "Объявление для всех",
              "Сад удовольствий",
              # демо-источник: имя источника и названия его дорожек приходят с сервера
              # по-русски и на английской странице (см. «Нужно от соседей» в отчёте)
              "Демо", "HLS-поток", "Обычный файл"]


def install_routes(ctx, counter):
    """Чужие адреса (картинки, видео, фильмы Blender в демо) — из testdata."""
    from urllib.parse import urlparse

    def media(route, request):
        path = urlparse(request.url).path
        name = os.path.basename(path)
        # Демо-источник ссылается на плейлисты Blender: любой .m3u8 — наш
        # главный плейлист, любой .mp4 — наш webm (браузер смотрит на
        # содержимое, не на расширение).
        if name.endswith(".m3u8") and not os.path.isfile(os.path.join(FIX, "hls", name)):
            name = "master.m3u8" if "media" not in name else "media.m3u8"
        if name.endswith(".mp4") and not os.path.isfile(os.path.join(FIX, "hls", name)):
            name = "clip.webm"
        local = os.path.join(FIX, "hls", name)
        if not os.path.isfile(local):
            local = os.path.join(FIX, name)
        if not os.path.isfile(local):
            return route.fulfill(status=404, body="", headers={"Access-Control-Allow-Origin": "*"})
        ctype = {".m3u8": "application/vnd.apple.mpegurl", ".mp4": "video/mp4",
                 ".m4s": "video/mp4", ".webm": "video/webm"}.get(os.path.splitext(local)[1],
                                                                  "application/octet-stream")
        counter["media"] = counter.get("media", 0) + 1
        counter.setdefault("media_urls", []).append(request.url)
        with open(local, "rb") as fh:
            route.fulfill(status=200, body=fh.read(),
                          headers={"Content-Type": ctype, "Access-Control-Allow-Origin": "*"})

    def img(route, request):
        if "broken" in request.url:
            return route.fulfill(status=404, body="")
        with open(os.path.join(FIX, "poster.png"), "rb") as fh:
            route.fulfill(status=200, body=fh.read(), headers={"Content-Type": "image/png"})

    ctx.route("https://media.test/**", media)
    ctx.route("https://test-streams.mux.dev/**", media)
    ctx.route("https://media.w3.org/**", media)
    ctx.route("https://download.blender.org/**", media)
    ctx.route("https://img.test/**", img)
    ctx.route("https://shikimori.one/**", img)
    ctx.route("https://nyaa.shikimori.one/**", img)


class Console:
    def __init__(self, page):
        self.page, self.items = page, []
        self.requests = []
        page.on("console", self._msg)
        page.on("pageerror", lambda e: self.items.append("pageerror: " + str(e)[:300]))
        page.on("request", lambda r: self.requests.append((r.method, r.url)))
        page.on("response", self._resp)
        self.bad = []

    def _resp(self, r):
        if "/api/" in r.url and r.status >= 400:
            self.bad.append((r.status, r.request.method, r.url.split("?")[0]))

    def _msg(self, m):
        if m.type not in ("error", "warning"):
            return
        where = (m.location or {}).get("url", "")
        if "Failed to load resource" in m.text and ("broken" in where or "broken" in m.text):
            return
        self.items.append(m.type + ": " + m.text[:200] + " @ " + where)

    def take(self):
        extra = self.page.evaluate("() => ({c: window.__csp || [], r: window.__rej || [], x: window.__xss || 0})")
        out = self.items + ["CSP: " + c for c in extra["c"]] + ["unhandledrejection: " + r for r in extra["r"]]
        if extra["x"]:
            out.append("ВЫПОЛНИЛСЯ ЧУЖОЙ КОД (window.__xss)")
        self.items = []
        self.page.evaluate("() => { window.__csp = []; window.__rej = []; }")
        return out

    def take_bad(self, expected=()):
        out = [b for b in self.bad if b[0] not in expected]
        self.bad = []
        return out


def ctx_for(b, counter, width=1280, height=800, **kw):
    ctx = b.new_context(viewport={"width": width, "height": height}, locale="ru-RU", **kw)
    ctx.add_init_script(INIT)
    install_routes(ctx, counter)
    return ctx


def login(page, base, user, lang=None):
    page.goto(base + "/")
    page.wait_for_selector("#gate:not([hidden])")
    if lang:
        page.click('.langrow .lang[data-set-lang="%s"]' % lang)
    page.fill("#lg-user", user)
    page.fill("#lg-pass", PASS)
    page.click("#btn-login")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)


def guest(page, base):
    page.goto(base + "/")
    page.wait_for_selector("#gate:not([hidden])")
    page.click("#btn-guest")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)


def post(page, path, body):
    return page.evaluate("([p, b]) => App.api.post(p, b).then(r => r, e => ({error: e.message, status: e.status}))",
                         [path, body])


def wait(page, js, limit=8.0):
    end = time.time() + limit
    while time.time() < end:
        try:
            if page.evaluate(js):
                return True
        except Exception:                                  # noqa: BLE001
            pass
        time.sleep(0.15)
    return False


def shot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    try:
        page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=False)
    except Exception:                                      # noqa: BLE001
        pass


def clean(con, page, what, en=False, expected=()):
    """Консоль чистая, ручки без неожиданных 4xx/5xx, нет ключей вместо
    текста, у кнопок и окон есть имена; на английской странице — ни одного
    русского слова."""
    errs = con.take()
    check(what + ": ноль ошибок в консоли и нарушений CSP", not errs, errs[:6])
    bad = con.take_bad(expected)
    check(what + ": ручки без неожиданных 4xx/5xx", not bad, bad[:6])
    pr = page.evaluate("([e, a]) => window.__probe(e, a)", [en, EN_ALLOWED])
    check(what + ": чужой <img> не вставился", pr["xssNodes"] == 0, pr["xssNodes"])
    check(what + ": нет undefined / [object Object] / {{ }} в тексте", not pr["junk"], pr["junk"][:5])
    check(what + ": у кнопок и полей есть имена", not pr["noname"], pr["noname"][:5])
    check(what + ": у открытых окон есть имена", not pr["dialogs"], pr["dialogs"])
    check(what + ": нет прокрутки вбок", pr["wide"] <= 0, pr["wide"])
    if en:
        check(what + ": на английской странице нет русских слов", not pr["cyr"], pr["cyr"][:8])
    return pr


# Ключ тайтла «Наруто» у источника: в демо — ролик «bunny» (см. LAUNCHER)
NARUTO = "bunny" if DEMO else "mock:naruto"
SRC = "demo" if DEMO else "ru_a"
WATCH = ("/watch?key={key}&source={src}&title=%D0%9D%D0%B0%D1%80%D1%83%D1%82%D0%BE"
         "&poster=https%3A%2F%2Fimg.test%2Fnaruto.jpg&total=3&cid=mal%3A20")


def watch_url():
    return WATCH.format(key=NARUTO.replace(":", "%3A"), src=SRC)



# ==========================================================================
# Статическая часть: стражи правок этого прохода (без браузера)
# ==========================================================================
def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()


def static_checks():
    group("1. Стражи правок (по исходникам)")
    app, ix, wj, css = read("app.js"), read("index.js"), read("watch.js"), read("app.css")
    check("app.js: фраза сервера вне словаря берётся из detail_en (B-01)",
          "typeof data.detail_en === 'string'" in app and "if (detailEn && !CYR.test(detailEn)) return detailEn;" in app)
    check("app.js: заголовок X-UI-Lang не шлём (слова сервера «login» против «username» на экране)",
          "headers['X-UI-Lang']" not in app)
    check("app.js: плашка «демо» помнится в браузере и показывается сразу (B-10)",
          "localStorage.getItem(MODE_KEY) === 'demo'" in app)
    check("app.js: щелчок мимо окна в первые мгновения — не «отмена» (двойной щелчок по «×»)",
          "function tooEarly(d)" in app and "!tooEarly(d)" in app and "at: Date.now()" in app)
    check("app.js: второй «Выйти» не шлёт второй запрос", "logoutPending" in app)
    check("index.js: второй Enter на входе не шлёт второй запрос", "loginSending" in ix)
    check("index.js: битый файл картинки — сообщение, а не тишина", "cimg.onerror = function ()" in ix)
    check("index.js: помощник открылся — совет про вход по коду убран", "hideToast(false);" in ix)
    check("watch.js: «осталось» меньше часа — в минутах, а не «0 ч»", "' мин', ' min'" in wj)
    check("app.css: на телефоне совет про код выше кнопки «?»", ".toast{bottom:76px}" in css)
    hls = open(os.path.join(WEB, "hls.min.js"), encoding="utf-8").read()
    ver = re.search(r'"(1\.\d+\.\d+)"', hls)
    print("  hls.js в проекте: " + (ver.group(1) if ver else "?") + " (на npm 2026-11-03 — 1.7.4; обновляет D)")


# ==========================================================================
# Браузерная часть
# ==========================================================================
def totp(secret):
    import base64
    import hashlib
    import hmac
    import struct
    s = secret.replace(" ", "").upper()
    key = base64.b32decode(s + "=" * (-len(s) % 8))
    c = int(time.time()) // 30
    h = hmac.new(key, struct.pack(">Q", c), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7fffffff) % 10 ** 6).zfill(6)


def png_bytes(w, h):
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes([i % 256, (i * 3) % 256, 90]) * w for i in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def dialog_text(page):
    return page.evaluate("() => (document.querySelector('.veil.dialog') || {}).innerText || ''")


def api_count(con, path, method=None):
    return sum(1 for m, u in con.requests if path in u and (method is None or m == method))


PERF_JS = r"""() => new Promise(done => {
  let frames = 0, long = 0; const t0 = performance.now();
  const po = new PerformanceObserver(l => { l.getEntries().forEach(e => { long += e.duration; }); });
  try { po.observe({type: 'longtask', buffered: false}); } catch (e) {}
  function f() { frames++; if (performance.now() - t0 < 2000) requestAnimationFrame(f);
                 else { po.disconnect(); done({fps: frames / 2, longMs: long}); } }
  requestAnimationFrame(f);
})"""

CLS_INIT = """window.__cls = 0; try { new PerformanceObserver(l => l.getEntries().forEach(e => {
  if (!e.hadRecentInput) window.__cls += e.value; })).observe({type:'layout-shift', buffered:true}); } catch (e) {}"""

CONTRAST_JS = r"""(sels) => {
  var cv = document.createElement('canvas'); cv.width = cv.height = 1;
  var cx = cv.getContext('2d', { willReadFrequently: true });
  function parse(c) {
    if (!c || c === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };
    var m = /^rgba?\(([^)]+)\)$/.exec(c);
    if (m) { var p = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat);
             return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; }
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
    var d = cx.getImageData(0, 0, 1, 1).data; return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  }
  function over(t, b) { var a = t.a + b.a * (1 - t.a);
    return { r: (t.r * t.a + b.r * b.a * (1 - t.a)) / a, g: (t.g * t.a + b.g * b.a * (1 - t.a)) / a,
             b: (t.b * t.a + b.b * b.a * (1 - t.a)) / a, a: a }; }
  function lum(c) { var f = function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); }
  var out = [];
  sels.forEach(function (sel) {
    var el = document.querySelector(sel);
    if (!el || !el.getClientRects().length || !(el.textContent || '').trim()) return;
    var layers = [];
    for (var n = el; n; n = n.parentElement) { var c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; } }
    var bg = { r: 255, g: 255, b: 255, a: 1 };
    for (var i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
    var s = getComputedStyle(el), fg = over(parse(s.color), bg);
    var L1 = lum(fg), L2 = lum(bg);
    var big = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && +s.fontWeight >= 700);
    out.push({ sel: sel, ratio: Math.round((Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05) * 100) / 100,
               need: big ? 3 : 4.5 });
  });
  return out;
}"""

KEY_TEXT = ["#list-count", ".boxcard .meta", ".boxcard .lrow .l", ".sec h2", "footer a", "footer .in span",
            ".ucstat span", ".uclbl", ".cabh p", ".opt .lb span", ".grp > h4"]


def browser_checks():
    from playwright.sync_api import sync_playwright
    srv = Server()
    counter = {}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            try:
                run_browser(b, srv, counter)
            finally:
                b.close()
    finally:
        srv.stop()


def run_browser(b, srv, counter):
    base = srv.base
    V = "document.getElementById('video')"

    # ------------------------------------------------------------------
    group("2. Вход, гость, двойные нажатия на входе")
    ctx = ctx_for(b, counter)
    page = ctx.new_page()
    con = Console(page)
    page.goto(base + "/")
    page.wait_for_selector("#gate:not([hidden])")
    clean(con, page, "страница входа")
    check("<html lang> на русской странице — ru", page.evaluate("() => document.documentElement.lang") == "ru")
    page.click('.langrow .lang[data-set-lang="en"]')
    time.sleep(0.2)
    clean(con, page, "страница входа (EN)", en=True)
    check("<html lang> переключился на en", page.evaluate("() => document.documentElement.lang") == "en")
    check("заголовок вкладки по-английски", page.title() == "animeDik", page.title())
    page.click('.langrow .lang[data-set-lang="ru"]')
    page.fill("#lg-user", "  friend  ")
    page.fill("#lg-pass", "wrong")
    page.click("#btn-login")
    page.wait_for_selector("#gate-err.show")
    check("неверный пароль — понятная ошибка", "Неверный логин или пароль" in page.inner_text("#gate-err"),
          page.inner_text("#gate-err"))
    con.take()
    con.take_bad()
    page.fill("#lg-pass", PASS)
    con.requests.clear()
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)
    time.sleep(0.8)
    check("два Enter на входе — один запрос входа (B-04)", api_count(con, "/api/auth/login", "POST") == 1,
          api_count(con, "/api/auth/login", "POST"))
    check("логин с пробелами по краям принят", page.evaluate("() => App.me && App.me.name") == "friend")
    clean(con, page, "главная после входа")
    page.click("#who")
    page.wait_for_selector("#ucard-wrap.show")
    con.requests.clear()
    page.dblclick("#btn-out")
    page.wait_for_selector("#gate:not([hidden])", timeout=10000)
    time.sleep(0.6)
    check("двойной «Выйти» — один запрос выхода, без окна ошибки (B-03)",
          api_count(con, "/api/auth/logout", "POST") == 1 and not dialog_text(page),
          (api_count(con, "/api/auth/logout", "POST"), dialog_text(page)[:60]))
    con.take()
    con.take_bad()
    con.requests.clear()
    page.dblclick("#btn-guest")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)
    time.sleep(0.6)
    check("двойной «гость» — один пропуск", api_count(con, "/api/auth/guest", "POST") == 1)
    clean(con, page, "гость: главная")
    page.click("#who")
    time.sleep(0.3)
    clean(con, page, "гость: карточка профиля")
    check("гостю — «Войти», без «Выйти» и настроек",
          page.evaluate("() => !document.getElementById('uc-login').hidden && document.getElementById('btn-out').hidden && document.getElementById('uc-set').hidden"))
    page.click("#uc-login")
    page.wait_for_selector("#gate:not([hidden])", timeout=10000)
    ctx.close()

    # ------------------------------------------------------------------
    group("3. Главная: все кнопки, окна, поиск, «глупые» строки")
    ctx = ctx_for(b, counter)
    page = ctx.new_page()
    con = Console(page)
    login(page, base, "friend")
    time.sleep(1.0)
    clean(con, page, "главная")
    check("объявление с <img> — текстом", XSS in page.inner_text("#sitenews-text"))
    check("карточки списка: иврит и эмодзи не ломают раскладку",
          page.evaluate("([e, a]) => window.__probe(e, a).wide", [False, EN_ALLOWED]) <= 0)
    for tab in ("done", "later", "open"):
        page.click('#tabs a[data-tab="%s"]' % tab)
        time.sleep(0.25)
        check("вкладка «%s» переключается" % tab, page.evaluate("(t) => document.querySelector('#tabs a[data-tab=\"' + t + '\"]').classList.contains('on')", tab))
    check("счётчики вкладок на месте", page.inner_text("#tabn-open") == "2" and page.inner_text("#tabn-later") == "1",
          (page.inner_text("#tabn-open"), page.inner_text("#tabn-later")))
    # кнопки шапки
    page.click("#btn-roul")
    page.wait_for_selector("#roul.done")
    time.sleep(0.3)
    clean(con, page, "«Рандом»")
    check("«Рандом»: чужой текст — текстом, карточка заполнена",
          "Наруто" in page.inner_text("#pickres") and "оценка" in page.inner_text("#pickres"))
    page.click("#roul-go")
    page.wait_for_selector("#roul.done")
    check("«Ещё раз» крутит снова", XSS in page.inner_text("#pickres") or "Наруто" in page.inner_text("#pickres"))
    page.click("#roul-watch")
    time.sleep(0.3)
    check("«Найти на сайте» закрывает окно и ищет", not page.evaluate("() => document.getElementById('veil-roul').classList.contains('show')")
          and not page.evaluate("() => document.getElementById('search-wrap').hidden"))
    page.click("#search-clear")
    page.click("#btn-bell")
    time.sleep(0.6)
    check("колокольчик открылся и отвечает", page.evaluate("() => document.getElementById('bell-drop').classList.contains('show')")
          and "Новых серий нет" in page.inner_text("#bell-list"))
    clean(con, page, "колокольчик")
    page.keyboard.press("Escape")
    check("Esc закрыл колокольчик", not page.evaluate("() => document.getElementById('bell-drop').classList.contains('show')"))
    page.click("#btn-about")
    time.sleep(0.3)
    check("«Про сайт» открылось", page.evaluate("() => document.getElementById('veil').classList.contains('show')"))
    clean(con, page, "«Про сайт»")
    page.click("#btn-close2")
    page.click("#foot-about")
    time.sleep(0.2)
    check("«О проекте» в подвале — то же окно", page.evaluate("() => document.getElementById('veil').classList.contains('show')"))
    page.click("#btn-close")
    with ctx.expect_page() as newp:
        page.click("#btn-rules")
    p2 = newp.value
    p2.wait_for_selector(".pravila-list")
    check("«Правила» — в новой вкладке без opener", p2.url.endswith("/pravila") and p2.evaluate("() => window.opener === null"))
    p2.close()
    check("«Итоги года» в шапке ведёт на /stats", page.get_attribute("#btn-stats", "href") == "/stats")
    # поиск
    page.fill("#q", "а" * 400)
    page.keyboard.press("Enter")
    time.sleep(1.2)
    # в демо источник «находит» что угодно (ролик на любой запрос) — там
    # достаточно, что поиск не сломался и выдача есть
    check("очень длинный запрос обрезан полем (100) и не ломает поиск",
          page.evaluate("() => document.getElementById('q').value.length") == 100
          and ("ничего" in page.inner_text("#search-note") or (DEMO and page.locator("#search-grid .card").count() > 0)),
          (page.inner_text("#search-note"), page.locator("#search-grid .card").count()))
    page.fill("#q", "   ")
    page.keyboard.press("Enter")
    time.sleep(0.3)
    check("пробелы — не поиск", page.evaluate("() => document.getElementById('search-wrap').hidden"))
    page.fill("#q", " НАРУТО ")
    page.keyboard.press("Enter")
    page.wait_for_selector("#search-grid .card")
    check("заглавные и пробелы по краям — находит", "Наруто" in page.inner_text("#search-grid"))
    page.fill("#q", "очень")
    page.keyboard.press("Enter")
    page.wait_for_selector("#search-grid .card")
    time.sleep(0.4)
    page.click("#search-grid .card >> nth=0")
    page.wait_for_selector("#veil-fr.show")
    time.sleep(0.8)
    pr = clean(con, page, "окно частей: очень длинное название, иврит, эмодзи")
    shot(page, "fr-long")
    page.keyboard.press("Escape")
    page.fill("#q", "наруто")
    page.keyboard.press("Enter")
    page.wait_for_selector("#search-grid .card")
    time.sleep(0.4)
    page.click("#search-grid .card >> nth=0")
    page.wait_for_selector("#veil-fr.show")
    time.sleep(0.8)
    clean(con, page, "окно частей «Наруто»")
    txt = page.inner_text("#fr-parts")
    check("части по порядку: «Начни отсюда», 220 серий, продолжение", "Начни отсюда" in txt and "220 серий" in txt
          and "Ураганные хроники" in txt, txt[:200])
    check("этти-часть без 18+ не показана", "Горячие источники" not in txt)
    check("склонения в окне частей: «2 части основной истории · 1 дополнительная»",
          "2 части основной истории" in page.inner_text("#fr-note") and "1 дополнительная" in page.inner_text("#fr-note"),
          page.inner_text("#fr-note"))
    # «В планы» у части, которой нет в списке
    btn = page.locator("#fr-parts .planbtn:not([hidden])").first
    btn.click()
    time.sleep(1.5)
    if DEMO and "Точного совпадения нет" in dialog_text(page):
        # демо-ролик на 1 серию против 500 в справочнике — сайт честно
        # переспрашивает; соглашаемся
        page.click(".veil.dialog .wide:not(.ghost)")
        time.sleep(1.5)
    check("«В планы» у части: записалось", "В планах" in btn.inner_text(), btn.inner_text())
    btn.click()
    time.sleep(1.5)
    check("второе нажатие убирает из планов", "В планы" == btn.inner_text().strip(), btn.inner_text())
    clean(con, page, "после «В планы»")
    page.keyboard.press("Escape")
    check("Esc закрыл окно частей", not page.evaluate("() => document.getElementById('veil-fr').classList.contains('show')"))
    # «×» на карточке — двойной щелчок
    page.hover("#grid .card >> nth=0")
    page.dblclick("#grid .card >> nth=0 >> .del")
    time.sleep(0.6)
    check("двойной щелчок по «×» карточки: окно подтверждения осталось открытым (B-02)",
          "Убрать из списка" in dialog_text(page), dialog_text(page)[:60])
    page.click(".veil.dialog .wide.ghost")
    time.sleep(0.3)
    check("«Отмена» закрывает окно, карточка на месте", not dialog_text(page) and page.locator("#grid .card").count() == 2)
    # поиск с медленным справочником: индикация, один запрос
    held = []           # запросы держим «в руках» и отпускаем сами — так
                        # страница по-настоящему ждёт, а не мы спим в обработчике
    ctx.route("**/api/find?*", lambda route: held.append(route))
    con.requests.clear()
    page.fill("#q", "фильм")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    time.sleep(1.0)
    check("медленный справочник: видно «ищем…» и заглушки", "ищем" in page.inner_text("#search-note")
          and page.locator("#search-grid .skeleton").count() > 0,
          (page.inner_text("#search-note"), page.locator("#search-grid .skeleton").count()))
    time.sleep(2.0)
    for r in held:
        r.continue_()
    page.wait_for_selector("#search-grid .card", timeout=15000)
    check("двойной Enter при медленном ответе — один запрос", api_count(con, "/api/find", "GET") == 1)
    ctx.unroute("**/api/find?*")
    # обрыв сети посреди сохранения
    ctx.route("**/api/library/**", lambda r: r.abort())
    page.hover("#grid .card >> nth=0")
    page.click("#grid .card >> nth=0 >> .del")
    time.sleep(0.5)
    page.click(".veil.dialog .wide:not(.ghost)")
    time.sleep(1.2)
    check("обрыв сети при удалении: понятное окно, карточка не пропала",
          "Нет связи с сервером" in dialog_text(page) and page.locator("#grid .card").count() == 2, dialog_text(page)[:80])
    page.keyboard.press("Escape")
    ctx.unroute("**/api/library/**")
    ctx.route("**/api/me/settings", lambda r: r.abort())
    page.click("#who")
    page.wait_for_selector("#ucard-wrap.show")
    page.click('#uc-theme .ucth[data-th="noch"]')
    time.sleep(0.8)
    check("обрыв сети при смене темы: тема на экране осталась, окно объясняет",
          "Тема не сохранилась" in dialog_text(page) and page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "noch")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    ctx.unroute("**/api/me/settings")
    con.take()
    con.take_bad()
    # «назад» со страницы просмотра — список перечитан (pageshow)
    page.goto(base + watch_url())
    page.wait_for_selector("#w-eplist .eprow")
    page.go_back()
    page.wait_for_selector("#grid .card")
    time.sleep(0.6)
    check("«Назад» со страницы просмотра — главная живая", page.locator("#grid .card").count() == 2)
    page.go_forward()
    page.wait_for_selector("#w-eplist .eprow")
    check("«Вперёд» — снова просмотр", "/watch" in page.url)
    ctx.close()

    # ------------------------------------------------------------------
    group("4. Настройки, профиль, фото, вход по коду")
    ctx = ctx_for(b, counter)
    page = ctx.new_page()
    con = Console(page)
    login(page, base, "friend")
    page.click("#who")
    page.click("#uc-set")
    page.wait_for_selector("#veil-set.show")
    for sec in ("profile", "look", "lang", "view", "safe"):
        page.click("#cab-t-" + sec)
        time.sleep(0.25)
        clean(con, page, "настройки: " + sec)
    page.click("#cab-t-profile")
    page.fill("#s-name", "  דני 😀  ")
    page.click("#set-save")
    wait(page, "() => document.getElementById('saved').classList.contains('show')")
    check("имя с ивритом и эмодзи сохранилось, пробелы по краям срезаны",
          page.evaluate("() => App.me.display_name") == "דני 😀" and page.inner_text("#whoami") == "דני 😀")
    page.fill("#s-name", "Друг")
    page.click("#set-save")
    wait(page, "() => document.getElementById('saved').classList.contains('show')")
    page.set_input_files("#s-photo", {"name": "фото ёлка.png", "mimeType": "image/png", "buffer": png_bytes(40, 30)})
    page.wait_for_selector("#veil-crop.show")
    page.click("#crop-ok")
    check("фото с русским именем файла загрузилось", wait(page, "() => App.me.has_avatar === true", 8))
    for name, mime, data, want in (("x.gif", "image/gif", b"GIF89a" + b"\x00" * 50, "Не тот формат"),
                                   ("x.svg", "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "Не тот формат"),
                                   ("big.png", "image/png", b"\x00" * (6 * 1024 * 1024), "Файл великоват"),
                                   ("fake.png", "image/png", b"not a png at all", "не удалось прочитать")):
        page.set_input_files("#s-photo", {"name": name, "mimeType": mime, "buffer": data})
        wait(page, "() => !!document.querySelector('.veil.dialog')", 3)
        check("фото %s: сказано «%s»" % (name, want), want in dialog_text(page), dialog_text(page)[:60])
        page.keyboard.press("Escape")
        time.sleep(0.2)
    page.click("#s-photo-clear")
    check("«Убрать фото» работает", wait(page, "() => App.me.has_avatar === false", 8))
    page.set_input_files("#s-banner", {"name": "баннер.png", "mimeType": "image/png", "buffer": png_bytes(300, 100)})
    check("баннер поставился", wait(page, "() => App.me.banner_at > 0", 8))
    page.click("#s-banner-clear")
    check("баннер убрался", wait(page, "() => !App.me.banner_at", 8))
    clean(con, page, "после фото и баннера")
    page.click("#cab-t-view")
    check("«Хентай» заперт, пока 18+ выключено", page.evaluate("() => document.getElementById('s-hentai').disabled")
          and "Сначала включи 18+" in page.inner_text("#hentai-why"))
    page.click("#s-adult")
    check("18+ включился", wait(page, "() => App.me.settings.adult === true"))
    page.click("#s-hentai")
    time.sleep(0.3)
    check("«Хентай» просит подтвердить возраст", "Только для взрослых" in dialog_text(page))
    page.click(".veil.dialog .wide.ghost")
    time.sleep(0.3)
    check("отказ — хентай выключен", page.get_attribute("#s-hentai", "aria-checked") == "false")
    page.click("#s-hentai")
    time.sleep(0.3)
    page.click(".veil.dialog .wide:not(.ghost)")
    check("подтверждение — хентай включён", wait(page, "() => App.me.settings.hentai === true"))
    page.click("#s-adult")
    check("выключил 18+ — хентай погас", wait(page, "() => App.me.settings.hentai === false && App.me.settings.adult === false"))
    page.click("#cab-t-lang")
    page.click('#s-animelang button[data-anime-lang="ensub"]')
    check("язык аниме сохраняется сразу", wait(page, "() => App.me.settings.anime_lang === 'ensub'"))
    page.click('#s-animelang button[data-anime-lang="ru"]')
    wait(page, "() => App.me.settings.anime_lang === 'ru'")
    page.click("#cab-t-look")
    page.click('#s-holiday button[data-h="off"]')
    page.click("#set-save")
    wait(page, "() => document.getElementById('saved').classList.contains('show')")
    check("«Выключить» праздник сохраняется у обычного аккаунта", page.evaluate("() => App.me.settings.holiday") == "off")
    check("кнопки праздников кроме «по календарю»/«выключить» обычному не видны",
          page.evaluate("() => Array.from(document.querySelectorAll('#s-holiday .adm-only')).every(b => b.hidden)"))
    page.click('#s-holiday button[data-h=""]')
    page.click("#set-save")
    wait(page, "() => document.getElementById('saved').classList.contains('show')")
    # вход по коду
    page.click("#cab-t-safe")
    page.click("#s-2fa")
    time.sleep(0.3)
    page.fill(".veil.dialog input", PASS)
    page.keyboard.press("Enter")
    check("окно с QR и ключом появилось", wait(page, "() => !document.getElementById('twofa-box').hidden", 6)
          and page.evaluate("() => (document.getElementById('twofa-qr').getAttribute('src') || '').indexOf('data:image/svg+xml') === 0"))
    secret = page.evaluate("() => document.getElementById('twofa-secret').textContent")
    page.fill("#twofa-code", "000000")
    page.click("#twofa-confirm")
    time.sleep(0.8)
    check("неверный код — ошибка на месте", "не подошёл" in page.inner_text("#twofa-err"))
    page.fill("#twofa-code", totp(secret))
    page.dblclick("#twofa-confirm")
    time.sleep(1.5)
    check("верный код (двойной щелчок) — окно с запасными кодами", "запасные коды" in dialog_text(page), dialog_text(page)[:80])
    page.keyboard.press("Escape")
    check("вход по коду включён", wait(page, "() => App.me.totp_on === true"))
    page.click("#s-2fa")
    time.sleep(0.3)
    page.fill(".veil.dialog input", "wrong-pass")
    page.keyboard.press("Enter")
    time.sleep(1.0)
    check("выключение с неверным паролем — отказ, переключатель остался", "Пароль не подошёл" in dialog_text(page)
          and page.get_attribute("#s-2fa", "aria-checked") == "true")
    page.keyboard.press("Escape")
    con.take()
    con.take_bad()
    # смена пароля
    page.click("#s-pass")
    page.fill("#p-cur", PASS)
    page.fill("#p-new", "short")
    page.click("#p-save")
    time.sleep(0.8)
    check("короткий пароль — ошибка словами", "короче" in page.inner_text("#p-err"), page.inner_text("#p-err"))
    page.fill("#p-new", "Noviy-Parol-Drug-77")
    page.dblclick("#p-save")
    time.sleep(1.5)
    check("смена пароля (двойной щелчок) — одно окно «Пароль изменён»", "Пароль изменён" in dialog_text(page))
    page.click(".veil.dialog .wide")
    page.wait_for_selector("#gate:not([hidden])", timeout=10000)
    page.fill("#lg-user", "friend")
    page.fill("#lg-pass", "Noviy-Parol-Drug-77")
    page.click("#btn-login")
    check("после смены пароля с кодом — просят код", wait(page, "() => !document.getElementById('lg-code-box').hidden", 6))
    page.fill("#lg-code", totp(secret))
    page.click("#btn-login")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)
    check("вход с кодом удался", page.evaluate("() => App.me && App.me.name === 'friend'"))
    con.take()
    con.take_bad()
    page.click("#who")
    page.click("#uc-set")
    page.wait_for_selector("#veil-set.show")
    page.click("#cab-t-safe")
    page.click("#s-logoutall")
    time.sleep(0.3)
    page.click(".veil.dialog .wide:not(.ghost)")
    check("«Выйти везде» ведёт на вход", wait(page, "() => !document.getElementById('gate').hidden", 8))
    ctx.close()

    # ------------------------------------------------------------------
    group("5. Админка")
    ctx = ctx_for(b, counter)
    page = ctx.new_page()
    con = Console(page)
    login(page, base, "hozyain")
    page.click("#who")
    page.click("#uc-admin")
    page.wait_for_selector("#admin-list .acc")
    time.sleep(0.3)
    clean(con, page, "админка")
    own = page.locator("#admin-list .acc", has_text="hozyain")
    check("на своей строке нет «Выключить» и «Удалить»", own.locator("button").count() == 0)
    my_id = page.evaluate("() => App.api.get('/api/admin/users').then(r => r.users.filter(u => u.login === 'hozyain')[0].id)")
    r1 = post(page, "/api/admin/users/%d/disable" % my_id, {})
    r2 = page.evaluate("(id) => App.api.del('/api/admin/users/' + id).then(r => r, e => ({error: e.message, status: e.status}))", my_id)
    check("сервер: себя выключить/удалить нельзя (409)", r1.get("status") == 409 and r2.get("status") == 409, (r1, r2))
    con.take()
    con.take_bad()
    page.click("#a-new")
    page.fill("#a-login", "newguy")
    page.fill("#a-pass", "Novyy-Parol-2026x")
    page.dblclick("#a-save")
    time.sleep(1.2)
    check("создание аккаунта (двойной щелчок) — создан, без «логин занят»",
          "newguy" in page.inner_text("#admin-list") and not page.inner_text("#a-err").strip(), page.inner_text("#a-err"))
    page.click("#a-new")
    page.fill("#a-login", "דני")
    page.fill("#a-pass", "Novyy-Parol-2026x")
    page.click("#a-save")
    time.sleep(1.0)
    check("логин на иврите — отказ словами", "латиница" in page.inner_text("#a-err"))
    con.take()
    con.take_bad()
    row = page.locator("#admin-list .acc", has_text="newguy")
    row.get_by_role("button", name="Выключить").click()
    time.sleep(0.3)
    page.click(".veil.dialog .wide:not(.ghost)")
    time.sleep(1.0)
    check("аккаунт выключен", "выключен" in page.locator("#admin-list .acc", has_text="newguy").inner_text())
    page.locator("#admin-list .acc", has_text="newguy").get_by_role("button", name="Включить").click()
    time.sleep(1.0)
    check("аккаунт включён", "Выключить" in page.locator("#admin-list .acc", has_text="newguy").inner_text())
    page.locator("#admin-list .acc", has_text="newguy").get_by_role("button", name="Удалить").dblclick()
    time.sleep(0.6)
    check("двойной щелчок «Удалить»: окно подтверждения на месте (B-02)", "Удалить аккаунт newguy" in dialog_text(page))
    page.click(".veil.dialog .wide:not(.ghost)")
    time.sleep(1.0)
    check("аккаунт удалён", "newguy" not in page.inner_text("#admin-list"))
    page.click("#cab-news")
    page.wait_for_selector("#veil-news.show")
    page.fill("#news-text", "Сервер перезапустится в 23:00")
    page.fill("#news-text-en", "Restart at 23:00")
    page.dblclick("#news-save")
    time.sleep(1.0)
    check("объявление сохранилось и показано", page.inner_text("#sitenews-text") == "Сервер перезапустится в 23:00"
          and "осталось" in page.inner_text("#news-count"))
    page.click("#news-del")
    time.sleep(0.3)
    page.click(".veil.dialog .wide:not(.ghost)")
    time.sleep(0.8)
    check("объявление убрано", page.evaluate("() => document.getElementById('sitenews').hidden"))
    clean(con, page, "админка после действий")
    ctx.close()

    # ------------------------------------------------------------------
    group("6. Английская страница: всё, включая меню плеера и ошибки сервера")
    ctx = ctx_for(b, counter)
    page = ctx.new_page()
    con = Console(page)
    login(page, base, "hozyain", lang="en")
    time.sleep(1.0)
    # в аккаунте hozyain уже сохранён русский (вход в группе 5) — нажатый
    # на странице входа EN должен победить (B-09)
    check("язык, нажатый на странице входа, не сбрасывается на сохранённый в аккаунте (B-09)",
          page.evaluate("() => document.documentElement.lang") == "en"
          and page.evaluate("() => App.me.settings.ui_lang") == "en",
          page.evaluate("() => [document.documentElement.lang, App.me.settings.ui_lang]"))
    clean(con, page, "главная (EN)", en=True)
    page.click("#who")
    time.sleep(0.3)
    clean(con, page, "карточка профиля (EN)", en=True)
    page.click("#uc-set")
    page.wait_for_selector("#veil-set.show")
    for sec in ("profile", "look", "lang", "view", "safe"):
        page.click("#cab-t-" + sec)
        time.sleep(0.25)
        clean(con, page, "настройки: " + sec + " (EN)", en=True)
    page.click("#cab-t-safe")
    page.click("#s-pass")
    clean(con, page, "смена пароля (EN)", en=True)
    page.keyboard.press("Escape")
    page.click("#who")
    page.click("#uc-admin")
    page.wait_for_selector("#admin-list .acc")
    page.click("#a-new")
    time.sleep(0.3)
    clean(con, page, "админка (EN)", en=True)
    page.click("#cab-news")
    page.wait_for_selector("#veil-news.show")
    clean(con, page, "объявление (EN)", en=True)
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    page.fill("#q", "naruto")
    page.keyboard.press("Enter")
    page.wait_for_selector("#search-grid .card")
    time.sleep(0.5)
    check("карточка поиска по-английски: Naruto · 2002 · TV series", "TV series" in page.inner_text("#search-grid"))
    page.click("#search-grid .card >> nth=0")
    page.wait_for_selector("#veil-fr.show")
    time.sleep(0.8)
    clean(con, page, "окно частей (EN)", en=True)
    check("склонения EN: «220 episodes», «2 parts of the main story · 1 extra»",
          "220 episodes" in page.inner_text("#fr-parts") and "2 parts of the main story" in page.inner_text("#fr-note")
          and "1 extra" in page.inner_text("#fr-note"), page.inner_text("#fr-note"))
    page.keyboard.press("Escape")
    page.click("#btn-roul")
    page.wait_for_selector("#roul.done")
    time.sleep(0.4)
    clean(con, page, "«Рандом» (EN)", en=True)
    check("«Рандом» EN: английские жанры и описание", "Action" in page.inner_text("#pickres") and "Naruto Uzumaki" in page.inner_text("#pickres"))
    page.keyboard.press("Escape")
    page.click("#btn-bell")
    time.sleep(0.6)
    clean(con, page, "колокольчик (EN)", en=True)
    page.keyboard.press("Escape")
    page.click("#btn-about")
    time.sleep(0.3)
    clean(con, page, "«Про сайт» (EN)", en=True)
    page.keyboard.press("Escape")
    page.hover("#grid .card >> nth=0")
    page.click("#grid .card >> nth=0 >> .del")
    time.sleep(0.3)
    clean(con, page, "окно «Убрать?» (EN)", en=True)
    page.keyboard.press("Escape")
    # ошибки сервера, которых нет в словаре страницы
    r = post(page, "/api/helper/ask", {"message": "   ", "history": [], "lang": "en"})
    check("фраза сервера вне словаря страницы — по-английски от сервера (B-01)",
          r.get("error") == ("The helper is not available in demo mode." if DEMO else "Type a question."), r)
    r = post(page, "/api/admin/users", {"login": "bad login!", "password": "Novyy-Parol-2026x"})
    check("ошибка про логин — по-английски (из словаря страницы: «Username»)",
          r.get("error", "").startswith("Username: Latin letters"), r)
    r = post(page, "/api/me/password", {"current": "", "new": ""})
    check("422 — человеческая английская фраза", r.get("error", "").startswith("Check the fields"), r)
    con.take()
    con.take_bad()
    page.goto(base + watch_url())
    page.wait_for_selector("#w-eplist .eprow")
    time.sleep(1.5)
    clean(con, page, "просмотр (EN)", en=True)
    for m in ("m-q", "m-e", "m-d", "m-s", "m-l"):
        page.click('.selbtn[data-menu="%s"]' % m)
        time.sleep(0.5)
        pr = page.evaluate("([e, a]) => window.__probe(e, a)", [True, EN_ALLOWED + ["Субтитры Luna"]])
        check("меню %s без русских слов (кроме имени озвучки источника)" % m, not pr["cyr"], pr["cyr"][:4])
        page.keyboard.press("Escape")
    page.click("#w-fr-card")
    time.sleep(0.3)
    check("вкладка «Вся история» EN", "watching" in page.inner_text("#w-fr-list") and "TV series" in page.inner_text("#w-fr-list"))
    page.click("#bigplay")
    wait(page, "() => %s.currentTime > 0.5" % V, 10)
    pr = page.evaluate("([e, a]) => window.__probe(e, a)", [True, EN_ALLOWED + ["Субтитры Luna"]])
    check("во время воспроизведения (EN) — без русских слов", not pr["cyr"], pr["cyr"][:4])
    check("подпись «Пауза/Play» по-английски", page.get_attribute("#c-play", "aria-label") in ("Pause", "Play"))
    for path, sel in (("/stats", "#year .mon"), ("/pravila", ".pravila-list")):
        page.goto(base + path)
        page.wait_for_selector(sel)
        time.sleep(0.8)
        clean(con, page, path + " (EN)", en=True)
    page.goto(base + "/stats")
    page.wait_for_selector("#year .mon")
    body = page.inner_text("body")
    check("итоги EN: подписи и месяцы по-английски, «full day(s)» / «not much yet»",
          "YEAR IN REVIEW" in body and "jan" in body and "days so far" in body
          and ("not much yet" in body or "full day" in body), body[:200])
    post(page, "/api/me/settings", {"ui_lang": "ru"})     # дальше hozyain нужен русским
    ctx.close()

    # гость по-английски
    ctx = ctx_for(b, counter)
    page = ctx.new_page()
    con = Console(page)
    page.goto(base + "/")
    page.wait_for_selector("#gate:not([hidden])")
    page.click('.langrow .lang[data-set-lang="en"]')
    page.click("#btn-guest")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)
    time.sleep(0.6)
    clean(con, page, "гость: главная (EN)", en=True)
    page.click("#btn-svyaz")
    # сначала страница рисует «гостям нельзя», потом отвечает сервер; в демо
    # его ответ — «в демо помощника нет» (оба верны) — ждём именно ответ
    want = "demo mode" if DEMO else "Guests can"
    wait(page, "() => document.getElementById('hlp-note').innerText.indexOf(%r) >= 0" % want, 6)
    check("гостю помощник закрыт, по-английски", want in page.inner_text("#hlp-note"), page.inner_text("#hlp-note"))
    clean(con, page, "гость: помощник (EN)", en=True)
    page.keyboard.press("Escape")
    page.fill("#q", "ecchi")
    page.keyboard.press("Enter")
    time.sleep(1.0)
    check("гость ищет «ecchi» — подсказка по-английски", "Guests never see 18+" in page.inner_text("#search-grid"))
    page.goto(base + watch_url())
    page.wait_for_selector("#w-eplist .eprow")
    time.sleep(1.0)
    clean(con, page, "гость: просмотр (EN)", en=True)
    check("гостю «not saved for guests»", "not saved for guests" in page.inner_text("#w-autobox"))
    ctx.close()

    # ------------------------------------------------------------------
    group("7. Плеер: поток, звук, озвучки, субтитры, серии, отметки")
    ctx = ctx_for(b, counter)
    page = ctx.new_page()
    con = Console(page)
    login(page, base, "hozyain")
    # группа 6 уже «посмотрела» часть серии — возвращаем полку в исходное: серия 2, 02:05
    post(page, "/api/library/progress", {"key": NARUTO, "source": SRC, "title": "Наруто",
                                         "total_eps": 3, "watched_ep": 2, "position": 125, "status": "watching", "cid": "mal:20"})
    page.goto(base + watch_url())
    page.wait_for_selector("#w-eplist .eprow")
    time.sleep(1.0)
    check("серия из полки: «2 / 3», «продолжить с 02:05»", page.inner_text("#v-e") == "2 / 3" and "02:05" in page.inner_text("#w-saved"),
          (page.inner_text("#v-e"), page.inner_text("#w-saved")))
    check("«Осталось» на второй из трёх серий — «~46 мин», а не «0 ч»", page.inner_text("#w-left") == "Осталось ~46 мин", page.inner_text("#w-left"))
    page.goto(base + watch_url() + "&ep=3")
    page.wait_for_selector("#w-eplist .eprow")
    time.sleep(0.8)
    check("на последней серии «Осталось ~23 мин», а не «0 ч» (B-05)", page.inner_text("#w-left") == "Осталось ~23 мин", page.inner_text("#w-left"))
    page.goto(base + watch_url() + "&ep=2")
    page.wait_for_selector("#w-eplist .eprow")
    time.sleep(0.8)
    page.click("#bigplay")
    check("поток играет (currentTime растёт)", wait(page, "() => %s.currentTime > 0.5" % V, 10))
    check("ровно один <video> на странице", page.evaluate("() => document.querySelectorAll('video').length") == 1)
    page.click("#c-mute")
    check("выключение звука", page.evaluate("() => %s.muted" % V) is True)
    page.click("#c-mute")
    check("включение звука", page.evaluate("() => %s.muted" % V) is False)
    page.evaluate("() => { var v = document.getElementById('c-vol'); v.value = 0.3; v.dispatchEvent(new Event('input')); }")
    check("громкость по ползунку", abs(page.evaluate("() => %s.volume" % V) - 0.3) < 0.01)
    if not DEMO:
        page.click("#dub-btn")
        time.sleep(0.2)
        check("меню озвучек: три строки, у субтитров подпись", "яп. + рус. текст" in page.inner_text("#m-d"))
        page.click("#m-d button >> nth=1")
        time.sleep(2.0)
        check("мёртвая озвучка: плашка «не отвечает», видео не остановилось",
              "не отвечает" in page.inner_text("#w-notice") and not page.evaluate("() => %s.paused" % V), page.inner_text("#w-notice")[:80])
        page.click("#dub-btn")
        page.click("#m-d button >> nth=2")
        check("субтитровая озвучка: дорожки появились, позиция не сброшена",
              wait(page, "() => document.querySelectorAll('video track').length === 2 && %s.currentTime > 1" % V, 10))
        time.sleep(1.5)
        check("русская дорожка включена сама, кнопка CC показывает RU",
              page.evaluate("() => Array.from(%s.textTracks).some(t => t.mode === 'showing' && t.language === 'ru')" % V)
              and page.inner_text("#v-c") == "RU")
        check("звук не пропал после смены озвучки", page.evaluate("() => !%s.muted && %s.volume > 0.2" % (V, V)))
        cues = page.evaluate("() => Array.from(%s.textTracks).map(t => t.cues ? Array.from(t.cues).map(c => c.text) : null)" % V)
        check("субтитры из cp1251: «ёж и Ёлка» без кракозябр", any(c and "Привет, мир — ёж и Ёлка" in c for c in cues), cues)
        # выключенная дорожка реплики не грузит — читаем её файл так же, как браузер
        en_vtt = page.evaluate("() => { var t = Array.from(document.querySelectorAll('video track')).filter(t => t.srclang === 'en')[0]; return t ? fetch(t.src).then(r => r.text()) : ''; }")
        check("субтитры из utf-8 с BOM: английские реплики, BOM и кракозябр нет",
              "Hello English cue" in en_vtt and "\ufeff" not in en_vtt and en_vtt.startswith("WEBVTT"), en_vtt[:80])
        src0 = page.evaluate("() => document.querySelector('video track').src")
        ct = page.evaluate("(u) => fetch(u).then(r => r.headers.get('content-type'))", src0)
        check("/api/subtitle отдаёт text/vtt; charset=utf-8", ct == "text/vtt; charset=utf-8", ct)
        page.evaluate("() => { %s.currentTime = 1.0; }" % V)
        time.sleep(0.8)
        active = page.evaluate("() => Array.from(%s.textTracks).filter(t => t.mode === 'showing').map(t => t.activeCues ? Array.from(t.activeCues).map(c => c.text) : [])" % V)
        check("реплика совпадает по времени (на 1-й секунде — первая)", any("Привет" in x for a in active for x in a), active)
        page.keyboard.press("c")
        time.sleep(0.2)
        check("клавиша C выключает субтитры", page.inner_text("#v-c") == "выкл")
        page.keyboard.press("c")
        time.sleep(0.2)
        check("клавиша C включает обратно", page.inner_text("#v-c") == "RU")
        page.click("#cc-btn")
        time.sleep(0.2)
        page.click("#m-c button >> nth=2")
        time.sleep(0.3)
        check("кнопка CC: выбор английской дорожки", page.inner_text("#v-c") == "EN"
              and page.evaluate("() => Array.from(%s.textTracks).filter(t => t.mode === 'showing').length === 1" % V))
        clean(con, page, "плеер с субтитрами", expected=(502,))
    else:
        # демо-источник: две озвучки — поток HLS и обычный файл; субтитров и «мёртвых» нет
        page.click("#dub-btn")
        time.sleep(0.2)
        check("меню озвучек (демо): «HLS-поток» и «Обычный файл»", "HLS-поток" in page.inner_text("#m-d") and "Обычный файл" in page.inner_text("#m-d"), page.inner_text("#m-d"))
        page.click("#m-d button >> nth=1")
        check("смена озвучки на обычный файл: играет, позиция не сброшена",
              wait(page, "() => %s.currentSrc.indexOf('.mp4') > 0 && %s.currentTime > 1" % (V, V), 10), page.evaluate("() => %s.currentSrc" % V))
        check("звук не пропал после смены озвучки", page.evaluate("() => !%s.muted && %s.volume > 0.2" % (V, V)))
        clean(con, page, "плеер после смены озвучки (демо)")
    counter["media_urls"] = []
    page.click("#w-next")
    time.sleep(3.0)
    urls = counter.get("media_urls", [])
    check("следующая серия: играет, старый поток не грузит куски дальше",
          page.inner_text("#v-e") == "3 / 3" and not page.evaluate("() => %s.paused" % V)
          and page.evaluate("() => document.querySelectorAll('video').length") == 1, (page.inner_text("#v-e"), urls))
    check("звук сохранился после смены серии", page.evaluate("() => !%s.muted && %s.volume > 0.2" % (V, V)))
    page.evaluate("() => { var v = %s; v.currentTime = v.duration * 0.92; }" % V)
    time.sleep(1.5)
    lib = page.evaluate("() => App.api.get('/api/library')")
    it = [i for i in lib["items"] if i["key"] == NARUTO][0]
    check("после 90% серия отмечена просмотренной (watched_ep = 3)", it["watched_ep"] == 3, it)
    time.sleep(2.5)
    check("последняя серия закончилась — «Это последняя серия», автоперехода некуда", "последняя" in page.inner_text("#w-nexttitle"))
    page.click("#w-eplist .eprow >> nth=0")
    time.sleep(0.5)
    if not page.evaluate("() => document.getElementById('bigplay').hidden"):
        page.click("#bigplay")
    wait(page, "() => %s.currentTime > 2.2" % V, 10)
    page.evaluate("() => %s.pause()" % V)
    pos = page.evaluate("() => %s.currentTime" % V)
    page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    time.sleep(0.8)
    page.goto(base + watch_url())          # без &ep= — серию выбирает полка
    page.wait_for_selector("#w-eplist .eprow")
    time.sleep(1.2)
    check("секунда сохранилась и восстановилась после ухода со страницы",
          page.inner_text("#v-e") == "1 / 3" and "00:0%d" % int(pos) in page.inner_text("#w-saved"), (pos, page.inner_text("#v-e"), page.inner_text("#w-saved")))
    page.click("#bigplay")
    check("продолжает с сохранённой секунды", wait(page, "() => %s.currentTime >= %d" % (V, int(pos)), 10))
    if DEMO:
        # у демо-потока одно качество; второе — у озвучки «Обычный файл» (1080 и 480)
        page.click("#dub-btn")
        page.click("#m-d button >> nth=1")
        wait(page, "() => %s.currentSrc.indexOf('.mp4') > 0 && %s.currentTime > 0.5" % (V, V), 10)
        page.click('[data-menu="m-q"]')
        page.click("#m-q button >> nth=1")
        check("смена качества (демо, 480) — играет", wait(page, "() => %s.currentSrc.indexOf('trailer.mp4') > 0 && %s.currentTime > 0.5" % (V, V), 10),
              page.evaluate("() => %s.currentSrc" % V))
    else:
        page.click('[data-menu="m-q"]')
        page.click("#m-q button >> nth=1")
        check("смена качества на обычный файл — играет", wait(page, "() => %s.currentSrc.indexOf('clip.webm') > 0 && %s.currentTime > 0.5" % (V, V), 10))
    page.click("#c-full")
    time.sleep(0.5)
    check("полный экран включается, подпись «Свернуть»", page.evaluate("() => !!document.fullscreenElement")
          and page.get_attribute("#c-full", "aria-label") == "Свернуть")
    page.keyboard.press("Escape")
    time.sleep(0.4)
    page.click("#w-autobox .sw")
    time.sleep(0.8)
    check("«Следующая сама» под плеером ушла в аккаунт", page.evaluate("() => App.api.get('/api/me').then(m => m.settings.autonext)") is False)
    page.click("#w-autobox .sw")
    time.sleep(0.5)
    clean(con, page, "плеер после всех действий", expected=(502,))
    for ep in ("-5", "99999999999", "abc"):
        page.goto(base + watch_url() + "&ep=" + ep)
        page.wait_for_selector("#w-eplist .eprow")
        time.sleep(0.6)
        errs = con.take()
        check("ep=%s в адресе — открылась серия из полки, без ошибок" % ep, page.inner_text("#v-e") == "1 / 3" and not errs,
              (page.inner_text("#v-e"), errs))
    page.goto(base + "/watch?key=mock%3Anope&source=" + SRC + "&title=%D0%9D%D0%B5%D1%82")
    wait(page, "() => !document.getElementById('w-notice').hidden", 8)
    check("выдуманный ключ: плашка объясняет и предлагает другой источник",
          "этого аниме нет" in page.inner_text("#w-notice") and not page.evaluate("() => document.getElementById('w-notice-go').hidden"))
    con.take()
    con.take_bad()
    ctx.close()

    # ------------------------------------------------------------------
    group("8. Помощник")
    ctx = ctx_for(b, counter)
    page = ctx.new_page()
    con = Console(page)
    login(page, base, "hozyain")
    page.click("#btn-svyaz")
    if DEMO:
        # в демо помощника нет: окно открывается и объясняет это, поля вопроса нет
        time.sleep(0.8)
        check("демо: помощник объясняет, что недоступен, поля вопроса нет",
              not page.evaluate("() => document.getElementById('hlp').hidden")
              and page.evaluate("() => document.getElementById('hlp-form').hidden")
              and "демо" in page.inner_text("#hlp").lower(), page.inner_text("#hlp")[:120])
        clean(con, page, "помощник (демо)", expected=(503,))
        ctx.close()
    else:
        wait(page, "() => !document.getElementById('hlp-form').hidden")
        page.fill("#hlp-in", "")
        page.keyboard.press("Enter")
        time.sleep(0.4)
        check("пустой вопрос не уходит", page.locator("#hlp-log .hlpmsg").count() == 0)
        page.fill("#hlp-in", "😀 " + "очень длинный вопрос " * 40)
        check("поле режет на 500 символах", page.evaluate("() => document.getElementById('hlp-in').value.length") == 500)
        page.keyboard.press("Enter")
        page.wait_for_selector("#hlp-log .hlpmsg.bot:not(.typing)")
        time.sleep(0.3)
        check("ответ с HTML внутри — текстом, кнопка только из белого списка",
              XSS.split('"')[0] in page.inner_text("#hlp-log") and page.evaluate("() => Array.from(document.querySelectorAll('#hlp-log [data-go]')).map(b => b.getAttribute('data-go'))") == ["settings-look"])
        page.click("#hlp-log [data-go]")
        time.sleep(0.6)
        check("чип «Открыть» ведёт в настройки → Вид", page.evaluate("() => document.getElementById('veil-set').classList.contains('show') && document.getElementById('cab-t-look').classList.contains('on')"))
        page.keyboard.press("Escape")
        page.click("#btn-svyaz")
        page.click("#hlp-clear")
        check("«Очистить» стирает разговор", page.locator("#hlp-log .hlpmsg").count() == 0)
        clean(con, page, "помощник")
        ctx.close()

    # ------------------------------------------------------------------
    group("9. Внешний вид: темы, праздники, сдвиг раскладки, телефон")
    ctx = ctx_for(b, counter)
    ctx.add_init_script(CLS_INIT)
    page = ctx.new_page()
    con = Console(page)
    login(page, base, "hozyain")
    # каждую страницу грузим дважды и берём меньшее: первый заход тянет
    # шрифты и картинки, и сдвиг от них зависит от скорости машины, а не
    # от вёрстки
    for path, sel in (("/", "#grid .boxcard"), (watch_url(), "#w-eplist .eprow"), ("/stats", "#year .mon"), ("/pravila", ".pravila-list")):
        got = []
        for _ in range(2):
            page.goto(base + path)
            page.wait_for_selector(sel)
            time.sleep(2.0)
            got.append(page.evaluate("() => window.__cls"))
        check("%s: сдвиг раскладки при загрузке мал (CLS < 0.1)" % path.split("?")[0], min(got) < 0.1, got)
        if DEMO and path == "/stats":
            # плашка «демо» показывается сразу, по памяти браузера, а не после ответа сервера (B-10)
            check("демо: плашка «демо» на месте до ответа сервера (запомнена в браузере)",
                  page.evaluate("() => localStorage.getItem('animedik.mode')") == "demo"
                  and not page.evaluate("() => document.getElementById('demonote').hidden"))
    for hol in ("off", "halloween", "newyear", "sakura"):
        for theme in ("kak-seychas", "noch", "ugol", "bumaga"):
            post(page, "/api/me/settings", {"theme": theme, "holiday": hol})
            page.goto(base + "/")
            page.wait_for_selector("#grid .boxcard")
            time.sleep(0.8)
            what = "%s / %s" % (theme, hol)
            clean(con, page, what)
            ratios = page.evaluate(CONTRAST_JS, KEY_TEXT)
            low = [r for r in ratios if r["ratio"] < r["need"]]
            check(what + ": контраст ключевых надписей ≥ 4.5:1", not low, low)
            perf = page.evaluate(PERF_JS)
            check(what + ": анимации не грузят процессор (нет длинных задач, ≥ 30 к/с)",
                  perf["longMs"] < 300 and perf["fps"] >= 30, perf)
            if hol != "off":
                check(what + ": праздник наложен (слои и значок у логотипа)",
                      page.evaluate("() => document.querySelectorAll('.prazdnik').length === 2 && document.querySelectorAll('.prazdznak').length === 1"))
                page.click("#btn-roul")
                check(what + ": кнопка под летающими украшениями нажимается", wait(page, "() => document.getElementById('roul').classList.contains('done')", 8))
                page.keyboard.press("Escape")
            shot(page, "theme-%s-%s" % (theme, hol))
        if hol != "off":
            page.goto(base + watch_url())
            page.wait_for_selector("#w-eplist .eprow")
            time.sleep(0.8)
            clean(con, page, "просмотр / " + hol)
    post(page, "/api/me/settings", {"theme": "kak-seychas", "holiday": ""})
    ctx.close()
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    ctx.add_init_script(INIT)
    install_routes(ctx, counter)
    page = ctx.new_page()
    login(page, base, "hozyain")
    post(page, "/api/me/settings", {"holiday": "newyear"})
    page.reload()
    page.wait_for_selector("#grid .boxcard")
    time.sleep(1.0)
    check("«меньше движения»: снег стоит на месте, подсказка об этом в настройках есть",
          page.evaluate("() => { var s = document.querySelector('.snezh'); return !!s && getComputedStyle(s).animationName === 'none'; }")
          and not page.evaluate("() => document.getElementById('hol-motion').hidden"))
    post(page, "/api/me/settings", {"holiday": ""})
    ctx.close()
    ctx = b.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(INIT)
    install_routes(ctx, counter)
    ctx.clock.set_fixed_time("2026-12-25T12:00:00")
    page = ctx.new_page()
    login(page, base, "hozyain")
    time.sleep(0.8)
    check("праздник по календарю: 25 декабря — Новый год со снегом",
          page.evaluate("() => document.documentElement.getAttribute('data-holiday')") == "newyear"
          and page.evaluate("() => document.querySelectorAll('.snezh').length") > 0)
    ctx.close()
    for (w, h) in ((390, 844), (360, 640)):
        ctx = b.new_context(viewport={"width": w, "height": h}, is_mobile=True, has_touch=True)
        ctx.add_init_script(INIT)
        install_routes(ctx, counter)
        page = ctx.new_page()
        con = Console(page)
        page.goto(base + "/")
        page.wait_for_selector("#gate:not([hidden])")
        page.evaluate("() => localStorage.removeItem('animedik.2fa-hint-off')")
        page.fill("#lg-user", "hozyain")
        page.fill("#lg-pass", PASS)
        page.click("#btn-login")
        page.wait_for_selector("#app:not([hidden])")
        page.wait_for_selector("#boot", state="hidden", timeout=15000)
        wait(page, "() => document.getElementById('toast').classList.contains('show')", 6)
        time.sleep(0.5)
        # что лежит под серединой кнопки «?» — сама кнопка (или значок внутри неё)?
        at = page.evaluate("() => { var b = document.getElementById('btn-svyaz'); var r = b.getBoundingClientRect(); var e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return [b.contains(e), e && (e.id || e.tagName)]; }")
        check("%d: совет про вход по коду не закрывает кнопку «?» (B-06)" % w, at[0] is True, at)
        page.click("#btn-svyaz")
        time.sleep(0.5)
        # в демо поля вопроса нет (помощник выключен) — проверяем само окно и его записку
        reach = page.evaluate("() => { var el = document.getElementById(%s); var r = el.getBoundingClientRect(); var e = document.elementFromPoint(r.left + 20, r.top + r.height / 2); return e && (e === el || el.contains(e)); }"
                              % ("'hlp-note'" if DEMO else "'hlp-in'"))
        check("%d: помощник открылся, совет убран, поле вопроса не перекрыто" % w,
              not page.evaluate("() => document.getElementById('hlp').hidden")
              and not page.evaluate("() => document.getElementById('toast').classList.contains('show')")
              and reach, reach)
        page.keyboard.press("Escape")
        clean(con, page, "%d: главная" % w)
        page.click("#btn-burger")
        time.sleep(0.3)
        check("%d: бургер открывает меню" % w, page.evaluate("() => document.getElementById('acts').classList.contains('open')"))
        page.click("#btn-bell")
        time.sleep(0.5)
        check("%d: колокольчик внутри меню" % w, page.evaluate("() => document.getElementById('bell-drop').classList.contains('show')"))
        clean(con, page, "%d: меню и колокольчик" % w)
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        page.click("#who")
        time.sleep(0.4)
        clean(con, page, "%d: карточка профиля (лист снизу)" % w)
        page.click("#uc-set")
        page.wait_for_selector("#veil-set.show")
        page.click("#cab-t-view")
        time.sleep(0.3)
        clean(con, page, "%d: настройки" % w)
        page.keyboard.press("Escape")
        page.fill("#q", "наруто")
        page.keyboard.press("Enter")
        page.wait_for_selector("#search-grid .card")
        page.click("#search-grid .card >> nth=0")
        page.wait_for_selector("#veil-fr.show")
        time.sleep(0.5)
        clean(con, page, "%d: окно частей" % w)
        page.keyboard.press("Escape")
        page.goto(base + watch_url())
        page.wait_for_selector("#w-eplist .eprow")
        time.sleep(1.0)
        clean(con, page, "%d: просмотр" % w)
        page.click('.selbtn[data-menu="m-s"]')
        time.sleep(0.5)
        clean(con, page, "%d: меню источника" % w)
        page.keyboard.press("Escape")
        page.goto(base + "/stats")
        page.wait_for_selector("#year .mon")
        time.sleep(0.6)
        clean(con, page, "%d: итоги" % w)
        ctx.close()


def _pw_python():
    mine = os.path.abspath(sys.executable)
    for cand in (os.environ.get("PW_PYTHON"), shutil.which("python3"), shutil.which("python")):
        if not cand or os.path.abspath(cand) == mine:
            continue
        try:
            ok = subprocess.run([cand, "-c", "import playwright.sync_api"], capture_output=True, timeout=60).returncode == 0
        except Exception:                                  # noqa: BLE001
            ok = False
        if ok:
            return cand
    return None


def run():
    only_browser = "--browser-only" in sys.argv
    if not only_browser:
        static_checks()
    try:
        import playwright.sync_api  # noqa: F401
        have = True
    except ImportError:
        have = False
    if have:
        try:
            browser_checks()
        except Exception as exc:                           # noqa: BLE001
            group("Браузерная часть")
            check("браузерная часть отработала до конца", False, repr(exc))
    elif not only_browser:
        other = _pw_python()
        group("Браузерная часть")
        if other:
            print("  Playwright есть у " + other + " — браузерная часть идёт там.", flush=True)
            env = dict(os.environ)
            env["FE_SERVER_PY"] = sys.executable
            r = subprocess.run([other, os.path.abspath(__file__), "--browser-only"], env=env)
            check("браузерная часть прошла (" + other + ")", r.returncode == 0)
        else:
            print("  !!! ПРОПУСК: Playwright не найден. Поставить: pip install playwright && playwright install chromium")
    print()
    if FAILS:
        print("ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("Все проверки прохода 39 прошли.")


if __name__ == "__main__":
    run()
