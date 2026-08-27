"""Checks for what was found while going through the code.

A separate suite. The point is that the first five were written along
with the code and checked exactly what the author had already thought of.
Here is what he had not: every check below failed on the previous version.

To run:  python check6_audit.py
"""
import os
import re
import sys
import tempfile
import time

# The public version's checks run in demonstration mode: there is no
# other here. External video sources are not part of this repository, and
# the only working source is free video (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "audit.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "audit-pepper"
os.environ["TRUST_PROXY"] = "1"          # как в docker-compose

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging                                    # noqa: E402
logging.getLogger("anime").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)

from fastapi.testclient import TestClient          # noqa: E402
from api import anime, main, security, store       # noqa: E402

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
PASS = "SchastlivyiParol-2026"


def group(name):
    GROUP[0] = name
    print("\n== " + name + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(GROUP[0] + " / " + name)
    return ok


def reset():
    main.login_guard.reset()
    for lim in (main.api_limit, main.search_limit, main.guest_limit, main.write_limit):
        lim.reset()
    main.guests._items.clear()


def login(c, user=None, pwd=None):
    r = c.post("/api/auth/login", json={"login": user or "valera", "password": pwd or PASS})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def without_comments(src):
    """The code with the comments stripped.

    Needed for checks of the kind "such-and-such a piece is gone". Next to
    every fix stands a comment explaining the mistake, and it has to name
    it — otherwise the explanation is useless. A check that fires on such
    an explanation forces it to be deleted: that way exactly the comments
    everything was written for get lost.
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"(?m)//.*$", " ", src)


def run():
    store.init()
    for name, role in (("valera", "admin"), ("misha", "user")):
        if store.get_user_by_login(name) is None:
            store.create_user(name, PASS, role)

    # ==================================================================
    group("A forged address no longer gets round the limits")
    # This is what a request looks like after our nginx: X-Real-IP it
    # REPLACES in full, while X-Forwarded-For by default it APPENDS to —
    # that is, what the visitor made up stays first in the chain. The real
    # address is one and the same.
    def through_nginx(spoof, real="198.51.100.9"):
        return {"X-Forwarded-For": f"{spoof}, {real}", "X-Real-IP": real}

    reset()
    c = TestClient(main.app)
    codes = {}
    for i in range(120):
        r = c.post("/api/auth/login",
                   json={"login": f"nosuch{i:04d}", "password": "Password12345"},
                   headers=through_nginx(f"10.1.{i // 256}.{i % 256}"))
        codes[r.status_code] = codes.get(r.status_code, 0) + 1
    # On the previous version there were 120 answers of 401 here and not
    # one block: every request looked like a new visitor.
    check("password spraying runs into a block", codes.get(429, 0) > 0, codes)
    check("no more than the ceiling per address went through",
          codes.get(401, 0) <= main.login_guard.MAX_PER_IP + 2, codes)

    reset()
    c = TestClient(main.app)
    given = 0
    for i in range(40):
        r = c.post("/api/auth/guest",
                   headers=through_nginx(f"172.16.{i // 256}.{i % 256}"))
        c.cookies.clear()
        if r.status_code == 200:
            given += 1
    # All forty used to be issued against a limit of six.
    check("guest passes are not issued beyond the limit", given <= 8, given)

    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/guest",
               headers={"X-Forwarded-For": "not-an-address, 198.51.100.9",
                        "X-Real-IP": "198.51.100.9"})
    check("rubbish in the chain does not stop the real address being found",
          r.status_code == 200, r.status_code)
    r = c.post("/api/auth/guest", headers={"X-Real-IP": "sdelay-mne-krasivo"})
    c.cookies.clear()
    check("rubbish instead of an address does not knock the server over",
          r.status_code in (200, 429, 503), r.status_code)

    # The nginx setting is part of the same protection, so we check it too.
    conf = open("deploy/nginx-anime.conf", encoding="utf-8").read()
    check("nginx overwrites X-Forwarded-For",
          "proxy_set_header X-Forwarded-For $remote_addr;" in conf)
    check("the overwrite stands after include proxy_params",
          conf.index("include /etc/nginx/proxy_params;")
          < conf.index("proxy_set_header X-Forwarded-For $remote_addr;"))

    # ==================================================================
    group("The form marker is tied to the session")
    reset()
    c = TestClient(main.app)
    login(c)
    real = c.cookies.get("csrf")
    check("a marker was issued", bool(real))
    check("the marker is computed from the session token",
          real == security.csrf_for(c.cookies.get("sid")))

    # We plant our own marker in the cookie and the header at once — that
    # is what an attack through a planted cookie would look like. The old
    # scheme let such a thing through.
    forged = "a" * 32
    c.cookies.set("csrf", forged)
    r = c.post("/api/me/profile", json={"display_name": "взломано"},
               headers={"X-CSRF-Token": forged})
    check("a planted cookie does not get through", r.status_code == 403, r.status_code)

    c.cookies.set("csrf", real)
    r = c.post("/api/me/profile", json={"display_name": "Валера"},
               headers={"X-CSRF-Token": real})
    check("a real marker gets through", r.status_code == 200, r.status_code)

    # ==================================================================
    group("Search: empty and refused are different things")
    reset()
    c = TestClient(main.app)
    login(c)
    saved = main.try_source

    async def nothing(source, q):
        return []

    async def silence(source, q):
        return None

    main.try_source = nothing
    r = c.get("/api/search?q=такогоаниместочнонет")
    check("nothing was found -> 200 and an empty list",
          r.status_code == 200 and r.json()["items"] == [], r.status_code)

    reset()
    main.try_source = silence
    r = c.get("/api/search?q=такогоаниместочнонет")
    check("every source is silent -> 502", r.status_code == 502, r.status_code)
    main.try_source = saved

    # ==================================================================
    group("Settings: only the values that exist in the interface")
    reset()
    c = TestClient(main.app)
    login(c)
    # The "cover size" setting has been taken off the account page
    # entirely: it is set once and never touched again. The field is
    # deleted from the model too, so we check not for a refusal but that
    # it does not settle in the database.
    c.post("/api/me/settings", json={"card_size": 200})
    check("the deleted cover size is not saved",
          "card_size" not in c.get("/api/me").json()["settings"])
    r = c.post("/api/me/settings", json={"logo": 9})
    # 400 from the allow-list or 422 from the request parsing — what matters is that it is not 200
    check("a logo that does not exist is rejected", r.status_code in (400, 422), r.status_code)

    # The settings must survive a write and a read in full.
    store.set_settings(1, {"lang": "en", "accent": "sky"})
    check("the settings read back", store.get_settings(1).get("accent") == "sky")

    # ==================================================================
    group("Changing the password")
    reset()
    c = TestClient(main.app)
    login(c, "misha")
    r = c.post("/api/me/password", json={"current": PASS, "new": PASS})
    check("the new password cannot be the same as the old one", r.status_code == 400, r.status_code)
    r = c.post("/api/me/password", json={"current": PASS, "new": "Drugoi-Parol-2026"})
    check("a different password is accepted", r.status_code == 200, r.status_code)
    store.set_password(store.get_user_by_login("misha")["id"], PASS)

    # ==================================================================
    group("The status in /watched is checked")
    reset()
    c = TestClient(main.app)
    login(c)
    body = {"key": "x:1", "title": "Проверка", "watched_ep": 1, "status": "выдуманный"}
    r = c.post("/api/library/watched", json=body)
    check("an unknown status is rejected", r.status_code == 400, r.status_code)
    body["status"] = "watching"
    r = c.post("/api/library/watched", json=body)
    check("a known status is accepted", r.status_code == 200, r.status_code)

    # ==================================================================
    group("Sessions do not pile up forever")
    reset()
    uid = store.get_user_by_login("misha")["id"]
    store.drop_all_sessions(uid)
    for i in range(store.MAX_SESSIONS_PER_USER + 15):
        store.create_session(uid, security.new_token())
    n = store.count_sessions(uid)
    check("we keep no more than the ceiling", n == store.MAX_SESSIONS_PER_USER, n)
    store.drop_all_sessions(uid)

    # ==================================================================
    group("The source cache is used for what it is for")
    anime._cache.clear()

    class FakeAnime:
        pass

    class FakeItem:
        def __init__(self):
            self.calls = 0

        async def a_get_anime(self):
            self.calls += 1
            return FakeAnime()

    item = FakeItem()
    anime.cache_put("raw:demo:777", item)

    import asyncio
    loop = asyncio.new_event_loop()
    got = loop.run_until_complete(
        anime.find_anime("demo", "777", ""))
    # Before, the blank from the search was never read, and with no title
    # a 409 refusal would have arrived here.
    check("the blank from the search unfolds without going to the network",
          isinstance(got, FakeAnime) and item.calls == 1, item.calls)
    got2 = loop.run_until_complete(
        anime.find_anime("demo", "777", ""))
    check("the second time it is taken from the cache", got2 is got and item.calls == 1, item.calls)
    loop.close()

    # ==================================================================
    group("The pages' markup is assembled correctly")
    from html.parser import HTMLParser
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr",
            "path", "circle", "rect", "ellipse", "stop", "use"}

    class Nest(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []
            self.errs = []

        def handle_starttag(self, tag, attrs):
            if tag not in void:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in void:
                return
            if not self.stack:
                self.errs.append("лишний </%s>" % tag)
            elif self.stack[-1] == tag:
                self.stack.pop()
            elif tag in self.stack:
                self.stack = self.stack[: self.stack.index(tag)]
            else:
                self.errs.append("лишний </%s>" % tag)

    for page in ("index.html", "watch.html", "stats.html"):
        p = Nest()
        with open(os.path.join("web", page), encoding="utf-8") as fh:
            p.feed(fh.read())
        # watch.html had a surplus </div>, because of which the right
        # column fell out of the page grid.
        check(f"{page}: the tags are closed correctly",
              not p.errs and not p.stack, p.errs or p.stack)

    # ==================================================================
    group("The interface covers what it promises")
    js = open("web/index.js", encoding="utf-8").read()
    watch = open("web/watch.js", encoding="utf-8").read()
    check("the admin page has switching off", "/disable" in js)
    check("the admin page has switching on", "/enable" in js)
    check("the admin page has deletion", "api.del('/api/admin/users/" in js)
    check("the auto-next setting is read on the watch page", "autonext" in watch)
    # The automatic mark at 90% now always works, with no setting:
    # there is no reason to switch it off, while a redundant setting piles
    # up code and space in the database.
    check("the automatic mark at 90% works", "0.9" in watch and "autoMarkEnabled" in watch)
    index_html = open("web/index.html", encoding="utf-8").read()
    check("no setting for the automatic mark is left on the account page",
          'data-k="automark"' not in index_html)
    check("saving on leaving the page survives the tab being closed",
          "keepalive" in watch)

    # ==================================================================
    group("No dead code is left")
    app_js = open("web/app.js", encoding="utf-8").read()
    check("the esc function is gone", "function esc(" not in app_js)
    main_py = open("api/main.py", encoding="utf-8").read()
    for dead in ("import json", "import secrets", "Cookie,", "Header,"):
        check(f"nothing unused: {dead}", dead not in main_py)
    check("no deprecated on_event handlers are left", "@app.on_event" not in main_py)

    # ==================================================================
    group('"Who am I" is filled in right after signing in')
    # Found by a live run in a browser. After signing in, index.js called
    # showApp(r.me) directly, while the module variable me in app.js stayed
    # empty until the page was reloaded. The consequences were quiet: an
    # admin saw "Switch off" and "Delete" on their own row, and the
    # library sorting rolled back to the default.
    check("app.js can remember who signed in", "function setMe(" in app_js)
    check("setMe is exposed", "setMe: setMe" in app_js)
    check('signing in and the guest pass update "who am I"',
          js.count("A.setMe(r.me)") >= 2, js.count("A.setMe(r.me)"))
    # We look in the code rather than the text: in watch.js, next to the
    # fix, stands a comment explaining that very mistake, and it has to
    # name it. A check that fires on the explanation of a fixed mistake
    # forces the explanation to be removed — that way exactly the comments
    # everything was written for get lost.
    check("no deprecated window.__sources is left in the code",
          "__sources" not in without_comments(watch)
          and "__sources" not in without_comments(js))

    group("Pages are not cached, files are served with a re-ask")
    check("the rule covers both /watch and /stats",
          '"/", "/watch", "/stats"' in main_py)
    check("a re-ask is set for the files", "no-cache" in main_py)

    group("What was found in a live browser")
    css = open("web/app.css", encoding="utf-8").read()
    # .wrap is both the cover wrapper in a card and the container of the
    # totals page. While the rule was written as plain `.wrap`, it hit
    # both: the cover shrank by half, with 90px of emptiness left under it.
    import re as _re
    bare_wrap = _re.search(r"(?m)^\.wrap\s*\{", css)
    check("the page container's rule does not hit the cards",
          bare_wrap is None, "a bare .wrap{ was found")
    check("the page container is limited to a direct child of body",
          "body > .wrap{" in css)

    # The "remove from the library" button: the endpoint was on the server
    # from the very beginning, while there was nowhere to press it.
    check("the card has a delete button", "function removeCard(" in js)
    check("deletion calls a real endpoint", "api.del('/api/library/" in js)
    check("the button has a style", ".card .del{" in css)
    check("on a touch screen the button is always visible", "@media (hover:none)" in css)

    # Agreement: "1 тайтлов" and "502 серий" kept showing up on the main page.
    check("the agreement of numerals lives in the shared file",
          "function plural(" in app_js and "function say(" in app_js)
    check("the lists use it", "A.say(" in js)

    # The cover, when there is nowhere to take it from
    check("the watch page can fetch a cover", "function ensurePoster(" in watch)

    # An episode's number and their count are different quantities
    check("the last episode's number is counted separately", "function lastOrdinal(" in watch)
    check('"of N" no longer takes the length of the list',
          "' из ', ' of ') + st.episodes.length" not in watch)

    # Full screen: a refusal has to be handled
    check("a refusal of full screen is caught",
          "attempt.catch(" in watch and "webkitEnterFullscreen" in watch)

    group("The filters have been removed entirely")
    # The panel picked only among what was already saved, while there is
    # nothing to search for new anime by genre with: the sources can do
    # text search only. A panel that promises what it does not do has been
    # removed — along with every trace of it.
    html = index_html
    for trace in ('id="filters"', 'id="g-chips"', 'id="y-chips"',
                  'id="f-random"', 'id="f-reset"', 'id="f-count"',
                  'id="btn-filt"', 'id="filt-num"'):
        check(f"no {trace} left in the markup", trace not in html)
    for trace in ("var GENRES", "var YEARS", "buildChips", "readFilters",
                  "state.genres", "state.years", "bucketOf"):
        check(f"no {trace} left in the script", trace not in js)
    check("the panel's styles were removed", ".filters{" not in css and ".frow{" not in css)

    group("The roulette takes a random pick from the catalogue")
    js_code = without_comments(js)
    check("it has moved to the header", 'id="btn-roul"' in html and "$('btn-roul')" in js)
    # It used to spin your own library — that is, it offered what you had
    # already chosen once. Now it takes a random anime from an open
    # catalogue, and the request goes through our server rather than
    # straight from the browser.
    check("it asks the server rather than spinning your own library",
          "api.get('/api/random')" in js)
    check("no drum and none of its geometry is left",
          "function drawReel(" not in js and "function offsetFor(" not in js
          and "reelin" not in js_code)
    check("the result has a cover", "'cover'" in js and ".pickres .cover{" in css)
    check("a description is shown", "it.about" in js)
    check("there is a button to search at the sources",
          'id="roul-watch"' in html and "$('roul-watch')" in js)
    check("the button searches for what came up", "doSearch(it.title)" in js)

    group("The catalogue is reached through our own server")
    catalog_py = open("api/catalog.py", encoding="utf-8").read()
    # A direct request from the browser would send every visitor's address
    # to somebody else's site every time the watch page opened.
    # By code rather than by text: next to the fix stands a comment that
    # has to name the catalogue.
    check("there are no calls to somebody else's catalogue in the browser",
          "anilist" not in js_code.lower()
          and "anilist" not in without_comments(watch).lower())
    check("the server goes there itself", "graphql.anilist.co" in catalog_py)
    check("the description is cleaned of markup", "def clean_description(" in catalog_py)
    check("and trimmed to the setup", "_SPOILER" in catalog_py)
    check("the watch page shows a description", "/api/about" in watch)

    group("The redundant buttons under the player have been removed")
    watch_html = open("web/watch.html", encoding="utf-8").read()
    check("the buttons are not in the markup",
          'id="w-mark"' not in watch_html and 'id="w-skip"' not in watch_html)
    check("and neither are their handlers",
          "w-mark" not in watch and "w-skip" not in watch)

    group("The news and sign-in by code")
    check("the news button is there and only for an admin",
          'id="btn-news"' in html and "$('btn-news').hidden = !isAdmin" in js)
    check("the announcement is inserted as text rather than markup",
          "sitenews-text').textContent" in js)
    check("sign-in by code: the tick box and the code after it",
          'id="s-2fa"' in html and 'id="twofa-box"' in html)
    check("the secret is not served in /api/me",
          "totp_secret" not in open("api/main.py", encoding="utf-8").read()
          .split("def me_payload")[1].split("def ")[0])
    check("switching off asks for the password", "2fa/disable" in js and "password: pass" in js)


    group("Our own dialogs instead of the system ones")
    # The browser is entitled not to show the system confirm/prompt/alert,
    # and after several in a row Chrome offers outright to block them.
    # Because of that "Delete" on a card, "Delete" on an announcement and
    # switching off sign-in by code silently did nothing: the code reached
    # confirm(), got false and left.
    for f in ("app.js", "index.js", "watch.js", "stats.js"):
        code = without_comments(open("web/" + f, encoding="utf-8").read())
        for bad_call in ("confirm(", "prompt(", "alert("):
            # window.confirm = ... in the checks does not count, only calls here
            check(f"{f}: no system {bad_call.rstrip('(')}",
                  bad_call not in code.replace("window." + bad_call, ""),
                  bad_call)
    check("our own dialogs are declared", "function ask(" in app_js and "function tell(" in app_js)
    check("and exposed", "ask: ask" in app_js and "tell: tell" in app_js)
    check("a dialog lies over the account page",
          ".veil.dialog{z-index:400}" in css)
    check("deletion from a card asks with our own dialog", "A.ask({" in js)

    group("Progress is back on the card")
    check("there is an episode counter", "'count'" in js and ".card .count{" in css)
    check("there is a bar", "'line'" in js and ".card .line{" in css)
    # "2 / 1" — the source counted fewer episodes than have been watched.
    check("a mismatch is not shown", "knownTotal" in js)

    group("Switches instead of tick boxes")
    check("sign-in by code is a switch",
          'id="s-2fa"' in html and 'class="sw"' in html)
    check("letters are a switch", 'id="s-mailnew"' in html)
    check("no tick boxes are left", 'type="checkbox" id="s-2fa"' not in html
          and 'type="checkbox" id="s-mailnew"' not in html)
    # The common .sw handler flipped the state a second time over its own.
    check("the common handler takes only settings that have no code of their own",
          "querySelectorAll('.sw[data-k]')" in js)

    group("The catalogue answers in Russian")
    check("the main catalogue is Russian-language", "shikimori.one" in catalog_py)
    check("genres are translated if the answer is English", "GENRE_RU" in catalog_py)
    # HTTP headers are single-byte: Cyrillic in them kills the request before it is sent.
    ua = [l for l in catalog_py.split(chr(10)) if "User-Agent" in l]
    check("there is no Cyrillic in the User-Agent",
          all(all(ord(ch) < 128 for ch in line) for line in ua), ua[:1])
    check("Shikimori's markup is cut out", "_BB_PAIR" in catalog_py)

    group("Loading a source's module is wrapped")
    anime_py = open("api/anime.py", encoding="utf-8").read()
    head = anime_py[anime_py.index("def get_extractor"):]
    head = head[:head.index("# ------")]
    check("import_module is not left bare",
          "try:" in head and "upstream_error" in head)

    # ==================================================================
    print("\n" + "=" * 60)
    if FAILS:
        print("PASS 6 — failed: %d" % len(FAILS))
        for f in FAILS:
            print("   •", f)
        return 1
    print("PASS 6 — every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
