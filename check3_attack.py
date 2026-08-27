"""Проход 3: пробуем сломать намеренно.

Здесь не «работает ли», а «можно ли обойти»: подмена сессий, гонки,
хитрые кодировки, логические дыры в правах.
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import logging

# Проверки публичной версии идут в демонстрационном режиме: другого
# здесь нет. Внешние источники видео в этот репозиторий не входят, и
# единственный работающий источник — свободное видео (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p3.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass3"
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


def reset():
    main.login_guard.reset()
    main.api_limit.reset()
    main.search_limit.reset()
    main.guest_limit.reset()
    main.write_limit.reset()


def run():
    store.init()
    reset()
    aid = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")
    mid = store.create_user("misha", "Tihiy-Signal-2026", "user", "misha")
    kid = store.create_user("kate", "Dolina-Otrazheniy-26", "user", "kate")

    # ==================================================================
    group("Подмена сессии до входа")
    # Классическая атака: подсунуть жертве заранее известный токен,
    # чтобы после её входа он стал действующим.
    c = C()
    fake = security.new_token()
    c.cookies.set("sid", fake)
    check("свой токен до входа не работает", c.get("/api/me").status_code == 401)
    r = c.post("/api/auth/login", json={"login": "valera", "password": "Zaliv-Pepel-2026"})
    issued = ""
    for raw in r.headers.get_list("set-cookie"):
        if raw.startswith("sid="):
            issued = raw.split(";", 1)[0][4:]
    check("после входа выдан новый токен", issued and issued != fake,
          "совпал!" if issued == fake else "")
    d = C()
    d.cookies.set("sid", fake)
    check("подсунутый токен так и не заработал", d.get("/api/me").status_code == 401)
    # Сервер обязан выдавать куку с явным путём, иначе рядом может
    # ужиться вторая с тем же именем и другим путём.
    paths = [raw for raw in r.headers.get_list("set-cookie")
             if raw.startswith("sid=") and "Path=/" in raw]
    check("кука выдаётся с явным путём", len(paths) == 1, r.headers.get_list("set-cookie"))

    # ==================================================================
    group("Гостевой и пользовательский токены не смешиваются")
    reset()
    g = C()
    rg = g.post("/api/auth/guest")
    gt = [x.split(";", 1)[0][4:] for x in rg.headers.get_list("set-cookie")
          if x.startswith("sid=")][0]
    u = C()
    ru = u.post("/api/auth/login", json={"login": "misha", "password": "Tihiy-Signal-2026"})
    u.headers["X-CSRF-Token"] = ru.json()["csrf"]
    ut = [x.split(";", 1)[0][4:] for x in ru.headers.get_list("set-cookie")
          if x.startswith("sid=")][0]
    x = C(); x.cookies.set("sid", gt)
    check("гостевым токеном не войти как пользователь",
          x.get("/api/library").json().get("guest") is True)
    check("гостевым токеном не открыть статистику", x.get("/api/stats/year").status_code == 403)
    y = C(); y.cookies.set("sid", ut)
    check("пользовательский токен не считается гостевым",
          y.get("/api/me").json().get("role") == "user")
    check("гостевого токена нет в базе",
          store.session_user(gt) is None)

    # ==================================================================
    group("Перебор токенов")
    reset()
    tries = 0
    for i in range(60):
        t = C()
        t.cookies.set("sid", security.new_token())
        if t.get("/api/me").status_code == 200:
            tries += 1
    check("шестьдесят случайных токенов не подошли", tries == 0, tries)
    # длина токена: 32 байта = 256 бит
    check("токен достаточно длинный", len(security.new_token()) >= 43, len(security.new_token()))

    # ==================================================================
    group("Хитрые логины")
    reset()
    tricky = [
        ("VALERA", "верхний регистр — тот же человек", 200),
        ("  valera  ", "пробелы по краям", 200),
        ("valera\u0000", "нулевой байт", 401),
        # Перевод строки по краям — та же лишняя пустота, что и пробел:
        # его убирают намеренно, потому что он часто приезжает из буфера обмена.
        ("valera\n", "перевод строки по краю убирается", 200),
        ("vale\nra", "перевод строки внутри", 401),
        ("valera\u202eadmin", "переворот направления текста", 401),
        ("ＶＡＬＥＲＡ", "полноширинные буквы", 200),
        ("valera'--", "хвост как в SQL", 401),
        ("valerа", "кириллическая а внутри", 401),
    ]
    for value, why, want in tricky:
        reset()
        r = C().post("/api/auth/login", json={"login": value, "password": "Zaliv-Pepel-2026"})
        check(f"{why}: {r.status_code}", r.status_code == want, f"ждали {want}")

    # ==================================================================
    group("Время ответа не выдаёт существующие логины")
    reset()

    def timed(login_name):
        best = 999
        for _ in range(3):
            reset()
            t0 = time.perf_counter()
            C().post("/api/auth/login", json={"login": login_name, "password": "Nevernyy-Parol-26"})
            best = min(best, time.perf_counter() - t0)
        return best

    t_real = timed("valera")
    t_fake = timed("nikogo-net-takogo")
    ratio = max(t_real, t_fake) / max(0.0001, min(t_real, t_fake))
    check("время сопоставимо", ratio < 3, f"{t_real:.3f}с против {t_fake:.3f}с, разница x{ratio:.1f}")

    # ==================================================================
    group("Одновременная запись одного и того же")
    reset()
    w = C()
    login(w, "valera", "Zaliv-Pepel-2026")
    errors = []

    def hammer(n):
        try:
            cc = C()
            cc.cookies = w.cookies
            cc.headers["X-CSRF-Token"] = w.headers["X-CSRF-Token"]
            for i in range(6):
                r = cc.post("/api/library/progress",
                            json={"key": "race", "position": n * 100 + i})
                if r.status_code not in (200, 429):
                    errors.append(r.status_code)
        except Exception as exc:                   # noqa: BLE001
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("одновременные записи не ломают базу", not errors, errors[:4])
    rows = [x for x in w.get("/api/library").json()["items"] if x["key"] == "race"]
    check("запись осталась одна", len(rows) == 1, len(rows))

    # ==================================================================
    group("Права: понижение и удаление администратора")
    reset()
    a = C()
    login(a, "valera", "Zaliv-Pepel-2026")
    check("нельзя удалить себя", a.request("DELETE", f"/api/admin/users/{aid}").status_code == 409)
    check("нельзя выключить последнего администратора",
          a.post(f"/api/admin/users/{aid}/disable").status_code == 409)
    r = a.post("/api/admin/users", json={"login": "vtoroy", "password": "Vtoroy-Admin-2026",
                                         "role": "admin"})
    check("второй администратор создан", r.status_code == 200, r.text[:70])
    # Раньше здесь проверялось обратное: «теперь себя выключить можно».
    # Это была не защита, а описание ошибки, закреплённое проверкой. Пока
    # администратор один, кнопку держит правило «последний администратор»;
    # стоило завести второго — и та же кнопка на своей же строке закрывала
    # вход себе и обрывала собственную сессию. Отдельная проверка на себя,
    # как на удалении, закрывает это насовсем.
    check("себя выключить нельзя даже вдвоём",
          a.post(f"/api/admin/users/{aid}/disable").status_code == 409)
    check("своя сессия цела", a.get("/api/me").status_code == 200)
    vtoroy = store.get_user_by_login("vtoroy")
    check("а другого администратора выключить можно",
          a.post(f"/api/admin/users/{vtoroy['id']}/disable").status_code == 200)
    store.set_disabled(vtoroy["id"], False)

    # ==================================================================
    group("Обычный не может стать администратором")
    reset()
    m = C()
    login(m, "misha", "Tihiy-Signal-2026")
    check("нельзя создать себе админа",
          m.post("/api/admin/users", json={"login": "hax", "password": "Hacker-Parol-2026",
                                           "role": "admin"}).status_code == 404)
    check("нельзя выключить чужой аккаунт",
          m.post(f"/api/admin/users/{kid}/disable").status_code == 404)
    check("роль через настройки не меняется",
          m.post("/api/me/settings", json={"role": "admin"}).status_code == 200)
    check("роль осталась прежней", m.get("/api/me").json()["role"] == "user")
    check("роль через профиль не меняется",
          m.post("/api/me/profile", json={"role": "admin", "display_name": "x"}).status_code == 200)
    check("роль по-прежнему прежняя", m.get("/api/me").json()["role"] == "user")

    # ==================================================================
    group("Чужие данные недостижимы")
    reset()
    k = C()
    login(k, "kate", "Dolina-Otrazheniy-26")
    k.post("/api/library/progress", json={"key": "секрет-кати", "title": "Личное"})
    m2 = C()
    login(m2, "misha", "Tihiy-Signal-2026")
    mine = [x["key"] for x in m2.get("/api/library").json()["items"]]
    check("чужой записи не видно", "секрет-кати" not in mine)
    m2.request("DELETE", "/api/library/секрет-кати")
    still = [x["key"] for x in k.get("/api/library").json()["items"]]
    check("чужой записи не удалить", "секрет-кати" in still)
    a2 = C()
    login(a2, "valera", "Zaliv-Pepel-2026")
    admin_sees = [x["key"] for x in a2.get("/api/library").json()["items"]]
    check("даже администратор не видит чужую полку", "секрет-кати" not in admin_sees)

    # ==================================================================
    group("Выход действительно закрывает вход")
    reset()
    o = C()
    ro = o.post("/api/auth/login", json={"login": "misha", "password": "Tihiy-Signal-2026"})
    o.headers["X-CSRF-Token"] = ro.json()["csrf"]
    tok = [x.split(";", 1)[0][4:] for x in ro.headers.get_list("set-cookie")
           if x.startswith("sid=")][0]
    check("до выхода работает", o.get("/api/me").status_code == 200)
    o.post("/api/auth/logout")
    check("после выхода не работает", o.get("/api/me").status_code == 401)
    z = C(); z.cookies.set("sid", tok)
    check("старый токен мёртв даже в другом браузере", z.get("/api/me").status_code == 401)
    check("сессии нет в базе", store.session_user(tok) is None)

    # ==================================================================
    group("Метка формы: подделка")
    reset()
    f = C()
    login(f, "misha", "Tihiy-Signal-2026")
    real = f.headers["X-CSRF-Token"]
    bad_values = ["", "x", real[:-1], real + "a", real.upper(), "null"]
    for v in bad_values:
        f.headers["X-CSRF-Token"] = v
        code = f.post("/api/library/progress", json={"key": "csrf"}).status_code
        check(f"метка {v[:10] or 'пустая'} отклонена", code == 403, code)
    f.headers["X-CSRF-Token"] = real
    check("настоящая метка работает",
          f.post("/api/library/progress", json={"key": "csrf"}).status_code == 200)

    # ==================================================================
    group("Метка одного не подходит другому")
    reset()
    u1, u2 = C(), C()
    login(u1, "misha", "Tihiy-Signal-2026")
    login(u2, "kate", "Dolina-Otrazheniy-26")
    u1.headers["X-CSRF-Token"] = u2.headers["X-CSRF-Token"]
    check("чужая метка отклонена",
          u1.post("/api/library/progress", json={"key": "x"}).status_code == 403)

    # ==================================================================
    group("Странные запросы")
    reset()
    q = C()
    login(q, "valera", "Zaliv-Pepel-2026")
    check("очень длинный адрес", q.get("/api/library?" + "a=1&" * 2000).status_code in (200, 414, 431))
    check("HEAD не ломает", q.head("/api/health").status_code in (200, 405))
    check("неизвестный метод", q.request("PATCH", "/api/library").status_code in (404, 405))
    check("двойные ключи в json",
          q.post("/api/library/progress",
                 content=b'{"key":"a","key":"b"}',
                 headers={"Content-Type": "application/json"}).status_code in (200, 422))
    check("неверный тип содержимого",
          q.post("/api/library/progress", content=b"key=a",
                 headers={"Content-Type": "text/plain"}).status_code in (400, 415, 422))
    check("сервер жив", C().get("/api/health").status_code == 200)

    # ==================================================================
    group("Просроченные сессии убираются")
    reset()
    e = C()
    re_ = e.post("/api/auth/login", json={"login": "kate", "password": "Dolina-Otrazheniy-26"})
    tok = [x.split(";", 1)[0][4:] for x in re_.headers.get_list("set-cookie")
           if x.startswith("sid=")][0]
    fp = security.token_fingerprint(tok)
    old = store.now() - security.SESSION_IDLE - 10
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE fp = ?", (old, fp))
    check("давно неактивная сессия не пускает", e.get("/api/me").status_code == 401)
    check("и удалена из базы", store.session_user(tok) is None)

    reset()
    e2 = C()
    re2 = e2.post("/api/auth/login", json={"login": "kate", "password": "Dolina-Otrazheniy-26"})
    tok2 = [x.split(";", 1)[0][4:] for x in re2.headers.get_list("set-cookie")
            if x.startswith("sid=")][0]
    fp2 = security.token_fingerprint(tok2)
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET created_at = ? WHERE fp = ?",
                     (store.now() - security.SESSION_MAX_LIFE - 10, fp2))
    check("слишком старая сессия не пускает", e2.get("/api/me").status_code == 401)

    # ==================================================================
    group("Пароль: смена обрывает всё")
    reset()
    d1, d2, d3 = C(), C(), C()
    for cl in (d1, d2, d3):
        login(cl, "kate", "Dolina-Otrazheniy-26")
    check("три входа живы", all(cl.get("/api/me").status_code == 200 for cl in (d1, d2, d3)))
    d1.post("/api/me/password", json={"current": "Dolina-Otrazheniy-26", "new": "Novaya-Dolina-2026"})
    check("остальные два выкинуты",
          d2.get("/api/me").status_code == 401 and d3.get("/api/me").status_code == 401)
    check("тот, кто менял, тоже вышел", d1.get("/api/me").status_code == 401)
    reset()
    check("новый пароль работает",
          C().post("/api/auth/login",
                   json={"login": "kate", "password": "Novaya-Dolina-2026"}).status_code == 200)

    # ==================================================================
    group("Пароль не всплывает в ответах")
    reset()
    pc = C()
    r = login(pc, "valera", "Zaliv-Pepel-2026")
    check("в ответе входа нет пароля", "Zaliv-Pepel-2026" not in r.text)
    check("в ответе входа нет хэша", "pbkdf2" not in r.text)
    check("в /api/me нет пароля", "pbkdf2" not in pc.get("/api/me").text)
    check("в списке аккаунтов нет хэшей", "pbkdf2" not in pc.get("/api/admin/users").text)
    check("в списке аккаунтов нет отпечатков сессий",
          "fp" not in json.dumps(pc.get("/api/admin/users").json()))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 3 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 3 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
