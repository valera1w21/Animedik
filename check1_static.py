"""Pass 1: looking for mistakes in the code without running it.

We parse the markup and the scripts as text and check everything that can
be checked without a browser: calls to elements that do not exist,
handlers with no buttons, dangerous practices, lost translations.
"""
import io
import json
import os
import re
import subprocess
import sys

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

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

PROBLEMS = []
NOTES = []


def bad(where, what, detail=""):
    PROBLEMS.append((where, what, detail))
    print(f"  ✗ [{where}] {what}" + (f"  -> {detail}" if detail else ""))


def note(where, what, detail=""):
    NOTES.append((where, what, detail))
    print(f"  ~ [{where}] {what}" + (f"  -> {detail}" if detail else ""))


def ok(what):
    print(f"  ✓ {what}")


def read(p):
    return io.open(p, encoding="utf-8").read()


PAGES = {"index": "web/index.js", "watch": "web/watch.js", "stats": "web/stats.js"}
HTML = {"index": "web/index.html", "watch": "web/watch.html", "stats": "web/stats.html"}

print("\n=== 1. Syntax ===")
for f in ["api/main.py", "api/store.py", "api/security.py", "api/anime.py", "api/admin.py"]:
    r = subprocess.run([sys.executable, "-m", "py_compile", f], capture_output=True)
    if r.returncode:
        bad(f, "does not compile", r.stderr.decode()[:120])
# Script syntax is checked by node. It may be absent, and it may be
# broken: on this machine `node --version` answers cheerfully while
# `node --check` falls over inside node itself. Both used to look like
# four "syntax errors" in our code — the check was reporting not that the
# code was broken but that there was nothing to run it with. The worst
# kind of false alarm: it teaches you not to believe the checks at all.
#
# So we first check the tool itself on a knowingly correct file. If it
# cannot manage that, it stays silent about our files too.
def node_works():
    probe = os.path.join(ROOT, ".node-probe.js")
    try:
        with io.open(probe, "w", encoding="utf-8") as fh:
            fh.write("var a = 1;\n")
        r = subprocess.run(["node", "--check", probe], capture_output=True)
        return r.returncode == 0
    except (OSError, ValueError):
        return False
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


if node_works():
    for f in ["web/app.js"] + list(PAGES.values()):
        r = subprocess.run(["node", "--check", f], capture_output=True)
        if r.returncode:
            bad(f, "syntax error", r.stderr.decode("utf-8", "replace")[:160])
    ok("all the code is syntactically correct")
else:
    note("web", "no working node — script syntax was not checked",
         "install node if you want this check")
    ok("the server code is syntactically correct")

print("\n=== 2. Calls to elements that do not exist ===")
for name, js in PAGES.items():
    src, html = read(js), read(HTML[name])
    shared = read("web/app.js")
    ids = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", src))
    ids |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", src + shared))
    miss = sorted(i for i in ids if f'id="{i}"' not in html)
    if miss:
        bad(name, "the script calls elements that are not in the markup", ", ".join(miss))
    sels = set(re.findall(r"querySelector(?:All)?\('([#.][^']+)'\)", src))
    for sel in sels:
        root = re.split(r"[ >,:]", sel)[0]
        if root.startswith("#") and f'id="{root[1:]}"' not in html:
            bad(name, "a lookup by an id that does not exist", sel)
ok("every call to an element resolves")

print("\n=== 3. Buttons with no handlers ===")
for name, js in PAGES.items():
    src, html = read(js), read(HTML[name])
    for m in re.finditer(r'<button[^>]*id="([A-Za-z0-9_-]+)"', html):
        bid = m.group(1)
        # A button can be connected not only directly by id but through a
        # helper — onSwitch('s-2fa', ...) for switches, for instance.
        # Counting such a button as "doing nothing" would be wrong.
        wired = (f"$('{bid}')" in src
                 or f"getElementById('{bid}')" in src
                 or f"onSwitch('{bid}'" in src)
        # a form's submit button is handled through the form itself
        if not wired and 'type="submit"' in m.string[m.start():m.start() + 240]:
            form = re.search(r'<form id="([\w-]+)"', html)
            wired = bool(form and f"$('{form.group(1)}')" in src)
        if not wired:
            bad(name, "the button does nothing", "#" + bid)
    for m in re.finditer(r'<(?:a|button)[^>]*\bclass="[^"]*\bchipbtn\b[^"]*"[^>]*>', html):
        tag = m.group(0)
        if 'id=' not in tag and 'href="#"' in tag:
            note(name, "a stub link with no action", tag[:60])
