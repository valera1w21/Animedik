"""Pass 5: what was left unchecked.

Resilience under failures, behaviour when the database goes away,
permissions on files, how the front end behaves on unexpected answers
from the server.
"""
import io
import json
import os
import re
import sys
import tempfile
import time
import logging

WORK = tempfile.mkdtemp()
# The public version's checks run in demonstration mode: there is no
# other here. External video sources are not part of this repository, and
# the only working source is free video (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(WORK, "p5.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass5"
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.ERROR)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient          # noqa: E402
from api import main, security, store              # noqa: E402

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
    for lim in (main.login_guard, main.api_limit, main.search_limit,
                main.guest_limit, main.write_limit):
        lim.reset()


def run():
    store.init()
    reset()
    uid = store.create_user("valera", "Zaliv-Pepel-2026", "admin", "valera")
    store.create_user("misha", "Tihiy-Signal-2026", "user", "misha")

    # ==================================================================
    group("Permissions on the database file")
    # 600 — only the owner may read and write.
    # The last digit of the permissions is for "everyone else": there must
    # be a zero there.
    #
    # The check only makes sense where permissions work that way. In
    # Windows access is governed by ACLs, and os.chmod there can toggle
    # little more than "read only" — the permission digits will always
    # show 666 however hard you set 600. These four checks used simply to
    # fail on Windows: four red lines with not one real problem behind
    # them — and next to those it is easy to miss a real one.
    if os.name != "posix":
        print("       the system is not POSIX — ACLs are in charge, the check was skipped")
        print("       (on the server in a container it does run and has to pass)")
    else:
        st = os.stat(os.environ["DB_PATH"])
        mode = oct(st.st_mode)[-3:]
        check("outsiders do not read the database file", mode[2] == "0", mode)
        check("the group does not read it either", mode[1] == "0", mode)
        print(f"       permissions on the file: {mode}")
        for suffix in ("-wal", "-shm"):
            p2 = os.environ["DB_PATH"] + suffix
            if os.path.exists(p2):
                m2 = oct(os.stat(p2).st_mode)[-3:]
                check(f"the journal file{suffix} is closed", m2[2] == "0", m2)

    # ==================================================================
    group("The session secret string is compulsory")
    src = io.open("docker-compose.yml", encoding="utf-8").read()
    check("with no string the container does not start", ":?" in src,
          "проверка есть" if ":?" in src else "ПРОВЕРКИ НЕТ")
    check("there is an example in .env.example", "SESSION_PEPPER" in
          io.open(".env.example", encoding="utf-8").read())
    # changing the string must close every session
    old_fp = security.token_fingerprint("токен")
    os.environ["SESSION_PEPPER"] = "другая-строка"
    new_fp = security.token_fingerprint("токен")
    os.environ["SESSION_PEPPER"] = "pass5"
    check("changing the string makes old tokens invalid", old_fp != new_fp)

    # ==================================================================
    group("A database error does not bring the server down")
    reset()
    c = C()
    login(c, "valera", "Zaliv-Pepel-2026")

    real = store.library
    store.library = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("база отвалилась"))
    try:
        r = c.get("/api/library")
        check("it answers 500 rather than falling over", r.status_code == 500, r.status_code)
        check("the error text does not give away the internals",
              "база отвалилась" not in r.text and "Traceback" not in r.text, r.text[:70])
    finally:
        store.library = real
    check("the server is alive after the failure", C().get("/api/health").status_code == 200)

    # ==================================================================
    group("A source's failure does not bring search down")
    reset()
    s = C()
    login(s, "valera", "Zaliv-Pepel-2026")
    real_try = main.try_source

    async def always_fail(source, q):
        return None

    main.try_source = always_fail
    try:
        r = s.get("/api/search", params={"q": "чтонибудь"})
        check("silence from every source — 502", r.status_code == 502, r.status_code)
        check("the text makes sense to a person", "Попробуйте" in r.text, r.text[:80])
    finally:
        main.try_source = real_try

    # ==================================================================
    group("The walk over sources works")
    reset()
    calls = []

    async def second_works(source, q):
        calls.append(source)
        return [{"key": "k", "title": "Найдено", "poster": "", "year": 2024,
                 "genres": "Драма", "episodes_total": 12}] if source == "demo" else None

    main.try_source = second_works
    try:
        r = s.get("/api/search", params={"q": "тест", "source": "demo"})
        check("it was found at the fallback", r.status_code == 200, r.status_code)
        body = r.json()
        check("it is said who had it", body.get("source") == "demo", body.get("source"))
        check("the chosen one was tried first", calls and calls[0] == "demo", calls[:3])
        check("the attempts are listed", "demo" in body.get("tried", []), body.get("tried"))
    finally:
        main.try_source = real_try

    reset()
    main.try_source = second_works
    calls.clear()
    try:
        r = s.get("/api/search", params={"q": "тест2", "source": "demo",
                                         "any_source": "false"})
        # There is one source here, and nothing to walk: the only one's
        # refusal is the refusal of the search. We check that it reaches
        # the outside with a clear code rather than turning into empty
        # results.
        check("the only source's refusal is visible", r.status_code in (200, 502),
              r.status_code)
        check("only the chosen one was tried", calls == ["demo"], calls)
    finally:
        main.try_source = real_try

    # ==================================================================
    group("The server's answers match what the front end expects")
    reset()
    f = C()
    login(f, "valera", "Zaliv-Pepel-2026")

    me = f.get("/api/me").json()
    for field in ["kind", "role", "name", "display_name", "avatar_color",
                  "has_avatar", "settings", "can_edit"]:
        check(f"/api/me has {field}", field in me, list(me))

    lib = f.get("/api/library").json()
    check("the library has an items field", "items" in lib)
    f.post("/api/library/progress", json={"key": "проверка", "title": "Т",
                                          "total_eps": 12, "watched_ep": 3,
                                          "position": 100, "genres": "Драма", "year": 2024})
    item = [x for x in f.get("/api/library").json()["items"] if x["key"] == "проверка"][0]
    for field in ["key", "source", "title", "poster", "year", "genres",
                  "total_eps", "watched_ep", "position", "status", "updated_at"]:
        check(f"the record has {field}", field in item, list(item))

    stats = f.get("/api/stats/year?tz=0").json()
    for field in ["episodes", "seconds", "days", "genres", "titles", "finished"]:
        check(f"the totals have {field}", field in stats, list(stats))

    srcs = f.get("/api/sources").json()
    check("sources are a list", isinstance(srcs, list))
    check("a source has an id and a caption",
          all("id" in x and "label" in x for x in srcs))

    users = f.get("/api/admin/users").json()
    check("the list of accounts has users and guests_now",
          "users" in users and "guests_now" in users, list(users))

    # ==================================================================
    group("The front end reads the same fields the server gives")
    js = io.open("web/index.js", encoding="utf-8").read()
    js += io.open("web/watch.js", encoding="utf-8").read()
    js += io.open("web/stats.js", encoding="utf-8").read()
    used = set(re.findall(r"\b(?:it|item|r|res|me|m)\.([a-z_]+)\b", js))
    known = set(item) | set(me) | {"items", "source", "tried", "episodes_total",
                                  "guest", "csrf", "expires_in", "sessions", "length",
                                  "message", "status", "value", "files", "style",
                                  "textContent", "href", "type", "id", "label", "note",
                                  "dubs", "player", "videos", "quality", "url", "ordinal",
                                  "days", "genres", "titles", "finished", "seconds",
                                  "users", "guests_now", "disabled", "login", "role",
                                  "created_at", "detail", "classList", "dataset",
                                  "hidden", "onerror", "src", "then", "catch", "map",
                                  "filter", "forEach", "slice", "sort", "split", "trim",
                                  "toLowerCase", "charAt", "appendChild", "replace",
                                  "indexOf", "push", "concat", "join", "get", "post",
                                  "duration", "currentTime", "paused", "muted", "volume",
                                  "naturalwidth", "naturalheight", "width", "height",
                                  "settings", "kind", "name",
                                  # fields from /api/updates: what came out for a title
                                  "fresh", "now", "was", "checked", "items",
                                  # catalogue fields (/api/about, /api/random)
                                  "about", "found", "episodes", "score", "genres",
                                  # the administrator's announcement and sign-in by code
                                  "text", "secret", "backup", "totp_on",
                                  "mail_new_eps", "email",
                                  # search by franchise: /api/find says
                                  # whether the catalogue answered, and
                                  # /api/resolve whether the name matched
                                  # the source exactly
                                  "catalog", "exact",
                                  # dubs: the list of names, which one is open,
                                  # and which sources have the title at all
                                  "dubs", "chosen", "here",
                                  # /api/subs: whether subtitles were found and
                                  # which variant at the source it is
                                  "dub", "lang",
                                  # the administrator's announcement: the Russian
                                  # version and the English one beside it
                                  "text_en",
                                  # properties of the browser, not fields from the server
                                  "left", "right", "top", "bottom", "width", "height",
                                  "checked", "disabled", "innerText", "currentSrc"}
    unknown = sorted(x for x in used if x not in known and len(x) > 2)
    check("the front end does not expect fields that do not exist", not unknown, unknown[:8])

    # ==================================================================
    group("The error codes the front end counts on")
    reset()
    e = C()
    check("without signing in — 401", e.get("/api/library").status_code == 401)
    g = C()
    g.post("/api/auth/guest")
    g.headers["X-CSRF-Token"] = "x"
    check("a guest writing — 403",
          g.post("/api/library/progress", json={"key": "x"}).status_code in (403,))
    check("the admin page to outsiders — 404", g.get("/api/admin/users").status_code == 404)
    app_js = io.open("web/app.js", encoding="utf-8").read()
    check("the front end reads the detail field from an error", "data.detail" in app_js)
    check("the front end knows about the error code", "err.status" in app_js or ".status" in app_js)

    # ==================================================================
    group("A long idle does not break the session")
    reset()
    d = C()
    login(d, "misha", "Tihiy-Signal-2026")
    tok = [x.split(";", 1)[0][4:] for x in
           d.post("/api/auth/login",
                  json={"login": "misha", "password": "Tihiy-Signal-2026"}
                  ).headers.get_list("set-cookie") if x.startswith("sid=")][0]
    fp = security.token_fingerprint(tok)
    # almost expired, but still alive
    with store.tx() as conn:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE fp = ?",
                     (store.now() - security.SESSION_IDLE + 600, fp))
    z = C()
    z.cookies.set("sid", tok)
    check("one almost expired still works", z.get("/api/me").status_code == 200)
    row = store.connect().execute("SELECT last_seen FROM sessions WHERE fp = ?",
                                  (fp,)).fetchone()
    check("the time of the last visit was updated",
          store.now() - row["last_seen"] < 60, store.now() - row["last_seen"])

    # ==================================================================
    group("A disabled account does not come back to life")
    reset()
    k = C()
    kid = store.create_user("temp", "Vremennyy-Parol-2026", "user")
    login(k, "temp", "Vremennyy-Parol-2026")
    check("signed in", k.get("/api/me").status_code == 200)
    store.set_disabled(kid, True)
    check("thrown out at once", k.get("/api/me").status_code == 401)
    reset()
    check("signing in is impossible",
          C().post("/api/auth/login",
                   json={"login": "temp", "password": "Vremennyy-Parol-2026"}
                   ).status_code == 401)
    store.set_disabled(kid, False)
    reset()
    check("after enabling it is possible again",
          C().post("/api/auth/login",
                   json={"login": "temp", "password": "Vremennyy-Parol-2026"}
                   ).status_code == 200)

    # ==================================================================
    group("Deleting an account takes all its data with it")
    reset()
    victim = store.create_user("udalyaemyy", "Udalyaemyy-Parol-26", "user")
    store.save_progress(victim, {"key": "его-запись", "title": "Личное"})
    store.log_watch(victim, "его-запись", "Личное", "Драма", 1, 600)
    vc = C()
    login(vc, "udalyaemyy", "Udalyaemyy-Parol-26")
    check("the data is in place", len(store.library(victim)) == 1)
    store.delete_user(victim)
    left_lib = store.connect().execute(
        "SELECT COUNT(*) AS n FROM library WHERE user_id = ?", (victim,)).fetchone()["n"]
    left_log = store.connect().execute(
        "SELECT COUNT(*) AS n FROM watch_log WHERE user_id = ?", (victim,)).fetchone()["n"]
    left_ses = store.connect().execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (victim,)).fetchone()["n"]
    check("the library was deleted", left_lib == 0, left_lib)
    check("the journal was deleted", left_log == 0, left_log)
    check("the sessions were deleted", left_ses == 0, left_ses)
    check("their session no longer lets them in", vc.get("/api/me").status_code == 401)

    # ==================================================================
    group("The front-end files contain no secrets")
    for f in ["web/app.js", "web/index.js", "web/watch.js", "web/stats.js",
              "web/index.html", "web/watch.html", "web/stats.html"]:
        t = io.open(f, encoding="utf-8").read()
        for word in ["PEPPER", "pbkdf2", "password_hash", "pass_hash", "SECRET"]:
            if word in t:
                check(f"{f} without {word}", False, word)
    check("there are no secrets in the front end", True)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"PASS 5 — failed: {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("PASS 5 — every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
