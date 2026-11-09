"""Проход 46: общий демо-аккаунт — посмотреть можно всё, поменять нельзя ничего.

    python check46_demo_account.py

Как устроен демо-аккаунт (подробно — в INSTALL.md и в api/main.py,
раздел «Демо-аккаунт»):
  * включается переменной DEMO_ACCOUNT (логин обычного, не админского
    аккаунта) и только в демонстрационном режиме (MODE=demo);
  * входят в него одной кнопкой, без пароля: POST /api/auth/demo;
  * любой запрос, который меняет данные (всё, кроме GET/HEAD/OPTIONS),
    от сеанса демо-аккаунта получает 403 — кроме выхода.

Что проверяется:
  1. функция выключена — ручки входа «нет» (404), /api/mode молчит;
  2. включена — вход одной кнопкой, признак demo_account, срок сеанса;
  3. ВСЕ изменяющие ручки приложения (список берётся у самого
     приложения, а не выписан руками) отвечают демо-сеансу отказом и
     ничего не меняют в базе; выход работает;
  4. чтение работает как у обычного человека;
  5. админ в DEMO_ACCOUNT, выключенный аккаунт, MODE=private — 404;
  6. ограничение частоты по адресу; чужой перебор пароля не мешает;
  7. обычный пользователь и гость не задеты;
  8. браузер: кнопка видна только при включённой функции, двойной
     щелчок — один запрос, страница сама ничего не пытается записать.

Набор сам включает MODE=demo (функция живёт только там), поэтому в
обоих прогонах — demo и private — проверяет одно и то же. Личный режим
проверяется отдельным процессом с MODE=private.
"""
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="check46-")

# Режим и демо-аккаунт задаются ДО импорта приложения: оно читает их
# один раз при запуске, как и на настоящем сервере.
os.environ["MODE"] = "demo"
os.environ["DB_PATH"] = os.path.join(TMP, "p46.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass46-pepper-0123456789"
os.environ["DEMO_ACCOUNT"] = "  Demo-Show "      # регистр и пробелы — как ошибся бы человек
os.environ["DEMO_SESSION_MIN"] = "60"
os.environ["UPDATES_EVERY_MIN"] = "0"

sys.path.insert(0, HERE)

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
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)[:600]) if extra else ""),
          flush=True)
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


DEMO_LOGIN = "demo-show"
DEMO_PASS = "Vitrina-Dlya-Vseh-2026"
USER_PASS = "Tihiy-Signal-2026"
ADMIN_PASS = "Zaliv-Pepel-2026"
DENY_RU = "В демо-аккаунте ничего менять нельзя"
DENY_EN = "Nothing can be changed in the demo account."
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