ok("every button has a handler")

print("\n=== 4. Dangerous practices in the front end ===")
for f in ["web/app.js"] + list(PAGES.values()):
    src = read(f)
    for pat, why in [("innerHTML", "вставка HTML — путь для чужого кода"),
                     ("outerHTML", "то же самое"),
                     ("document.write", "устарело и небезопасно"),
                     ("eval(", "исполнение строки"),
                     ("new Function", "то же самое")]:
        if pat in src:
            bad(f, why, pat)
ok("innerHTML, eval and document.write are not used")

print("\n=== 5. Addresses from other sites are checked ===")
for f in list(PAGES.values()):
    src = read(f)
    for m in re.finditer(r"(?:style\.backgroundImage\s*=|\.src\s*=|\.href\s*=)\s*([^;\n]+)", src):
        expr = m.group(1)
        if "safeUrl" in expr or "encodeURIComponent" in expr or "'/" in expr or '"/' in expr:
            continue
        if re.match(r"\s*['\"]", expr) or "Date.now" in expr or "cv.toDataURL" in expr:
            continue
        if "safeSrc" in expr or "checked" in expr.lower():
            continue
        if "poster" in expr or "url" in expr.lower():
            # we take ten lines above: safeUrl could have stood there
            before = src[max(0, m.start() - 900):m.start()]
            if "safeUrl" in before or "currentUrl()" in before:
                continue
            bad(f, "the address is inserted without a check", expr[:70])
ok("addresses from external data go through safeUrl")

print("\n=== 6. Translations ===")
for name, html in HTML.items():
    src = read(html)
    for m in re.finditer(r'<[^>]*data-ru="([^"]*)"[^>]*>', src):
        if "data-en=" not in m.group(0):
            bad(name, "no English counterpart", m.group(1)[:40])
    for m in re.finditer(r'<[^>]*data-ru-ph="([^"]*)"[^>]*>', src):
        if "data-en-ph=" not in m.group(0):
            bad(name, "no English placeholder", m.group(1)[:40])
ok("every caption has both languages")

print("\n=== 7. The server side: permissions on every endpoint ===")
main = read("api/main.py")
routes = re.findall(r'@app\.(get|post|delete)\("([^"]+)"\)\s*\nasync def (\w+)\((.*?)\):', main, re.S)
# /api/mode is deliberately open: the banner about demonstration mode has
# to be shown before signing in — otherwise a person presses "watch" and
# cannot tell why a cartoon about a rabbit plays instead of an episode.
# One word leaves through it.
public = {"/api/health", "/api/mode", "/api/auth/login", "/api/auth/guest",
          "/api/auth/logout", "/", "/{page}"}
for method, path, fname, args in routes:
    guarded = "Depends(need_" in args
    if path in public:
        continue
    body = re.search(rf'async def {fname}\(.*?\n(?=@app\.|# =|\Z)', main, re.S)
    inline = body and ("await whoami(" in body.group() or "Depends(need_" in body.group())
    if not guarded and not inline:
        bad("api/main.py", "an endpoint with no permission check", f"{method.upper()} {path}")
ok(f"endpoints checked: {len(routes)}, every protected one has a permission check")

print("\n=== 8. Changing requests require a form marker ===")
for method, path, fname, args in routes:
    if method not in ("post", "delete"):
        continue
    if path in ("/api/auth/login", "/api/auth/guest", "/api/auth/logout"):
        continue
    body = re.search(rf'async def {fname}\(.*?\n(?=@app\.|# =|\Z)', main, re.S)
    if body and "guard_csrf" not in body.group():
        bad("api/main.py", "a changing endpoint with no protection against forgery", f"{method.upper()} {path}")
