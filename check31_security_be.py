"""Проход 31: аудит безопасности серверной части.

Каждая группа ниже — одна находка аудита, и каждая проверка на прежней
версии кода проваливалась (или не могла быть написана вовсе). Номера
групп — те же, что в отчёте аудита (BE-01 …).

Сеть настоящая только в одном месте: маленький сервер на 127.0.0.1, к
которому сторож исходящих запросов НЕ должен пускать. Имена «из
интернета» подменяются (netguard.RESOLVE) — DNS в проверках не нужен.
"""
import asyncio
import base64
import io
import logging
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["MODE"] = os.environ.get("MODE", "private")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p31.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["UPDATES_EVERY_MIN"] = "0"
os.environ["TRUST_PROXY"] = "1"
os.environ.setdefault("SESSION_PEPPER", "pass31")
logging.getLogger("httpx").setLevel(logging.ERROR)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import httpx                                          # noqa: E402
from fastapi import HTTPException                     # noqa: E402
from fastapi.testclient import TestClient             # noqa: E402
from api import (anime, catalog, helper, hls, mail,  # noqa: E402
                 main, netguard, security, store, subtitles, twofa)
import fake_sources                                 # noqa: E402
fake_sources.install()

logging.getLogger("anime").setLevel(logging.CRITICAL)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PASS = "Audit-Bekend-2026"
FAILS = []
G = [""]


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


class Patch:
    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