# ==========================================================================
# Часть 1. Сервер внутри этого процесса (TestClient)
# ==========================================================================
def api_checks():
    from fastapi.testclient import TestClient
    from api import anime, catalog, errors, main, security, store

    # Журнал: предупреждение про админа в DEMO_ACCOUNT ловим сами, а
    # остальное приглушаем, чтобы не тонуть в строках.
    caught = []

    class Catch(logging.Handler):
        def emit(self, record):
            caught.append(record.getMessage())
    for name in ("httpx", "anime.catalog", "anime.sources"):
        logging.getLogger(name).setLevel(logging.ERROR)
    log = logging.getLogger("anime")
    log.setLevel(logging.INFO)
    log.addHandler(Catch())
    log.propagate = False

    def reset():
        main.login_guard.reset()
        for v in vars(main).values():
            if isinstance(v, security.RateLimiter):
                v.reset()
        main.guests._items.clear()

    def client():
        return TestClient(main.app)

    def login(c, who, password):
        r = c.post("/api/auth/login", json={"login": who, "password": password})
        if r.status_code == 200:
            c.headers["X-CSRF-Token"] = r.json()["csrf"]
        return r

    def demo(c, **kw):
        r = c.post("/api/auth/demo", json={}, **kw)
        if r.status_code == 200:
            c.headers["X-CSRF-Token"] = r.json()["csrf"]
        return r

    def guest(c):
        r = c.post("/api/auth/guest", json={})
        if r.status_code == 200:
            c.headers["X-CSRF-Token"] = r.json()["csrf"]
        return r

    def detail(r):
        try:
            return r.json().get("detail")
        except Exception:                                  # noqa: BLE001
            return r.text[:200]

    def snapshot():
        """Всё содержимое базы, кроме отметки «когда сессией пользовались»
        (её двигает любое чтение, и так и задумано)."""
        con = sqlite3.connect(os.environ["DB_PATH"])
        out = {}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
            keep = [c for c in cols if not (t == "sessions" and c == "last_seen")]
            out[t] = sorted(map(repr, con.execute(
                "SELECT %s FROM %s" % (",".join(keep), t)).fetchall()))
        con.close()
        return out

    def age_sessions(user_id, seconds):
        con = sqlite3.connect(os.environ["DB_PATH"])
        con.execute("UPDATE sessions SET created_at = created_at - ? WHERE user_id = ?",
                    (seconds, user_id))
        con.commit()
        con.close()

    def item(key, title, **kw):
        it = {"key": key, "source": "demo", "title": title, "title_en": title, "poster": "",
              "year": 2008, "genres": "", "total_eps": 1, "watched_ep": 0, "position": 30,
              "status": "watching", "cid": ""}
        it.update(kw)
        return it

    store.init()
    reset()

    # ------------------------------------------------------------------
    group("Настройка прочитана из окружения")
    check("логин из DEMO_ACCOUNT приведён к обычному виду (регистр, пробелы)",
          getattr(main, "DEMO_ACCOUNT", None) == DEMO_LOGIN, getattr(main, "DEMO_ACCOUNT", None))
    check("DEMO_SESSION_MIN=60 → сеанс 3600 секунд",
          getattr(main, "DEMO_SESSION", None) == 3600, getattr(main, "DEMO_SESSION", None))
    check("текст отказа переведён (api/errors.py)", errors.translate(DENY_RU) == DENY_EN,
          errors.translate(DENY_RU))

    # ------------------------------------------------------------------
    group("Функция выключена (DEMO_ACCOUNT пуст) — её нет совсем")
    saved = getattr(main, "DEMO_ACCOUNT", "")
    main.DEMO_ACCOUNT = ""
    try:
        store.create_user(DEMO_LOGIN, DEMO_PASS, "user", "")
        c = client()
        r = c.get("/api/mode")
        check("/api/mode: demo_account не true", r.status_code == 200 and r.json().get("demo_account") is not True,
              r.text)
        r = c.post("/api/auth/demo", json={})
        check("/api/auth/demo → 404", r.status_code == 404, r.status_code)
        check("куки не выданы", "sid" not in r.cookies and not r.headers.get("set-cookie"),
              r.headers.get("set-cookie"))
        c2 = client()
        login(c2, DEMO_LOGIN, DEMO_PASS)
        r = c2.get("/api/me")
        check("вход паролем в этот аккаунт — обычный, без признака демо",
              r.status_code == 200 and not r.json().get("demo_account"), r.text)
        r = c2.post("/api/me/profile", json={"display_name": "Витрина"})
        check("и владелец может наполнять его (тут: имя) — запрета нет", r.status_code == 200, r.text)
        r = c2.post("/api/me/settings", json={"ui_lang": "en", "anime_lang": "ensub", "theme": "noch"})
        check("… и настройки (язык, тема)", r.status_code == 200, r.text)
        r = c2.post("/api/library/progress", json=item("demo:bbb", "Big Buck Bunny"))
        check("… и полку", r.status_code == 200, r.text)
        r = c2.post("/api/library/progress", json=item("demo:sintel", "Sintel", status="done",
                                                        watched_ep=1))
        check("… ещё тайтл", r.status_code == 200, r.text)
        c2.post("/api/auth/logout")
    finally:
        main.DEMO_ACCOUNT = saved

    demo_user = store.get_user_by_login(DEMO_LOGIN)
    demo_id = int(demo_user["id"])
    store.create_user("misha", USER_PASS, "user", "Миша")
    admin_id = store.create_user("hozyain", ADMIN_PASS, "admin", "Хозяин")
    user_id = int(store.get_user_by_login("misha")["id"])

    # ------------------------------------------------------------------
    group("Функция включена: кнопка и вход одной кнопкой")
    reset()
    c = client()
    r = c.get("/api/mode")
    check("/api/mode: demo_account true", r.status_code == 200 and r.json().get("demo_account") is True, r.text)
    check("/api/mode: логина демо-аккаунта в ответе нет", DEMO_LOGIN not in r.text.lower(), r.text)
    r = demo(c)
    check("POST /api/auth/demo → 200", r.status_code == 200, r.text)
    body = r.json() if r.status_code == 200 else {}
    check("в ответе метка формы (csrf)", bool(body.get("csrf")))
    check("в ответе me.demo_account true", (body.get("me") or {}).get("demo_account") is True, body)
    check("логина нет и в ответе входа", DEMO_LOGIN not in r.text.lower(), r.text[:300])
    cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else \
        r.headers.get("set-cookie", "").split(", sid")
    raw = "; ".join(cookies)
    check("выданы обе куки, как при обычном входе (sid и csrf)",
          "sid=" in raw and "csrf=" in raw, raw[:300])
    check("срок куки — DEMO_SESSION_MIN (Max-Age=3600)", raw.count("Max-Age=3600") >= 2, raw[:300])
    check("кука сессии httponly", "HttpOnly" in raw.split("csrf=")[0] or "httponly" in raw.lower())
    r = c.get("/api/me")
    me = r.json() if r.status_code == 200 else {}
    check("/api/me: 200 и demo_account true", r.status_code == 200 and me.get("demo_account") is True, r.text)
    check("/api/me: это не админ", me.get("role") == "user", me.get("role"))
    check("/api/me: логина нет нигде в ответе", DEMO_LOGIN not in r.text.lower(), r.text[:300])
    check("/api/me: почты владельца аккаунта нет", not me.get("email"), me.get("email"))
    check("/api/me: имя, которое владелец задал, видно", me.get("display_name") == "Витрина",
          me.get("display_name"))
    st = me.get("settings") or {}
    check("/api/me: тема владельца видна (витрина)", st.get("theme") == "noch", st)
    check("/api/me: языка сайта и аниме из аккаунта нет — они за посетителем",
          not {"ui_lang", "lang", "anime_lang"} & set(st), st)
    check("/api/me: can_edit false", me.get("can_edit") is False, me.get("can_edit"))
    check("сессия записана на демо-аккаунт",
          store.count_sessions(demo_id) >= 1, store.count_sessions(demo_id))

    # ------------------------------------------------------------------
    group("Демо-сеанс живёт DEMO_SESSION_MIN")
    age_sessions(demo_id, 3600 - 120)
    check("за две минуты до конца ещё пускает", c.get("/api/me").status_code == 200)
    age_sessions(demo_id, 240)
    r = c.get("/api/me")
    check("через час после входа — уже нет (401)", r.status_code == 401, r.status_code)
    check("и строка сессии убрана из базы", store.count_sessions(demo_id) == 0,
          store.count_sessions(demo_id))
    reset()
    c = client()
    r = demo(c)
    check("новый вход одной кнопкой снова работает", r.status_code == 200, r.text)

    # ------------------------------------------------------------------
    group("Защиты входа — как у /api/auth/guest")
    reset()
    x = client()
    r = x.post("/api/auth/demo", data={"a": "b"})
    check("HTML-форма с чужого сайта (не JSON) → 403", r.status_code == 403, r.status_code)
    r = x.post("/api/auth/demo")
    check("без Content-Type → 403", r.status_code == 403, r.status_code)
    r = x.post("/api/auth/demo", json={}, headers={"Origin": "https://evil.example"})
    check("чужой Origin → 403", r.status_code == 403, r.status_code)
    check("ни один из отказов не выдал куку", not x.cookies.get("sid"))
    u = client()
    login(u, "misha", USER_PASS)
    r = u.post("/api/auth/demo", json={})
    check("уже вошедший в свой аккаунт → 409, его вход не перезаписан",
          r.status_code == 409 and u.get("/api/me").json().get("name") == "misha", r.status_code)
    g = client()
    guest(g)
    old = g.cookies.get("sid")
    r = demo(g)
    check("гость → входит в демо-аккаунт", r.status_code == 200 and
          g.get("/api/me").json().get("demo_account") is True, r.text)
    check("гостевой пропуск при этом закрыт", main.guests.get(old) is None)
    r = c.post("/api/auth/demo", json={})
    check("из самого демо-сеанса повторный вход — отказ 403 (как любая запись)",
          r.status_code == 403 and detail(r) == DENY_RU, r.text)

    # ------------------------------------------------------------------
    group("ВСЕ изменяющие ручки приложения: демо-сеансу — отказ")
    store.save_progress(demo_id, item("demo:tos", "Tears of Steel"))
    # Список — у самого приложения: новая ручка попадёт сюда сама.
    routes = []
    for rt in main.app.routes:
        path = getattr(rt, "path", "")
        methods = sorted(m for m in (getattr(rt, "methods", None) or ()) if m not in SAFE_METHODS)
        if path.startswith("/api") and methods:
            routes += [(m, path) for m in methods]
    routes.sort()

    def fill(path):
        return re.sub(r"\{(\w+)(?::\w+)?\}",
                      lambda m: str(demo_id) if m.group(1).endswith("id") else "demo:tos", path)

    body = {"display_name": "Взлом", "avatar_color": "#000000", "current": DEMO_PASS,
            "new": "Novyy-Parol-Zahvat-2026", "password": DEMO_PASS, "code": "123456",
            "adult": True, "hentai": True, "theme": "noch", "ui_lang": "en",
            "key": "demo:tos", "source": "demo", "title": "Tears of Steel", "watched_ep": 1,
            "status": "done", "login": "vzlom", "role": "admin", "text": "захвачено",
            "email": "x@example.com", "on": True, "message": "привет", "history": []}
    check("ручек, меняющих данные, нашлось много (больше двадцати)", len(routes) > 20, len(routes))
    before = snapshot()
    seen = []
    for method, path in routes:
        if path == "/api/auth/logout":
            continue                                          # проверяется ниже, отдельно
        r = c.request(method, fill(path), json=body)
        seen.append("%s %s" % (method, path))
        if path.startswith(main.ADMIN_PREFIX + "/") or path == main.ADMIN_PREFIX:
            # Админка для не-админа не существует вовсе (session_edges):
            # отказ — 404, как всем остальным. Это сильнее 403.
            check("%s %s → 404 (админки для не-админа нет)" % (method, path),
                  r.status_code == 404, r.status_code)
            continue
        check("%s %s → 403 «%s»" % (method, path, DENY_RU),
              r.status_code == 403 and detail(r) == DENY_RU, "%s %s" % (r.status_code, detail(r)))
    print("  обошли ручек: %d" % len(seen))
    for method in ("PUT", "PATCH", "DELETE", "POST"):
        r = c.request(method, "/api/novaya-ruchka-kotoroy-poka-net", json=body)
        check("%s на несуществующую ручку → тоже 403 («запрещено всё, что не разрешено»)" % method,
              r.status_code == 403 and detail(r) == DENY_RU, r.status_code)
    r = c.put("/api/me/settings", json=body)
    check("чужой метод на настоящую ручку (PUT /api/me/settings) → 403, а не 405",
          r.status_code == 403, r.status_code)
    r = c.post("/api/me/avatar", content=b"\x89PNG....", headers={"Content-Type": "image/png"})
    check("картинка (не JSON) → 403", r.status_code == 403 and detail(r) == DENY_RU, r.status_code)
    r = c.post("/api/me/settings", json={"adult": True}, headers={"X-UI-Lang": "en"})
    check("по-английски — английский текст", r.status_code == 403 and detail(r) == DENY_EN, detail(r))
    check("detail_en — всегда английский", r.json().get("detail_en") == DENY_EN, r.text)
    after = snapshot()
    changed = [t for t in before if before[t] != after.get(t)]
    check("в базе не изменилось НИЧЕГО (все таблицы)", not changed, changed)
    du = store.get_user(demo_id)
    check("пароль прежний", security.verify_password(DEMO_PASS, du["pass_hash"]))
    check("вход по коду не включён", not du["totp_on"])
    check("имя прежнее", du["display_name"] == "Витрина", du["display_name"])
    check("настройки 18+ и «Хентай» не тронуты",
          store.get_settings(demo_id).get("adult") is not True
          and store.get_settings(demo_id).get("hentai") is not True, store.get_settings(demo_id))
    check("«выйти везде» не сработал: сеанс жив", c.get("/api/me").status_code == 200)
    check("аккаунт «vzlom» не создан", store.get_user_by_login("vzlom") is None)
    check("полка на месте", len(store.library(demo_id)) == 3, len(store.library(demo_id)))

    # GET, который мог бы записать: /api/poster с key дописывает обложку
    # в запись полки. Для демо-аккаунта — нет.
    real_find, real_cached = catalog.find_poster, catalog.poster_cached

    async def fake_find(*a, **k):
        return "https://shikimori.one/system/animes/original/1.jpg"
    def poster_of(key):
        return [i for i in store.library(demo_id) if i["key"] == key][0]["poster"]
    catalog.find_poster, catalog.poster_cached = fake_find, (lambda *a, **k: None)
    try:
        r = c.get("/api/poster", params={"title": "Tears of Steel", "key": "demo:tos"})
        check("GET /api/poster с key: обложку отдал, но в полку не записал",
              r.status_code == 200 and r.json().get("poster") and not poster_of("demo:tos"), r.text)
        # Для сравнения: тот же аккаунт при выключенной функции — записывает
        # (значит, проверка выше что-то проверяет).
        main.DEMO_ACCOUNT = ""
        o = client()
        login(o, DEMO_LOGIN, DEMO_PASS)
        o.get("/api/poster", params={"title": "Sintel", "key": "demo:sintel"})
        check("… а обычному сеансу этого аккаунта — записывает", bool(poster_of("demo:sintel")))
        o.post("/api/auth/logout")
    finally:
        main.DEMO_ACCOUNT = DEMO_LOGIN
        catalog.find_poster, catalog.poster_cached = real_find, real_cached

    # ------------------------------------------------------------------
    group("Чтение — как у обычного пользователя")
    r = c.get("/api/library")
    titles = sorted(i.get("title") for i in (r.json().get("items") or [])) if r.status_code == 200 else []
    check("/api/library — полка видна", titles == ["Big Buck Bunny", "Sintel", "Tears of Steel"], r.text[:300])
    r = c.get("/api/search", params={"q": "sintel"})
    check("поиск в демо-режиме работает", r.status_code == 200 and r.json(), r.text[:300])
    r = c.get("/api/stats/year")
    check("итоги года открываются", r.status_code == 200, r.status_code)
    r = c.get("/api/updates")
    check("колокольчик читается", r.status_code == 200, r.status_code)
    r = c.get("/api/me/avatar")
    check("аватар читается (своего нет — как у всех)", r.status_code in (200, 204, 404), r.status_code)
    r = c.get("/api/admin/users")
    check("админка — по-прежнему 404", r.status_code == 404, r.status_code)

    # ------------------------------------------------------------------
    group("Выход работает — и только из своего сеанса")
    reset()
    a, b = client(), client()
    demo(a)
    demo(b)
    n = store.count_sessions(demo_id)
    r = a.post("/api/auth/logout")
    check("POST /api/auth/logout → 200", r.status_code == 200, r.text)
    check("этот сеанс закрыт", a.get("/api/me").status_code == 401)
    check("сеанс другого посетителя жив", b.get("/api/me").status_code == 200)
    check("в базе ровно на одну сессию меньше", store.count_sessions(demo_id) == n - 1,
          (n, store.count_sessions(demo_id)))

    # ------------------------------------------------------------------
    group("Вход паролем в демо-аккаунт, пока функция включена, — тоже только чтение")
    reset()
    p = client()
    r = login(p, DEMO_LOGIN, DEMO_PASS)
    check("пароль подходит", r.status_code == 200, r.text)
    check("признак demo_account и тут", (r.json().get("me") or {}).get("demo_account") is True)
    raw = "; ".join(r.headers.get_list("set-cookie")) if hasattr(r.headers, "get_list") else ""
    check("срок куки тот же, что у кнопки (3600)", "Max-Age=3600" in raw, raw[:200])
    r = p.post("/api/me/profile", json={"display_name": "Взлом"})
    check("запись — 403", r.status_code == 403 and detail(r) == DENY_RU, r.text)
    r = p.post("/api/me/password", json={"current": DEMO_PASS, "new": "Novyy-Parol-Zahvat-2026"})
    check("сменить пароль, зная его, — тоже нельзя", r.status_code == 403, r.status_code)

    # ------------------------------------------------------------------
    group("Ограничение частоты: по адресу")
    reset()
    main.TRUST_PROXY = True
    try:
        ok = 0
        for i in range(10):
            r = client().post("/api/auth/demo", json={}, headers={"X-Real-IP": "203.0.113.5"})
            ok += r.status_code == 200
        check("десять входов в час с одного адреса — можно", ok == 10, ok)
        r = client().post("/api/auth/demo", json={}, headers={"X-Real-IP": "203.0.113.5"})
        check("одиннадцатый — 429", r.status_code == 429, r.status_code)
        r = client().post("/api/auth/demo", json={}, headers={"X-Real-IP": "203.0.113.77"})
        check("с другого адреса — пускает", r.status_code == 200, r.status_code)
    finally:
        main.TRUST_PROXY = False

    # ------------------------------------------------------------------
    group("Перебор пароля демо-аккаунта не запирает кнопку")
    reset()
    main.TRUST_PROXY = True
    try:
        bad = client()
        codes = []
        for i in range(40):
            r = bad.post("/api/auth/login", json={"login": DEMO_LOGIN, "password": "nepravilno-%d" % i},
                         headers={"X-Real-IP": "198.51.100.9"})
            codes.append(r.status_code)
            if r.status_code == 429:
                break
        check("перебор паролем упёрся в блокировку (429)", 429 in codes, codes[-3:])
        r = client().post("/api/auth/demo", json={}, headers={"X-Real-IP": "198.51.100.9"})
        check("кнопка с того же адреса — работает", r.status_code == 200, r.text)
        r = client().post("/api/auth/demo", json={}, headers={"X-Real-IP": "192.0.2.10"})
        check("и с любого другого — тоже", r.status_code == 200, r.text)
        check("пароль демо-аккаунта не изменился", security.verify_password(
            DEMO_PASS, store.get_user(demo_id)["pass_hash"]))
    finally:
        main.TRUST_PROXY = False

    # ------------------------------------------------------------------
    group("Выключенный аккаунт и админ в DEMO_ACCOUNT — 404")
    reset()
    store.set_disabled(demo_id, True)
    r = client().get("/api/mode")
    check("выключен: /api/mode кнопку не предлагает", r.json().get("demo_account") is not True, r.text)
    r = client().post("/api/auth/demo", json={})
    check("выключен: 404", r.status_code == 404, r.status_code)
    store.set_disabled(demo_id, False)
    main.DEMO_ACCOUNT = "net-takogo"
    r = client().post("/api/auth/demo", json={})
    check("нет такого аккаунта: 404", r.status_code == 404, r.status_code)
    check("нет такого аккаунта: кнопки нет", client().get("/api/mode").json().get("demo_account") is not True)
    main.DEMO_ACCOUNT = "hozyain"
    caught.clear()
    r = client().post("/api/auth/demo", json={})
    check("админ: 404", r.status_code == 404, r.status_code)
    check("админ: куки не выданы", not r.headers.get("set-cookie"), r.headers.get("set-cookie"))
    check("админ: кнопки нет", client().get("/api/mode").json().get("demo_account") is not True)
    check("админ: предупреждение в журнале",
          any("DEMO_ACCOUNT" in m and "админ" in m.lower() for m in caught), caught[-3:])
    sh = client()
    login(sh, "hozyain", ADMIN_PASS)
    r = sh.post("/api/admin/news", json={"text": "Работаем"})
    check("сам админ при этом не заперт (ошибка в настройке не отнимает админку)",
          r.status_code == 200, r.text)
    sh.request("DELETE", "/api/admin/news")
    main.DEMO_ACCOUNT = DEMO_LOGIN

    # ------------------------------------------------------------------
    group("MODE=private (в этом же процессе) — функции нет, даже с DEMO_ACCOUNT")
    reset()
    anime.DEMO = False
    try:
        r = client().get("/api/mode")
        check("/api/mode: demo_account не true", r.json().get("demo_account") is not True, r.text)
        r = client().post("/api/auth/demo", json={})
        check("/api/auth/demo → 404", r.status_code == 404, r.status_code)
        q = client()
        login(q, DEMO_LOGIN, DEMO_PASS)
        check("вход паролем — обычный аккаунт, не демо", not q.get("/api/me").json().get("demo_account"))
        r = q.post("/api/me/settings", json={"theme": "noch"})
        check("и запись не запрещена", r.status_code == 200, r.text)
        q.post("/api/me/settings", json={"theme": "kak-seychas"})
    finally:
        anime.DEMO = True

    # ------------------------------------------------------------------
    group("Обычный пользователь и гость не задеты")
    reset()
    u = client()
    login(u, "misha", USER_PASS)
    check("пользователь: /api/me без признака демо", not u.get("/api/me").json().get("demo_account"))
    r = u.post("/api/me/profile", json={"display_name": "Миша 2"})
    check("пользователь: имя меняется", r.status_code == 200, r.text)
    r = u.post("/api/me/settings", json={"adult": True})
    check("пользователь: настройки сохраняются", r.status_code == 200, r.text)
    r = u.post("/api/library/progress", json=item("demo:bbb", "Big Buck Bunny"))
    check("пользователь: полка пополняется", r.status_code == 200, r.text)
    r = u.request("DELETE", "/api/library/demo:bbb")
    check("пользователь: и убирается", r.status_code == 200, r.text)
    r = u.post("/api/auth/logout-all")
    check("пользователь: «выйти везде» работает", r.status_code == 200, r.text)
    g = client()
    guest(g)
    r = g.post("/api/me/profile", json={"display_name": "x"})
    check("гость: отказ прежний (403 с гостевым текстом, не демо)",
          r.status_code == 403 and detail(r) != DENY_RU and "Гост" in str(detail(r)), r.text)
    r = g.get("/api/library")
    check("гость: полка пустая, как раньше", r.status_code == 200 and r.json().get("items") == [], r.text)
    check("гость: без признака демо", not g.get("/api/me").json().get("demo_account"))
    r = g.post("/api/auth/logout")
    check("гость: выход работает", r.status_code == 200)
    check("чужие полки демо-сеанс не видит (у Миши своё)",
          not [i for i in store.library(demo_id) if i.get("title") == "Миша"])
    _ = admin_id, user_id
    return seen


