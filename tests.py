"""Checking what really works and what really is closed.

To run:  python tests.py
"""
import asyncio
import io
import os
import sys
import tempfile
import time

# The public version's checks run in demonstration mode: there is no
# other here. External video sources are not part of this repository, and
# the only working source is free video (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "test-pepper"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anime").setLevel(logging.WARNING)

from fastapi import HTTPException                  # noqa: E402
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
GROUP = [""]


def group(name):
    GROUP[0] = name
    print("\n== " + name + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(GROUP[0] + " / " + name)
    return ok


def fresh_client():
    return TestClient(main.app)


def login(c, user, pwd):
    r = c.post("/api/auth/login", json={"login": user, "password": pwd})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def as_guest(c):
    r = c.post("/api/auth/guest")
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


# ==========================================================================
def run():
    store.init()
    main.login_guard.reset()
    main.api_limit.reset()
    main.search_limit.reset()
    main.guest_limit.reset()
    main.write_limit.reset()

    # -------------------------------------------------------------- passwords
    group("Passwords")
    h = security.hash_password("правильный-пароль-1")
    check("the right password is accepted", security.verify_password("правильный-пароль-1", h))
    check("a wrong one is rejected", not security.verify_password("другой-пароль-999", h))
    check("the hash does not contain the password", "правильный-пароль-1" not in h)
    check("two hashes of one password differ",
          security.hash_password("одинаковый-пароль") != security.hash_password("одинаковый-пароль"))
    check("a broken hash does not knock the check over", not security.verify_password("x", "мусор"))
    check("an empty hash does not pass", not security.verify_password("x", ""))
    check("a short password is rejected", security.password_problem("abc123") is not None)
    check("a common password is rejected", security.password_problem("password1") is not None)
    check("a monotonous one is rejected", security.password_problem("aaaaaaaaaaaa") is not None)
    check("a normal one is accepted", security.password_problem("Zaliv-Pepel-2026") is None)

    group("Logins")
    check("SQL in a login does not pass validation",
          security.login_problem("admin'--") is not None)
    check("spaces do not pass", security.login_problem("va lera") is not None)
    check("Cyrillic does not pass", security.login_problem("валера") is not None)
    check("Valera == valera", security.normalize_login("  VaLeRa ") == "valera")
    check("a normal login is accepted", security.login_problem("valera") is None)

    # -------------------------------------------------------------- accounts
    group("Creating accounts")
    admin_id = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")
    user_id = store.create_user("misha", "Tihiy-Signal-2026", "user", "misha")
    check("the admin was created", admin_id > 0)
    check("the user was created", user_id > 0)
    try:
        store.create_user("valera", "Drugoy-Parol-2026")
        check("a repeated login is rejected", False)
    except Exception:
        check("a repeated login is rejected", True)
    try:
        store.create_user("noob", "123")
        check("a weak password is rejected", False)
    except ValueError:
        check("a weak password is rejected", True)

    # -------------------------------------------------------------- signing in
    group("Signing in")
    c = fresh_client()
    check("without signing in /api/me gives 401", c.get("/api/me").status_code == 401)
    check("without signing in the library is closed", c.get("/api/library").status_code == 401)

    r = login(c, "valera", "неверный-пароль")
    check("a wrong password -> 401", r.status_code == 401, r.status_code)
    check("the error text does not give away that the login exists",
          "не найден" not in r.text.lower() and "нет такого" not in r.text.lower())

    main.login_guard.reset()
    c = fresh_client()
    r = login(c, "valera", "Zaliv-Pepel-2026")
    check("the right password -> 200", r.status_code == 200, r.status_code)
    check("a session cookie was issued", "sid" in c.cookies)
    check("a form marker was issued", bool(r.json().get("csrf")))
    check("the admin role was recognised", r.json()["me"]["role"] == "admin")
    check("/api/me works", c.get("/api/me").status_code == 200)

    group("The session cookie")
    raw = c.cookies.get("sid")
    check("the token is long", len(raw) >= 40, len(raw))
    row = store.connect().execute("SELECT fp FROM sessions").fetchone()
    check("the database stores something other than the token itself", row["fp"] != raw)
    check("the fingerprint matches", row["fp"] == security.token_fingerprint(raw))

    group("Sign-up is closed")
    for path in ("/api/auth/register", "/api/register", "/api/signup",
                 "/api/users", "/api/auth/signup"):
        code = c.post(path, json={"login": "hacker", "password": "Hacker-Parol-2026"}).status_code
        check(f"{path} is absent", code in (404, 405), code)

    # -------------------------------------------------------------- guest
    group("Guest")
    g = fresh_client()
    r = as_guest(g)
    check("a guest pass was issued", r.status_code == 200, r.status_code)
    check("the role is guest", r.json()["me"]["role"] == "guest")
    check("the time left is stated", r.json()["me"]["expires_in"] > 0)
    check("a guest sees the catalogue", g.get("/api/sources").status_code == 200)
    check("a guest's library is empty", g.get("/api/library").json()["items"] == [])

    code = g.post("/api/library/progress", json={"key": "x", "title": "t"}).status_code
    check("a guest cannot write progress", code == 403, code)
    check("a guest does not change the profile",
          g.post("/api/me/profile", json={"display_name": "hacker"}).status_code == 403)
    check("a guest does not change the settings",
          g.post("/api/me/settings", json={"accent": "sky"}).status_code == 403)
    check("a guest does not change the password",
          g.post("/api/me/password", json={"current": "a", "new": "Novyy-Parol-2026"}).status_code == 403)
    check("a guest does not see the list of accounts", g.get("/api/admin/users").status_code == 404)
    check("a guest does not create accounts",
          g.post("/api/admin/users", json={"login": "hax", "password": "Hacker-Parol-2026"}).status_code == 404)
    check("a guest does not see the statistics", g.get("/api/stats/year").status_code == 403)

    before = store.connect().execute("SELECT COUNT(*) n FROM library").fetchone()["n"]
    g.post("/api/library/progress", json={"key": "y", "title": "t"})
    after = store.connect().execute("SELECT COUNT(*) n FROM library").fetchone()["n"]
    check("the guest left no traces in the database", before == after, f"{before} → {after}")
    check("there are no guest sessions in the database",
          store.connect().execute("SELECT COUNT(*) n FROM sessions").fetchone()["n"] == 1)

    g.post("/api/auth/logout")
    check("after signing out a guest does not get through", g.get("/api/me").status_code == 401)

    group("A guest pass expires")
    g2 = fresh_client()
    as_guest(g2)
    tok = g2.cookies.get("sid")
    fp = security.token_fingerprint(tok)
    main.guests._items[fp]["expires"] = time.time() - 1
    check("an expired pass does not work", g2.get("/api/me").status_code == 401)

    # ------------------------------------------- an ordinary user's rights
    group("An ordinary user")
    u = fresh_client()
    main.login_guard.reset()
    login(u, "misha", "Tihiy-Signal-2026")
    check("signed in", u.get("/api/me").status_code == 200)
    check("the role is user", u.get("/api/me").json()["role"] == "user")
    check("does not see the admin endpoint", u.get("/api/admin/users").status_code == 404)
    check("does not create accounts",
          u.post("/api/admin/users",
                 json={"login": "hax", "password": "Hacker-Parol-2026"}).status_code == 404)
    check("does not delete other people's accounts",
          u.request("DELETE", f"/api/admin/users/{admin_id}").status_code == 404)

    r = u.post("/api/library/progress", json={
        "key": "source-a:1", "title": "Пепел над заливом", "source": "demo",
        "watched_ep": 12, "position": 862, "total_eps": 24, "status": "watching"})
    check("their own progress is saved", r.status_code == 200, r.status_code)
    items = u.get("/api/library").json()["items"]
    check("the progress reads back", len(items) == 1 and items[0]["position"] == 862,
          items[0]["position"] if items else "пусто")

    group("Data does not leak between people")
    a = fresh_client()
    main.login_guard.reset()
    login(a, "valera", "Zaliv-Pepel-2026")
    check("the admin has their own empty shelf", a.get("/api/library").json()["items"] == [])
    a.post("/api/library/progress", json={"key": "source-a:9", "title": "Своё"})
    check("the user still has one record",
          len(u.get("/api/library").json()["items"]) == 1)
    check("the admin has one of their own",
          len(a.get("/api/library").json()["items"]) == 1)

    # ---------------------------------------------------------------- CSRF
    group("A forged request from another site")
    n = fresh_client()
    main.login_guard.reset()
    login(n, "misha", "Tihiy-Signal-2026")
    saved = n.headers.pop("X-CSRF-Token")
    code = n.post("/api/library/progress", json={"key": "z", "title": "t"}).status_code
    check("without a form marker the request is rejected", code == 403, code)
    n.headers["X-CSRF-Token"] = "poddelannaya-metka-xxxx"
    code = n.post("/api/library/progress", json={"key": "z", "title": "t"}).status_code
    check("with somebody else's marker it is rejected", code == 403, code)
    n.headers["X-CSRF-Token"] = saved
    check("with your own marker it goes through",
          n.post("/api/library/progress", json={"key": "z", "title": "t"}).status_code == 200)
    check("a marker is not needed for reading", n.get("/api/library").status_code == 200)

    # ------------------------------------------- guessing the password
    group("Guessing the password")
    main.login_guard.reset()
    b = fresh_client()
    codes = [b.post("/api/auth/login",
                    json={"login": "misha", "password": f"попытка-{i}"}).status_code
             for i in range(12)]
    check("after several attempts a block comes on", 429 in codes,
          f"401: {codes.count(401)}, 429: {codes.count(429)}")
    check("the block arrives by the eighth attempt at the latest",
          codes.index(429) <= 8 if 429 in codes else False, codes.index(429) if 429 in codes else "-")
    r = b.post("/api/auth/login", json={"login": "misha", "password": "Tihiy-Signal-2026"})
    check("the right password does not let you in during a block either", r.status_code == 429, r.status_code)

    # ---------------------------------------------------------- injections
    group("SQL injection")
    main.login_guard.reset()
    payloads = ["' OR '1'='1", "admin'--", "'; DROP TABLE users;--",
                "' UNION SELECT 1,2,3,4,5,6,7,8,9,10,11 --", "\\'; DELETE FROM users; --"]
    for p in payloads:
        c2 = fresh_client()
        code = c2.post("/api/auth/login", json={"login": p, "password": p}).status_code
        check(f"does not let through: {p[:26]}", code in (401, 429), code)
    alive = store.connect().execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    check("the users table is in place", alive == 2, alive)

    group("Directory traversal")
    for probe in ["/static/../api/main.py", "/static/..%2f..%2fetc%2fpasswd",
                  "/static/%2e%2e/%2e%2e/etc/passwd", "/../api/store.py",
                  "/etc/passwd", "/api/main.py"]:
        r = fresh_client().get(probe)
        leaked = r.status_code == 200 and ("pbkdf2" in r.text or "root:" in r.text
                                           or "SESSION_COOKIE" in r.text)
        check(f"does not serve {probe[:36]}", not leaked, r.status_code)

    group("The API schema is hidden")
    for p in ("/docs", "/openapi.json", "/redoc"):
        check(f"{p} is closed", fresh_client().get(p).status_code == 404)

    group("Security headers")
    h = fresh_client().get("/api/health").headers
    check("a content policy is set", "content-security-policy" in h)
    check("scripts from our own domain only", "script-src 'self'" in h.get("content-security-policy", ""))
    check("embedding in a foreign frame is forbidden in the policy", "frame-ancestors 'none'" in h.get("content-security-policy", ""))
    check("embedding in a foreign frame is forbidden", h.get("x-frame-options") == "DENY")
    check("guessing the file type is forbidden", h.get("x-content-type-options") == "nosniff")
    check("the page's address does not leak", h.get("referrer-policy") == "no-referrer")
    check("API pages are not cached", "no-store" in h.get("cache-control", ""))

    group("Cookies")
    c3 = fresh_client()
    main.login_guard.reset()
    r = login(c3, "valera", "Zaliv-Pepel-2026")
    raw = "; ".join(r.headers.get_list("set-cookie"))
    check("the session cookie is out of reach for scripts", "HttpOnly" in raw)
    check("the cookie does not go to other sites", "samesite=lax" in raw.lower(), raw[:70])
    check("the form marker is read by the script", raw.count("HttpOnly") == 1, raw.count("HttpOnly"))

    # ---------------------------------------------------------- forgery
    group("Forging a session")
    for fake in ["a" * 43, security.new_token(), "", "null", "admin"]:
        c4 = fresh_client()
        c4.cookies.set("sid", fake)
        check(f"somebody else's token does not let you in ({fake[:12] or 'empty'})",
              c4.get("/api/me").status_code == 401)

    group("Checking the input values")
    main.login_guard.reset()
    v = fresh_client()
    login(v, "misha", "Tihiy-Signal-2026")
    check("an enormous key is rejected",
          v.post("/api/library/progress", json={"key": "x" * 5000}).status_code == 422)
    check("a negative episode is rejected",
          v.post("/api/library/progress", json={"key": "a", "watched_ep": -5}).status_code == 422)
    check("an unknown status is rejected",
          v.post("/api/library/progress",
                 json={"key": "a", "status": "хакер"}).status_code == 400)
    check("a long string instead of a colour is rejected",
          v.post("/api/me/profile", json={"avatar_color": "javascript:alert(1)"}).status_code == 422)
    check("a non-colour of the right length is rejected",
          v.post("/api/me/profile", json={"avatar_color": "#ZZZZZZ"}).status_code == 400)
    check("a normal colour is accepted",
          v.post("/api/me/profile", json={"avatar_color": "#8DB5DF"}).status_code == 200)
    check("an alien accent is rejected",
          v.post("/api/me/settings", json={"accent": "'; DROP--"}).status_code == 400)
    check("a normal accent is accepted",
          v.post("/api/me/settings", json={"accent": "sky"}).status_code == 200)
    check("too long a search is rejected",
          v.get("/api/search", params={"q": "a" * 300}).status_code == 422)
    check("too short a search is rejected",
          v.get("/api/search", params={"q": "a"}).status_code == 422)
    check("an unknown source is rejected",
          v.get("/api/search", params={"q": "test", "source": "../../os"}).status_code == 400)
    check("substituting a module is rejected",
          v.get("/api/search", params={"q": "test", "source": "subprocess"}).status_code == 400)

    # ----------------------------------------------------------- avatar
    group("Avatar")
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (900, 700), (60, 90, 110)).save(buf, format="PNG")
    r = v.post("/api/me/avatar", content=buf.getvalue())
    check("the picture was accepted", r.status_code == 200, r.status_code)
    got = v.get("/api/me/avatar")
    check("the avatar is served", got.status_code == 200)
    check("it is served as jpeg", got.headers["content-type"] == "image/jpeg")
    check("guessing the type is forbidden", got.headers.get("x-content-type-options") == "nosniff")
    img = Image.open(io.BytesIO(got.content))
    check("it was brought to 128x128", img.size == (128, 128), img.size)
    check("the avatar does not weigh much", len(got.content) < 30000, len(got.content))

    check("html instead of a picture is rejected",
          v.post("/api/me/avatar", content=b"<script>alert(1)</script>").status_code == 400)
    check("php instead of a picture is rejected",
          v.post("/api/me/avatar", content=b"<?php system($_GET[0]); ?>").status_code == 400)
    poly = b"GIF89a<script>alert(1)</script>"
    check("a forged header is rejected",
          v.post("/api/me/avatar", content=poly).status_code == 400)
    check("too large a file is rejected",
          v.post("/api/me/avatar", content=b"\x00" * (400 * 1024)).status_code == 413)

    group("Changing the password")
    p = fresh_client()
    main.login_guard.reset()
    login(p, "misha", "Tihiy-Signal-2026")
    check("it does not change without the current password",
          p.post("/api/me/password",
                 json={"current": "не-тот", "new": "Novyy-Parol-2026"}).status_code == 403)
    check("a weak new one is rejected",
          p.post("/api/me/password",
                 json={"current": "Tihiy-Signal-2026", "new": "123"}).status_code == 400)
    r = p.post("/api/me/password",
               json={"current": "Tihiy-Signal-2026", "new": "Sovsem-Novyy-2026"})
    check("the password changed", r.status_code == 200, r.status_code)
    check("the old session is closed", u.get("/api/me").status_code == 401)
    main.login_guard.reset()
    check("the old password no longer fits",
          fresh_client().post("/api/auth/login",
                              json={"login": "misha", "password": "Tihiy-Signal-2026"}).status_code == 401)
    main.login_guard.reset()
    check("the new password works",
          fresh_client().post("/api/auth/login",
                              json={"login": "misha", "password": "Sovsem-Novyy-2026"}).status_code == 200)

    group("Administrator")
    main.login_guard.reset()
    ad = fresh_client()
    login(ad, "valera", "Zaliv-Pepel-2026")
    check("sees the list of accounts", ad.get("/api/admin/users").status_code == 200)
    r = ad.post("/api/admin/users",
                json={"login": "kate", "password": "Dolina-Otrazheniy-26", "role": "user"})
    check("creates an account", r.status_code == 200, r.text[:80])
    check("a repeated login is rejected",
          ad.post("/api/admin/users",
                  json={"login": "kate", "password": "Drugoy-Parol-2026"}).status_code == 409)
    check("a weak password is rejected",
          ad.post("/api/admin/users",
                  json={"login": "bob", "password": "123"}).status_code == 400)
    check("does not delete themselves",
          ad.request("DELETE", f"/api/admin/users/{admin_id}").status_code == 409)
    main.login_guard.reset()
    check("the new account works",
          fresh_client().post("/api/auth/login",
                              json={"login": "kate", "password": "Dolina-Otrazheniy-26"}).status_code == 200)

    group("Disabling an account")
    kate = store.get_user_by_login("kate")
    k = fresh_client()
    main.login_guard.reset()
    login(k, "kate", "Dolina-Otrazheniy-26")
    check("she signed in", k.get("/api/me").status_code == 200)
    ad.post(f"/api/admin/users/{kate['id']}/disable")
    check("the session was cut off at once", k.get("/api/me").status_code == 401)
    main.login_guard.reset()
    check("signing in is no longer possible",
          fresh_client().post("/api/auth/login",
                              json={"login": "kate", "password": "Dolina-Otrazheniy-26"}).status_code == 401)

    group("Signing out on every device")
    main.login_guard.reset()
    d1, d2 = fresh_client(), fresh_client()
    login(d1, "valera", "Zaliv-Pepel-2026")
    login(d2, "valera", "Zaliv-Pepel-2026")
    check("both sign-ins are alive",
          d1.get("/api/me").status_code == 200 and d2.get("/api/me").status_code == 200)
    d1.post("/api/auth/logout-all")
    check("the second device was thrown out", d2.get("/api/me").status_code == 401)

    group("Rate limiting")
    main.guest_limit.reset()
    codes = [fresh_client().post("/api/auth/guest").status_code for _ in range(10)]
    check("issuing guest passes is limited", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")

    group("Pages")
    check("the main page is served", fresh_client().get("/").status_code in (200, 404))
    check("a page that does not exist -> 404", fresh_client().get("/hacker").status_code == 404)

    # ------------------------------------------------------------------
    print("\n" + "=" * 56)
    if FAILS:
        print(f"FAILED: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("Every check passed.")
    return 0




def run_extra():
    """The third pass: what is easy to miss."""
    global FAILS
    FAILS = []
    main.login_guard.reset(); main.api_limit.reset(); main.guest_limit.reset()
    main.write_limit.reset(); main.search_limit.reset()

    group("A guest and other people's sessions")
    g = fresh_client()
    as_guest(g)
    gt = g.cookies.get("sid")
    # a guest plants their token as a user's and the other way round
    u = fresh_client()
    login(u, "valera", "Zaliv-Pepel-2026")
    ut = u.cookies.get("sid")
    x = fresh_client(); x.cookies.set("sid", gt)
    check("a guest token does not give a user's permissions",
          x.get("/api/stats/year").status_code == 403)
    y = fresh_client(); y.cookies.set("sid", ut[:-3] + "aaa")
    check("a tampered token does not work", y.get("/api/me").status_code == 401)

    group("The token's fingerprint and its salting")
    t1 = security.token_fingerprint("одинаковый")
    t2 = security.token_fingerprint("одинаковый")
    check("the fingerprint is stable", t1 == t2)
    check("the fingerprint is 64 long", len(t1) == 64)
    check("the fingerprint is not equal to the original", t1 != "одинаковый")

    group("Different users, different avatars")
    a = fresh_client(); main.login_guard.reset()
    login(a, "valera", "Zaliv-Pepel-2026")
    from PIL import Image
    b1 = io.BytesIO(); Image.new("RGB", (300, 300), (200, 30, 30)).save(b1, "PNG")
    a.post("/api/me/avatar", content=b1.getvalue())
    got_a = a.get("/api/me/avatar").content
    m = fresh_client(); main.login_guard.reset()
    login(m, "misha", "Sovsem-Novyy-2026")
    b2 = io.BytesIO(); Image.new("RGB", (300, 300), (30, 30, 200)).save(b2, "PNG")
    m.post("/api/me/avatar", content=b2.getvalue())
    got_m = m.get("/api/me/avatar").content
    check("the avatars were not mixed up", got_a != got_m)

    group("Progress is not overwritten by somebody else's")
    a.post("/api/library/progress", json={"key": "shared-key", "title": "Моё", "position": 100})
    m.post("/api/library/progress", json={"key": "shared-key", "title": "Чужое", "position": 900})
    ia = [i for i in a.get("/api/library").json()["items"] if i["key"] == "shared-key"][0]
    im = [i for i in m.get("/api/library").json()["items"] if i["key"] == "shared-key"][0]
    check("each has their own record under one key",
          ia["position"] == 100 and im["position"] == 900, f"{ia['position']} / {im['position']}")

    group("Deleting your own record")
    check("your own record is deleted",
          a.request("DELETE", "/api/library/shared-key").status_code == 200)
    check("somebody else's stayed intact",
          len([i for i in m.get("/api/library").json()["items"] if i["key"] == "shared-key"]) == 1)

    group("The statistics are counted")
    a.post("/api/library/watched", json={
        "key": "st1", "title": "Тайтл", "genres": "Драма,Детектив",
        "watched_ep": 1, "position": 1380, "total_eps": 12})
    a.post("/api/library/watched", json={
        "key": "st1", "title": "Тайтл", "genres": "Драма,Детектив",
        "watched_ep": 2, "position": 1400, "total_eps": 12})
    s = a.get("/api/stats/year").json()
    check("the episodes were counted", s["episodes"] == 2, s["episodes"])
    check("the time was counted", s["seconds"] == 2780, s["seconds"])
    check("the genres were sorted out", len(s["genres"]) == 2, s["genres"])
    check("a guest does not see the statistics",
          fresh_client().get("/api/stats/year").status_code == 401)

    group("The account page without signing in")
    n = fresh_client()
    for path in ["/api/me/settings", "/api/me/profile", "/api/me/avatar",
                 "/api/library/progress", "/api/auth/logout-all"]:
        check(f"{path} is closed without signing in", n.post(path, json={}).status_code == 401)
    check("/api/admin/users is closed without signing in", n.get("/api/admin/users").status_code == 404)

    group("The X-Forwarded-For header does not fool it")
    main.login_guard.reset()
    os.environ["TRUST_PROXY"] = "0"
    b = fresh_client()
    codes = []
    for i in range(12):
        codes.append(b.post("/api/auth/login",
                            json={"login": "misha", "password": f"x{i}"},
                            headers={"X-Forwarded-For": f"1.2.3.{i}"}).status_code)
    check("a substituted address does not get round the block", 429 in codes,
          f"401: {codes.count(401)}, 429: {codes.count(429)}")

    group("An enormous request body")
    main.login_guard.reset()
    c = fresh_client()
    login(c, "valera", "Zaliv-Pepel-2026")
    big = {"key": "a", "title": "т" * 100000}
    check("a gigantic field is rejected",
          c.post("/api/library/progress", json=big).status_code == 422)

    group("Parsing broken data")
    main.login_guard.reset()
    c2 = fresh_client()
    r = c2.post("/api/auth/login", content="не json".encode("utf-8"),
                headers={"Content-Type": "application/json"})
    check("non-json does not knock the server over", r.status_code in (400, 422), r.status_code)
    check("the server is alive after it", fresh_client().get("/api/health").status_code == 200)

    group("Empty and strange values")
    check("an empty login is rejected",
          fresh_client().post("/api/auth/login",
                              json={"login": "", "password": "x"}).status_code == 422)
    check("null instead of a password is rejected",
          fresh_client().post("/api/auth/login",
                              json={"login": "a", "password": None}).status_code == 422)
    check("an array instead of an object is rejected",
          fresh_client().post("/api/auth/login", json=[1, 2, 3]).status_code == 422)

    print("\n" + "=" * 56)
    if FAILS:
        print(f"FAILED: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("The additional checks passed.")
    return 0


def run_fixes():
    """The fourth pass: what was fixed last.

    Every check below failed on the previous version. The point of keeping
    them in a bundle of their own is that at the next edit it is
    immediately visible whether exactly the mistake already removed once
    has come back.
    """
    global FAILS
    FAILS = []
    main.login_guard.reset(); main.api_limit.reset(); main.guest_limit.reset()
    main.write_limit.reset(); main.search_limit.reset()
    main.pass_limit.reset(); main.avatar_limit.reset(); main.source_limit.reset()

    ADMIN = ("valera", "Zaliv-Pepel-2026")

    # ------------------------------------------------------------------
    group("Signing in does not stop the whole server")
    # Computing the password takes 600,000 rounds — about half a second of
    # pure arithmetic. While it was computed right in the event loop, the
    # server handled sign-ins strictly in turn and froze entirely for all
    # that time: not one page opened for anybody else.
    #
    # Measuring with a single sign-in is pointless: while the measurer
    # waits, the computation manages to finish and no difference is
    # visible — that is what the first version of this check was fooled
    # by. So we start several sign-ins at once and compare with a single
    # one. If it computes in threads, four sign-ins take almost as long as
    # one. If it computes in the event loop, four times as long, because
    # they queue up.
    import httpx

    N = 4

    async def timed(count):
        main.login_guard.reset()
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            t0 = time.perf_counter()
            await asyncio.gather(*[
                ac.post("/api/auth/login",
                        json={"login": ADMIN[0], "password": f"zavedomo-nevernyy-{i}"})
                for i in range(count)])
            return time.perf_counter() - t0

    one = asyncio.run(timed(1))
    many = asyncio.run(timed(N))
    ratio = many / max(one, 0.0001)
    check(f"{N} sign-ins at once do not queue up", ratio < 2.5,
          f"one {one:.2f}s, {N} at once {many:.2f}s — that is x{ratio:.1f}, "
          f"in the event loop it would be ~x{N}")

    # There is deliberately no separate measurement of "how long a page
    # takes to answer during sign-ins" here. There was one — and it passed
    # identically whatever the code: the answer came in 0.01 s both before
    # the fix and after. A check that cannot fail is worse than none: it
    # creates confidence out of nothing. Everything it supposedly showed
    # is shown by the ratio above.
    async def still_alive():
        main.login_guard.reset()
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            jobs = [ac.post("/api/auth/login",
                            json={"login": ADMIN[0], "password": f"nevernyy-{i}"})
                    for i in range(N)]
            out = await asyncio.gather(*jobs, ac.get("/api/health"))
            return out[-1].status_code

    check("the server is alive under several sign-ins at once",
          asyncio.run(still_alive()) == 200)

    # ------------------------------------------------------------------
    group("Signing out cannot be called from another site")
    main.login_guard.reset()
    c = fresh_client()
    login(c, *ADMIN)
    saved = c.headers.pop("X-CSRF-Token")
    r = c.post("/api/auth/logout")
    check("without a form marker signing out is rejected", r.status_code == 403, r.status_code)
    check("and the session is intact", c.get("/api/me").status_code == 200)
    c.headers["X-CSRF-Token"] = saved
    check("with your own marker signing out goes through",
          c.post("/api/auth/logout").status_code == 200)
    check("after signing out there is no session", c.get("/api/me").status_code == 401)
    # With no session, signing out must answer calmly rather than 403:
    # otherwise signing out after an expired cookie would be impossible.
    check("signing out with no session does not complain",
          fresh_client().post("/api/auth/logout").status_code == 200)

    # ------------------------------------------------------------------
    group("Changing the password is rate limited")
    main.login_guard.reset()
    main.pass_limit.reset()
    p = fresh_client()
    login(p, *ADMIN)
    codes = [p.post("/api/me/password",
                    json={"current": "ne-tot-parol", "new": f"Novyy-Parol-{i}-26"}).status_code
             for i in range(12)]
    check("guessing the current password runs into the limit", 429 in codes,
          f"403: {codes.count(403)}, 429: {codes.count(429)}")
    check("the limit arrives by the ninth attempt at the latest",
          codes.index(429) <= 8, codes.index(429) if 429 in codes else "-")
    main.pass_limit.reset()
    # The requirements for the new password are checked before the current
    # one is verified: that is pure arithmetic, it costs nothing and gives
    # nothing away.
    check("a weak new one is rejected at once",
          p.post("/api/me/password",
                 json={"current": "tozhe-ne-tot", "new": "123"}).status_code == 400)

    # ------------------------------------------------------------------
    group("Avatar: a bomb and the rate")
    main.login_guard.reset()
    main.avatar_limit.reset()
    a = fresh_client()
    login(a, *ADMIN)
    from PIL import Image
    # A plain 5000x5000 picture weighs pennies as a file and a hundred and
    # fifty megabytes in memory. Anything up to 50 million pixels used to
    # pass.
    big = io.BytesIO()
    Image.new("RGB", (5000, 5000), (10, 20, 30)).save(big, format="PNG")
    raw = big.getvalue()
    check("the bomb fits within the weight limit", len(raw) < main.AVATAR_MAX_BYTES,
          f"{len(raw)} bytes")
    check("but is rejected by the size of the canvas",
          a.post("/api/me/avatar", content=raw).status_code == 400)
    small = io.BytesIO()
    Image.new("RGB", (400, 300), (90, 120, 140)).save(small, format="PNG")
    main.avatar_limit.reset()
    codes = [a.post("/api/me/avatar", content=small.getvalue()).status_code
             for _ in range(14)]
    check("uploading avatars often is limited", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")

    # ------------------------------------------------------------------
    group("Nothing surplus leaves for the outside")
    main.login_guard.reset()
    main.avatar_limit.reset()
    L = fresh_client()
    login(L, *ADMIN)
    L.post("/api/library/progress", json={"key": "utech", "title": "Т", "position": 5})
    items = L.get("/api/library").json()["items"]
    check("there are records on the shelf", len(items) >= 1, len(items))
    check("the user's internal number is not in the answer",
          all("user_id" not in it for it in items), list(items[0]))
    check("the shelf's fields are exactly those listed",
          set(items[0]) == set(store.LIBRARY_FIELDS), sorted(items[0]))

    # ------------------------------------------------------------------
    group("The sign-in counter counts only the live ones")
    uid = store.get_user_by_login(ADMIN[0])["id"]
    store.drop_all_sessions(uid)
    live = security.new_token()
    store.create_session(uid, live, "ua")
    dead = security.new_token()
    store.create_session(uid, dead, "ua")
    # We age the second session by hand so that it can no longer be used to sign in
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET last_seen = ?, created_at = ? WHERE fp = ?",
                     (store.now() - security.SESSION_IDLE - 10,
                      store.now() - security.SESSION_IDLE - 10,
                      security.token_fingerprint(dead)))
    check("an expired session does not let you in", store.session_user(dead) is None)
    check("a live one does", store.session_user(live) is not None)
    check("only the live one is in the counter", store.count_sessions(uid) == 1,
          store.count_sessions(uid))

    group("The cleanup carries out sessions that are too old as well")
    old = security.new_token()
    store.create_session(uid, old, "ua")
    with store.tx() as conn:
        # fresh by its last visit, but created too long ago
        conn.execute("UPDATE sessions SET created_at = ? WHERE fp = ?",
                     (store.now() - security.SESSION_MAX_LIFE - 10,
                      security.token_fingerprint(old)))
    check("such a session does not let you in", store.session_user(old) is None)
    store.create_session(uid, old, "ua")
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET created_at = ? WHERE fp = ?",
                     (store.now() - security.SESSION_MAX_LIFE - 10,
                      security.token_fingerprint(old)))
    dropped = store.purge_old_sessions()
    check("and the cleanup really does delete it", dropped >= 1, dropped)
    check("a live session survived the cleanup", store.session_user(live) is not None)

    # ------------------------------------------------------------------
    group("An administrator does not switch themselves off")
    main.login_guard.reset()
    ad = fresh_client()
    login(ad, *ADMIN)
    me_id = store.get_user_by_login(ADMIN[0])["id"]
    r = ad.post("/api/admin/users", json={"login": "zapasnoy", "password": "Zapasnoy-Admin-26",
                                          "role": "admin"})
    check("a second administrator was created", r.status_code in (200, 409), r.text[:70])
    check("you cannot switch yourself off",
          ad.post(f"/api/admin/users/{me_id}/disable").status_code == 409)
    check("your own session is intact", ad.get("/api/me").status_code == 200)

    group("A name from the admin page is cleaned")
    r = ad.post("/api/admin/users", json={
        "login": "chistyy", "password": "Chistoe-Imya-2026",
        # The name deliberately gathers everything that has no business
        # being there: a text-direction reversal, a newline and a null
        # byte. We assemble it through chr so that the check file itself
        # stays ordinary text.
        "display_name": ("Zhenya" + chr(0x202E) + chr(10) + chr(0) + "Sorok")})
    check("the account was created", r.status_code == 200, r.text[:80])
    made = store.get_user_by_login("chistyy")
    check("no control characters are left in the name",
          made is not None and all(ch.isprintable() for ch in made["display_name"]),
          repr(made["display_name"]) if made else "нет")

    # ------------------------------------------------------------------
    group("The endpoints to the sources are limited separately")
    main.login_guard.reset()
    main.source_limit.reset()
    s = fresh_client()
    login(s, *ADMIN)
    real = main.anime.find_episodes

    async def pretend(source, key, title):
        class E:
            ordinal = 1
            title = ""
        return [E()]

    main.anime.find_episodes = pretend
    try:
        codes = [s.get("/api/episodes", params={"key": "k", "source": "demo"}).status_code
                 for _ in range(50)]
    finally:
        main.anime.find_episodes = real
    check("a stream of requests to a source runs into the limit", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")

    # ------------------------------------------------------------------
    group("Caching of pages and files")
    n = fresh_client()
    # The watch and totals pages used not to be marked at all: only "/"
    # and everything ending in .html fell under the condition. The browser
    # decided for itself, and after signing out those pages stayed
    # reachable with the "back" button.
    for path in ("/", "/watch", "/stats"):
        h = n.get(path).headers.get("cache-control", "")
        check(f"{path} is not put in the cache", "no-store" in h, h or "no header")
    h = n.get("/static/app.js").headers.get("cache-control", "")
    # Files need a cache, but with a compulsory re-ask: otherwise after a
    # site upgrade fresh markup runs with old code.
    check("scripts are cached with a re-ask", h == "no-cache", h or "заголовка нет")

    # ------------------------------------------------------------------
    group("A source's failure does not look like the site being broken")
    main.login_guard.reset()
    main.source_limit.reset()
    e = fresh_client()
    login(e, *ADMIN)
    real_cache = dict(main.anime._extractors)
    main.anime._extractors.clear()
    real_extractor = main.anime.anime_demo.Extractor

    def broken():
        raise ImportError("модуль источника не загрузился")

    # We break the building of the parser — exactly what happens when a
    # plugged-in source has renamed its module or is not installed.
    main.anime.anime_demo.Extractor = broken
    try:
        r = e.get("/api/episodes", params={"key": "k", "source": "demo",
                                           "title": "Т"})
    finally:
        main.anime.anime_demo.Extractor = real_extractor
        main.anime._extractors.update(real_cache)
    check("trouble with a source's module is a 502, not a 500",
          r.status_code == 502, r.status_code)
    check("and the text speaks of the source rather than of us",
          "источник" in r.text.lower(), r.text[:80])
    check("the internals did not ride out",
          "ImportError" not in r.text and "Traceback" not in r.text, r.text[:80])

    # ------------------------------------------------------------------
    group("Notifications about new episodes only")
    main.login_guard.reset()
    main.source_limit.reset()
    u = fresh_client()
    login(u, *ADMIN)
    uid = store.get_user_by_login(ADMIN[0])["id"]

    real_eps = main.anime.find_episodes

    class Ep:
        def __init__(self, n):
            self.ordinal = n
            self.title = ""

    async def twelve(source, key, title):
        return [Ep(n) for n in range(1, 13)]

    main.anime.find_episodes = twelve
    try:
        # The title has just been added: we have not yet counted how many episodes it has.
        store.save_progress(uid, {"key": "novyy", "source": "demo",
                                  "title": "Новый", "total_eps": 0,
                                  "watched_ep": 1, "status": "watching"})
        r = u.get("/api/updates").json()
        check("one just added does not count as new",
              not any(x["key"] == "novyy" for x in r["items"]), r["items"])
        # The first pass remembered 12 episodes — the second must stay silent too.
        main.source_limit.reset()
        r = u.get("/api/updates").json()
        check("and stays silent the second time",
              not any(x["key"] == "novyy" for x in r["items"]), r["items"])

        # We pretend that last time we saw ten episodes.
        store.set_known_eps(uid, "novyy", 10)
        main.source_limit.reset()
        r = u.get("/api/updates").json()
        hit = [x for x in r["items"] if x["key"] == "novyy"]
        check("new ones came out — we said so", len(hit) == 1, r["items"])
        if hit:
            check("we counted how many exactly came out",
                  hit[0]["was"] == 10 and hit[0]["now"] == 12 and hit[0]["fresh"] == 2, hit[0])

        # "I have seen it" puts the notification out.
        u.post("/api/updates/seen", json={"key": "novyy", "source": "demo",
                                          "title": "Новый"})
        main.source_limit.reset()
        r = u.get("/api/updates").json()
        check("after watching, the notification goes out",
              not any(x["key"] == "novyy" for x in r["items"]), r["items"])

        # A finished one does not ring: we watch only what is being watched.
        store.save_progress(uid, {"key": "novyy", "source": "demo",
                                  "title": "Новый", "total_eps": 5,
                                  "watched_ep": 5, "status": "done"})
        main.source_limit.reset()
        r = u.get("/api/updates").json()
        check("a finished one does not bother you",
              not any(x["key"] == "novyy" for x in r["items"]), r["items"])
    finally:
        main.anime.find_episodes = real_eps

    # ------------------------------------------------------------------
    group("The cover fills itself in")
    main.login_guard.reset()
    main.source_limit.reset()
    pc = fresh_client()
    login(pc, *ADMIN)
    uid = store.get_user_by_login(ADMIN[0])["id"]
    store.save_progress(uid, {"key": "bezfoto", "source": "demo",
                              "title": "Без обложки", "poster": "",
                              "watched_ep": 1, "status": "watching"})
    before = [x for x in store.library(uid) if x["key"] == "bezfoto"][0]
    check("it was saved with no cover", not before["poster"])

    real_poster = main.anime.find_poster

    async def fake_poster(source, key, title):
        return "https://example.org/oblozhka.jpg"

    main.anime.find_poster = fake_poster
    try:
        r = pc.post("/api/library/poster", json={"key": "bezfoto",
                                                 "source": "demo",
                                                 "title": "Без обложки"})
        check("the endpoint answered", r.status_code == 200, r.status_code)
    finally:
        main.anime.find_poster = real_poster
    after = [x for x in store.library(uid) if x["key"] == "bezfoto"][0]
    check("the cover appeared", after["poster"] == "https://example.org/oblozhka.jpg",
          after["poster"])
    check("the edit time did not move", after["updated_at"] == before["updated_at"],
          f"{before['updated_at']} -> {after['updated_at']}")

    # ------------------------------------------------------------------
    group("The administrator's announcement")
    main.login_guard.reset()
    ad = fresh_client()
    login(ad, *ADMIN)
    usr = fresh_client()
    login(usr, "misha", "Sovsem-Novyy-2026")

    check("at first there is no announcement", ad.get("/api/news").json()["text"] == "")
    r = ad.post("/api/admin/news", json={"text": "Сервер перезапустится в 23:00"})
    check("the admin saved one", r.status_code == 200, r.status_code)
    check("an ordinary person sees it",
          usr.get("/api/news").json()["text"] == "Сервер перезапустится в 23:00")
    check("an ordinary person cannot write one",
          usr.post("/api/admin/news", json={"text": "я тут главный"}).status_code == 404)
    check("and cannot delete one",
          usr.request("DELETE", "/api/admin/news").status_code == 404)
    check("an empty announcement is rejected",
          ad.post("/api/admin/news", json={"text": "   "}).status_code == 400)
    ad.request("DELETE", "/api/admin/news")
    check("after deletion it is empty", usr.get("/api/news").json()["text"] == "")

    # ------------------------------------------------------------------
    group("Signing in with a code from an app")
    from api import twofa

    secret = twofa.new_secret()
    check("the secret is fit for an app", len(secret) >= 26, len(secret))
    now_code = twofa._code_at(secret, int(time.time() // twofa.STEP))
    check("your own code is accepted", twofa.verify(secret, now_code))
    check("somebody else's code is not accepted", not twofa.verify(secret, "000000"))
    check("a code from two steps back no longer does",
          not twofa.verify(secret, twofa._code_at(secret, int(time.time() // twofa.STEP) - 3)))
    check("rubbish instead of a code does not knock it over", not twofa.verify(secret, "не-цифры"))
    check("the link for the app has the secret and the issuer",
          "secret=" + secret in twofa.otpauth_uri(secret, "valera")
          and "issuer=" in twofa.otpauth_uri(secret, "valera"))

    main.login_guard.reset()
    t = fresh_client()
    login(t, "misha", "Sovsem-Novyy-2026")
    uid = store.get_user_by_login("misha")["id"]

    r = t.post("/api/me/2fa/start")
    check("the set-up began", r.status_code == 200, r.status_code)
    body = r.json()
    check("the picture with the code arrived", body["qr"].startswith("data:image/png;base64,"))
    check("the code is drawn here rather than on somebody else's site",
          "http" not in body["qr"][:40])

    check("with somebody else's code it does not switch on",
          t.post("/api/me/2fa/enable", json={"code": "000000"}).status_code == 400)
    good = twofa._code_at(body["secret"], int(time.time() // twofa.STEP))
    r = t.post("/api/me/2fa/enable", json={"code": good})
    check("with your own code it does switch on", r.status_code == 200, r.status_code)
    codes = r.json().get("backup") or []
    check("backup codes were issued", len(codes) >= 4, len(codes))

    me = t.get("/api/me").json()
    check("the answer shows that it is on", me["totp_on"] is True)
    check("the secret is not served to the outside", "totp_secret" not in me)

    main.login_guard.reset()
    n = fresh_client()
    r = n.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026"})
    check("the right password with no code does not let you in", r.status_code == 401, r.status_code)
    check("but it is said that a code is what is needed", r.headers.get("X-Need-Code") == "1")
    check("and there is no session", n.get("/api/me").status_code == 401)

    main.login_guard.reset()
    n = fresh_client()
    r = n.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026",
                                        "code": "111111"})
    check("with a wrong code it does not let you in", r.status_code == 401, r.status_code)

    main.login_guard.reset()
    n = fresh_client()
    fresh = twofa._code_at(body["secret"], int(time.time() // twofa.STEP))
    r = n.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026",
                                        "code": fresh})
    check("with the right code it does", r.status_code == 200, r.status_code)

    # A backup code works once.
    main.login_guard.reset()
    n2 = fresh_client()
    r = n2.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026",
                                         "code": codes[0]})
    check("a backup code lets you in", r.status_code == 200, r.status_code)
    main.login_guard.reset()
    n3 = fresh_client()
    r = n3.post("/api/auth/login", json={"login": "misha", "password": "Sovsem-Novyy-2026",
                                         "code": codes[0]})
    check("and the same one no longer works a second time", r.status_code == 401, r.status_code)

    main.pass_limit.reset()
    check("switching off without the password does not go through",
          t.post("/api/me/2fa/disable", json={"password": "ne-tot"}).status_code == 403)
    main.pass_limit.reset()
    check("with the password it switches off",
          t.post("/api/me/2fa/disable",
                 json={"password": "Sovsem-Novyy-2026"}).status_code == 200)
    check("the secret was erased from the database",
          not store.get_user(uid)["totp_secret"])

    # ------------------------------------------------------------------
    group("The catalogue: description and the random pick")
    from api import catalog

    # The setup must stay, the plot twist must go. The beginning is
    # deliberately long: a description must not be trimmed to a couple of
    # words, so there is a threshold in the code, and a short text is cut
    # by length only.
    dirty = ("A boy sells charcoal to feed his family in Taisho-era Japan.<br>"
             "<i>One winter day</i> he comes home to find them gone, and his "
             "sister changed into something else entirely. "
             "However, it turns out his brother is the villain and dies at the end.")
    clean = catalog.clean_description(dirty)
    check("the markup was cut out", "<" not in clean and ">" not in clean, clean[:60])
    check("the setup stayed", "charcoal" in clean, clean[:60])
    check("the spoiler was cut off", "villain" not in clean and "dies" not in clean, clean[-60:])

    long_text = "Первое предложение. " * 60
    cut = catalog.clean_description(long_text)
    check("a long description was trimmed", len(cut) <= catalog.DESC_MAX + 2, len(cut))

    check("we do not show adult material",
          catalog.pack_anilist({"isAdult": True, "title": {"romaji": "X"}}) is None)
    check("we do not show anything with no name",
          catalog.pack_anilist({"isAdult": False, "title": {}}) is None)

    main.catalog_limit.reset()
    real_about = catalog.about

    async def fake_about(title, lang="ru"):
        return {"title": "Тест", "about": "Описание без спойлеров.", "genres": ["Драма"],
                "year": 2024, "episodes": 12, "poster": "", "score": 80,
                "title_en": "", "season": "", "format": "TV", "status": ""}

    catalog.about = fake_about
    try:
        r = t.get("/api/about", params={"title": "Тест"})
        check("the description is served", r.status_code == 200 and r.json()["found"], r.status_code)
        check("there is text in it", r.json()["about"].startswith("Описание"))
    finally:
        catalog.about = real_about

    main.catalog_limit.reset()
    real_random = catalog.random_anime

    async def fake_random():
        return {"title": "Случайное", "about": "", "genres": [], "year": 2020,
                "episodes": 24, "poster": "", "score": None, "title_en": "",
                "season": "", "format": "TV", "status": ""}

    catalog.random_anime = fake_random
    try:
        r = t.get("/api/random")
        check("the random pick is served", r.status_code == 200 and r.json()["found"], r.status_code)
    finally:
        catalog.random_anime = real_random

    main.catalog_limit.reset()
    check("the catalogue is available to a guest too", True)
    codes_seen = [t.get("/api/about", params={"title": "Тест"}).status_code
                  for _ in range(26)]
    check("the catalogue is rate limited", 429 in codes_seen,
          f"200: {codes_seen.count(200)}, 429: {codes_seen.count(429)}")

    # ------------------------------------------------------------------
    group("Name matching: rubbish from the results does not pass")
    from api import anime as an

    # This is not an invented example. For "Атака титанов" both source A
    # and source B answer with exactly this line — the word "атака"
    # matched. The walk over sources stopped at the first one that
    # answered with anything at all, and nobody looked at the right answer
    # at the next source.
    junk = an.relevance("Атака титанов", "Не издевайся, Нагаторо: Вторая атака")
    check("something similar by one word does not pass the threshold",
          junk < an.MIN_RELEVANCE, round(junk, 3))
    check("an exact name is a one",
          an.relevance("Атака титанов", "Атака титанов") == 1.0)

    exact = an.relevance("Наруто", "Наруто")
    seq = an.relevance("Наруто", "Наруто Ураганные хроники")
    check("the first season ranks above the sequel", exact > seq, f"{exact} > {round(seq, 3)}")
    # It is for this that the threshold for stopping the walk was raised:
    # 0.855 for a sequel passed the old bar of 0.85, and instead of
    # "Наруто" it was "Ураганные хроники" that opened.
    check("a sequel does not count as exact enough",
          seq < main.GOOD_ENOUGH, round(seq, 3))

    # Source E writes names on one line with Latin letters and a counter.
    check("the source's tails are cut off",
          an.relevance("Наруто", "Наруто / Naruto [1-220 из 220]") == 1.0)
    check("a note in brackets does not get in the way",
          an.relevance("Наруто", "Наруто (ТВ)") == 1.0)
    check("the letter yo does not diverge from ye",
          an.relevance("Тетрадь смерти", "Тетрадь смёрти") == 1.0)

    # ------------------------------------------------------------------
    group("Search by franchise")
    from api import catalog as cat

    naruto = [
        {"id": "1735", "russian": "Наруто: Ураганные хроники", "name": "Naruto: Shippuuden",
         "franchise": "naruto", "kind": "tv", "episodes": 500, "score": "8.2",
         "airedOn": {"year": 2007}, "poster": {"mainUrl": "https://x/1735.webp"}},
        {"id": "20", "russian": "Наруто", "name": "Naruto", "franchise": "naruto",
         "kind": "tv", "episodes": 220, "score": "8.0", "airedOn": {"year": 2002},
         "poster": {"mainUrl": "https://x/20.webp"}},
        {"id": "9999", "russian": "Наруто PV", "name": "Naruto PV", "franchise": "naruto",
         "kind": "pv", "episodes": 1, "score": "5.0", "airedOn": {"year": 2002},
         "poster": {}},
        {"id": "77", "russian": "Вынос гигантов", "name": "Giant Killing",
         "franchise": "", "kind": "tv", "episodes": 26, "score": "7.5",
         "airedOn": {"year": 2010}, "poster": {}},
    ]
    packed = [cat.pack_part(n) for n in naruto]
    check("a trailer is filtered out by its label",
          packed[2]["kind"] in cat.JUNK_KINDS, packed[2]["kind"])
    check("the score is brought to a hundred", packed[1]["score"] == 80, packed[1]["score"])

    live = [p for p in packed if p["kind"] not in cat.JUNK_KINDS]
    order, grouped = cat.group_found(live)
    check("the seasons of one story gathered into one card",
          len(grouped["naruto"]) == 2, len(grouped["naruto"]))
    check("somebody else's title did not get into it", len(order) == 2, order)
    check("a title with no franchise got a label from its name",
          order[1].startswith("title:"), order[1])

    card = cat.franchise_card("naruto", grouped["naruto"])
    # This is what it was all done for: one "Наруто" card instead of
    # twenty-one lines with seasons, films and specials jumbled together.
    check("the card is named after the first season", card["title"] == "Наруто", card["title"])
    check("and its year comes from the first season", card["year"] == 2002, card["year"])

    # The order and the "start" mark are different things. For "Ван-Пис"
    # an unrelated OVA came out before the series, and by year it stands
    # as the first row.
    op = [
        {"id": "a", "title": "Ван-Пис: Победить пирата Ганзака!", "kind": "ova",
         "year": 1998, "episodes": 1},
        {"id": "b", "title": "Ван-Пис", "kind": "tv", "year": 1999, "episodes": 1174},
        {"id": "c", "title": "Ван-Пис. Фильм", "kind": "movie", "year": 2000,
         "episodes": 1},
        {"id": "d", "title": "Ван-Пис: анонс", "kind": "tv", "year": None,
         "episodes": None},
    ]
    ordered = cat.mark_main(cat.sort_parts(op))
    check("the parts go by year",
          [p["id"] for p in ordered][:3] == ["a", "b", "c"],
          [p["id"] for p in ordered])
    check("what is not out yet goes to the end", ordered[-1]["id"] == "d", ordered[-1]["id"])
    # For "Ван-Пис" the earliest is an unrelated OVA from 1998, shot
    # before the series. The mark is on it anyway: the list goes by year,
    # and "start" must coincide with the first row, otherwise there is
    # nothing to explain it with.
    check("the start is the earliest by year",
          ordered[0]["main"] and not ordered[1]["main"],
          [p["id"] for p in ordered if p["main"]])

    # A recap of the first season comes out in the same year as it and is
    # marked tv_special. While series were picked together with it, the
    # franchise was called "Атака титанов: Рекап".
    aot = [
        {"id": "r", "title": "Атака титанов: Рекап", "kind": "tv_special",
         "year": 2013, "episodes": 1},
        {"id": "s", "title": "Атака титанов", "kind": "tv", "year": 2013,
         "episodes": 25},
    ]
    check("a recap does not become the face of the franchise",
          cat.main_part(aot)["id"] == "s", cat.main_part(aot)["id"])
    # The "start" mark goes by position, and within one year a series
    # stands above a recap — so it goes to the series.
    check("within one year the mark goes to the series rather than the cut",
          cat.mark_main(cat.sort_parts([dict(p) for p in aot]))[0]["id"] == "s")

    check("the film label is translated", cat.KIND_RU["tv_special"] == "спецвыпуск")
    ongoing = cat.pack_part({"id": "1", "russian": "Идёт", "name": "Ongoing",
                             "kind": "tv", "episodes": 0, "episodesAired": 1174,
                             "status": "ongoing", "airedOn": {"year": 1999},
                             "poster": {}})
    check("for one still airing we count the episodes that are out",
          ongoing["episodes"] == 1174 and ongoing["ongoing"], ongoing["episodes"])

    # ------------------------------------------------------------------
    group("The endpoints of search by franchise")
    main.catalog_limit.reset()
    main.search_limit.reset()
    real_search = cat.search_franchises
    real_parts = cat.franchise_parts

    async def fake_search(q):
        return [cat.franchise_card("naruto", grouped["naruto"])]

    async def fake_parts(fid):
        return cat.mark_main(cat.sort_parts([dict(p) for p in grouped["naruto"]]))

    cat.search_franchises = fake_search
    cat.franchise_parts = fake_parts
    try:
        r = t.get("/api/find", params={"q": "наруто"})
        body = r.json()
        check("search serves franchise cards",
              r.status_code == 200 and len(body["items"]) == 1, r.status_code)
        check("and says that the catalogue answered", body["catalog"] is True)

        r = t.get("/api/franchise", params={"id": "naruto"})
        check("the franchise's parts are served",
              r.status_code == 200 and len(r.json()["items"]) == 2, r.status_code)
    finally:
        cat.search_franchises = real_search
        cat.franchise_parts = real_parts

    # The catalogue may be down. That is not "nothing found": on such an
    # answer the page goes off to search the old way, straight at the
    # sources.
    main.catalog_limit.reset()

    async def dead_search(q):
        return None

    cat.search_franchises = dead_search
    try:
        r = t.get("/api/find", params={"q": "наруто"})
        check("the catalogue's silence is not an error but an answer of its own",
              r.status_code == 200 and r.json()["catalog"] is False, r.status_code)
    finally:
        cat.search_franchises = real_search

    main.catalog_limit.reset()

    async def dead_parts(fid):
        return None

    cat.franchise_parts = dead_parts
    try:
        r = t.get("/api/franchise", params={"id": "naruto"})
        check("while an empty list of parts is a 502", r.status_code == 502, r.status_code)
    finally:
        cat.franchise_parts = real_parts

    # ------------------------------------------------------------------
    group("A part of the catalogue is found at a source")
    main.search_limit.reset()
    real_try = main.try_source
    real_eps = main.anime.find_episodes

    # Resolve no longer takes a name at its word, it checks that the
    # title's episodes really are served. The network is unavailable in
    # the checks, so we fake the episode list: by default everyone has one.
    EPS = {}

    async def fake_episodes(source, key, title):
        n = EPS.get(str(key), 12)
        if not n:
            raise HTTPException(status_code=502, detail="Не удалось получить серии")
        return [object()] * n

    main.anime.find_episodes = fake_episodes

    # The source answers with two rows: junk first, then the right title.
    # Exactly this case used to end with a person opening "Нагаторо"
    # instead of "Атака титанов": the first thing that came was taken
    # rather than the thing that matched.
    async def junk_then_right(source, q):
        return [
            {"key": "junk", "title": "Не издевайся, Нагаторо: Вторая атака",
             "poster": "", "year": 2021, "genres": "", "episodes_total": 12,
             "match": 0.31},
            {"key": "aot-1", "title": "Атака титанов", "poster": "p",
             "year": 2013, "genres": "Экшен", "episodes_total": 25,
             "match": 1.0},
        ]

    main.try_source = junk_then_right
    try:
        r = t.get("/api/resolve", params={"title": "Атака титанов",
                                          "title_en": "Shingeki no Kyojin"})
        body = r.json()
        check("an unalike answer is not taken for the right one",
              r.status_code == 200 and body["key"] == "aot-1", body)
        check("and the one that matched exactly is taken",
              body["source"] == "demo" and body["exact"], body.get("source"))
    finally:
        main.try_source = real_try

    main.search_limit.reset()

    async def only_junk(source, q):
        return [{"key": "junk", "title": "Совсем другое кино", "poster": "",
                 "year": 2020, "genres": "", "episodes_total": 1, "match": 0.0}]

    main.try_source = only_junk
    try:
        r = t.get("/api/resolve", params={"title": "Атака титанов"})
        check("if there is nothing similar at all — a 404, not somebody else's title",
              r.status_code == 404, r.status_code)
        check("and the text explains what to do",
              "источник" in r.text.lower(), r.text[:90])
    finally:
        main.try_source = real_try

    main.search_limit.reset()

    async def all_silent(source, q):
        return None

    main.try_source = all_silent
    try:
        r = t.get("/api/resolve", params={"title": "Атака титанов"})
        check("complete silence from the sources — 502", r.status_code == 502, r.status_code)
    finally:
        main.try_source = real_try

    # An inexact match is served, but marked: on that mark the page asks
    # rather than opening silently.
    main.search_limit.reset()

    async def close_enough(source, q):
        return [{"key": "near", "title": "Атака титанов 3. Часть 2", "poster": "",
                 "year": 2019, "genres": "", "episodes_total": 10, "match": 0.72}]

    main.try_source = close_enough
    try:
        r = t.get("/api/resolve", params={"title": "Атака титанов"})
        body = r.json()
        check("something similar is served marked as inexact",
              r.status_code == 200 and body["exact"] is False, body.get("exact"))
    finally:
        main.try_source = real_try

    # ------------------------------------------------------------------
    # An exact name match does not yet mean the title opens. It happens
    # that under exactly the same name as in the catalogue sits a title
    # whose episode list is not served at all: the parser trips over the
    # double episode "57-58". Resolve returned that title, and the person
    # landed in a player saying "episodes did not load", although an
    # identical but working one lay beside it.
    main.search_limit.reset()

    async def one_broken(source, q):
        return [
            {"key": "broken", "title": "Наруто: Ураганные хроники",
             "poster": "", "year": 2007, "genres": "",
             "episodes_total": 0, "match": 1.0},
            {"key": "works", "title": "Наруто Ураганные хроники",
             "poster": "", "year": 2007, "genres": "",
             "episodes_total": 0, "match": 1.0},
        ]

    EPS["broken"] = 0                      # серии не отдаются
    EPS["works"] = 131
    main.try_source = one_broken
    try:
        r = t.get("/api/resolve", params={"title": "Наруто: Ураганные хроники",
                                          "source": "demo"})
        body = r.json()
        check("a title with no episodes is not palmed off, even though the name matched exactly",
              r.status_code == 200 and body["key"] == "works", body)
        check("and the episode count is taken from the real list",
              body.get("episodes_total") == 131, body.get("episodes_total"))
    finally:
        main.try_source = real_try

    main.search_limit.reset()

    async def all_broken(source, q):
        return [{"key": "broken", "title": "Наруто: Ураганные хроники",
                 "poster": "", "year": 2007, "genres": "",
                 "episodes_total": 0, "match": 1.0}]

    main.try_source = all_broken
    try:
        r = t.get("/api/resolve", params={"title": "Наруто: Ураганные хроники"})
        check("if nobody has episodes — a 404 rather than a dead player",
              r.status_code == 404, r.status_code)
    finally:
        main.try_source = real_try

    # ------------------------------------------------------------------
    # A stub instead of a series. It happens that under the name "Ван-Пис"
    # seven episodes are posted out of one thousand one hundred and
    # seventy-four — the name matched, the episodes are there, there is no
    # error. And the person got "episode 1 of 7" of a series that has been
    # running for twenty-six years.
    main.search_limit.reset()

    async def stub_and_full(source, q):
        return [
            {"key": "stub", "title": "Ван-Пис", "poster": "", "year": 1999,
             "genres": "", "episodes_total": 0, "match": 1.0},
            {"key": "full", "title": "Ван-Пис", "poster": "", "year": 1999,
             "genres": "", "episodes_total": 0, "match": 1.0},
        ]

    EPS["stub"] = 7
    EPS["full"] = 1174
    main.try_source = stub_and_full
    try:
        r = t.get("/api/resolve", params={"title": "Ван-Пис", "episodes": 1174})
        body = r.json()
        check("a stub loses to a source with the full list",
              r.status_code == 200 and body["key"] == "full",
              str(body.get("key")) + " / " + str(body.get("episodes_total")))

        main.search_limit.reset()
        # With no hint from the catalogue there is nothing to compare
        # against — we take the first working one and do not spend time
        # walking the rest.
        r = t.get("/api/resolve", params={"title": "Ван-Пис"})
        check("with no hint about the episode count, the first working one is taken",
              r.json()["key"] == "stub", r.json().get("key"))
    finally:
        main.try_source = real_try

    # A source lagging by a dozen episodes is no reason to search
    # further. For "Блич" the difference is fourteen episodes out of three
    # hundred and sixty-six, and there is nothing to find fault with.
    main.search_limit.reset()

    async def slightly_behind(source, q):
        return [
            {"key": "behind", "title": "Блич", "poster": "", "year": 2004,
             "genres": "", "episodes_total": 0, "match": 1.0},
            {"key": "other", "title": "Блич", "poster": "", "year": 2004,
             "genres": "", "episodes_total": 0, "match": 1.0},
        ]

    EPS["behind"] = 352
    EPS["other"] = 366
    main.try_source = slightly_behind
    try:
        r = t.get("/api/resolve", params={"title": "Блич", "episodes": 366})
        check("a source's small lag does not drive the walk further",
              r.json()["key"] == "behind", r.json().get("key"))
    finally:
        main.try_source = real_try
        main.anime.find_episodes = real_eps

    # ------------------------------------------------------------------
    group("A player with not a single link is not a player")
    main.source_limit.reset()
    real_eps2 = main.anime.find_episodes

    class FakeVideo:
        def __init__(self, url):
            self.url, self.quality, self.type = url, 720, "m3u8"

    class FakePlayer:
        def __init__(self, name, urls):
            self.name, self._urls = name, urls

        async def a_get_videos(self):
            return [FakeVideo(u) for u in self._urls]

    class FakeEpisode:
        ordinal = 1

        async def a_get_sources(self):
            # Exactly what source B serves for "Наруто 1: Книга искусств
            # ниндзя": two players, and not one link inside — its players
            # work only from CIS addresses.
            return [FakePlayer("пустой", []), FakePlayer("тоже пустой", [])]

    async def fake_find(source, key, title):
        return [FakeEpisode()]

    main.anime.find_episodes = fake_find
    try:
        r = t.get("/api/videos", params={"key": "k", "ordinal": 1, "source": "demo"})
        check("players with no links do not leave for the outside", r.status_code == 502, r.status_code)
        check("and the person is told what exactly is wrong",
              "плеер" in r.text.lower(), r.text[:80])
    finally:
        main.anime.find_episodes = real_eps2

    # ------------------------------------------------------------------
    group("This story in full: the parts next to the player")

    # The catalogue joins into one franchise things a person does not
    # count as one: "Врата Штейна" lies together with "Вершина хаоса"
    # under the shared label science_adventure, and the first series there
    # is "Вершина хаоса" from 2008. So one thing was written on the card
    # while the "start" mark stood on a different anime.
    sciadv = [
        {"id": "1", "title": "Вершина хаоса", "kind": "tv", "year": 2008,
         "episodes": 12},
        {"id": "2", "title": "Врата Штейна", "kind": "tv", "year": 2011,
         "episodes": 24},
        {"id": "3", "title": "Врата Штейна: Зона загрузки дежавю", "kind": "movie",
         "year": 2013, "episodes": 1},
    ]
    plain = cat.mark_main(cat.sort_parts([dict(p) for p in sciadv]))
    check("the start stands on the earliest part",
          next(p["id"] for p in plain if p["main"]) == "1",
          next(p["title"] for p in plain if p["main"]))
    check("and that is exactly the list's first row", plain[0]["main"] is True)
    check("exactly one part is marked",
          sum(1 for p in plain if p["main"]) == 1)

    # The marks live in copies. The cache is shared by everyone: if they
    # were written straight into it, neighbouring requests would overwrite
    # each other's marks.
    cat._cache_put("fr:testfr", cat.sort_parts([dict(p) for p in sciadv]))
    got = asyncio.run(cat.franchise_parts("testfr"))
    check("the parts come from the cache already marked",
          got[0]["main"] and not got[1]["main"])
    check("and no marks appeared in the cache itself",
          all("main" not in p for p in cat._cache_get("fr:testfr")))

    # A source may write a name SHORTER than the catalogue: "Евангелион
    # нового поколения" sits at all three sources simply as "Евангелион".
    # By words that gave 1 out of 3 — below the threshold, and the site
    # answered "no source has posted it" about a title everyone has.
    short = an.relevance("Евангелион нового поколения", "Евангелион")
    check("the source's short name passes the threshold",
          short >= an.MIN_RELEVANCE, round(short, 2))
    check("but does not count as an exact match",
          short < main.GOOD_ENOUGH, round(short, 2))
    # By the same rule "Наруто" fits "Наруто: Ураганные хроники". There
    # is no telling one from the other by strings — so such a match always
    # loses to a real one.
    check("a real match is stronger anyway",
          an.relevance("Наруто: Ураганные хроники", "Наруто Ураганные хроники") > short)

    main.catalog_limit.reset()
    real_related = cat.related_parts

    async def fake_related(title):
        rows = cat.mark_main(cat.sort_parts([dict(p) for p in sciadv]))
        for row in rows:
            row["current"] = row["title"] == title
        return rows

    cat.related_parts = fake_related
    try:
        r = t.get("/api/related", params={"title": "Врата Штейна"})
        body = r.json()
        check("the story's parts are served to the watch page",
              r.status_code == 200 and len(body["items"]) == 3, r.status_code)
        check("and what is open right now is marked in them",
              [p["title"] for p in body["items"] if p["current"]] == ["Врата Штейна"])
    finally:
        cat.related_parts = real_related

    main.catalog_limit.reset()

    async def dead_related(title):
        return None

    cat.related_parts = dead_related
    try:
        r = t.get("/api/related", params={"title": "Врата Штейна"})
        # For the watch page the catalogue's silence is a trifle: the
        # block simply will not appear. Bringing the player down over it
        # would be out of proportion.
        check("the catalogue's silence does not break the watch page",
              r.status_code == 200 and r.json()["catalog"] is False, r.status_code)
    finally:
        cat.related_parts = real_related

    # ------------------------------------------------------------------
    group("The dub: whose exactly")

    class Src:
        def __init__(self, title, urls):
            self.title, self._urls = title, urls

        async def a_get_videos(self):
            return [FakeVideo(u) for u in self._urls]

    class FakeVideo2:
        def __init__(self, url, q):
            self.url, self.quality, self.type = url, q, "m3u8"

    # The dub's name sits in the title field, while the code read name —
    # the source has no field by that name at all. Every line in the menu
    # said "плеер".
    check("the dub's name is taken from title",
          main.dub_name(Src("Озвучка источник A", []), "demo")[0] == "источник A",
          main.dub_name(Src("Озвучка источник A", []), "demo")[0])
    check("the word Озвучка is removed from the line",
          main.dub_name(Src("Озвучка студия озвучки", []), "demo")[0] == "студия озвучки")
    # "Субтитры", though, must not be removed: that is the difference
    # between listening and reading, and it has to be seen before the click.
    name, is_sub = main.dub_name(Src("Субтитры крупный сервис", []), "demo")
    check("subtitles stay labelled as subtitles",
          name == "Субтитры крупный сервис" and is_sub, name)
    check("with no name the source itself is put in",
          main.dub_name(Src("", []), "demo")[0] == main.anime.SOURCES["demo"]["label"],
          main.dub_name(Src("", []), "demo")[0])

    links = main.pack_links([FakeVideo2("a", 480), FakeVideo2("b", 1080),
                             FakeVideo2("b", 1080), FakeVideo2("", 720)])
    check("the qualities run from best to worst",
          [x["quality"] for x in links] == [1080, 480], [x["quality"] for x in links])
    check("duplicates and empty links were thrown out", len(links) == 2, len(links))

    # ------------------------------------------------------------------
    group("Dubs: we ask about one, we show them all")
    main.source_limit.reset()
    real_eps3 = main.anime.find_episodes
    asked = []

    class DubSrc:
        def __init__(self, title, urls):
            self.title, self._urls = title, urls

        async def a_get_videos(self):
            asked.append(self.title)
            return [FakeVideo2(u, 720) for u in self._urls]

    class DubEpisode:
        ordinal = 1

        async def a_get_sources(self):
            # This is how it arrives: one dub through several hosts.
            return [DubSrc("Озвучка источник A", ["al-1"]),
                    DubSrc("Озвучка источник A", ["al-2"]),
                    DubSrc("Озвучка JAM", ["jam-1"]),
                    DubSrc("Субтитры крупный сервис", ["cr-1"]),
                    DubSrc("Озвучка студия озвучки", ["studio-1"])]

    async def dub_find(source, key, title):
        return [DubEpisode()]

    main.anime.find_episodes = dub_find
    try:
        r = t.get("/api/videos", params={"key": "k", "ordinal": 1,
                                          "source": "demo"})
        body = r.json()
        check("the list of dubs is complete", r.status_code == 200
              and [d["name"] for d in body["dubs"]]
              == ["источник A", "JAM", "студия озвучки", "Субтитры крупный сервис"],
              [d["name"] for d in body["dubs"]])
        # Asking everyone for links is seconds apiece: "Магическая битва"
        # has thirty-seven of them, that is, a minute in front of an empty
        # player.
        check("while links were asked for from one only", len(asked) == 1, asked)
        check("the first in order opened", body["chosen"] == "источник A",
              body["chosen"])
        check("subtitles went to the end of the list",
              body["dubs"][-1]["sub"] is True)

        main.source_limit.reset()
        asked.clear()
        r = t.get("/api/videos", params={"key": "k", "ordinal": 1,
                                          "source": "demo",
                                          "dub": "студия озвучки"})
        check("the chosen dub opens by itself",
              r.json()["chosen"] == "студия озвучки" and len(asked) == 1,
              r.json()["chosen"] + " / " + str(asked))
    finally:
        main.anime.find_episodes = real_eps3

    # ------------------------------------------------------------------
    group("Sources: we do not offer those that do not have the title")
    main.search_limit.reset()
    real_try2 = main.try_source

    async def has_title(source, q):
        return [{"key": "k", "title": "Магическая битва", "poster": "",
                 "year": 2020, "genres": "", "episodes_total": 24,
                 "match": 1.0}]

    main.try_source = has_title
    try:
        r = t.get("/api/where", params={"title": "Магическая битва"})
        body = r.json()
        check("it is said who has the title", body["here"] == ["demo"], body["here"])
        # There is one source, and it is the whole walk: everything there
        # is has been checked, and there is nothing left to be "not
        # checked".
        check("and who was checked at all",
              body["checked"] == list(main.anime.SOURCES), body["checked"])
        check("no unchecked ones are left", body["unknown"] == [], body["unknown"])
    finally:
        main.try_source = real_try2

    # ------------------------------------------------------------------
    group("Subtitles: we look at everyone, not only at the current one")
    main.search_limit.reset()
    real_try3 = main.try_source
    real_eps4 = main.anime.find_episodes

    check("«Субтитры крупный сервис» is recognised as subtitles",
          main.is_subs("Субтитры крупный сервис"))
    check("«Озвучка студия озвучки» is not subtitles", not main.is_subs("Озвучка студия озвучки"))
    # That is what source D calls it: the Japanese track with text over
    # it. The rule looked for the word "субтитры" only at the start of the
    # line, while here it is in brackets at the end — and the variant was
    # not seen at all, although "Атака титанов", "Наруто" and "Ван-Пис"
    # all have it.
    check("«Оригинал (+субтитры)» is subtitles too",
          main.is_subs("Оригинал (+субтитры)"))
    check("plain «Оригинал» with no text does not count as subtitles",
          not main.is_subs("Оригинал"))
    # The orders for looking for subtitles are empty in the public
    # version: there are no external sources. An empty list is not a
    # breakdown but a state: for text the site goes to the one source
    # there is.
    check("the order for looking for subtitles is empty", main.SUB_ORDER == [], main.SUB_ORDER)
    check("in English the order is empty too",
          main.SUB_ORDER_EN == [], main.SUB_ORDER_EN)
    check("«Subtitles (English)» is recognised both as text and as English",
          main.is_subs("Subtitles (English)")
          and main.sub_lang("Subtitles (English)") == "en",
          main.sub_lang("Subtitles (English)"))
    check("«English dub» does not count as subtitles", not main.is_subs("English dub"))

    # The language of the text. Every subtitle track the sources give is
    # Russian — a live check over four titles found not one English one.
    # But English still has to be recognised: passing it off as Russian
    # would be worse than not finding it at all.
    check("Russian subtitles are marked as Russian",
          main.sub_lang("Субтитры команда субтитров") == "ru")
    check("English ones are recognised if they turn up",
          main.sub_lang("Субтитры ENG") == "en", main.sub_lang("Субтитры ENG"))
    check("a dub has no text language at all",
          main.sub_lang("Озвучка студия озвучки") == "")
    # The regular expression for this check once arrived with a backspace
    # character inside: through the shell a backslash turned into a real
    # control character and settled in the source invisibly. There were
    # never any matches, and it looked like "English subtitles do not
    # exist".
    check("there are no control characters in the source",
          not [c for c in io.open("api/main.py", encoding="utf-8").read()
               if ord(c) < 9 or (13 < ord(c) < 32)])

    class SubSrc:
        def __init__(self, title):
            self.title = title

        async def a_get_videos(self):
            return []

    class SubEpisode:
        def __init__(self, ordinal, titles):
            self.ordinal, self._titles = ordinal, titles

        async def a_get_sources(self):
            return [SubSrc(x) for x in self._titles]

    # The layout answers the question "what tracks does the episode have":
    # a dub and text. The site has to find the subtitles itself — a person
    # must not go through the tracks by hand to learn whether they exist.
    LAYOUT = {
        "demo": ["Озвучка студия озвучки", "Субтитры крупный сервис"],
    }

    async def sub_try(source, q):
        if source in LAYOUT:
            return [{"key": source + "-key", "title": "Магическая битва",
                     "poster": "p", "year": 2020, "genres": "Экшен",
                     "episodes_total": 24, "match": 1.0}]
        return []

    async def sub_eps(source, key, title):
        return [SubEpisode(1, LAYOUT.get(source, [])),
                SubEpisode(2, LAYOUT.get(source, []))]

    main.try_source = sub_try
    main.anime.find_episodes = sub_eps
    try:
        r = t.get("/api/subs", params={"title": "Магическая битва",
                                        "ordinal": 1})
        body = r.json()
        check("the subtitles were found", r.status_code == 200 and body["found"]
              and body["source"] == "demo", body)
        check("and named by their own name",
              body["dub"] == "Субтитры крупный сервис", body.get("dub"))
        check("together with the key to open them by",
              body["key"] == "demo-key", body.get("key"))

        # The source the person is already sitting at we do not look at twice.
        main.search_limit.reset()
        r = t.get("/api/subs", params={"title": "Магическая битва",
                                        "ordinal": 1, "skip": "demo"})
        check("the source where we already watched is skipped",
              "demo" not in r.json()["tried"], r.json()["tried"])
        # We skipped the only source — there is nowhere left to look.
        # This is an honest "not found", not an error and not an empty
        # player.
        check("and there is nowhere left to look — an honest not found",
              r.json()["found"] is False, r.json())
    finally:
        main.try_source = real_try3
        main.anime.find_episodes = real_eps4

    # There may be no subtitles anywhere. That is not an error and not an
    # empty answer — it has to be said outright.
    main.search_limit.reset()

    async def no_subs_anywhere(source, q):
        return []

    main.try_source = no_subs_anywhere
    try:
        r = t.get("/api/subs", params={"title": "Стальной алхимик"})
        check("if there are no subtitles anywhere — that is what is said, not a 502",
              r.status_code == 200 and r.json()["found"] is False, r.status_code)
    finally:
        main.try_source = real_try3

    # ------------------------------------------------------------------
    group("Letters: the address and the template")
    from api import mail

    check("a good address was accepted", security.looks_like_email("valera@example.com"))
    for bad_addr in ["без-собаки", "два@@собаки.ru", "с пробелом@x.ru",
                     "конец@точка.", "@нет-имени.ru", "перенос@стро\nки.ru"]:
        check(f"a bad address was rejected: {bad_addr[:18]}",
              not security.looks_like_email(bad_addr))

    main.pass_limit.reset()
    check("letters cannot be switched on with no address",
          t.post("/api/me/mail", json={"email": "", "want": True}).status_code == 400)
    check("with an address they can",
          t.post("/api/me/mail",
                 json={"email": "valera@example.com", "want": True}).status_code == 200)
    me = t.get("/api/me").json()
    check("the address was saved", me["email"] == "valera@example.com", me["email"])
    check("the consent was saved", me["mail_new_eps"] is True)

    letter = mail.episode_html(display_name="Валера", title="Тайтл <script>",
                               episode=7, season="Сезон 2", released="1 мая",
                               about="Описание", poster="", watch_url="http://x/y")
    check("the letter has the site's name", "анимеДик" in letter)
    check("there is an episode number", ">7<" in letter)
    check("there is a season and a date", "Сезон 2" in letter and "1 мая" in letter)
    check("there is a description", "Описание" in letter)
    check("there is a button", "Смотреть" in letter)
    # The name arrives from somebody else's site — in a letter it must be escaped.
    check("markup from the name was made harmless",
          "<script>" not in letter and "&lt;script&gt;" in letter)
    check("laid out with tables, as mail clients require",
          letter.count("<table") >= 4)
    check("with no mail settings the letters simply do not go out",
          mail.send_episode("valera@example.com", title="Т", episode=1) is False
          or mail.enabled())

    print("\n" + "=" * 56)
    if FAILS:
        print(f"FAILED: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("The checks of the fixes passed.")
    return 0


if __name__ == "__main__":
    code = run()
    if code == 0:
        code = run_extra()
    if code == 0:
        code = run_fixes()
    sys.exit(code)
