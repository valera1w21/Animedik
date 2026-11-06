"""Проход 20: тайтл, которого нет у основных источников, но есть у узких.

Жалоба владельца: у части тайтлов «нет источников». Обычный перебор в
/api/resolve ходит только к основным (RU-A, RU-B, RU-C,
RU-D). Узкие — RU-E, RU-F, RU-G, RU-H — спрашивались
только из меню источников на странице просмотра, руками. Теперь, когда
основные ничего не дали, резолв сам идёт к узким — по тем же правилам.

Здесь проверяется:
  * тайтл, лежащий только у RU-E, находится, и сначала спрашивали
    основных, а узких — потом;
  * синонимы работают и у узких («Демоны старшей школы» → RU-E);
  * нашлось у основного — к узким не ходили вовсе (скорость не страдает);
  * нигде нет — честный 404, и узких спрашивали;
  * only=1 (переключение источника вручную) к узким не уходит;
  * лимит обращений: на три имени не хватает — спрашиваем одно; не
    хватает и на одно — 429, а не «нигде нет»;
  * у узкого тайтл есть, но серий не отдаёт — не выдаём его за живой.

Сети нет: источники и справочник подменены.
"""
import logging
import os
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p20.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass20")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import anime, catalog, main, store         # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

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
    return ok


class Patch:
    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


def reset():
    for lim in (main.login_guard, main.api_limit, main.search_limit, main.guest_limit,
                main.write_limit, main.source_limit, main.catalog_limit):
        lim.reset()
    anime._cache.clear()
    catalog._cache.clear()
    catalog._ALT_INDEX.clear()
    ASKED.clear()


def row(key, title, year=None, eps=None):
    return {"key": key, "title": title, "poster": "", "year": year,
            "genres": "", "episodes_total": eps}


MAIN = list(main.FALLBACK_ORDER)
NARROW = [s for s in anime.sources_for("ru") if s not in MAIN]

# Что где лежит. «Сага о Винланде» — только у RU-E; «Наруто» — у
# RU-A; «Старшая школа DxD» — у RU-E и только под синонимом.
SHELF = {
    "ru_a": [row("al-n", "Наруто", 2002, 220)],
    "ru_b": [],
    "ru_c": [],
    "ru_d": [],
    "ru_e": [row("hr-v", "Сага о Винланде [ТВ-1] Завершен (все серии)", 2019),
                row("hr-d", "Демоны старшей школы [ТВ-1] Завершен (все серии)", 2012),
                row("hr-dead", "Мёртвый тайтл", 2020)],
    "ru_f": [],
    "ru_g": [],
    "ru_h": [],
}
ASKED = []
SILENT = set()


async def fake_try_source(source, q):
    ASKED.append((source, q))
    if source in SILENT:
        return None
    words = [w for w in anime.normalize_title(q).split() if len(w) > 3]
    out = []
    for r in SHELF.get(source, []):
        t = " ".join(anime.title_variants(r["title"]))
        if any(w in t for w in words):
            out.append(dict(r, match=round(anime.relevance(q, r["title"]), 3)))
    out.sort(key=lambda r: -r["match"])
    anime.cache_put(f"search:{source}:{q.strip().lower()}", out)
    return out


async def fake_episodes(source, key, title):
    # У «мёртвого» тайтла серии не отдаются: название есть, смотреть нечего.
    return [] if key == "hr-dead" else [object()] * 12


async def no_gql(query, variables=None):
    return {"animes": []}


def sources_asked():
    return [s for s, _ in ASKED]


