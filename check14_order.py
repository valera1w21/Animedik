"""Проход 14: порядок просмотра частей франшизы.

Раньше части стояли по годам выхода, а «начало» ставилось на самую
раннюю. У «ДжоДжо» это была OVA 1993 года — старая отдельная экранизация
третьей части, — а у «Каменного океана» вторая и третья части менялись
местами (обе 2022 года).

Теперь порядок строится по связям справочника (api/catalog.py,
order_parts): основная история по цепочке «продолжение», после каждой её
части — дополнения к ней, в конце — другие версии.

Проверка идёт на настоящих данных Shikimori, сохранённых в
testdata/franchises.json (сети здесь не нужно): «ДжоДжо», «Наруто»,
«Атака титанов», «Гинтама», «Ван-Пис».
"""
import asyncio
import json
import logging
import os
import random
import sys
import tempfile

# Ручки проверяются в личном режиме: демо-режим справочник не трогает.
os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p14.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass14")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient           # noqa: E402
from api import catalog, main, store                # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Poryadok-Serij-2026"


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)


class Patch:
    """Временная подмена атрибута: вернётся на место при выходе из with."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


with open(os.path.join(ROOT, "testdata", "franchises.json"), encoding="utf-8") as fh:
    DATA = json.load(fh)

# Настоящие метки франшиз у Shikimori — по ним ходит /api/franchise.
FRANCHISE = {"jojo": "jojo_no_kimyou_na_bouken", "naruto": "naruto",
             "aot": "shingeki_no_kyojin", "gintama": "gintama",
             "one_piece": "one_piece"}


def node(fr, p, links=True):
    """Часть из сохранённых данных — в том виде, в каком её отдаёт GraphQL."""
    out = {
        "id": int(p["id"]), "russian": p["title"], "name": p["title"],
        "franchise": FRANCHISE[fr], "kind": p["kind"],
        "episodes": p["episodes"] or 0, "episodesAired": 0, "status": p["status"],
        "score": 7.5,
        "airedOn": {"year": int(p["date"][:4]) if p["date"] else None, "date": p["date"]},
        "poster": {"mainUrl": "/x.webp"},
    }
    if links:
        out["related"] = [
            {"relationKind": r["relationKind"],
             "anime": {"id": r["id"]} if r.get("id") else None, "manga": None}
            for r in p["related"]]
    return out


def ordered(fr, links=True, shuffle=None):
    nodes = [node(fr, p, links) for p in DATA[fr]]
    if shuffle is not None:
        random.Random(shuffle).shuffle(nodes)
    parts = [catalog.pack_part(n) for n in nodes]
    parts = [p for p in parts if p["kind"] not in catalog.JUNK_KINDS]
    return catalog.order_parts(parts)


def chain(rows):
    return [p["id"] for p in rows if p["role"] == "main"]


def by_id(rows):
    return {p["id"]: p for p in rows}


def common(fr, rows, links=True):
    """Общие свойства любого порядка: ничего не потерялось, поля на месте."""
    name = fr if links else fr + "/без связей"
    live = [p for p in DATA[fr] if p["kind"] not in catalog.JUNK_KINDS]
    check(name + ": ни одна часть не потерялась и не задвоилась",
          sorted(p["id"] for p in rows) == sorted(p["id"] for p in live),
          "%d из %d" % (len(rows), len(live)))
    need = ("role", "step", "rel", "rel_ru", "rel_en", "main", "date", "anchor", "guess")
    check(name + ": у каждой части есть role/step/rel/rel_ru/rel_en/main",
          all(all(k in p for k in need) for p in rows),
          [k for k in need if not all(k in p for p in rows)])
    check(name + ": связи наружу не уходят (поле related убрано)",
          all("related" not in p for p in rows))
    steps = [p["step"] for p in rows if p["role"] == "main"]
    check(name + ": шаги основной истории идут 1..N подряд",
          steps == list(range(1, len(steps) + 1)), steps)
    check(name + ": «начало» ровно одно, и это шаг 1",
          [p["step"] for p in rows if p["main"]] == [1])
    if links:
        # В запасном порядке это не так нарочно: OVA, вышедшая раньше
        # сериала, стоит выше него, а «начало» — на сериале.
        check(name + ": первая строка списка — «начало»", rows[0]["main"] is True,
              rows[0]["title"])
    # Дополнение стоит после своей части основной истории — и до следующей.
    current, fine = None, True
    for p in rows:
        if p["role"] == "main":
            current = p["id"]
        elif p["role"] == "extra":
            fine = fine and p["anchor"] == current
    check(name + ": каждое дополнение стоит сразу под своей частью", fine)
    tail = [p["role"] for p in rows]
    first_other = tail.index("other") if "other" in tail else len(tail)
    check(name + ": «прочее» собрано в конце, одним куском",
          all(r == "other" for r in tail[first_other:]))
    extras = [p for p in rows if p["role"] != "main" and links]
    check(name + ": у всех дополнений и прочего есть подпись на двух языках",
          all(p["rel"] and p["rel_ru"] and p["rel_en"] for p in extras),
          [p["title"] for p in extras if not p["rel_ru"]][:3])
    again = ordered(fr, links=links, shuffle=7)
    check(name + ": порядок не зависит от порядка ответа справочника",
          [p["id"] for p in again] == [p["id"] for p in rows])


def run():
    # ==================================================================
    group("ДжоДжо")
    jojo = ordered("jojo")
    common("jojo", jojo)
    want = ["14719", "20899", "26055", "31933", "37991", "48661", "51367", "53273", "61469"]
    check("основная история: 2012 → Рыцари → Египет → Алмаз → Ветер → "
          "Океан 1–3 → Стальной шар", chain(jojo) == want, chain(jojo))
    j = by_id(jojo)
    check("«начни отсюда» — сериал 2012 года", j["14719"]["main"] and j["14719"]["step"] == 1)
    check("OVA 1993 года больше не «начало»", not j["666"]["main"])
    check("OVA 1993 года ушла в «другие версии» с подписью «другая экранизация»",
          j["666"]["role"] == "other" and j["666"]["rel"] == "alternative_version"
          and j["666"]["rel_ru"] == "другая экранизация", (j["666"]["role"], j["666"]["rel"]))
    ids = [p["id"] for p in jojo]
    check("«Каменный океан. Часть 2» раньше «Части 3»",
          ids.index("51367") < ids.index("53273"))
    check("рекап «Золотого ветра» — дополнение к «Золотому ветру», «пересказ»",
          j["38972"]["anchor"] == "37991" and j["38972"]["rel"] == "summary"
          and j["38972"]["rel_ru"] == "пересказ")
    check("«Так говорил Рохан Кишибэ» — побочная история после «Несокрушимого алмаза»",
          j["33191"]["anchor"] == "31933" and j["33191"]["rel"] == "side_story")
    check("«другие версии» идут по дате",
          [p["id"] for p in jojo if p["role"] == "other"] == ["666", "665", "41306", "3603"],
          [p["id"] for p in jojo if p["role"] == "other"])
    check("9 частей основной истории и 6 дополнительных",
          len(chain(jojo)) == 9 and len(jojo) - 9 == 6)

    # ==================================================================
    group("Наруто")
    naruto = ordered("naruto")
    common("naruto", naruto)
    check("основная история: Наруто → Ураганные хроники → Боруто: Новое поколение → Часть 2",
          chain(naruto) == ["20", "1735", "34566", "54687"], chain(naruto))
    n = by_id(naruto)
    # У «Ураганных хроник» два продолжения: фильм «Боруто» и сериал.
    check("из двух продолжений цепочка идёт в сериал, фильм — дополнение",
          n["28755"]["role"] == "extra" and n["28755"]["anchor"] == "1735"
          and n["28755"]["rel"] == "sequel", (n["28755"]["role"], n["28755"]["rel"]))
    check("фильмы «Ураганных хроник» — «фильм по мотивам» после них",
          n["2472"]["anchor"] == "1735" and n["2472"]["rel"] == "movie_extra"
          and n["2472"]["rel_ru"] == "фильм по мотивам")
    check("спешлы первого «Наруто» стоят под ним, а не под «Ураганными хрониками»",
          n["761"]["anchor"] == "20" and n["594"]["anchor"] == "20")
    check("«Весна юности Рока Ли» — спин-офф", n["12979"]["rel"] == "spin_off")
    # «Спортивный фестиваль Конохи» связан только с первым фильмом —
    # и встаёт туда же, куда и он.
    check("связанное только с дополнением встаёт рядом с ним",
          n["2248"]["anchor"] == n["442"]["anchor"] == "20")
    check("анонс второй части «Боруто» — последний шаг основной истории",
          n["54687"]["step"] == 4 and n["54687"]["date"] is None)

    # ==================================================================
    group("Атака титанов")
    aot = ordered("aot")
    common("aot", aot)
    check("основная история: 1 → 2 → 3 → 3 ч.2 → Финал → Финал ч.2 → Заключительная глава",
          chain(aot) == ["16498", "25777", "35760", "38524", "40028", "48583", "51535"],
          chain(aot))
    a = by_id(aot)
    check("рекап 2013 года не «начало», а пересказ первого сезона",
          a["19285"]["anchor"] == "16498" and a["19285"]["rel"] == "summary")
    # «Хроника» пересказывает первые три сезона и вышла после них.
    check("«Хроника» стоит после третьего сезона, ч.2 — ближайшего по дате",
          a["42091"]["anchor"] == "38524", a["42091"]["anchor"])
    check("«Титаны средней школы» — спин-офф первого сезона",
          a["31374"]["anchor"] == "16498" and a["31374"]["rel"] == "spin_off")
    check("реклама со «Сникерсом» отсеяна", "49739" not in a)

    # ==================================================================
    group("Гинтама")
    gin = ordered("gintama")
    common("gintama", gin)
    check("основная история: Гинтама → 2 → … → 8 → Полуфинал → Финал",
          chain(gin) == ["918", "9969", "15417", "28977", "34096", "35843",
                         "36838", "37491", "44087", "39486"], chain(gin))
    g = by_id(gin)
    check("«Душа маджонга» (своя цепочка без связи с «Гинтамой») — в «прочем»",
          g["50789"]["role"] == "other" and g["50789"]["rel"] == "unlinked")
    check("«Сказание о Бэнидзакуре» — другая экранизация, в «прочем»",
          g["7472"]["role"] == "other" and g["7472"]["rel"] == "alternative_version")

    # ==================================================================
    group("Ван-Пис")
    op = ordered("one_piece")
    common("one_piece", op)
    check("основная история — сериал 1999 года", chain(op) == ["21"], chain(op))
    o = by_id(op)
    check("OVA 1998 года — не «начало», а побочная история после сериала",
          not o["466"]["main"] and o["466"]["role"] == "extra"
          and o["466"]["anchor"] == "21" and o["466"]["rel"] == "side_story")
    check("новая экранизация (ONA 2027 года) — «другая экранизация»",
          o["57557"]["role"] == "other" and o["57557"]["rel"] == "alternative_version")
    dates = [catalog._date_key(p) for p in op if p["role"] == "extra"]
    check("дополнения под сериалом идут по дате", dates == sorted(dates))

    # ==================================================================
    group("Запасной порядок — когда связей нет")
    # Так приходят части из поиска, из запасного справочника и из старых
    # записей кэша: без поля related.
    jj = ordered("jojo", links=False)
    common("jojo", jj, links=False)
    check("помечено, что порядок — догадка по датам", all(p["guess"] for p in jj))
    ids = [p["id"] for p in jj]
    check("порядок — по полной дате: «Каменный океан» 2 раньше 3",
          ids.index("51367") < ids.index("53273"))
    dates = [catalog._date_key(p) for p in jj]
    check("весь список идёт по дате выхода", dates == sorted(dates))
    check("«начало» — первый сериал (2012), а не OVA 1993, хоть та и раньше",
          next(p["id"] for p in jj if p["main"]) == "14719")

    opf = ordered("one_piece", links=False)
    check("«Ван-Пис» без связей: «начало» — сериал 1999, не OVA 1998",
          next(p["id"] for p in opf if p["main"]) == "21")
    check("OVA 1998 при этом остаётся выше по списку (честный порядок дат)",
          [p["id"] for p in opf].index("466") < [p["id"] for p in opf].index("21"))

    same_year = [
        {"id": "b", "title": "Часть 3", "kind": "ona", "year": 2022, "date": "2022-12-01",
         "episodes": 12},
        {"id": "a", "title": "Часть 2", "kind": "ona", "year": 2022, "date": "2022-09-01",
         "episodes": 12},
        {"id": "c", "title": "Только год", "kind": "ova", "year": 2022, "episodes": 1},
        {"id": "d", "title": "Анонс", "kind": "tv", "year": None, "episodes": None},
    ]
    got = [p["id"] for p in catalog.order_parts([dict(p) for p in same_year])]
    check("внутри года — по дню; без дня — в конце года; без даты — в самом конце",
          got == ["a", "b", "c", "d"], got)
    movies = [
        {"id": "m2", "title": "Фильм 2", "kind": "movie", "year": 2010, "episodes": 1},
        {"id": "m1", "title": "Фильм 1", "kind": "movie", "year": 2008, "episodes": 1},
    ]
    got = catalog.order_parts([dict(p) for p in movies])
    check("франшиза из одних фильмов: все — основная история, «начало» на первом",
          [p["role"] for p in got] == ["main", "main"] and got[0]["id"] == "m1"
          and got[0]["main"])

    # Старая запись кэша — без дат и связей, только год. Ломаться ей нечем.
    catalog._cache.clear()
    old = [{"id": "1", "title": "Старый", "kind": "tv", "year": 2010, "episodes": 12},
           {"id": "2", "title": "Старый 2", "kind": "tv", "year": 2012, "episodes": 12}]
    catalog._cache_put("fr:oldentry", old)
    got = asyncio.run(catalog.franchise_parts("oldentry"))
    check("старая запись кэша отдаётся в запасном порядке",
          [p["id"] for p in got] == ["1", "2"] and got[0]["main"] and got[0]["step"] == 1)
    check("и сама запись в кэше не изменилась",
          all("role" not in p and "main" not in p for p in catalog._cache_get("fr:oldentry")))

    # ==================================================================
    group("Запрос к справочнику и ручки сайта")
    seen = []

    async def gql(query, variables=None):
        seen.append(query)
        if "franchise:" in query:
            nodes = [node("jojo", p) for p in DATA["jojo"]]
            return {"p1": nodes[:10], "p2": nodes[10:]}
        return {"animes": [node("jojo", p, links=False) for p in DATA["jojo"]]}

    catalog._cache.clear()
    store.init()
    if store.get_user_by_login("poryadok") is None:
        store.create_user("poryadok", PASS, "admin")
    t = TestClient(main.app)
    r = t.post("/api/auth/login", json={"login": "poryadok", "password": PASS, "code": ""})
    check("вход", r.status_code == 200, r.status_code)
    with Patch(catalog, "_ask_shiki_gql", gql):
        main.catalog_limit.reset()
        r = t.get("/api/franchise", params={"id": "jojo_no_kimyou_na_bouken"})
        items = r.json().get("items") or []
        check("/api/franchise отвечает", r.status_code == 200 and len(items) == 15,
              (r.status_code, len(items)))
        check("в нём новый порядок: первая строка — сериал 2012 года, «начало»",
              items and items[0]["id"] == "14719" and items[0]["main"]
              and items[0]["step"] == 1 and items[0]["role"] == "main")
        check("и новые поля у каждой части",
              all({"role", "step", "rel", "rel_ru", "rel_en", "date"} <= set(p) for p in items))
        check("связи наружу не уходят", all("related" not in p for p in items))
        check("запрос частей франшизы спрашивает связи и полную дату",
              any("franchise:" in q and "related" in q and "date" in q for q in seen))
        check("в кэше лежат части со связями (порядок собирается из них)",
              any(p.get("related") for p in catalog._cache_get("fr:jojo_no_kimyou_na_bouken")))

        main.catalog_limit.reset()
        title = "Невероятное приключение ДжоДжо: Каменный океан. Часть 2"
        r = t.get("/api/related", params={"title": title})
        rel = r.json().get("items") or []
        check("/api/related отвечает", r.status_code == 200 and len(rel) == 15,
              (r.status_code, len(rel)))
        check("порядок под плеером тот же, что в окне частей",
              [p["id"] for p in rel] == [p["id"] for p in items])
        check("и те же поля", all({"role", "step", "rel_ru", "rel_en", "main"} <= set(p)
                                  for p in rel))
        check("открытая часть помечена current",
              [p["id"] for p in rel if p.get("current")] == ["51367"],
              [p["title"] for p in rel if p.get("current")])
        check("поиск связей не спрашивает (запрос лёгкий)",
              any("search:" in q and "related" not in q for q in seen))


run()
print("\n" + "=" * 60)
if FAILS:
    print("ПРОХОД 14 — не прошли: %d" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    sys.exit(1)
print("ПРОХОД 14 — порядок просмотра частей в порядке.")
