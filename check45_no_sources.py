"""Проход 45: личный режим без единого источника — честный 501.

В публичной версии источников видео нет (api/anime.py, SOURCES пустой).
Кто запустит её с MODE=private, не подключив свой источник, должен
получить живой сайт — вход, каталог, полку, админку, — а на ручках видео
понятный ответ 501 «Источники видео не подключены», а не «источник не
отвечает», не «неизвестный источник» и не внутреннюю ошибку 500.

Запускается при любом MODE: сам ставит MODE=private до импорта и
подменные источники (fake_sources.py) НЕ подключает. Сети нет.
"""
import logging
import os
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p45.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass45-pepper-0123456789")
logging.getLogger("httpx").setLevel(logging.ERROR)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient            # noqa: E402
from api import anime, catalog, main, store          # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Long-Passw0rd-145"


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)[:300]) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def reset():
    for lim in (main.login_guard, main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.catalog_limit):
        lim.reset()


def run():
    group("1. Источников нет — и сервер это знает")
    check("режим личный, источников нет", not anime.DEMO and anime.SOURCES == {}
          and anime.no_sources(), list(anime.SOURCES))
    records = []

    class Catch(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Catch(level=logging.WARNING)
    logging.getLogger("anime").addHandler(handler)
    try:
        with TestClient(main.app) as c:
            check("при запуске в журнале — предупреждение про 501",
                  any("не подключены" in m and "501" in m for m in records), records[-3:])
            check("/api/health — 200", c.get("/api/health").status_code == 200)
            check("главная — 200", c.get("/").status_code == 200)
            check("/api/mode — личный", c.get("/api/mode").json() == {"demo": False})
    finally:
        logging.getLogger("anime").removeHandler(handler)

    store.init()
    store.create_user("admin1", PASS, "admin", "Админ")
    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": "admin1", "password": PASS})
    check("вход работает", r.status_code == 200, r.status_code)
    c.headers["X-CSRF-Token"] = r.json().get("csrf", "")

    group("2. Ручки видео — 501 с понятным текстом")
    calls = [
        ("/api/search", {"q": "Naruto"}),
        ("/api/search", {"q": "Naruto", "source": "ru_a"}),
        ("/api/episodes", {"key": "k", "source": "demo", "title": "Naruto"}),
        ("/api/videos", {"key": "k", "ordinal": 1, "title": "Naruto"}),
        ("/api/resolve", {"title": "Naruto"}),
        ("/api/resolve", {"title": "Naruto", "lang": "ensub", "rx": 1}),
        ("/api/subs", {"title": "Naruto"}),
        ("/api/where", {"title": "Naruto"}),
    ]
    for path, params in calls:
        reset()
        r = c.get(path, params=params)
        check(f"{path} {params} — 501", r.status_code == 501
              and "не подключены" in r.json().get("detail", "")
              and "are connected" in r.json().get("detail_en", ""), (r.status_code, r.text[:120]))
    reset()
    check("/api/sources — пустой список", c.get("/api/sources").json() == [])
    try:
        anime.get_extractor("ru_a")
        code = None
    except Exception as exc:                        # noqa: BLE001
        code = getattr(exc, "status_code", type(exc).__name__)
    check("anime.get_extractor — тоже 501", code == 501, code)

    group("3. Остальное живёт как обычно")

    async def no_net(*a, **k):
        return []
    saved = catalog._shiki_found, catalog._anilist_search
    catalog._shiki_found = catalog._anilist_search = no_net
    try:
        reset()
        r = c.post("/api/library/progress", json={"key": "k1", "source": "", "title": "Своё",
                                                  "watched_ep": 1})
        check("полка: запись сохраняется", r.status_code == 200, (r.status_code, r.text[:100]))
        reset()
        r = c.get("/api/library")
        check("полка: читается", r.status_code == 200 and r.json()["items"], r.text[:100])
        reset()
        r = c.get("/api/updates")
        check("колокольчик: не 500", r.status_code < 500, r.status_code)
        reset()
        r = c.post("/api/library/poster", json={"key": "k1", "source": "", "title": "Своё"})
        check("обложка с полки: не 500", r.status_code < 500, r.status_code)
        reset()
        r = c.get("/api/admin/users")
        check("админка: список людей", r.status_code == 200, r.status_code)
    finally:
        catalog._shiki_found, catalog._anilist_search = saved

    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 45 — ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("ПРОХОД 45 — личный режим без источников отвечает честно.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
