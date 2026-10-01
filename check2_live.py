"""Проход 2: гоняем настоящий сервер и смотрим, что он реально делает.

Здесь не разбор текста, а живые запросы: полный путь пользователя,
краевые случаи, попытки сломать.
"""
import io
import os
import sys
import tempfile
import time
import logging

# Проверки публичной версии идут в демонстрационном режиме: другого здесь
# нет. Внешние источники видео не входят в этот репозиторий, и
# единственный рабочий источник — свободное видео (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p2.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass2"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import main, security, store             # noqa: E402

# Вывод здесь на русском, а консоль Windows по умолчанию живёт в cp1251:
# первая же стрелка или галочка роняла весь запуск с UnicodeEncodeError,
# и проверки обрывались на середине, не дойдя до сути. Просим поток
# работать в utf-8; там, где он и так utf-8, строка ничего не меняет.
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


def C():
    return TestClient(main.app)


def login(c, u, p):
    r = c.post("/api/auth/login", json={"login": u, "password": p})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def guest(c):
    r = c.post("/api/auth/guest")
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def reset():
    main.login_guard.reset()
    main.api_limit.reset()
    main.search_limit.reset()
    main.guest_limit.reset()
    main.write_limit.reset()


def run():
    store.init()
    reset()
    admin_id = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")
    store.create_user("misha", "Tihiy-Signal-2026", "user", "misha")

    # ------------------------------------------------------------------
    group("Полный путь пользователя")
    c = C()
    check("до входа главная отдаётся", c.get("/").status_code == 200)
    check("но данные закрыты", c.get("/api/library").status_code == 401)
    r = login(c, "valera", "Zaliv-Pepel-2026")
    check("вход прошёл", r.status_code == 200)
    me = r.json()["me"]
    check("роль администратор", me["role"] == "admin")
    check("настройки пришли пустыми", me["settings"] == {})

    r = c.post("/api/me/settings", json={"accent": "sky", "depth": "mid", "lang": "en",
                                         "card_size": 224, "logo": 2, "sort": "title",
                                         "autonext": False, "automark": True, "show_done": False})
    check("настройки сохранились", r.status_code == 200, r.text[:60])
    got = c.get("/api/me").json()["settings"]
    check("настройки читаются обратно", got.get("accent") == "sky" and got.get("logo") == 2, got)
    check("логическое поле сохранилось как есть", got.get("autonext") is False, got.get("autonext"))

    r = c.post("/api/library/progress", json={
        "key": "source-a:100", "source": "demo", "title": "Тайтл",
        "poster": "https://example.com/a.jpg", "year": 2024,
        "genres": "Драма,Детектив", "total_eps": 12, "watched_ep": 3,
        "position": 421, "status": "watching"})
    check("прогресс сохранён", r.status_code == 200)
    item = c.get("/api/library").json()["items"][0]
    check("обложка сохранилась", item["poster"] == "https://example.com/a.jpg", item["poster"])
    check("жанры сохранились", item["genres"] == "Драма,Детектив", item["genres"])
    check("секунда сохранилась", item["position"] == 421, item["position"])
    check("год сохранился", item["year"] == 2024, item["year"])

    # ------------------------------------------------------------------
    group("Краевые значения прогресса")
    cases = [
        ("отрицательная секунда", {"key": "k1", "position": -10}, 422),
        ("секунда больше суток", {"key": "k1", "position": 999999}, 422),
        ("серия больше предела", {"key": "k1", "watched_ep": 99999}, 422),
        ("год из будущего", {"key": "k1", "year": 9999}, 422),
        ("год слишком ранний", {"key": "k1", "year": 1800}, 422),
        ("нет ключа", {"title": "x"}, 422),
        ("пустой ключ", {"key": ""}, 422),
        ("статус выдуман", {"key": "k1", "status": "хакер"}, 400),
    ]
    for name, body, want in cases:
        got = c.post("/api/library/progress", json=body).status_code
        check(name + " отклонена", got == want, f"{got}, ждали {want}")

    check("нулевая секунда допустима",
          c.post("/api/library/progress", json={"key": "k2", "position": 0}).status_code == 200)
    check("серия ноль допустима",
          c.post("/api/library/progress", json={"key": "k3", "watched_ep": 0}).status_code == 200)

    # ------------------------------------------------------------------
    group("Повторное сохранение не плодит записей")
    before = len(c.get("/api/library").json()["items"])
    for i in range(5):
        c.post("/api/library/progress", json={"key": "same", "position": i * 100})
    after = c.get("/api/library").json()["items"]
    same = [x for x in after if x["key"] == "same"]
    check("запись одна", len(same) == 1, len(same))
    check("сохранилась последняя секунда", same[0]["position"] == 400, same[0]["position"])

    # ------------------------------------------------------------------
    group("Удаление")
    check("удаление своей записи", c.request("DELETE", "/api/library/same").status_code == 200)
    check("запись исчезла",
          not [x for x in c.get("/api/library").json()["items"] if x["key"] == "same"])
    check("повторное удаление не падает",
          c.request("DELETE", "/api/library/same").status_code == 200)
    check("удаление несуществующего не падает",
          c.request("DELETE", "/api/library/нет-такого").status_code == 200)
    check("ключ с косой чертой обрабатывается",
          c.request("DELETE", "/api/library/source-a/100").status_code in (200, 404))

    # ------------------------------------------------------------------
    group("Аватар: краевые случаи")
    from PIL import Image
    def png(w, h, color=(80, 120, 160)):
        b = io.BytesIO()
        Image.new("RGB", (w, h), color).save(b, "PNG")
        return b.getvalue()

    check("широкая картинка", c.post("/api/me/avatar", content=png(1200, 300)).status_code == 200)
    got = c.get("/api/me/avatar")
    img = Image.open(io.BytesIO(got.content))
    check("обрезана в квадрат", img.size == (128, 128), img.size)
    check("узкая картинка", c.post("/api/me/avatar", content=png(200, 1400)).status_code == 200)
    check("крошечная картинка", c.post("/api/me/avatar", content=png(8, 8)).status_code == 200)
    check("пустое тело убирает аватар",
          c.post("/api/me/avatar", content=b"").json().get("removed") is True)
    check("после удаления аватара его нет", c.get("/api/me/avatar").status_code == 404)

    check("текст вместо картинки", c.post("/api/me/avatar", content=b"hello").status_code == 400)
    check("обрезанный png", c.post("/api/me/avatar", content=png(100, 100)[:60]).status_code == 400)
    check("gif не принимается", c.post("/api/me/avatar", content=b"GIF89a" + b"\x00" * 200).status_code == 400)
    big = b"\x00" * (AVATAR := 400 * 1024)
    check("файл больше предела", c.post("/api/me/avatar", content=big).status_code == 413)

    # ------------------------------------------------------------------
    group("Профиль: краевые случаи")
    check("пустое имя отклонено",
          c.post("/api/me/profile", json={"display_name": "   "}).status_code == 400)
    check("имя длиннее предела отклонено",
          c.post("/api/me/profile", json={"display_name": "и" * 200}).status_code == 422)
    check("управляющие символы вычищаются",
          c.post("/api/me/profile", json={"display_name": "имя\u0000\u001b[31m"}).status_code == 200)
    saved = c.get("/api/me").json()["display_name"]
    check("в имени нет управляющих символов",
          all(ch.isprintable() for ch in saved), repr(saved))
    check("цвет без решётки отклонён",
          c.post("/api/me/profile", json={"avatar_color": "FF0000"}).status_code == 400)
    check("короткий цвет принят",
          c.post("/api/me/profile", json={"avatar_color": "#abc"}).status_code == 200)

    # ------------------------------------------------------------------
    group("Настройки: только известные значения")
    # sort и card_size из настроек убраны вместе с сортировкой и размером
    # обложек: список теперь один, без вкладок и без перенастройки размера.
    for field, value in [("accent", "rainbow"), ("depth", "light"),
                         ("lang", "de")]:
        got = c.post("/api/me/settings", json={field: value}).status_code
        check(f"{field}={value} отклонено", got == 400, got)
    # Удалённые поля должны именно игнорироваться, а не сохраняться:
    # иначе старый клиент тихо набьёт базу мусором.
    c.post("/api/me/settings", json={"card_size": 9999, "sort": "random"})
    left = c.get("/api/me").json()["settings"]
    check("удалённые настройки не оседают в базе",
          "card_size" not in left and "sort" not in left, sorted(left))
    check("номер логотипа вне диапазона отклонён",
          c.post("/api/me/settings", json={"logo": 42}).status_code == 422)

    # ------------------------------------------------------------------
    group("Гость")
    reset()
    g = C()
    guest(g)
    check("гость видит список источников", g.get("/api/sources").status_code == 200)
    n = len(g.get("/api/sources").json())
    # В публичной версии источник ровно один — демонстрационный. Он
    # обслуживает оба языка: свободное видео не бывает «русским» или
    # «английским». Число проверяем не ради числа — это ловит случайно
    # затесавшийся сюда внешний источник.
    check("источник ровно один", n == 1, n)
    ru_ids = [s["id"] for s in g.get("/api/sources").json()]
    en_ids = [s["id"] for s in g.get("/api/sources?lang=en").json()]
    check("и это демонстрационный", ru_ids == ["demo"], ru_ids)
    check("один и тот же для обоих языков", ru_ids == en_ids, en_ids)
    check("каждый источник имеет подпись",
          all(s.get("label") and s.get("note") for s in g.get("/api/sources").json()))
    for path, body in [("/api/library/progress", {"key": "x"}),
                       ("/api/library/watched", {"key": "x"}),
                       ("/api/me/settings", {"accent": "sky"}),
                       ("/api/me/profile", {"display_name": "x"}),
                       ("/api/me/avatar", None)]:
        got = g.post(path, json=body) if body else g.post(path, content=b"")
        check(f"гостю закрыт {path}", got.status_code == 403, got.status_code)
    check("гость не удаляет записи",
          g.request("DELETE", "/api/library/x").status_code == 403)
    check("аватара у гостя нет", g.get("/api/me/avatar").status_code == 404)

    # ------------------------------------------------------------------
    group("Статистика")
    reset()
    a = C()
    login(a, "valera", "Zaliv-Pepel-2026")
    s0 = a.get("/api/stats/year").json()
    base_eps = s0["episodes"]
    a.post("/api/library/watched", json={
        "key": "st", "title": "Т", "genres": "Драма", "watched_ep": 1,
        "position": 1200, "total_eps": 5})
    s1 = a.get("/api/stats/year").json()
    check("серия учтена", s1["episodes"] == base_eps + 1, s1["episodes"])
    check("время учтено", s1["seconds"] >= 1200, s1["seconds"])
    check("день записан", len(s1["days"]) >= 1, s1["days"])
    check("жанр разложен", any(g[0] == "Драма" for g in s1["genres"]), s1["genres"])
    check("тайтл попал в список", any(t[0] == "Т" for t in s1["titles"]), s1["titles"])

    # ------------------------------------------------------------------
    group("Ограничение частоты записи")
    reset()
    w = C()
    login(w, "misha", "Tihiy-Signal-2026")
    codes = [w.post("/api/library/progress",
                    json={"key": f"r{i}", "position": i}).status_code for i in range(120)]
    check("частая запись притормаживается", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")

    # ------------------------------------------------------------------
    group("Страницы отдаются")
    reset()
    p = C()
    login(p, "valera", "Zaliv-Pepel-2026")
    for path in ["/", "/watch", "/stats"]:
        r = p.get(path)
        check(f"{path} отдаётся", r.status_code == 200 and "<html" in r.text.lower(), r.status_code)
    for f in ["app.css", "app.js", "index.js", "watch.js", "stats.js", "hls.min.js"]:
        r = p.get("/static/" + f)
        check(f"{f} отдаётся", r.status_code == 200 and len(r.content) > 100, r.status_code)
    check("несуществующая страница", p.get("/hacker").status_code == 404)
    check("несуществующий файл", p.get("/static/нет.js").status_code == 404)

    # ------------------------------------------------------------------
    group("Заголовки")
    h = p.get("/").headers
    csp = h.get("content-security-policy", "")
    check("политика содержимого есть", bool(csp))
    check("скрипты только свои", "script-src 'self';" in csp, csp[:60])
    check("исполнение строк запрещено", "unsafe-eval" not in csp)
    check("видео с чужих доменов разрешено", "media-src" in csp and "https:" in csp)
    check("рабочий поток из blob разрешён", "worker-src 'self' blob:" in csp)
    check("вставка в чужой фрейм запрещена", "frame-ancestors 'none'" in csp)
    check("страница не кэшируется", "no-store" in h.get("cache-control", ""))

    # ------------------------------------------------------------------
    group("Устойчивость к мусору")
    reset()
    m = C()
    login(m, "valera", "Zaliv-Pepel-2026")
    junk = [
        ("строка вместо объекта", "просто строка"),
        ("число вместо объекта", 42),
        ("вложенность", {"key": {"a": {"b": {"c": 1}}}}),
        ("массив в поле", {"key": ["a", "b"]}),
        ("null в поле", {"key": None}),
    ]
    for name, body in junk:
        r = m.post("/api/library/progress", json=body)
        check(name + " отклонён без падения", r.status_code in (400, 422), r.status_code)
    check("сервер жив после мусора", C().get("/api/health").status_code == 200)

    r = m.post("/api/auth/login", content=b"{broken json",
               headers={"Content-Type": "application/json"})
    check("битый json не роняет", r.status_code in (400, 422), r.status_code)
    check("сервер жив", C().get("/api/health").status_code == 200)

    # ------------------------------------------------------------------
    group("Поиск: проверка входа")
    reset()
    s = C()
    login(s, "valera", "Zaliv-Pepel-2026")
    check("источник-модуль отклонён",
          s.get("/api/search", params={"q": "test", "source": "os"}).status_code == 400)
    check("обход каталога в источнике отклонён",
          s.get("/api/search", params={"q": "test", "source": "../../os"}).status_code == 400)
    check("пустой запрос отклонён",
          s.get("/api/search", params={"q": ""}).status_code == 422)
    check("серия отрицательная отклонена",
          s.get("/api/videos", params={"key": "k", "ordinal": -1}).status_code == 422)
    check("серия огромная отклонена",
          s.get("/api/videos", params={"key": "k", "ordinal": 10**9}).status_code == 422)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 2 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 2 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
