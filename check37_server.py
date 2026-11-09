"""Проход 37: сервер и безопасность (аудит 3 ноября 2026).

Живой сервер (uvicorn в отдельном процессе, временная база, порт из
диапазона 8101–8149) и настоящие HTTP-запросы: от анонима, гостя,
пользователя и администратора. Каждая группа — одна находка или один
пункт задания; номера находок — как в отчёте (A-01 …). Там, где живой
сервер не нужен (права файлов, чистая арифметика), проверяется прямо
в этом процессе.

Сеть наружу не нужна: источники в личном режиме молчат, и это
учитывается (ответы 409/502 — «источник не ответил»).

Запуск:  MODE=private SESSION_PEPPER=... COOKIE_SECURE=0 python check37_server.py
         (и так же с MODE=demo)
"""
import gzip
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MODE = os.environ.get("MODE", "private")
PEPPER = os.environ.get("SESSION_PEPPER") or "check37-pepper-0123456789abcdef"
os.environ["SESSION_PEPPER"] = PEPPER
os.environ["COOKIE_SECURE"] = "0"
TMP = tempfile.mkdtemp(prefix="p37-")
DB = os.path.join(TMP, "p37.db")
os.environ["DB_PATH"] = DB
os.environ["MODE"] = MODE

import httpx                                           # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
ADMIN_PW = "Admin-Parol-2026!"
USER_PW = "User-Parol-2026!"
PY = sys.executable


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def free_port(lo=8101, hi=8149):
    for port in range(lo, hi + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("нет свободного порта 8101–8149")


class Server:
    """uvicorn в отдельном потоке ЭТОГО процесса (не в другом процессе):
    так проверка может подменять внутренности (сторож сети, TRUST_PROXY)
    и читать журнал. Порт — из диапазона 8101–8149."""

    def __init__(self):
        import logging
        import uvicorn
        from api import main
        import fake_sources
        fake_sources.install()
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.records = []
        handler = logging.Handler()
        handler.emit = lambda rec: self.records.append(rec)
        logging.getLogger("anime").addHandler(handler)
        logging.getLogger("anime").setLevel(logging.DEBUG)
        logging.getLogger("uvicorn.error").addHandler(handler)
        for noisy in ("httpx", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        # Журнал обращений (как в docker: uvicorn пишет путь с параметрами)
        # тоже ловим: в нём не должно быть ничего секретного.
        logging.getLogger("uvicorn.access").addHandler(handler)
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)
        cfg = uvicorn.Config(main.app, host="127.0.0.1", port=self.port, log_level="warning",
                             lifespan="on", proxy_headers=False)   # как в Dockerfile
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            try:
                if httpx.get(self.base + "/api/health", timeout=2, trust_env=False).status_code:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError("сервер не поднялся")

    def logs(self):
        return "\n".join(r.getMessage() for r in self.records)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(10)


def client(srv, ip="203.0.113.5"):
    c = httpx.Client(base_url=srv.base, timeout=30, trust_env=False)
    c.headers["X-Real-IP"] = ip
    return c


def login(c, user, pw, code=""):
    c.headers.pop("X-CSRF-Token", None)
    r = c.post("/api/auth/login", json={"login": user, "password": pw, "code": code})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def guest(c):
    r = c.post("/api/auth/guest", json={})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def make_users():
    from api import store
    store.init()
    store.create_user("hozyain", ADMIN_PW, "admin", "hozyain")
    store.create_user("misha", USER_PW, "user", "misha")
    store.create_user("kate", USER_PW, "user", "kate")


# ----------------------------------------------------------------------
# Маленький «чужой хостинг» на 127.0.0.1 — для прокладки /api/hls.
# ----------------------------------------------------------------------
SEGMENT = bytes(range(256)) * 400          # 100 КБ «куска видео»


class Hosting(BaseHTTPRequestHandler):
    def do_GET(self):                                  # noqa: N802
        if self.path.startswith("/gz"):
            body = gzip.compress(SEGMENT)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/big.m3u8"):
            body = ("#EXTM3U\n" + "".join(f"#EXTINF:4.0,\nseg{i}.ts\n" for i in range(29000))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = SEGMENT
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def run():
    make_users()
    from api import hls, main, netguard, security, store
    # Сторож исходящих запросов (netguard.install) стоит на всех httpx в
    # процессе — и на клиенте этой проверки тоже. Главному потоку (где
    # живут клиенты) разрешаем 127.0.0.1; поток сервера это разрешение не
    # наследует (у потока свой контекст), и сторож там работает как в жизни.
    netguard._ALLOW_PRIVATE.set(True)

    # ==================================================================
    group("A-01. Копия базы (python -m api.admin backup) с правами 600 с первого байта")
    target = os.path.join(TMP, "copy", "copy.db")
    seen = []
    real_connect = sqlite3.connect

    def spy(path, *a, **kw):
        if str(path) == target:
            seen.append(oct(os.stat(target).st_mode & 0o777) if os.path.exists(target)
                        else "файла ещё нет: его создаст sqlite с правами 644")
        return real_connect(path, *a, **kw)
    old_umask = os.umask(0o022)
    try:
        sqlite3.connect = spy
        store.backup(target)
    finally:
        sqlite3.connect = real_connect
        os.umask(old_umask)
    check("в момент открытия файла копии права уже 600", seen == ["0o600"], seen)
    check("и после копирования тоже", oct(os.stat(target).st_mode & 0o777) == "0o600")
    check("копия читается", real_connect(target).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(DB + suffix):
            check(f"сама база{suffix or ''}: 600", oct(os.stat(DB + suffix).st_mode & 0o777) == "0o600")

    # ==================================================================
    group("A-02. Прокладка /api/hls: хостинг сжал кусок видео (gzip)")
    hport = free_port(8130, 8149)
    hosting = ThreadingHTTPServer(("127.0.0.1", hport), Hosting)
    threading.Thread(target=hosting.serve_forever, daemon=True).start()
    # В проверке хостинг живёт на 127.0.0.1: сторожа на время группы пускаем
    # (в жизни он туда не пустит — это проверяет check31, группа BE-01).
    real_ip_ok = netguard.ip_ok
    netguard.ip_ok = lambda ip: True
    srv = Server()
    try:
        u = client(srv)
        check("вход", login(u, "misha", USER_PW).status_code == 200)
        plain = hls.proxied(f"http://127.0.0.1:{hport}/plain.ts", "https://ref.example/")
        r = u.get(plain)
        check("обычный кусок доезжает целиком", r.status_code == 200 and r.content == SEGMENT,
              (r.status_code, len(r.content)))
        check("с Content-Length", r.headers.get("content-length") == str(len(SEGMENT)))
        gz = hls.proxied(f"http://127.0.0.1:{hport}/gz.ts", "https://ref.example/")
        try:
            r = u.get(gz)
            ok = r.status_code == 200 and r.content == SEGMENT
            extra = (r.status_code, len(r.content), r.headers.get("content-length"))
        except httpx.HTTPError as exc:
            ok, extra = False, type(exc).__name__ + ": " + str(exc)[:80]
        check("сжатый хостингом кусок доезжает целиком и без обрыва", ok, extra)
        check("в журнале нет «Response content longer than Content-Length»",
              "longer than Content-Length" not in srv.logs())
        check("хостинг просят не сжимать (Accept-Encoding: identity)",
              hls.client("").headers.get("accept-encoding") == "identity")

        # A-05. Огромный плейлист (29 000 адресов — под потолком) переписывается
        # в отдельном потоке: пока он переписывается, /api/health отвечает
        # быстро. Раньше цикл событий стоял всё время переписывания.
        big = hls.proxied(f"http://127.0.0.1:{hport}/big.m3u8", "https://ref.example/")
        gaps = []
        stop = [False]

        def pulse():
            while not stop[0]:
                t0 = time.perf_counter()
                try:
                    httpx.get(srv.base + "/api/health", timeout=10, trust_env=False)
                except httpx.HTTPError:
                    pass
                gaps.append(time.perf_counter() - t0)
                time.sleep(0.01)
        th = threading.Thread(target=pulse, daemon=True)
        th.start()
        t0 = time.perf_counter()
        r = u.get(big, timeout=60)
        took = time.perf_counter() - t0
        stop[0] = True
        th.join(5)
        check("плейлист на 29 000 адресов переписан", r.status_code == 200 and r.text.count("/api/hls?") >= 29000,
              (r.status_code, r.text.count("/api/hls?")))
        worst = max(gaps) if gaps else 0
        check("пока он переписывался, остальные запросы не замирали",
              worst < max(0.15, took * 0.4), f"самая долгая пауза {worst:.2f}с при переписывании {took:.2f}с")
    finally:
        netguard.ip_ok = real_ip_ok
        srv.stop()
        hosting.shutdown()

    # ==================================================================
    group("A-03. Чужой Origin у запросов, меняющих данные")
    srv = Server()
    try:
        anon = client(srv)
        host = f"127.0.0.1:{srv.port}"
        for origin, want in (("https://evil.example", 403), ("null", 403),
                             (f"http://{host}", 200), ("http://127.0.0.1", 200),
                             (f"http://127.0.0.1:{srv.port + 1}", 403)):
            r = anon.post("/api/auth/guest", json={}, headers={"Origin": origin})
            check(f"гостевой пропуск с Origin {origin!r} → {want}", r.status_code == want, r.status_code)
        fresh = client(srv, "203.0.113.6")
        r = fresh.post("/api/auth/login", json={"login": "misha", "password": USER_PW},
                       headers={"Origin": "https://evil.example"})
        check("вход с чужого сайта отклонён до проверки пароля", r.status_code == 403, r.status_code)
        check("…и в сессию не попал", "sid" not in fresh.cookies)
        check("без Origin (curl, скрипт) — как раньше: метку формы проверяет ручка",
              anon.post("/api/auth/guest", json={}).status_code == 200)
        u = client(srv, "203.0.113.7")
        check("вход со своим Origin работает",
              u.post("/api/auth/login", json={"login": "misha", "password": USER_PW},
                     headers={"Origin": f"http://{host}"}).status_code == 200)
        u.headers["X-CSRF-Token"] = security.csrf_for(u.cookies.get("sid"))
        r = u.post("/api/me/profile", json={"display_name": "m"}, headers={"Origin": "https://evil.example"})
        check("даже с верной меткой формы чужой Origin — отказ", r.status_code == 403, r.status_code)
        check("чтение (GET) с чужим Origin не трогаем",
              u.get("/api/me", headers={"Origin": "https://evil.example"}).status_code == 200)
        r = anon.post("/api/admin/users", json={}, headers={"Origin": "https://evil.example"})
        check("админка постороннему по-прежнему 404, а не 403", r.status_code == 404, r.status_code)
    finally:
        srv.stop()

    # ==================================================================
    group("A-04. HEAD на страницах и /api/health (сторожа доступности)")
    srv = Server()
    try:
        c = client(srv)
        for path in ("/", "/watch", "/stats", "/pravila", "/api/health", "/favicon.ico"):
            r = c.head(path)
            check(f"HEAD {path} → 200 без тела", r.status_code == 200 and r.content == b"",
                  (r.status_code, len(r.content)))
        full = c.get("/")
        head = c.head("/")
        check("у HEAD / те же защитные заголовки, что у GET",
              head.headers.get("content-security-policy") == full.headers.get("content-security-policy")
              and head.headers.get("cache-control") == "no-store")
        check("HEAD несуществующей страницы → 404", c.head("/nope").status_code == 404)
        check("а TRACE по-прежнему 405", c.request("TRACE", "/").status_code == 405)
    finally:
        srv.stop()

    # ==================================================================
    group("Журнал (уровень DEBUG) без паролей, кодов, токенов и секретов")
    srv = Server()
    from api import twofa
    try:
        k = client(srv, "203.0.113.90")
        login(k, "kate", "wrong-password-xyz")
        check("вход", login(k, "kate", USER_PW).status_code == 200)
        sid = k.cookies.get("sid")
        r = k.post("/api/me/2fa/start", json={"password": USER_PW})
        secret = r.json()["secret"]
        code = twofa._code_at(secret, int(time.time() // 30))
        r = k.post("/api/me/2fa/enable", json={"code": code})
        backup = r.json()["backup"]
        k2 = client(srv, "203.0.113.91")
        login(k2, "kate", USER_PW, "000000")
        login(k2, "kate", USER_PW, backup[0])
        k.post("/api/me/mail", json={"email": "kate@example.com", "want": False})
        k.post("/api/me/password", json={"current": USER_PW, "new": "Drugoy-Parol-2026!"})
        text = srv.logs()
        for name, needle in (("пароль (верный)", USER_PW), ("пароль (неверный)", "wrong-password-xyz"),
                             ("новый пароль", "Drugoy-Parol-2026!"), ("токен сессии", sid),
                             ("метка формы", k.headers.get("X-CSRF-Token", "")),
                             ("секрет 2FA", secret), ("код 2FA", code), ("запасной код", backup[0]),
                             ("хэш пароля", "pbkdf2_sha256$"), ("адрес почты", "kate@example.com"),
                             ("SESSION_PEPPER", PEPPER)):
            check(f"в журнале нет: {name}", bool(needle) and needle not in text)
        check("а сам вход в журнале отмечен (логин, роль)", "вход: kate (user)" in text)
    finally:
        srv.stop()

    # ==================================================================
    group("База цела после kill -9 посреди записи (WAL)")
    kdb = os.path.join(TMP, "kill.db")
    writer = os.path.join(TMP, "writer.py")
    with open(writer, "w", encoding="utf-8") as fh:
        fh.write(
            "import os, sys\n"
            f"sys.path.insert(0, {HERE!r})\n"
            f"os.environ.update(DB_PATH={kdb!r}, SESSION_PEPPER={PEPPER!r}, MODE={MODE!r})\n"
            "from api import store\n"
            "store.init(); uid = store.create_user('writer', 'Writer-Parol-2026!', 'user')\n"
            "i = 0\n"
            "while True:\n"
            "    store.save_progress(uid, {'key': 'k%d' % i, 'title': 'Тайтл %d' % i, 'cid': 'mal:%d' % i,"
            " 'watched_ep': i % 50, 'genres': 'a,b,c'})\n"
            "    store.log_watch(uid, 'k%d' % i, 't', 'g', i, 100)\n"
            "    i += 1\n")
    proc = subprocess.Popen([PY, writer], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)
    proc.kill()                                   # SIGKILL, как при отключении света
    proc.wait()
    conn = sqlite3.connect(kdb)
    rows = conn.execute("SELECT COUNT(*) FROM library").fetchone()[0]
    check("за время работы что-то успело записаться", rows > 10, rows)
    check("quick_check: ok", conn.execute("PRAGMA quick_check").fetchone()[0] == "ok")
    check("связи целы (foreign_key_check пуст)", conn.execute("PRAGMA foreign_key_check").fetchall() == [])
    logs_ = conn.execute("SELECT COUNT(*) FROM watch_log").fetchone()[0]
    check("журнал и полка согласованы (запись — одной транзакцией каждая)", logs_ in (rows, rows - 1, rows + 1),
          (rows, logs_))
    conn.close()

    # ==================================================================
    group("Хвосты владельца: MODE по умолчанию одинаков везде (demo)")
    import re
    from api import anime
    import fake_sources
    fake_sources.install()
    with open(os.path.join(HERE, "api", "anime.py"), encoding="utf-8") as fh:
        m = re.search(r'MODE = os.getenv\("MODE", "(\w+)"\)', fh.read())
    with open(os.path.join(HERE, "docker-compose.yml"), encoding="utf-8") as fh:
        m2 = re.search(r'MODE: "\$\{MODE:-(\w+)\}"', fh.read())
    with open(os.path.join(HERE, ".env.example"), encoding="utf-8") as fh:
        m3 = re.search(r"^MODE=(\w+)", fh.read(), re.M)
    # Публичная версия: забытая переменная поднимает витрину, а не личный
    # режим без источников.
    check("api/anime.py: по умолчанию demo", m and m.group(1) == "demo", m and m.group(1))
    check("docker-compose.yml: ${MODE:-demo}", m2 and m2.group(1) == "demo", m2 and m2.group(1))
    check(".env.example: MODE=demo", m3 and m3.group(1) == "demo", m3 and m3.group(1))

    # ==================================================================
    group("Хвосты владельца: /api/search — проверка источника стоит ДО подстановки")
    from fastapi import HTTPException
    for bad in ("../../os", "os", "subprocess", "x" * 40):
        try:
            main.pick_source(bad, "ru")
            check(f"мусорное имя {bad!r} → 400", False, "прошло")
        except HTTPException as exc:
            check(f"мусорное имя {bad!r} → 400", exc.status_code == 400, exc.status_code)
    if anime.DEMO:
        # В публичной версии личных источников нет: чужое имя — просто
        # неизвестное, а пустое — демо-источник.
        try:
            main.pick_source("ru_a", "ru")
            check("в демо имя несуществующего источника (ru_a) → 400", False, "прошло")
        except HTTPException as exc:
            check("в демо имя несуществующего источника (ru_a) → 400", exc.status_code == 400,
                  exc.status_code)
        got = main.pick_source("", "ru")
        check("в демо пустое имя — демо-источник", got == anime.anime_demo.NAME, got)
    else:
        got = main.pick_source("ru_a", "ru")
        check("в личном режиме ru_a остаётся ru_a", got == "ru_a", got)

    # ==================================================================
    group("Регрессии прошлых аудитов (живые запросы)")
    srv = Server()
    try:
        # подделка адреса: TRUST_PROXY=1 — первый элемент X-Forwarded-For не считается
        codes = []
        for i in range(10):
            c = httpx.Client(base_url=srv.base, trust_env=False)
            r = c.post("/api/auth/login", json={"login": "misha", "password": "wrong-1"},
                       headers={"X-Forwarded-For": f"10.0.{i}.1, 198.51.100.77"})
            codes.append(r.status_code)
        check("TRUST_PROXY=1: подмена первого элемента X-Forwarded-For не обходит блокировку",
              codes[-1] == 429, codes)
        main.TRUST_PROXY = False
        codes = []
        for i in range(10):
            c = httpx.Client(base_url=srv.base, trust_env=False)
            r = c.post("/api/auth/login", json={"login": "hozyain", "password": "wrong-1"},
                       headers={"X-Forwarded-For": f"10.0.{i}.1", "X-Real-IP": f"10.0.{i}.2",
                                "CF-Connecting-IP": f"10.0.{i}.3", "Forwarded": f"for=10.0.{i}.4"})
            codes.append(r.status_code)
        main.TRUST_PROXY = True
        check("TRUST_PROXY=0: никакой заголовок адреса не считается", codes[-1] == 429, codes)
        main.login_guard.reset()
        # склейка логина
        c = client(srv, "203.0.113.11")
        for weird in ("mis\nha", "mi\u200bsha", "mis\rha", "misha\x00"):
            check(f"логин {weird!r} отклоняется, а не исправляется",
                  login(c, weird, USER_PW).status_code == 401)
        check("а ' misha\\n' (мусор по краям) и MISHA — тот же человек",
              login(c, " misha\n", USER_PW).status_code == 200
              and login(c, "MISHA", USER_PW).status_code == 200)
        u = c
        # длинные настройки: отказ, а не битый JSON
        r = u.post("/api/me/settings", json={"accent": "x" * 4000})
        check("слишком длинное значение настройки → 422, а не обрезка", r.status_code == 422, r.status_code)
        r = u.post("/api/me/settings", json={"garbage": "x" * 100000, "accent": "sky"})
        after = u.get("/api/me").json()["settings"]
        check("лишние поля отбрасываются, известные сохраняются",
              r.status_code == 200 and after.get("accent") == "sky" and "garbage" not in after)
        # день итогов по поясу посетителя
        u.post("/api/library/watched", json={"key": "tzkey", "title": "TZ", "watched_ep": 1, "position": 100})
        import calendar
        y = time.gmtime().tm_year
        at = calendar.timegm(time.strptime(f"{y}-06-15 23:30:00", "%Y-%m-%d %H:%M:%S"))
        db = sqlite3.connect(DB)
        db.execute("UPDATE watch_log SET at = ? WHERE key = 'tzkey'", (at,))
        db.commit()
        db.close()
        days0 = u.get("/api/stats/year?tz=0").json()["days"]
        days_il = u.get("/api/stats/year?tz=180").json()["days"]
        check("23:30 UTC — по Гринвичу 15 июня", f"{y}-06-15" in days0, list(days0))
        check("…а по Израилю (UTC+3) уже 16 июня", f"{y}-06-16" in days_il, list(days_il))
        # кэш raw:/anime: — найденное поиском не ищется заново
        if anime.DEMO:
            u.get("/api/search?q=big+buck")
            check("поиск кладёт тайтл в кэш raw:источник:номер",
                  anime.cache_get("raw:demo:bunny") is not None)
        # XSS: названия/описания отдаются как есть в JSON, без HTML
        u.post("/api/library/progress", json={"key": "xss", "title": "<img src=x onerror=alert(1)>"})
        r = u.get("/api/library")
        check("название с разметкой уходит в JSON как текст, тип ответа JSON",
              r.headers["content-type"].startswith("application/json")
              and "<img src=x onerror=alert(1)>" in [i["title"] for i in r.json()["items"]])
        check("нигде в ответах API нет text/html",
              not u.get("/api/me").headers["content-type"].startswith("text/html"))
    finally:
        srv.stop()

    # ==================================================================
    group("Права: каждая ручка main.py от анонима, гостя и пользователя")
    # Уровень берётся у самого приложения (какая зависимость стоит на ручке),
    # так что новая ручка попадёт в проверку сама. Для каждой — живой запрос
    # с минимальным телом; важен только код доступа, не смысл ответа.
    import inspect
    from fastapi.routing import APIRoute
    LEVEL = {"need_any": "все", "need_user": "вошедший", "need_admin": "админ"}
    # /api/auth/demo — вход в демо-аккаунт одной кнопкой (check46_demo_account.py).
    OPEN = {"/api/auth/login", "/api/auth/guest", "/api/auth/demo", "/api/auth/logout", "/api/me",
            "/api/mode", "/api/health", "/", "/{page}"}
    srv = Server()
    try:
        anon = client(srv, "203.0.113.101")
        g = client(srv, "203.0.113.102")
        guest(g)
        u = client(srv, "203.0.113.103")
        login(u, "misha", USER_PW)
        rows = []
        # «Выход» и «выйти везде» — последними: после них у клиента нет сессии.
        ordered = sorted((r for r in main.app.routes if isinstance(r, APIRoute)),
                         key=lambda r: r.path.startswith("/api/auth/logout"))
        for route in ordered:
            deps = [p.default.dependency.__name__ for p in inspect.signature(route.endpoint).parameters.values()
                    if hasattr(p.default, "dependency")]
            level = next((LEVEL[d] for d in deps if d in LEVEL), None)
            if level is None:
                check(f"{route.path}: без охраны — только если в списке открытых",
                      route.path in OPEN, "нет в списке открытых ручек")
                continue
            method = sorted(m for m in route.methods if m != "HEAD")[0]
            path = route.path.replace("{user_id}", "2").replace("{key:path}", "k").replace("{name}", "x.vtt")
            path = path.replace("{page}", "watch")
            kw = {"json": {}} if method in ("POST", "DELETE") else {}
            got = []
            for c in (anon, g, u):
                try:
                    got.append(c.request(method, path, timeout=30, **kw).status_code)
                except httpx.HTTPError as exc:
                    got.append(type(exc).__name__)
            a_, g_, u_ = got
            if level == "все":
                # 404 здесь — «нет такой картинки/субтитров», а не отказ в доступе
                ok = a_ == 401 and g_ not in (401, 403) and u_ not in (401, 403)
            elif level == "вошедший":
                ok = a_ == 401 and g_ == 403 and u_ not in (401, 403, 404)
            else:
                ok = a_ == 404 and g_ == 404 and u_ == 404
            rows.append((method, route.path, level, got))
            check(f"{method} {route.path} [{level}] аноним/гость/пользователь = {got}", ok)
        # особые: аватар и баннер гостю 404 (а не 403 — ему нечего показать)
        check("GET /api/me/avatar гостю → 404", g.get("/api/me/avatar").status_code == 404)
        check("GET /api/me/banner гостю → 404", g.get("/api/me/banner").status_code == 404)
    finally:
        srv.stop()

    print("\n(итог ниже)")


if __name__ == "__main__":
    run()
    print("\n" + "=" * 56)
    if FAILS:
        print(f"ПРОВАЛОВ: {len(FAILS)}")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("Проход 37 пройден.")
