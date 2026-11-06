"""Проход 33: страница — безопасность, языки, доступность, консоль браузера.

Аудит фронтенда нашёл то, что остальные проверки не ловят:
они смотрят на сервер и на отдельные строки кода, а не на страницу целиком
в настоящем браузере. Этот проход стережёт найденное, чтобы оно не
вернулось.

Часть 1 — без браузера, по исходникам (секунда):
  * никакой вставки HTML строкой (innerHTML, insertAdjacentHTML,
    document.write), никакого eval / new Function / таймера со строкой;
  * в разметке нет обработчиков onclick="…", встроенных <script> и
    javascript:-ссылок — их запрещает политика безопасности (CSP);
  * каждая ссылка target="_blank" — с rel="noopener";
  * русский текст в скриптах — только в паре с английским (A.t('…','…'),
    словари, пары [ru, en]); новая русская строка без пары — провал;
  * у русских aria-label, title, alt и placeholder в разметке есть
    английская пара (data-en-aria и т. п.);
  * у каждого окна (role="dialog") есть имя для экранного диктора;
  * localStorage и JSON.parse — только внутри try;
  * код страницы — ES5: без let/const, стрелок и шаблонных строк;
  * hls.js не старее 1.7.3.

Часть 2 — в настоящем браузере (Playwright + Chromium, около двух минут):
  поднимается настоящий сервер (MODE=private, своя база), всё, что ходит
  к чужим сайтам (справочник, источники, видео), подменяется прямо в
  браузере, а видео — крошечные ролики из testdata/frontend. Проверяется:
  ноль ошибок в консоли и ноль нарушений CSP на всех страницах и окнах,
  чужой текст с <img onerror=…> остаётся текстом, английская страница без
  русских слов, окна с именами и возвратом фокуса, двойной щелчок не шлёт
  запрос дважды, плеер играет HLS и обычный файл, автопереход под плеером
  сохраняется, телефонная ширина без прокрутки вбок, контраст текста.

Playwright нужен только части 2. Если его нет у этого Python, проход
ищет его у `python3` (или у того, что указано в PW_PYTHON) и запускает
браузерную часть там; не нашёл нигде — часть 2 пропускается с громким
предупреждением (поставить: pip install playwright && playwright install
chromium).
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
FIX = os.path.join(HERE, "testdata", "frontend")

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
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)[:600]) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()


OWN_JS = ["app.js", "index.js", "watch.js", "stats.js", "pravila.js"]
PAGES_HTML = ["index.html", "watch.html", "stats.html", "pravila.html"]
CYR = re.compile("[А-Яа-яЁё]")


# ==========================================================================
# Маленький разборщик ES5: строки, регулярные выражения, имена, знаки.
# Нужен, чтобы отличать код от комментариев и строк: в комментариях этого
# проекта много русского текста и слов вроде «innerHTML» — проверка не
# должна срабатывать на объяснения.
# ==========================================================================
_BEFORE_REGEX = set("(,=:[!&|?{};+-*%<>~^")
_KW_BEFORE_REGEX = {"return", "typeof", "case", "do", "else", "in", "of", "new",
                    "delete", "void", "throw"}


def js_tokens(src):
    """(вид, текст, строка): str, regex, id, num, punc."""
    i, n, line, last = 0, len(src), 1, None
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r﻿":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            line += src[i:j + 2].count("\n")
            i = j + 2
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                j += 1
            tok = src[i:j + 1]
            yield ("str", tok, line)
            line += tok.count("\n")
            last = ("str", tok)
            i = j + 1
            continue
        if c == "/":
            if last is None or (last[0] == "punc" and last[1] in _BEFORE_REGEX) or \
                    (last[0] == "id" and last[1] in _KW_BEFORE_REGEX):
                j, in_cls = i + 1, False
                while j < n:
                    ch = src[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_cls = True
                    elif ch == "]":
                        in_cls = False
                    elif ch == "/" and not in_cls:
                        break
                    j += 1
                j += 1
                while j < n and src[j].isalpha():
                    j += 1
                yield ("regex", src[i:j], line)
                last = ("regex", src[i:j])
                i = j
                continue
        if c.isalpha() or c in "_$" or ord(c) > 127:
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$" or ord(src[j]) > 127):
                j += 1
            yield ("id", src[i:j], line)
            last = ("id", src[i:j])
            i = j
            continue
        if c.isdigit():
            j = i
            while j < n and (src[j].isalnum() or src[j] == "."):
                j += 1
            yield ("num", src[i:j], line)
            last = ("num", src[i:j])
            i = j
            continue
        if src.startswith("=>", i):
            yield ("punc", "=>", line)
            last = ("punc", "=>")
            i += 2
            continue
        yield ("punc", c, line)
        last = ("punc", c)
        i += 1


_LANG_KEYS = re.compile(r"^(ru|sru|nmRu|.*_ru|ru_.*)$")
_DATA_RU = re.compile(r"^['\"]data-ru(-[a-z]+)?['\"]$")
# Функции вида f(…, 'русский', 'english'): номер аргумента с русским.
_PAIR_FUNCS = {"t": 0, "langText": 1}


def unpaired_russian(src):
    """Русские строки, у которых рядом нет английской пары.

    Парой считается: первый аргумент t('ru', 'en') (и langText(узел, 'ru',
    'en')); ключ словаря {'русское': 'English'}; значение у ключа ru/sru;
    второй аргумент setAttribute('data-ru…', '…'); присваивание var ru = …;
    массив, где рядом есть латинская строка ([ru, en], [[формы], [forms]]);
    массив, у которого есть близнец ИМЯ_EN; массив форм, переданный в
    функцию рядом с массивом английских форм; одна буква (раскладка, ё→е).
    """
    toks = list(js_tokens(src))
    names = {t[1] for t in toks if t[0] == "id"}
    out, stack = [], []
    for k, (kind, text, line) in enumerate(toks):
        prev = toks[k - 1] if k else ("", "", 0)
        prev2 = toks[k - 2] if k > 1 else ("", "", 0)
        if kind == "punc" and text in "([{":
            fr = {"ch": text, "arg": 0, "pair": None, "strs": [], "cyr": [], "latin_kid": False,
                  "var": None, "setattr": False, "arr_cyr": [], "arr_latin": False}
            if text == "(" and prev[0] == "id" and prev[1] in _PAIR_FUNCS:
                fr["pair"] = _PAIR_FUNCS[prev[1]]
            if text == "(" and prev[0] == "id" and prev[1] == "setAttribute":
                fr["setattr"] = True
                fr["first"] = toks[k + 1][1] if k + 1 < len(toks) else ""
            if text == "[" and prev[0] == "punc" and prev[1] == "=" and prev2[0] == "id":
                fr["var"] = prev2[1]
            stack.append(fr)
            continue
        if kind == "punc" and text in ")]}":
            if not stack:
                continue
            fr = stack.pop()
            parent = stack[-1] if stack else None
            if fr["ch"] == "[":
                latin = any(not CYR.search(s) for s in fr["strs"]) or fr["latin_kid"]
                twin = fr["var"] and (fr["var"] + "_EN") in names
                left = [] if (latin or twin) else fr["cyr"]
                if parent is not None and parent["ch"] == "[":
                    parent["cyr"].extend(left)
                    parent["latin_kid"] = parent["latin_kid"] or latin
                elif parent is not None and parent["ch"] == "(":
                    parent["arr_cyr"].extend(left)
                    parent["arr_latin"] = parent["arr_latin"] or latin
                else:
                    out.extend(left)
            elif fr["ch"] == "(":
                if not fr["arr_latin"]:
                    out.extend(fr["arr_cyr"])
            continue
        if kind == "punc" and text == "," and stack:
            stack[-1]["arg"] += 1
            continue
        if kind != "str":
            continue
        fr = stack[-1] if stack else None
        if fr is not None and fr["ch"] == "[":
            fr["strs"].append(text)
        if not CYR.search(text) or len(text) <= 3:
            continue
        nxt = toks[k + 1] if k + 1 < len(toks) else ("", "", 0)
        if any(f["pair"] is not None and f["arg"] == f["pair"] for f in stack):
            continue
        if fr is not None and fr["ch"] == "{" and nxt[:2] == ("punc", ":"):
            continue
        if prev[:2] == ("punc", ":") and prev2[0] == "id" and _LANG_KEYS.match(prev2[1]):
            continue
        if fr is not None and fr["setattr"] and fr["arg"] == 1 and _DATA_RU.match(fr.get("first", "")):
            continue
        j, is_ru = k - 1, False
        while j >= 0 and not (toks[j][0] == "punc" and toks[j][1] in ";{}"):
            if toks[j][:2] == ("id", "ru") and toks[j + 1][1] == "=":
                is_ru = True
                break
            j -= 1
        if is_ru:
            continue
        item = (line, text)
        if fr is not None and fr["ch"] == "[":
            fr["cyr"].append(item)
            continue
        out.append(item)
    return out


# Русские строки без пары, которые оставлены сознательно. У каждой — почему.
RUSSIAN_OK = {
    # Внутренние имена слоёв праздничной сцены (app.js, «земля» под
    # предметами). На экран не выводятся никогда.
    "'земля'", "'холм'", "'гребень'", "'холмы'", "'сугроб'", "'берег'",
    # Подпись над английским описанием на РУССКОЙ странице: показывается
    # только при langCode() !== 'en' (index.js aboutNode, watch.js loadAbout).
    "'Описание на английском'",
    # Начальная aria-label дорожки перемотки; рядом же ставятся data-ru-aria
    # и data-en-aria, и applyLang сразу выбирает нужную (watch.js).
    "'Перемотка'",
}


def static_checks():
    group("1. Нет вставки HTML строкой и исполнения строк")
    bad = []
    for f in OWN_JS:
        toks = list(js_tokens(read(f)))
        for k, (kind, text, line) in enumerate(toks):
            if kind != "id":
                continue
            nxt = toks[k + 1] if k + 1 < len(toks) else ("", "", 0)
            if text in ("innerHTML", "outerHTML", "insertAdjacentHTML") and \
                    toks[k - 1][1] == ".":
                bad.append("%s:%d %s" % (f, line, text))
            if text in ("eval", "Function") and nxt[1] == "(" and toks[k - 1][1] != ".":
                bad.append("%s:%d %s(" % (f, line, text))
            if text == "write" and toks[k - 1][1] == "." and toks[k - 2][1] == "document":
                bad.append("%s:%d document.write" % (f, line))
            if text in ("setTimeout", "setInterval") and nxt[1] == "(" and \
                    k + 2 < len(toks) and toks[k + 2][0] == "str":
                bad.append("%s:%d %s со строкой" % (f, line, text))
            if text == "srcdoc":
                bad.append("%s:%d srcdoc" % (f, line))
    check("innerHTML / insertAdjacentHTML / document.write / eval / таймеры со строкой", not bad, bad)

    group("2. Разметка: CSP, ссылки, окна")
    for f in PAGES_HTML:
        src = re.sub(r"<!--.*?-->", "", read(f), flags=re.S)
        inline = re.findall(r"<[a-zA-Z][^>]*\son[a-z]+\s*=", src)
        check(f + ": нет обработчиков onclick=… (CSP их не исполнит)", not inline, inline[:3])
        scripts = re.findall(r"<script(?![^>]*\ssrc=)[^>]*>", src)
        check(f + ": нет встроенных <script> без src (CSP script-src 'self')", not scripts, scripts)
        js_urls = re.findall(r"""(?:href|src|action|formaction)\s*=\s*["']\s*javascript:""", src, re.I)
        check(f + ": нет javascript:-ссылок", not js_urls, js_urls)
        blank_bad = [m for m in re.findall(r"<a\s[^>]*target=\"_blank\"[^>]*>", src)
                     if not re.search(r'rel="[^"]*noopener', m)]
        check(f + ": target=_blank — только с rel=noopener", not blank_bad, blank_bad)
        dialogs = re.findall(r"<[a-z]+\s[^>]*role=\"dialog\"[^>]*>", src)
        unnamed = [d for d in dialogs if "aria-labelledby=" not in d and "aria-label=" not in d]
        check(f + ": у каждого окна есть имя (aria-labelledby / aria-label)", not unnamed, unnamed)
        ids = re.findall(r'\sid="([^"]+)"', src)
        dup = sorted({i for i in ids if ids.count(i) > 1})
        check(f + ": id не повторяются", not dup, dup)
    for f in OWN_JS:
        src = read(f)
        opens = re.findall(r"window\.open\(([^)]*)\)", src)
        check(f + ": window.open — только с noopener",
              all("noopener" in o for o in opens), opens)

    group("3. Английская пара у каждой русской строки")
    for f in OWN_JS:
        left = [(ln, t) for ln, t in unpaired_russian(read(f)) if t not in RUSSIAN_OK]
        check(f + ": русские строки — только с английской парой (A.t('…', '…'))",
              not left, ["%d %s" % (ln, t[:60]) for ln, t in left[:12]])
    # Атрибуты, которые на лету ставит скрипт (на обоих языках) — их
    # проверяет браузерная часть на настоящей английской странице.
    js_managed = {("index.html", "btn-bell", "aria-label"), ("watch.html", "c-play", "aria-label"),
                  ("watch.html", "w-prev", "aria-label"), ("watch.html", "w-prev", "title"),
                  ("watch.html", "w-next", "aria-label"), ("watch.html", "w-next", "title"),
                  ("watch.html", "lang-btn", "title"), ("watch.html", "cc-btn", "title")}
    pair = {"aria-label": "data-en-aria", "title": "data-en-title", "alt": "data-en-alt",
            "placeholder": "data-en-ph"}
    for f in PAGES_HTML:
        src = re.sub(r"<!--.*?-->", "", read(f), flags=re.S)
        missing = []
        for m in re.finditer(r"<[a-zA-Z][^>]*>", src, flags=re.S):
            tag = m.group(0)
            idm = re.search(r'\sid="([^"]+)"', tag)
            tid = idm.group(1) if idm else ""
            for attr, en in pair.items():
                a = re.search(r"\s" + attr + r'="([^"]*)"', tag)
                if not a or not CYR.search(a.group(1)) or en + "=" in tag:
                    continue
                val = a.group(1)
                if (f, tid, attr) in js_managed:
                    continue
                if " / " in val and re.search("[A-Za-z]", val):
                    continue                      # «Язык сайта / Site language» — для обоих сразу
                if val in ("Русский",) or "data-th=" in tag:
                    continue                      # имя языка — на нём самом; темы подписывает скрипт
                missing.append("%s %s=%s" % (tid or tag[:30], attr, val))
        check(f + ": у русских aria-label/title/alt/placeholder есть английская пара",
              not missing, missing[:10])

    group("4. Хранилище браузера и чужие данные")
    for f in OWN_JS:
        lines = read(f).split("\n")
        loose = []
        for i, l in enumerate(lines):
            code = l.split("//")[0]
            for word in ("localStorage.", "JSON.parse("):
                if word in code:
                    around = "\n".join(lines[max(0, i - 3):i + 1])
                    if "try" not in around:
                        loose.append("%d %s" % (i + 1, l.strip()[:70]))
        check(f + ": localStorage и JSON.parse — только внутри try", not loose, loose)
    app = read("app.js")
    check("обложки: только http/https (safeUrl) и экранирование для CSS (cssUrl)",
          ".map(safeUrl)" in app and "node.style.backgroundImage = cssUrl(u)" in app
          and "function cssUrl" in app)
    check("цвет кружка проверяется перед style.background",
          "/^#[0-9a-f]{6}$/i.test(String(m.avatar_color" in app)
    check("/api/me не спрашивается без метки csrf (нет 401 в консоли у не вошедших)",
          "if (!csrfToken()) { me = null; return Promise.resolve(null); }" in app)
    check("выход забывает, о каких сериях уже уведомляли",
          "'animedik.notified'" in app and "forgetPerson()" in app)

    ix, wj = read("index.js"), read("watch.js")
    check("«Подтвердить» вход по коду: второй щелчок не шлёт запрос (иначе ошибка "
          "вытесняла окно с запасными кодами)",
          "if (twofaSending) return;" in ix and "btn.disabled = true;" in ix)
    check("«Сохранить», «Сохранить пароль», «Создать», объявление — через guarded()",
          ix.count("guarded(") >= 5)
    check("обрезка фото: палец слушается на картинке, а не на всём окне",
          "window.addEventListener('touchmove'" not in ix
          and "stage.addEventListener('touchmove', move, { passive: false })" in ix)
    check("обложки грузятся по мере прокрутки (IntersectionObserver)",
          "new IntersectionObserver(" in app and "whenSeen(node, next);" in app)
    check("плеер: сохранение при уходе и на телефоне (pagehide, скрытая вкладка)",
          "window.addEventListener('pagehide', saveOnLeave);" in wj
          and "if (document.hidden) saveOnLeave();" in wj)
    check("плеер: тайтл без серий у источника при уходе в список не пишется",
          "if (!st.episodes.length && !st.touched) return;" in wj)
    check("меню «Серия»: пункт части не теряет название (go = null)",
          "openRelated(p, b, null);" in wj and "if (go) go.textContent = '…';" in wj)

    group("5. ES5 и сторонний код")
    for f in OWN_JS:
        toks = list(js_tokens(read(f)))
        es6 = []
        for k, (kind, t, ln) in enumerate(toks):
            nxt = toks[k + 1] if k + 1 < len(toks) else ("", "", 0)
            prv = toks[k - 1] if k else ("", "", 0)
            # class как имя свойства ({ class: 'luna' }, узел.class) — это ES5;
            # объявление класса — уже нет.
            if kind == "id" and t in ("let", "const") and nxt[0] == "id":
                es6.append("%d %s" % (ln, t))
            elif kind == "id" and t == "class" and nxt[0] == "id" and prv[1] != ".":
                es6.append("%d %s" % (ln, t))
            elif (kind == "punc" and t == "=>") or (kind == "str" and t.startswith("`")):
                es6.append("%d %s" % (ln, t))
        check(f + ": только ES5 (без let/const, стрелок и шаблонных строк)", not es6, es6[:5])
    hls = open(os.path.join(WEB, "hls.min.js"), encoding="utf-8").read()
    ver = re.search(r'"(1\.\d+\.\d+)"', hls)
    got = tuple(int(x) for x in ver.group(1).split(".")) if ver else (0,)
    check("hls.js не старее 1.7.3", got >= (1, 7, 3), ver.group(1) if ver else "?")
    check("hls.js без eval / new Function (CSP без unsafe-eval)",
          "new Function" not in hls and not re.search(r"[^.\w]eval\(", hls))