# ==========================================================================
# Часть 2. Настоящий запуск с переменными окружения (отдельный процесс)
# ==========================================================================
ENV_PROBE = r'''
import json, os, sys
sys.path.insert(0, os.environ["P46_HERE"])
import logging
logging.disable(logging.CRITICAL)
from fastapi.testclient import TestClient
from api import main, store
store.init()
if not store.get_user_by_login("demo-show"):
    store.create_user("demo-show", "Vitrina-Dlya-Vseh-2026", "user", "")
c = TestClient(main.app)
out = {"account": getattr(main, "DEMO_ACCOUNT", None), "session": getattr(main, "DEMO_SESSION", None),
       "mode": c.get("/api/mode").json(), "demo": c.post("/api/auth/demo", json={}).status_code}
print("P46=" + json.dumps(out))
'''


def env_probe(**env):
    e = dict(os.environ)
    for k in ("DEMO_ACCOUNT", "DEMO_SESSION_MIN"):
        e.pop(k, None)
    e.update({"P46_HERE": HERE, "DB_PATH": os.path.join(tempfile.mkdtemp(dir=TMP), "e.db")})
    e.update(env)
    r = subprocess.run([sys.executable, "-c", ENV_PROBE], env=e, cwd=HERE, capture_output=True,
                       text=True, timeout=120)
    for line in r.stdout.splitlines():
        if line.startswith("P46="):
            return json.loads(line[4:])
    return {"error": (r.stderr or r.stdout)[-800:]}


