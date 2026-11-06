"""Проход 42 (проверяющий C): демо-режим — куда сервер ходит наружу.

Запускается при любом MODE: сам ставит MODE=demo до импорта приложения.

Как проверяется. До импорта приложения подменяются все способы открыть
соединение: socket.socket.connect / connect_ex, socket.create_connection и
socket.getaddrinfo. Каждая попытка к адресу не из localhost записывается
(узел) и обрывается ошибкой — в сеть не уходит ничего, но видно, кто
пытался. Затем обходятся ВСЕ ручки сайта от имени гостя и вошедшего (с
включёнными 18+ и «Хентай»), включая помощника с подставленным ключом.

Что должно быть:
  * ни одного обращения к источникам видео, к их хостингам и прокладке;
  * ни одного обращения к модели помощника (Groq), даже с ключом в .env;
  * справочники (Shikimori, AniList, Jikan) — по решению владельца в демо
    работают (anime_demo.py: «справочник остаётся рабочим: это открытые
    данные»). Набор их перечисляет отдельно и проверяет, что больше никого.
"""
import logging
import os
import socket
import sys
import tempfile

os.environ["MODE"] = "demo"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p42.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass42-pepper-0123456789")
# Ключ помощника задан — а наружу он всё равно не должен пойти.
os.environ["HELPER_API_KEY"] = "gsk_fake_key_for_check42"
os.environ["UPDATES_EVERY_MIN"] = "0"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

ATTEMPTS: list[str] = []
LOCAL = ("127.0.0.1", "::1", "localhost", "testserver", "0.0.0.0")


def _host_of(address):
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _blocked(host):
    if host in LOCAL or host.startswith("127."):
        return False
    ATTEMPTS.append(host)
    return True


_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_create = socket.create_connection
_orig_gai = socket.getaddrinfo


def _connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and _blocked(_host_of(address)):
        raise OSError("check42: исходящее соединение запрещено")
    return _orig_connect(self, address)


def _connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and _blocked(_host_of(address)):
        return 111
    return _orig_connect_ex(self, address)


def _create(address, *a, **kw):
    if _blocked(_host_of(address)):
        raise OSError("check42: исходящее соединение запрещено")
    return _orig_create(address, *a, **kw)


def _gai(host, *a, **kw):
    if host is not None and _blocked(str(host)):
        raise socket.gaierror("check42: имя наружу не разрешаем")
    return _orig_gai(host, *a, **kw)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.create_connection = _create
socket.getaddrinfo = _gai

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import anime, main, store                 # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Long-Passw0rd-142"
CATALOGS = ("shikimori.one", "graphql.anilist.co", "api.jikan.moe")


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)[:300]) if extra and not ok else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def reset():
    for lim in (main.login_guard, main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.catalog_limit, main.helper_limit,
                main.helper_day_limit):
        lim.reset()


