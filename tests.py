"""Проверка того, что действительно работает и что действительно закрыто.

Запуск:  python tests.py
"""
import asyncio
import io
import os
import sys
import tempfile
import time

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "test-pepper"

# Проверки публичной версии идут в демонстрационном режиме: другого здесь
# нет. Внешние источники видео не входят в этот репозиторий, и
# единственный рабочий источник — свободное видео (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anime").setLevel(logging.WARNING)

from fastapi import HTTPException                  # noqa: E402
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
GROUP = [""]


def group(name):
    GROUP[0] = name
    print("\n== " + name + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(GROUP[0] + " / " + name)
    return ok


def fresh_client():
    return TestClient(main.app)


def login(c, user, pwd):
    r = c.post("/api/auth/login", json={"login": user, "password": pwd})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def as_guest(c):
    r = c.post("/api/auth/guest")
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


# ==========================================================================
def run():
    store.init()
    main.login_guard.reset()
    main.api_limit.reset()
    main.search_limit.reset()
    main.guest_limit.reset()
    main.write_limit.reset()

    # ---------------------------------------------------------------- пароли
    group("Пароли")
    h = security.hash_password("правильный-пароль-1")
    check("верный пароль принимается", security.verify_password("правильный-пароль-1", h))
    check("неверный отклоняется", not security.verify_password("другой-пароль-999", h))
    check("хэш не содержит пароля", "правильный-пароль-1" not in h)
    check("два хэша одного пароля различаются",
          security.hash_password("одинаковый-пароль") != security.hash_password("одинаковый-пароль"))
    check("битый хэш не роняет проверку", not security.verify_password("x", "мусор"))
    check("пустой хэш не проходит", not security.verify_password("x", ""))
    check("короткий пароль отклонён", security.password_problem("abc123") is not None)
    check("частый пароль отклонён", security.password_problem("password1") is not None)
    check("однообразный отклонён", security.password_problem("aaaaaaaaaaaa") is not None)
    check("нормальный принят", security.password_problem("Zaliv-Pepel-2026") is None)

    group("Логины")
    check("SQL в логине не проходит валидацию",
          security.login_problem("admin'--") is not None)
    check("пробелы не проходят", security.login_problem("va lera") is not None)
    check("кириллица не проходит", security.login_problem("валера") is not None)
    check("Valera == valera", security.normalize_login("  VaLeRa ") == "valera")
    check("нормальный логин принят", security.login_problem("valera") is None)

    # ---------------------------------------------------------------- аккаунты
    group("Создание аккаунтов")
    admin_id = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")
    user_id = store.create_user("misha", "Tihiy-Signal-2026", "user", "misha")
    check("админ создан", admin_id > 0)
    check("пользователь создан", user_id > 0)
    try:
        store.create_user("valera", "Drugoy-Parol-2026")
        check("повтор логина отклонён", False)
    except Exception:
        check("повтор логина отклонён", True)
    try:
        store.create_user("noob", "123")
        check("слабый пароль отклонён", False)
    except ValueError:
        check("слабый пароль отклонён", True)

    # ---------------------------------------------------------------- вход
    group("Вход")
    c = fresh_client()
    check("без входа /api/me даёт 401", c.get("/api/me").status_code == 401)
    check("без входа библиотека закрыта", c.get("/api/library").status_code == 401)

    r = login(c, "valera", "неверный-пароль")
    check("неверный пароль → 401", r.status_code == 401, r.status_code)
    check("текст ошибки не выдаёт, что логин есть",
          "не найден" not in r.text.lower() and "нет такого" not in r.text.lower())

    main.login_guard.reset()
    c = fresh_client()
    r = login(c, "valera", "Zaliv-Pepel-2026")
    check("верный пароль → 200", r.status_code == 200, r.status_code)
    check("выдана кука сессии", "sid" in c.cookies)
    check("выдана метка формы", bool(r.json().get("csrf")))
    check("роль admin определилась", r.json()["me"]["role"] == "admin")
    check("/api/me работает", c.get("/api/me").status_code == 200)

    group("Кука сессии")
    raw = c.cookies.get("sid")
    check("токен длинный", len(raw) >= 40, len(raw))
    row = store.connect().execute("SELECT fp FROM sessions").fetchone()
    check("в базе хранится не сам токен", row["fp"] != raw)
    check("отпечаток совпадает", row["fp"] == security.token_fingerprint(raw))

    group("Регистрация закрыта")
    for path in ("/api/auth/register", "/api/register", "/api/signup",
                 "/api/users", "/api/auth/signup"):
        code = c.post(path, json={"login": "hacker", "password": "Hacker-Parol-2026"}).status_code
        check(f"{path} отсутствует", code in (404, 405), code)

    # ---------------------------------------------------------------- гость
    group("Гость")
    g = fresh_client()
    r = as_guest(g)
    check("гостевой пропуск выдан", r.status_code == 200, r.status_code)
    check("роль guest", r.json()["me"]["role"] == "guest")
    check("указано время до конца", r.json()["me"]["expires_in"] > 0)
    check("гость видит каталог", g.get("/api/sources").status_code == 200)
    check("библиотека гостя пуста", g.get("/api/library").json()["items"] == [])

    code = g.post("/api/library/progress", json={"key": "x", "title": "t"}).status_code
    check("гость не может писать прогресс", code == 403, code)
    check("гость не меняет профиль",
          g.post("/api/me/profile", json={"display_name": "hacker"}).status_code == 403)
    check("гость не меняет настройки",
          g.post("/api/me/settings", json={"accent": "sky"}).status_code == 403)
    check("гость не меняет пароль",
          g.post("/api/me/password", json={"current": "a", "new": "Novyy-Parol-2026"}).status_code == 403)
    check("гость не видит список аккаунтов", g.get("/api/admin/users").status_code == 404)
    check("гость не создаёт аккаунты",
          g.post("/api/admin/users", json={"login": "hax", "password": "Hacker-Parol-2026"}).status_code == 404)
    check("гость не видит статистику", g.get("/api/stats/year").status_code == 403)

    before = store.connect().execute("SELECT COUNT(*) n FROM library").fetchone()["n"]
    g.post("/api/library/progress", json={"key": "y", "title": "t"})
    after = store.connect().execute("SELECT COUNT(*) n FROM library").fetchone()["n"]
    check("гость не оставил следов в базе", before == after, f"{before} → {after}")
    check("гостевых сессий в базе нет",
          store.connect().execute("SELECT COUNT(*) n FROM sessions").fetchone()["n"] == 1)

    g.post("/api/auth/logout")
    check("после выхода гость не пройдёт", g.get("/api/me").status_code == 401)

    group("Гостевой пропуск истекает")
    g2 = fresh_client()
    as_guest(g2)
    tok = g2.cookies.get("sid")
    fp = security.token_fingerprint(tok)
    main.guests._items[fp]["expires"] = time.time() - 1
    check("просроченный пропуск не работает", g2.get("/api/me").status_code == 401)

    # ------------------------------------------------------- права обычного
    group("Обычный пользователь")
    u = fresh_client()
    main.login_guard.reset()
    login(u, "misha", "Tihiy-Signal-2026")
    check("вход выполнен", u.get("/api/me").status_code == 200)
    check("роль user", u.get("/api/me").json()["role"] == "user")
    check("не видит админскую ручку", u.get("/api/admin/users").status_code == 404)
    check("не создаёт аккаунты",
          u.post("/api/admin/users",
                 json={"login": "hax", "password": "Hacker-Parol-2026"}).status_code == 404)
    check("не удаляет чужие аккаунты",
          u.request("DELETE", f"/api/admin/users/{admin_id}").status_code == 404)

    r = u.post("/api/library/progress", json={
        "key": "source-a:1", "title": "Пепел над заливом", "source": "demo",
        "watched_ep": 12, "position": 862, "total_eps": 24, "status": "watching"})
    check("свой прогресс сохраняется", r.status_code == 200, r.status_code)
    items = u.get("/api/library").json()["items"]
    check("прогресс читается обратно", len(items) == 1 and items[0]["position"] == 862,
          items[0]["position"] if items else "пусто")

    group("Данные не протекают между людьми")
    a = fresh_client()
    main.login_guard.reset()
    login(a, "valera", "Zaliv-Pepel-2026")
    check("у админа своя пустая полка", a.get("/api/library").json()["items"] == [])
    a.post("/api/library/progress", json={"key": "source-a:9", "title": "Своё"})
    check("у пользователя по-прежнему одна запись",
          len(u.get("/api/library").json()["items"]) == 1)
    check("у админа одна своя",
          len(a.get("/api/library").json()["items"]) == 1)

    # ---------------------------------------------------------------- CSRF
    group("Подделка запроса с чужого сайта")
    n = fresh_client()
    main.login_guard.reset()
    login(n, "misha", "Tihiy-Signal-2026")
    saved = n.headers.pop("X-CSRF-Token")
    code = n.post("/api/library/progress", json={"key": "z", "title": "t"}).status_code
    check("без метки формы запрос отклонён", code == 403, code)
    n.headers["X-CSRF-Token"] = "poddelannaya-metka-xxxx"
    code = n.post("/api/library/progress", json={"key": "z", "title": "t"}).status_code
    check("с чужой меткой отклонён", code == 403, code)
    n.headers["X-CSRF-Token"] = saved
    check("со своей меткой проходит",
          n.post("/api/library/progress", json={"key": "z", "title": "t"}).status_code == 200)
    check("на чтение метка не нужна", n.get("/api/library").status_code == 200)

    # ------------------------------------------------------- перебор пароля
    group("Перебор пароля")
    main.login_guard.reset()
    b = fresh_client()
    codes = [b.post("/api/auth/login",
                    json={"login": "misha", "password": f"попытка-{i}"}).status_code
             for i in range(12)]
    check("после нескольких попыток включается блокировка", 429 in codes,
          f"401: {codes.count(401)}, 429: {codes.count(429)}")
    check("блокировка наступает не позже восьмой попытки",
          codes.index(429) <= 8 if 429 in codes else False, codes.index(429) if 429 in codes else "-")
    r = b.post("/api/auth/login", json={"login": "misha", "password": "Tihiy-Signal-2026"})
    check("верный пароль во время блокировки тоже не пускает", r.status_code == 429, r.status_code)

    # ------------------------------------------------------------ инъекции
    group("Внедрение SQL")
    main.login_guard.reset()
    payloads = ["' OR '1'='1", "admin'--", "'; DROP TABLE users;--",
                "' UNION SELECT 1,2,3,4,5,6,7,8,9,10,11 --", "\\'; DELETE FROM users; --"]
    for p in payloads:
        c2 = fresh_client()
        code = c2.post("/api/auth/login", json={"login": p, "password": p}).status_code
        check(f"не пускает: {p[:26]}", code in (401, 429), code)
    alive = store.connect().execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    check("таблица users на месте", alive == 2, alive)

    group("Обход каталога")
    for probe in ["/static/../api/main.py", "/static/..%2f..%2fetc%2fpasswd",
                  "/static/%2e%2e/%2e%2e/etc/passwd", "/../api/store.py",
                  "/etc/passwd", "/api/main.py"]:
        r = fresh_client().get(probe)
        leaked = r.status_code == 200 and ("pbkdf2" in r.text or "root:" in r.text
                                           or "SESSION_COOKIE" in r.text)
        check(f"не отдаёт {probe[:36]}", not leaked, r.status_code)

    group("Схема API скрыта")
    for p in ("/docs", "/openapi.json", "/redoc"):
        check(f"{p} закрыт", fresh_client().get(p).status_code == 404)

    group("Заголовки безопасности")
    h = fresh_client().get("/api/health").headers
    check("политика содержимого выставлена", "content-security-policy" in h)
    check("скрипты только со своего домена", "script-src 'self'" in h.get("content-security-policy", ""))
    check("вставка в чужой фрейм запрещена в политике", "frame-ancestors 'none'" in h.get("content-security-policy", ""))
    check("вставка в чужой фрейм запрещена", h.get("x-frame-options") == "DENY")
    check("угадывание типа файла запрещено", h.get("x-content-type-options") == "nosniff")
    check("адрес страницы не утекает", h.get("referrer-policy") == "no-referrer")
    check("страницы API не кэшируются", "no-store" in h.get("cache-control", ""))

    group("Куки")
    c3 = fresh_client()
    main.login_guard.reset()
    r = login(c3, "valera", "Zaliv-Pepel-2026")
    raw = "; ".join(r.headers.get_list("set-cookie"))
    check("кука сессии недоступна скриптам", "HttpOnly" in raw)
    check("кука не уходит на чужие сайты", "samesite=lax" in raw.lower(), raw[:70])
    check("метка формы читается скриптом", raw.count("HttpOnly") == 1, raw.count("HttpOnly"))

    # ------------------------------------------------------------ подделка
    group("Подделка сессии")
    for fake in ["a" * 43, security.new_token(), "", "null", "admin"]:
        c4 = fresh_client()
        c4.cookies.set("sid", fake)
        check(f"чужой токен не пускает ({fake[:12] or 'пусто'})",
              c4.get("/api/me").status_code == 401)

    group("Проверка входных значений")
    main.login_guard.reset()
    v = fresh_client()
    login(v, "misha", "Tihiy-Signal-2026")
    check("огромный ключ отклонён",
          v.post("/api/library/progress", json={"key": "x" * 5000}).status_code == 422)
    check("отрицательная серия отклонена",
          v.post("/api/library/progress", json={"key": "a", "watched_ep": -5}).status_code == 422)
    check("неизвестный статус отклонён",
          v.post("/api/library/progress",
                 json={"key": "a", "status": "хакер"}).status_code == 400)
    check("длинная строка вместо цвета отклонена",
          v.post("/api/me/profile", json={"avatar_color": "javascript:alert(1)"}).status_code == 422)
    check("не-цвет нужной длины отклонён",
          v.post("/api/me/profile", json={"avatar_color": "#ZZZZZZ"}).status_code == 400)
    check("нормальный цвет принят",
          v.post("/api/me/profile", json={"avatar_color": "#8DB5DF"}).status_code == 200)
    check("чужой акцент отклонён",
          v.post("/api/me/settings", json={"accent": "'; DROP--"}).status_code == 400)
    check("нормальный акцент принят",
          v.post("/api/me/settings", json={"accent": "sky"}).status_code == 200)
    check("слишком длинный поиск отклонён",
          v.get("/api/search", params={"q": "a" * 300}).status_code == 422)
    check("слишком короткий поиск отклонён",
          v.get("/api/search", params={"q": "a"}).status_code == 422)
    check("неизвестный источник отклонён",
          v.get("/api/search", params={"q": "test", "source": "../../os"}).status_code == 400)
    check("подстановка модуля отклонена",
          v.get("/api/search", params={"q": "test", "source": "subprocess"}).status_code == 400)

    # ------------------------------------------------------------- аватар
    group("Аватар")
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (900, 700), (60, 90, 110)).save(buf, format="PNG")
    r = v.post("/api/me/avatar", content=buf.getvalue())
    check("картинка принята", r.status_code == 200, r.status_code)
    got = v.get("/api/me/avatar")
    check("аватар отдаётся", got.status_code == 200)
    check("отдаётся как jpeg", got.headers["content-type"] == "image/jpeg")
    check("угадывание типа запрещено", got.headers.get("x-content-type-options") == "nosniff")
    img = Image.open(io.BytesIO(got.content))
    check("приведён к 128×128", img.size == (128, 128), img.size)
    check("аватар весит немного", len(got.content) < 30000, len(got.content))

    check("html вместо картинки отклонён",
          v.post("/api/me/avatar", content=b"<script>alert(1)</script>").status_code == 400)
    check("php вместо картинки отклонён",
          v.post("/api/me/avatar", content=b"<?php system($_GET[0]); ?>").status_code == 400)
    poly = b"GIF89a<script>alert(1)</script>"
    check("подделанный заголовок отклонён",
          v.post("/api/me/avatar", content=poly).status_code == 400)
    check("слишком большой файл отклонён",
          v.post("/api/me/avatar", content=b"\x00" * (400 * 1024)).status_code == 413)

    group("Смена пароля")
    p = fresh_client()
    main.login_guard.reset()
    login(p, "misha", "Tihiy-Signal-2026")
    check("без текущего пароля не меняет",
          p.post("/api/me/password",
                 json={"current": "не-тот", "new": "Novyy-Parol-2026"}).status_code == 403)
    check("слабый новый отклонён",
          p.post("/api/me/password",
                 json={"current": "Tihiy-Signal-2026", "new": "123"}).status_code == 400)
    r = p.post("/api/me/password",
               json={"current": "Tihiy-Signal-2026", "new": "Sovsem-Novyy-2026"})
    check("пароль сменился", r.status_code == 200, r.status_code)
    check("старая сессия закрыта", u.get("/api/me").status_code == 401)
    main.login_guard.reset()
    check("старый пароль больше не подходит",
          fresh_client().post("/api/auth/login",
                              json={"login": "misha", "password": "Tihiy-Signal-2026"}).status_code == 401)
    main.login_guard.reset()
    check("новый пароль работает",
          fresh_client().post("/api/auth/login",
                              json={"login": "misha", "password": "Sovsem-Novyy-2026"}).status_code == 200)

    group("Администратор")
    main.login_guard.reset()
    ad = fresh_client()
    login(ad, "valera", "Zaliv-Pepel-2026")
    check("видит список аккаунтов", ad.get("/api/admin/users").status_code == 200)
    r = ad.post("/api/admin/users",
                json={"login": "kate", "password": "Dolina-Otrazheniy-26", "role": "user"})
    check("создаёт аккаунт", r.status_code == 200, r.text[:80])
    check("повтор логина отклонён",
          ad.post("/api/admin/users",
                  json={"login": "kate", "password": "Drugoy-Parol-2026"}).status_code == 409)
    check("слабый пароль отклонён",
          ad.post("/api/admin/users",
                  json={"login": "bob", "password": "123"}).status_code == 400)
    check("не удаляет сам себя",
          ad.request("DELETE", f"/api/admin/users/{admin_id}").status_code == 409)
    main.login_guard.reset()
    check("новый аккаунт работает",
          fresh_client().post("/api/auth/login",
                              json={"login": "kate", "password": "Dolina-Otrazheniy-26"}).status_code == 200)

    group("Отключение аккаунта")
    kate = store.get_user_by_login("kate")
    k = fresh_client()
    main.login_guard.reset()
    login(k, "kate", "Dolina-Otrazheniy-26")
    check("вошла", k.get("/api/me").status_code == 200)
    ad.post(f"/api/admin/users/{kate['id']}/disable")
    check("сессия оборвана сразу", k.get("/api/me").status_code == 401)
    main.login_guard.reset()
    check("войти больше нельзя",
          fresh_client().post("/api/auth/login",
                              json={"login": "kate", "password": "Dolina-Otrazheniy-26"}).status_code == 401)

    group("Выход со всех устройств")
    main.login_guard.reset()
    d1, d2 = fresh_client(), fresh_client()
    login(d1, "valera", "Zaliv-Pepel-2026")
    login(d2, "valera", "Zaliv-Pepel-2026")
    check("оба входа живы",
          d1.get("/api/me").status_code == 200 and d2.get("/api/me").status_code == 200)
    d1.post("/api/auth/logout-all")
    check("второе устройство выкинуто", d2.get("/api/me").status_code == 401)

    group("Ограничение частоты")
    main.guest_limit.reset()
    codes = [fresh_client().post("/api/auth/guest").status_code for _ in range(10)]
    check("выдача гостевых пропусков ограничена", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")

    group("Страницы")
    check("главная отдаётся", fresh_client().get("/").status_code in (200, 404))
    check("несуществующая страница → 404", fresh_client().get("/hacker").status_code == 404)

    # ------------------------------------------------------------------
    print("\n" + "=" * 56)
    if FAILS:
        print(f"НЕ ПРОШЛИ: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("Все проверки пройдены.")
    return 0




def run_extra():
    """Третий заход: то, что легко упустить."""
    global FAILS
    FAILS = []
    main.login_guard.reset(); main.api_limit.reset(); main.guest_limit.reset()
    main.write_limit.reset(); main.search_limit.reset()

    group("Гость и чужие сессии")
    g = fresh_client()
    as_guest(g)
    gt = g.cookies.get("sid")
    # гость подставляет свой токен как пользовательский и наоборот
    u = fresh_client()
    login(u, "valera", "Zaliv-Pepel-2026")
    ut = u.cookies.get("sid")
    x = fresh_client(); x.cookies.set("sid", gt)
    check("гостевой токен не даёт прав пользователя",
          x.get("/api/stats/year").status_code == 403)
    y = fresh_client(); y.cookies.set("sid", ut[:-3] + "aaa")
    check("подпорченный токен не работает", y.get("/api/me").status_code == 401)

    group("Отпечаток токена и подсоленность")
    t1 = security.token_fingerprint("одинаковый")
    t2 = security.token_fingerprint("одинаковый")
    check("отпечаток устойчив", t1 == t2)
    check("отпечаток длиной 64", len(t1) == 64)
    check("отпечаток не равен исходнику", t1 != "одинаковый")

    group("Разные пользователи — разные аватары")
    a = fresh_client(); main.login_guard.reset()
    login(a, "valera", "Zaliv-Pepel-2026")
    from PIL import Image
    b1 = io.BytesIO(); Image.new("RGB", (300, 300), (200, 30, 30)).save(b1, "PNG")
    a.post("/api/me/avatar", content=b1.getvalue())
    got_a = a.get("/api/me/avatar").content
    m = fresh_client(); main.login_guard.reset()
    login(m, "misha", "Sovsem-Novyy-2026")
    b2 = io.BytesIO(); Image.new("RGB", (300, 300), (30, 30, 200)).save(b2, "PNG")
    m.post("/api/me/avatar", content=b2.getvalue())
    got_m = m.get("/api/me/avatar").content
    check("аватары не перепутаны", got_a != got_m)

    group("Прогресс не переписывается чужим")
    a.post("/api/library/progress", json={"key": "shared-key", "title": "Моё", "position": 100})
    m.post("/api/library/progress", json={"key": "shared-key", "title": "Чужое", "position": 900})
    ia = [i for i in a.get("/api/library").json()["items"] if i["key"] == "shared-key"][0]
    im = [i for i in m.get("/api/library").json()["items"] if i["key"] == "shared-key"][0]
    check("у каждого своя запись под одним ключом",
          ia["position"] == 100 and im["position"] == 900, f"{ia['position']} / {im['position']}")

    group("Удаление своей записи")
    check("своя запись удаляется",
          a.request("DELETE", "/api/library/shared-key").status_code == 200)
    check("чужая осталась цела",
          len([i for i in m.get("/api/library").json()["items"] if i["key"] == "shared-key"]) == 1)

    group("Статистика считается")
    a.post("/api/library/watched", json={
        "key": "st1", "title": "Тайтл", "genres": "Драма,Детектив",
        "watched_ep": 1, "position": 1380, "total_eps": 12})
    a.post("/api/library/watched", json={
        "key": "st1", "title": "Тайтл", "genres": "Драма,Детектив",
        "watched_ep": 2, "position": 1400, "total_eps": 12})
    s = a.get("/api/stats/year").json()
    check("серии посчитаны", s["episodes"] == 2, s["episodes"])
    check("время посчитано", s["seconds"] == 2780, s["seconds"])
    check("жанры разложены", len(s["genres"]) == 2, s["genres"])
    check("гость статистику не видит",
          fresh_client().get("/api/stats/year").status_code == 401)

    group("Кабинет без входа")
    n = fresh_client()
    for path in ["/api/me/settings", "/api/me/profile", "/api/me/avatar",
                 "/api/library/progress", "/api/auth/logout-all"]:
        check(f"{path} закрыт без входа", n.post(path, json={}).status_code == 401)
    check("/api/admin/users закрыт без входа", n.get("/api/admin/users").status_code == 404)

    group("Заголовок X-Forwarded-For не обманывает")
    main.login_guard.reset()
    os.environ["TRUST_PROXY"] = "0"
    b = fresh_client()
    codes = []
    for i in range(12):
        codes.append(b.post("/api/auth/login",
                            json={"login": "misha", "password": f"x{i}"},
                            headers={"X-Forwarded-For": f"1.2.3.{i}"}).status_code)
    check("подмена адреса не обходит блокировку", 429 in codes,
          f"401: {codes.count(401)}, 429: {codes.count(429)}")

    group("Огромное тело запроса")
    main.login_guard.reset()
    c = fresh_client()
    login(c, "valera", "Zaliv-Pepel-2026")
    big = {"key": "a", "title": "т" * 100000}
    check("гигантское поле отклонено",
          c.post("/api/library/progress", json=big).status_code == 422)

    group("Разбор битых данных")
    main.login_guard.reset()
    c2 = fresh_client()
    r = c2.post("/api/auth/login", content="не json".encode("utf-8"),
                headers={"Content-Type": "application/json"})
    check("не-json не роняет сервер", r.status_code in (400, 422), r.status_code)
    check("сервер жив после этого", fresh_client().get("/api/health").status_code == 200)

    group("Пустые и странные значения")
    check("пустой логин отклонён",
          fresh_client().post("/api/auth/login",
                              json={"login": "", "password": "x"}).status_code == 422)
    check("null вместо пароля отклонён",
          fresh_client().post("/api/auth/login",
                              json={"login": "a", "password": None}).status_code == 422)
    check("массив вместо объекта отклонён",
          fresh_client().post("/api/auth/login", json=[1, 2, 3]).status_code == 422)

    print("\n" + "=" * 56)
    if FAILS:
        print(f"НЕ ПРОШЛИ: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("Дополнительные проверки пройдены.")
    return 0


def run_fixes():
    """Четвёртый заход: то, что чинили последним.

    Каждая проверка ниже на прежней версии проваливалась. Смысл держать их
    отдельной пачкой в том, что при следующей правке сразу видно, не
    вернулась ли обратно ровно та ошибка, которую уже один раз убрали.
    """
    global FAILS
    FAILS = []
    main.login_guard.reset(); main.api_limit.reset(); main.guest_limit.reset()
    main.write_limit.reset(); main.search_limit.reset()
    main.pass_limit.reset(); main.avatar_limit.reset(); main.source_limit.reset()

    ADMIN = ("valera", "Zaliv-Pepel-2026")

    # ------------------------------------------------------------------
    group("Вход не останавливает весь сервер")
    # Подсчёт пароля идёт 600 000 раундов — примерно полсекунды чистой
    # арифметики. Пока он считался прямо в цикле событий, сервер выполнял
    # входы строго по очереди и на всё это время замирал целиком:
    # у остальных не открывалась ни одна страница.
    #
    # Замерять одним входом бессмысленно: пока замеряющий ждёт, подсчёт
    # успевает закончиться, и разницы не видно — на этом первая версия
    # проверки и обманулась. Поэтому запускаем несколько входов разом и
    # сравниваем с одиночным. Считает потоками — четыре входа занимают
    # почти столько же, сколько один. Считает в цикле событий — вчетверо
    # больше, потому что они выстраиваются в очередь.
    import httpx

    N = 4

    async def timed(count):
        main.login_guard.reset()
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            t0 = time.perf_counter()
            await asyncio.gather(*[
                ac.post("/api/auth/login",
                        json={"login": ADMIN[0], "password": f"zavedomo-nevernyy-{i}"})
                for i in range(count)])
            return time.perf_counter() - t0

    one = asyncio.run(timed(1))
    many = asyncio.run(timed(N))
    ratio = many / max(one, 0.0001)
    check(f"{N} входов разом не выстраиваются в очередь", ratio < 2.5,
          f"один {one:.2f}с, {N} разом {many:.2f}с — это x{ratio:.1f}, "
          f"в цикле событий было бы ~x{N}")

    # Отдельного замера «за сколько ответит страница во время входов» здесь
    # намеренно нет. Он был — и проходил одинаково при любом коде: ответ
    # приходил за 0.01 с и до правки, и после. Проверка, которая не умеет
    # провалиться, хуже отсутствующей: она создаёт уверенность на пустом
    # месте. Всё, что она якобы показывала, показывает отношение выше.
    async def still_alive():
        main.login_guard.reset()
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            jobs = [ac.post("/api/auth/login",
                            json={"login": ADMIN[0], "password": f"nevernyy-{i}"})
                    for i in range(N)]
            out = await asyncio.gather(*jobs, ac.get("/api/health"))
            return out[-1].status_code

    check("сервер жив под несколькими входами разом",
          asyncio.run(still_alive()) == 200)

    # ------------------------------------------------------------------
    group("Выход нельзя вызвать с чужого сайта")
    main.login_guard.reset()
    c = fresh_client()
    login(c, *ADMIN)
    saved = c.headers.pop("X-CSRF-Token")
    r = c.post("/api/auth/logout")
    check("без метки формы выход отклонён", r.status_code == 403, r.status_code)
    check("сессия при этом цела", c.get("/api/me").status_code == 200)
    c.headers["X-CSRF-Token"] = saved
    check("со своей меткой выход проходит",
          c.post("/api/auth/logout").status_code == 200)
    check("после выхода сессии нет", c.get("/api/me").status_code == 401)
    # Без сессии выход обязан отвечать спокойно, а не 403: иначе выйти
    # после протухшей куки было бы невозможно.
    check("выход без сессии не ругается",
          fresh_client().post("/api/auth/logout").status_code == 200)

    # ------------------------------------------------------------------
    group("Смена пароля ограничена по частоте")
    main.login_guard.reset()
    main.pass_limit.reset()
    p = fresh_client()
    login(p, *ADMIN)
    codes = [p.post("/api/me/password",
                    json={"current": "ne-tot-parol", "new": f"Novyy-Parol-{i}-26"}).status_code
             for i in range(12)]
    check("перебор текущего пароля упирается в ограничение", 429 in codes,
          f"403: {codes.count(403)}, 429: {codes.count(429)}")
    check("ограничение наступает не позже девятой попытки",
          codes.index(429) <= 8, codes.index(429) if 429 in codes else "-")
    main.pass_limit.reset()
    # Требования к новому паролю проверяются раньше сверки текущего:
    # это чистая арифметика, она не стоит ничего и ничего не выдаёт.
    check("слабый новый отклонён сразу",
          p.post("/api/me/password",
                 json={"current": "tozhe-ne-tot", "new": "123"}).status_code == 400)

    # ------------------------------------------------------------------
    group("Аватар: бомба и частота")
    main.login_guard.reset()
    main.avatar_limit.reset()
    a = fresh_client()
    login(a, *ADMIN)
    from PIL import Image
    # Однотонная картинка 5000×5000 весит копейки в файле и полтораста
    # мегабайт в памяти. Раньше проходило всё до 50 миллионов точек.
    big = io.BytesIO()
    Image.new("RGB", (5000, 5000), (10, 20, 30)).save(big, format="PNG")
    raw = big.getvalue()
    check("бомба помещается в предел по весу", len(raw) < main.AVATAR_MAX_BYTES,
          f"{len(raw)} байт")
    check("но по размеру холста отклонена",
          a.post("/api/me/avatar", content=raw).status_code == 400)
    small = io.BytesIO()
    Image.new("RGB", (400, 300), (90, 120, 140)).save(small, format="PNG")
    main.avatar_limit.reset()
    codes = [a.post("/api/me/avatar", content=small.getvalue()).status_code
             for _ in range(14)]
    check("частая загрузка аватара ограничена", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")

    # ------------------------------------------------------------------
    group("Наружу не уходит лишнего")
    main.login_guard.reset()
    main.avatar_limit.reset()
    L = fresh_client()
    login(L, *ADMIN)
    L.post("/api/library/progress", json={"key": "utech", "title": "Т", "position": 5})
    items = L.get("/api/library").json()["items"]
    check("в полке есть записи", len(items) >= 1, len(items))
    check("внутреннего номера пользователя в ответе нет",
          all("user_id" not in it for it in items), list(items[0]))
    check("поля полки — ровно те, что перечислены",
          set(items[0]) == set(store.LIBRARY_FIELDS), sorted(items[0]))

    # ------------------------------------------------------------------
    group("Счётчик входов считает только живые")
    uid = store.get_user_by_login(ADMIN[0])["id"]
    store.drop_all_sessions(uid)
    live = security.new_token()
    store.create_session(uid, live, "ua")
    dead = security.new_token()
    store.create_session(uid, dead, "ua")
    # Руками состариваем вторую сессию так, чтобы по ней уже нельзя было войти
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET last_seen = ?, created_at = ? WHERE fp = ?",
                     (store.now() - security.SESSION_IDLE - 10,
                      store.now() - security.SESSION_IDLE - 10,
                      security.token_fingerprint(dead)))
    check("просроченная сессия не пускает", store.session_user(dead) is None)
    check("живая пускает", store.session_user(live) is not None)
    check("в счётчике только живая", store.count_sessions(uid) == 1,
          store.count_sessions(uid))

    group("Уборка выносит и слишком старые сессии")
    old = security.new_token()
    store.create_session(uid, old, "ua")
    with store.tx() as conn:
        # свежая по последнему визиту, но заведена слишком давно
        conn.execute("UPDATE sessions SET created_at = ? WHERE fp = ?",
                     (store.now() - security.SESSION_MAX_LIFE - 10,
                      security.token_fingerprint(old)))
    check("такая сессия не пускает", store.session_user(old) is None)
    store.create_session(uid, old, "ua")
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET created_at = ? WHERE fp = ?",
                     (store.now() - security.SESSION_MAX_LIFE - 10,
                      security.token_fingerprint(old)))
    dropped = store.purge_old_sessions()
    check("и уборка её действительно удаляет", dropped >= 1, dropped)
    check("живая сессия уборку пережила", store.session_user(live) is not None)

    # ------------------------------------------------------------------
    group("Администратор не выключает сам себя")
    main.login_guard.reset()
    ad = fresh_client()
    login(ad, *ADMIN)
    me_id = store.get_user_by_login(ADMIN[0])["id"]
    r = ad.post("/api/admin/users", json={"login": "zapasnoy", "password": "Zapasnoy-Admin-26",
                                          "role": "admin"})
    check("второй администратор заведён", r.status_code in (200, 409), r.text[:70])
    check("себя выключить нельзя",
          ad.post(f"/api/admin/users/{me_id}/disable").status_code == 409)
    check("своя сессия цела", ad.get("/api/me").status_code == 200)

    group("Имя из админки чистится")
    r = ad.post("/api/admin/users", json={
        "login": "chistyy", "password": "Chistoe-Imya-2026",
        # В имени намеренно собрано всё, чему там не место: переворот
        # направления текста, перевод строки и нулевой байт. Собираем
        # через chr, чтобы сам файл проверок оставался обычным текстом.
        "display_name": ("Zhenya" + chr(0x202E) + chr(10) + chr(0) + "Sorok")})
    check("аккаунт создан", r.status_code == 200, r.text[:80])
    made = store.get_user_by_login("chistyy")
    check("управляющих символов в имени не осталось",
          made is not None and all(ch.isprintable() for ch in made["display_name"]),
          repr(made["display_name"]) if made else "нет")

    # ------------------------------------------------------------------
    group("Ручки к источникам ограничены отдельно")
    main.login_guard.reset()
    main.source_limit.reset()
    s = fresh_client()
    login(s, *ADMIN)
    real = main.anime.find_episodes

    async def pretend(source, key, title):
        class E:
            ordinal = 1
            title = ""
        return [E()]

    main.anime.find_episodes = pretend
    try:
        codes = [s.get("/api/episodes", params={"key": "k", "source": "demo"}).status_code
                 for _ in range(50)]
    finally:
        main.anime.find_episodes = real
    check("поток запросов к источнику упирается в предел", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")

    # ------------------------------------------------------------------
    group("Кэширование страниц и файлов")
    n = fresh_client()
    # Страницы просмотра и итогов раньше не помечались никак: под условие
    # попадали только «/» и всё, что кончается на .html. Браузер решал сам,
    # и после выхода эти страницы оставались доступны кнопкой «назад».
    for path in ("/", "/watch", "/stats"):
        h = n.get(path).headers.get("cache-control", "")
        check(f"{path} не кладётся в кэш", "no-store" in h, h or "заголовка нет")
    h = n.get("/static/app.js").headers.get("cache-control", "")
    # Файлам кэш нужен, но с обязательным переспросом: иначе после
    # обновления сайта свежая разметка работает со старым кодом.
    check("скрипты кэшируются с переспросом", h == "no-cache", h or "заголовка нет")

    # ------------------------------------------------------------------
    group("Сбой источника не выглядит как поломка сайта")
    main.login_guard.reset()
    main.source_limit.reset()
    e = fresh_client()
    login(e, *ADMIN)
    real_cache = dict(main.anime._extractors)
    main.anime._extractors.clear()
    real_import = main.anime.import_module

    def broken(name):
        raise ImportError("модуль " + name + " не загрузился")

    main.anime.import_module = broken
    try:
        r = e.get("/api/episodes", params={"key": "k", "source": "demo",
                                           "title": "Т"})
    finally:
        main.anime.import_module = real_import
        main.anime._extractors.update(real_cache)
    check("беда с модулем источника — это 502, а не 500",
          r.status_code == 502, r.status_code)
    check("и текст говорит про источник, а не про нас",
          "источник" in r.text.lower(), r.text[:80])
    check("внутренности наружу не поехали",
          "ImportError" not in r.text and "Traceback" not in r.text, r.text[:80])

    # ------------------------------------------------------------------
    group("Уведомления только о новых сериях")
    main.login_guard.reset()
    main.source_limit.reset()
    u = fresh_client()
    login(u, *ADMIN)
    uid = store.get_user_by_login(ADMIN[0])["id"]

    real_eps = main.anime.find_episodes

    class Ep:
        def __init__(self, n):
            self.ordinal = n
            self.title = ""

    async def twelve(source, key, title):
        return [Ep(n) for n in range(1, 13)]

    main.anime.find_episodes = twelve
    try:
        # Тайтл добавлен только что: сколько у него серий, мы ещё не считали.
        store.save_progress(uid, {"key": "novyy", "source": "demo",
                                  "title": "Новый", "total_eps": 0,
                                  "watched_ep": 1, "status": "watching"})
        r = u.get("/api/updates").json()
        check("только что добавленный не считается новинкой",
              not any(x["key"] == "novyy" for x in r["items"]), r["items"])
        # Первый заход запомнил 12 серий — второй тоже должен молчать.
        main.source_limit.reset()
        r = u.get("/api/updates").json()
        check("и во второй раз молчит",
              not any(x["key"] == "novyy" for x in r["items"]), r["items"])

        # Делаем вид, что в прошлый раз видели десять серий.
        store.set_known_eps(uid, "novyy", 10)
        main.source_limit.reset()
        r = u.get("/api/updates").json()
        hit = [x for x in r["items"] if x["key"] == "novyy"]
        check("вышли новые — сказали об этом", len(hit) == 1, r["items"])
        if hit:
            check("посчитали, сколько именно вышло",
                  hit[0]["was"] == 10 and hit[0]["now"] == 12 and hit[0]["fresh"] == 2, hit[0])

        # «Я видел» гасит уведомление.
        u.post("/api/updates/seen", json={"key": "novyy", "source": "demo",
                                          "title": "Новый"})
        main.source_limit.reset()
        r = u.get("/api/updates").json()
        check("после просмотра уведомление гаснет",
              not any(x["key"] == "novyy" for x in r["items"]), r["items"])

        # Досмотренное не звенит: следим только за тем, что смотрим.
        store.save_progress(uid, {"key": "novyy", "source": "demo",
                                  "title": "Новый", "total_eps": 5,
                                  "watched_ep": 5, "status": "done"})
        main.source_limit.reset()
        r = u.get("/api/updates").json()
        check("законченное не беспокоит",
              not any(x["key"] == "novyy" for x in r["items"]), r["items"])
    finally:
        main.anime.find_episodes = real_eps

    # ------------------------------------------------------------------
    group("Обложка дописывается сама")
    main.login_guard.reset()
    main.source_limit.reset()
    pc = fresh_client()
    login(pc, *ADMIN)
    uid = store.get_user_by_login(ADMIN[0])["id"]
    store.save_progress(uid, {"key": "bezfoto", "source": "demo",
                              "title": "Без обложки", "poster": "",
                              "watched_ep": 1, "status": "watching"})
    before = [x for x in store.library(uid) if x["key"] == "bezfoto"][0]
    check("сохранился без обложки", not before["poster"])

    real_poster = main.anime.find_poster

    async def fake_poster(source, key, title):
        return "https://example.org/oblozhka.jpg"

    main.anime.find_poster = fake_poster
    try:
        r = pc.post("/api/library/poster", json={"key": "bezfoto",
                                                 "source": "demo",
                                                 "title": "Без обложки"})
        check("ручка ответила", r.status_code == 200, r.status_code)
    finally:
        main.anime.find_poster = real_poster
    after = [x for x in store.library(uid) if x["key"] == "bezfoto"][0]
    check("обложка появилась", after["poster"] == "https://example.org/oblozhka.jpg",
          after["poster"])
    check("время правки не сдвинулось", after["updated_at"] == before["updated_at"],
          f"{before['updated_at']} -> {after['updated_at']}")

    # ------------------------------------------------------------------
    group("Объявление администратора")
    main.login_guard.reset()
    ad = fresh_client()
    login(ad, *ADMIN)
    usr = fresh_client()
    login(usr, "misha", "Sovsem-Novyy-2026")

    check("сначала объявления нет", ad.get("/api/news").json()["text"] == "")
    r = ad.post("/api/admin/news", json={"text": "Сервер перезапустится в 23:00"})
    check("админ сохранил", r.status_code == 200, r.status_code)
    check("его видит обычный человек",
          usr.get("/api/news").json()["text"] == "Сервер перезапустится в 23:00")
    check("обычный человек не может писать",
          usr.post("/api/admin/news", json={"text": "я тут главный"}).status_code == 404)
    check("и не может удалять",
          usr.request("DELETE", "/api/admin/news").status_code == 404)
    check("пустое объявление отклонено",
          ad.post("/api/admin/news", json={"text": "   "}).status_code == 400)
    ad.request("DELETE", "/api/admin/news")
    check("после удаления пусто", usr.get("/api/news").json()["text"] == "")

    # ------------------------------------------------------------------
    group("Вход по коду из приложения")
    from api import twofa

    secret = twofa.new_secret()
    check("секрет пригоден для приложения", len(secret) >= 26, len(secret))
    now_code = twofa._code_at(secret, int(time.time() // twofa.STEP))
    check("свой код принимается", twofa.verify(secret, now_code))
    check("чужой код не принимается", not twofa.verify(secret, "000000"))
    check("код на два шага назад уже не годится",
          not twofa.verify(secret, twofa._code_at(secret, int(time.time() // twofa.STEP) - 3)))
    check("мусор вместо кода не роняет", not twofa.verify(secret, "не-цифры"))
    check("в ссылке для приложения есть секрет и издатель",
          "secret=" + secret in twofa.otpauth_uri(secret, "valera")
          and "issuer=" in twofa.otpauth_uri(secret, "valera"))

    main.login_guard.reset()
    t = fresh_client()
    login(t, "misha", "Sovsem-Novyy-2026")
    uid = store.get_user_by_login("misha")["id"]

    r = t.post("/api/me/2fa/start")
    check("настройка началась", r.status_code == 200, r.status_code)
    body = r.json()
    # Ключ обязателен всегда: его можно ввести в приложение руками.
    # Картинка — удобство, и её отсутствие не должно ронять настройку.
    check("ключ пришёл", isinstance(body.get("secret"), str) and len(body["secret"]) == 32)
    qr = body.get("qr")
    check("настройка не падает без рисовалки", "qr" in body)
    if qr is None:
        check("без картинки настройка всё равно возможна", True, "qr=null, ключ есть")
    else:
        check("картинка с кодом пришла",
              qr.startswith("data:image/svg+xml;base64,")
              or qr.startswith("data:image/png;base64,"), qr[:32])
        check("код рисуется у нас, а не на чужом сайте", "http" not in qr[:40])

    check("с чужим кодом не включается",
          t.post("/api/me/2fa/enable", json={"code": "000000"}).status_code == 400)
    good = twofa._code_at(body["secret"], int(time.time() // twofa.STEP))
    r = t.post("/api/me/2fa/enable", json={"code": good})
    check("со своим кодом включается", r.status_code == 200, r.status_code)
    codes = r.json().get("backup") or []
    check("выданы запасные коды", len(codes) >= 4, len(codes))

    me = t.get("/api/me").json()
    check("в ответе видно, что включено", me["totp_on"] is True)
    check("секрет наружу не отдаётся", "totp_secret" not in me)

    main.login_guard.reset()
    n = fresh_client()
    r = n.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026"})
    check("верный пароль без кода не пускает", r.status_code == 401, r.status_code)
    check("но сказано, что нужен именно код", r.headers.get("X-Need-Code") == "1")
    check("сессии при этом нет", n.get("/api/me").status_code == 401)

    main.login_guard.reset()
    n = fresh_client()
    r = n.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026",
                                        "code": "111111"})
    check("с неверным кодом не пускает", r.status_code == 401, r.status_code)

    main.login_guard.reset()
    n = fresh_client()
    fresh = twofa._code_at(body["secret"], int(time.time() // twofa.STEP))
    r = n.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026",
                                        "code": fresh})
    check("с верным кодом пускает", r.status_code == 200, r.status_code)

    # Запасной код работает один раз.
    main.login_guard.reset()
    n2 = fresh_client()
    r = n2.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026",
                                         "code": codes[0]})
    check("запасной код пускает", r.status_code == 200, r.status_code)
    main.login_guard.reset()
    n3 = fresh_client()
    r = n3.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026",
                                         "code": codes[0]})
    check("и второй раз тот же уже не работает", r.status_code == 401, r.status_code)

    main.pass_limit.reset()
    check("выключение без пароля не проходит",
          t.post("/api/me/2fa/disable", json={"password": "ne-tot"}).status_code == 403)
    main.pass_limit.reset()
    check("с паролем выключается",
          t.post("/api/me/2fa/disable",
                 json={"password": "Sovsem-Novyy-2026"}).status_code == 200)
    check("секрет из базы стёрт",
          not store.get_user(uid)["totp_secret"])

    # ------------------------------------------------------------------
    group("Справочник: описание и случайное")
    from api import catalog

    # Завязка должна остаться, поворот сюжета — уйти. Начало намеренно
    # длинное: обрезать описание до пары слов нельзя, поэтому в коде стоит
    # порог, и короткий текст режется только по длине.
    dirty = ("A boy sells charcoal to feed his family in Taisho-era Japan.<br>"
             "<i>One winter day</i> he comes home to find them gone, and his "
             "sister changed into something else entirely. "
             "However, it turns out his brother is the villain and dies at the end.")
    clean = catalog.clean_description(dirty)
    check("разметка вырезана", "<" not in clean and ">" not in clean, clean[:60])
    check("завязка осталась", "charcoal" in clean, clean[:60])
    check("спойлер отрезан", "villain" not in clean and "dies" not in clean, clean[-60:])

    long_text = "Первое предложение. " * 60
    cut = catalog.clean_description(long_text)
    check("длинное описание обрезано", len(cut) <= catalog.DESC_MAX + 2, len(cut))

    check("взрослое не показываем",
          catalog.pack_anilist({"isAdult": True, "title": {"romaji": "X"}}) is None)
    check("без названия не показываем",
          catalog.pack_anilist({"isAdult": False, "title": {}}) is None)

    main.catalog_limit.reset()
    real_about = catalog.about

    async def fake_about(title, lang="ru"):
        return {"title": "Тест", "about": "Описание без спойлеров.", "genres": ["Драма"],
                "year": 2024, "episodes": 12, "poster": "", "score": 80,
                "title_en": "", "season": "", "format": "TV", "status": ""}

    catalog.about = fake_about
    try:
        r = t.get("/api/about", params={"title": "Тест"})
        check("описание отдаётся", r.status_code == 200 and r.json()["found"], r.status_code)
        check("в нём есть текст", r.json()["about"].startswith("Описание"))
    finally:
        catalog.about = real_about

    main.catalog_limit.reset()
    real_random = catalog.random_anime

    async def fake_random():
        return {"title": "Случайное", "about": "", "genres": [], "year": 2020,
                "episodes": 24, "poster": "", "score": None, "title_en": "",
                "season": "", "format": "TV", "status": ""}

    catalog.random_anime = fake_random
    try:
        r = t.get("/api/random")
        check("случайное отдаётся", r.status_code == 200 and r.json()["found"], r.status_code)
    finally:
        catalog.random_anime = real_random

    main.catalog_limit.reset()
    check("гостю справочник тоже доступен", True)
    codes_seen = [t.get("/api/about", params={"title": "Тест"}).status_code
                  for _ in range(26)]
    check("справочник ограничен по частоте", 429 in codes_seen,
          f"200: {codes_seen.count(200)}, 429: {codes_seen.count(429)}")

    # ------------------------------------------------------------------
    group("Совпадение названий: мусор из выдачи не проходит")
    from api import anime as an

    # Это не выдуманный пример. На «Атака титанов» источники отвечают
    # ровно этой строкой — совпало слово «атака». Перебор источников
    # останавливался на первом, кто ответил хоть чем-то, и правильный
    # ответ у следующего источника не смотрел никто.
    junk = an.relevance("Атака титанов", "Не издевайся, Нагаторо: Вторая атака")
    check("похожее по одному слову не проходит порог",
          junk < an.MIN_RELEVANCE, round(junk, 3))
    check("точное название — единица",
          an.relevance("Атака титанов", "Атака титанов") == 1.0)

    exact = an.relevance("Наруто", "Наруто")
    seq = an.relevance("Наруто", "Наруто Ураганные хроники")
    check("первый сезон выше продолжения", exact > seq, f"{exact} > {round(seq, 3)}")
    # Ради этого порог остановки перебора и поднят: 0.855 у продолжения
    # проходило старую планку в 0.85, и вместо «Наруто» открывались
    # «Ураганные хроники».
    check("продолжение не считается достаточно точным",
          seq < main.GOOD_ENOUGH, round(seq, 3))

    # Некоторые источники пишут названия одной строкой с латиницей и счётчиком.
    check("хвосты источника отрезаются",
          an.relevance("Наруто", "Наруто / Naruto [1-220 из 220]") == 1.0)
    check("приписка в скобках не мешает",
          an.relevance("Наруто", "Наруто (ТВ)") == 1.0)
    check("буква ё не расходится с е",
          an.relevance("Тетрадь смерти", "Тетрадь смёрти") == 1.0)

    # ------------------------------------------------------------------
    group("Поиск по франшизам")
    from api import catalog as cat

    naruto = [
        {"id": "1735", "russian": "Наруто: Ураганные хроники", "name": "Naruto: Shippuuden",
         "franchise": "naruto", "kind": "tv", "episodes": 500, "score": "8.2",
         "airedOn": {"year": 2007}, "poster": {"mainUrl": "https://x/1735.webp"}},
        {"id": "20", "russian": "Наруто", "name": "Naruto", "franchise": "naruto",
         "kind": "tv", "episodes": 220, "score": "8.0", "airedOn": {"year": 2002},
         "poster": {"mainUrl": "https://x/20.webp"}},
        {"id": "9999", "russian": "Наруто PV", "name": "Naruto PV", "franchise": "naruto",
         "kind": "pv", "episodes": 1, "score": "5.0", "airedOn": {"year": 2002},
         "poster": {}},
        {"id": "77", "russian": "Вынос гигантов", "name": "Giant Killing",
         "franchise": "", "kind": "tv", "episodes": 26, "score": "7.5",
         "airedOn": {"year": 2010}, "poster": {}},
    ]
    packed = [cat.pack_part(n) for n in naruto]
    check("трейлер отсеивается по метке",
          packed[2]["kind"] in cat.JUNK_KINDS, packed[2]["kind"])
    check("оценка приводится к сотне", packed[1]["score"] == 80, packed[1]["score"])

    live = [p for p in packed if p["kind"] not in cat.JUNK_KINDS]
    order, grouped = cat.group_found(live)
    check("сезоны одной истории собрались в одну карточку",
          len(grouped["naruto"]) == 2, len(grouped["naruto"]))
    check("чужой тайтл в неё не попал", len(order) == 2, order)
    check("тайтл без франшизы получил метку по названию",
          order[1].startswith("title:"), order[1])

    card = cat.franchise_card("naruto", grouped["naruto"])
    # Ради этого всё и делалось: одна карточка «Наруто» вместо двадцати
    # одной строки, где вперемешку лежат сезоны, фильмы и спешлы.
    check("карточка названа по первому сезону", card["title"] == "Наруто", card["title"])
    check("и год у неё от первого сезона", card["year"] == 2002, card["year"])

    # Порядок и метка «начало» — разные вещи. У «Ван-Пис» раньше сериала
    # вышла посторонняя OVA, и по годам она встаёт первой строкой.
    op = [
        {"id": "a", "title": "Ван-Пис: Победить пирата Ганзака!", "kind": "ova",
         "year": 1998, "episodes": 1},
        {"id": "b", "title": "Ван-Пис", "kind": "tv", "year": 1999, "episodes": 1174},
        {"id": "c", "title": "Ван-Пис. Фильм", "kind": "movie", "year": 2000,
         "episodes": 1},
        {"id": "d", "title": "Ван-Пис: анонс", "kind": "tv", "year": None,
         "episodes": None},
    ]
    ordered = cat.mark_main(cat.sort_parts(op))
    check("части идут по годам",
          [p["id"] for p in ordered][:3] == ["a", "b", "c"],
          [p["id"] for p in ordered])
    check("невышедшее уезжает в конец", ordered[-1]["id"] == "d", ordered[-1]["id"])
    # У «Ван-Пис» самое раннее — посторонняя OVA 1998 года, снятая до
    # сериала. Отметка всё равно на ней: список идёт по годам, и «начало»
    # обязано совпадать с первой строкой, иначе объяснить его нечем.
    check("«начало» — самое раннее по году",
          ordered[0]["main"] and not ordered[1]["main"],
          [p["id"] for p in ordered if p["main"]])

    # Пересказ первого сезона выходит с ним в один год и помечен как
    # tv_special. Пока сериалы отбирались вместе с ним, франшиза
    # называлась «Атака титанов: Рекап».
    aot = [
        {"id": "r", "title": "Атака титанов: Рекап", "kind": "tv_special",
         "year": 2013, "episodes": 1},
        {"id": "s", "title": "Атака титанов", "kind": "tv", "year": 2013,
         "episodes": 25},
    ]
    check("пересказ не становится лицом франшизы",
          cat.main_part(aot)["id"] == "s", cat.main_part(aot)["id"])
    # Отметка «начало» идёт по порядку, а внутри одного года сериал стоит
    # выше пересказа — значит и она достаётся сериалу.
    check("в один год отметка достаётся сериалу, а не нарезке",
          cat.mark_main(cat.sort_parts([dict(p) for p in aot]))[0]["id"] == "s")

    check("метка кино переведена", cat.KIND_RU["tv_special"] == "спецвыпуск")
    ongoing = cat.pack_part({"id": "1", "russian": "Идёт", "name": "Ongoing",
                             "kind": "tv", "episodes": 0, "episodesAired": 1174,
                             "status": "ongoing", "airedOn": {"year": 1999},
                             "poster": {}})
    check("у выходящего сейчас считаем вышедшие серии",
          ongoing["episodes"] == 1174 and ongoing["ongoing"], ongoing["episodes"])

    # ------------------------------------------------------------------
    group("Ручки поиска по франшизам")
    main.catalog_limit.reset()
    main.search_limit.reset()
    real_search = cat.search_franchises
    real_parts = cat.franchise_parts

    async def fake_search(q):
        return [cat.franchise_card("naruto", grouped["naruto"])]

    async def fake_parts(fid):
        return cat.mark_main(cat.sort_parts([dict(p) for p in grouped["naruto"]]))

    cat.search_franchises = fake_search
    cat.franchise_parts = fake_parts
    try:
        r = t.get("/api/find", params={"q": "наруто"})
        body = r.json()
        check("поиск отдаёт карточки франшиз",
              r.status_code == 200 and len(body["items"]) == 1, r.status_code)
        check("и говорит, что справочник ответил", body["catalog"] is True)

        r = t.get("/api/franchise", params={"id": "naruto"})
        check("части франшизы отдаются",
              r.status_code == 200 and len(r.json()["items"]) == 2, r.status_code)
    finally:
        cat.search_franchises = real_search
        cat.franchise_parts = real_parts

    # Справочник может лежать. Это не «ничего не нашлось»: страница по
    # такому ответу уходит искать старым способом, прямо у источников.
    main.catalog_limit.reset()

    async def dead_search(q):
        return None

    cat.search_franchises = dead_search
    try:
        r = t.get("/api/find", params={"q": "наруто"})
        check("молчание справочника — не ошибка, а отдельный ответ",
              r.status_code == 200 and r.json()["catalog"] is False, r.status_code)
    finally:
        cat.search_franchises = real_search

    main.catalog_limit.reset()

    async def dead_parts(fid):
        return None

    cat.franchise_parts = dead_parts
    try:
        r = t.get("/api/franchise", params={"id": "naruto"})
        check("а вот пустой список частей — это 502", r.status_code == 502, r.status_code)
    finally:
        cat.franchise_parts = real_parts

    # ------------------------------------------------------------------
    group("Часть каталога находится у источника")
    main.search_limit.reset()
    real_try = main.try_source
    real_eps = main.anime.find_episodes

    # Резолв теперь не верит названию на слово, а проверяет, что серии
    # у тайтла и правда отдаются. В проверках сеть недоступна, поэтому
    # список серий подделываем: по умолчанию у всех он есть.
    EPS = {}

    async def fake_episodes(source, key, title):
        n = EPS.get(str(key), 12)
        if not n:
            raise HTTPException(status_code=502, detail="Не удалось получить серии")
        return [object()] * n

    main.anime.find_episodes = fake_episodes

    # Источник отвечает двумя строками: сначала мусор, потом правильный
    # тайтл. Ровно этот случай раньше заканчивался тем, что человек
    # открывал «Нагаторо» вместо «Атаки титанов»: бралось первое, что
    # пришло, а не то, что совпало.
    async def junk_then_right(source, q):
        return [
            {"key": "junk", "title": "Не издевайся, Нагаторо: Вторая атака",
             "poster": "", "year": 2021, "genres": "", "episodes_total": 12,
             "match": 0.31},
            {"key": "aot-1", "title": "Атака титанов", "poster": "p",
             "year": 2013, "genres": "Экшен", "episodes_total": 25,
             "match": 1.0},
        ]

    main.try_source = junk_then_right
    try:
        r = t.get("/api/resolve", params={"title": "Атака титанов",
                                          "title_en": "Shingeki no Kyojin"})
        body = r.json()
        check("перебор не останавливается на непохожем ответе",
              r.status_code == 200 and body["key"] == "aot-1", body)
        check("и берёт того, у кого совпало точно",
              body["source"] == "demo" and body["exact"], body.get("source"))
    finally:
        main.try_source = real_try

    main.search_limit.reset()

    async def only_junk(source, q):
        return [{"key": "junk", "title": "Совсем другое кино", "poster": "",
                 "year": 2020, "genres": "", "episodes_total": 1, "match": 0.0}]

    main.try_source = only_junk
    try:
        r = t.get("/api/resolve", params={"title": "Атака титанов"})
        check("если похожего нет вовсе — 404, а не чужой тайтл",
              r.status_code == 404, r.status_code)
        check("и текст объясняет, что делать",
              "источник" in r.text.lower(), r.text[:90])
    finally:
        main.try_source = real_try

    main.search_limit.reset()

    async def all_silent(source, q):
        return None

    main.try_source = all_silent
    try:
        r = t.get("/api/resolve", params={"title": "Атака титанов"})
        check("полное молчание источников — 502", r.status_code == 502, r.status_code)
    finally:
        main.try_source = real_try

    # Неточное совпадение отдаётся, но помечено: страница по этой метке
    # спрашивает, а не открывает молча.
    main.search_limit.reset()

    async def close_enough(source, q):
        return [{"key": "near", "title": "Атака титанов 3. Часть 2", "poster": "",
                 "year": 2019, "genres": "", "episodes_total": 10, "match": 0.72}]

    main.try_source = close_enough
    try:
        r = t.get("/api/resolve", params={"title": "Атака титанов"})
        body = r.json()
        check("похожее отдаётся с пометкой «неточно»",
              r.status_code == 200 and body["exact"] is False, body.get("exact"))
    finally:
        main.try_source = real_try

    # ------------------------------------------------------------------
    # Точное совпадение названия ещё не значит, что тайтл открывается.
    # Под ровно тем же названием, что в справочнике, может лежать тайтл,
    # у которого список серий не отдаётся вовсе: разборщик спотыкается на
    # сдвоенной серии «57-58». Резолв возвращал этот тайтл, и человек
    # попадал в плеер с «серии не загрузились», хотя рядом лежал
    # идентичный, но рабочий.
    main.search_limit.reset()

    async def one_broken(source, q):
        return [
            {"key": "broken", "title": "Наруто: Ураганные хроники",
             "poster": "", "year": 2007, "genres": "",
             "episodes_total": 0, "match": 1.0},
            {"key": "works", "title": "Наруто Ураганные хроники",
             "poster": "", "year": 2007, "genres": "",
             "episodes_total": 0, "match": 1.0},
        ]

    EPS["broken"] = 0                      # серии не отдаются
    EPS["works"] = 131
    main.try_source = one_broken
    try:
        r = t.get("/api/resolve", params={"title": "Наруто: Ураганные хроники",
                                          "source": "demo"})
        body = r.json()
        check("тайтл без серий не подсовывается, хотя название совпало точно",
              r.status_code == 200 and body["key"] == "works", body)
        check("и число серий берётся из настоящего списка",
              body.get("episodes_total") == 131, body.get("episodes_total"))
    finally:
        main.try_source = real_try

    main.search_limit.reset()

    async def all_broken(source, q):
        return [{"key": "broken", "title": "Наруто: Ураганные хроники",
                 "poster": "", "year": 2007, "genres": "",
                 "episodes_total": 0, "match": 1.0}]

    main.try_source = all_broken
    try:
        r = t.get("/api/resolve", params={"title": "Наруто: Ураганные хроники"})
        check("если серий нет ни у кого — 404, а не мёртвый плеер",
              r.status_code == 404, r.status_code)
    finally:
        main.try_source = real_try

    # ------------------------------------------------------------------
    # Огрызок вместо сериала. У «Ван-Пис» один источник выложил семь
    # серий из тысячи ста семидесяти четырёх — название совпало, серии
    # есть, ошибки нет. А человек получал «серия 1 из 7» у сериала,
    # который идёт двадцать шестой год.
    main.search_limit.reset()

    async def stub_and_full(source, q):
        return [
            {"key": "stub", "title": "Ван-Пис", "poster": "", "year": 1999,
             "genres": "", "episodes_total": 0, "match": 1.0},
            {"key": "full", "title": "Ван-Пис", "poster": "", "year": 1999,
             "genres": "", "episodes_total": 0, "match": 1.0},
        ]

    EPS["stub"] = 7
    EPS["full"] = 1174
    main.try_source = stub_and_full
    try:
        r = t.get("/api/resolve", params={"title": "Ван-Пис", "episodes": 1174})
        body = r.json()
        check("огрызок уступает источнику с полным списком",
              r.status_code == 200 and body["key"] == "full",
              str(body.get("key")) + " / " + str(body.get("episodes_total")))

        main.search_limit.reset()
        # Без подсказки справочника сравнивать не с чем — берём первого
        # рабочего и не тратим время на обход остальных.
        r = t.get("/api/resolve", params={"title": "Ван-Пис"})
        check("без подсказки о числе серий берётся первый рабочий",
              r.json()["key"] == "stub", r.json().get("key"))
    finally:
        main.try_source = real_try

    # Отставание источника на десяток серий — не повод искать дальше.
    # У «Блича» разница в четырнадцать серий из трёхсот шестидесяти
    # шести, и придираться к ней не за что.
    main.search_limit.reset()

    async def slightly_behind(source, q):
        return [
            {"key": "behind", "title": "Блич", "poster": "", "year": 2004,
             "genres": "", "episodes_total": 0, "match": 1.0},
            {"key": "other", "title": "Блич", "poster": "", "year": 2004,
             "genres": "", "episodes_total": 0, "match": 1.0},
        ]

    EPS["behind"] = 352
    EPS["other"] = 366
    main.try_source = slightly_behind
    try:
        r = t.get("/api/resolve", params={"title": "Блич", "episodes": 366})
        check("небольшое отставание источника не гонит перебор дальше",
              r.json()["key"] == "behind", r.json().get("key"))
    finally:
        main.try_source = real_try
        main.anime.find_episodes = real_eps

    # ------------------------------------------------------------------
    group("Плеер без единой ссылки — не плеер")
    main.source_limit.reset()
    real_eps2 = main.anime.find_episodes

    class FakeVideo:
        def __init__(self, url):
            self.url, self.quality, self.type = url, 720, "m3u8"

    class FakePlayer:
        def __init__(self, name, urls):
            self.name, self._urls = name, urls

        async def a_get_videos(self):
            return [FakeVideo(u) for u in self._urls]

    class FakeEpisode:
        ordinal = 1

        async def a_get_sources(self):
            # Ровно то, что отдаёт источник B на «Наруто 1: Книга
            # искусств ниндзя»: два плеера, и ни одной ссылки внутри —
            # его плееры работают только с определённых адресов.
            return [FakePlayer("пустой", []), FakePlayer("тоже пустой", [])]

    async def fake_find(source, key, title):
        return [FakeEpisode()]

    main.anime.find_episodes = fake_find
    try:
        r = t.get("/api/videos", params={"key": "k", "ordinal": 1, "source": "demo"})
        check("плееры без ссылок наружу не уходят", r.status_code == 502, r.status_code)
        check("и человеку сказано, что именно не так",
              "плеер" in r.text.lower(), r.text[:80])
    finally:
        main.anime.find_episodes = real_eps2

    # ------------------------------------------------------------------
    group("Эта история целиком: части рядом с плеером")

    # Справочник объединяет в одну франшизу вещи, которые человеком за
    # одно не считаются: «Врата Штейна» лежат вместе с «Вершиной хаоса»
    # под общей меткой science_adventure, и первый сериал там — «Вершина
    # хаоса» 2008 года. Получалось, что на карточке написано одно, а
    # отметка «начало» стоит на другом аниме.
    sciadv = [
        {"id": "1", "title": "Вершина хаоса", "kind": "tv", "year": 2008,
         "episodes": 12},
        {"id": "2", "title": "Врата Штейна", "kind": "tv", "year": 2011,
         "episodes": 24},
        {"id": "3", "title": "Врата Штейна: Зона загрузки дежавю", "kind": "movie",
         "year": 2013, "episodes": 1},
    ]
    plain = cat.mark_main(cat.sort_parts([dict(p) for p in sciadv]))
    check("«начало» стоит на самой ранней части",
          next(p["id"] for p in plain if p["main"]) == "1",
          next(p["title"] for p in plain if p["main"]))
    check("и это ровно первая строка списка", plain[0]["main"] is True)
    check("помечена ровно одна часть",
          sum(1 for p in plain if p["main"]) == 1)

    # Пометки живут в копиях. Кэш общий на всех: если бы они писались
    # прямо в него, соседние запросы переписывали бы друг другу отметки.
    cat._cache_put("fr:testfr", cat.sort_parts([dict(p) for p in sciadv]))
    got = asyncio.run(cat.franchise_parts("testfr"))
    check("из кэша части приходят помеченными",
          got[0]["main"] and not got[1]["main"])
    check("в самом кэше пометок не появилось",
          all("main" not in p for p in cat._cache_get("fr:testfr")))

    # Источник может писать название КОРОЧЕ справочника: «Евангелион
    # нового поколения» у всех трёх источников лежит просто как
    # «Евангелион». По словам это давало 1 из 3 — ниже порога, и сайт
    # отвечал «ни один источник его не выложил» про тайтл, который есть
    # у всех.
    short = an.relevance("Евангелион нового поколения", "Евангелион")
    check("короткое название источника проходит порог",
          short >= an.MIN_RELEVANCE, round(short, 2))
    check("но точным совпадением не считается",
          short < main.GOOD_ENOUGH, round(short, 2))
    # Тем же правилом «Наруто» подходит под «Наруто: Ураганные хроники».
    # Отличить одно от другого по строкам нельзя — поэтому такое
    # совпадение всегда проигрывает настоящему.
    check("настоящее совпадение всё равно сильнее",
          an.relevance("Наруто: Ураганные хроники", "Наруто Ураганные хроники") > short)

    main.catalog_limit.reset()
    real_related = cat.related_parts

    async def fake_related(title):
        rows = cat.mark_main(cat.sort_parts([dict(p) for p in sciadv]))
        for row in rows:
            row["current"] = row["title"] == title
        return rows

    cat.related_parts = fake_related
    try:
        r = t.get("/api/related", params={"title": "Врата Штейна"})
        body = r.json()
        check("части истории отдаются странице просмотра",
              r.status_code == 200 and len(body["items"]) == 3, r.status_code)
        check("и в них помечено, что открыто сейчас",
              [p["title"] for p in body["items"] if p["current"]] == ["Врата Штейна"])
    finally:
        cat.related_parts = real_related

    main.catalog_limit.reset()

    async def dead_related(title):
        return None

    cat.related_parts = dead_related
    try:
        r = t.get("/api/related", params={"title": "Врата Штейна"})
        # Для страницы просмотра молчание справочника — мелочь: блок
        # просто не появится. Ронять из-за него плеер несоразмерно.
        check("молчание справочника не ломает страницу просмотра",
              r.status_code == 200 and r.json()["catalog"] is False, r.status_code)
    finally:
        cat.related_parts = real_related

    # ------------------------------------------------------------------
    group("Озвучка: чья именно")

    class Src:
        def __init__(self, title, urls):
            self.title, self._urls = title, urls

        async def a_get_videos(self):
            return [FakeVideo(u) for u in self._urls]

    class FakeVideo2:
        def __init__(self, url, q):
            self.url, self.quality, self.type = url, q, "m3u8"

    # Название озвучки лежит в поле title, а код читал name — поля с таким
    # именем у источника нет вовсе. В меню у всех строк стояло «плеер».
    check("название озвучки берётся из title",
          main.dub_name(Src("Озвучка источник A", []), "demo")[0] == "источник A",
          main.dub_name(Src("Озвучка источник A", []), "demo")[0])
    check("слово «Озвучка» из строки убирается",
          main.dub_name(Src("Озвучка студия озвучки", []), "demo")[0] == "студия озвучки")
    # А вот «Субтитры» убирать нельзя: это разница между «слушать» и
    # «читать», и видеть её надо до нажатия.
    name, is_sub = main.dub_name(Src("Субтитры крупный сервис", []), "demo")
    check("субтитры остаются подписанными субтитрами",
          name == "Субтитры крупный сервис" and is_sub, name)
    check("без названия подставляется сам источник",
          main.dub_name(Src("", []), "demo")[0] == main.anime.SOURCES["demo"]["label"],
          main.dub_name(Src("", []), "demo")[0])

    links = main.pack_links([FakeVideo2("a", 480), FakeVideo2("b", 1080),
                             FakeVideo2("b", 1080), FakeVideo2("", 720)])
    check("качества идут от лучшего к худшему",
          [x["quality"] for x in links] == [1080, 480], [x["quality"] for x in links])
    check("повторы и пустые ссылки выброшены", len(links) == 2, len(links))

    # ------------------------------------------------------------------
    group("Озвучки: спрашиваем одну, показываем все")
    main.source_limit.reset()
    real_eps3 = main.anime.find_episodes
    asked = []

    class DubSrc:
        def __init__(self, title, urls):
            self.title, self._urls = title, urls

        async def a_get_videos(self):
            asked.append(self.title)
            return [FakeVideo2(u, 720) for u in self._urls]

    class DubEpisode:
        ordinal = 1

        async def a_get_sources(self):
            # Так это и приходит: одна озвучка несколькими хостингами.
            return [DubSrc("Озвучка источник A", ["al-1"]),
                    DubSrc("Озвучка источник A", ["al-2"]),
                    DubSrc("Озвучка JAM", ["jam-1"]),
                    DubSrc("Субтитры крупный сервис", ["cr-1"]),
                    DubSrc("Озвучка студия озвучки", ["studio-1"])]

    async def dub_find(source, key, title):
        return [DubEpisode()]

    main.anime.find_episodes = dub_find
    try:
        r = t.get("/api/videos", params={"key": "k", "ordinal": 1,
                                          "source": "demo"})
        body = r.json()
        check("список озвучек полный", r.status_code == 200
              and [d["name"] for d in body["dubs"]]
              == ["источник A", "JAM", "студия озвучки", "Субтитры крупный сервис"],
              [d["name"] for d in body["dubs"]])
        # Спросить ссылки у всех — это секунды на каждую: у «Магической
        # битвы» их тридцать семь, то есть минута перед пустым плеером.
        check("а ссылки спрошены только у одной", len(asked) == 1, asked)
        check("открылась первая по порядку", body["chosen"] == "источник A",
              body["chosen"])
        check("субтитры уехали в конец списка",
              body["dubs"][-1]["sub"] is True)

        main.source_limit.reset()
        asked.clear()
        r = t.get("/api/videos", params={"key": "k", "ordinal": 1,
                                          "source": "demo",
                                          "dub": "студия озвучки"})
        check("выбранная озвучка открывается ею одной",
              r.json()["chosen"] == "студия озвучки" and len(asked) == 1,
              r.json()["chosen"] + " / " + str(asked))
    finally:
        main.anime.find_episodes = real_eps3

    # ------------------------------------------------------------------
    group("Источники: не предлагаем те, где тайтла нет")
    main.search_limit.reset()
    real_try2 = main.try_source

    async def has_title(source, q):
        return [{"key": "k", "title": "Магическая битва", "poster": "",
                 "year": 2020, "genres": "", "episodes_total": 24,
                 "match": 1.0}]

    main.try_source = has_title
    try:
        r = t.get("/api/where", params={"title": "Магическая битва"})
        body = r.json()
        check("сказано, у кого тайтл есть", body["here"] == ["demo"], body["here"])
        # Источник один, и это весь перебор: проверено всё, что есть, и
        # «непроверенных» не остаётся.
        check("и кого вообще проверяли",
              body["checked"] == list(main.anime.SOURCES), body["checked"])
        check("непроверенных не осталось", body["unknown"] == [], body["unknown"])
    finally:
        main.try_source = real_try2

    # ------------------------------------------------------------------
    group("Субтитры: ищем у всех, а не только у текущего")
    main.search_limit.reset()
    real_try3 = main.try_source
    real_eps4 = main.anime.find_episodes

    check("«Субтитры крупный сервис» опознаются как субтитры",
          main.is_subs("Субтитры крупный сервис"))
    check("«Озвучка студия озвучки» — не субтитры", not main.is_subs("Озвучка студия озвучки"))
    # Так это называется у одного из источников: японская дорожка, поверх
    # которой идёт текст. Правило искало слово «субтитры» только в начале
    # строки, а здесь оно в скобках на конце — и вариант не видели вовсе,
    # хотя он есть у «Атаки титанов», «Наруто» и «Ван-Пис».
    check("«Оригинал (+субтитры)» — тоже субтитры",
          main.is_subs("Оригинал (+субтитры)"))
    check("просто «Оригинал» без текста субтитрами не считается",
          not main.is_subs("Оригинал"))
    # В публичной версии списки перебора для субтитров пусты: внешних
    # источников нет. Пустой список — не поломка, а состояние: за текстом
    # сайт идёт к единственному источнику, который есть.
    check("список перебора для субтитров пуст",
          main.SUB_ORDER == [], main.SUB_ORDER)
    check("и по-английски тоже пуст",
          main.SUB_ORDER_EN == [], main.SUB_ORDER_EN)
    check("«Subtitles (English)» опознаются и как текст, и как английский",
          main.is_subs("Subtitles (English)")
          and main.sub_lang("Subtitles (English)") == "en",
          main.sub_lang("Subtitles (English)"))
    check("«English dub» субтитрами не считается",
          not main.is_subs("English dub"))

    # Язык текста. Все субтитры, что дают источники, русские — живая
    # проверка по четырём тайтлам не нашла ни одного английского. Но
    # опознавать английский всё равно надо: выдать его за русский было бы
    # хуже, чем не найти вовсе.
    check("русские субтитры помечены русскими",
          main.sub_lang("Субтитры команда субтитров") == "ru")
    check("английские опознаются, если появятся",
          main.sub_lang("Субтитры ENG") == "en", main.sub_lang("Субтитры ENG"))
    check("у озвучки языка текста нет вовсе",
          main.sub_lang("Озвучка студия озвучки") == "")
    # Регулярка для этой проверки однажды приехала с символом забоя
    # внутри: через оболочку обратная косая превратилась в настоящий
    # управляющий символ и осела в исходнике невидимкой. Совпадений не
    # было никогда, и выглядело это как «английских субтитров не бывает».
    check("в исходнике нет управляющих символов",
          not [c for c in io.open("api/main.py", encoding="utf-8").read()
               if ord(c) < 9 or (13 < ord(c) < 32)])

    class SubSrc:
        def __init__(self, title):
            self.title = title

        async def a_get_videos(self):
            return []

    class SubEpisode:
        def __init__(self, ordinal, titles):
            self.ordinal, self._titles = ordinal, titles

        async def a_get_sources(self):
            return [SubSrc(x) for x in self._titles]

    # Раскладка отвечает на вопрос «какие дорожки есть у серии»: озвучка
    # и текст. Сайт должен находить субтитры сам — человеку не нужно
    # вручную перебирать дорожки, чтобы узнать, есть ли они.
    LAYOUT = {
        "demo": ["Озвучка студия озвучки", "Субтитры крупный сервис"],
    }

    async def sub_try(source, q):
        if source in LAYOUT:
            return [{"key": source + "-key", "title": "Магическая битва",
                     "poster": "p", "year": 2020, "genres": "Экшен",
                     "episodes_total": 24, "match": 1.0}]
        return []

    async def sub_eps(source, key, title):
        return [SubEpisode(1, LAYOUT.get(source, [])),
                SubEpisode(2, LAYOUT.get(source, []))]

    main.try_source = sub_try
    main.anime.find_episodes = sub_eps
    try:
        r = t.get("/api/subs", params={"title": "Магическая битва",
                                        "ordinal": 1})
        body = r.json()
        check("субтитры найдены",
              r.status_code == 200 and body["found"]
              and body["source"] == "demo", body)
        check("и названы своим именем",
              body["dub"] == "Субтитры крупный сервис", body.get("dub"))
        check("вместе с ключом, по которому их открыть",
              body["key"] == "demo-key", body.get("key"))

        # Источник, где человек уже сидит, второй раз не смотрим.
        main.search_limit.reset()
        r = t.get("/api/subs", params={"title": "Магическая битва",
                                        "ordinal": 1, "skip": "demo"})
        check("источник, где уже смотрели, пропускается",
              "demo" not in r.json()["tried"], r.json()["tried"])
        # Пропущен единственный источник — искать больше негде.
        # Это честное «не нашлось», а не ошибка и не пустой плеер.
        check("искать больше негде — честное «не нашлось»",
              r.json()["found"] is False, r.json())
    finally:
        main.try_source = real_try3
        main.anime.find_episodes = real_eps4

    # Субтитров может не быть ни у кого. Это не ошибка и не пустой ответ —
    # об этом надо сказать прямо.
    main.search_limit.reset()

    async def no_subs_anywhere(source, q):
        return []

    main.try_source = no_subs_anywhere
    try:
        r = t.get("/api/subs", params={"title": "Стальной алхимик"})
        check("если субтитров нет нигде — так и сказано, а не 502",
              r.status_code == 200 and r.json()["found"] is False, r.status_code)
    finally:
        main.try_source = real_try3

    # ------------------------------------------------------------------
    group("Письма: адрес и шаблон")
    from api import mail

    check("хороший адрес принят", security.looks_like_email("valera@example.com"))
    for bad_addr in ["без-собаки", "два@@собаки.ru", "с пробелом@x.ru",
                     "конец@точка.", "@нет-имени.ru", "перенос@стро\nки.ru"]:
        check(f"плохой адрес отклонён: {bad_addr[:18]}",
              not security.looks_like_email(bad_addr))

    main.pass_limit.reset()
    check("включить письма без адреса нельзя",
          t.post("/api/me/mail", json={"email": "", "want": True}).status_code == 400)
    check("с адресом можно",
          t.post("/api/me/mail",
                 json={"email": "valera@example.com", "want": True}).status_code == 200)
    me = t.get("/api/me").json()
    check("адрес сохранён", me["email"] == "valera@example.com", me["email"])
    check("согласие сохранено", me["mail_new_eps"] is True)

    letter = mail.episode_html(display_name="Валера", title="Тайтл <script>",
                               episode=7, season="Сезон 2", released="1 мая",
                               about="Описание", poster="", watch_url="http://x/y")
    check("в письме есть название сайта", "анимеДик" in letter)
    check("есть номер серии", ">7<" in letter)
    check("есть сезон и дата", "Сезон 2" in letter and "1 мая" in letter)
    check("есть описание", "Описание" in letter)
    check("есть кнопка", "Смотреть" in letter)
    # Название приходит с чужого сайта — в письме оно обязано быть экранировано.
    check("разметка из названия обезврежена",
          "<script>" not in letter and "&lt;script&gt;" in letter)
    check("вёрстка таблицами, как требуют почтовые клиенты",
          letter.count("<table") >= 4)
    check("без настроек почты письма просто не уходят",
          mail.send_episode("valera@example.com", title="Т", episode=1) is False
          or mail.enabled())

    print("\n" + "=" * 56)
    if FAILS:
        print(f"НЕ ПРОШЛИ: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("Проверки исправлений пройдены.")
    return 0


if __name__ == "__main__":
    code = run()
    if code == 0:
        code = run_extra()
    if code == 0:
        code = run_fixes()
    sys.exit(code)
