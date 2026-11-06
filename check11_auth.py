"""Проход 11: вход, сессии, вход по коду, гости, лимиты, админка.

Каждая проверка ниже на прежней версии проваливалась. Что именно было
не так — написано прямо над группой.

Запуск:  python check11_auth.py
"""
import argparse
import os
import sys
import tempfile
import time

# Эти проверки написаны под личный режим: они подменяют настоящие
# источники (ru_a, ru_b…), которых в демо-режиме нет.
# Режим ставим здесь, а не берём из окружения, — иначе запуск при
# MODE=demo падал бы не из-за ошибок, а из-за чужих условий.
# Демо-режим проверяет check10_demo.py.
os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p11.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass11"
# Адрес посетителя берём из X-Real-IP, как за настоящим nginx: так один
# клиент может притвориться разными адресами.
os.environ["TRUST_PROXY"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging                                      # noqa: E402
for _n in ("anime", "httpx", "anime.catalog", "anime.sources"):
    logging.getLogger(_n).setLevel(logging.ERROR)

from fastapi.testclient import TestClient           # noqa: E402
from api import admin, anime, main, security, store, twofa   # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
ADMIN_PASS = "Zaliv-Pepel-2026"
USER_PASS = "Tihiy-Signal-2026"


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)


def reset():
    main.login_guard.reset()
    for lim in (main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.pass_limit,
                main.avatar_limit, main.catalog_limit):
        lim.reset()
    main._cookie_sent.clear()


def клиент(ip="198.51.100.1"):
    c = TestClient(main.app)
    c.headers["X-Real-IP"] = ip
    return c


