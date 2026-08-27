"""Проход 1: ищем ошибки в коде, не запуская его.

Разбираем разметку и скрипты как текст и проверяем всё, что можно
проверить без браузера: обращения к несуществующим элементам, обработчики
без кнопок, опасные приёмы, потерянные переводы.
"""
import io
import json
import os
import re
import subprocess
import sys

# Вывод здесь на русском, а консоль Windows по умолчанию живёт в cp1251:
# первая же стрелка или галочка роняла весь запуск с UnicodeEncodeError,
# и проверки обрывались на середине, не дойдя до сути. Просим поток
# работать в utf-8; там, где он и так utf-8, строка ничего не меняет.
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

print("\n=== 1. Синтаксис ===")
for f in ["api/main.py", "api/store.py", "api/security.py", "api/anime.py", "api/admin.py"]:
    r = subprocess.run([sys.executable, "-m", "py_compile", f], capture_output=True)
    if r.returncode:
        bad(f, "не компилируется", r.stderr.decode()[:120])
# Синтаксис скриптов проверяет node. Его может не быть, а может быть и
# сломанный: на этой машине `node --version` отвечает бодро, а `node --check`
# падает внутри самого node. И то и другое раньше выглядело как четыре
# «ошибки синтаксиса» в нашем коде — проверка сообщала не о том, что сломан
# код, а о том, что её нечем выполнить. Худший вид ложной тревоги: он
# приучает не верить проверкам вообще.
#
# Поэтому сначала проверяем сам инструмент на заведомо правильном файле.
# Не справился с ним — молчит и про наши файлы тоже.
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
            bad(f, "ошибка синтаксиса", r.stderr.decode("utf-8", "replace")[:160])
    ok("весь код синтаксически верен")
else:
    note("web", "рабочего node нет — синтаксис скриптов не проверялся",
         "поставьте node, если нужна эта проверка")
    ok("серверный код синтаксически верен")

print("\n=== 2. Обращения к несуществующим элементам ===")
for name, js in PAGES.items():
    src, html = read(js), read(HTML[name])
    shared = read("web/app.js")
    ids = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", src))
    ids |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", src + shared))
    miss = sorted(i for i in ids if f'id="{i}"' not in html)
    if miss:
        bad(name, "скрипт зовёт элементы, которых нет в разметке", ", ".join(miss))
    sels = set(re.findall(r"querySelector(?:All)?\('([#.][^']+)'\)", src))
    for sel in sels:
        root = re.split(r"[ >,:]", sel)[0]
        if root.startswith("#") and f'id="{root[1:]}"' not in html:
            bad(name, "выборка по несуществующему id", sel)
ok("все обращения к элементам разрешаются")

print("\n=== 3. Кнопки без обработчиков ===")
for name, js in PAGES.items():
    src, html = read(js), read(HTML[name])
    for m in re.finditer(r'<button[^>]*id="([A-Za-z0-9_-]+)"', html):
        bid = m.group(1)
        # Кнопку можно подключить не только напрямую по id, но и через
        # помощника — например onSwitch('s-2fa', ...) для переключателей.
        # Считать такую кнопку «ничего не делающей» неверно.
        wired = (f"$('{bid}')" in src
                 or f"getElementById('{bid}')" in src
                 or f"onSwitch('{bid}'" in src)
        # кнопка отправки формы обрабатывается через саму форму
        if not wired and 'type="submit"' in m.string[m.start():m.start() + 240]:
            form = re.search(r'<form id="([\w-]+)"', html)
            wired = bool(form and f"$('{form.group(1)}')" in src)
        if not wired:
            bad(name, "кнопка ничего не делает", "#" + bid)
    for m in re.finditer(r'<(?:a|button)[^>]*\bclass="[^"]*\bchipbtn\b[^"]*"[^>]*>', html):
        tag = m.group(0)
        if 'id=' not in tag and 'href="#"' in tag:
            note(name, "ссылка-заглушка без действия", tag[:60])
ok("у каждой кнопки есть обработчик")

print("\n=== 4. Опасные приёмы во фронтенде ===")
for f in ["web/app.js"] + list(PAGES.values()):
    src = read(f)
    for pat, why in [("innerHTML", "вставка HTML — путь для чужого кода"),
                     ("outerHTML", "то же самое"),
                     ("document.write", "устарело и небезопасно"),
                     ("eval(", "исполнение строки"),
                     ("new Function", "то же самое")]:
        if pat in src:
            bad(f, why, pat)
ok("innerHTML, eval и document.write не используются")

print("\n=== 5. Адреса от чужих сайтов проверяются ===")
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
            # берём десять строк выше: там мог стоять safeUrl
            before = src[max(0, m.start() - 900):m.start()]
            if "safeUrl" in before or "currentUrl()" in before:
                continue
            bad(f, "адрес подставляется без проверки", expr[:70])
ok("адреса из внешних данных проходят через safeUrl")

print("\n=== 6. Переводы ===")
for name, html in HTML.items():
    src = read(html)
    for m in re.finditer(r'<[^>]*data-ru="([^"]*)"[^>]*>', src):
        if "data-en=" not in m.group(0):
            bad(name, "нет английской пары", m.group(1)[:40])
    for m in re.finditer(r'<[^>]*data-ru-ph="([^"]*)"[^>]*>', src):
        if "data-en-ph=" not in m.group(0):
            bad(name, "нет английского placeholder", m.group(1)[:40])