class Logs(logging.Handler):
    """Ловит записи журнала — проверить, что тревога поднята."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def reset():
    main.login_guard.reset()
    for lim in (main.api_limit, main.search_limit, main.guest_limit, main.write_limit,
                main.source_limit, main.pass_limit, main.avatar_limit, main.catalog_limit,
                main.hls_limit, main.code_fails):
        lim.reset()


def client(ip="198.51.100.1"):
    c = TestClient(main.app)
    c.headers["X-Real-IP"] = ip
    return c


def login(c, user, pwd=PASS, code=""):
    c.headers.pop("X-CSRF-Token", None)
    r = c.post("/api/auth/login", json={"login": user, "password": pwd, "code": code})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def took(fn, *a):
    t0 = time.perf_counter()
    fn(*a)
    return time.perf_counter() - t0


# ----------------------------------------------------------------------
# Маленький «домашний» сервер на 127.0.0.1: сторож не должен к нему пускать.
# ----------------------------------------------------------------------
HITS = []


class Home(BaseHTTPRequestHandler):
    def do_GET(self):                                  # noqa: N802
        HITS.append(self.path)
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        body = b'{"secret": "router-admin-page"}' if self.path != "/big" else b"[" + b"1," * 600000 + b"1]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def free_port():
    for port in range(8131, 8199):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("нет свободного порта 8131–8198")


async def fetch_status(url, **kw):
    async with httpx.AsyncClient(timeout=5, trust_env=False, **kw) as c:
        r = await c.get(url)
        return r.status_code


def blocked(coro_fn):
    try:
        asyncio.run(coro_fn())
    except (httpx.ConnectError, httpx.RequestError):
        return True
    return False


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "hozyain")
    store.create_user("kod", PASS, "user", "kod")
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Home)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home = f"http://127.0.0.1:{port}"

    # ==================================================================
    group("BE-01. Сторож исходящих запросов: только интернет")
    bad_ips = ["127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.5", "169.254.169.254",
               "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "::1", "fe80::1", "fc00::1",
               "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::"]
    check("закрытые, служебные и «спрятанные в IPv6» адреса не пускаются",
          not any(netguard.ip_ok(x) for x in bad_ips),
          [x for x in bad_ips if netguard.ip_ok(x)])
    check("обычные адреса интернета пускаются",
          netguard.ip_ok("93.184.216.34") and netguard.ip_ok("2606:4700::1111"))
    forms = ["127.1", "2130706433", "0x7f000001", "0177.0.0.1", "0x7f.1"]
    check("127.0.0.1 в любой записи узнаётся",
          all(str(netguard.literal_ip(f)) == "127.0.0.1" for f in forms),
          [(f, str(netguard.literal_ip(f))) for f in forms])
    bad_urls = [f"http://{f}/x" for f in forms] + [
        "http://localhost/", "http://router/", "http://nas.local/", "http://x.internal/",
        "https://example.com:22/", "http://example.com:25/", "ftp://example.com/",
        "file:///etc/passwd", "http://[::1]/", "javascript:alert(1)"]
    check("строкой отсекаются локальные имена, служебные порты и чужие схемы",
          not any(netguard.url_ok(u) for u in bad_urls), [u for u in bad_urls if netguard.url_ok(u)])
    check("обычный адрес CDN проходит строковую проверку",
          netguard.url_ok("https://cdn.example.com:8443/a/seg1.ts"))
    check("subtitles._safe_remote — та же проверка", not subtitles._safe_remote("http://2130706433/a.vtt"))

    HITS.clear()
    check("прямой адрес 127.0.0.1 — соединения нет",
          blocked(lambda: fetch_status(home + "/a")))
    check("127.0.0.1 в десятичной записи — соединения нет",
          blocked(lambda: fetch_status(f"http://2130706433:{port}/b")))

    async def evil_dns(host, p):
        return ["127.0.0.1"]

    with Patch(netguard, "RESOLVE", [evil_dns]):
        check("имя, которое строкой — «сайт», проходит строковую проверку",
              netguard.url_ok(f"http://rebind.example.com:{port}/c"))
        check("…но ведёт на 127.0.0.1 — соединения нет (DNS rebinding)",
              blocked(lambda: fetch_status(f"http://rebind.example.com:{port}/c")))

    async def mixed_dns(host, p):
        return ["93.184.216.34", "127.0.0.1"]

    with Patch(netguard, "RESOLVE", [mixed_dns]):
        check("ответ DNS с внутренним адресом рядом с настоящим — не пускаем",
              blocked(lambda: fetch_status(f"http://mixed.example.com:{port}/d")))
    real_ip_ok = netguard.ip_ok
    with Patch(netguard, "ip_ok", lambda ip: str(ip) == "127.0.0.1" or real_ip_ok(ip)):
        check("переадресация во внутреннюю сеть проверяется на своём шаге",
              blocked(lambda: fetch_status(home + "/redirect", follow_redirects=True)))
    try:
        with httpx.Client(timeout=5, trust_env=False) as sc:
            sc.get(home + "/sync")
        ok = False
    except httpx.ConnectError:
        ok = True
    check("синхронный клиент httpx тоже под сторожем", ok)
    check("сервер в домашней сети не получил ни одного запроса",
          HITS == ["/redirect"], HITS)

    with netguard.allow_private():
        status = asyncio.run(fetch_status(home + "/ok"))
    check("помощник (allow_private) в домашнюю сеть ходить может", status == 200, status)

    # Проверка для разборщиков на requests (их соединения сторож httpx не
    # видит): адрес проверяется прямо перед запросом.
    HITS.clear()
    try:
        netguard.check_url_sync(home + "/sync-lib")
        ok = False
    except netguard.Blocked:
        ok = True
    check("проверка для клиентов на requests: 127.0.0.1 не пускается", ok and not HITS, HITS)

    def evil_dns_sync(host, p):
        return ["127.0.0.1"]

    with Patch(netguard, "RESOLVE_SYNC", [evil_dns_sync]):
        try:
            netguard.check_url_sync(f"http://evil.example.com:{port}/sync-lib2")
            ok = False
        except netguard.Blocked:
            ok = True
    check("…и имя, ведущее внутрь, тоже", ok and not HITS, HITS)

    with netguard.allow_private():
        got = asyncio.run(catalog._get(home + "/big"))
    check("ответ больше потолка справочник не читает целиком",
          got is None or catalog.MAX_RESPONSE > 1_200_000, type(got).__name__)

    async def capped():
        def handler(request):
            return httpx.Response(200, content=b"x" * 5000)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            async with c.stream("GET", "https://t.example/") as r:
                return await netguard.read_capped(r, 1000)
    try:
        asyncio.run(capped())
        check("read_capped обрывает длинный ответ", False)
    except netguard.TooBig:
        check("read_capped обрывает длинный ответ", True)
    # ==================================================================
    group("BE-02. Прокладка видео: чужой тип ответа не исполняется у нас")
    check("SVG, XHTML, HTML, скрипт — только как «байты»",
          all(hls.safe_media_type(t) == "application/octet-stream" for t in (
              "image/svg+xml", "application/xhtml+xml", "text/html; charset=utf-8",
              "application/javascript", "text/javascript", "application/json", "", "x/y")))
    check("видео, звук, плейлист и картинки-маскировки — как есть",
          [hls.safe_media_type(t) for t in ("video/mp2t", "audio/aac", "image/jpeg",
                                            "application/vnd.apple.mpegurl")]
          == ["video/mp2t", "audio/aac", "image/jpeg", "application/vnd.apple.mpegurl"])

    replies = {}

    def handler(request):
        kind = replies.get(request.url.path, ("video/mp2t", b"\x47" * 100))
        return httpx.Response(200, content=kind[1], headers={"content-type": kind[0]})

    def fake_client(referer):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    replies["/evil.svg"] = ("image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    replies["/evil.js"] = ("application/javascript", b"fetch('/api/me')")
    replies["/seg.ts"] = ("video/mp2t", b"\x47" * 188)
    big = "#EXTM3U\n" + "".join(f"s{i}.ts\n" for i in range(hls.MAX_PLAYLIST_ENTRIES + 5))
    replies["/huge.m3u8"] = ("application/vnd.apple.mpegurl", big.encode())
    replies["/inner.m3u8"] = ("application/vnd.apple.mpegurl",
                              b"#EXTM3U\n#EXTINF:4,\nhttp://192.168.1.1/admin\n#EXTINF:4,\nok.ts\n")
    reset()
    c = client()
    check("вход", login(c, "hozyain").status_code == 200)
    with Patch(hls, "client", fake_client):
        for name, want in (("evil.svg", "application/octet-stream"),
                           ("evil.js", "application/octet-stream"),
                           ("seg.ts", "video/mp2t")):
            r = c.get(hls.proxied("https://cdn.example.com/" + name, "https://player.example.com/"))
            check(f"{name}: тип ответа {want}",
                  r.status_code == 200 and r.headers["content-type"].startswith(want),
                  (r.status_code, r.headers.get("content-type")))
            check(f"{name}: своя строгая политика (sandbox)",
                  "sandbox" in r.headers.get("content-security-policy", ""),
                  r.headers.get("content-security-policy"))
        r = c.get(hls.proxied("https://cdn.example.com/huge.m3u8", "https://player.example.com/"))
        check("BE-04. плейлист с десятками тысяч адресов — 502, а не сотни мегабайт",
              r.status_code == 502, (r.status_code, len(r.content)))
        r = c.get(hls.proxied("https://cdn.example.com/inner.m3u8", "https://player.example.com/"))
        lines = r.text.split("\n")
        check("адрес домашней сети в плейлисте не подписывается",
              r.status_code == 200 and "192.168.1.1" not in r.text
              and sum(1 for x in lines if x.startswith("/api/hls?")) == 1, r.text[:300])
    old_link = hls.proxied("https://cdn.example.com/seg.ts", "https://player.example.com/")
    q = dict(x.split("=", 1) for x in old_link.split("?", 1)[1].split("&"))
    stale = hls._sign(q["u"], q["r"], now=time.time() - hls.LINK_TTL - 60)
    check("BE-19. подпись просрочена — ссылка не работает", hls.unpack(q["u"], q["r"], stale) is None)
    until, mac = q["s"].split(".")
    check("BE-19. срок в подписи не продлить, поменяв число",
          hls.unpack(q["u"], q["r"], f"{int(until) + 86400}.{mac}") is None)
    check("свежая подпись работает", hls.unpack(q["u"], q["r"], q["s"]) is not None)

    reg = subtitles.register([{"label": "English", "lang": "en", "url": "https://s.example.com/a.vtt"}])

    async def fake_vtt(meta):
        return "WEBVTT\n\n00:00.000 --> 00:01.000\n<b>hi</b>\n"

    with Patch(subtitles, "fetch_vtt", fake_vtt):
        r = c.get(reg[0]["src"])
    check("файл субтитров — тоже со строгой политикой",
          r.status_code == 200 and "sandbox" in r.headers.get("content-security-policy", ""),
          r.headers.get("content-security-policy"))
    r = c.get("/api/library")
    check("обычные ответы — с общей политикой сайта",
          r.headers.get("content-security-policy") == main.CSP)

    group("BE-15. Потолок одновременных потоков прокладки")
    main._hls_open.clear()
    slots = [main._Slot("u:999999") for _ in range(main.HLS_PER_CALLER)]
    check("потолок считается", main._hls_busy("u:999999") == main.HLS_PER_CALLER)
    with Patch(hls, "client", fake_client):
        r = c.get(hls.proxied("https://cdn.example.com/seg.ts", "https://player.example.com/"))
    vid = store.get_user_by_login("hozyain")["id"]
    check("свой поток при занятых местах другого — открывается", r.status_code == 200
          and main._hls_busy(f"u:{vid}") == 0, (r.status_code, main._hls_open))
    mine = [main._Slot(f"u:{vid}") for _ in range(main.HLS_PER_CALLER)]
    with Patch(hls, "client", fake_client):
        r = c.get(hls.proxied("https://cdn.example.com/seg.ts", "https://player.example.com/"))
    check("свои места заняты — 429", r.status_code == 429, r.status_code)
    for s_ in mine:
        s_.release()
    for s_ in slots:
        s_.release()
        s_.release()                                   # повторно — без вреда
    check("места освобождаются (и повторное освобождение безвредно)", main._hls_busy() == 0)
    stuck = main._Slot("u:2")
    main._hls_open["u:2"][stuck.id] = time.time() - main.HLS_SLOT_MAX_AGE - 1
    check("забытое место освобождается само по сроку", main._hls_busy("u:2") == 0)

    # ==================================================================
    group("BE-03. Разбор чужих строк (справочник, названия): без «вечных» регулярок")
    check("описание из справочника: тысячи меток без пары — быстро",
          took(catalog.clean_description, "[spoiler]" * 20000 + "[b]" * 20000) < 1.0)
    check("название из справочника: тысяча пробелов — быстро",
          took(catalog._base_name, "a" + " " * 3000 + "!") < 0.5)
    check("сравнение названий: «tv» и тысячи пробелов — быстро",
          took(anime.relevance, "наруто", "tv" + " " * 20000 + "x") < 0.5)
    check("сравнение названий для настоящих строк не изменилось",
          anime.relevance("Наруто", "Наруто  /  Naruto [1-220 из 220]") == 1.0
          and anime.relevance("наруто", "Наруто Ураганные хроники") < 0.9)
    check("ответ помощника режется до разбора",
          took(helper.clean_reply, "a." * 500000 + "com", False) < 0.5)

    # ==================================================================
    group("BE-05. SESSION_PEPPER: образец и короткие строки")
    check("образец из .env.example — плохо",
          security.pepper_problem("замените-на-длинную-случайную-строку") is not None)
    check("пусто и коротко — плохо",
          security.pepper_problem("") is not None and security.pepper_problem("abc123") is not None)
    check("openssl rand -hex 32 — годится",
          security.pepper_problem("9f2c4e1a7b3d5f6e8a0c2b4d6f8e1a3c5b7d9f0e2a4c6b8d0f1e3a5c7b9d1f3e") is None)

    async def start_with(pepper, secure):
        with Patch(main, "COOKIE_SECURE", secure):
            old = os.environ.get("SESSION_PEPPER")
            os.environ["SESSION_PEPPER"] = pepper
            try:
                async with main.lifespan(main.app):
                    return "запущен"
            except RuntimeError as exc:
                return "отказ: " + str(exc)
            finally:
                os.environ["SESSION_PEPPER"] = old
    got = asyncio.run(start_with("замените-на-длинную-случайную-строку", True))
    check("рабочий сайт (COOKIE_SECURE=1) с образцом не запускается", got.startswith("отказ"), got)
    got = asyncio.run(start_with("9f2c4e1a7b3d5f6e8a0c2b4d6f8e1a3c", True))
    check("…а с настоящей строкой запускается", got == "запущен", got)

    # ==================================================================
    group("BE-06. Секрет входа по коду в базе — зашифрован")
    sealed = security.seal("JBSWY3DPEHPK3PXP")
    check("зашифрованное не похоже на исходное",
          sealed.startswith("s1:") and "JBSWY3DPEHPK3PXP" not in sealed)
    check("и расшифровывается обратно", security.unseal(sealed) == "JBSWY3DPEHPK3PXP")
    check("одно и то же шифруется каждый раз по-разному", security.seal("abc") != security.seal("abc"))
    raw = base64.urlsafe_b64decode(sealed[3:])
    forged = "s1:" + base64.urlsafe_b64encode(raw[:20] + bytes([raw[20] ^ 1]) + raw[21:]).decode()
    try:
        security.unseal(forged)
        check("подправленная запись не расшифровывается", False)
    except security.Unsealable:
        check("подправленная запись не расшифровывается", True)
    os.environ["SESSION_PEPPER"], keep = "совсем-другая-строка-сервера-0123", os.environ["SESSION_PEPPER"]
    try:
        security.unseal(sealed)
        check("с другой SESSION_PEPPER не расшифровывается", False)
    except security.Unsealable:
        check("с другой SESSION_PEPPER не расшифровывается", True)
    finally:
        os.environ["SESSION_PEPPER"] = keep
    check("старая запись открытым текстом читается как есть", security.unseal("ABCDEF") == "ABCDEF")

    reset()
    k = client("203.0.113.10")
    check("вход kod", login(k, "kod").status_code == 200)
    r = k.post("/api/me/2fa/start", json={"password": PASS})
    secret = r.json().get("secret", "")
    code = twofa._code_at(secret, int(time.time() // twofa.STEP))
    r = k.post("/api/me/2fa/enable", json={"code": code})
    backup = r.json().get("backup") or []
    check("вход по коду включён", r.status_code == 200 and len(backup) >= 4, r.status_code)
    row = store.get_user_by_login("kod")
    check("в базе — шифр, а не секрет", row["totp_secret"].startswith("s1:")
          and secret not in row["totp_secret"])
    reset()
    k = client("203.0.113.10")
    r = login(k, "kod", code=twofa._code_at(secret, int(time.time() // twofa.STEP) + 1))
    check("вход с кодом из приложения работает (секрет расшифровался)", r.status_code == 200, r.text[:120])

    conn = store.connect()
    conn.execute("UPDATE users SET totp_secret = ? WHERE login = 'kod'", (secret,))
    check("старая запись открытым текстом шифруется при запуске",
          store.seal_plain_secrets() == 1
          and store.totp_secret(store.get_user_by_login("kod")) == secret)

    # ==================================================================
    group("BE-08. Перебор кода тем, кто уже знает пароль")
    watcher = Logs()
    logging.getLogger("anime").addHandler(watcher)
    logging.getLogger("anime").setLevel(logging.WARNING)
    reset()
    known = client("203.0.113.10")
    check("вход с «домашнего» адреса (запоминается)", login(known, "kod", code=backup[0]).status_code == 200)
    statuses = []
    for i in range(main.CODE_FAILS_MAX):
        statuses.append(login(client(f"198.51.100.{20 + i}"), "kod", code="000000").status_code)
    check("пять неверных кодов с пяти разных адресов — пять отказов", statuses == [401] * 5, statuses)
    r = login(client("198.51.100.99"), "kod", code=backup[1])
    check("шестая попытка с нового адреса — 429, даже с верным кодом", r.status_code == 429, r.status_code)
    check("в журнале — тревога «пароль знает посторонний»",
          any("пароль знает посторонний" in x for x in watcher.lines), watcher.lines[-3:])
    r = login(client("203.0.113.10"), "kod", code=backup[1])
    check("владелец со своего адреса входит как обычно", r.status_code == 200, r.status_code)
    logging.getLogger("anime").removeHandler(watcher)
    logging.getLogger("anime").setLevel(logging.CRITICAL)

    # ==================================================================
    group("BE-07. Запасной код — ровно один раз, даже при одновременных входах")
    uid = store.get_user_by_login("kod")["id"]
    wins = []
    for attempt in range(15):
        codes = twofa.new_backup_codes()
        store.connect().execute("UPDATE users SET backup_codes = ? WHERE id = ?",
                                (",".join(twofa.hash_backup(x) for x in codes), uid))
        gate = threading.Barrier(4)
        got = []

        def spend():
            gate.wait()
            got.append(store.use_backup_code(uid, twofa.hash_backup(codes[0])))
        threads = [threading.Thread(target=spend) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wins.append(sum(got))
    check("из четырёх одновременных попыток срабатывает одна (×15)", set(wins) == {1}, wins)

    # ==================================================================
    group("BE-09. Подсчёт паролей: не больше двух разом, очередь с потолком")

    async def concurrency():
        loop = asyncio.get_running_loop()
        main._hash_gate.update(loop=None)
        now_, top = [0], [0]
        lock = threading.Lock()

        def work(*_):
            with lock:
                now_[0] += 1
                top[0] = max(top[0], now_[0])
            time.sleep(0.05)
            with lock:
                now_[0] -= 1
        await asyncio.gather(*[main._hashing(work) for _ in range(8)])
        main._hash_gate.update(loop=loop, busy=main.HASH_QUEUE_MAX)
        try:
            await main.verify_password("x", "y")
            status = 200
        except HTTPException as exc:
            status = exc.status_code
        main._hash_gate.update(busy=0)
        return top[0], status
    top, status = asyncio.run(concurrency())
    check(f"разом считается не больше {main.HASH_PARALLEL}", top <= main.HASH_PARALLEL, top)
    check("очередь переполнена — 503, а не бесконечное ожидание", status == 503, status)

    # ==================================================================
    group("BE-12. Вход заменяет прежнюю сессию браузера")
    reset()
    v = client()
    login(v, "hozyain")
    first = v.cookies.get("sid")
    login(v, "hozyain")
    check("новый вход — новый токен", v.cookies.get("sid") != first)
    check("а прежний больше не пускает", store.session_user(first) is None)
    g = client("198.51.100.77")
    r = g.post("/api/auth/guest", json={})
    guest_token = g.cookies.get("sid")
    check("гостевой пропуск выдан", r.status_code == 200 and main.guests.get(guest_token) is not None)
    login(g, "hozyain")
    check("вход из-под гостя закрывает гостевой пропуск", main.guests.get(guest_token) is None)

    # ==================================================================
    group("BE-16. Картинки: только три формата, метаданные не сохраняются")
    from PIL import Image
    reset()
    v = client()
    login(v, "hozyain")
    img = Image.new("RGB", (300, 300), (200, 30, 30))
    exif = Image.Exif()
    exif[0x010E] = "GPS-SECRET-home-address"
    exif[0x013B] = "Ivan Ivanov"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    check("в присланном файле метаданные есть", b"GPS-SECRET" in buf.getvalue())
    r = v.post("/api/me/avatar", content=buf.getvalue())
    stored = store.get_avatar(store.get_user_by_login("hozyain")["id"]) or b""
    check("аватар принят", r.status_code == 200, r.text[:100])
    check("в сохранённом — ни метаданных, ни имени автора",
          b"GPS-SECRET" not in stored and b"Ivan" not in stored
          and not Image.open(io.BytesIO(stored)).info.get("exif"))
    for fmt in ("TIFF", "GIF", "BMP", "ICO"):
        b2 = io.BytesIO()
        Image.new("RGB", (64, 64)).save(b2, fmt)
        reset()
        check(f"{fmt} не принимается", v.post("/api/me/avatar", content=b2.getvalue()).status_code == 400)
    eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nshowpage\n"
    reset()
    check("EPS (разбор через Ghostscript) не принимается",
          v.post("/api/me/avatar", content=eps).status_code == 400)
    check("в /api/me — признак аватара без чтения картинки",
          v.get("/api/me").json().get("has_avatar") is True)

    # ==================================================================
    group("BE-17. Запрос: потолок тела, ответ 422 без эха, заголовок Range")
    reset()
    r = v.post("/api/helper/ask", content=b"x" * (main.BODY_MAX + 10),
               headers={"content-type": "application/json"})
    check("заявленный размер больше мегабайта — 413", r.status_code == 413, r.status_code)

    def chunks():
        for _ in range(40):
            yield b" " * 65536
    r = v.post("/api/me/settings", content=chunks(), headers={"content-type": "application/json"})
    check("без заявленного размера (по кускам) — тоже 413", r.status_code == 413, r.status_code)
    evil_range = "bytes=" + ",".join(f"{i}-{i}" for i in range(0, 20000, 2))
    t0 = time.perf_counter()
    r = TestClient(main.app).get("/static/hls.min.js", headers={"Range": evil_range})
    check("Range из тысяч кусков к статике — быстро и файл целиком (без входа!)",
          r.status_code == 200 and time.perf_counter() - t0 < 1.0,
          (r.status_code, round(time.perf_counter() - t0, 2)))
    r = TestClient(main.app).get("/static/hls.min.js", headers={"Range": "bytes=0-99"})
    check("обычный Range работает как раньше", r.status_code == 206 and len(r.content) == 100)
    long_pass = "Секретный-пароль-" + "x" * 250
    r = client().post("/api/auth/login", json={"login": "hozyain", "password": long_pass})
    check("ответ 422 не возвращает присланное значение",
          r.status_code == 422 and "Секретный" not in r.text and "input" not in r.text, r.text[:200])

    # ==================================================================
    group("BE-14. Прогрев серии: задач не больше потолка")

    async def many_warm():
        for _ in range(30):
            main.spawn_warm(lambda: asyncio.sleep(5))
        n = len(main._warm_tasks)
        for t in list(main._warm_tasks):
            t.cancel()
        await asyncio.sleep(0)
        return n
    n = asyncio.run(many_warm())
    check(f"из 30 прогревов в работе не больше {main.WARM_PENDING_MAX}",
          n <= main.WARM_PENDING_MAX, n)

    # ==================================================================
    group("BE-20, BE-21. Почта и мелочи: адрес почты, номер в адресе")
    reset()
    a = client()
    login(a, "hozyain")
    r = a.post("/api/admin/users/123456789012345678901234567890/disable")
    check("огромный номер пользователя — 422, а не внутренняя ошибка", r.status_code == 422, r.status_code)
    r = a.post("/api/me/mail", json={"email": "a@b.ru\r\nBcc: x@y.z", "want": False})
    check("кривой адрес почты не сохраняется и без согласия", r.status_code == 400, r.status_code)
    r = a.post("/api/me/mail", json={"email": "", "want": False})
    check("пустой адрес без согласия — можно", r.status_code == 200, r.status_code)

    sent = {}

    class FakeSSL:
        def __init__(self, host, port, timeout=None, context=None):
            sent["ssl"] = (host, port, context is not None)

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def starttls(self, **k):
            sent["starttls"] = True

        def login(self, u, p):
            sent["login"] = u

        def send_message(self, msg):
            sent["msg"] = msg["To"]

    with Patch(mail, "SMTP_HOST", "smtp.example.com"), Patch(mail, "SMTP_PORT", 465), \
            Patch(mail, "SMTP_FROM", "robot@example.com"), Patch(mail, "SMTP_USER", "robot"), \
            Patch(mail.smtplib, "SMTP_SSL", FakeSSL):
        ok = mail.send_episode("hozyain@example.com", display_name="V", title="T", episode=2)
    check("порт 465 — шифрование с первого байта (SMTP_SSL)",
          ok and sent.get("ssl", (0, 0, 0))[1] == 465 and "starttls" not in sent, sent)

    print("\n" + "=" * 60)
    server.shutdown()
    if FAILS:
        print(f"ПРОХОД 31: НЕ ПРОШЛИ {len(FAILS)}")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("ПРОХОД 31 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