# ==========================================================================
# Часть 2. Настоящий браузер
# ==========================================================================
PASS = "Proverka-Fronta-2026"
XSS = '<img src=x onerror="window.__xss=1">'

# Сервер запускается отдельным процессом тем же Python, что и остальные
# проверки (у него все библиотеки сервера). Ограничители частоты здесь
# раздвинуты: один браузер делает десятки запросов на страницу, а
# проверяется не сервер, а страница. Походы к источникам за новыми сериями
# выключены: к чужим сайтам этот проход не ходит вовсе.
LAUNCHER = r'''
import os, sys
sys.path.insert(0, os.environ["FE_REPO"])
from api import main, security, store, anime
import fake_sources                                 # noqa: E402
fake_sources.install()
import uvicorn
for name in list(vars(main)):
    if isinstance(getattr(main, name), security.RateLimiter):
        setattr(main, name, security.RateLimiter(limit=10**6, period=60))
async def _quiet(*a, **k):
    return None
main.check_titles = _quiet
class _Ep:
    def __init__(self, n): self.ordinal = n; self.title = ""
async def _eps(source, key, title=""):
    return [_Ep(n) for n in (1, 2, 3)]
anime.find_episodes = _eps
store.init()
PASS, XSS = os.environ["FE_PASS"], os.environ["FE_XSS"]
admin = store.create_user("hozyain", PASS, "admin", "Хозяин")
friend = store.create_user("friend", PASS, "user", "Друг")
evil = store.create_user("evil", PASS, "user", (XSS + " evil")[:40])
items = [
    {"key": "mock:naruto", "source": "ru_a", "title": "Наруто", "title_en": "Naruto",
     "poster": "https://img.test/naruto.png", "year": 2002, "genres": "Экшен,Комедия,Сёнэн",
     "total_eps": 3, "watched_ep": 2, "position": 125, "status": "watching", "cid": "mal:20"},
    {"key": "mock:naruto-movie", "source": "ru_a", "title": "Наруто: Фильм",
     "title_en": "Naruto the Movie", "poster": "", "year": 2004, "genres": "Экшен",
     "total_eps": 1, "watched_ep": 1, "status": "done", "cid": "mal:442"},
    {"key": "mock:evil", "source": "ru_a", "title": XSS + " злой", "title_en": XSS + " evil",
     "poster": "javascript:window.__xss=1", "year": 2020, "genres": XSS, "total_eps": 12,
     "watched_ep": 1, "status": "watching"},
    {"key": "mock:bocchi", "source": "ru_a", "title": "Одинокий рокер!",
     "title_en": "Bocchi the Rock!", "poster": "https://img.test/broken.png", "year": 2022,
     "genres": "Музыка,Комедия", "total_eps": 12, "status": "later"},
]
for uid in (admin, friend):
    for it in items:
        store.save_progress(uid, dict(it))
    store.apply_episode_count(uid, "mock:naruto", 3)
    store.apply_episode_count(uid, "mock:naruto", 4)
    for ep in (1, 2):
        store.log_watch(uid, "mock:naruto", "Наруто", "Экшен,Комедия,Сёнэн", ep, 1400)
store.set_news("Объявление: " + XSS, "Announcement: " + XSS)
uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ["FE_PORT"]), log_level="warning")
'''


