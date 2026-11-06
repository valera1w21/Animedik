"""Проход 21: «Планирую смотреть» и одинаковый аватар в списке аккаунтов.

Что добавилось (fix-14):
  * третья вкладка на главной — «Планирую смотреть». В неё попадает
    запись со статусом «later», которую кладёт кнопка «В планы» в окне
    частей франшизы: тот же /api/library/progress, watched_ep = 0;
  * запланированное не звенит колокольчиком, не проверяется фоновой
    проверкой, не уходит в письма и не попадает в итоги года;
  * стоит начать смотреть (плеер присылает «watching» с серией) — запись
    уходит из планов в «Не закончил». Сервер страхует и сам: «later» с
    настоящим прогрессом (серия или секунда) сохраняется как «watching»;
  * «В планы» никогда не затирает уже начатое или досмотренное — ни ту
    же запись, ни её близнеца с другого источника. Только открытая
    страница (серия 1, секунда 0) начатым не считается;
  * закладка «В планы» есть на карточках поиска и под плеером, а
    страница просмотра не пишет «смотрю» запланированному тайтлу, пока
    видео не пошло;
  * список аккаунтов в админке отдаёт цвет аватара и признак фото: аватар
    там рисуется так же, как у человека в шапке.

Сети нет: к источникам здесь никто не ходит.
"""
import logging
import os
import re
import sys
import tempfile
import time

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p21.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["UPDATES_EVERY_MIN"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass21")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient          # noqa: E402
from api import main, store                         # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PASS = "Zaliv-Pepel-2026"
FAILS = []
G = [""]


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def reset():
    main.login_guard.reset()
    for lim in (main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.pass_limit,
                main.avatar_limit, main.catalog_limit):
        lim.reset()


def login(c, user, pwd=PASS):
    reset()
    c.headers.pop("X-CSRF-Token", None)
    r = c.post("/api/auth/login", json={"login": user, "password": pwd, "code": ""})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def item(key, title, **kw):
    d = {"key": key, "source": "ru_a", "title": title, "poster": "",
         "year": 2020, "genres": "", "total_eps": 12, "watched_ep": 0,
         "position": 0, "status": "later"}
    d.update(kw)
    return d


def lib(c):
    return {i["key"]: i for i in c.get("/api/library").json()["items"]}


def read(p):
    with open(os.path.join(HERE, p), encoding="utf-8") as f:
        return f.read()


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "Хозяин")
    uid = store.create_user("drug", PASS, "user", "Друг")
    c = TestClient(main.app)

    # ------------------------------------------------------------------
    group("«В планы» сохраняется и приходит в списке")
    check("вход", login(c, "drug").status_code == 200)
    r = c.post("/api/library/progress", json=item("p1", "Монолог фармацевта"))
    check("сохранение со статусом later — 200", r.status_code == 200, r.text)
    check("в ответе статус later", r.json().get("status") == "later", r.json())
    got = lib(c).get("p1")
    check("/api/library отдаёт запись", got is not None)
    check("статус later, серия 0, секунда 0",
          got and got["status"] == "later" and got["watched_ep"] == 0 and got["position"] == 0, got)
    r = c.post("/api/library/progress", json=item("p2", "Что-то", status="plan"))
    check("неизвестный статус — 400", r.status_code == 400, r.status_code)

    # ------------------------------------------------------------------
    group("Запланированное не звенит, не проверяется и не считается")
    c.post("/api/library/progress", json=item("w1", "Смотрю сейчас", status="watching",
                                              watched_ep=3, position=40))
    # Обоим тайтлам «вышла» новая серия — как если бы её нашла проверка.
    with store.tx() as conn:
        conn.execute("UPDATE library SET latest_ep = 20, checked_at = ? WHERE user_id = ?",
                     (store.now(), uid))
    fresh = [r["key"] for r in store.fresh_updates(uid)]
    check("fresh_updates: только начатое", fresh == ["w1"], fresh)
    r = c.get("/api/updates")
    keys = [u["key"] for u in r.json().get("items", r.json().get("updates", []))] \
        if r.status_code == 200 else None
    check("/api/updates не упоминает запланированное",
          keys is not None and "p1" not in keys, (r.status_code, r.text[:200]))
    to_check = [r["key"] for r in store.user_rows_to_check(uid, store.now() + 10**6)]
    check("фоновая проверка его не берёт", "p1" not in to_check and "w1" in to_check, to_check)
    check("и общая проверка тоже",
          "p1" not in [r["key"] for r in store.rows_to_check(store.now() + 10**6)])
    mail = [r["key"] for r in store.watching_for_mail(uid)]
    check("письма о сериях — только начатое", mail == ["w1"], mail)
    year = time.strftime("%Y", time.gmtime())
    before = c.get("/api/stats/year?year=" + year + "&tz=0")
    c.post("/api/library/progress", json=item("p3", "Ещё один план"))
    after = c.get("/api/stats/year?year=" + year + "&tz=0")
    check("итоги года не меняются от планов",
          before.status_code == 200 and before.json() == after.json(),
          (before.status_code, after.status_code))

    # ------------------------------------------------------------------
    group("Начал смотреть — запись уходит из планов")
    r = c.post("/api/library/progress", json=item("p1", "Монолог фармацевта", status="watching",
                                                  watched_ep=1, position=12))
    got = lib(c)["p1"]
    check("плеер прислал watching — статус watching", got["status"] == "watching", got["status"])
    check("серия и секунда сохранились", got["watched_ep"] == 1 and got["position"] == 12, got)
    # Клиент по ошибке прислал «later» вместе с настоящим прогрессом.
    r = c.post("/api/library/progress", json=item("p3", "Ещё один план", watched_ep=2, position=5))
    check("later с серией сервер сохраняет как watching",
          r.json().get("status") == "watching" and lib(c)["p3"]["status"] == "watching",
          (r.json(), lib(c)["p3"]["status"]))
    r = c.post("/api/library/progress", json=item("p4", "Только секунда", position=30))
    check("later с секундой — тоже watching", lib(c)["p4"]["status"] == "watching",
          lib(c)["p4"]["status"])
    c.post("/api/library/progress", json=item("p5", "Отметка серии"))
    r = c.post("/api/library/watched", json=item("p5", "Отметка серии", status="watching",
                                                 watched_ep=1, position=600))
    check("отметка просмотренной серии переносит из планов",
          r.status_code == 200 and lib(c)["p5"]["status"] == "watching", r.text)

    # ------------------------------------------------------------------
    group("«В планы» не затирает начатое")
    r = c.post("/api/library/progress", json=item("w1", "Смотрю сейчас"))
    got = lib(c)["w1"]
    check("та же запись: статус и серия остались",
          got["status"] == "watching" and got["watched_ep"] == 3 and got["position"] == 40, got)
    check("ответ честно говорит, что не в планах", r.json().get("status") == "watching", r.json())
    c.post("/api/library/progress", json=item("d1", "Досмотренное", status="done",
                                              watched_ep=12, position=0))
    c.post("/api/library/progress", json=item("d1", "Досмотренное"))
    check("досмотренное остаётся досмотренным", lib(c)["d1"]["status"] == "done", lib(c)["d1"])
    # Близнец: тот же тайтл с другого источника (другой ключ).
    r = c.post("/api/library/progress", json=dict(item("tw-2", "Смотрю сейчас"), source="ru_c"))
    items = [i for i in lib(c).values() if i["title"] == "Смотрю сейчас"]
    check("близнец с другого источника: одна запись",
          len(items) == 1, [(i["key"], i["status"]) for i in items])
    check("и у неё прогресс и статус начатого",
          items and items[0]["status"] == "watching" and items[0]["watched_ep"] == 3,
          items and items[0])
    # И наоборот: план, а потом тот же тайтл начали на другом источнике.
    c.post("/api/library/progress", json=item("pl-a", "Магическая битва"))
    c.post("/api/library/progress", json=dict(item("pl-b", "Магическая битва", status="watching",
                                                   watched_ep=1, position=3), source="ru_c"))
    items = [i for i in lib(c).values() if i["title"] == "Магическая битва"]
    check("план + начали на другом источнике → одна запись «смотрю»",
          len(items) == 1 and items[0]["status"] == "watching",
          [(i["key"], i["status"]) for i in items])

    # Страница просмотра кладёт тайтл в список уже при открытии: серия 1,
    # секунда 0, «watching». Это ещё не просмотр — план его заменяет.
    c.post("/api/library/progress", json=item("o1", "Только открыли", status="watching",
                                              watched_ep=1, position=0))
    r = c.post("/api/library/progress", json=item("o1", "Только открыли"))
    check("открытое, но не начатое (серия 1, секунда 0) → в планы",
          r.json().get("status") == "later" and lib(c)["o1"]["status"] == "later",
          (r.json(), lib(c)["o1"]))
    c.post("/api/library/progress", json=dict(item("o2a", "Открыли на другом", status="watching",
                                                   watched_ep=1, position=0), source="ru_c"))
    c.post("/api/library/progress", json=item("o2b", "Открыли на другом"))
    items = [i for i in lib(c).values() if i["title"] == "Открыли на другом"]
    check("близнец только открыт → одна запись, и она в планах",
          len(items) == 1 and items[0]["status"] == "later" and items[0]["watched_ep"] == 0,
          [(i["key"], i["status"], i["watched_ep"]) for i in items])
    c.post("/api/library/progress", json=item("o3", "Вторая серия", status="watching",
                                              watched_ep=2, position=0))
    c.post("/api/library/progress", json=item("o3", "Вторая серия"))
    check("вторая серия — уже начато, план не заменяет",
          lib(c)["o3"]["status"] == "watching" and lib(c)["o3"]["watched_ep"] == 2, lib(c)["o3"])

    # ------------------------------------------------------------------
    group("Убрать из планов")
    c.post("/api/library/progress", json=item("p9", "Убираемое"))
    r = c.delete("/api/library/p9")
    check("удаление — 200", r.status_code == 200, r.status_code)
    check("записи больше нет", "p9" not in lib(c))

    # ------------------------------------------------------------------
    group("Список аккаунтов: аватар как у человека")
    check("вход администратора", login(c, "hozyain").status_code == 200)
    c.post("/api/me/profile", json={"avatar_color": "#2B3A55"})
    r = c.get("/api/admin/users")
    users = {u["login"]: u for u in r.json()["users"]}
    check("цвет аватара в ответе", users["hozyain"].get("avatar_color") == "#2B3A55", users["hozyain"])
    check("у другого — его собственный цвет (по умолчанию)",
          users["drug"].get("avatar_color") == "#84CBB6", users["drug"])
    check("признак фото есть и он логический",
          users["hozyain"].get("has_avatar") is False, users["hozyain"])

    # ------------------------------------------------------------------
    group("Разметка, скрипт и стили")
    html, js, appjs, css = read("web/index.html"), read("web/index.js"), read("web/app.js"), read("web/app.css")
    check("вкладка «Планирую смотреть» со счётчиком",
          'data-tab="later"' in html and 'id="tabn-later"' in html and "Планирую смотреть" in html)
    check("текст пустой вкладки",
          "Пока ничего не запланировано" in js and "Нажмите «В планы» в списке частей после поиска." in js)
    check("кнопка «В планы» в окне частей сохраняет status later",
          "planbtn" in js and re.search(r"status:\s*'later'", js) is not None and "resolvePart(p)" in js)
    check("кнопка оформлена", ".planbtn{" in css and ".planbtn.on{" in css)
    wjs, whtml = read("web/watch.js"), read("web/watch.html")
    check("закладка «В планы» на карточках поиска (франшиза и источник)",
          "function planMark" in js and "franchiseMark(fr)" in js and "startPart(" in js
          and ".planmark{" in css and 'id="flash"' in html
          and "Добавлено в «Планирую смотреть»" in js and "Убрано из планов" in js)
    check("кнопка «В планы» на странице просмотра",
          'id="w-plan"' in whtml and "function paintPlan" in wjs and "Уже смотрите" in wjs)
    check("открытие страницы не пишет «смотрю» запланированному",
          re.search(r"if \(holdSaves\(\) && !st\.touched && !asWatched\) return", wjs) is not None)
    check("кружков-точек у образцов тем больше нет",
          "thdot" not in html and "<em style" not in html and ".ucth em" not in css)
    check("в карточке профиля разделители ровные, не пунктир",
          not re.search(r"\.ucmenu\{[^}]*dashed", css) and not re.search(r"\.ucout::before\{[^}]*dashed", css))
    check("цвет буквы аватара считается от фона (один способ на всех)",
          "function avaInk" in appjs and "A.paintAvatar(av" in js
          and "u.role === 'admin' ? '#84CBB6'" not in js)


run()
print("\n" + "=" * 60)
if FAILS:
    print("ПРОВАЛЕНО: %d" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("ПРОХОД 21: всё в порядке")