def env_checks():
    group("Запуск с настоящими переменными окружения")
    r = env_probe(MODE="demo")
    check("DEMO_ACCOUNT не задан — функции нет (кнопки нет, 404)",
          r.get("mode", {}).get("demo_account") is not True and r.get("demo") == 404, r)
    r = env_probe(MODE="demo", DEMO_ACCOUNT="")
    check("DEMO_ACCOUNT пустой — то же", r.get("demo") == 404, r)
    r = env_probe(MODE="demo", DEMO_ACCOUNT="demo-show")
    check("MODE=demo + DEMO_ACCOUNT — кнопка есть, вход 200",
          r.get("mode", {}).get("demo_account") is True and r.get("demo") == 200, r)
    check("DEMO_SESSION_MIN не задан — 60 минут", r.get("session") == 3600, r)
    r = env_probe(MODE="private", DEMO_ACCOUNT="demo-show")
    check("MODE=private + DEMO_ACCOUNT — функции нет: 404 и кнопки нет",
          r.get("mode", {}).get("demo_account") is not True and r.get("demo") == 404, r)
    r = env_probe(MODE="demo", DEMO_ACCOUNT="demo-show", DEMO_SESSION_MIN="abc")
    check("DEMO_SESSION_MIN=abc — 60 минут, сайт работает", r.get("session") == 3600 and r.get("demo") == 200, r)
    r = env_probe(MODE="demo", DEMO_ACCOUNT="demo-show", DEMO_SESSION_MIN="15")
    check("DEMO_SESSION_MIN=15 — 900 секунд", r.get("session") == 900, r)
    r = env_probe(MODE="demo", DEMO_ACCOUNT="demo-show", DEMO_SESSION_MIN="0")
    check("DEMO_SESSION_MIN=0 — не «вечный и мгновенный», а 60 минут", r.get("session") == 3600, r)


