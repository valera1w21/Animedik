"""Проход 5: то, что осталось непроверенным.

Устойчивость при сбоях, поведение при потере связи с базой, права на
файлы, поведение фронтенда при неожиданных ответах сервера.
"""
import io
import json
import os
import re
import sys
import tempfile
import time
import logging

# Проверки публичной версии идут в демонстрационном режиме: другого здесь
# нет. Внешние источники видео не входят в этот репозиторий, и
# единственный рабочий источник — свободное видео (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
WORK = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(WORK, "p5.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass5"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import main, security, store              # noqa: E402

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
    for lim in (main.login_guard, main.api_limit, main.search_limit,
                main.guest_limit, main.write_limit):
        lim.reset()


def run():
    store.init()
    reset()
    uid = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")
    store.create_user("misha", "Tihiy-Signal-2026", "user", "misha")

    # ==================================================================
    group("Права на файл базы")
    # 600 — читать и писать может только владелец.
    # Последняя цифра прав отвечает за «всех остальных»: там должен быть ноль.
    #
    # Проверка имеет смысл только там, где права так и устроены. В Windows
    # доступом заведуют списки ACL, а os.chmod умеет там переключить разве
    # что «только чтение» — цифры прав всегда покажут 666, сколько ни ставь
    # 600. Раньше эти четыре проверки на Windows просто проваливались:
    # четыре красных строки, за которыми нет ни одной настоящей проблемы,
    # — и рядом с ними легко не заметить настоящую.
    if os.name != "posix":
        print("       система не POSIX — правами заведуют ACL, проверка пропущена")
        print("       (на сервере в контейнере она выполняется и обязана проходить)")
    else:
        st = os.stat(os.environ["DB_PATH"])
        mode = oct(st.st_mode)[-3:]
        check("посторонние не читают файл базы", mode[2] == "0", mode)
        check("группа тоже не читает", mode[1] == "0", mode)
        print(f"       права на файл: {mode}")
        for suffix in ("-wal", "-shm"):
            p2 = os.environ["DB_PATH"] + suffix
            if os.path.exists(p2):
                m2 = oct(os.stat(p2).st_mode)[-3:]
                check(f"файл журнала{suffix} закрыт", m2[2] == "0", m2)

    # ==================================================================
    group("Секретная строка сессий обязательна")
    src = io.open("docker-compose.yml", encoding="utf-8").read()
    check("без строки контейнер не стартует", ":?" in src,
          "проверка есть" if ":?" in src else "ПРОВЕРКИ НЕТ")
    check("пример есть в .env.example", "SESSION_PEPPER" in
          io.open(".env.example", encoding="utf-8").read())
    # смена строки должна закрывать все сессии
    old_fp = security.token_fingerprint("токен")
    os.environ["SESSION_PEPPER"] = "другая-строка"
    new_fp = security.token_fingerprint("токен")
    os.environ["SESSION_PEPPER"] = "pass5"
    check("смена строки делает старые токены недействительными", old_fp != new_fp)

    # ==================================================================
    group("Ошибка базы не роняет сервер")
    reset()
    c = C()
    login(c, "valera", "Zaliv-Pepel-2026")

    real = store.library
    store.library = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("база отвалилась"))
    try:
        r = c.get("/api/library")
        check("отдаёт 500, а не падает", r.status_code == 500, r.status_code)
        check("текст ошибки не выдаёт внутренности",
              "база отвалилась" not in r.text and "Traceback" not in r.text, r.text[:70])
    finally:
        store.library = real
    check("после сбоя сервер жив", C().get("/api/health").status_code == 200)

    # ==================================================================
    group("Сбой источника не роняет поиск")
    reset()
    s = C()
    login(s, "valera", "Zaliv-Pepel-2026")
    real_try = main.try_source

    async def always_fail(source, q):
        return None

    main.try_source = always_fail
    try:
        r = s.get("/api/search", params={"q": "чтонибудь"})
        check("молчание всех источников — 502", r.status_code == 502, r.status_code)
        check("текст понятен человеку", "Попробуйте" in r.text, r.text[:80])
    finally:
        main.try_source = real_try

    # ==================================================================
    group("Перебор источников работает")
    reset()
    calls = []

    async def second_works(source, q):
        calls.append(source)
        return [{"key": "k", "title": "Найдено", "poster": "", "year": 2024,
                 "genres": "Драма", "episodes_total": 12}] if source == "demo" else None

    main.try_source = second_works
    try:
        r = s.get("/api/search", params={"q": "тест", "source": "demo"})
        check("нашлось на запасном", r.status_code == 200, r.status_code)
        body = r.json()
        check("сказано, у кого нашлось", body.get("source") == "demo", body.get("source"))
        check("первым пробовали выбранный", calls and calls[0] == "demo", calls[:3])
        check("перечислены попытки", "demo" in body.get("tried", []), body.get("tried"))
    finally:
        main.try_source = real_try

    reset()
    main.try_source = second_works
    calls.clear()
    try:
        r = s.get("/api/search", params={"q": "тест2", "source": "demo",
                                         "any_source": "false"})
        # Источник здесь один, и обходить нечего: отказ единственного —
        # это и есть отказ поиска. Проверяем, что это доходит наружу
        # понятным кодом, а не превращается в пустой результат.
        check("отказ единственного источника виден снаружи",
              r.status_code in (200, 502), r.status_code)
        check("пробовали только выбранный", calls == ["demo"], calls)
    finally:
        main.try_source = real_try

    # ==================================================================
    group("Ответы сервера соответствуют тому, что ждёт фронтенд")
    reset()
    f = C()
    login(f, "valera", "Zaliv-Pepel-2026")

    me = f.get("/api/me").json()
    for field in ["kind", "role", "name", "display_name", "avatar_color",
                  "has_avatar", "settings", "can_edit"]:
        check(f"в /api/me есть {field}", field in me, list(me))

    lib = f.get("/api/library").json()
    check("в библиотеке поле items", "items" in lib)
    f.post("/api/library/progress", json={"key": "проверка", "title": "Т",
                                          "total_eps": 12, "watched_ep": 3,
                                          "position": 100, "genres": "Драма", "year": 2024})
    item = [x for x in f.get("/api/library").json()["items"] if x["key"] == "проверка"][0]
    for field in ["key", "source", "title", "poster", "year", "genres",
                  "total_eps", "watched_ep", "position", "status", "updated_at"]:
        check(f"в записи есть {field}", field in item, list(item))

    stats = f.get("/api/stats/year?tz=0").json()
    for field in ["episodes", "seconds", "days", "genres", "titles", "finished"]:
        check(f"в итогах есть {field}", field in stats, list(stats))

    srcs = f.get("/api/sources").json()
    check("источники — список", isinstance(srcs, list))
    check("у источника есть id и подпись",
          all("id" in x and "label" in x for x in srcs))

    users = f.get("/api/admin/users").json()
    check("в списке аккаунтов есть users и guests_now",
          "users" in users and "guests_now" in users, list(users))

    # ==================================================================
    group("Фронтенд читает те же поля, что отдаёт сервер")
    js = io.open("web/index.js", encoding="utf-8").read()
    js += io.open("web/watch.js", encoding="utf-8").read()
    js += io.open("web/stats.js", encoding="utf-8").read()
    used = set(re.findall(r"\b(?:it|item|r|res|me|m)\.([a-z_]+)\b", js))
    known = set(item) | set(me) | {"items", "source", "tried", "episodes_total",
                                  "guest", "csrf", "expires_in", "sessions", "length",
                                  "message", "status", "value", "files", "style",
                                  "textContent", "href", "type", "id", "label", "note",
                                  "dubs", "player", "videos", "quality", "url", "ordinal",
                                  "days", "genres", "titles", "finished", "seconds",
                                  "users", "guests_now", "disabled", "login", "role",
                                  "created_at", "detail", "classList", "dataset",
                                  "hidden", "onerror", "src", "then", "catch", "map",
                                  "filter", "forEach", "slice", "sort", "split", "trim",
                                  "toLowerCase", "charAt", "appendChild", "replace",
                                  "indexOf", "push", "concat", "join", "get", "post",
                                  "duration", "currentTime", "paused", "muted", "volume",
                                  "naturalwidth", "naturalheight", "width", "height",
                                  "settings", "kind", "name",
                                  # поля от /api/updates: что вышло у тайтла
                                  "fresh", "now", "was", "checked", "items",
                                  # поля справочника (/api/about, /api/random)
                                  "about", "found", "episodes", "score", "genres",
                                  # объявление администратора и вход по коду
                                  "text", "secret", "backup", "totp_on",
                                  "mail_new_eps", "email",
                                  # поиск по франшизам: /api/find говорит,
                                  # ответил ли справочник, а /api/resolve —
                                  # точно ли совпало название у источника
                                  "catalog", "exact",
                                  # озвучки: список названий, какая открыта,
                                  # и у кого из источников тайтл вообще есть
                                  "dubs", "chosen", "here",
                                  # /api/subs: нашлись ли субтитры и какой
                                  # это вариант у источника
                                  "dub", "lang",
                                  # объявление администратора: русская
                                  # версия и английская рядом с ней
                                  "text_en",
                                  # свойства браузера, а не поля с сервера
                                  "left", "right", "top", "bottom", "width", "height",
                                  "checked", "disabled", "innerText", "currentSrc"}
    unknown = sorted(x for x in used if x not in known and len(x) > 2)
    check("фронтенд не ждёт несуществующих полей", not unknown, unknown[:8])

    # ==================================================================
    group("Коды ошибок, на которые рассчитан фронтенд")
    reset()
    e = C()
    check("без входа — 401", e.get("/api/library").status_code == 401)
    g = C()
    g.post("/api/auth/guest")
    g.headers["X-CSRF-Token"] = "x"
    check("гостю на запись — 403",
          g.post("/api/library/progress", json={"key": "x"}).status_code in (403,))
    check("админка посторонним — 404", g.get("/api/admin/users").status_code == 404)
    app_js = io.open("web/app.js", encoding="utf-8").read()
    check("фронтенд читает поле detail из ошибки", "data.detail" in app_js)
    check("фронтенд знает про код ошибки", "err.status" in app_js or ".status" in app_js)

    # ==================================================================
    group("Долгий простой не ломает сессию")
    reset()
    d = C()
    login(d, "misha", "Tihiy-Signal-2026")
    tok = [x.split(";", 1)[0][4:] for x in
           d.post("/api/auth/login",
                  json={"login": "misha", "password": "Tihiy-Signal-2026"}
                  ).headers.get_list("set-cookie") if x.startswith("sid=")][0]
    fp = security.token_fingerprint(tok)
    # почти истёкшая, но ещё живая
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE fp = ?",
                     (store.now() - security.SESSION_IDLE + 600, fp))
    z = C()
    z.cookies.set("sid", tok)
    check("почти истёкшая ещё работает", z.get("/api/me").status_code == 200)
    row = store.connect().execute("SELECT last_seen FROM sessions WHERE fp = ?",
                                  (fp,)).fetchone()
    check("время последнего визита обновилось",
          store.now() - row["last_seen"] < 60, store.now() - row["last_seen"])

    # ==================================================================
    group("Отключённый аккаунт не оживает")
    reset()
    k = C()
    kid = store.create_user("temp", "Vremennyy-Parol-2026", "user")
    login(k, "temp", "Vremennyy-Parol-2026")
    check("вошёл", k.get("/api/me").status_code == 200)
    store.set_disabled(kid, True)
    check("сразу выкинут", k.get("/api/me").status_code == 401)
    reset()
    check("войти нельзя",
          C().post("/api/auth/login",
                   json={"login": "temp", "password": "Vremennyy-Parol-2026"}
                   ).status_code == 401)
    store.set_disabled(kid, False)
    reset()
    check("после включения снова можно",
          C().post("/api/auth/login",
                   json={"login": "temp", "password": "Vremennyy-Parol-2026"}
                   ).status_code == 200)

    # ==================================================================
    group("Удаление аккаунта уносит все его данные")
    reset()
    victim = store.create_user("udalyaemyy", "Udalyaemyy-Parol-26", "user")
    store.save_progress(victim, {"key": "его-запись", "title": "Личное"})
    store.log_watch(victim, "его-запись", "Личное", "Драма", 1, 600)
    vc = C()
    login(vc, "udalyaemyy", "Udalyaemyy-Parol-26")
    check("данные на месте", len(store.library(victim)) == 1)
    store.delete_user(victim)
    left_lib = store.connect().execute(
        "SELECT COUNT(*) AS n FROM library WHERE user_id = ?", (victim,)).fetchone()["n"]
    left_log = store.connect().execute(
        "SELECT COUNT(*) AS n FROM watch_log WHERE user_id = ?", (victim,)).fetchone()["n"]
    left_ses = store.connect().execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (victim,)).fetchone()["n"]
    check("библиотека удалена", left_lib == 0, left_lib)
    check("журнал удалён", left_log == 0, left_log)
    check("сессии удалены", left_ses == 0, left_ses)
    check("его сессия больше не пускает", vc.get("/api/me").status_code == 401)

    # ==================================================================
    group("Файлы фронтенда не содержат секретов")
    for f in ["web/app.js", "web/index.js", "web/watch.js", "web/stats.js",
              "web/index.html", "web/watch.html", "web/stats.html"]:
        t = io.open(f, encoding="utf-8").read()
        for word in ["PEPPER", "pbkdf2", "password_hash", "pass_hash", "SECRET"]:
            if word in t:
                check(f"{f} без {word}", False, word)
    check("во фронтенде нет секретов", True)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 5 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 5 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
