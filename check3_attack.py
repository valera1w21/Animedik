"""Pass 3: we try to break it deliberately.

Not "does it work" here but "can it be got round": substituted sessions,
races, tricky encodings, logical holes in permissions.
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import logging

# The public version's checks run in demonstration mode: there is no
# other here. External video sources are not part of this repository, and
# the only working source is free video (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p3.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass3"
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


def reset():
    main.login_guard.reset()
    main.api_limit.reset()
    main.search_limit.reset()
    main.guest_limit.reset()
    main.write_limit.reset()


def run():
    store.init()
    reset()
    aid = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")
    mid = store.create_user("misha", "Tihiy-Signal-2026", "user", "misha")
    kid = store.create_user("kate", "Dolina-Otrazheniy-26", "user", "kate")

    # ==================================================================
    group("Substituting a session before signing in")
    # The classic attack: plant a token you know in advance on a victim,
    # so that it becomes valid once they sign in.
    c = C()
    fake = security.new_token()
    c.cookies.set("sid", fake)
    check("your own token does not work before signing in", c.get("/api/me").status_code == 401)
    r = c.post("/api/auth/login", json={"login": "valera", "password": "Zaliv-Pepel-2026"})
    issued = ""
    for raw in r.headers.get_list("set-cookie"):
        if raw.startswith("sid="):
            issued = raw.split(";", 1)[0][4:]
    check("a new token was issued after signing in", issued and issued != fake,
          "совпал!" if issued == fake else "")
    d = C()
    d.cookies.set("sid", fake)
    check("the planted token never did start working", d.get("/api/me").status_code == 401)
    # The server must issue the cookie with an explicit path, otherwise a
    # second one with the same name and a different path can live
    # alongside it.
    paths = [raw for raw in r.headers.get_list("set-cookie")
             if raw.startswith("sid=") and "Path=/" in raw]
    check("the cookie is issued with an explicit path", len(paths) == 1, r.headers.get_list("set-cookie"))

    # ==================================================================
    group("Guest and user tokens do not mix")
    reset()
    g = C()
    rg = g.post("/api/auth/guest")
    gt = [x.split(";", 1)[0][4:] for x in rg.headers.get_list("set-cookie")
          if x.startswith("sid=")][0]
    u = C()
    ru = u.post("/api/auth/login", json={"login": "misha", "password": "Tihiy-Signal-2026"})
    u.headers["X-CSRF-Token"] = ru.json()["csrf"]
    ut = [x.split(";", 1)[0][4:] for x in ru.headers.get_list("set-cookie")
          if x.startswith("sid=")][0]
    x = C(); x.cookies.set("sid", gt)
    check("a guest token cannot sign you in as a user",
          x.get("/api/library").json().get("guest") is True)
    check("a guest token cannot open the statistics", x.get("/api/stats/year").status_code == 403)
    y = C(); y.cookies.set("sid", ut)
    check("a user token does not count as a guest one",
          y.get("/api/me").json().get("role") == "user")
    check("the guest token is not in the database",
          store.session_user(gt) is None)

    # ==================================================================
    group("Guessing tokens")
    reset()
    tries = 0
    for i in range(60):
        t = C()
        t.cookies.set("sid", security.new_token())
        if t.get("/api/me").status_code == 200:
            tries += 1
    check("sixty random tokens did not fit", tries == 0, tries)
    # the token's length: 32 bytes = 256 bits
    check("the token is long enough", len(security.new_token()) >= 43, len(security.new_token()))

    # ==================================================================
    group("Tricky logins")
    reset()
    tricky = [
        ("VALERA", "верхний регистр — тот же человек", 200),
        ("  valera  ", "пробелы по краям", 200),
        ("valera\u0000", "нулевой байт", 401),
        # A newline at the edges is the same surplus emptiness as a space:
        # that is removed deliberately, because it often arrives from the
        # clipboard.
        ("valera\n", "перевод строки по краю убирается", 200),
        ("vale\nra", "перевод строки внутри", 401),
        ("valera\u202eadmin", "переворот направления текста", 401),
        ("ＶＡＬＥＲＡ", "полноширинные буквы", 200),
        ("valera'--", "хвост как в SQL", 401),
        ("valerа", "кириллическая а внутри", 401),
    ]
    for value, why, want in tricky:
        reset()
        r = C().post("/api/auth/login", json={"login": value, "password": "Zaliv-Pepel-2026"})
        check(f"{why}: {r.status_code}", r.status_code == want, f"expected {want}")

    # ==================================================================
    group("The response time does not give away which logins exist")
    reset()

    def timed(login_name):
        best = 999
        for _ in range(3):
            reset()
            t0 = time.perf_counter()
            C().post("/api/auth/login", json={"login": login_name, "password": "Nevernyy-Parol-26"})
            best = min(best, time.perf_counter() - t0)
        return best

    t_real = timed("valera")
    t_fake = timed("nikogo-net-takogo")
    ratio = max(t_real, t_fake) / max(0.0001, min(t_real, t_fake))
    check("the times are comparable", ratio < 3, f"{t_real:.3f}s against {t_fake:.3f}s, difference x{ratio:.1f}")

    # ==================================================================
    group("Writing the same thing at the same time")
    reset()
    w = C()
    login(w, "valera", "Zaliv-Pepel-2026")
    errors = []

    def hammer(n):
        try:
            cc = C()
            cc.cookies = w.cookies
            cc.headers["X-CSRF-Token"] = w.headers["X-CSRF-Token"]
            for i in range(6):
                r = cc.post("/api/library/progress",
                            json={"key": "race", "position": n * 100 + i})
                if r.status_code not in (200, 429):
                    errors.append(r.status_code)
        except Exception as exc:                   # noqa: BLE001
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("simultaneous writes do not break the database", not errors, errors[:4])
    rows = [x for x in w.get("/api/library").json()["items"] if x["key"] == "race"]
    check("one record was left", len(rows) == 1, len(rows))

    # ==================================================================
    group("Permissions: demoting and deleting an administrator")
    reset()
    a = C()
    login(a, "valera", "Zaliv-Pepel-2026")
    check("you cannot delete yourself", a.request("DELETE", f"/api/admin/users/{aid}").status_code == 409)
    check("the last administrator cannot be switched off",
          a.post(f"/api/admin/users/{aid}/disable").status_code == 409)
    r = a.post("/api/admin/users", json={"login": "vtoroy", "password": "Vtoroy-Admin-2026",
                                         "role": "admin"})
    check("a second administrator was created", r.status_code == 200, r.text[:70])
    # This used to check the opposite: "now you can switch yourself off".
    # That was not a protection but a description of a bug, fixed in place
    # by a check. While there is one administrator, the button is held by
    # the "last administrator" rule; create a second one — and that same
    # button on your own row closed your own way in and cut off your own
    # session. A separate check for yourself, as on deletion, closes that
    # for good.
    check("you cannot switch yourself off even when there are two",
          a.post(f"/api/admin/users/{aid}/disable").status_code == 409)
    check("your own session is intact", a.get("/api/me").status_code == 200)
    vtoroy = store.get_user_by_login("vtoroy")
    check("but another administrator can be switched off",
          a.post(f"/api/admin/users/{vtoroy['id']}/disable").status_code == 200)
    store.set_disabled(vtoroy["id"], False)

    # ==================================================================
    group("An ordinary user cannot become an administrator")
    reset()
    m = C()
    login(m, "misha", "Tihiy-Signal-2026")
    check("you cannot make yourself an admin",
          m.post("/api/admin/users", json={"login": "hax", "password": "Hacker-Parol-2026",
                                           "role": "admin"}).status_code == 404)
    check("you cannot switch off somebody else's account",
          m.post(f"/api/admin/users/{kid}/disable").status_code == 404)
    check("the role does not change through the settings",
          m.post("/api/me/settings", json={"role": "admin"}).status_code == 200)
    check("the role stayed as it was", m.get("/api/me").json()["role"] == "user")
    check("the role does not change through the profile",
          m.post("/api/me/profile", json={"role": "admin", "display_name": "x"}).status_code == 200)
    check("the role is still as it was", m.get("/api/me").json()["role"] == "user")

    # ==================================================================
    group("Other people's data is out of reach")
    reset()
    k = C()
    login(k, "kate", "Dolina-Otrazheniy-26")
    k.post("/api/library/progress", json={"key": "секрет-кати", "title": "Личное"})
    m2 = C()
    login(m2, "misha", "Tihiy-Signal-2026")
    mine = [x["key"] for x in m2.get("/api/library").json()["items"]]
    check("somebody else's record is not visible", "секрет-кати" not in mine)
    m2.request("DELETE", "/api/library/секрет-кати")
    still = [x["key"] for x in k.get("/api/library").json()["items"]]
    check("somebody else's record cannot be deleted", "секрет-кати" in still)
    a2 = C()
    login(a2, "valera", "Zaliv-Pepel-2026")
    admin_sees = [x["key"] for x in a2.get("/api/library").json()["items"]]
    check("even an administrator does not see somebody else's shelf", "секрет-кати" not in admin_sees)

    # ==================================================================
    group("Signing out really does close the way in")
    reset()
    o = C()
    ro = o.post("/api/auth/login", json={"login": "misha", "password": "Tihiy-Signal-2026"})
    o.headers["X-CSRF-Token"] = ro.json()["csrf"]
    tok = [x.split(";", 1)[0][4:] for x in ro.headers.get_list("set-cookie")
           if x.startswith("sid=")][0]
    check("it works before signing out", o.get("/api/me").status_code == 200)
    o.post("/api/auth/logout")
    check("it does not work after signing out", o.get("/api/me").status_code == 401)
    z = C(); z.cookies.set("sid", tok)
    check("the old token is dead even in another browser", z.get("/api/me").status_code == 401)
    check("the session is not in the database", store.session_user(tok) is None)

    # ==================================================================
    group("The form marker: forgery")
    reset()
    f = C()
    login(f, "misha", "Tihiy-Signal-2026")
    real = f.headers["X-CSRF-Token"]
    bad_values = ["", "x", real[:-1], real + "a", real.upper(), "null"]
    for v in bad_values:
        f.headers["X-CSRF-Token"] = v
        code = f.post("/api/library/progress", json={"key": "csrf"}).status_code
        check(f"marker {v[:10] or 'empty'} rejected", code == 403, code)
    f.headers["X-CSRF-Token"] = real
    check("a real marker works",
          f.post("/api/library/progress", json={"key": "csrf"}).status_code == 200)

    # ==================================================================
    group("One person's marker does not fit another")
    reset()
    u1, u2 = C(), C()
    login(u1, "misha", "Tihiy-Signal-2026")
    login(u2, "kate", "Dolina-Otrazheniy-26")
    u1.headers["X-CSRF-Token"] = u2.headers["X-CSRF-Token"]
    check("somebody else's marker is rejected",
          u1.post("/api/library/progress", json={"key": "x"}).status_code == 403)

    # ==================================================================
    group("Strange requests")
    reset()
    q = C()
    login(q, "valera", "Zaliv-Pepel-2026")
    check("a very long address", q.get("/api/library?" + "a=1&" * 2000).status_code in (200, 414, 431))
    check("HEAD does not break it", q.head("/api/health").status_code in (200, 405))
    check("an unknown method", q.request("PATCH", "/api/library").status_code in (404, 405))
    check("duplicate keys in json",
          q.post("/api/library/progress",
                 content=b'{"key":"a","key":"b"}',
                 headers={"Content-Type": "application/json"}).status_code in (200, 422))
    check("the wrong content type",
          q.post("/api/library/progress", content=b"key=a",
                 headers={"Content-Type": "text/plain"}).status_code in (400, 415, 422))
    check("the server is alive", C().get("/api/health").status_code == 200)

    # ==================================================================
    group("Expired sessions are removed")
    reset()
    e = C()
    re_ = e.post("/api/auth/login", json={"login": "kate", "password": "Dolina-Otrazheniy-26"})
    tok = [x.split(";", 1)[0][4:] for x in re_.headers.get_list("set-cookie")
           if x.startswith("sid=")][0]
    fp = security.token_fingerprint(tok)
    old = store.now() - security.SESSION_IDLE - 10
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE fp = ?", (old, fp))
    check("a long-idle session does not let you in", e.get("/api/me").status_code == 401)
    check("and has been deleted from the database", store.session_user(tok) is None)

    reset()
    e2 = C()
    re2 = e2.post("/api/auth/login", json={"login": "kate", "password": "Dolina-Otrazheniy-26"})
    tok2 = [x.split(";", 1)[0][4:] for x in re2.headers.get_list("set-cookie")
            if x.startswith("sid=")][0]
    fp2 = security.token_fingerprint(tok2)
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET created_at = ? WHERE fp = ?",
                     (store.now() - security.SESSION_MAX_LIFE - 10, fp2))
    check("a session that is too old does not let you in", e2.get("/api/me").status_code == 401)

    # ==================================================================
    group("The password: changing it cuts everything off")
    reset()
    d1, d2, d3 = C(), C(), C()
    for cl in (d1, d2, d3):
        login(cl, "kate", "Dolina-Otrazheniy-26")
    check("three sign-ins are alive", all(cl.get("/api/me").status_code == 200 for cl in (d1, d2, d3)))
    d1.post("/api/me/password", json={"current": "Dolina-Otrazheniy-26", "new": "Novaya-Dolina-2026"})
    check("the other two were thrown out",
          d2.get("/api/me").status_code == 401 and d3.get("/api/me").status_code == 401)
    check("whoever changed it was signed out too", d1.get("/api/me").status_code == 401)
    reset()
    check("the new password works",
          C().post("/api/auth/login",
                   json={"login": "kate", "password": "Novaya-Dolina-2026"}).status_code == 200)

    # ==================================================================
    group("The password does not surface in the answers")
    reset()
    pc = C()
    r = login(pc, "valera", "Zaliv-Pepel-2026")
    check("there is no password in the sign-in answer", "Zaliv-Pepel-2026" not in r.text)
    check("there is no hash in the sign-in answer", "pbkdf2" not in r.text)
    check("there is no password in /api/me", "pbkdf2" not in pc.get("/api/me").text)
    check("there are no hashes in the list of accounts", "pbkdf2" not in pc.get("/api/admin/users").text)
    check("there are no session fingerprints in the list of accounts",
          "fp" not in json.dumps(pc.get("/api/admin/users").json()))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"PASS 3 — failed: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("PASS 3 — every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