# ==========================================================================
# Часть 3. Браузер
# ==========================================================================
LAUNCHER = r'''
import os, sys
sys.path.insert(0, os.environ["P46_HERE"])
from api import main, store
import uvicorn
store.init()
store.create_user("demo-show", "Vitrina-Dlya-Vseh-2026", "user", "Витрина")
store.create_user("misha", "Tihiy-Signal-2026", "user", "Миша")
uid = store.get_user_by_login("demo-show")["id"]
for key, title in (("demo:bbb", "Big Buck Bunny"), ("demo:sintel", "Sintel")):
    store.save_progress(uid, {"key": key, "source": "demo", "title": title, "title_en": title,
                              "poster": "", "year": 2008, "genres": "", "total_eps": 1,
                              "watched_ep": 0, "position": 30, "status": "watching", "cid": ""})
uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ["P46_PORT"]), log_level="warning")
'''


def free_port():
    import random
    ports = list(range(8700, 8800))
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
    raise RuntimeError("нет свободного порта в 8700–8799")


class Server:
    def __init__(self, demo_account):
        self.tmp = tempfile.mkdtemp(prefix="srv-", dir=TMP)
        self.log = open(os.path.join(self.tmp, "server.log"), "w")
        for _ in range(3):
            self.port = free_port()
            self.base = "http://127.0.0.1:%d" % self.port
            env = dict(os.environ)
            env.update({"MODE": "demo", "SESSION_PEPPER": "pass46-pepper-0123456789",
                        "COOKIE_SECURE": "0", "UPDATES_EVERY_MIN": "0", "P46_HERE": HERE,
                        "DB_PATH": os.path.join(self.tmp, "b-%d.db" % self.port),
                        "DEMO_ACCOUNT": demo_account, "P46_PORT": str(self.port)})
            py = os.environ.get("P46_SERVER_PY") or sys.executable
            self.proc = subprocess.Popen([py, "-c", LAUNCHER], cwd=HERE, env=env,
                                         stdout=self.log, stderr=subprocess.STDOUT)
            if self._wait():
                return
        raise RuntimeError("сервер не запустился:\n" + open(self.log.name).read()[-3000:])

    def _wait(self):
        end = time.time() + 60
        while time.time() < end:
            if self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(self.base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        return True
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


def browser_checks():
    from playwright.sync_api import sync_playwright

    def wait(page, js, limit=8.0):
        end = time.time() + limit
        while time.time() < end:
            try:
                if page.evaluate(js):
                    return True
            except Exception:                              # noqa: BLE001
                pass
            time.sleep(0.15)
        return False

    group("Браузер: функция выключена — кнопки нет")
    off = Server("")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            page.goto(off.base + "/")
            page.wait_for_selector("#gate:not([hidden])")
            time.sleep(0.8)                               # /api/mode успел ответить
            check("кнопки «Войти в демо-аккаунт» не видно", not page.is_visible("#btn-demo"))
            check("кнопка гостя на месте", page.is_visible("#btn-guest"))
            b.close()
    finally:
        off.stop()

    group("Браузер: функция включена")
    on = Server("demo-show")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 1280, "height": 800}, locale="ru-RU")
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            sent = []

            def on_req(req):
                if req.method not in SAFE_METHODS and "/api/" in req.url:
                    sent.append((req.method, req.url.split("://", 1)[1].split("/", 1)[1]))
            page.on("request", on_req)
            denied = []
            page.on("response", lambda r: denied.append(r.url) if r.status == 403 else None)

            page.goto(on.base + "/")
            page.wait_for_selector("#gate:not([hidden])")
            check("кнопка видна", wait(page, "!!document.querySelector('#btn-demo') && "
                                             "document.querySelector('#btn-demo').offsetParent !== null"))
            check("подпись по-русски", "демо" in page.inner_text("#btn-demo").lower(),
                  page.inner_text("#btn-demo"))
            check("тот же вид, что у кнопки гостя (классы)",
                  page.get_attribute("#btn-demo", "class") == page.get_attribute("#btn-guest", "class"),
                  page.get_attribute("#btn-demo", "class"))
            page.click('.langrow .lang[data-set-lang="en"]')
            check("по-английски — Try the demo account",
                  "Try the demo account" in page.inner_text("#btn-demo"), page.inner_text("#btn-demo"))
            page.click('.langrow .lang[data-set-lang="ru"]')
            page.dblclick("#btn-demo")
            page.wait_for_selector("#app:not([hidden])", timeout=15000)
            page.wait_for_selector("#boot", state="hidden", timeout=15000)
            time.sleep(1.5)
            demo_posts = [s for s in sent if s[1].startswith("api/auth/demo")]
            check("двойной щелчок — один запрос входа", len(demo_posts) == 1, sent)
            me = page.evaluate("App.me")
            check("вошли: App.me.demo_account", bool(me and me.get("demo_account")), me)
            check("в шапке — имя аккаунта, не логин",
                  page.inner_text("#whoami").strip() == "Витрина", page.inner_text("#whoami"))
            check("полка демо-аккаунта видна",
                  wait(page, "document.querySelectorAll('#app .card').length >= 2"),
                  page.evaluate("document.querySelectorAll('#app .card').length"))
            check("на карточках полки нет «×» (убрать нельзя)",
                  page.evaluate("document.querySelectorAll('#app .card .del').length") == 0)
            check("плашки гостя нет", not page.is_visible("#guestbar"))
            # Кабинет: открыть можно (посмотреть), поменять — нет.
            page.click("#who")
            wait(page, "document.getElementById('ucard-wrap').classList.contains('show')")
            check("в карточке профиля есть «Настройки» (посмотреть можно)", page.is_visible("#uc-set"))
            page.click("#uc-set")
            wait(page, "document.getElementById('veil-set').classList.contains('show')")
            check("кабинет: плашка «Только просмотр»", page.is_visible("#lockbar"))
            check("кабинет: текст плашки — про демо-аккаунт",
                  "демо" in page.inner_text("#lockbar").lower(), page.inner_text("#lockbar"))
            check("кабинет: «Сохранить» выключен", page.is_disabled("#set-save"))
            live = page.evaluate("""Array.prototype.slice.call(document.querySelectorAll(
                '#pbody input, #pbody select, #pbody button')).filter(function (n) {
                  return !n.disabled && !n.closest('.lockbar') && !n.classList.contains('guest-ok') &&
                         !n.closest('.cabrail');
                }).map(function (n) { return n.id || n.className; })""")
            check("кабинет: включённых полей и кнопок нет (кроме языка)", not live, live)
            # Сервер всё равно откажет — и страница покажет понятный текст.
            res = page.evaluate("App.api.post('/api/me/profile', {display_name: 'x'})"
                                ".then(function () { return 'прошло'; }, function (e) { return e.message; })")
            check("запрос в обход кнопок — понятный текст отказа", res == DENY_RU, res)
            page.click('.langrow .lang[data-set-lang="en"]') if page.is_visible('.langrow .lang') else None
            page.evaluate("App.applyLang('en')")
            res = page.evaluate("App.api.post('/api/me/profile', {display_name: 'x'})"
                                ".then(function () { return 'прошло'; }, function (e) { return e.message; })")
            check("… и по-английски", res == DENY_EN, res)
            page.evaluate("App.applyLang('ru')")
            # Тема из карточки: на экране меняется, на сервер не уходит.
            page.keyboard.press("Escape")
            sent_before = len(sent)
            page.evaluate("""(function () { var b = document.querySelector('#uc-theme .ucth[data-th="noch"]');
                             if (b) b.click(); })()""")
            page.evaluate("App.setAnimeLang('endub')")
            time.sleep(0.8)
            extra = [s for s in sent[sent_before:]]
            check("тема и язык аниме: запрос на запись не уходит", not extra, extra)
            # Страница просмотра: прогресс не сохраняется (и не пытается).
            # Без обложки в адресе: обычному человеку страница сама нашла бы
            # её и сохранила в полку — демо-аккаунту не должна.
            page.goto(on.base + "/watch?key=demo%3Abbb&source=demo&title=Big%20Buck%20Bunny&total=1")
            time.sleep(4)
            check("просмотр: кнопки «В планы» нет", not page.is_visible("#w-plan"))
            check("просмотр: под автопереходом — «в демо-аккаунте не сохраняем»",
                  "демо" in (page.text_content("#w-autonote") or ""), page.text_content("#w-autonote"))
            page.goto(on.base + "/stats")
            time.sleep(1.5)
            page.goto(on.base + "/")
            page.wait_for_selector("#app:not([hidden])", timeout=15000)
            time.sleep(1.5)
            # Кроме входа и двух нарочных запросов выше (/api/me/profile).
            writes = [s for s in sent if not s[1].startswith("api/auth/demo")
                      and s[1] != "api/me/profile"]
            check("за весь обход страница сама не послала ни одной записи", not writes, writes)
            check("нарочных запросов — ровно два", len([s for s in sent if s[1] == "api/me/profile"]) == 2,
                  sent)
            check("и ни одного отказа 403 (кроме нарочных выше)",
                  len([u for u in denied if "/api/me/profile" not in u]) == 0, denied)
            check("ошибок скриптов нет", not errs, errs)
            # Выход.
            page.evaluate("App.logout()")
            page.wait_for_selector("#gate:not([hidden])", timeout=15000)
            check("выход — снова страница входа с кнопкой", page.is_visible("#btn-demo"))
            ctx.close()
            b.close()
    finally:
        on.stop()