def free_port():
    """Свободный порт из 8201–8299, наугад, а не первый по счёту: два
    прохода, запущенные рядом, иначе брали бы один и тот же порт, и
    браузер одного ходил бы на сервер другого."""
    import random
    ports = list(range(8201, 8300))
    random.shuffle(ports)
    for p in ports:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="check33-")
        self.log = open(os.path.join(self.tmp, "server.log"), "w")
        for _ in range(3):
            if self._start():
                return
        raise RuntimeError("сервер не запустился:\n" + open(self.log.name).read()[-2000:])

    def _start(self):
        self.port = free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        env = dict(os.environ)
        env.update({"MODE": "private", "SESSION_PEPPER": "check33", "COOKIE_SECURE": "0",
                    "DB_PATH": os.path.join(self.tmp, "c33-%d.db" % self.port),
                    "UPDATES_EVERY_MIN": "0", "HELPER_API_KEY": "", "FE_REPO": HERE,
                    "FE_PASS": PASS, "FE_XSS": XSS, "FE_PORT": str(self.port)})
        py = os.environ.get("FE_SERVER_PY") or sys.executable
        self.proc = subprocess.Popen([py, "-c", LAUNCHER], cwd=HERE, env=env,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        end = time.time() + 60
        while time.time() < end:
            if self.proc.poll() is not None:
                return False                    # порт успели занять — пробуем другой
            try:
                with urllib.request.urlopen(self.base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        # Ответил именно наш процесс, а не чужой на том же порту.
                        time.sleep(0.5)
                        if self.proc.poll() is None:
                            return True
                        return False
            except Exception:                              # noqa: BLE001
                time.sleep(0.2)
        self.proc.kill()
        return False

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _parts(evil=False, current=False):
    out = [
        {"id": "20", "title": "Наруто", "title_en": "Naruto", "cid": "mal:20", "year": 2002,
         "kind": "tv", "episodes": 220, "poster": "https://img.test/naruto.png", "role": "main",
         "step": 1, "main": True, "guess": False, "rel_ru": "", "rel_en": "", "current": current},
        {"id": "442", "title": "Наруто: Фильм", "title_en": "Naruto the Movie", "year": 2004,
         "kind": "movie", "episodes": 1, "poster": "", "role": "extra", "main": False,
         "guess": False, "rel_ru": "побочная история", "rel_en": "side story"},
        {"id": "1735", "title": "Наруто: Ураганные хроники", "title_en": "Naruto: Shippuuden",
         "year": 2007, "kind": "tv", "episodes": 500, "poster": "https://img.test/broken.png",
         "role": "main", "step": 2, "main": False, "guess": False, "rel_ru": "продолжение",
         "rel_en": "sequel"},
    ]
    if evil:
        out.append({"id": "666", "title": XSS, "title_en": XSS, "year": 2001, "kind": XSS,
                    "episodes": 2, "poster": "javascript:window.__xss=1", "role": "other",
                    "rel_ru": XSS, "rel_en": XSS, "main": False, "guess": False})
    return out


def install_routes(ctx, counter):
    """Всё, что ушло бы к чужим сайтам, — подменяется здесь, в браузере."""
    from urllib.parse import parse_qs, urlparse

    def j(route, data, status=200):
        route.fulfill(status=status, body=json.dumps(data, ensure_ascii=False),
                      headers={"Content-Type": "application/json"})

    def q(request):
        return {k: v[0] for k, v in parse_qs(urlparse(request.url).query).items()}

    def media(route, request):
        path = urlparse(request.url).path.lstrip("/")
        local = os.path.join(FIX, path)
        if not os.path.isfile(local):
            return route.fulfill(status=404, body="", headers={"Access-Control-Allow-Origin": "*"})
        ctype = {".m3u8": "application/vnd.apple.mpegurl", ".mp4": "video/mp4",
                 ".m4s": "video/mp4", ".webm": "video/webm"}.get(os.path.splitext(local)[1],
                                                                  "application/octet-stream")
        with open(local, "rb") as fh:
            route.fulfill(status=200, body=fh.read(),
                          headers={"Content-Type": ctype, "Access-Control-Allow-Origin": "*"})

    def img(route, request):
        if "broken" in request.url:
            return route.fulfill(status=404, body="")
        with open(os.path.join(FIX, "poster.png"), "rb") as fh:
            route.fulfill(status=200, body=fh.read(), headers={"Content-Type": "image/png"})

    def find(route, request):
        text = q(request).get("q", "").lower()
        if text == "этти":
            return j(route, {"items": [], "catalog": True, "genre": "ecchi", "hint": "adult_off"})
        cards = [{"id": "20", "title": "Наруто", "title_en": "Naruto", "title_eng": "Naruto",
                  "cid": "mal:20", "year": 2002, "episodes": 220, "kind": "сериал",
                  "poster": "https://img.test/naruto.png", "rating": "pg_13"},
                 {"id": "666", "title": XSS, "title_en": XSS, "title_eng": XSS, "year": 2001,
                  "kind": XSS, "poster": "javascript:window.__xss=1", "rating": "pg_13"}]
        j(route, {"items": cards, "catalog": True})

    def videos(route, request):
        j(route, {"chosen": "RU-A",
                  "dubs": [{"name": "RU-A", "sub": False, "audio": "ru", "lang": "ru"},
                           {"name": XSS, "sub": True, "lang": "ru"}],
                  "videos": [{"quality": 720, "url": "https://media.test/hls/master.m3u8"},
                             {"quality": 480, "url": "https://media.test/clip.webm"}],
                  "subs": [{"id": "s1", "label": XSS, "lang": "en", "src": "/api/subtitle/s1.vtt"}]})

    def subtitle(route, request):
        with open(os.path.join(FIX, "sub.vtt"), "rb") as fh:
            route.fulfill(status=200, body=fh.read(), headers={"Content-Type": "text/vtt"})

    def helper(route, request):
        if request.method == "POST":
            return j(route, {"reply": "Ответ. " + XSS, "chips": [
                {"id": "settings-look", "label": XSS}, {"id": "javascript:alert(1)", "label": "bad"}]})
        j(route, {"state": "ok"})

    def settings_post(route, request):
        if request.method == "POST":
            counter["settings"] = counter.get("settings", 0) + 1
        route.continue_()

    def about(route, request):
        en = q(request).get("lang") == "en"
        j(route, {"found": True, "about_lang": "en" if en else "ru", "title": "Naruto" if en else "Наруто",
                  "about": ("A ninja story. " if en else "История ниндзя. ") + XSS,
                  "genres": ["Action"] if en else ["Экшен"], "year": 2002})

    ctx.route("https://media.test/**", media)
    ctx.route("https://img.test/**", img)
    ctx.route("**/api/find?*", find)
    ctx.route("**/api/franchise?*", lambda r, q_: j(r, {"items": _parts(evil=True)}))
    ctx.route("**/api/related?*", lambda r, q_: j(r, {"items": _parts(current=True)}))
    ctx.route("**/api/about?*", about)
    ctx.route("**/api/random*", lambda r, q_: j(r, {"found": False, "warm": True}) if "warm=1" in q_.url else j(r, {
        "found": True, "title": XSS, "title_en": "Naruto", "title_eng": "Naruto",
        "poster": "https://img.test/naruto.png", "year": 2002, "episodes": 220, "score": 80,
        "genres": ["Экшен", "Неизвестный жанр"], "about": XSS, "about_lang": "ru"}))
    ctx.route("**/api/resolve?*", lambda r, q_: j(r, {"key": "mock:naruto", "source": "ru_a",
                                                     "title": "Наруто", "exact": True, "episodes_total": 3}))
    ctx.route("**/api/where?*", lambda r, q_: j(r, {"here": ["ru_a"], "checked": ["ru_a"], "silent": []}))
    ctx.route("**/api/episodes?*", lambda r, q_: j(r, [{"ordinal": n, "title": ""} for n in (1, 2, 3)]))
    ctx.route("**/api/videos?*", videos)
    ctx.route("**/api/subtitle/**", subtitle)
    ctx.route("**/api/helper?*", helper)
    ctx.route("**/api/helper/ask", helper)
    ctx.route("**/api/poster?*", lambda r, q_: j(r, {"poster": ""}))
    ctx.route("**/api/library/poster", lambda r, q_: j(r, {"poster": ""}))
    ctx.route("**/api/subs?*", lambda r, q_: j(r, {"found": False}))
    ctx.route("**/api/search?*", lambda r, q_: j(r, {"items": [], "source": "ru_a"}))
    ctx.route("**/api/me/settings", settings_post)


INIT = r"""
window.__csp = [];
document.addEventListener('securitypolicyviolation', function (e) {
  window.__csp.push(e.violatedDirective + ' ' + e.blockedURI);
});
window.__rej = [];
window.addEventListener('unhandledrejection', function (e) {
  window.__rej.push(String(e.reason && (e.reason.message || e.reason)));
});
window.__probe = function (wantEn, allowed) {
  var CYR = /[А-Яа-яЁё]/;
  function shown(n) {
    if (!n || !n.getClientRects().length) return false;
    for (var x = n; x && x !== document.body; x = x.parentElement) {
      var s = getComputedStyle(x);
      if (x.hidden || x.getAttribute('aria-hidden') === 'true' || s.visibility === 'hidden' ||
          s.display === 'none') return false;
    }
    return true;
  }
  /* Разрешённое имя целиком или его начало (буква на кружке аватара). */
  function ok(t) { return allowed.some(function (a) { return t.indexOf(a) >= 0 || a.indexOf(t) === 0; }); }
  var out = { cyr: [], noname: [], dialogs: [], xssNodes: 0,
              wide: document.documentElement.scrollWidth - document.documentElement.clientWidth };
  if (wantEn) {
    var w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), t;
    while ((t = w.nextNode())) {
      var v = t.nodeValue.trim();
      if (v && CYR.test(v) && shown(t.parentElement) && !ok(v)) out.cyr.push(v.slice(0, 80));
    }
    document.querySelectorAll('[aria-label],[title],[placeholder],[alt]').forEach(function (n) {
      if (!shown(n)) return;
      ['aria-label', 'title', 'placeholder', 'alt'].forEach(function (a) {
        var v = n.getAttribute(a);
        if (v && CYR.test(v) && !ok(v)) out.cyr.push('@' + a + ' ' + (n.id || n.className) + ': ' + v.slice(0, 60));
      });
    });
  }
  document.querySelectorAll('button, a[href], [role=button], [role=switch], [role=tab], input, textarea').forEach(function (n) {
    if (!shown(n) || n.type === 'hidden' || n.type === 'file') return;
    var name = (n.getAttribute('aria-label') || n.getAttribute('title') || '').trim();
    var lb = n.getAttribute('aria-labelledby');
    if (!name && lb) name = lb.split(/\s+/).map(function (id) { var x = document.getElementById(id); return x ? x.textContent : ''; }).join('').trim();
    if (!name && n.id && document.querySelector('label[for="' + n.id + '"]')) name = 'label';
    if (!name && n.closest('label')) name = 'label';
    if (!name) name = (n.textContent || '').trim();
    if (!name) out.noname.push(n.tagName + '#' + n.id + '.' + n.className);
  });
  document.querySelectorAll('[role=dialog]').forEach(function (d) {
    if (!shown(d)) return;
    var lb = d.getAttribute('aria-labelledby'), name = d.getAttribute('aria-label') || '';
    if (lb) name = lb.split(/\s+/).map(function (id) { var x = document.getElementById(id); return x ? x.textContent : ''; }).join('').trim();
    if (!name) out.dialogs.push(d.id || d.className);
  });
  out.xssNodes = document.querySelectorAll('img[src="x"], img[src$="/x"]').length;
  return out;
};
"""

# Что законно остаётся русским на английской странице: имена людей и
# названия аниме из тестовых данных (у справочника для них нет английских,
# у «Итогов года» английских названий сервер пока не отдаёт), подписи языка
# на нём самом и подпись, рассчитанная на оба языка.
EN_ALLOWED = ["Хозяин", "Друг", "Наруто", "злой", "Одинокий", "Русский", "Язык сайта · Site language",
              "Язык сайта / Site language"]


def browser_checks():
    from playwright.sync_api import sync_playwright

    srv = Server()
    counter = {}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            _run_browser(b, srv, counter)
            b.close()
    finally:
        srv.stop()


class Console:
    def __init__(self, page):
        self.page, self.items, self.me_calls = page, [], 0
        page.on("console", self._msg)
        page.on("pageerror", lambda e: self.items.append("pageerror: " + str(e)[:300]))
        page.on("request", self._req)

    def _req(self, req):
        if req.url.split("?")[0].endswith("/api/me"):
            self.me_calls += 1

    def _msg(self, m):
        if m.type not in ("error", "warning"):
            return
        where = (m.location or {}).get("url", "")
        if "Failed to load resource" in m.text and "broken" in where:
            return              # битая обложка подменена так нарочно
        self.items.append(m.type + ": " + m.text[:200] + " @ " + where)

    def take(self):
        extra = self.page.evaluate("() => ({c: window.__csp || [], r: window.__rej || [], x: window.__xss || 0})")
        out = self.items + ["CSP: " + c for c in extra["c"]] + ["unhandledrejection: " + r for r in extra["r"]]
        if extra["x"]:
            out.append("ВЫПОЛНИЛСЯ ЧУЖОЙ КОД (window.__xss)")
        self.items = []
        self.page.evaluate("() => { window.__csp = []; window.__rej = []; }")
        return out


def _ctx(b, counter, width=1280, height=820):
    ctx = b.new_context(viewport={"width": width, "height": height})
    ctx.add_init_script(INIT)
    install_routes(ctx, counter)
    return ctx


def _login(page, base, user):
    page.goto(base + "/")
    page.wait_for_selector("#gate:not([hidden])")
    page.fill("#lg-user", user)
    page.fill("#lg-pass", PASS)
    page.click("#btn-login")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)


def _post(page, path, body):
    return page.evaluate("([p, b]) => App.api.post(p, b).then(r => r, e => ({error: e.message}))", [path, body])


def _clean(con, page, what, en=False):
    errs = con.take()
    check(what + ": ноль ошибок в консоли, CSP и чужого кода", not errs, errs[:6])
    pr = page.evaluate("([e, a]) => window.__probe(e, a)", [en, EN_ALLOWED])
    check(what + ": чужой <img> не вставился", pr["xssNodes"] == 0, pr["xssNodes"])
    check(what + ": у кнопок и полей есть имена", not pr["noname"], pr["noname"][:5])
    check(what + ": у открытых окон есть имена", not pr["dialogs"], pr["dialogs"])
    if en:
        check(what + ": на английской странице нет русских слов", not pr["cyr"], pr["cyr"][:8])
    return pr


def _wait(page, js, limit=8.0):
    end = time.time() + limit
    while time.time() < end:
        if page.evaluate(js):
            return True
        time.sleep(0.15)
    return False


CONTRAST_JS = r"""(sels) => {
  /* Цвет любого вида — rgb(), oklab() (так браузер отдаёт color-mix) —
     переводим в rgb через холст: он понимает всё, что понимает CSS. */
  var cv = document.createElement('canvas'); cv.width = cv.height = 1;
  var cx = cv.getContext('2d', { willReadFrequently: true });
  function parse(c) {
    if (!c || c === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };
    var m = /^rgba?\(([^)]+)\)$/.exec(c);
    if (m) {
      var p = m[1].split(/[ ,\/]+/).filter(Boolean).map(parseFloat);
      return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
    }
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
    var d = cx.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  }
  function over(t, b) {
    var a = t.a + b.a * (1 - t.a);
    return { r: (t.r * t.a + b.r * b.a * (1 - t.a)) / a, g: (t.g * t.a + b.g * b.a * (1 - t.a)) / a,
             b: (t.b * t.a + b.b * b.a * (1 - t.a)) / a, a: a };
  }
  function lum(c) {
    var f = function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  }
  var out = [];
  sels.forEach(function (sel) {
    var el = document.querySelector(sel);
    if (!el || !el.getClientRects().length || !(el.textContent || '').trim()) return;
    var layers = [];
    for (var n = el; n; n = n.parentElement) {
      var c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    var bg = { r: 255, g: 255, b: 255, a: 1 };
    for (var i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
    var s = getComputedStyle(el), fg = over(parse(s.color), bg);
    var L1 = lum(fg), L2 = lum(bg);
    var big = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && +s.fontWeight >= 700);
    out.push({ sel: sel, ratio: Math.round((Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05) * 100) / 100,
               need: big ? 3 : 4.5, color: s.color });
  });
  return out;
}"""

WATCH = ("/watch?key=mock%3Anaruto&source=ru_a&title=%D0%9D%D0%B0%D1%80%D1%83%D1%82%D0%BE"
         "&poster=https%3A%2F%2Fimg.test%2Fnaruto.png&total=3&cid=mal%3A20")


def _run_browser(b, srv, counter):
    base = srv.base

    group("6. Страница входа")
    ctx = _ctx(b, counter)
    page = ctx.new_page()
    con = Console(page)
    page.goto(base + "/")
    page.wait_for_selector("#gate:not([hidden])")
    time.sleep(0.4)
    check("не вошедший не спрашивает /api/me (в консоли нет красного 401)", con.me_calls == 0, con.me_calls)
    _clean(con, page, "вход по-русски")
    page.click('.langrow .lang[data-set-lang="en"]')
    time.sleep(0.2)
    _clean(con, page, "вход по-английски", en=True)
    page.fill("#lg-user", "friend")
    page.fill("#lg-pass", "wrong-password-1")
    page.click("#btn-login")
    page.wait_for_selector("#gate-err.show")
    msg = page.inner_text("#gate-err")
    check("ошибка сервера на английской странице — по-английски", msg == "Wrong username or password.", msg)
    con.take()                      # 401 неверного пароля — ожидаемый ответ, не поломка
    ctx.close()

    group("7. Главная вошедшего: окна, чужой текст, двойные щелчки")
    ctx = _ctx(b, counter)
    page = ctx.new_page()
    con = Console(page)
    _login(page, base, "friend")
    time.sleep(1.0)
    _clean(con, page, "главная")
    check("объявление с <img> показано текстом", XSS in page.inner_text("#sitenews-text"))
    page.click("#who")
    page.wait_for_selector("#ucard-wrap.show")
    time.sleep(0.3)
    _clean(con, page, "карточка профиля")
    page.click("#uc-set")
    page.wait_for_selector("#veil-set.show")
    for sec in ("look", "lang", "view", "safe", "profile"):
        page.click("#cab-t-" + sec)
        time.sleep(0.2)
    _clean(con, page, "настройки, все разделы")
    counter["settings"] = 0
    page.dblclick("#set-save")
    _wait(page, "() => document.getElementById('saved').classList.contains('show')")
    time.sleep(0.5)
    check("двойной щелчок «Сохранить» — один запрос", counter.get("settings") == 1, counter)
    page.keyboard.press("Escape")
    time.sleep(0.2)

    page.click("#btn-roul")
    page.wait_for_selector("#roul.done")
    time.sleep(0.3)
    check("«Рандом»: чужое название и описание — текстом", XSS in page.inner_text("#pickres"))
    check("«Рандом»: фокус внутри окна",
          page.evaluate("() => document.getElementById('roul').contains(document.activeElement)"))
    _clean(con, page, "«Рандом»")
    page.keyboard.press("Escape")
    time.sleep(0.2)
    check("«Рандом»: после Esc фокус вернулся на кнопку",
          page.evaluate("() => !!document.activeElement && document.activeElement.id === 'btn-roul'"))

    page.fill("#q", "наруто")
    page.keyboard.press("Enter")
    page.wait_for_selector("#search-grid .card")
    time.sleep(0.4)
    page.click("#search-grid .card >> nth=0")
    page.wait_for_selector("#veil-fr.show")
    time.sleep(0.6)
    _clean(con, page, "поиск и окно частей")
    page.keyboard.press("Escape")

    page.click("#btn-svyaz")
    _wait(page, "() => !document.getElementById('hlp-form').hidden")
    page.fill("#hlp-in", "тема?")
    page.keyboard.press("Enter")
    page.wait_for_selector("#hlp-log .hlpmsg.bot:not(.typing)")
    chips = page.evaluate("() => Array.from(document.querySelectorAll('#hlp-log [data-go]'))"
                          ".map(b => b.getAttribute('data-go'))")
    check("помощник: кнопки «Открыть» — только из белого списка", chips == ["settings-look"], chips)
    _clean(con, page, "помощник")
    page.keyboard.press("Escape")

    res = page.evaluate("""() => new Promise(done => {
        const q = document.getElementById('q'); q.focus();
        const first = App.ask({title: 'A'});
        App.ask({title: 'B', text: 'b'});
        first.then(v => setTimeout(() => {
          const box = document.querySelector('.veil.dialog .dbox');
          const lab = box && document.getElementById(box.getAttribute('aria-labelledby'));
          document.querySelector('.veil.dialog .ghost').click();
          setTimeout(() => done({first: v, label: lab && lab.textContent,
                                 back: document.activeElement === q}), 60);
        }, 60));
    })""")
    check("окно, вытесненное другим, отвечает «отмена» (а не висит вечно)", res.get("first") is False, res)
    check("у окна A.ask есть имя: aria-labelledby → заголовок", res.get("label") == "B", res)
    check("после закрытия окна фокус вернулся туда, где был", res.get("back") is True, res)
    e422 = page.evaluate("() => App.api.post('/api/me/password', {current: '', new: ''})"
                         ".then(() => 'ok', e => e.message)")
    check("422 (поля не приняты) — человеческая фраза, а не [object Object]",
          e422.startswith("Проверьте поля"), e422)
    con.take()

    page.evaluate("() => localStorage.setItem('animedik.notified', '[\"mock:naruto#4\"]')")
    page.click("#who")
    page.wait_for_selector("#ucard-wrap.show")
    page.click("#btn-out")
    page.wait_for_selector("#gate:not([hidden])")
    check("выход забыл, о каких сериях уведомляли (чужой список в браузере)",
          page.evaluate("() => localStorage.getItem('animedik.notified')") is None)
    con.take()
    ctx.close()

    group("8. Админка")
    ctx = _ctx(b, counter)
    page = ctx.new_page()
    con = Console(page)
    _login(page, base, "hozyain")
    time.sleep(0.6)
    page.click("#who")
    page.click("#uc-admin")
    page.wait_for_selector("#admin-list .acc")
    check("кнопки проверки чужих источников нет", page.locator("#cab-encheck").count() == 0)
    _clean(con, page, "админка")
    page.click("#cab-news")
    page.wait_for_selector("#veil-news.show")
    time.sleep(0.3)
    _clean(con, page, "редактор объявления")
    ctx.close()

    group("9. Страница просмотра: плеер")
    ctx = _ctx(b, counter)
    page = ctx.new_page()
    con = Console(page)
    _login(page, base, "friend")
    page.goto(base + WATCH)
    page.wait_for_selector("#w-eplist .eprow")
    time.sleep(0.8)
    _clean(con, page, "просмотр до пуска")
    page.click("#bigplay")
    check("поток HLS играет (hls.js)",
          _wait(page, "() => document.getElementById('video').currentTime > 0.5", 10))
    page.click('[data-menu="m-q"]')
    page.click("#m-q button >> nth=1")
    check("обычный файл играет",
          _wait(page, "() => document.getElementById('video').currentSrc.indexOf('clip.webm') > 0"
                      " && document.getElementById('video').currentTime > 0.5", 10))
    page.keyboard.press("c")
    check("субтитры включаются клавишей C",
          _wait(page, "() => Array.from(document.getElementById('video').textTracks)"
                      ".some(t => t.mode === 'showing')"))
    page.click("#dub-btn")
    time.sleep(0.2)
    check("меню под плеером: aria-expanded", page.get_attribute("#dub-btn", "aria-expanded") == "true")
    check("чужое имя озвучки — текстом", XSS in page.inner_text("#m-d"))
    page.keyboard.press("Escape")
    time.sleep(0.2)
    check("Esc закрывает меню", page.evaluate("() => !document.querySelector('.menu.show')"))
    before = page.evaluate("() => App.me.settings.autonext !== false")
    page.click("#w-autobox .sw")
    time.sleep(0.8)
    saved = page.evaluate("() => App.api.get('/api/me').then(m => m.settings.autonext !== false)")
    check("«Следующая сама» под плеером сохраняется в аккаунт", saved is (not before), (before, saved))
    _clean(con, page, "просмотр: HLS, файл, субтитры, меню")
    ctx.close()

    group("10. Английская страница — без русских слов")
    ctx = _ctx(b, counter)
    page = ctx.new_page()
    con = Console(page)
    _login(page, base, "friend")
    _post(page, "/api/me/settings", {"ui_lang": "en"})
    page.reload()
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)
    time.sleep(0.8)
    _clean(con, page, "главная (EN)", en=True)
    errs = page.evaluate("""() => Promise.all([
        App.api.post('/api/me/password', {current: '', new: ''}).then(() => 'ok', e => e.message),
        App.api.post('/api/me/profile', {avatar_color: 'red'}).then(() => 'ok', e => e.message)])""")
    check("ошибки сервера по-английски: 422 и 400",
          errs[0].startswith("Check the fields") and errs[1] == "The colour must look like #RRGGBB.", errs)
    con.take()                      # 422 и 400 выше — нарочные ответы, не поломка
    page.click("#who")
    time.sleep(0.3)
    _clean(con, page, "карточка профиля (EN)", en=True)
    page.click("#uc-set")
    page.wait_for_selector("#veil-set.show")
    for sec in ("profile", "look", "lang", "view", "safe"):
        page.click("#cab-t-" + sec)
        time.sleep(0.25)
        _clean(con, page, "настройки: " + sec + " (EN)", en=True)
    page.keyboard.press("Escape")
    for path, sel in ((WATCH, "#w-eplist .eprow"), ("/stats", "#year .mon"), ("/pravila", ".pravila-list")):
        page.goto(base + path)
        page.wait_for_selector(sel)
        time.sleep(0.8)
        _clean(con, page, path.split("?")[0] + " (EN)", en=True)
    ctx.close()

    group("11. Телефон: страница не шире экрана")
    ctx = _ctx(b, counter, 390, 844)
    page = ctx.new_page()
    con = Console(page)
    _login(page, base, "friend")
    for path, sel in (("/", "#grid .boxcard"), (WATCH, "#w-eplist .eprow"),
                      ("/stats", "#year .mon"), ("/pravila", ".pravila-list")):
        page.goto(base + path)
        page.wait_for_selector(sel)
        time.sleep(0.6)
        pr = _clean(con, page, "телефон " + path.split("?")[0])
        check("телефон " + path.split("?")[0] + ": нет прокрутки вбок", pr["wide"] <= 0, pr["wide"])
    ctx.close()

    group("12. Гость")
    ctx = _ctx(b, counter)
    page = ctx.new_page()
    con = Console(page)
    page.goto(base + "/")
    page.wait_for_selector("#gate:not([hidden])")
    page.click("#btn-guest")
    page.wait_for_selector("#app:not([hidden])")
    page.wait_for_selector("#boot", state="hidden", timeout=15000)
    _clean(con, page, "гость: главная")
    page.click("#btn-svyaz")
    _wait(page, "() => !document.getElementById('hlp-note').hidden")
    _clean(con, page, "гость: помощник")
    ctx.close()

    group("13. Темы и праздники: консоль и контраст")
    ctx = _ctx(b, counter)
    page = ctx.new_page()
    con = Console(page)
    _login(page, base, "hozyain")
    for theme, hol in (("kak-seychas", "halloween"), ("noch", "newyear"), ("ugol", "sakura"), ("bumaga", "off")):
        _post(page, "/api/me/settings", {"theme": theme, "holiday": hol})
        for path, sel in (("/", "#grid .boxcard"), ("/stats", "#year .mon")):
            page.goto(base + path)
            page.wait_for_selector(sel)
            time.sleep(0.8)
            _clean(con, page, theme + " / " + hol + " " + path)
            ratios = page.evaluate(CONTRAST_JS, ["footer a", "footer .in span", ".b .sub", ".mon .mn",
                                                 ".hero .k", ".b.mint .n", "#list-count", ".boxcard .meta"])
            low = [r for r in ratios if r["ratio"] < r["need"]]
            check(theme + " " + path + ": контраст текста не ниже нормы", not low, low)
    ctx.close()


