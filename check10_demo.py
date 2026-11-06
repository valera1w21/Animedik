"""Проход 8: демонстрационный режим.

Публичная версия сайта работает при MODE=demo: плеер играет фильмы
Blender Foundation под Creative Commons, внешних источников видео нет
вовсе. Проверяется главное — что в этом режиме сайт действительно
никуда за чужим видео не ходит и работает без библиотек, которых в
публичном репозитории нет.

Запускать отдельно от остальных наборов: режим включается переменной
окружения до импорта приложения, а поменять его на ходу нельзя.
"""
import io
import os
import sys
import tempfile
import logging

os.environ["MODE"] = "demo"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p8.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass8"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import anime, anime_demo, main            # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

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


c = TestClient(main.app)
r = c.post("/api/auth/guest", json={})
if r.status_code == 200:
    c.headers["X-CSRF-Token"] = r.json()["csrf"]

# ----------------------------------------------------------------------
group("Режим включён и виден")
check("переменная прочитана", anime.DEMO is True, anime.MODE)
r = c.get("/api/mode")
check("страница может о нём спросить", r.status_code == 200 and r.json()["demo"] is True)
check("спросить можно без входа",
      TestClient(main.app).get("/api/mode").status_code == 200)

# ----------------------------------------------------------------------
group("Внешних источников видео нет")
check("источник ровно один", list(anime.SOURCES) == [anime_demo.NAME], list(anime.SOURCES))
check("он же для обоих языков",
      anime.sources_for("ru") == anime.sources_for("en") == [anime_demo.NAME])
check("и он же по умолчанию",
      anime.default_source("ru") == anime.default_source("en") == anime_demo.NAME)

ru = c.get("/api/sources?lang=ru").json()
en = c.get("/api/sources?lang=en").json()
check("наружу отдаётся только он",
      [x["id"] for x in ru] == [x["id"] for x in en] == [anime_demo.NAME])

# Других источников нет вовсе: ни в списке, ни в переборе, ни в личном
# режиме (публичная версия, см. anime.SOURCES).
check("перебор — только демо-источник",
      main.fallback_for("ru") == main.fallback_for("en") == [anime_demo.NAME],
      (main.fallback_for("ru"), main.fallback_for("en")))
check("источников личной версии нет",
      anime.PRIVATE_SOURCES == {} and anime.EXTRACTORS == {} and main.FALLBACK_ORDER == []
      and main.SUB_ORDER == [] and anime.EN_ORDER == [] and anime.RX_ORDER == [],
      list(anime.PRIVATE_SOURCES))

# ----------------------------------------------------------------------
group("Библиотеки внешних источников не нужны")
# Разборщик достаётся без сторонних библиотек: в публичной версии их нет
# вовсе (requirements-private.txt — одни комментарии).
extractor = anime.get_extractor(anime_demo.NAME)
check("разборщик демо-источника выдан", extractor is not None)
check("библиотек источников не нужно", anime.LIBRARIES == {} and anime.missing_libraries() == [])

# ----------------------------------------------------------------------
group("Видео — свободное и настоящее")
seen_hosts = set()
for clip in anime_demo.CLIPS:
    for track in clip["tracks"]:
        for v in track["videos"]:
            seen_hosts.add(v["url"].split("/")[2])
check("ссылки ведут на известные открытые площадки",
      all(h.endswith(("blender.org", "w3.org", "mux.dev")) for h in seen_hosts),
      sorted(seen_hosts))
check("у каждого ролика указана лицензия",
      all(c_["licence"].startswith("CC") for c_ in anime_demo.CLIPS))
check("есть и поток, и обычный файл",
      {"m3u8", "mp4"} <= {v["type"] for c_ in anime_demo.CLIPS
                          for t in c_["tracks"] for v in t["videos"]})

# Один и тот же запрос всегда даёт один и тот же ролик: иначе список
# «продолжить смотреть» вёл бы каждый раз на другое видео.
check("выбор ролика повторяем",
      anime_demo.pick_clip("наруто")["id"] == anime_demo.pick_clip("наруто")["id"])

# ----------------------------------------------------------------------
group("Сайт при этом работает")
r = c.get("/api/search", params={"q": "наруто", "lang": "ru"})
check("поиск у источника отвечает", r.status_code == 200 and r.json()["items"],
      r.status_code)
row = r.json()["items"][0]
check("карточка названа тем, что искали", row["title"] == "наруто", row["title"])

q = {"key": row["key"], "source": anime_demo.NAME, "title": row["title"]}
r = c.get("/api/episodes", params=q)
check("серии отдаются", r.status_code == 200 and isinstance(r.json(), list), r.status_code)

main.source_limit.reset()
r = c.get("/api/videos", params={**q, "ordinal": 1, "lang": "ru"})
body = r.json() if r.status_code == 200 else {}
check("плеер получает ссылки", r.status_code == 200 and body.get("videos"), r.status_code)
check("дорожка названа понятно",
      body.get("chosen") in ("HLS-поток", "Обычный файл"), body.get("chosen"))

group("Имя источника проверяется и в демо-режиме")
# Раньше демо-режим подменял источник ДО проверки и молча принимал
# любой мусор, вплоть до «../../os». Теперь сначала проверка, потом
# подстановка демонстрационного.
main.search_limit.reset()
r = c.get("/api/search", params={"q": "test", "source": "../../os"})
check("мусор вместо источника отклонён", r.status_code == 400, r.status_code)
r = c.get("/api/search", params={"q": "test", "source": "ru_a"})
check("имени чужого источника нет и в личной версии — отклонено", r.status_code == 400, r.status_code)
r = c.get("/api/search", params={"q": "test", "source": ""})
check("пустое имя — демо-источник", r.status_code == 200 and r.json()["source"] == anime_demo.NAME,
      r.status_code)
r = c.get("/api/resolve", params={"title": "наруто", "source": "../../os"})
check("и в поиске части тоже", r.status_code == 400, r.status_code)

print("\n" + "=" * 60)
if FAILS:
    print("ПРОХОД 8 — не прошли: %d" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    sys.exit(1)
print("ПРОХОД 8 — демонстрационный режим в порядке.")