def войти(c, логин, пароль, код=""):
    r = c.post("/api/auth/login", json={"login": логин, "password": пароль, "code": код})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def гостем(c):
    r = c.post("/api/auth/guest", json={})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def код_сейчас(секрет, сдвиг=0):
    return twofa._code_at(секрет, int(time.time() // twofa.STEP) + сдвиг)


def куки_сессии(r):
    return [x for x in r.headers.get_list("set-cookie") if x.startswith("sid=")]


def max_age(строка):
    for часть in строка.split(";"):
        часть = часть.strip()
        if часть.lower().startswith("max-age="):
            return int(часть.split("=", 1)[1])
    return None


def включить_код(c, пароль):
    r = c.post("/api/me/2fa/start", json={"password": пароль})
    секрет = r.json()["secret"]
    r = c.post("/api/me/2fa/enable", json={"code": код_сейчас(секрет)})
    return секрет, r


# ==========================================================================
def run():
    store.init()
    reset()
    store.create_user("hozyain", ADMIN_PASS, "admin", "hozyain")
    store.create_user("misha", USER_PASS, "user", "misha")
    store.create_user("kate", USER_PASS, "user", "kate")
    store.create_user("olya", USER_PASS, "user", "olya")

    # ------------------------------------------------------------------
    # 1. Вход по коду включался без пароля и поверх уже включённого:
    #    кто подсел за незапертый ноутбук, ставил код на свой телефон.
    group("1. Включение входа по коду требует пароль")
    reset()
    c = клиент()
    войти(c, "misha", USER_PASS)
    r = c.post("/api/me/2fa/start", json={})
    check("без пароля настройка не начинается", r.status_code == 422, r.status_code)
    check("и черновика нет", store.get_user_by_login("misha")["id"] not in main.pending_2fa)
    reset()
    r = c.post("/api/me/2fa/start", json={"password": "Ne-Tot-Parol-2026"})
    check("с чужим паролем не начинается", r.status_code == 403, r.status_code)
    check("и черновика нет", store.get_user_by_login("misha")["id"] not in main.pending_2fa)
    reset()
    r = c.post("/api/me/2fa/enable", json={"code": "123456"})
    check("включить без черновика нельзя", r.status_code == 409, r.status_code)
    reset()
    секрет, r = включить_код(c, USER_PASS)
    check("с паролем включается", r.status_code == 200, r.status_code)
    reset()
    r = c.post("/api/me/2fa/start", json={"password": USER_PASS})
    check("поверх включённого заново не начать", r.status_code == 409, r.status_code)
    # Секрет в базе теперь зашифрован (security.seal, аудит BE-06): сверяем
    # расшифрованный, а не строку из базы как есть.
    check("секрет в базе остался прежним",
          store.totp_secret(store.get_user_by_login("misha")) == секрет)
    check("и лежит в базе зашифрованным",
          store.get_user_by_login("misha")["totp_secret"].startswith("s1:")
          and секрет not in store.get_user_by_login("misha")["totp_secret"])
    reset()
    check("метка формы по-прежнему нужна",
          клиент().post("/api/me/2fa/start", json={"password": USER_PASS},
                        cookies={"sid": c.cookies.get("sid")}).status_code == 403)

    # ------------------------------------------------------------------
    # 2. Один и тот же код пускал повторно до полутора минут.
    group("2. Код из приложения пускает один раз")
    reset()
    свежий = код_сейчас(секрет)
    r = войти(клиент(), "misha", USER_PASS, свежий)
    check("код пускает", r.status_code == 200, r.status_code)
    reset()
    r = войти(клиент(), "misha", USER_PASS, свежий)
    check("тот же код второй раз не пускает", r.status_code == 401, r.status_code)
    check("и сказано почему", "использован" in r.json().get("detail", ""), r.json())
    reset()
    r = войти(клиент(), "misha", USER_PASS, код_сейчас(секрет, -1))
    check("код постарше уже не пускает", r.status_code == 401, r.status_code)
    reset()
    r = войти(клиент(), "misha", USER_PASS, код_сейчас(секрет, 1))
    check("следующий код пускает", r.status_code == 200, r.status_code)
    uid = store.get_user_by_login("misha")["id"]
    шаг = int(store.get_user(uid)["totp_last_step"])
    check("шаг запомнен в базе", шаг == int(time.time() // twofa.STEP) + 1, шаг)
    check("гонка: тот же шаг не занять дважды",
          store.claim_totp_step(uid, шаг) is False and store.claim_totp_step(uid, шаг + 1) is True)
    check("столбец заводится при обновлении старой базы",
          ("users", "totp_last_step", "INTEGER NOT NULL DEFAULT 0") in store.LATER_COLUMNS)

    # ------------------------------------------------------------------
    # 3. Включение кода не трогало уже открытые сессии — в том числе
    #    чужую, если пароль утёк.
    group("3. Включение кода закрывает остальные входы")
    reset()
    a, b, d = клиент(), клиент("203.0.113.5"), клиент("203.0.113.6")
    for x in (a, b, d):
        войти(x, "kate", USER_PASS)
    check("три входа живы", all(x.get("/api/me").status_code == 200 for x in (a, b, d)))
    _, r = включить_код(a, USER_PASS)
    check("код включён", r.status_code == 200, r.status_code)
    check("сказано, сколько входов закрыто", r.json().get("closed") == 2, r.json().get("closed"))
    check("тот, кто включал, остался внутри", a.get("/api/me").status_code == 200)
    check("второй вход закрыт", b.get("/api/me").status_code == 401)
    check("третий вход закрыт", d.get("/api/me").status_code == 401)

    # ------------------------------------------------------------------
    # 4. Восемь неверных паролей откуда угодно запирали логин для всех,
    #    включая владельца с верным паролем.
    group("4. Чужой перебор не запирает владельца")
    reset()
    злой = клиент("192.0.2.66")
    коды = [войти(злой, "hozyain", f"podbor-{i}-2026").status_code for i in range(20)]
    check("перебор с одного адреса упирается в блокировку", 429 in коды, коды)
    check("и не позже восьмой попытки", коды.index(429) <= 8 if 429 in коды else False)
    check("верный пароль с того же адреса тоже ждёт",
          войти(клиент("192.0.2.66"), "hozyain", ADMIN_PASS).status_code == 429)
    хозяин = клиент("198.51.100.77")
    r = войти(хозяин, "hozyain", ADMIN_PASS)
    check("владелец с другого адреса входит", r.status_code == 200, r.status_code)

    # Перебор одного логина с сотни адресов: каждой паре — по разу.
    reset()
    войти(клиент("198.51.100.77"), "hozyain", ADMIN_PASS)     # адрес владельца знаком
    for i in range(main.login_guard.MAX_PER_LOGIN + 5):
        войти(клиент(f"10.9.{i // 200}.{i % 200 + 1}"), "hozyain", f"x-{i}-podbor")
    r = войти(клиент("10.200.0.1"), "hozyain", "eshche-odin-2026")
    check("распределённый перебор упирается в общую паузу", r.status_code == 429, r.status_code)
    wait = int(r.headers.get("Retry-After", "0"))
    check("но это пауза, а не запирание на четверть часа",
          0 < wait <= main.login_guard.LOGIN_PAUSE, wait)
    r = войти(клиент("198.51.100.77"), "hozyain", ADMIN_PASS)
    check("с адреса, где владелец уже входил, пускает", r.status_code == 200, r.status_code)

    # Один пароль ко многим логинам с одного адреса — счётчик адреса.
    reset()
    коды = [войти(клиент("192.0.2.99"), f"nosuch{i:03d}", "Password-12345").status_code
            for i in range(40)]
    check("один пароль ко многим логинам упирается в адрес", 429 in коды)

    # ------------------------------------------------------------------
    # 5. Гостевой пропуск выдавался кому угодно: форма с чужого сайта
    #    подменяла куку сессии гостевой.
    group("5. Гостевой пропуск не подменяет сессию")
    reset()
    g = клиент()
    r = g.post("/api/auth/guest", data={"x": "1"})
    check("обычная форма отклонена", r.status_code == 403, r.status_code)
    check("и куки не выдано", not куки_сессии(r))
    r = g.post("/api/auth/guest", content=b"{}", headers={"Content-Type": "text/plain"})
    check("text/plain отклонён", r.status_code == 403, r.status_code)
    r = g.post("/api/auth/guest")
    check("пустой запрос отклонён", r.status_code == 403, r.status_code)
    r = гостем(g)
    check("как шлёт наша страница — пропускает", r.status_code == 200, r.status_code)
    sid = g.cookies.get("sid")
    r = g.post("/api/auth/guest", json={})
    check("гостю второй пропуск не выдаётся", r.status_code == 200 and not куки_сессии(r))
    check("гость остался со своим", g.cookies.get("sid") == sid)

    reset()
    u = клиент()
    войти(u, "olya", USER_PASS)
    sid = u.cookies.get("sid")
    r = u.post("/api/auth/guest", json={})
    check("вошедшему гостевой не выдаётся", r.status_code == 409, r.status_code)
    check("кука сессии не тронута", not куки_сессии(r) and u.cookies.get("sid") == sid)
    check("человек остался в аккаунте", u.get("/api/me").json().get("kind") == "user")

    # ------------------------------------------------------------------
    # 6. Лимиты: лазейка в /api/updates/seen, лимит на каждую сессию
    #    вместо человека и «одно обращение» за десяток походов наружу.
    group("6. Лимиты частоты")
    старые = (anime.find_episodes, main.try_source)

    async def без_сети(*a, **k):
        return []

    anime.find_episodes = без_сети
    main.try_source = без_сети
    try:
        reset()
        u = клиент()
        войти(u, "olya", USER_PASS)
        коды = [u.post("/api/updates/seen",
                       json={"key": f"k{i}", "source": "ru_a"}).status_code
                for i in range(main.source_limit.limit + 5)]
        check("/api/updates/seen ограничен", 429 in коды,
              f"200: {коды.count(200)}, 429: {коды.count(429)}")
        check("ровно по общему пределу", коды.count(200) == main.source_limit.limit,
              коды.count(200))

        reset()
        входы = [клиент() for _ in range(3)]
        for x in входы:
            войти(x, "olya", USER_PASS)
        коды = [входы[i % 3].get("/api/me").status_code
                for i in range(main.api_limit.limit + 3)]
        check("три входа одного человека делят один лимит",
              коды.count(200) == main.api_limit.limit,
              f"200: {коды.count(200)}, 429: {коды.count(429)}")

        reset()
        гости = [клиент("192.0.2.150") for _ in range(3)]
        for x in гости:
            гостем(x)
        коды = [гости[i % 3].get("/api/news").status_code
                for i in range(main.api_limit.limit + 3)]
        check("гости с одного адреса делят один лимит",
              коды.count(200) == main.api_limit.limit,
              f"200: {коды.count(200)}, 429: {коды.count(429)}")
        другой = клиент("192.0.2.151")
        гостем(другой)
        check("гость с другого адреса не страдает", другой.get("/api/news").status_code == 200)

        # resolve без кэша: четыре источника по двум названиям — восемь
        # походов наружу. Предел двадцать, значит третий такой вызов — уже 429.
        # (С запасным кругом к узким источникам первый вызов стоит больше
        # восьми, и 429 приходит уже на второй — проверка это допускает.)
        reset()
        anime._cache.clear()
        q = "/api/resolve?title=Nichego-Takogo&title_en=Nothing-Such&lang=ru"
        коды = [u.get(q).status_code for _ in range(3)]
        check("resolve мимо кэша стоит по числу походов", коды[:2] != [429, 429] and коды[2] == 429,
              коды)
        reset()
        # Кэшируем ответы всех источников, а не только основных: тайтла
        # нет нигде, и после основных резолв идёт в запасной круг к узким
        # (RU-E и др., см. api_resolve). Их походы мимо кэша — тоже
        # настоящие обращения наружу и честно списываются с лимита.
        for s in dict.fromkeys(main.fallback_for("ru") + anime.sources_for("ru")):
            for t in ("nichego-takogo", "nothing-such"):
                anime.cache_put(f"search:{s}:{t}", [])
        коды = [u.get(q).status_code for _ in range(10)]
        check("тот же resolve из кэша стоит одно обращение", 429 not in коды, коды)

        reset()
        anime._cache.clear()
        q = "/api/where?title=Gde-To&title_en=Somewhere&lang=ru"
        коды = [u.get(q).status_code for _ in range(3)]
        check("where мимо кэша тоже", коды[2] == 429, коды)

        reset()
        anime._cache.clear()
        q = "/api/subs?title=Sub-Gde&title_en=Sub-Where&lang=ru"
        коды = [u.get(q).status_code for _ in range(4)]
        check("subs мимо кэша тоже", 429 in коды, коды)
        check("цена считается от промахов",
              main.uncached_lookups(["a", "b"], ("x", " ", "")) == 2
              and main.uncached_lookups([], ()) == 1)
    finally:
        anime.find_episodes, main.try_source = старые
        anime._cache.clear()

    # ------------------------------------------------------------------
    # 7. Запасной код принимался только буква в букву, с дефисом.
    group("7. Запасной код в любом написании")
    код = "ab12-cd34"
    for вариант in ("AB12-CD34", "ab12cd34", "ab12 cd34", " ab12 - cd34 ", "ab12–cd34",
                    "ab12—cd34", "ＡＢ12ｃｄ34"):
        check("принят: %r" % вариант, twofa.hash_backup(вариант) == twofa.hash_backup(код))
    check("чужой не принят", twofa.hash_backup("ab12cd35") != twofa.hash_backup(код))
    check("старые отпечатки (с дефисом) совпадают",
          twofa.hash_backup(код) == __import__("hashlib").sha256(
              (os.environ["SESSION_PEPPER"] + "backup|" + код).encode()).hexdigest())

    reset()
    c = клиент()
    войти(c, "olya", USER_PASS)
    _, r = включить_код(c, USER_PASS)
    запасной = r.json()["backup"][0]
    reset()
    r = войти(клиент(), "olya", USER_PASS, запасной.replace("-", " ").upper())
    check("вход по запасному коду без дефиса и заглавными", r.status_code == 200, r.status_code)
    reset()
    r = войти(клиент(), "olya", USER_PASS, запасной)
    check("и он одноразовый", r.status_code == 401, r.status_code)

    # ------------------------------------------------------------------
    # 8. Кука получала срок только при входе: через две недели браузер
    #    её выбрасывал, хотя сессия в базе была живой.
    group("8. Кука сессии продлевается")
    reset()
    c = клиент()
    r = войти(c, "hozyain", ADMIN_PASS)
    check("при входе кука на две недели",
          max_age(куки_сессии(r)[0]) == security.SESSION_IDLE, куки_сессии(r))
    r = c.get("/api/me")
    check("сразу после входа не переотправляется", not куки_сессии(r))
    token = c.cookies.get("sid")
    fp = security.token_fingerprint(token)
    main._cookie_sent[fp] = time.time() - main.COOKIE_SLIDE_EVERY - 5
    r = c.get("/api/me")
    check("через сутки переотправляется", len(куки_сессии(r)) == 1, куки_сессии(r))
    check("на полный срок простоя",
          куки_сессии(r) and abs(max_age(куки_сессии(r)[0]) - security.SESSION_IDLE) <= 2)
    check("с ней метка формы", any(x.startswith("csrf=") for x in r.headers.get_list("set-cookie")))
    r = c.get("/api/me")
    check("и снова молчит до следующих суток", not куки_сессии(r))
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET created_at = ? WHERE fp = ?",
                     (int(time.time()) - security.SESSION_MAX_LIFE + 3600, fp))
    main._cookie_sent[fp] = 0
    r = c.get("/api/me")
    срок = max_age(куки_сессии(r)[0]) if куки_сессии(r) else None
    check("не дальше предельного срока сессии", срок is not None and срок <= 3600, срок)
    r = c.post("/api/auth/logout")
    check("выход куку стирает, продление не мешает",
          куки_сессии(r) and max_age(куки_сессии(r)[0]) in (0, None)
          and c.get("/api/me").status_code == 401)

    # ------------------------------------------------------------------
    # 9. Выключенного администратора нельзя было ни удалить, ни выключить.
    group("9. Выключенный администратор")
    reset()
    store.create_user("boss2", ADMIN_PASS, "admin", "boss2")
    store.create_user("boss3", ADMIN_PASS, "admin", "boss3")
    b2 = store.get_user_by_login("boss2")["id"]
    b3 = store.get_user_by_login("boss3")["id"]
    store.set_disabled(b2, True)
    store.set_disabled(b3, True)
    шеф = клиент()
    войти(шеф, "hozyain", ADMIN_PASS)
    check("действующий админ один", store.count_admins() == 1)
    r = шеф.post(f"/api/admin/users/{b2}/disable")
    check("выключенного можно выключить ещё раз", r.status_code == 200, r.status_code)
    r = шеф.request("DELETE", f"/api/admin/users/{b2}")
    check("выключенного можно удалить", r.status_code == 200, r.status_code)
    check("и он удалён", store.get_user(b2) is None)
    try:
        admin.cmd_disable(argparse.Namespace(login="boss3"))
        вышло = True
    except SystemExit:
        вышло = False
    check("из консоли выключенного тоже можно", вышло)
    check("последний действующий по-прежнему под защитой",
          store.is_last_admin(store.get_user_by_login("hozyain")))
    store.set_disabled(b3, False)
    check("когда действующих двое — не последний",
          not store.is_last_admin(store.get_user_by_login("hozyain")))
    store.delete_user(b3)

    # ------------------------------------------------------------------
    # 10. Админка выдавала себя кодами 405 и 422 раньше, чем срабатывала
    #     проверка роли.
    group("10. Для чужих админки нет вовсе")
    reset()
    никто = клиент()
    гость = клиент("192.0.2.200")
    гостем(гость)
    простой = клиент()
    войти(простой, "olya", USER_PASS)
    ничего = никто.get("/api/nichego/net")
    check("образец: несуществующий адрес — 404", ничего.status_code == 404)
    запросы = [
        ("GET", "/api/admin/news", None),
        ("PUT", "/api/admin/users", None),
        ("PATCH", "/api/admin/users/1/disable", None),
        ("POST", "/api/admin/users", b"{broken"),
        ("POST", "/api/admin/news", b"\xff\xfe"),
        ("GET", "/api/admin/users/", None),
        ("GET", "/api/admin", None),
        ("DELETE", "/api/admin/users/abc", None),
        ("GET", "/api/admin/users", None),
    ]
    for кто, c in (("аноним", никто), ("гость", гость), ("пользователь", простой)):
        for метод, путь, тело in запросы:
            r = c.request(метод, путь, content=тело,
                          headers={"Content-Type": "application/json"} if тело else None)
            check(f"{кто}: {метод} {путь} → 404, как у несуществующего",
                  r.status_code == 404 and r.json() == ничего.json(),
                  (r.status_code, r.text[:60]))
    r = никто.get("/api/admin/users")
    check("у ответа те же защитные заголовки",
          r.headers.get("X-Frame-Options") == "DENY" and "Content-Security-Policy" in r.headers)
    reset()
    шеф = клиент()
    войти(шеф, "hozyain", ADMIN_PASS)
    check("администратору список открыт", шеф.get("/api/admin/users").status_code == 200)
    без_метки = клиент()
    без_метки.cookies.set("sid", шеф.cookies.get("sid"))
    check("администратору без метки формы писать нельзя",
          без_метки.post("/api/admin/news", json={"text": "x"}).status_code == 403)
    check("администратору кривое тело — честная 422",
          шеф.post("/api/admin/users", content=b"{broken",
                   headers={"Content-Type": "application/json"}).status_code == 422)

    # ------------------------------------------------------------------
    # 11. Переключатель кода оставался «вкл», если настройку бросили.
    #     Здесь — только наличие механизма; поведение в браузере
    #     проверяется отдельно.
    group("11. Страница: переключатель кода показывает правду")
    здесь = os.path.dirname(os.path.abspath(__file__))
    js = open(os.path.join(здесь, "web", "index.js"), encoding="utf-8").read()
    check("закрытие кабинета бросает черновик", "dropTwofaDraft(); }" in js
          and "function closeSet() { vset.classList.remove('show'); dropTwofaDraft(); }" in js)
    check("переключатель возвращается к ответу сервера",
          "setCheck('s-2fa', twofaReal)" in js)
    check("перед настройкой спрашивается пароль",
          "A.api.post('/api/me/2fa/start', {password: pass})" in js)

    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 11 — не прошли: %d" % len(FAILS))
        for f in FAILS:
            print("   • " + f)
        return 1
    print("ПРОХОД 11 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
