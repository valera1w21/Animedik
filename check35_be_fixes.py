"""Проход 35: мелкие правки сервера после аудита (ветка ref-be).

Что проверяется:

1. Картинки разбираются только JPEG / PNG / WebP (formats= у Image.open).
2. COOKIE_SECURE выключается только явным «0 / false / no / off»; «true»,
   «yes», пусто — защита включена.
3. LOG_LEVEL любыми буквами («info»), неизвестное слово — INFO и строка
   в журнале, а не падение.
4. SMTP_PORT с опечаткой — письма выключены, в журнале причина, сайт
   запускается.
5. /api/health трогает базу: база не отвечает — 503, без подробностей.
6. Ошибки по-английски: у КАЖДОЙ русской фразы, которая может попасть
   в detail, есть перевод (разбор исходников api/); ответ несёт detail_en
   всегда, а detail — на языке сайта (X-UI-Lang / кука lang).
7. /api/stats/year отдаёт латинские имена тайтлов и английские жанры.
8. Обложка из /api/library/progress — только https с известных сайтов.
9. CSP без «http:».

Сети нет: всё внутри TestClient и отдельных процессов Python.
"""
import ast
import glob
import io
import logging
import os
import re
import subprocess
import sys
import tempfile

os.environ.setdefault("MODE", "private")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p35.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass35")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient          # noqa: E402
from PIL import Image                              # noqa: E402
from api import anime, errors, helper, main, security, store   # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Long-Passw0rd-135"
CYR = re.compile(r"[а-яё]", re.I)


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def reset():
    for lim in (main.login_guard, main.api_limit, main.guest_limit, main.write_limit,
                main.avatar_limit, main.pass_limit):
        lim.reset()


def login(name):
    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": name, "password": PASS})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


def py(code, **env):
    """Запуск кода в отдельном процессе: настройки читаются при импорте."""
    full = dict(os.environ, MODE="demo", SESSION_PEPPER="t", PYTHONPATH=HERE,
                DB_PATH=os.path.join(tempfile.mkdtemp(), "sub.db"))
    full.update(env)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=full, cwd=HERE, timeout=120)
    return r.returncode, r.stdout.strip(), r.stderr


# --------------------------------------------------------------------------
# Разбор исходников: какие русские фразы могут оказаться в detail
# --------------------------------------------------------------------------
# Откуда текст попадает в ответ с ошибкой:
#   * HTTPException(detail=…) — строкой, f-строкой, именем константы или
#     say(lang, русская, английская) у помощника;
#   * anime.upstream_error(ошибка, «что не вышло»);
#   * helper.HelperError(«текст», …) — уходит через exc.text(lang);
#   * ValueError и LibraryFull из store.py и security.py — ручки отдают
#     их текст как detail (str(exc));
#   * то, что возвращают security.password_problem и login_problem.
SAMPLE = "7"


