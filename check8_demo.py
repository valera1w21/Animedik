"""Pass 8: demonstration mode.

The public version of the site runs with MODE=demo: the player plays
Blender Foundation films under Creative Commons, and there are no
external video sources at all. What is checked is the main thing — that
in this mode the site really does not go anywhere for someone else's
video and works without libraries the public repository does not have.

Run it apart from the other suites: the mode is switched on by an
environment variable before the application is imported, and it cannot
be changed on the fly.
"""
import io
import os
import sys
import tempfile
import logging

# The public version's checks run in demonstration mode: there is no
# other here. External video sources are not part of this repository, and
# the only working source is free video (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["MODE"] = "demo"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p8.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass8"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import anime, anime_demo, main            # noqa: E402

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
r = c.post("/api/auth/guest")
if r.status_code == 200:
    c.headers["X-CSRF-Token"] = r.json()["csrf"]

# ----------------------------------------------------------------------
group("Mode is on and visible")
check("the variable was read", anime.DEMO is True, anime.MODE)
r = c.get("/api/mode")
check("the page can ask about it", r.status_code == 200 and r.json()["demo"] is True)
check("it can be asked without signing in",
      TestClient(main.app).get("/api/mode").status_code == 200)

# ----------------------------------------------------------------------
group("There are no external video sources")
check("exactly one source", list(anime.SOURCES) == [anime_demo.NAME], list(anime.SOURCES))
check("the same one for both languages",
      anime.sources_for("ru") == anime.sources_for("en") == [anime_demo.NAME])
check("and the same one by default",
      anime.default_source("ru") == anime.default_source("en") == anime_demo.NAME)

ru = c.get("/api/sources?lang=ru").json()
en = c.get("/api/sources?lang=en").json()
check("only it is served to the outside",
      [x["id"] for x in ru] == [x["id"] for x in en] == [anime_demo.NAME])

# Not a single source apart from the demonstration one may leave for the
# outside. We check it not against a list of forbidden names but the
# other way round: everything the site calls a source has to be the demo.
# That way the check survives any new name somebody writes in here one day.
named = ([x["id"] for x in ru] + [x["id"] for x in en]
         + list(main.fallback_for("ru")) + list(main.fallback_for("en"))
         + list(main.SUB_ORDER) + list(main.SUB_ORDER_EN)
         + list(main.FALLBACK_ORDER) + list(main.FALLBACK_ORDER_EN))
check("apart from the demonstration one, there are no sources anywhere",
      set(named) == {anime_demo.NAME}, sorted(set(named)))

# The fallback lists are empty for a reason: there is nothing to plug in,
# and only whoever adds a source of their own can fill them.
check("the fallback orders are empty",
      not (main.FALLBACK_ORDER or main.FALLBACK_ORDER_EN
           or main.SUB_ORDER or main.SUB_ORDER_EN))

# ----------------------------------------------------------------------
group("no external source libraries are needed")
# The parser is obtained without touching any source library: in the
# public version those packages will not be there at all, and reaching
# for them would bring the site down.
before = set(sys.modules)
extractor = anime.get_extractor(anime_demo.NAME)
loaded = set(sys.modules) - before
check("the demo source's parser was handed over", extractor is not None)
check("and no third-party libraries were loaded for it",
      not [m for m in loaded if m.startswith(("библиотека источников", "библиотека источников"))],
      [m for m in loaded if m.startswith(("библиотека источников", "библиотека источников"))])

# ----------------------------------------------------------------------
group("The video is free and real")
seen_hosts = set()
for clip in anime_demo.CLIPS:
    for track in clip["tracks"]:
        for v in track["videos"]:
            seen_hosts.add(v["url"].split("/")[2])
check("the links lead to known open platforms",
      all(h.endswith(("blender.org", "w3.org", "mux.dev")) for h in seen_hosts),
      sorted(seen_hosts))
check("every clip states its licence",
      all(c_["licence"].startswith("CC") for c_ in anime_demo.CLIPS))
check("there is both a stream and a plain file",
      {"m3u8", "mp4"} <= {v["type"] for c_ in anime_demo.CLIPS
                          for t in c_["tracks"] for v in t["videos"]})

# The same query always gives the same clip: otherwise the "continue
# watching" list would lead to a different video every time.
check("the choice of clip repeats",
      anime_demo.pick_clip("наруто")["id"] == anime_demo.pick_clip("наруто")["id"])

# ----------------------------------------------------------------------
group("And the site works")
r = c.get("/api/search", params={"q": "наруто", "lang": "ru"})
check("search at the source answers", r.status_code == 200 and r.json()["items"],
      r.status_code)
row = r.json()["items"][0]
check("the card is named what was searched for", row["title"] == "наруто", row["title"])

q = {"key": row["key"], "source": anime_demo.NAME, "title": row["title"]}
r = c.get("/api/episodes", params=q)
check("episodes are served", r.status_code == 200 and isinstance(r.json(), list), r.status_code)

main.source_limit.reset()
r = c.get("/api/videos", params={**q, "ordinal": 1, "lang": "ru"})
body = r.json() if r.status_code == 200 else {}
check("the player gets links", r.status_code == 200 and body.get("videos"), r.status_code)
check("the track is named clearly",
      body.get("chosen") in ("HLS-поток", "Обычный файл"), body.get("chosen"))

print("\n" + "=" * 60)
if FAILS:
    print("ПРОХОД 8 — не прошли: %d" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    sys.exit(1)
print("PASS 8 — demonstration mode is in order.")
