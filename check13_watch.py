"""Проход 13: страница просмотра — то, что от неё приходит на сервер.

Здесь серверная половина исправлений страницы просмотра:

  * одна и та же серия, отмеченная несколько раз подряд (перезагрузка,
    другое устройство), в итогах года считается один раз;
  * «Тайтлов закрыто» в итогах — только закрытые в этом году, для этого
    у полки появился столбец done_at, и старая база получает его сама;
  * просимая озвучка не отвечает — сервер пробует остальные и честно
    говорит, какая ответила, а не отдаёт 502 на всю серию.

Клиентская половина (очерёдность запросов, ▶ до прихода ссылок, секунда
не той серии) проверяется в браузере — здесь её не видно.
"""
import calendar
import os
import sqlite3
import sys
import tempfile
import time
import logging

# Эти проверки написаны под личный режим: они подменяют настоящие
# источники (ru_a, ru_b…), которых в демо-режиме нет.
# Режим ставим здесь, а не берём из окружения, — иначе запуск при
# MODE=demo падал бы не из-за ошибок, а из-за чужих условий.
# Демо-режим проверяет check10_demo.py.
os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p13.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass13")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient           # noqa: E402
from api import main, store                         # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

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


def login(c, user="hozyain", pwd=PASS):
    r = c.post("/api/auth/login", json={"login": user, "password": pwd, "code": ""})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def log_rows(uid, key):
    return store.connect().execute(
        "SELECT ep, at FROM watch_log WHERE user_id = ? AND key = ? ORDER BY id",
        (uid, key)).fetchall()


def year_start():
    year = time.strftime("%Y", time.gmtime())
    return calendar.timegm(time.strptime(year + "-01-01", "%Y-%m-%d"))