def _render(node, consts):
    """Текст узла: строка, f-строка (подстановки → SAMPLE) или константа."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else SAMPLE for v in node.values)
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.Attribute) and node.attr in consts:
        return consts[node.attr]
    if isinstance(node, ast.Call):
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name == "say" and len(node.args) >= 2:
            return _render(node.args[1], consts)
    return None


def russian_details():
    """[(где, текст)] — все русские фразы, которые могут уйти в detail."""
    consts = {}
    for mod in (anime, main):
        for k, v in vars(mod).items():
            if k.isupper() and isinstance(v, str) and CYR.search(v):
                consts[k] = v
    found, unknown = [], []
    for path in sorted(glob.glob(os.path.join(HERE, "api", "**", "*.py"), recursive=True)):
        rel = os.path.relpath(path, HERE)
        tree = ast.parse(open(path, encoding="utf-8").read())
        problem_fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                       and n.name in ("password_problem", "login_problem")]
        for fn in problem_fns:
            for n in ast.walk(fn):
                if isinstance(n, ast.Return) and n.value is not None:
                    text = _render(n.value, consts)
                    if text:
                        found.append((f"{rel}:{n.lineno}", text))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            args = []
            if name == "HTTPException":
                args = [k.value for k in node.keywords if k.arg == "detail"]
            elif name == "upstream_error":
                args = node.args[1:2]
            elif name == "HelperError":
                args = node.args[:1]
            elif name in ("ValueError", "LibraryFull") and rel in (
                    os.path.join("api", "store.py"), os.path.join("api", "security.py")):
                args = node.args[:1]
            for a in args:
                text = _render(a, consts)
                if text is None:
                    unknown.append(f"{rel}:{a.lineno} {ast.unparse(a)[:60]}")
                elif CYR.search(text):
                    found.append((f"{rel}:{a.lineno}", text))
    return found, unknown


# Выражения в detail, которые разбор не может прочитать, — и откуда
# на самом деле берётся их текст (эти места разбор проверяет отдельно).
DYNAMIC_OK = (
    "what",              # anime.upstream_error: второй аргумент проверен у вызовов
    "problem",           # security.password_problem — проверено по return
    "str(exc)",          # ValueError / LibraryFull из store.py и security.py
    "exc.text(lang)",    # helper.HelperError — проверено по вызовам
    "exc.detail",        # пересылка уже готовой ошибки
)


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "Хозяин")
    store.create_user("drug", PASS, "user", "Друг")

    # ==================================================================
    group("1. Картинки — только JPEG / PNG / WebP")
    src = open(os.path.join(HERE, "api", "main.py"), encoding="utf-8").read()
    check("Image.open с formats=", src.count("formats=IMAGE_FORMATS") == 2)
    check("и это ровно три формата", tuple(main.IMAGE_FORMATS) == ("JPEG", "PNG", "WEBP"),
          main.IMAGE_FORMATS)
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (1, 2, 3)).save(buf, format="GIF")
    try:
        main._open_image(buf.getvalue())
        gif = "открылась"
    except Exception as exc:                       # noqa: BLE001
        gif = type(exc).__name__
    check("GIF не разбирается", gif != "открылась", gif)
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (1, 2, 3)).save(buf, format="WEBP")
    check("WebP разбирается", main._open_image(buf.getvalue()).size == (40, 40))

    # ==================================================================
    group("2. COOKIE_SECURE")
    code = "from api import main; print(main.COOKIE_SECURE)"
    for value, want in (("0", "False"), ("false", "False"), ("No", "False"), (" OFF ", "False"),
                        ("1", "True"), ("true", "True"), ("yes", "True"), ("on", "True"),
                        ("", "True"), ("абв", "True")):
        rc, out, err = py(code, COOKIE_SECURE=value, SESSION_PEPPER="x" * 40)
        check(f"COOKIE_SECURE={value!r} → Secure {want}", rc == 0 and out.endswith(want),
              (rc, out, err[-200:]))
    rc, out, err = py(code, SESSION_PEPPER="x" * 40, COOKIE_SECURE="0")
    env = dict(os.environ)
    env.pop("COOKIE_SECURE", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=HERE,
                       env=dict(env, MODE="demo", SESSION_PEPPER="x" * 40, PYTHONPATH=HERE,
                                DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db")), timeout=120)
    check("переменной нет вовсе → Secure включён", r.stdout.strip().endswith("True"), r.stdout)

    # ==================================================================
    group("3. LOG_LEVEL любыми буквами")
    code = "import logging; from api import main; print(logging.getLogger().level)"
    rc, out, err = py(code, LOG_LEVEL="info")
    check("«info» маленькими — запуск и INFO", rc == 0 and out.endswith(str(logging.INFO)),
          (rc, out, err[-300:]))
    rc, out, err = py(code, LOG_LEVEL="Debug")
    check("«Debug» — DEBUG", rc == 0 and out.endswith(str(logging.DEBUG)), (rc, out))
    rc, out, err = py(code, LOG_LEVEL="громко")
    check("неизвестное слово — не падение, а INFO", rc == 0 and out.endswith(str(logging.INFO)),
          (rc, out, err[-300:]))
    check("и строка в журнале о неизвестном уровне", "LOG_LEVEL" in err, err[-300:])

    # ==================================================================
    group("4. SMTP_PORT с опечаткой")
    code = "from api import mail, main; print(mail.SMTP_PORT, mail.enabled())"
    rc, out, err = py(code, SMTP_HOST="smtp.test", SMTP_FROM="a@b.c", SMTP_PORT="58o")
    check("сайт запускается", rc == 0, err[-300:])
    check("письма выключены", out.endswith("0 False"), out)
    check("в журнале причина", "SMTP_PORT" in err and "выключены" in err, err[-300:])
    rc, out, err = py(code, SMTP_HOST="smtp.test", SMTP_FROM="a@b.c", SMTP_PORT="70000")
    check("порт вне 1–65535 — тоже выключены", rc == 0 and out.endswith("0 False"), out)
    rc, out, err = py(code, SMTP_HOST="smtp.test", SMTP_FROM="a@b.c", SMTP_PORT=" 465 ")
    check("правильный порт работает", rc == 0 and out.endswith("465 True"), out)
    rc, out, err = py(code, SMTP_HOST="smtp.test", SMTP_FROM="a@b.c", SMTP_PORT="")
    check("пустой — 587, как раньше", rc == 0 and out.endswith("587 True"), out)

    # ==================================================================
    group("5. /api/health трогает базу")
    c = TestClient(main.app)
    r = c.get("/api/health")
    check("база живая — 200 ok", r.status_code == 200 and r.json() == {"status": "ok"}, r.text)
    real = store.connect

    def broken():
        raise RuntimeError("секретная подробность /data/anime.db")
    store.connect = broken
    try:
        r = c.get("/api/health")
    finally:
        store.connect = real
    check("база не отвечает — 503", r.status_code == 503, r.status_code)
    check("без подробностей наружу", "anime.db" not in r.text and "секрет" not in r.text, r.text)
    check("и без входа", c.get("/api/health").status_code == 200)

    # ==================================================================
    group("6. Ошибки по-английски")
    found, unknown = russian_details()
    check("разбор нашёл фразы (больше пятидесяти)", len(found) > 50, len(found))
    missing = sorted({text for _, text in found if errors.translate(text) is None})
    check("у каждой русской фразы из кода есть перевод", not missing, missing[:10])
    strange = [u for u in unknown if not any(u.split(" ", 1)[1].startswith(d) for d in DYNAMIC_OK)]
    check("в detail нет выражений, которые разбор не понимает", not strange, strange[:10])
    check("в переводах нет русских букв (кроме подставленного)",
          not [e for e in errors.EN.values() if CYR.search(e)])
    check("переводов не меньше, чем фраз", len(errors.EN) + len(errors.EN_PATTERNS) >= 80,
          len(errors.EN))

    c = TestClient(main.app)
    r = c.get("/api/library")
    check("без входа: 401 и русский detail, как раньше",
          r.status_code == 401 and r.json().get("detail") == "Нужно войти", r.json())
    check("и detail_en рядом", r.json().get("detail_en") == "Please sign in.", r.json())
    r = c.get("/api/library", headers={"X-UI-Lang": "en"})
    check("X-UI-Lang: en — detail по-английски",
          r.json() == {"detail": "Please sign in.", "detail_en": "Please sign in."}, r.json())
    r = c.get("/api/library", headers={"X-UI-Lang": "ru"})
    check("X-UI-Lang: ru — по-русски", r.json().get("detail") == "Нужно войти", r.json())
    c2 = TestClient(main.app)
    c2.cookies.set("lang", "en")
    r = c2.get("/api/library")
    check("кука lang=en — тоже по-английски", r.json().get("detail") == "Please sign in.", r.json())
    r = c.get("/api/library", headers={"Accept-Language": "en-US,en;q=0.9"})
    check("язык браузера detail не меняет (см. api/errors.py)",
          r.json().get("detail") == "Нужно войти", r.json())

    reset()
    r = c.post("/api/auth/login", json={"login": "hozyain", "password": "не-тот-пароль-1"},
               headers={"X-UI-Lang": "en"})
    check("неверный пароль по-английски", r.status_code == 401
          and r.json().get("detail") == "Wrong login or password.", r.json())
    for _ in range(40):
        r = c.post("/api/auth/login", json={"login": "hozyain", "password": "не-тот-пароль-1"})
        if r.status_code == 429:
            break
    check("429: заголовок Retry-After на месте", r.status_code == 429
          and r.headers.get("retry-after"), (r.status_code, dict(r.headers)))
    check("429: f-строка с минутами переведена",
          re.fullmatch(r"Too many attempts\. Try again in \d+ min\.", r.json().get("detail_en", "")),
          r.json())
    reset()

    r = c.get("/api/nichego-net")
    check("несуществующий адрес: 404 Not Found + detail_en",
          r.status_code == 404 and r.json() == {"detail": "Not Found", "detail_en": "Not Found"},
          r.json())
    r = c.get("/api/admin/users")
    check("админка для чужих — тот же ответ, что у несуществующего",
          r.status_code == 404 and r.json() == {"detail": "Not Found", "detail_en": "Not Found"},
          r.json())
    r = c.put("/api/health")
    check("405: заголовок Allow на месте", r.status_code == 405 and "allow" in r.headers,
          dict(r.headers))
    r = c.post("/api/auth/login", json={"login": "x"})
    check("422: список полей как был и detail_en",
          r.status_code == 422 and isinstance(r.json().get("detail"), list)
          and r.json().get("detail_en"), r.json())
    r = c.post("/api/auth/guest", content=b"x" * (2 * 1024 * 1024),
               headers={"Content-Type": "application/json", "X-UI-Lang": "en"})
    check("413 от потолка тела — тоже по-английски",
          r.status_code == 413 and r.json().get("detail") == "The request is too large.", r.json())

    check("двуязычная «ru · en»: русской странице как есть",
          errors.payload(main.NO_ENGLISH, 404, "ru")["detail"] == main.NO_ENGLISH)
    check("…английской — английская половина",
          errors.payload(main.NO_ENGLISH, 404, "en")["detail"] == "Not available in English yet")
    p = errors.payload("Совсем новая фраза", 409, "en")
    check("незнакомая фраза: detail русский, detail_en — общая по коду",
          p == {"detail": "Совсем новая фраза", "detail_en": errors.BY_STATUS[409]}, p)
    check("подставленное значение переносится",
          errors.translate("Озвучка «Yozh» сейчас не отвечает, и другие тоже")
          == "The dub «Yozh» is not responding right now, and neither are the others.")

    # ==================================================================
    group("7. Итоги года по-английски")
    v = login("hozyain")
    v.post("/api/library/progress", json={
        "key": "jjk", "source": "ru_a", "title": "Магическая битва",
        "title_en": "Jujutsu Kaisen", "genres": "Экшен,Сёнэн", "total_eps": 24,
        "watched_ep": 1, "status": "watching"})
    v.post("/api/library/watched", json={
        "key": "jjk", "source": "ru_a", "title": "Магическая битва",
        "title_en": "Jujutsu Kaisen", "genres": "Экшен,Сёнэн", "watched_ep": 1,
        "position": 1400, "total_eps": 24})
    v.post("/api/library/watched", json={
        "key": "nolatin", "source": "ru_a", "title": "Без латиницы",
        "genres": "Драма", "watched_ep": 1, "position": 900, "total_eps": 12})
    s = v.get("/api/stats/year").json()
    check("прежние поля на месте (пары название–время)",
          all(isinstance(t, list) and len(t) == 2 for t in s["titles"]), s["titles"])
    check("titles_en: латинское имя тайтла",
          s.get("titles_en", {}).get("Магическая битва") == "Jujutsu Kaisen", s.get("titles_en"))
    check("без латинского — в словаре его нет", "Без латиницы" not in s.get("titles_en", {}))
    check("genres_en: английские жанры",
          s.get("genres_en", {}).get("Экшен") == "Action" and s["genres_en"].get("Драма") == "Drama",
          s.get("genres_en"))
    check("незнакомого жанра в словаре нет", "Сёнэн" not in s.get("genres_en", {}),
          s.get("genres_en"))

    # ==================================================================
    group("8. Обложка — только с известных сайтов")

    def poster_of(key):
        row = store.connect().execute(
            "SELECT poster FROM library WHERE user_id = ? AND key = ?",
            (store.get_user_by_login("hozyain")["id"], key)).fetchone()
        return row["poster"] if row else None

    def saved(poster, key="p8"):
        r = v.post("/api/library/progress", json={
            "key": key, "source": "ru_a", "title": "Обложка " + key, "watched_ep": 1,
            "status": "watching", "poster": poster})
        return r.status_code, poster_of(key)

    # Сайты источников (и их хранилища картинок) известны только в личном
    # режиме — здесь это подменные источники (fake_sources.py). В демо
    # остаются справочники.
    ok_urls = ["https://shikimori.one/uploads/poster/animes/1/main.jpg",
               "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/bx1.jpg",
               "https://cdn.myanimelist.net/images/anime/1/1.jpg"]
    if not main.anime.DEMO:
        ok_urls += ["https://static.img-b.example/posters/1.jpg",
                    "https://ru-a.example/storage/releases/posters/1/x.jpg"]
    for url in ok_urls:
        code, got = saved(url, "ok-" + url[8:20])
        check("принято: " + url[8:40], code == 200 and got == url, (code, got))
    noscheme = "static.img-b.example/p.jpg" if not main.anime.DEMO else "shikimori.one/p.jpg"
    code, got = saved("//" + noscheme, "noscheme")
    check("адрес без схемы дочиняется и принимается", got == "https://" + noscheme, got)
    for url in ("https://evil.test/pixel.gif", "http://shikimori.one/x.jpg",
                "https://shikimori.one.evil.test/x.jpg", "https://evilshikimori.one/x.jpg",
                "https://user@shikimori.one/x.jpg", "https://shikimori.one:8443/x.jpg",
                "javascript:alert(1)", "data:image/png;base64,AAAA", "https://127.0.0.1/x.jpg"):
        code, got = saved(url, "bad-" + str(abs(hash(url)) % 10000))
        check("не принято (пусто): " + url[:40], code == 200 and got == "", (code, got))
    code, got = saved("https://evil.test/pixel.gif", "ok-shikimori.on")
    check("чужая обложка не затирает уже сохранённую",
          got == "https://shikimori.one/uploads/poster/animes/1/main.jpg", got)
    r = v.post("/api/library/watched", json={
        "key": "w8", "source": "ru_a", "title": "Обложка w8", "watched_ep": 1,
        "position": 10, "poster": "https://evil.test/pixel.gif"})
    check("/api/library/watched — то же правило", r.status_code == 200 and poster_of("w8") == "",
          (r.status_code, poster_of("w8")))

    # ==================================================================
    group("9. CSP без http:")
    check("в политике нет «http:»", "http:" not in main.CSP, main.CSP)
    h = TestClient(main.app).get("/").headers.get("content-security-policy", "")
    check("страница отдаётся с этой политикой", h == main.CSP and "https:" in h, h)

    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОВАЛЫ:")
        for x in FAILS:
            print("   - " + x)
        return 1
    print("ПРОХОД 35 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