ok("every changing request checks the form marker")

print("\n=== 9. Queries to the database ===")
store = read("api/store.py")

# There is one exception, and it is genuine: a table name and a column
# name cannot be passed as a parameter (the ? sign) in any database —
# parameters exist for values only. Adding missing columns while upgrading
# the database cannot do without gluing.
#
# So the exception is made not "for the file" but for one particular
# function, and on top of that we check that it verifies the names against
# a pattern itself and falls over on anything else. Gluing appears
# somewhere else — an error again.
MIGRATION = "_add_missing_columns"
mig = re.search(r"def " + MIGRATION + r"\(.*?\n(?=\ndef |\nclass |\Z)", store, re.S)
mig_src = mig.group() if mig else ""
if not mig_src:
    bad("api/store.py", "the schema upgrade function was not found", MIGRATION)
elif "_PLAIN_NAME.match" not in mig_src or "raise ValueError" not in mig_src:
    bad("api/store.py", "the schema upgrade does not check column names", MIGRATION)

outside = store.replace(mig_src, "") if mig_src else store

for m in re.finditer(r"execute\(\s*(f?[\"\'])", outside):
    if m.group(1).startswith("f"):
        bad("api/store.py", "a formatted string in SQL", m.group(0))
for c in re.findall(r"execute\([^)]*\+[^)]*\)", outside):
    # safe if only pieces with ? are glued and no user data is involved
    if "?" in c and not re.search(r"\+\s*(?!\")[a-z_]+\s*\+?\s*\"", c.replace('", "', '')):
        continue
    bad("api/store.py", "strings glued together in SQL", c[:60])
ok("SQL is assembled with parameters only (apart from the checked schema upgrade)")

print("\n=== 10. Length limits in the models ===")
models = re.findall(r'class (\w+In)\(BaseModel\):(.*?)(?=\nclass |\n\n\n)', main, re.S)
for cname, body in models:
    for line in body.strip().split("\n"):
        if ":" not in line or "Field" not in line:
            continue
        if "str" in line and "max_length" not in line:
            bad("api/main.py", f"a string with no length limit in {cname}", line.strip()[:60])
        if re.search(r":\s*int", line) and not ("ge=" in line and "le=" in line):
            note("api/main.py", f"a number with no bounds in {cname}", line.strip()[:60])
ok(f"models checked: {len(models)}")

print("\n=== 11. Container settings ===")
comp = read("docker-compose.yml")
dock = read("Dockerfile")
if "127.0.0.1:" not in comp:
    bad("docker-compose.yml", "the port is open to the outside rather than locally only")
if "USER app" not in dock:
    bad("Dockerfile", "the container runs as root")
if "SESSION_PEPPER" not in comp:
    bad("docker-compose.yml", "no session secret string is set")
ok("the container is configured correctly")

print("\n=== 12. Traces of the previous version ===")
for root, _, files in os.walk("."):
    if "__pycache__" in root or "/.git" in root:
        continue
    for fn in files:
        p = os.path.join(root, fn)
        if fn.endswith((".md", ".gz")) or fn.startswith("check"):
            continue
        try:
            t = read(p)
        except Exception:
            continue
        if re.search(r'eyJ[A-Za-z0-9_-]{30,}', t):
            bad(p, "looks like an access key in the code")
        if "supabase" in t.lower():
            bad(p, "code calling Supabase is still here")
ok("no keys and no old code are left")

print("\n" + "=" * 60)
print(f"PASS 1 — errors: {len(PROBLEMS)}, notes: {len(NOTES)}")
if PROBLEMS:
    print("\nWhat to fix:")
    for w, t, d in PROBLEMS:
        print(f"  [{w}] {t}" + (f" — {d}" if d else ""))
sys.exit(1 if PROBLEMS else 0)
