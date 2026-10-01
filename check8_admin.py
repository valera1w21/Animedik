"""Проход 8: до администраторского не добирается никто, кроме админа.

Отличие от прежних наборов: список ручек не выписан руками, а берётся
из самого приложения. Поэтому проверка не может отстать от кода — если
завтра появится новая ручка, она попадёт сюда сама, и если её забудут
закрыть, набор упадёт.

Проверяется три вещи:
  1. Каждая ручка под /api/admin/ отвечает «не найдено» всем, кроме
     администратора, — и при этом действительно ничего не делает.
  2. У каждой непубличной ручки есть охрана, и охрана эта настоящая:
     проверяется не наличие строчки в коде, а ответ сервера.
  3. Права нельзя получить в обход: ни через свой профиль, ни через
     настройки, ни чужой сессией, ни подделав метку формы.

Запуск:  python check8_admin.py
"""
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p8.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass8"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging                                      # noqa: E402
for _n in ("anime", "httpx", "anime.catalog", "anime.sources"):
    logging.getLogger(_n).setLevel(logging.ERROR)

from fastapi.testclient import TestClient           # noqa: E402
from api import main, store                         # noqa: E402

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


def клиент():
    return TestClient(main.app)


def войти(c, логин, пароль):
    r = c.post("/api/auth/login", json={"login": логин, "password": пароль})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def гостем(c):
    r = c.post("/api/auth/guest")
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


# ==========================================================================
# Список ручек берём у самого приложения
# ==========================================================================
def ручки():
    """Все ручки /api: путь, методы и имя охраны."""
    из_кода = []
    for r in main.app.routes:
        путь = getattr(r, "path", "")
        методы = sorted(m for m in (getattr(r, "methods", None) or ())
                        if m not in ("HEAD", "OPTIONS"))
        if not путь.startswith("/api") or not методы:
            continue
        охрана = ""
        зав = getattr(r, "dependant", None)
        for d in (зав.dependencies if зав else []):
            имя = getattr(d.call, "__name__", "")
            if имя in ("need_admin", "need_user", "need_any"):
                охрана = имя
        из_кода.append((путь, методы, охрана))
    return sorted(из_кода)


# Ручки, которым охрана не нужна по смыслу. Всё остальное обязано быть
# закрытым — если завтра добавят ручку и забудут охрану, набор упадёт.
ОТКРЫТЫЕ = {
    "/api/health",        # проверка живости для nginx и docker
    "/api/auth/login",    # вход
    "/api/auth/guest",    # гостевой пропуск
    "/api/auth/logout",   # выход: закрывать нечего
    "/api/me",            # отвечает «не вошли» вместо ошибки — так задумано
    "/api/mode",          # одно слово «демо или нет»: плашка видна до входа
}


def подставить(путь):
    """Заменяет {параметры} в пути на что-нибудь допустимое."""
    return (путь.replace("{user_id}", "999999")
                .replace("{key:path}", "kakoy-to-klyuch")
                .replace("{key}", "kakoy-to-klyuch"))


def дёрнуть(c, метод, путь):
    тело = {"login": "vzlom", "password": "Ochen-Slozhnyy-2026", "role": "admin",
            "text": "захвачено", "key": "k", "title": "t"}
    if метод == "GET":
        return c.get(путь)
    if метод == "DELETE":
        return c.request("DELETE", путь, json=тело)
    return c.request(метод, путь, json=тело)


