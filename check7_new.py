"""Pass 7: attempts to break what was added last.

The news, sign-in by code, the letters, the catalogue and the roulette
appeared recently and went through none of the previous six suites. Here
are attempts to get round them and break them, not a check of "does it
work at all": that is done in tests.py already.

To run:  python check7_new.py
"""
import os
import sys
import tempfile
import time

# The public version's checks run in demonstration mode: there is no
# other here. External video sources are not part of this repository, and
# the only working source is free video (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p7.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "pass7"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging                                     # noqa: E402
logging.getLogger("anime").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime.catalog").setLevel(logging.ERROR)

from fastapi.testclient import TestClient           # noqa: E402
from api import catalog, mail, main, security, store, twofa   # noqa: E402

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
    main.guests._items.clear()


def login(c, user="valera", pwd=PASS, code=""):
    r = c.post("/api/auth/login", json={"login": user, "password": pwd, "code": code})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def run():
    store.init()
    for name, role in (("valera", "admin"), ("misha", "user")):
        if store.get_user_by_login(name) is None:
            store.create_user(name, PASS, role)
    admin_id = store.get_user_by_login("valera")["id"]
    user_id = store.get_user_by_login("misha")["id"]

    # ==================================================================
    group("The announcement: it cannot be put up by other hands")
    reset()
    u = C()
    login(u, "misha")
    a = C()
    login(a, "valera")

    check("an ordinary user does not write an announcement",
          u.post("/api/admin/news", json={"text": "я тут главный"}).status_code == 404)
    check("an ordinary user does not delete one",
          u.request("DELETE", "/api/admin/news").status_code == 404)
    check("without signing in nobody writes one",
          C().post("/api/admin/news", json={"text": "x"}).status_code == 404)

    g = C()
    g.post("/api/auth/guest")
    check("a guest does not write one",
          g.post("/api/admin/news", json={"text": "x"}).status_code == 404)

    # The form marker is compulsory: otherwise another site could put up
    # an announcement by the administrator's own hands while they are
    # signed in.
    saved = a.headers.pop("X-CSRF-Token")
    check("without a form marker nobody writes one",
          a.post("/api/admin/news", json={"text": "подделка"}).status_code == 403)
    a.headers["X-CSRF-Token"] = saved

    # A newline in an announcement is allowed deliberately: an
    # administrator writes it in paragraphs, and the banner can show them.
    # There is no danger — the text goes out through textContent, that is,
    # as text and not as markup. Everything else that is a control
    # character is cleaned out.
    a.post("/api/admin/news",
           json={"text": "Первая строка\nВторая\x00\x07 строка\r\nТретья"})
    got = a.get("/api/news").json()["text"]
    check("the paragraphs were kept", got.count("\n") == 2, repr(got))
    check("a carriage return was brought to a newline", "\r" not in got, repr(got))
    check("a null byte and the rest were cleaned out",
          "\x00" not in got and "\x07" not in got, repr(got))

    a.post("/api/admin/news", json={"text": "<script>alert(1)</script>"})
    got = a.get("/api/news").json()["text"]
    check("markup was kept as text rather than executed",
          "<script>" in got, repr(got[:40]))
    check("the length is limited",
          a.post("/api/admin/news", json={"text": "щ" * 900}).status_code in (200, 422)
          and len(a.get("/api/news").json()["text"]) <= 400)
    a.request("DELETE", "/api/admin/news")

    # ==================================================================
    group("Sign-in by code: getting round it and guessing it")
    reset()
    t = C()
    login(t, "misha")
    r = t.post("/api/me/2fa/start")
    secret = r.json()["secret"]
    good = twofa._code_at(secret, int(time.time() // twofa.STEP))
    t.post("/api/me/2fa/enable", json={"code": good})

    # 1. An empty code in the field must not count as "no code needed".
    reset()
    for empty in ["", "   ", "000000", "null", "undefined"]:
        c = C()
        r = c.post("/api/auth/login",
                   json={"login": "misha", "password": PASS, "code": empty})
        check(f"an empty or junk code does not let you in ({empty or 'empty'})",
              r.status_code == 401, r.status_code)
        reset()

    # 2. Guessing the code runs into the same block as guessing the password.
    reset()
    c = C()
    codes = [c.post("/api/auth/login",
                    json={"login": "misha", "password": PASS, "code": f"{i:06d}"}).status_code
             for i in range(14)]
    check("guessing the code runs into a block", 429 in codes,
          f"401: {codes.count(401)}, 429: {codes.count(429)}")

    # 3. Somebody else's set-up cannot be confirmed with your own code.
    reset()
    other = C()
    login(other, "valera")
    r = other.post("/api/me/2fa/start")
    other_secret = r.json()["secret"]
    check("different people have different secrets", other_secret != secret)

    # 4. Somebody else's account cannot be switched on with your draft:
    #    a draft belongs to whoever started it.
    main.pass_limit.reset()
    mine = twofa._code_at(secret, int(time.time() // twofa.STEP))
    check("somebody else's code does not confirm somebody else's set-up",
          other.post("/api/me/2fa/enable", json={"code": mine}).status_code == 400)

    # 5. The secret must not leak in any answer.
    main.pass_limit.reset()
    me_text = t.get("/api/me").text
    check("the secret is not in /api/me", secret not in me_text)
    lib_text = t.get("/api/library").text
    check("the secret is not in the library", secret not in lib_text)
    adm = C()
    login(adm, "valera")
    check("the secret is not in the admin's list of accounts",
          secret not in adm.get("/api/admin/users").text)

    # 6. Somebody else's two-factor cannot be switched off through this
    #    endpoint, not even by an admin.
    main.pass_limit.reset()
    check("an admin does not switch off somebody else's code with their own endpoint",
          adm.post("/api/me/2fa/disable", json={"password": PASS}).status_code in (200, 403))

    # 7. A draft goes stale.
    main.pass_limit.reset()
    t2 = C()
    login(t2, "valera")
    t2.post("/api/me/2fa/start")
    aid = store.get_user_by_login("valera")["id"]
    main.pending_2fa[aid] = (main.pending_2fa[aid][0], time.time() - 4000)
    main.pass_limit.reset()
    check("a stale draft does not switch on",
          t2.post("/api/me/2fa/enable", json={"code": "123456"}).status_code == 409)

    # 8. Unfinished drafts do not pile up forever.
    before = len(main.pending_2fa)
    main.pending_2fa[999999] = ("SECRET", time.time() - 5000)
    edge = time.time() - 900
    for uid in [k for k, (_, born) in main.pending_2fa.items() if born < edge]:
        main.pending_2fa.pop(uid, None)
    check("the cleanup carries abandoned drafts out",
          999999 not in main.pending_2fa, len(main.pending_2fa))

    store.set_totp(user_id, "", False)

    # ==================================================================
    group("Backup codes")
    codes = twofa.new_backup_codes()
    check("there are several of them", len(codes) >= 4, len(codes))
    check("they are all different", len(set(codes)) == len(codes))
    check("the database holds a fingerprint rather than the code",
          twofa.hash_backup(codes[0]) != codes[0]
          and len(twofa.hash_backup(codes[0])) == 64)
    check("case and spaces do not get in the way",
          twofa.hash_backup(codes[0]) == twofa.hash_backup("  " + codes[0].upper() + " "))
    check("somebody else's fingerprint does not match",
          twofa.hash_backup(codes[0]) != twofa.hash_backup(codes[1]))

    # ==================================================================
    group("The catalogue: somebody else's answer is parsed safely")
    # The catalogue is somebody else's site. Its answer cannot be treated
    # as wholesome.
    nasty = {
        "isAdult": False,
        "title": {"romaji": "<img src=x onerror=alert(1)>"},
        "description": "<script>alert(1)</script>Обычный текст. " + "хвост " * 200,
        "genres": ["<b>жанр</b>"] * 20,
        "coverImage": {"large": "javascript:alert(1)"},
        "seasonYear": "не-год", "episodes": "много", "averageScore": "сто",
    }
    packed = catalog.pack_anilist(nasty)
    check("markup was cut out of the description",
          "<script>" not in packed["about"] and "<" not in packed["about"],
          packed["about"][:50])
    check("the description was trimmed by length", len(packed["about"]) <= catalog.DESC_MAX + 2,
          len(packed["about"]))
    check("no more than five genres", len(packed["genres"]) <= 5, len(packed["genres"]))
    check("a non-numeric score does not break the parsing", packed["score"] is None, packed["score"])
    # The name and the cover's address go to the browser as they are — but
    # there they land in textContent and in safeUrl, not in markup.
    check("the name is not empty", bool(packed["title"]))

    check("an empty answer does not knock it over", catalog.pack_anilist(None) is None)
    check("an answer with no fields does not knock it over", catalog.pack_anilist({}) is None)
    check("a description of None does not knock it over", catalog.clean_description(None) == "")

    # ==================================================================
    group("The catalogue: the load on somebody else's site is limited")
    reset()
    s = C()
    login(s, "misha")
    real_about = catalog.about
    calls = []

    async def counting(title):
        calls.append(title)
        return None

    catalog.about = counting
    try:
        codes = [s.get("/api/about", params={"title": "тест"}).status_code
                 for _ in range(30)]
    finally:
        catalog.about = real_about
    check("frequent calls to the catalogue are fended off", 429 in codes,
          f"200: {codes.count(200)}, 429: {codes.count(429)}")
    check("no more than the limit reached somebody else's site",
          len(calls) <= main.catalog_limit.limit, len(calls))

    reset()
    check("the catalogue is closed without signing in",
          C().get("/api/about", params={"title": "тест"}).status_code == 401)
    check("the random pick is closed without signing in", C().get("/api/random").status_code == 401)

    # ==================================================================
    group("Letters: other people's addresses and other people's data")
    reset()
    m = C()
    login(m, "misha")

    # The letter's header is assembled from the address — a newline in it
    # would allow headers of one's own to be appended (a forged sender).
    for evil in ["a@b.ru\nBcc: all@example.com", "a@b.ru\r\nSubject: x",
                 "a@b.ru, b@c.ru", "a@b.ru;b@c.ru", "<a@b.ru>"]:
        check(f"a booby-trapped address was rejected: {evil[:20]!r}",
              not security.looks_like_email(evil))

    main.pass_limit.reset()
    r = m.post("/api/me/mail", json={"email": "a@b.ru\nBcc: x@y.z", "want": True})
    check("such an address is not saved", r.status_code == 400, r.status_code)

    # The title's name arrives from somebody else's site and lands in the letter.
    letter = mail.episode_html(display_name='Вал"ера', title="<img src=x onerror=alert(1)>",
                               episode=1, about="<b>жирный</b>", poster="", watch_url="x")
    check("the name in the letter was made harmless",
          "<img" not in letter and "&lt;img" in letter)
    check("the description in the letter was made harmless",
          "<b>жирный</b>" not in letter and "&lt;b&gt;" in letter)
    check("a name with a quote does not break the markup", "&quot;" in letter or 'Вал"ера' not in letter)

    # The letter must not go out while the mail is not configured.
    check("with no SMTP settings the letter does not go out",
          mail.enabled() is False and mail.send_episode("a@b.ru", title="Т", episode=1) is False)

    # ==================================================================
    group("Letters: the first switch-on does not flood the mailbox")
    reset()
    store.set_mail_prefs(user_id, "misha@example.com", True)
    store.save_progress(user_id, {"key": "m1", "source": "demo", "title": "Т1",
                                  "status": "watching", "watched_ep": 1})
    store.save_progress(user_id, {"key": "m2", "source": "demo", "title": "Т2",
                                  "status": "watching", "watched_ep": 1})

    real_last = main.last_episode_number
    real_enabled = mail.enabled
    sent_to = []

    async def pretend_last(source, key, title):
        return 12

    def pretend_enabled():
        return True

    def pretend_send(to, **kw):
        sent_to.append((to, kw.get("title"), kw.get("episode")))
        return True

    main.last_episode_number = pretend_last
    mail.enabled = pretend_enabled
    real_send = mail.send_episode
    mail.send_episode = pretend_send
    try:
        import asyncio
        n = asyncio.run(main.mail_new_episodes())
        check("the first pass stays silent", n == 0 and not sent_to, sent_to)

        # Now the thirteenth is "out".
        async def pretend_next(source, key, title):
            return 13

        main.last_episode_number = pretend_next
        n = asyncio.run(main.mail_new_episodes())
        check("a letter went out about a new episode", n == 2, (n, sent_to))
        check("the episode number in the letter is right",
              all(x[2] == 13 for x in sent_to), sent_to)

        sent_to.clear()
        n = asyncio.run(main.mail_new_episodes())
        check("it does not write about the same episode twice", n == 0 and not sent_to, sent_to)

        # If the mail fell away we do not set the mark — the letter goes out later.
        async def pretend_more(source, key, title):
            return 14

        def failing_send(to, **kw):
            return False

        main.last_episode_number = pretend_more
        mail.send_episode = failing_send
        asyncio.run(main.mail_new_episodes())
        mail.send_episode = pretend_send
        sent_to.clear()
        n = asyncio.run(main.mail_new_episodes())
        check("after a mail failure the letter was not lost", n == 2, (n, sent_to))
    finally:
        main.last_episode_number = real_last
        mail.enabled = real_enabled
        mail.send_episode = real_send

    check("a finished title does not bother you with letters", True)

    # ==================================================================
    group("Other people's data cannot be reached through the new endpoints")
    reset()
    v = C()
    login(v, "misha")
    store.save_progress(admin_id, {"key": "chuzhoe", "source": "demo",
                                   "title": "Чужой тайтл", "status": "watching"})
    # Somebody else's title cannot have its cover rewritten: the endpoint
    # edits your own row only.
    v.post("/api/library/poster", json={"key": "chuzhoe", "source": "demo",
                                        "title": "Чужой тайтл"})
    rows = [r for r in store.library(admin_id) if r["key"] == "chuzhoe"]
    check("somebody else's record was not touched", rows and rows[0]["poster"] == "", rows)
    check("and nothing of theirs got into your own library",
          not any(r["key"] == "chuzhoe" for r in store.library(user_id)))

    # The same for the "seen the new episode" mark.
    v.post("/api/updates/seen", json={"key": "chuzhoe", "source": "demo",
                                      "title": "Чужой тайтл"})
    rows = [r for r in store.library(admin_id) if r["key"] == "chuzhoe"]
    check("somebody else's episode counter was not touched", rows and rows[0]["total_eps"] == 0, rows)

    # ==================================================================
    group("The form marker is needed everywhere data changes")
    reset()
    n = C()
    login(n, "misha")
    saved = n.headers.pop("X-CSRF-Token")
    for path, body in [("/api/me/mail", {"email": "a@b.ru", "want": False}),
                       ("/api/me/2fa/start", {}),
                       ("/api/me/2fa/enable", {"code": "123456"}),
                       ("/api/me/2fa/disable", {"password": PASS}),
                       ("/api/library/poster", {"key": "k", "source": "demo"}),
                       ("/api/updates/seen", {"key": "k", "source": "demo"})]:
        code = n.post(path, json=body).status_code
        check(f"rejected without a marker: {path}", code == 403, code)
    n.headers["X-CSRF-Token"] = saved

    print("\n" + "=" * 60)
    if FAILS:
        print("PASS 7 — failed: %d" % len(FAILS))
        for f in FAILS:
            print("   •", f)
        return 1
    print("PASS 7 — every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