def walk(c, who):
    """Все ручки, которые может дёрнуть страница, — с правдоподобными
    параметрами. Ответы не важны, важно, кто куда пошёл."""
    gets = [
        ("/api/me", {}), ("/api/sources", {"lang": "ru"}), ("/api/sources", {"lang": "ensub"}),
        ("/api/search", {"q": "Naruto", "lang": "ru"}),
        ("/api/search", {"q": "Naruto", "lang": "endub", "source": "en_d"}),
        ("/api/episodes", {"key": "bunny", "source": "demo", "title": "Naruto"}),
        ("/api/videos", {"key": "bunny", "ordinal": 1, "source": "demo", "title": "Naruto"}),
        ("/api/videos", {"key": "bunny", "ordinal": 1, "source": "ru_a", "title": "x"}),
        ("/api/videos", {"key": "bunny", "ordinal": 1, "source": "demo", "title": "x",
                         "lang": "ensub"}),
        ("/api/resolve", {"title": "Наруто", "title_en": "Naruto", "lang": "ru", "warm": 0}),
        ("/api/resolve", {"title": "Наруто", "title_en": "Naruto", "lang": "ensub", "rx": 1}),
        ("/api/resolve", {"title": "Clean", "source": "rx_a", "only": 1, "rx": 1,
                          "lang": "ensub"}),
        ("/api/subs", {"title": "Наруто", "title_en": "Naruto", "lang": "ensub"}),
        ("/api/where", {"title": "Наруто", "title_en": "Naruto", "lang": "ru", "wide": 1}),
        ("/api/find", {"q": "наруто"}), ("/api/find", {"q": "хентай"}),
        ("/api/find", {"q": "этти"}),
        ("/api/franchise", {"id": "naruto"}), ("/api/related", {"title": "Наруто"}),
        ("/api/about", {"title": "Наруто", "lang": "en"}),
        ("/api/poster", {"title": "Наруто", "title_en": "Naruto"}),
        ("/api/random", {}), ("/api/random", {"warm": 1}),
        ("/api/helper", {}), ("/api/news", {}), ("/api/library", {}),
        ("/api/subtitle/" + "0" * 32 + ".vtt", {}),
        ("/api/hls", {"u": "aHR0cHM6Ly9leGFtcGxlLmNvbS94Lm0zdTg", "r": "", "s": "1.0"}),
    ]
    codes = {}
    for path, params in gets:
        reset()
        codes[path + str(sorted(params.items()))] = c.get(path, params=params).status_code
    if who == "user":
        reset()
        codes["helper/ask"] = c.post("/api/helper/ask", json={
            "message": "Как включить субтитры?", "history": []}).status_code
        reset()
        codes["poster"] = c.post("/api/library/poster", json={
            "key": "bunny", "source": "demo", "title": "Naruto"}).status_code
        reset()
        codes["updates"] = c.get("/api/updates").status_code
        reset()
        codes["admin-check"] = c.post("/api/admin/english-check").status_code
    return codes


def run():
    group("1. Демо: обход всех ручек с подменёнными соединениями")
    check("режим демо", anime.DEMO is True)
    store.init()
    store.create_user("demo42", PASS, "admin", "demo42")
    with TestClient(main.app) as guest_c:
        r = guest_c.post("/api/auth/guest", json={})
        guest_c.headers["X-CSRF-Token"] = r.json().get("csrf", "")
        walk(guest_c, "guest")
    with TestClient(main.app) as c:
        reset()
        r = c.post("/api/auth/login", json={"login": "demo42", "password": PASS})
        c.headers["X-CSRF-Token"] = r.json().get("csrf", "")
        reset()
        c.post("/api/me/settings", json={"adult": True, "hentai": True})
        codes = walk(c, "user")
        check("помощник в демо отвечает «недоступен» (503)", codes.get("helper/ask") == 503,
              codes.get("helper/ask"))
        reset()
        st = c.get("/api/helper").json()
        check("/api/helper говорит state=demo", st.get("state") == "demo", st)
        check("ручки проверки чужих источников нет (404/405)",
              codes.get("admin-check") in (404, 405), codes.get("admin-check"))
        reset()
        v = c.get("/api/videos", params={"key": "bunny", "ordinal": 1, "source": "demo",
                                         "title": "Naruto"}).json()
        check("видео демо — прямые ссылки (не через прокладку /api/hls)",
              v.get("videos") and all(not x["url"].startswith("/api/") for x in v["videos"]), v)

    # Сам перехват работает: прямой запрос наружу записан и оборван.
    import httpx
    before = len(ATTEMPTS)
    try:
        httpx.get("https://example.com/", timeout=2, trust_env=False)
        through = True
    except Exception:                               # noqa: BLE001
        through = False
    check("перехват соединений работает (example.com записан и оборван)",
          not through and "example.com" in ATTEMPTS[before:], ATTEMPTS[before:])
    del ATTEMPTS[before:]

    hosts = sorted(set(ATTEMPTS))
    other = [h for h in hosts if not any(h == x or h.endswith("." + x) for x in CATALOGS)]
    print("  попытки соединений наружу (узлы): " + (", ".join(hosts) or "нет"))
    check("ни одного обращения к источникам видео, хостингам, прокладке и Groq",
          other == [], other)
    check("Groq не спрашивался даже с ключом в .env",
          not any("groq" in h for h in hosts), hosts)
    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 42 — ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("ПРОХОД 42 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