# ==========================================================================
def run():
    store.init()
    reset()

    админ = store.create_user("valera", ADMIN_PASS, "admin", "Валера")
    обычный = store.create_user("misha", USER_PASS, "user", "Миша")
    жертва = store.create_user("olya", "Spokoynyy-Veter-2026", "user", "Оля")
    check("подопытные созданы", админ and обычный and жертва)

    все = ручки()
    админские = [(п, м) for п, м, _ in все if п.startswith("/api/admin/")]
    print("\nручек всего: %d, из них администраторских: %d"
          % (len(все), len(админские)))

    # ------------------------------------------------------------------
    group("У каждой непубличной ручки есть охрана")
    голые = [(п, м) for п, м, о in все if not о and п not in ОТКРЫТЫЕ]
    check("ручек без охраны нет", not голые, голые or "")
    for путь, методы, охрана in все:
        if путь.startswith("/api/admin/"):
            check("охрана admin у " + путь, охрана == "need_admin", охрана or "нет")

    # ------------------------------------------------------------------
    group("Администраторские ручки: никого, кроме админа")

    reset()
    аноним = клиент()
    гость = клиент()
    гостем(гость)
    юзер = клиент()
    войти(юзер, "misha", USER_PASS)

    for кто, c in (("без входа", аноним), ("гость", гость), ("обычный", юзер)):
        for путь, методы in админские:
            for метод in методы:
                r = дёрнуть(c, метод, подставить(путь))
                check("%s: %s %s → не найдено" % (кто, метод, путь),
                      r.status_code == 404, r.status_code)

    # ------------------------------------------------------------------
    group("Отказ ничего не поменял в базе")

    было = len(store.list_users())
    for кто, c in (("без входа", аноним), ("гость", гость), ("обычный", юзер)):
        c.post("/api/admin/users", json={"login": "vzlom", "password": "Ochen-Slozhnyy-2026",
                                         "role": "admin"})
        c.request("DELETE", "/api/admin/users/%d" % жертва)
        c.post("/api/admin/users/%d/disable" % жертва)
        c.post("/api/admin/news", json={"text": "захвачено"})
    стало = len(store.list_users())
    check("аккаунтов столько же", было == стало, "%d → %d" % (было, стало))
    check("аккаунт «vzlom» не создан", store.get_user_by_login("vzlom") is None)
    check("жертва на месте", store.get_user(жертва) is not None)
    check("жертва не отключена", store.get_user(жертва)["disabled"] == 0)
    check("объявление не появилось", not (store.get_news() or {}).get("text"))

    # ------------------------------------------------------------------
    group("Администратор всё это может")

    reset()
    шеф = клиент()
    войти(шеф, "valera", ADMIN_PASS)
    r = шеф.get("/api/admin/users")
    check("список аккаунтов открыт", r.status_code == 200, r.status_code)
    r = шеф.post("/api/admin/news", json={"text": "Работы в субботу"})
    check("объявление ставится", r.status_code == 200, r.status_code)
    check("и видно другим", (store.get_news() or {}).get("text") == "Работы в субботу")
    check("объявление снимается",
          шеф.request("DELETE", "/api/admin/news").status_code == 200)

    # ------------------------------------------------------------------
    group("Права нельзя выписать себе самому")

    reset()
    юзер = клиент()
    войти(юзер, "misha", USER_PASS)

    for поле, значение in (("role", "admin"), ("is_admin", True), ("admin", 1)):
        юзер.post("/api/me/profile", json={"display_name": "Миша", поле: значение})
        юзер.post("/api/me/settings", json={"accent": "mint", поле: значение})
    check("роль осталась обычной", store.get_user(обычный)["role"] == "user",
          store.get_user(обычный)["role"])
    check("админские ручки по-прежнему закрыты",
          юзер.get("/api/admin/users").status_code == 404)

    # ------------------------------------------------------------------
    group("Чужую сессию не переиспользовать")

    reset()
    шеф = клиент()
    войти(шеф, "valera", ADMIN_PASS)
    админ_метка = шеф.headers["X-CSRF-Token"]

    чужой = клиент()
    войти(чужой, "misha", USER_PASS)
    # метка формы от администратора со своей сессией
    чужой.headers["X-CSRF-Token"] = админ_метка
    check("чужая метка формы не открывает админку",
          чужой.get("/api/admin/users").status_code == 404)
    check("и не даёт писать объявление",
          чужой.post("/api/admin/news", json={"text": "х"}).status_code == 404)

    # кука админа без его метки формы
    подмена = клиент()
    войти(подмена, "misha", USER_PASS)
    # Значение куки — только ASCII: кириллица в заголовке роняет сам
    # запрос, и проверка провалилась бы, не дойдя до сервера.
    подмена.cookies.set("sid", "poddelnyy-token-administratora-0123456789")
    check("выдуманная кука не пускает",
          подмена.get("/api/admin/users").status_code == 404)

    # ------------------------------------------------------------------
    group("Гость не может даже того, что может обычный")

    reset()
    гость = клиент()
    гостем(гость)
    для_гостя_закрыто = [
        ("POST", "/api/library/progress", {"key": "k", "title": "t"}),
        ("POST", "/api/library/watched", {"key": "k", "ep": 1}),
        ("POST", "/api/me/profile", {"display_name": "х"}),
        ("POST", "/api/me/settings", {"accent": "sky"}),
        ("POST", "/api/me/password", {"current": "a", "new": "Novyy-Parol-2026"}),
        ("POST", "/api/me/mail", {"on": True, "email": "a@b.cd"}),
        ("POST", "/api/me/2fa/start", {}),
        ("POST", "/api/library/poster", {"key": "k", "poster": "https://x/y.jpg"}),
        ("POST", "/api/updates/seen", {}),
        ("POST", "/api/auth/logout-all", {}),
        ("GET", "/api/stats/year", None),
        ("GET", "/api/updates", None),
    ]
    for метод, путь, тело in для_гостя_закрыто:
        r = гость.get(путь) if метод == "GET" else гость.post(путь, json=тело)
        check("гостю закрыто: %s" % путь, r.status_code == 403, r.status_code)

    # ------------------------------------------------------------------
    group("Без входа закрыто всё, кроме входа")

    reset()
    аноним = клиент()
    for путь, методы, охрана in все:
        if путь in ОТКРЫТЫЕ or not охрана:
            continue
        метод = "GET" if "GET" in методы else методы[0]
        r = дёрнуть(аноним, метод, подставить(путь))
        ждём = (404,) if охрана == "need_admin" else (401,)
        check("без входа %s %s" % (метод, путь), r.status_code in ждём, r.status_code)

    # ------------------------------------------------------------------
    group("Отключённый администратор перестаёт быть администратором")

    reset()
    шеф = клиент()
    войти(шеф, "valera", ADMIN_PASS)
    check("пока включён — админка открыта", шеф.get("/api/admin/users").status_code == 200)
    store.set_disabled(админ, True)
    check("после отключения — закрыта", шеф.get("/api/admin/users").status_code == 404)
    store.set_disabled(админ, False)

    # ------------------------------------------------------------------
    group("С включённым кодом одного пароля мало")

    # Смотрим не на текст ответа, а на то, выдана ли сессия: только это
    # и решает, попал человек в аккаунт или нет.
    import time as _time
    from api import twofa

    reset()
    c = клиент()
    войти(c, "olya", "Spokoynyy-Veter-2026")
    r = c.post("/api/me/2fa/start")
    check("настройка началась", r.status_code == 200, r.status_code)
    секрет = r.json()["secret"]
    свой = twofa._code_at(секрет, int(_time.time() // twofa.STEP))
    r = c.post("/api/me/2fa/enable", json={"code": свой})
    check("код включён", r.status_code == 200, r.status_code)
    запасные = r.json().get("backup") or []
    check("выданы запасные коды", len(запасные) >= 4, len(запасные))

    def попытка(тело):
        к = клиент()
        о = к.post("/api/auth/login", json=тело)
        сессия = "sid" in к.cookies
        закрыто = к.get("/api/library").status_code
        return о, сессия, закрыто

    reset()
    о, сессия, закрыто = попытка({"login": "olya", "password": "Spokoynyy-Veter-2026"})
    check("верный пароль без кода не пускает", о.status_code == 401, о.status_code)
    check("сессия не выдана", not сессия)
    check("библиотека закрыта", закрыто == 401, закрыто)
    check("сказано, что нужен именно код", о.headers.get("X-Need-Code") == "1")

    for подпись, значение in (
            ("чужой код", "000000"),
            ("код на минуту назад", twofa._code_at(секрет, int(_time.time() // twofa.STEP) - 3)),
            ("буквы вместо цифр", "абвгде"),
            ("длинный мусор", "9" * 30)):
        reset()
        о, сессия, _ = попытка({"login": "olya", "password": "Spokoynyy-Veter-2026",
                                "code": значение})
        check("%s не пускает" % подпись, о.status_code == 401, о.status_code)
        check("%s — сессии нет" % подпись, not сессия)

    reset()
    о, сессия, _ = попытка({"login": "olya", "password": "Sovsem-Ne-Tot-2026",
                            "code": twofa._code_at(секрет, int(_time.time() // twofa.STEP))})
    check("верный код без верного пароля не пускает", о.status_code == 401, о.status_code)
    check("сессии нет", not сессия)

    reset()
    о, сессия, открыто = попытка({"login": "olya", "password": "Spokoynyy-Veter-2026",
                                  "code": twofa._code_at(секрет, int(_time.time() // twofa.STEP))})
    check("со своим кодом входит", о.status_code == 200, о.status_code)
    check("сессия выдана", сессия)
    check("библиотека открылась", открыто == 200, открыто)

    reset()
    о, _, _ = попытка({"login": "olya", "password": "Spokoynyy-Veter-2026",
                       "code": запасные[0]})
    check("запасной код принят", о.status_code == 200, о.status_code)
    reset()
    о, _, _ = попытка({"login": "olya", "password": "Spokoynyy-Veter-2026",
                       "code": запасные[0]})
    check("тот же запасной второй раз не принят", о.status_code == 401, о.status_code)

    # Выключить можно только паролем: иначе защиту снимет любой, кто
    # подсел за незапертый компьютер.
    reset()
    c = клиент()
    войти(c, "olya", "Spokoynyy-Veter-2026")
    check("вход по коду без пароля не выключить",
          c.post("/api/me/2fa/disable", json={"password": "не-тот"}).status_code == 401)
    check("и чужой его не выключит",
          клиент().post("/api/me/2fa/disable",
                        json={"password": "Spokoynyy-Veter-2026"}).status_code == 401)

    # ------------------------------------------------------------------
    group("Совет включить код показывается к месту")

    здесь0 = os.path.dirname(os.path.abspath(__file__))
    js0 = open(os.path.join(здесь0, "web", "index.js"), encoding="utf-8").read()
    html0 = open(os.path.join(здесь0, "web", "index.html"), encoding="utf-8").read()
    check("гостю совет не показывается", "role === 'guest'" in js0 and "me.totp_on" in js0)
    check("у кого уже включён — тоже не показывается", "!me || me.role === 'guest' || me.totp_on" in js0)
    check("есть кнопка, ведущая к переключателю", 'id="toast-go"' in html0 and "s-2fa" in js0)
    check("старая плашка про ИИ убрана", "Сайт собран с помощью ИИ" not in html0)

    # ------------------------------------------------------------------
    group("Праздник назначает администратор")

    reset()
    обычн = клиент()
    войти(обычн, "misha", USER_PASS)
    for праздник in ("newyear", "halloween", "sakura"):
        r = обычн.post("/api/me/settings", json={"holiday": праздник})
        check("обычный не ставит себе «%s»" % праздник, r.status_code == 403, r.status_code)
    # ...но выключить и вернуть «по календарю» может
    for значение, подпись in (("off", "выключить"), ("", "по календарю")):
        r = обычн.post("/api/me/settings", json={"holiday": значение})
        check("обычному доступно «%s»" % подпись, r.status_code == 200, r.status_code)
    check("в базе чужого праздника не появилось",
          (store.get_settings(обычный) or {}).get("holiday") in ("", "off"),
          (store.get_settings(обычный) or {}).get("holiday"))

    reset()
    г = клиент()
    гостем(г)
    check("гость тем более не ставит",
          г.post("/api/me/settings", json={"holiday": "newyear"}).status_code == 403)

    reset()
    шеф = клиент()
    войти(шеф, "valera", ADMIN_PASS)
    for праздник in ("newyear", "halloween", "sakura", "off", ""):
        r = шеф.post("/api/me/settings", json={"holiday": праздник})
        check("администратор ставит «%s»" % (праздник or "по календарю"),
              r.status_code == 200, r.status_code)

    # Спрятанная кнопка — это просьба не нажимать, а не запрет. Поэтому
    # проверяем, что запрет есть и в разметке, и на сервере.
    здесь1 = os.path.dirname(os.path.abspath(__file__))
    html1 = open(os.path.join(здесь1, "web", "index.html"), encoding="utf-8").read()
    js1 = open(os.path.join(здесь1, "web", "index.js"), encoding="utf-8").read()
    main1 = open(os.path.join(здесь1, "api", "main.py"), encoding="utf-8").read()
    check("кнопки праздников помечены как админские",
          html1.count('class="adm-only" data-h=') == 3,
          html1.count('class="adm-only" data-h='))
    check("и прячутся по роли", "#s-holiday .adm-only" in js1)
    check("сервер проверяет то же самое", 'incoming.get("holiday")' in main1)

    # ------------------------------------------------------------------
    group("Кнопки админа не показываются обычному человеку")

    здесь = os.path.dirname(os.path.abspath(__file__))
    js = open(os.path.join(здесь, "web", "index.js"), encoding="utf-8").read()
    html = open(os.path.join(здесь, "web", "index.html"), encoding="utf-8").read()
    check("страница знает про роль admin", "role === 'admin'" in js or 'role == "admin"' in js)
    for id_ in ("btn-news",):
        check("кнопка %s спрятана в разметке" % id_,
              ('id="%s"' % id_) in html and "hidden" in
              html[html.index('id="%s"' % id_) - 200:html.index('id="%s"' % id_) + 200])
    check("скрытие кнопок завязано на роль",
          "isAdmin" in js, "isAdmin" in js)

    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 8 — не прошли: %d" % len(FAILS))
        for f in FAILS:
            print("   • " + f)
        return 1
    print("ПРОХОД 8 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