def run():
    store.init()
    if store.get_user_by_login("hozyain") is None:
        store.create_user("hozyain", PASS, "admin")
    uid = store.get_user_by_login("hozyain")["id"]

    # ==================================================================
    group("Одна серия — один просмотр")
    reset()
    a = C()
    login(a)
    body = {"key": "povtor", "title": "Повтор", "genres": "Драма",
            "watched_ep": 1, "position": 1300, "total_eps": 12}
    r1 = a.post("/api/library/watched", json=body)
    r2 = a.post("/api/library/watched", json=body)
    check("первая отметка принята", r1.status_code == 200 and r1.json().get("counted") is True,
          r1.text[:80])
    check("повторная тоже отвечает 200", r2.status_code == 200, r2.status_code)
    check("но сказано, что в журнал она не пошла", r2.json().get("counted") is False,
          r2.text[:80])
    check("в журнале одна строка, а не две", len(log_rows(uid, "povtor")) == 1,
          len(log_rows(uid, "povtor")))
    s = a.get("/api/stats/year").json()
    check("итоги считают серию один раз", s["episodes"] == 1, s["episodes"])

    a.post("/api/library/watched", json={**body, "watched_ep": 2})
    check("следующая серия считается отдельно", len(log_rows(uid, "povtor")) == 2,
          len(log_rows(uid, "povtor")))
    # Другой тайтл — с другим названием: запись с тем же названием сервер
    # теперь (fix-21) по праву считает тем же аниме с другого источника и
    # склеивает вместе с журналом (store.is_twin, _rekey_log).
    a.post("/api/library/watched", json={**body, "key": "drugoy", "title": "Другой"})
    check("та же серия другого тайтла — тоже", len(log_rows(uid, "drugoy")) == 1)

    # Пересмотр на следующий день — настоящий второй просмотр.
    store.connect().execute(
        "UPDATE watch_log SET at = at - ? WHERE user_id = ? AND key = 'povtor' AND ep = 1",
        (store.REWATCH_GAP + 60, uid))
    check("после окна отметка снова считается",
          store.log_watch(uid, "povtor", "Повтор", "Драма", 1, 1300) is True)
    check("и в журнале появилась строка",
          len([x for x in log_rows(uid, "povtor") if x["ep"] == 1]) == 2)
    check("окно — не меньше вечера и не больше суток",
          6 * 3600 <= store.REWATCH_GAP < 24 * 3600, store.REWATCH_GAP)

    # ==================================================================
    group("Старая база получает done_at сама")
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    old = sqlite3.connect(path)
    old.row_factory = sqlite3.Row
    old.execute(
        "CREATE TABLE library (user_id INTEGER NOT NULL, key TEXT NOT NULL,"
        " source TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',"
        " poster TEXT NOT NULL DEFAULT '', year INTEGER,"
        " genres TEXT NOT NULL DEFAULT '', total_eps INTEGER NOT NULL DEFAULT 0,"
        " watched_ep INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL DEFAULT 0,"
        " status TEXT NOT NULL DEFAULT 'watching', updated_at INTEGER NOT NULL,"
        " PRIMARY KEY (user_id, key))")
    old.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    old.execute("INSERT INTO library (user_id,key,title,status,updated_at)"
                " VALUES (1,'k','Старый','done',123)")
    store._add_missing_columns(old)
    cols = {r["name"] for r in old.execute("PRAGMA table_info(library)")}
    check("столбец добавлен", "done_at" in cols, sorted(cols))
    row = old.execute("SELECT title, done_at FROM library").fetchone()
    check("старые записи на месте, время закрытия пустое",
          row["title"] == "Старый" and row["done_at"] == 0, dict(row))
    old.close()

    # ==================================================================
    group("Когда тайтл закрыли")
    def done_at(key):
        return store.connect().execute(
            "SELECT done_at FROM library WHERE user_id = ? AND key = ?",
            (uid, key)).fetchone()["done_at"]

    t0 = int(time.time())
    store.save_progress(uid, {"key": "zakryt", "title": "Закрыт", "watched_ep": 12,
                              "total_eps": 12, "status": "done"})
    first = done_at("zakryt")
    check("закрыли — время записано", first >= t0, first)
    store.connect().execute(
        "UPDATE library SET done_at = done_at - 1000 WHERE user_id = ? AND key = 'zakryt'",
        (uid,))
    store.save_progress(uid, {"key": "zakryt", "title": "Закрыт", "watched_ep": 12,
                              "total_eps": 12, "status": "done"})
    check("повторная отметка «done» время не двигает",
          done_at("zakryt") == first - 1000, (done_at("zakryt"), first - 1000))
    store.save_progress(uid, {"key": "zakryt", "title": "Закрыт", "watched_ep": 3,
                              "status": "watching"})
    check("вернули в «смотрю» — время стёрто", done_at("zakryt") == 0, done_at("zakryt"))
    store.save_progress(uid, {"key": "zakryt", "title": "Закрыт", "watched_ep": 12,
                              "total_eps": 12, "status": "done"})
    check("закрыли снова — время новое", done_at("zakryt") >= t0, done_at("zakryt"))

    # ==================================================================
    group("«Тайтлов закрыто» — только за этот год")
    start = year_start()
    conn = store.connect()
    conn.execute("DELETE FROM library WHERE user_id = ?", (uid,))
    store.save_progress(uid, {"key": "etot-god", "title": "Этот год", "status": "done"})
    # Закрыт в прошлом году, а запись трогали в этом — считаться не должен.
    store.save_progress(uid, {"key": "proshlyy", "title": "Прошлый", "status": "done"})
    conn.execute("UPDATE library SET done_at = ? WHERE user_id = ? AND key = 'proshlyy'",
                 (start - 86400, uid))
    # Закрыты до появления done_at: судим по updated_at.
    store.save_progress(uid, {"key": "staryy-davno", "title": "Давно", "status": "done"})
    store.save_progress(uid, {"key": "staryy-nedavno", "title": "Недавно", "status": "done"})
    conn.execute("UPDATE library SET done_at = 0, updated_at = ?"
                 " WHERE user_id = ? AND key = 'staryy-davno'", (start - 86400, uid))
    conn.execute("UPDATE library SET done_at = 0, updated_at = ?"
                 " WHERE user_id = ? AND key = 'staryy-nedavno'", (start + 60, uid))
    store.save_progress(uid, {"key": "smotryu", "title": "Смотрю", "status": "watching"})

    got = store.year_stats(uid, start)["finished"]
    check("считаются закрытые в этом году (2 из 4)", got == 2, got)
    reset()
    a2 = C()
    login(a2)
    s = a2.get("/api/stats/year?tz=0").json()
    check("и страница итогов получает то же", s["finished"] == 2, s["finished"])
    store.save_progress(uid, {"key": "staryy-davno", "title": "Давно", "status": "done"})
    check("старую запись тронули — год закрытия не сменился",
          store.year_stats(uid, start)["finished"] == 2,
          store.year_stats(uid, start)["finished"])

    # ==================================================================
    group("Одна озвучка не отвечает — остальные на месте")
    reset()
    real_eps = main.anime.find_episodes

    class FakeVideo:
        def __init__(self, url):
            self.url, self.quality, self.type = url, 720, "m3u8"

    class FakePlayer:
        def __init__(self, title, url):
            self.title, self._url = title, url

        async def a_get_videos(self):
            if not self._url:
                raise RuntimeError("хостинг молчит")
            return [FakeVideo(self._url)]

    players = []

    class FakeEpisode:
        ordinal = 1

        async def a_get_sources(self):
            return players

    async def fake_find(source, key, title):
        return [FakeEpisode()]

    main.anime.find_episodes = fake_find
    try:
        t = C()
        login(t)
        q = {"key": "k", "ordinal": 1, "source": "ru_c"}
        players[:] = [FakePlayer("Озвучка Рабочая", "https://v.example/ok.m3u8"),
                      FakePlayer("Озвучка Молчит", "")]
        r = t.get("/api/videos", params={**q, "dub": "Молчит"})
        body = r.json() if r.status_code == 200 else {}
        check("просимая молчит — не 502 на всю серию", r.status_code == 200, r.text[:100])
        check("сказано, какая ответила на самом деле", body.get("chosen") == "Рабочая",
              body.get("chosen"))
        check("ссылки — от неё", [v["url"] for v in body.get("videos", [])]
              == ["https://v.example/ok.m3u8"])
        check("список озвучек полный",
              [d["name"] for d in body.get("dubs", [])] == ["Рабочая", "Молчит"],
              body.get("dubs"))

        main.source_limit.reset()
        main.anime._cache.clear()        # ответы /api/videos помнятся (fix-21)
        r = t.get("/api/videos", params={**q, "dub": "Рабочая"})
        check("рабочая по просьбе отдаётся она же",
              r.status_code == 200 and r.json().get("chosen") == "Рабочая", r.text[:80])

        # Порядок важен: просимая идёт первой, даже если в списке она не первая.
        players[:] = [FakePlayer("Озвучка Первая", "https://v.example/1.m3u8"),
                      FakePlayer("Озвучка Вторая", "https://v.example/2.m3u8")]
        main.source_limit.reset()
        main.anime._cache.clear()        # ответы /api/videos помнятся (fix-21)
        r = t.get("/api/videos", params={**q, "dub": "Вторая"})
        check("просимая рабочая — она, а не первая по списку",
              r.status_code == 200 and r.json().get("chosen") == "Вторая", r.text[:80])

        players[:] = [FakePlayer("Озвучка Молчит", ""), FakePlayer("Озвучка Тоже", "")]
        main.source_limit.reset()
        main.anime._cache.clear()        # ответы /api/videos помнятся (fix-21)
        r = t.get("/api/videos", params={**q, "dub": "Молчит"})
        check("молчат все — 502", r.status_code == 502, r.status_code)
        check("и в тексте названа просимая", "Молчит" in r.text, r.text[:100])
    finally:
        main.anime.find_episodes = real_eps


run()
print("\n" + "=" * 60)
if FAILS:
    print("ПРОХОД 13 — не прошли: %d" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    sys.exit(1)
print("ПРОХОД 13 — страница просмотра: серверная часть в порядке.")
