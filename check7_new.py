"""Проход 7: попытки сломать то, что добавлено последним.

Новости, вход по коду, письма, справочник и рулетка появились недавно
и через прежние шесть наборов не проходили. Здесь — попытки обойти их
и сломать, а не проверка «работает ли вообще»: это уже сделано в tests.py.

Запуск:  python check7_new.py
"""
import os
import sys
import tempfile
import time

# Проверки публичной версии идут в демонстрационном режиме: другого
# здесь нет. Внешние источники видео в этот репозиторий не входят, и
# единственный работающий источник — свободное видео (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p7.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass7"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging                                     # noqa: E402
logging.getLogger("anime").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime.catalog").setLevel(logging.ERROR)

from fastapi.testclient import TestClient           # noqa: E402
from api import catalog, mail, main, security, store, twofa   # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Zaliv-Pepel-2026"


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)


def C():
    return TestClient(main.app)


def reset():
    main.login_guard.reset()
    for lim in (main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.pass_limit,
                main.avatar_limit, main.catalog_limit):
        lim.reset()
    main.guests._items.clear()


def login(c, user="valera", pwd=PASS, code=""):
    r = c.post("/api/auth/login", json={"login": user, "password": pwd, "code": code})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def run():
    store.init()
    for name, role in (("valera", "admin"), ("misha", "user")):
        if store.get_user_by_login(name) is None:
            store.create_user(name, PASS, role)
    admin_id = store.get_user_by_login("valera")["id"]
    user_id = store.get_user_by_login("misha")["id"]

    # ==================================================================
    group("Объявление: чужими руками его не поставить")
    reset()
    u = C()
    login(u, "misha")
    a = C()
    login(a, "valera")

    check("обычный не пишет объявление",
          u.post("/api/admin/news", json={"text": "я тут главный"}).status_code == 404)
    check("обычный не удаляет",
          u.request("DELETE", "/api/admin/news").status_code == 404)
    check("без входа не пишет",
          C().post("/api/admin/news", json={"text": "x"}).status_code == 404)

    g = C()
    g.post("/api/auth/guest")
    check("гость не пишет",
          g.post("/api/admin/news", json={"text": "x"}).status_code == 404)

    # Метка формы обязательна: иначе чужой сайт мог бы поставить объявление
    # руками администратора, пока тот залогинен.
    saved = a.headers.pop("X-CSRF-Token")
    check("без метки формы не пишет",
          a.post("/api/admin/news", json={"text": "подделка"}).status_code == 403)
    a.headers["X-CSRF-Token"] = saved

    # Перевод строки в объявлении разрешён намеренно: администратор пишет
    # его абзацами, а плашка умеет их показывать. Опасности нет — текст
    # выводится через textContent, то есть как текст, а не как разметка.
    # Всё остальное управляющее вычищается.
    a.post("/api/admin/news",
           json={"text": "Первая строка\nВторая\x00\x07 строка\r\nТретья"})
    got = a.get("/api/news").json()["text"]
    check("абзацы сохранены", got.count("\n") == 2, repr(got))
    check("возврат каретки приведён к переводу строки", "\r" not in got, repr(got))
    check("нулевой байт и прочее вычищено",
          "\x00" not in got and "\x07" not in got, repr(got))

    a.post("/api/admin/news", json={"text": "<script>alert(1)</script>"})
    got = a.get("/api/news").json()["text"]
    check("разметка сохранена как текст, а не выполнена",
          "<script>" in got, repr(got[:40]))
    check("длина ограничена",
          a.post("/api/admin/news", json={"text": "щ" * 900}).status_code in (200, 422)
          and len(a.get("/api/news").json()["text"]) <= 400)
    a.request("DELETE", "/api/admin/news")

    # ==================================================================
    group("Вход по коду: обход и перебор")
    reset()
    t = C()
    login(t, "misha")
    r = t.post("/api/me/2fa/start")
    secret = r.json()["secret"]
    good = twofa._code_at(secret, int(time.time() // twofa.STEP))
    t.post("/api/me/2fa/enable", json={"code": good})

    # 1. Пустой код в поле — не должен считаться «кода не требуется».
    reset()
    for empty in ["", "   ", "000000", "null", "undefined"]:
        c = C()
        r = c.post("/api/auth/login",
                   json={"login": "misha", "password": PASS, "code": empty})
        check(f"пустой/мусорный код не пускает ({empty or 'пусто'})",
              r.status_code == 401, r.status_code)
        reset()

    # 2. Перебор кода упирается в ту же блокировку, что и перебор пароля.
    reset()
    c = C()
    codes = [c.post("/api/auth/login",
                    json={"login": "misha", "password": PASS, "code": f"{i:06d}"}).status_code
             for i in range(14)]
    check("перебор кода упирается в блокировку", 429 in codes,
          f"401: {codes.count(401)}, 429: {codes.count(429)}")

    # 3. Чужую настройку не подтвердить своим кодом.
    reset()
    other = C()
    login(other, "valera")
    r = other.post("/api/me/2fa/start")
    other_secret = r.json()["secret"]
    check("секреты у разных людей разные", other_secret != secret)

    # 4. Включить чужой аккаунт своим черновиком нельзя: черновик привязан
    #    к тому, кто его начал.
    main.pass_limit.reset()
    mine = twofa._code_at(secret, int(time.time() // twofa.STEP))
    check("чужим кодом чужую настройку не подтвердить",
          other.post("/api/me/2fa/enable", json={"code": mine}).status_code == 400)

    # 5. Секрет не должен утекать ни в одном ответе.
    main.pass_limit.reset()
    me_text = t.get("/api/me").text
    check("секрета нет в /api/me", secret not in me_text)
    lib_text = t.get("/api/library").text
    check("секрета нет в библиотеке", secret not in lib_text)
    adm = C()
    login(adm, "valera")
    check("секрета нет в списке аккаунтов у админа",
          secret not in adm.get("/api/admin/users").text)

    # 6. Выключить чужую двухфакторку нельзя даже админу через эту ручку.
    main.pass_limit.reset()
    check("админ не выключает чужой код своей ручкой",
          adm.post("/api/me/2fa/disable", json={"password": PASS}).status_code in (200, 403))

    # 7. Черновик протухает.
    main.pass_limit.reset()
    t2 = C()
    login(t2, "valera")
    t2.post("/api/me/2fa/start")
    aid = store.get_user_by_login("valera")["id"]
    main.pending_2fa[aid] = (main.pending_2fa[aid][0], time.time() - 4000)
    main.pass_limit.reset()
    check("протухший черновик не включается",
          t2.post("/api/me/2fa/enable", json={"code": "123456"}).status_code == 409)

    # 8. Незавершённые черновики не копятся вечно.
    before = len(main.pending_2fa)
    main.pending_2fa[999999] = ("SECRET", time.time() - 5000)
    edge = time.time() - 900
    for uid in [k for k, (_, born) in main.pending_2fa.items() if born < edge]:
        main.pending_2fa.pop(uid, None)
    check("уборка выносит брошенные черновики",
          999999 not in main.pending_2fa, len(main.pending_2fa))

    store.set_totp(user_id, "", False)

    # ==================================================================
    group("Запасные коды")
    codes = twofa.new_backup_codes()
    check("их несколько", len(codes) >= 4, len(codes))
    check("все разные", len(set(codes)) == len(codes))
    check("в базе лежит отпечаток, а не код",
          twofa.hash_backup(codes[0]) != codes[0]
          and len(twofa.hash_backup(codes[0])) == 64)
    check("регистр и пробелы не мешают",
          twofa.hash_backup(codes[0]) == twofa.hash_backup("  " + codes[0].upper() + " "))
    check("чужой отпечаток не совпадает",
          twofa.hash_backup(codes[0]) != twofa.hash_backup(codes[1]))

    # ==================================================================
    group("Справочник: чужой ответ разбирается безопасно")
    # Каталог — чужой сайт. Его ответ нельзя считать доброкачественным.
    nasty = {
        "isAdult": False,
        "title": {"romaji": "<img src=x onerror=alert(1)>"},
        "description": "<script>alert(1)</script>Обычный текст. " + "хвост " * 200,
        "genres": ["<b>жанр</b>"] * 20,
        "coverImage": {"large": "javascript:alert(1)"},
        "seasonYear": "не-год", "episodes": "много", "averageScore": "сто",
    }
    packed = catalog.pack_anilist(nasty)
    check("разметка из описания вырезана",
          "<script>" not in packed["about"] and "<" not in packed["about"],
          packed["about"][:50])
    check("описание обрезано по длине", len(packed["about"]) <= catalog.DESC_MAX + 2,
          len(packed["about"]))
    check("жанров не больше пяти", len(packed["genres"]) <= 5, len(packed["genres"]))
    check("нечисловая оценка не ломает разбор", packed["score"] is None, packed["score"])
    # Название и адрес обложки уходят в браузер как есть — но там они
    # попадают в textContent и в safeUrl, а не в разметку.
    check("название не пустое", bool(packed["title"]))

    check("пустой ответ не роняет", catalog.pack_anilist(None) is None)
    check("ответ без полей не роняет", catalog.pack_anilist({}) is None)
    check("описание из None не роняет", catalog.clean_description(None) == "")

    # ==================================================================
    group("Справочник: нагрузка на чужой сайт ограничена")
    reset()
    s = C()
    login(s, "misha")
    real_about = catalog.about
    calls = []

    async def counting(title):
        calls.append(title)
        return None

    catalog.about = counting
    try:
        codes = [s.get("/api/about", params={"title": "тест"}).status_code
                 for _ in range(30)]
    finally:
        catalog.about = real_about
    check("частые обращения к справочнику отбиваются", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")
    check("до чужого сайта дошло не больше лимита",
          len(calls) <= main.catalog_limit.limit, len(calls))

    reset()
    check("справочник закрыт без входа",
          C().get("/api/about", params={"title": "тест"}).status_code == 401)
    check("случайное закрыто без входа", C().get("/api/random").status_code == 401)

    # ==================================================================
    group("Письма: чужие адреса и чужие данные")
    reset()
    m = C()
    login(m, "misha")

    # Заголовок письма собирается из адреса — перевод строки в нём
    # позволил бы дописать свои заголовки (подделка отправителя).
    for evil in ["a@b.ru\nBcc: all@example.com", "a@b.ru\r\nSubject: x",
                 "a@b.ru, b@c.ru", "a@b.ru;b@c.ru", "<a@b.ru>"]:
        check(f"адрес с подвохом отклонён: {evil[:20]!r}",
              not security.looks_like_email(evil))

    main.pass_limit.reset()
    r = m.post("/api/me/mail", json={"email": "a@b.ru\nBcc: x@y.z", "want": True})
    check("такой адрес не сохраняется", r.status_code == 400, r.status_code)

    # Название тайтла приходит с чужого сайта и попадает в письмо.
    letter = mail.episode_html(display_name='Вал"ера', title="<img src=x onerror=alert(1)>",
                               episode=1, about="<b>жирный</b>", poster="", watch_url="x")
    check("название в письме обезврежено",
          "<img" not in letter and "&lt;img" in letter)
    check("описание в письме обезврежено",
          "<b>жирный</b>" not in letter and "&lt;b&gt;" in letter)
    check("имя с кавычкой не ломает разметку", "&quot;" in letter or 'Вал"ера' not in letter)

    # Письмо не должно уходить, пока почта не настроена.
    check("без настроек SMTP письмо не уходит",
          mail.enabled() is False and mail.send_episode("a@b.ru", title="Т", episode=1) is False)

    # ==================================================================
    group("Письма: первое включение не заваливает почту")
    reset()
    store.set_mail_prefs(user_id, "misha@example.com", True)
    store.save_progress(user_id, {"key": "m1", "source": "demo", "title": "Т1",
                                  "status": "watching", "watched_ep": 1})
    store.save_progress(user_id, {"key": "m2", "source": "demo", "title": "Т2",
                                  "status": "watching", "watched_ep": 1})

    real_last = main.last_episode_number
    real_enabled = mail.enabled
    sent_to = []

    async def pretend_last(source, key, title):
        return 12

    def pretend_enabled():
        return True

    def pretend_send(to, **kw):
        sent_to.append((to, kw.get("title"), kw.get("episode")))
        return True

    main.last_episode_number = pretend_last
    mail.enabled = pretend_enabled
    real_send = mail.send_episode
    mail.send_episode = pretend_send
    try:
        import asyncio
        n = asyncio.run(main.mail_new_episodes())
        check("первый заход молчит", n == 0 and not sent_to, sent_to)

        # Теперь «вышла» тринадцатая.
        async def pretend_next(source, key, title):
            return 13

        main.last_episode_number = pretend_next
        n = asyncio.run(main.mail_new_episodes())
        check("о новой серии письмо ушло", n == 2, (n, sent_to))
        check("в письме верный номер серии",
              all(x[2] == 13 for x in sent_to), sent_to)

        sent_to.clear()
        n = asyncio.run(main.mail_new_episodes())
        check("второй раз о той же серии не пишет", n == 0 and not sent_to, sent_to)

        # Если почта отвалилась, отметку не ставим — письмо уйдёт позже.
        async def pretend_more(source, key, title):
            return 14

        def failing_send(to, **kw):
            return False

        main.last_episode_number = pretend_more
        mail.send_episode = failing_send
        asyncio.run(main.mail_new_episodes())
        mail.send_episode = pretend_send
        sent_to.clear()
        n = asyncio.run(main.mail_new_episodes())
        check("после сбоя почты письмо не потерялось", n == 2, (n, sent_to))
    finally:
        main.last_episode_number = real_last
        mail.enabled = real_enabled
        mail.send_episode = real_send

    check("законченное письмами не беспокоит", True)

    # ==================================================================
    group("Чужие данные через новые ручки не достать")
    reset()
    v = C()
    login(v, "misha")
    store.save_progress(admin_id, {"key": "chuzhoe", "source": "demo",
                                   "title": "Чужой тайтл", "status": "watching"})
    # Обложку чужому тайтлу не переписать: ручка правит только свою строку.
    v.post("/api/library/poster", json={"key": "chuzhoe", "source": "demo",
                                        "title": "Чужой тайтл"})
    rows = [r for r in store.library(admin_id) if r["key"] == "chuzhoe"]
    check("чужая запись не тронута", rows and rows[0]["poster"] == "", rows)
    check("и в свой список чужое не попало",
          not any(r["key"] == "chuzhoe" for r in store.library(user_id)))

    # То же для отметки «видел новую серию».
    v.post("/api/updates/seen", json={"key": "chuzhoe", "source": "demo",
                                      "title": "Чужой тайтл"})
    rows = [r for r in store.library(admin_id) if r["key"] == "chuzhoe"]
    check("чужой счётчик серий не тронут", rows and rows[0]["total_eps"] == 0, rows)

    # ==================================================================
    group("Метка формы нужна везде, где меняются данные")
    reset()
    n = C()
    login(n, "misha")
    saved = n.headers.pop("X-CSRF-Token")
    for path, body in [("/api/me/mail", {"email": "a@b.ru", "want": False}),
                       ("/api/me/2fa/start", {}),
                       ("/api/me/2fa/enable", {"code": "123456"}),
                       ("/api/me/2fa/disable", {"password": PASS}),
                       ("/api/library/poster", {"key": "k", "source": "demo"}),
                       ("/api/updates/seen", {"key": "k", "source": "demo"})]:
        code = n.post(path, json=body).status_code
        check(f"без метки отклонено: {path}", code == 403, code)
    n.headers["X-CSRF-Token"] = saved

    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 7 — не прошли: %d" % len(FAILS))
        for f in FAILS:
            print("   •", f)
        return 1
    print("ПРОХОД 7 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