def run():
    store.init()
    store.create_user("hozyain", "Zaliv-Pepel-2026", "admin", "hozyain")
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": "hozyain", "password": "Zaliv-Pepel-2026"})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]

    group("0. Исходные условия")
    check("RU-E — узкий источник, в обычный перебор не входит",
          "ru_e" in NARROW and "ru_e" not in MAIN, (MAIN, NARROW))

    with Patch(main, "try_source", fake_try_source), \
            Patch(anime, "find_episodes", fake_episodes), \
            Patch(catalog, "_ask_shiki_gql", no_gql), Patch(catalog, "MIN_GAP", 0):

        # ==============================================================
        group("1. Есть только у RU-E — находится")
        reset()
        r = c.get("/api/resolve", params={"title": "Сага о Винланде", "episodes": 24,
                                          "year": 2019, "source": "ru_a"})
        j = r.json()
        check("ответ 200, источник RU-E", r.status_code == 200 and j.get("source") == "ru_e",
              (r.status_code, j))
        check("…тот самый тайтл и точное совпадение", j.get("key") == "hr-v" and j.get("exact") is True, j)
        got = sources_asked()
        first_narrow = min(i for i, s in enumerate(got) if s in NARROW)
        check("сначала спросили все основные, узких — только потом",
              set(MAIN) <= set(got[:first_narrow]), got)

        # ==============================================================
        group("2. Синонимы доходят и до узких")
        reset()
        r = c.get("/api/resolve", params={"title": "Старшая школа DxD", "episodes": 12, "year": 2012,
                                          "alt": "Демоны старшей школы", "source": "ru_a"})
        j = r.json()
        check("«Старшая школа DxD» нашлась у RU-E как «Демоны старшей школы»",
              r.status_code == 200 and j.get("key") == "hr-d" and j.get("exact"), (r.status_code, j))
        check("у RU-E спрашивали синоним", ("ru_e", "Демоны старшей школы") in ASKED, ASKED)

        # ==============================================================
        group("3. Нашлось у основного — к узким не ходим")
        reset()
        r = c.get("/api/resolve", params={"title": "Наруто", "episodes": 220, "year": 2002,
                                          "source": "ru_a"})
        j = r.json()
        check("«Наруто» — у RU-A", r.status_code == 200 and j.get("source") == "ru_a", j)
        check("узких не спрашивали", not set(sources_asked()) & set(NARROW), ASKED)

        # ==============================================================
        group("4. Нигде нет — честный 404")
        reset()
        r = c.get("/api/resolve", params={"title": "Такого аниме не бывает", "source": "ru_a",
                                          "alt": "Совсем другое имя"})
        check("404", r.status_code == 404, (r.status_code, r.text[:100]))
        check("…и узких перед этим спросили", set(NARROW) <= set(sources_asked()), ASKED)

        # ==============================================================
        group("5. Переключение вручную (only=1) — только выбранный")
        reset()
        r = c.get("/api/resolve", params={"title": "Сага о Винланде", "source": "ru_a", "only": 1})
        check("only=1: 404 у RU-A, к RU-E не ходили",
              r.status_code == 404 and set(sources_asked()) == {"ru_a"}, (r.status_code, ASKED))

        # ==============================================================
        group("6. Основные молчат — узкие всё равно спрашиваются")
        reset()
        SILENT.update(MAIN)
        try:
            r = c.get("/api/resolve", params={"title": "Сага о Винланде", "source": "ru_a"})
            j = r.json()
            check("основные лежат, RU-E ответил — нашлось",
                  r.status_code == 200 and j.get("source") == "ru_e", (r.status_code, j))
            SILENT.update(NARROW)
            reset()
            SILENT.update(MAIN + NARROW)
            r = c.get("/api/resolve", params={"title": "Сага о Винланде", "source": "ru_a"})
            check("молчат все — 502 «не отвечает», а не «нет»", r.status_code == 502,
                  (r.status_code, r.text[:80]))
        finally:
            SILENT.clear()

        # ==============================================================
        group("7. Лимит обращений")
        reset()
        # Основной круг: 4 источника × 1 имя (синонимов справочник не дал).
        # Запасной: 4 узких × 1 имя. Оставляем ровно на основной и одно имя
        # у узких — должно хватить, и тайтл находится.
        lim = main.search_limit.limit
        main.search_limit.allow("rs:u:1", lim - len(MAIN) - len(NARROW))
        r = c.get("/api/resolve", params={"title": "Сага о Винланде", "source": "ru_a"})
        check("впритык по лимиту — нашлось по основному названию",
              r.status_code == 200 and r.json().get("source") == "ru_e", (r.status_code, r.text[:80]))

        reset()
        main.search_limit.allow("rs:u:1", lim - len(MAIN))
        r = c.get("/api/resolve", params={"title": "Сага о Винланде", "source": "ru_a"})
        check("на узких лимита не осталось — 429, а не «нигде нет»",
              r.status_code == 429, (r.status_code, r.text[:80]))
        check("…и к узким не ходили", not set(sources_asked()) & set(NARROW), ASKED)

        reset()
        # Три имени у узких стоят 12, одно — 4. Оставляем 4 + основной круг.
        main.search_limit.allow("rs:u:1", lim - len(MAIN) * 3 - len(NARROW))
        r = c.get("/api/resolve", params={"title": "Старшая школа DxD", "source": "ru_a",
                                          "alt": "Высшая школа DxD|Демоны старшей школы"})
        narrow_q = {q for s, q in ASKED if s in NARROW}
        check("на три имени не хватило — у узких спросили одно основное",
              narrow_q == {"Старшая школа DxD"}, narrow_q)

        # ==============================================================
        group("8. У узкого тайтл есть, но серий не отдаёт")
        reset()
        r = c.get("/api/resolve", params={"title": "Мёртвый тайтл", "source": "ru_a"})
        check("не выдаём за живой — 404", r.status_code == 404, (r.status_code, r.text[:80]))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"ПРОХОД 20 — не прошли: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 20 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
