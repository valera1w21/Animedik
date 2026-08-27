"""Pass 2: we run a real server and look at what it actually does.

This is not text parsing but live requests: a user's whole path, edge
cases, attempts to break it.
"""
import io
import os
import sys
import tempfile
import time
import logging

# The public version's checks run in demonstration mode: there is no
# other here. External video sources are not part of this repository, and
# the only working source is free video (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p2.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass2"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import main, security, store             # noqa: E402

# The output here is in English, while the Windows console lives in cp1251
# by default: the very first arrow or tick knocked the whole run over with
# a UnicodeEncodeError, and the checks broke off halfway, never reaching
# the point. We ask the stream to work in utf-8; where it is utf-8 anyway,
# the line changes nothing.
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


def C():
    return TestClient(main.app)


def login(c, u, p):
    r = c.post("/api/auth/login", json={"login": u, "password": p})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def guest(c):
    r = c.post("/api/auth/guest")
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def reset():
    main.login_guard.reset()
    main.api_limit.reset()
    main.search_limit.reset()
    main.guest_limit.reset()
    main.write_limit.reset()


def run():
    store.init()
    reset()
    admin_id = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")
    store.create_user("misha", "Tihiy-Signal-2026", "user", "misha")

    # ------------------------------------------------------------------
    group("A user's whole path")
    c = C()
    check("before signing in the main page is served", c.get("/").status_code == 200)
    check("but the data is closed", c.get("/api/library").status_code == 401)
    r = login(c, "valera", "Zaliv-Pepel-2026")
    check("signing in worked", r.status_code == 200)
    me = r.json()["me"]
    check("the role is administrator", me["role"] == "admin")
    check("the settings arrived empty", me["settings"] == {})

    r = c.post("/api/me/settings", json={"accent": "sky", "depth": "mid", "lang": "en",
                                         "card_size": 224, "logo": 2, "sort": "title",
                                         "autonext": False, "automark": True, "show_done": False})
    check("the settings were saved", r.status_code == 200, r.text[:60])
    got = c.get("/api/me").json()["settings"]
    check("the settings read back", got.get("accent") == "sky" and got.get("logo") == 2, got)
    check("a boolean field was saved as it was", got.get("autonext") is False, got.get("autonext"))

    r = c.post("/api/library/progress", json={
        "key": "source-a:100", "source": "demo", "title": "Тайтл",
        "poster": "https://example.com/a.jpg", "year": 2024,
        "genres": "Драма,Детектив", "total_eps": 12, "watched_ep": 3,
        "position": 421, "status": "watching"})
    check("progress was saved", r.status_code == 200)
    item = c.get("/api/library").json()["items"][0]
    check("the cover was saved", item["poster"] == "https://example.com/a.jpg", item["poster"])
    check("the genres were saved", item["genres"] == "Драма,Детектив", item["genres"])
    check("the second was saved", item["position"] == 421, item["position"])
    check("the year was saved", item["year"] == 2024, item["year"])

    # ------------------------------------------------------------------
    group("Edge values of progress")
    cases = [
        ("отрицательная секунда", {"key": "k1", "position": -10}, 422),
        ("секунда больше суток", {"key": "k1", "position": 999999}, 422),
        ("серия больше предела", {"key": "k1", "watched_ep": 99999}, 422),
        ("год из будущего", {"key": "k1", "year": 9999}, 422),
        ("год слишком ранний", {"key": "k1", "year": 1800}, 422),
        ("нет ключа", {"title": "x"}, 422),
        ("пустой ключ", {"key": ""}, 422),
        ("статус выдуман", {"key": "k1", "status": "хакер"}, 400),
    ]
    for name, body, want in cases:
        got = c.post("/api/library/progress", json=body).status_code
        check(name + " rejected", got == want, f"{got}, expected {want}")

    check("second zero is allowed",
          c.post("/api/library/progress", json={"key": "k2", "position": 0}).status_code == 200)
    check("episode zero is allowed",
          c.post("/api/library/progress", json={"key": "k3", "watched_ep": 0}).status_code == 200)

    # ------------------------------------------------------------------
    group("saving again does not breed records")
    before = len(c.get("/api/library").json()["items"])
    for i in range(5):
        c.post("/api/library/progress", json={"key": "same", "position": i * 100})
    after = c.get("/api/library").json()["items"]
    same = [x for x in after if x["key"] == "same"]
    check("there is one record", len(same) == 1, len(same))
    check("the last second was saved", same[0]["position"] == 400, same[0]["position"])

    # ------------------------------------------------------------------
    group("Deletion")
    check("deleting your own record", c.request("DELETE", "/api/library/same").status_code == 200)
    check("the record is gone",
          not [x for x in c.get("/api/library").json()["items"] if x["key"] == "same"])
    check("deleting again does not fall over",
          c.request("DELETE", "/api/library/same").status_code == 200)
    check("deleting something that does not exist does not fall over",
          c.request("DELETE", "/api/library/нет-такого").status_code == 200)
    check("a key with a slash is handled",
          c.request("DELETE", "/api/library/source-a/100").status_code in (200, 404))

    # ------------------------------------------------------------------
    group("Avatar: edge cases")
    from PIL import Image
    def png(w, h, color=(80, 120, 160)):
        b = io.BytesIO()
        Image.new("RGB", (w, h), color).save(b, "PNG")
        return b.getvalue()

    check("a wide picture", c.post("/api/me/avatar", content=png(1200, 300)).status_code == 200)
    got = c.get("/api/me/avatar")
    img = Image.open(io.BytesIO(got.content))
    check("cropped to a square", img.size == (128, 128), img.size)
    check("a narrow picture", c.post("/api/me/avatar", content=png(200, 1400)).status_code == 200)
    check("a tiny picture", c.post("/api/me/avatar", content=png(8, 8)).status_code == 200)
    check("an empty body removes the avatar",
          c.post("/api/me/avatar", content=b"").json().get("removed") is True)
    check("after deleting the avatar there is none", c.get("/api/me/avatar").status_code == 404)

    check("text instead of a picture", c.post("/api/me/avatar", content=b"hello").status_code == 400)
    check("a truncated png", c.post("/api/me/avatar", content=png(100, 100)[:60]).status_code == 400)
    check("a gif is not accepted", c.post("/api/me/avatar", content=b"GIF89a" + b"\x00" * 200).status_code == 400)
    big = b"\x00" * (AVATAR := 400 * 1024)
    check("a file over the limit", c.post("/api/me/avatar", content=big).status_code == 413)

    # ------------------------------------------------------------------
    group("Profile: edge cases")
    check("an empty name is rejected",
          c.post("/api/me/profile", json={"display_name": "   "}).status_code == 400)
    check("a name over the limit is rejected",
          c.post("/api/me/profile", json={"display_name": "и" * 200}).status_code == 422)
    check("control characters are cleaned out",
          c.post("/api/me/profile", json={"display_name": "имя\u0000\u001b[31m"}).status_code == 200)
    saved = c.get("/api/me").json()["display_name"]
    check("there are no control characters in the name",
          all(ch.isprintable() for ch in saved), repr(saved))
    check("a colour with no hash is rejected",
          c.post("/api/me/profile", json={"avatar_color": "FF0000"}).status_code == 400)
    check("a short colour is accepted",
          c.post("/api/me/profile", json={"avatar_color": "#abc"}).status_code == 200)

    # ------------------------------------------------------------------
    group("Settings: known values only")
    # sort and card_size have gone from the settings along with sorting
    # and cover size: the library is now one list, with no tabs and no
    # resizing to configure.
    for field, value in [("accent", "rainbow"), ("depth", "light"),
                         ("lang", "de")]:
        got = c.post("/api/me/settings", json={field: value}).status_code
        check(f"{field}={value} rejected", got == 400, got)
    # Deleted fields must be ignored rather than saved: otherwise an old
    # client quietly stuffs the database with rubbish.
    c.post("/api/me/settings", json={"card_size": 9999, "sort": "random"})
    left = c.get("/api/me").json()["settings"]
    check("deleted settings do not settle in the database",
          "card_size" not in left and "sort" not in left, sorted(left))
    check("a logo number out of range is rejected",
          c.post("/api/me/settings", json={"logo": 42}).status_code == 422)

    # ------------------------------------------------------------------
    group("Guest")
    reset()
    g = C()
    guest(g)
    check("a guest sees the list of sources", g.get("/api/sources").status_code == 200)
    n = len(g.get("/api/sources").json())
    # In the public version there is exactly one source — the
    # demonstration one. It serves both languages: free video is neither
    # "Russian" nor "English". We check the number not for the number's
    # sake: it will catch an external source that slipped in here by
    # accident.
    check("exactly one source", n == 1, n)
    ru_ids = [s["id"] for s in g.get("/api/sources").json()]
    en_ids = [s["id"] for s in g.get("/api/sources?lang=en").json()]
    check("and it is the demonstration one", ru_ids == ["demo"], ru_ids)
    check("the same one for both languages", ru_ids == en_ids, en_ids)
    check("every source has a caption",
          all(s.get("label") and s.get("note") for s in g.get("/api/sources").json()))
    for path, body in [("/api/library/progress", {"key": "x"}),
                       ("/api/library/watched", {"key": "x"}),
                       ("/api/me/settings", {"accent": "sky"}),
                       ("/api/me/profile", {"display_name": "x"}),
                       ("/api/me/avatar", None)]:
        got = g.post(path, json=body) if body else g.post(path, content=b"")
        check(f"{path} is closed to a guest", got.status_code == 403, got.status_code)
    check("a guest does not delete records",
          g.request("DELETE", "/api/library/x").status_code == 403)
    check("a guest has no avatar", g.get("/api/me/avatar").status_code == 404)

    # ------------------------------------------------------------------
    group("Statistics")
    reset()
    a = C()
    login(a, "valera", "Zaliv-Pepel-2026")
    s0 = a.get("/api/stats/year").json()
    base_eps = s0["episodes"]
    a.post("/api/library/watched", json={
        "key": "st", "title": "Т", "genres": "Драма", "watched_ep": 1,
        "position": 1200, "total_eps": 5})
    s1 = a.get("/api/stats/year").json()
    check("the episode was counted", s1["episodes"] == base_eps + 1, s1["episodes"])
    check("the time was counted", s1["seconds"] >= 1200, s1["seconds"])
    check("the day was recorded", len(s1["days"]) >= 1, s1["days"])
    check("the genre was sorted out", any(g[0] == "Драма" for g in s1["genres"]), s1["genres"])
    check("the title made it into the list", any(t[0] == "Т" for t in s1["titles"]), s1["titles"])

    # ------------------------------------------------------------------
    group("A rate limit on writing")
    reset()
    w = C()
    login(w, "misha", "Tihiy-Signal-2026")
    codes = [w.post("/api/library/progress",
                    json={"key": f"r{i}", "position": i}).status_code for i in range(120)]
    check("frequent writing is slowed down", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")

    # ------------------------------------------------------------------
    group("The pages are served")
    reset()
    p = C()
    login(p, "valera", "Zaliv-Pepel-2026")
    for path in ["/", "/watch", "/stats"]:
        r = p.get(path)
        check(f"{path} is served", r.status_code == 200 and "<html" in r.text.lower(), r.status_code)
    for f in ["app.css", "app.js", "index.js", "watch.js", "stats.js", "hls.min.js"]:
        r = p.get("/static/" + f)
        check(f"{f} is served", r.status_code == 200 and len(r.content) > 100, r.status_code)
    check("a page that does not exist", p.get("/hacker").status_code == 404)
    check("a file that does not exist", p.get("/static/нет.js").status_code == 404)

    # ------------------------------------------------------------------
    group("Headers")
    h = p.get("/").headers
    csp = h.get("content-security-policy", "")
    check("there is a content policy", bool(csp))
    check("scripts from our own site only", "script-src 'self';" in csp, csp[:60])
    check("executing strings is forbidden", "unsafe-eval" not in csp)
    check("video from other domains is allowed", "media-src" in csp and "https:" in csp)
    check("a worker from a blob is allowed", "worker-src 'self' blob:" in csp)
    check("embedding in a foreign frame is forbidden", "frame-ancestors 'none'" in csp)
    check("the page is not cached", "no-store" in h.get("cache-control", ""))

    # ------------------------------------------------------------------
    group("Resistance to rubbish")
    reset()
    m = C()
    login(m, "valera", "Zaliv-Pepel-2026")
    junk = [
        ("строка вместо объекта", "просто строка"),
        ("число вместо объекта", 42),
        ("вложенность", {"key": {"a": {"b": {"c": 1}}}}),
        ("массив в поле", {"key": ["a", "b"]}),
        ("null в поле", {"key": None}),
    ]
    for name, body in junk:
        r = m.post("/api/library/progress", json=body)
        check(name + " отклонён без падения", r.status_code in (400, 422), r.status_code)
    check("the server is alive after rubbish", C().get("/api/health").status_code == 200)

    r = m.post("/api/auth/login", content=b"{broken json",
               headers={"Content-Type": "application/json"})
    check("broken json does not knock it over", r.status_code in (400, 422), r.status_code)
    check("the server is alive", C().get("/api/health").status_code == 200)

    # ------------------------------------------------------------------
    group("Search: checking the input")
    reset()
    s = C()
    login(s, "valera", "Zaliv-Pepel-2026")
    check("a module as a source is rejected",
          s.get("/api/search", params={"q": "test", "source": "os"}).status_code == 400)
    check("directory traversal in the source is rejected",
          s.get("/api/search", params={"q": "test", "source": "../../os"}).status_code == 400)
    check("an empty query is rejected",
          s.get("/api/search", params={"q": ""}).status_code == 422)
    check("a negative episode is rejected",
          s.get("/api/videos", params={"key": "k", "ordinal": -1}).status_code == 422)
    check("an enormous episode is rejected",
          s.get("/api/videos", params={"key": "k", "ordinal": 10**9}).status_code == 422)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"PASS 2 — failed: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("PASS 2 — every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
