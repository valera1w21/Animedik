"""Проход 27: одно аниме — одна карточка (fix-21).

Хозяин: «Не нужно разделять две разные карточки на одно и то же аниме».
Посмотрел тайтл на русском, потом на английском — в «Моём списке» две
карточки: одна с русского источника, другая с английского. Ключ записи —
номер тайтла У ИСТОЧНИКА, у каждого свой, а склейка по названию
спотыкалась о «Jujutsu Kaisen (TV)» против «Магической битвы» и о разное
число серий у источников.

Теперь:
  * у записи есть номер справочника (cid, «mal:<id>»), его присылают
    страница просмотра и «В планы»; по нему записи одного аниме склеиваются
    всегда, а разные номера не склеиваются никогда (ремейк с тем же
    названием — другая карточка);
  * без номера — по имени (без «(TV)», «[Dub]»), году и, у записей одного
    языка, числу серий;
  * склейка: прогресс — где досмотрено дальше, добавлен — самое раннее,
    источник и ключ — последнего, имя — русское (латинское рядом);
  * разовая склейка уже накопившихся дублей при запуске (merge_duplicates);
  * журнал просмотров переезжает под общий ключ и имя — итоги года
    считают одно аниме одной строкой;
  * название карточки — на текущем языке (A.listName).

Сети нет: к источникам здесь никто не ходит.
"""
import logging
import os
import sys
import tempfile
import time

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p27.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["UPDATES_EVERY_MIN"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass27")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient          # noqa: E402
from api import anime, main, store                  # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PASS = "Odna-Kartochka-2026"
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


def lib(c):
    return c.get("/api/library").json()["items"]


def read(p):
    with open(os.path.join(HERE, p), encoding="utf-8") as f:
        return f.read()


# Обложка — с сайта справочника: чужие адреса сервер не сохраняет
# (POSTER_SITES в api/main.py).
RU = {"key": "ru_a:9001", "source": "ru_a", "title": "Магическая битва",
      "title_en": "Jujutsu Kaisen", "year": 2020,
      "poster": "https://shikimori.one/uploads/poster/animes/40748/p.jpg",
      "genres": "экшен", "total_eps": 24, "watched_ep": 5, "position": 300,
      "status": "watching", "cid": "mal:40748"}
# Английский источник называет тайтл по-своему и кладёт оба сезона одной
# записью — 47 серий против 24: по старым правилам это «другой тайтл».
EN = {"key": "en_a:jujutsu-kaisen-tv-534", "source": "en_a",
      "title": "Jujutsu Kaisen (TV)", "title_en": "", "poster": "", "year": 2020,
      "genres": "", "total_eps": 47, "watched_ep": 7, "position": 61,
      "status": "watching", "cid": "mal:40748"}


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "hozyain")
    store.create_user("friend", PASS, "user", "friend")
    uid = store.get_user_by_login("hozyain")["id"]
    fid = store.get_user_by_login("friend")["id"]
    c = TestClient(main.app)
    check("вход", login(c, "hozyain").status_code == 200)

    # ==================================================================
    group("1. Смотрел по-русски, потом по-английски — одна карточка")
    r = c.post("/api/library/progress", json=RU)
    check("русская запись сохранилась", r.status_code == 200, r.text[:200])
    time.sleep(1.1)
    r = c.post("/api/library/progress", json=EN)
    check("английская запись сохранилась", r.status_code == 200, r.text[:200])
    items = lib(c)
    check("в списке одна карточка", len(items) == 1, [(i["key"], i["title"]) for i in items])
    it = items[0] if items else {}
    check("источник и ключ — последнего (английского)",
          it.get("key") == EN["key"] and it.get("source") == "en_a", it)
    check("прогресс — где досмотрено дальше (серия 7, секунда 61)",
          it.get("watched_ep") == 7 and it.get("position") == 61, it)
    check("имя — русское, латинское рядом",
          it.get("title") == "Магическая битва" and it.get("title_en") == "Jujutsu Kaisen", it)
    check("номер справочника на месте", it.get("cid") == "mal:40748", it)
    check("обложка не пропала (у английской её не было)", it.get("poster") == RU["poster"], it)
    first_added = it.get("added_at")

    # Снова по-русски, но на серии меньше: прогресс не откатывается.
    time.sleep(1.1)
    back = dict(RU, watched_ep=3, position=10)
    c.post("/api/library/progress", json=back)
    items = lib(c)
    check("вернулся на русский — всё ещё одна карточка, ключ русский",
          len(items) == 1 and items[0]["key"] == RU["key"], [(i["key"]) for i in items])
    check("прогресс не откатился (7 > 3)", items and items[0]["watched_ep"] == 7, items)
    check("«добавлен» — самое раннее", items and items[0]["added_at"] == first_added
          and first_added > 0, (first_added, items and items[0]["added_at"]))

    # Страница просмотра на английском, когда в адресе и полке есть cid, шлёт
    # имя из полки (русское) — и с ним, и без него склеивается.
    check("сервер вернул имя записи после склейки",
          c.post("/api/library/progress", json=dict(EN, watched_ep=8)).json().get("title")
          == "Магическая битва")

    # ==================================================================
    group("2. Журнал и итоги года — одно аниме одной строкой")
    r = c.post("/api/library/watched", json=dict(EN, watched_ep=8, position=1400))
    check("отметка о просмотре с английского засчитана", r.json().get("counted") is True, r.text)
    r = c.post("/api/library/watched", json=dict(RU, watched_ep=8, position=1400))
    check("та же серия сразу же с русского — тот же просмотр, не второй",
          r.json().get("counted") is False, r.text)
    c.post("/api/library/watched", json=dict(RU, watched_ep=9, position=1400))
    st = c.get("/api/stats/year").json()
    names = [t[0] for t in st["titles"]]
    check("в итогах одна строка «Магическая битва»", names == ["Магическая битва"], names)
    check("серий в журнале — две (8 и 9)", st["episodes"] == 2, st["episodes"])

    # ==================================================================
    group("3. Разные номера справочника — разные карточки")
    hxh1 = {"key": "ru_a:hxh99", "source": "ru_a", "title": "Охотник х Охотник",
            "title_en": "Hunter x Hunter", "year": 2011, "total_eps": 148, "watched_ep": 3,
            "status": "watching", "cid": "mal:11061"}
    hxh2 = dict(hxh1, key="ru_b:hxh", source="ru_b", cid="mal:136",
                watched_ep=62, total_eps=62, status="done")
    c.post("/api/library/progress", json=hxh1)
    c.post("/api/library/progress", json=hxh2)
    keys = {i["key"] for i in lib(c)}
    check("ремейк с тем же названием и годом рядом, но другим номером — отдельно",
          {"ru_a:hxh99", "ru_b:hxh"} <= keys, keys)
    junk = dict(RU, key="ru_a:junk", cid="javascript:alert(1)", title="Другое аниме",
                title_en="")
    c.post("/api/library/progress", json=junk)
    row = [i for i in lib(c) if i["key"] == "ru_a:junk"]
    check("чужой мусор в cid не сохраняется", row and row[0]["cid"] == "", row)

    # ==================================================================
    group("4. «В планы» с другого источника не трогает начатое")
    plan = dict(EN, key="en_c:jjk", source="en_c", watched_ep=0, position=0,
                status="later")
    r = c.post("/api/library/progress", json=plan)
    check("сервер ответил «уже смотрите»", r.json().get("status") == "watching", r.text)
    jj = [i for i in lib(c) if i["cid"] == "mal:40748"]
    check("карточка одна, прогресс на месте", len(jj) == 1 and jj[0]["watched_ep"] >= 8, jj)

    # ==================================================================
    group("5. Новые серии: одна запись — одно уведомление")
    jkey = jj[0]["key"] if jj else ""
    store.apply_episode_count(uid, jkey, 50)
    store.apply_episode_count(uid, jkey, 51)
    ups = store.fresh_updates(uid)
    check("уведомление одно, по общей записи",
          len([u for u in ups if u["title"] == "Магическая битва"]) == 1, ups)

    # ==================================================================
    group("6. Разовая склейка старых дублей (merge_duplicates)")
    conn = store.connect()
    now = store.now()

    def raw(user, key, source, title, title_en, year, total, watched, pos, status, upd,
            cid="", added=0):
        conn.execute(
            "INSERT INTO library (user_id,key,source,title,title_en,poster,year,genres,"
            " total_eps,watched_ep,position,status,updated_at,cid,added_at)"
            " VALUES (?,?,?,?,?,'',?,'',?,?,?,?,?,?,?)",
            (user, key, source, title, title_en, year, total, watched, pos, status, upd,
             cid, added))
    # Дубль «как у хозяина»: русская карточка старше, английская свежее.
    raw(fid, "ru_a:1", "ru_a", "Скитальцы", "Drifters", 2016, 12, 9, 500,
        "watching", now - 5000, added=now - 90000)
    raw(fid, "en_a:drifters-1", "en_a", "Drifters (TV)", "", 2016, 13, 4, 20,
        "watching", now - 100, added=now - 2000)
    # Тот же язык, то же имя, но годы далеко — ремейк, не трогаем.
    raw(fid, "ru_a:fb1", "ru_a", "Корзинка фруктов", "", 2001, 26, 26, 0,
        "done", now - 7000)
    raw(fid, "ru_b:fb2", "ru_b", "Корзинка фруктов", "", 2019, 25, 3, 0,
        "watching", now - 6000)
    # Тот же язык, одно имя, а серий втрое больше — тоже не одно и то же.
    raw(fid, "ru_a:x1", "ru_a", "Дубль-тест", "", 2010, 12, 2, 0,
        "watching", now - 300)
    raw(fid, "ru_c:x2", "ru_c", "Дубль-тест", "", 2010, 40, 2, 0,
        "watching", now - 200)
    # Номер у обоих один — склеиваются, как бы ни назывались.
    raw(fid, "ru_a:c1", "ru_a", "Атака титанов", "Shingeki no Kyojin", 2013, 25,
        25, 0, "done", now - 8000, cid="mal:16498")
    raw(fid, "en_a:aot", "en_a", "Attack on Titan", "", 2013, 25, 2, 0,
        "watching", now - 50, cid="mal:16498")
    # «В планы» свежее, но начатый близнец — главнее.
    raw(fid, "ru_a:p1", "ru_a", "Планы-тест", "Plan Test", 2020, 12, 6, 33,
        "watching", now - 9000)
    raw(fid, "en_a:p1", "en_a", "Plan Test", "", 2020, 12, 0, 0, "later", now - 10)
    for k, t in (("ru_a:1", "Скитальцы"), ("en_a:drifters-1", "Drifters (TV)")):
        conn.execute("INSERT INTO watch_log (user_id,key,title,genres,ep,seconds,at)"
                     " VALUES (?,?,?,'',1,1400,?)", (fid, k, t, now - 3000))
    conn.execute("DELETE FROM site WHERE key = 'one_card_v1'")

    dropped = store.merge_duplicates()
    rows = {r["key"]: r for r in store.library(fid)}
    check("ушло ровно три дубля", dropped == 3, (dropped, sorted(rows)))
    d = rows.get("en_a:drifters-1")
    check("Скитальцы: осталась свежая запись (английская), русской нет",
          d is not None and "ru_a:1" not in rows, sorted(rows))
    if d:
        check("Скитальцы: имя русское, латинское — с русской записи",
              d["title"] == "Скитальцы" and d["title_en"] == "Drifters", d)
        check("Скитальцы: прогресс — где дальше (серия 9, секунда 500)",
              d["watched_ep"] == 9 and d["position"] == 500, d)
        check("Скитальцы: добавлен — самое раннее", d["added_at"] == now - 90000, d)
    check("ремейк другого года остался отдельной карточкой",
          "ru_a:fb1" in rows and "ru_b:fb2" in rows, sorted(rows))
    check("один язык, серий втрое больше — не склеены",
          "ru_a:x1" in rows and "ru_c:x2" in rows, sorted(rows))
    a = rows.get("en_a:aot")
    check("один номер справочника — одна карточка, статус «досмотрено» сохранён",
          a is not None and "ru_a:c1" not in rows and a["status"] == "done"
          and a["watched_ep"] == 25 and a["title"] == "Атака титанов", a)
    p = rows.get("en_a:p1")
    check("план не отменил начатое", p is not None and "ru_a:p1" not in rows
          and p["status"] == "watching" and p["watched_ep"] == 6, p)
    logs = conn.execute("SELECT key, title FROM watch_log WHERE user_id = ?", (fid,)).fetchall()
    check("журнал переехал под общий ключ и имя",
          {(r["key"], r["title"]) for r in logs} == {("en_a:drifters-1", "Скитальцы")},
          [tuple(r) for r in logs])
    check("чужой список не тронут", len(lib(c)) >= 4)
    check("второй запуск ничего не делает (отметка в site)", store.merge_duplicates() == 0)
    store.init()
    check("и повторный store.init() тоже", len(store.library(fid)) == len(rows))

    # ==================================================================
    group("7. Справочник отдаёт номер, страница его носит")
    from api import catalog
    part = catalog.pack_part({"id": 40748, "russian": "Магическая битва",
                              "name": "Jujutsu Kaisen", "kind": "tv", "rating": "r",
                              "airedOn": {"year": 2020}})
    check("часть франшизы Shikimori — cid mal:<id>", part and part.get("cid") == "mal:40748",
          part)
    al = catalog._anilist_parts([{"id": 113415, "idMal": 40748,
                                  "title": {"romaji": "Jujutsu Kaisen"}, "format": "TV"}])
    check("AniList с номером MAL — тот же cid", al and al[0]["cid"] == "mal:40748", al)
    al2 = catalog._anilist_parts([{"id": 5, "title": {"romaji": "X"}, "format": "TV"}])
    check("AniList без номера MAL — al:<id>", al2 and al2[0]["cid"] == "al:5", al2)
    w = read("web/watch.js")
    ix = read("web/index.js")
    ap = read("web/app.js")
    check("страница просмотра шлёт cid", "key: KEY, source: SOURCE, cid: CID" in w)
    check("страница просмотра находит запись по cid, если ключ другой",
          "items.filter(function (i) { return i.cid === CID; })" in w)
    check("cid едет в адресах (часть истории, смена источника, переезд, субтитры)",
          w.count("cidParam()") >= 3 and "'&cid=' + encodeURIComponent(part.cid)" in w)
    check("переезд на источник другого языка оставляет имя справочника",
          "hrefFor(found, found.source, name, true)" in w)
    check("карточка списка и часть франшизы передают cid в плеер",
          "'&cid=' + encodeURIComponent(it.cid)" in ix and "cid: p.cid || ''" in ix)
    check("«В планы» шлёт cid", "cid: t.cid || ''," in ix)

    # ==================================================================
    group("8. Название карточки — на текущем языке")
    check("A.listName: латинское при английском сайте или аниме, иначе русское",
          "(lang === 'en' || animeSrcLang() === 'en') && row.title_en" in ap
          and "listName: listName" in ap)
    check("коробки списка называются через listName",
          "boxTitle(A.listName(shortest)" in ix)
    check("смена языка аниме перерисовывает список",
          "addEventListener('animelangchange', function () {\n    if (A.me) render();" in ix)

    print()
    if FAILS:
        print("ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("Все проверки прохода 27 прошли.")


if __name__ == "__main__":
    run()