def _pw_python():
    mine = os.path.abspath(sys.executable)
    for cand in (os.environ.get("PW_PYTHON"), shutil.which("python3"), shutil.which("python")):
        if not cand or os.path.abspath(cand) == mine:
            continue
        try:
            ok = subprocess.run([cand, "-c", "import playwright.sync_api"], capture_output=True,
                                timeout=60).returncode == 0
        except Exception:                                  # noqa: BLE001
            ok = False
        if ok:
            return cand
    return None


def run():
    only_browser = "--browser-only" in sys.argv
    seen = []
    if not only_browser:
        try:
            seen = api_checks()
        except Exception as exc:                           # noqa: BLE001
            import traceback
            traceback.print_exc()
            check("часть с сервером отработала до конца", False, repr(exc))
        env_checks()
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
            env["P46_SERVER_PY"] = sys.executable
            r = subprocess.run([other, os.path.abspath(__file__), "--browser-only"], env=env)
            check("браузерная часть прошла (" + other + ")", r.returncode == 0)
        else:
            print("  !!! ПРОПУСК: Playwright не найден. Поставить: pip install playwright && "
                  "playwright install chromium")
    shutil.rmtree(TMP, ignore_errors=True)
    if seen:
        print("\nИзменяющие ручки, которые обошли (%d):" % len(seen))
        for s in seen:
            print("  " + s)
    print()
    if FAILS:
        print("ПРОВАЛЫ (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("Проход 46: все проверки пройдены.")


if __name__ == "__main__":
    run()