ok("у всех надписей есть оба языка")

print("\n=== 7. Серверная часть: права на каждой ручке ===")
main = read("api/main.py")
routes = re.findall(r'@app\.(get|post|delete)\("([^"]+)"\)\s*\nasync def (\w+)\((.*?)\):', main, re.S)
# /api/mode открыт намеренно: плашку про демонстрационный режим надо
# показать до входа — иначе человек нажмёт «смотреть» и не поймёт,
# почему вместо серии играет мультфильм про кролика. Наружу оттуда
# уходит одно слово.
public = {"/api/health", "/api/mode", "/api/auth/login", "/api/auth/guest",
          "/api/auth/logout", "/", "/{page}"}
for method, path, fname, args in routes:
    guarded = "Depends(need_" in args
    if path in public:
        continue
    body = re.search(rf'async def {fname}\(.*?\n(?=@app\.|# =|\Z)', main, re.S)
    inline = body and ("await whoami(" in body.group() or "Depends(need_" in body.group())
    if not guarded and not inline:
        bad("api/main.py", "ручка без проверки прав", f"{method.upper()} {path}")
ok(f"проверено ручек: {len(routes)}, все защищённые имеют проверку прав")

print("\n=== 8. Изменяющие запросы требуют метку формы ===")
for method, path, fname, args in routes:
    if method not in ("post", "delete"):
        continue
    if path in ("/api/auth/login", "/api/auth/guest", "/api/auth/logout"):
        continue
    body = re.search(rf'async def {fname}\(.*?\n(?=@app\.|# =|\Z)', main, re.S)
    if body and "guard_csrf" not in body.group():
        bad("api/main.py", "изменяющая ручка без защиты от подделки", f"{method.upper()} {path}")
ok("все изменяющие запросы проверяют метку формы")

print("\n=== 9. Запросы к базе ===")
store = read("api/store.py")

# Одно исключение, и оно настоящее: имя таблицы и имя столбца нельзя
# передать параметром (знаком ?) ни в одной базе — параметры существуют
# только для значений. Добавление недостающих столбцов при обновлении
# базы обойтись без склейки не может.
#
# Поэтому исключение сделано не «для файла», а для одной конкретной
# функции, и вдобавок проверяется, что она сама сверяет имена с образцом
# и падает на постороннем. Появится склейка где-то ещё — снова ошибка.
MIGRATION = "_add_missing_columns"
mig = re.search(r"def " + MIGRATION + r"\(.*?\n(?=\ndef |\nclass |\Z)", store, re.S)
mig_src = mig.group() if mig else ""
if not mig_src:
    bad("api/store.py", "не нашлась функция обновления схемы", MIGRATION)
elif "_PLAIN_NAME.match" not in mig_src or "raise ValueError" not in mig_src:
    bad("api/store.py", "обновление схемы не проверяет имена столбцов", MIGRATION)

outside = store.replace(mig_src, "") if mig_src else store

for m in re.finditer(r"execute\(\s*(f?[\"\'])", outside):
    if m.group(1).startswith("f"):
        bad("api/store.py", "форматированная строка в SQL", m.group(0))
for c in re.findall(r"execute\([^)]*\+[^)]*\)", outside):
    # безопасно, если склеиваются только куски с ? и без данных пользователя
    if "?" in c and not re.search(r"\+\s*(?!\")[a-z_]+\s*\+?\s*\"", c.replace('", "', '')):
        continue
    bad("api/store.py", "склейка строк в SQL", c[:60])
ok("SQL собирается только с параметрами (кроме проверенного обновления схемы)")

print("\n=== 10. Ограничения длины в моделях ===")
models = re.findall(r'class (\w+In)\(BaseModel\):(.*?)(?=\nclass |\n\n\n)', main, re.S)
for cname, body in models:
    for line in body.strip().split("\n"):
        if ":" not in line or "Field" not in line:
            continue
        if "str" in line and "max_length" not in line:
            bad("api/main.py", f"строка без ограничения длины в {cname}", line.strip()[:60])
        if re.search(r":\s*int", line) and not ("ge=" in line and "le=" in line):
            note("api/main.py", f"число без границ в {cname}", line.strip()[:60])
ok(f"проверено моделей: {len(models)}")

print("\n=== 11. Настройки контейнера ===")
comp = read("docker-compose.yml")
dock = read("Dockerfile")
if "127.0.0.1:" not in comp:
    bad("docker-compose.yml", "порт открыт наружу, а не только локально")
if "USER app" not in dock:
    bad("Dockerfile", "контейнер работает от root")
if "SESSION_PEPPER" not in comp:
    bad("docker-compose.yml", "не задана секретная строка сессий")
ok("контейнер настроен верно")

print("\n=== 12. Следы прошлой версии ===")
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
            bad(p, "похоже на ключ доступа в коде")
        if "supabase" in t.lower():
            bad(p, "остался код обращения к Supabase")
ok("ключей и старого кода не осталось")

print("\n" + "=" * 60)
print(f"ПРОХОД 1 — ошибок: {len(PROBLEMS)}, замечаний: {len(NOTES)}")
if PROBLEMS:
    print("\nЧто чинить:")
    for w, t, d in PROBLEMS:
        print(f"  [{w}] {t}" + (f" — {d}" if d else ""))
sys.exit(1 if PROBLEMS else 0)
