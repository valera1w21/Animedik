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


def bez_kommentariev(src):
    """Код без комментариев.

    Проверка ищет опасные приёмы — но комментарий ничего не исполняет.
    Рядом с каждым таким местом стоит объяснение, почему приём не
    используется, и оно обязано называть приём по имени. Проверка,
    срабатывающая на объяснение, заставляет объяснение удалить: так
    теряются ровно те комментарии, ради которых всё и писалось.
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(^|[^:])//[^\n]*", r"\1", src)
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)
    return src


for f in ["web/app.js"] + list(PAGES.values()):
    src = bez_kommentariev(read(f))
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

print("\n=== Темы и праздники: у каждой кнопки есть оформление ===")
# Кнопка, которой не соответствует ни одного правила в стилях, — это
# кнопка, которая ничего не делает. Внешне отличить её невозможно:
# нажимаешь, и просто ничего не происходит.
_css_t = read("web/app.css")
_html_t = read("web/index.html")

_темы = re.findall(r'data-th="([\w-]+)"', _html_t)
if not _темы:
    bad("web/index.html", "карточек тем не нашлось вовсе")
for _т in _темы:
    if ('html[data-theme="%s"]' % _т) not in _css_t:
        bad("app.css", "у темы нет набора цветов", _т)
_праздники = [h for h in re.findall(r'data-h="([\w]*)"', _html_t) if h and h != "off"]
if not _праздники:
    bad("web/index.html", "кнопок праздников не нашлось вовсе")
for _п in _праздники:
    if ('html[data-holiday="%s"]' % _п) not in _css_t:
        bad("app.css", "у праздника нет оформления", _п)

# И наоборот: набор цветов, к которому не ведёт ни одна кнопка, — мусор,
# который никто никогда не увидит.
for _т in set(re.findall(r'html\[data-theme="([\w-]+)"\]', _css_t)):
    if ('data-th="%s"' % _т) not in _html_t:
        bad("app.css", "набор цветов есть, а кнопки к нему нет", _т)
for _п in set(re.findall(r'html\[data-holiday="([\w-]+)"\]', _css_t)):
    if ('data-h="%s"' % _п) not in _html_t:
        bad("app.css", "оформление праздника есть, а кнопки нет", _п)

# Скрипт должен знать ровно те же названия, что и разметка.
_js_t = read("web/app.js")
for _т in _темы:
    if ("'%s'" % _т) not in _js_t:
        bad("web/app.js", "тема неизвестна скрипту", _т)
for _п in _праздники:
    if ("'%s'" % _п) not in _js_t:
        bad("web/app.js", "праздник неизвестен скрипту", _п)
ok("у каждой темы и каждого праздника есть и кнопка, и оформление, и код")

print("\n=== Правила, меню и кнопка связи ===")
_h = read("web/index.html")
_j = read("web/index.js")
_pr = read("web/pravila.html")

check_pairs = [
    ('id="btn-rules"' in _h and 'href="/pravila"' in _h,
     "кнопки правил в шапке нет"),
    (_h.count('href="/pravila"') >= 2,
     "правила должны быть и в шапке, и в футере"),
    ('target="_blank"' in _h and 'rel="noopener"' in _h,
     "правила должны открываться в новой вкладке и с rel=noopener"),
    ('id="btn-burger"' in _h and 'aria-controls="acts"' in _h,
     "бургера нет или он не связан со списком кнопок"),
    ('id="acts"' in _h, "нет блока с кнопками, который прячет бургер"),
    (_h.count('class="lbl"') >= 5,
     "у кнопок в меню нет подписей — в списке значок без подписи не читается"),
    ("btn-svyaz" in _h and "btn-svyaz" in _j,
     "кнопки связи нет или у неё нет обработчика"),
]
for условие, беда in check_pairs:
    if not условие:
        bad("web/index.html", беда)

# Правила должны быть написаны, а не остаться заготовкой, и главное —
# в них должен быть раздел об ответственности: ради него их и открывают.
if "Правила пока не написаны" in _pr:
    bad("web/pravila.html", "правила остались незаполненными")
for кусок, беда in (
        ("не берёт на себя ответственность", "нет главного абзаца об ответственности"),
        ("вход по коду", "не сказано про вход по коду"),
        ("пароль", "не сказано про пароль"),
        ("почт", "не сказано про почту")):
    if кусок not in _pr:
        bad("web/pravila.html", беда, кусок)
_ru = _pr.count("data-ru=")
_en = _pr.count("data-en=")
if _ru != _en:
    bad("web/pravila.html", "перевод неполный", "ru:%d en:%d" % (_ru, _en))
ok("правила написаны, кнопки на месте")

print("\n=== Текст читается в каждой теме ===")
# Тему легко подобрать «на глаз» так, что приглушённый текст сольётся с
# фоном. Считаем контраст по той же формуле, что и браузеры (WCAG), и
# требуем 4.5 — ниже начинаются жалобы «ничего не видно».


def _svetlota(hex_):
    hex_ = hex_.lstrip("#")
    if len(hex_) == 3:
        hex_ = "".join(c * 2 for c in hex_)
    части = [int(hex_[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    линейно = [(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
               for c in части]
    return 0.2126 * линейно[0] + 0.7152 * линейно[1] + 0.0722 * линейно[2]


def _kontrast(a, b):
    x, y = _svetlota(a), _svetlota(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


_темы_css = read("web/app.css")
_наборы = re.findall(
    r'html\[data-theme="([\w-]+)"\](\[data-depth="(\w+)"\])?\s*\{([^}]*)\}', _темы_css)
_собрано = {}
for _имя, _, _глуб, _тело in _наборы:
    ключ = _имя + "/" + (_глуб or "deep")
    цвета = dict(re.findall(r"--([\w-]+)\s*:\s*(#[0-9A-Fa-f]{3,6})", _тело))
    основа = dict(_собрано.get(_имя + "/deep", {}))
    основа.update(цвета)
    _собрано[ключ] = основа

if len(_собрано) < 2:
    bad("app.css", "наборов цветов не нашлось — проверка контраста не сработала")
for _ключ, _ц in sorted(_собрано.items()):
    фон = _ц.get("bg")
    if not фон:
        bad("app.css", "у темы нет фона", _ключ)
        continue
    for _роль, _минимум in (("text", 4.5), ("dim", 4.5), ("dimmer", 3.0)):
        цвет = _ц.get(_роль)
        if not цвет:
            continue
        к = _kontrast(цвет, фон)
        if к < _минимум:
            bad("app.css", "текст сливается с фоном",
                "%s: --%s к фону %.1f, нужно %.1f" % (_ключ, _роль, к, _минимум))
ok("во всех %d наборах цветов текст читается" % len(_собрано))

print("\n=== Одно имя класса — одна вещь ===")
# Файл стилей один на три страницы. Если один и тот же класс объявлен
# начисто дважды, побеждает то правило, что ниже, и первая вещь молча
# получает чужие отступы, фон и рамку. Так уже случилось с .card, .bar,
# .iconbtn, .sw и .pane: карточка аниме забирала стили панели с итогов
# года, а полоса загрузки — отступы шапки.
#
# Правила внутри @media не считаются: там то же самое уточняют под
# другой размер экрана. Разделы-доводки в конце файла — тоже: они
# намеренно дописывают к уже описанным вещам.
_css_src = read("web/app.css")
_РАЗДЕЛЫ = [(0, "начало")]
for _m in re.finditer(r"/\*\s*=+\s*([^=\n]+?)\s*=+\s*\*/", _css_src):
    _РАЗДЕЛЫ.append((_m.start(), _m.group(1).strip()))
_ДОВОДКИ = ("ПРОИЗВОДИТЕЛЬНОСТЬ", "ОДИНАКОВЫЙ ВИД", "НАСТОЯЩЕЕ ВИДЕО", "ДВИЖЕНИЕ")
_css = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), _css_src, flags=re.S)


def _раздел(поз):
    имя = "начало"
    for нач, н in _РАЗДЕЛЫ:
        if нач <= поз:
            имя = н
    return имя


_где = {}
_уровень = 0
_media_на = None
_ТОЛЬКО_ДОВОДКА = {"transition", "animation", "animation-delay", "animation-name",
                   "will-change", "transition-duration", "animation-duration",
                   "transform-origin", "opacity", "color", "cursor"}
for _m in re.finditer(r"@media[^{]*\{|\{|\}|(?:^|\n)\s*([.\w#\[][^{}\n]*?)\s*\{", _css):
    _t = _m.group(0)
    if _t.startswith("@media"):
        if _media_на is None:
            _media_на = _уровень
        _уровень += 1
        continue
    if _t == "{":
        _уровень += 1
        continue
    if _t == "}":
        _уровень -= 1
        if _media_на is not None and _уровень <= _media_на:
            _media_на = None
        continue
    _уровень += 1
    if _media_на is not None:
        continue
    _р = _раздел(_m.start())
    if any(_д in _р.upper() for _д in _ДОВОДКИ):
        continue
    _конец = _css.find("}", _m.end())
    _тело = _css[_m.end():_конец] if _конец > 0 else ""
    _свойства = [d.split(":")[0].strip() for d in _тело.split(";") if ":" in d]
    if _свойства and all(s in _ТОЛЬКО_ДОВОДКА for s in _свойства):
        continue
    for _сел in (_m.group(1) or "").split(","):
        _сел = _сел.strip()
        if re.fullmatch(r"\.[A-Za-z][\w-]*", _сел):
            _где.setdefault(_сел, []).append(_css[:_m.end()].count("\n") + 1)

_дубли = {k: v for k, v in _где.items() if len(v) > 1}
if _дубли:
    for _имя in sorted(_дубли):
        bad("app.css", "класс объявлен начисто дважды — одно имя на разные вещи",
            "%s: строки %s" % (_имя, ", ".join(map(str, _дубли[_имя]))))
else:
    ok("ни один класс не описывает две разные вещи")

print("\n" + "=" * 60)
print(f"ПРОХОД 1 — ошибок: {len(PROBLEMS)}, замечаний: {len(NOTES)}")
if PROBLEMS:
    print("\nЧто чинить:")
    for w, t, d in PROBLEMS:
        print(f"  [{w}] {t}" + (f" — {d}" if d else ""))
sys.exit(1 if PROBLEMS else 0)