def _pw_python():
    """Другой Python, у которого есть Playwright."""
    # Сравниваем пути как есть, а не через realpath: виртуальное окружение —
    # это ссылка на тот же системный Python, но библиотеки у него свои.
    mine = os.path.abspath(sys.executable)
    for cand in (os.environ.get("PW_PYTHON"), shutil.which("python3"), shutil.which("python")):
        if not cand or os.path.abspath(cand) == mine:
            continue
        try:
            ok = subprocess.run([cand, "-c", "import playwright.sync_api"],
                                capture_output=True, timeout=60).returncode == 0
        except Exception:                                  # noqa: BLE001
            ok = False
        if ok:
            return cand
    return None


def run():
    only_browser = "--browser-only" in sys.argv
    if not only_browser:
        static_checks()
    try:
        import playwright.sync_api  # noqa: F401
        have = True
    except ImportError:
        have = False
    if have:
        try:
            browser_checks()
        except Exception as exc:                           # noqa: BLE001
            group("Браузерная часть")
            check("браузерная часть отработала до конца", False, repr(exc))
    elif not only_browser:
        other = _pw_python()
        group("Браузерная часть")
        if other:
            print("  Playwright есть у " + other + " — браузерная часть идёт там.", flush=True)
            env = dict(os.environ)
            env["FE_SERVER_PY"] = sys.executable
            r = subprocess.run([other, os.path.abspath(__file__), "--browser-only"], env=env)
            check("браузерная часть прошла (" + other + ")", r.returncode == 0)
        else:
            print("  !!! ПРОПУСК: Playwright не найден ни у этого Python, ни у python3.")
            print("      Браузерная часть НЕ проверялась. Поставить:")
            print("      pip install playwright && playwright install chromium")

    print()
    if FAILS:
        print("ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("Все проверки прохода 33 прошли.")


if __name__ == "__main__":
    run()
