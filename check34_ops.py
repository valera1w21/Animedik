"""Проверка настроек запуска: Docker, nginx, зависимости.

    python check34_ops.py

Код сайта здесь не запускается — только читаются файлы рядом с ним.
Смысл: всё, что починено при разборе обслуживания (аудит), не
должно тихо вернуться обратно при следующей правке. Каждая проверка
называет, что сломалось и где.

Зависимостей нет — только стандартная библиотека. Если на машине есть
nginx, он тоже прогоняется; нет — эта проверка пропускается с пометкой.
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []


def group(name):
    print("\n== " + name + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(name)
    return ok


def skip(name, why):
    print("  ---  " + name + " (пропущено: " + why + ")")


def read(path):
    """Содержимое файла; нет файла — провал с именем, а не падение набора."""
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        check("файл на месте: " + path, False)
        return ""


def code_only(text, comment="#"):
    """Текст без комментариев: проверяем то, что работает, а не пояснения."""
    return "\n".join(line.split(comment, 1)[0] for line in text.splitlines())


compose = read("docker-compose.yml")
compose_code = code_only(compose)
dockerfile = read("Dockerfile")
docker_code = code_only(dockerfile)
env_example = read(".env.example")
nginx_site = read("deploy/nginx-anime.conf")
nginx_limits = read("deploy/nginx-limits.conf")
site_code = code_only(nginx_site)
limits_code = code_only(nginx_limits)

# ==========================================================================
group("docker-compose.yml: защита контейнера")
# ==========================================================================
check("файловая система только для чтения (read_only: true)",
      re.search(r"^\s+read_only:\s*true\s*$", compose_code, re.M) is not None)
check("/tmp — в памяти и без запуска программ (tmpfs, noexec)",
      re.search(r"^\s+-\s*/tmp:[^\n]*noexec", compose_code, re.M) is not None)
check("запрет повышать права (no-new-privileges)",
      "no-new-privileges:true" in compose_code)
check("все особые права ядра сняты (cap_drop: ALL)",
      re.search(r"cap_drop:\s*\n\s*-\s*ALL\b", compose_code) is not None)
check("потолок памяти (mem_limit)", re.search(r"^\s+mem_limit:\s*\S+", compose_code, re.M) is not None)
check("потолок числа процессов (pids_limit)",
      re.search(r"^\s+pids_limit:\s*\d+", compose_code, re.M) is not None)
check("журнал с ротацией (max-size и max-file)",
      "max-size" in compose_code and "max-file" in compose_code)
check("сайт поднимается сам после сбоя (restart)",
      re.search(r"^\s+restart:\s*(unless-stopped|always)\s*$", compose_code, re.M) is not None)
ports = re.findall(r"^\s+-\s*\"?([^\"\n]*:\d+)\"?\s*$",
                   compose_code.split("ports:", 1)[1].split("restart:", 1)[0], re.M) \
    if "ports:" in compose_code else []
check("порты открыты только на 127.0.0.1", ports and all(p.startswith("127.0.0.1:") for p in ports), ports)
check("SHOW_DOCS в контейнер не передаётся (схема API закрыта)", "SHOW_DOCS:" not in compose_code)
check("TRUST_PROXY включён (адрес посетителя — от nginx)", 'TRUST_PROXY: "1"' in compose_code)

# ==========================================================================
group("Переменные окружения: всё, что читает приложение, доходит из .env")
# ==========================================================================
api_src = {p: read(p) for p in glob.glob("api/*.py")}
used = {}
for path, src in api_src.items():
    for m in re.finditer(r"os\.(?:getenv|environ\.get)\(\s*\"([A-Z0-9_]+)\"(?:\s*,\s*\"([^\"]*)\")?", src):
        used.setdefault(m.group(1), set()).add(m.group(2))
    for m in re.finditer(r"os\.environ\[\s*\"([A-Z0-9_]+)\"\s*\]", src):
        used.setdefault(m.group(1), set()).add(None)
env_block = compose_code.split("environment:", 1)[1].split("volumes:", 1)[0] if "environment:" in compose_code else ""
passed = dict(re.findall(r"^\s+([A-Z][A-Z0-9_]*):\s*(.*?)\s*$", env_block, re.M))
docker_env = set(re.findall(r"\b([A-Z][A-Z0-9_]*)=", " ".join(
    line for line in docker_code.splitlines() if line.strip().startswith("ENV") or line.startswith(" "))))
# Задаются в образе (Dockerfile ENV) — не настройка владельца.
FROM_IMAGE = {"DB_PATH", "WEB_DIR"}
# Не передаются намеренно (см. комментарии в docker-compose.yml).
NOT_PASSED = {"SHOW_DOCS"}
check("приложение читает переменные (нашлись)", len(used) >= 15, sorted(used))
missing = sorted(v for v in used if v not in passed and v not in FROM_IMAGE | NOT_PASSED)
check("каждая переменная приложения передана в контейнер (environment)", not missing, missing)
check("переменные из образа действительно заданы в Dockerfile",
      FROM_IMAGE <= docker_env, FROM_IMAGE - docker_env)
undocumented = sorted(v for v in passed if v != "TRUST_PROXY"
                      and not re.search(r"^#?\s*" + v + r"=", env_example, re.M))
check("каждая переданная переменная описана в .env.example", not undocumented, undocumented)
unknown = sorted(v for v in passed if v not in used)
check("в environment нет лишних (неизвестных приложению) переменных", not unknown, unknown)
# Пустое значение там, где у приложения есть своё по умолчанию, ломало бы
# его (адрес источника стал бы пустым). Поэтому значение по умолчанию в
# compose обязано совпадать с тем, что в коде.
bad_defaults = []
for var, defaults in used.items():
    real = {d for d in defaults if d}
    if not real or var not in passed:
        continue
    m = re.match(r"\"\$\{" + var + r":-([^}]*)\}\"", passed[var])
    if not m or m.group(1) not in real:
        bad_defaults.append((var, passed[var], sorted(real)))
check("значения по умолчанию в compose совпадают с кодом", not bad_defaults, bad_defaults)
check("SESSION_PEPPER обязателен (без него compose не запустится)",
      re.search(r"SESSION_PEPPER:\s*\"\$\{SESSION_PEPPER:\?", compose_code) is not None)

# ==========================================================================
group("Dockerfile")
# ==========================================================================
base = re.search(r"^FROM\s+(\S+)", docker_code, re.M)
check("базовый образ закреплён до точной версии (python:X.Y.Z-slim-…)",
      base and re.fullmatch(r"python:\d+\.\d+\.\d+-slim-[a-z]+(@sha256:[0-9a-f]{64})?", base.group(1)),
      base and base.group(1))
check("сайт работает не от root (USER app)", re.search(r"^USER\s+app\s*$", docker_code, re.M) is not None)
check("номер пользователя закреплён (uid 1000 — как у файлов в томе)", "--uid 1000" in docker_code)
check("код сайта не во владении пользователя сайта (нет chown /app)",
      re.search(r"chown[^\n]*\s/app\b", docker_code) is None)
check("HEALTHCHECK есть и спрашивает /api/health",
      re.search(r"^HEALTHCHECK\b[\s\S]*?/api/health", docker_code, re.M) is not None)
check("pip ставит только готовые сборки (--only-binary)", docker_code.count("--only-binary=:all:") >= 2)
check("pip ставит с точными версиями вложенных библиотек (-c constraints.txt)",
      docker_code.count("-c constraints.txt") >= 2 and "constraints.txt" in docker_code.split("RUN", 1)[0])
check("uvicorn не разбирает X-Forwarded-For сам (--no-proxy-headers)", "--no-proxy-headers" in docker_code)
check("один рабочий процесс (--workers 1)", "--workers 1" in docker_code)
check("exec перед uvicorn (сигнал остановки доходит)", "exec uvicorn" in docker_code)
check("PYTHONDONTWRITEBYTECODE", "PYTHONDONTWRITEBYTECODE=1" in docker_code)
health_route = any(re.search(r"@app\.get\(\"/api/health\"\)", s) for s in api_src.values())
check("ручка /api/health существует в приложении", health_route)

dockerignore = read(".dockerignore")
di_lines = [line.strip() for line in dockerignore.splitlines() if line.strip() and not line.startswith("#")]
check(".dockerignore — список разрешённого (первая строка «*»)", di_lines[:1] == ["*"], di_lines[:1])
check(".dockerignore не пускает .env и базы даже из api/ и web/",
      "**/.env*" in di_lines and "**/*.db" in di_lines)
copies = re.findall(r"^COPY\s+(.+?)\s+\S+\s*$", docker_code, re.M)
allowed = [line[1:].rstrip("/") for line in di_lines if line.startswith("!")]
for c in copies:
    for src in c.split():
        check("COPY %s разрешён в .dockerignore" % src,
              any(src == a or src.startswith(a + "/") for a in allowed), allowed)

# ==========================================================================
group("Зависимости: точные версии")
# ==========================================================================


def pins(path):
    out = {}
    for raw in read(path).splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        spec = line.split(";", 1)[0].strip()
        m = re.fullmatch(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([0-9][0-9A-Za-z.\-+]*)", spec)
        out[spec] = m and (re.sub(r"[-_.]+", "-", m.group(1)).lower(), m.group(3))
    return out


cons = pins("api/constraints.txt")
cons_map = dict(v for v in cons.values() if v)
for req in ("api/requirements.txt", "api/requirements-private.txt", "api/constraints.txt"):
    p = pins(req)
    loose = [k for k, v in p.items() if not v]
    check(req + ": каждая строка закреплена «==»", not loose, loose)
    if req != "api/constraints.txt":
        mismatch = [(n, v, cons_map.get(n)) for n, v in (x for x in p.values() if x) if cons_map.get(n) != v]
        check(req + ": версии совпадают с constraints.txt", not mismatch, mismatch)


def ver(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


check("starlette не ниже 1.3.1 (CVE-2025-54121, -62727, CVE-2026-48710, -48817, -54282, -54283)",
      ver(cons_map.get("starlette", "0")) >= (1, 3, 1), cons_map.get("starlette"))
check("pillow не ниже 13.0.0 (≈25 CVE в 11.1.0; 39 в 12.3.0, в т. ч. CVE-2026-112399 — APNG занимал "
      "гигабайты памяти ещё внутри Image.open)", ver(cons_map.get("pillow", "0")) >= (13, 0, 0),
      cons_map.get("pillow"))
check("certifi не ниже 2026.10.19 (GHSA-6q48-6c7w-9gw2: корень emSign с украденными ключами)",
      ver(cons_map.get("certifi", "0")) >= (2026, 10, 19), cons_map.get("certifi"))
check("h11 не ниже 0.16.0 (CVE-2025-43859, подмена запросов)", ver(cons_map.get("h11", "0")) >= (0, 16, 0),
      cons_map.get("h11"))
check("uvicorn без [standard] (websockets/watchfiles сайту не нужны)",
      "uvicorn[" not in code_only(read("api/requirements.txt")))

# ==========================================================================
group("nginx")
# ==========================================================================
check("nginx слушает только 127.0.0.1", re.search(r"^\s*listen\s+127\.0\.0\.1:80\b", site_code, re.M) is not None)
check("server_tokens off (версия nginx не видна)", re.search(r"server_tokens\s+off;", site_code) is not None)
check("адрес посетителя — из CF-Connecting-IP только от localhost",
      "real_ip_header   CF-Connecting-IP;" in site_code or re.search(r"real_ip_header\s+CF-Connecting-IP;", site_code)
      and re.search(r"set_real_ip_from\s+127\.0\.0\.1;", site_code))
check("proxy_params не подключается (с ним заголовки уходили бы дважды)", "proxy_params" not in site_code)
for hdr, val in (("X-Real-IP", r"\$remote_addr"), ("X-Forwarded-For", r"\$remote_addr"),
                 ("Host", r"\$host"), ("Range", r"\$animedik_range")):
    check("proxy_set_header %s задан начисто" % hdr,
          re.search(r"proxy_set_header\s+" + hdr + r"\s+" + val + r";", site_code) is not None)
# proxy_set_header / add_header / proxy_hide_header внутри location отменяют
# всё, что задано на уровне server, — адрес снова стал бы подделываемым.
location_bodies = []
depth, cur = 0, None
for line in site_code.splitlines():
    if re.match(r"\s*location\b", line) and cur is None:
        cur, depth = [], 0
    if cur is not None:
        cur.append(line)
        depth += line.count("{") - line.count("}")
        if depth <= 0 and "}" in line:
            location_bodies.append("\n".join(cur))
            cur = None
check("в location нет proxy_set_header (не отменяет заголовки уровня server)",
      not any("proxy_set_header" in b for b in location_bodies))
proxied = [b for b in location_bodies if "proxy_pass" in b]
check("в проксирующих location нет add_header/proxy_hide_header",
      proxied and not any(("add_header" in b or "proxy_hide_header" in b) for b in proxied), len(proxied))
check("nosniff ровно один раз (свой прячем, ставим сами)",
      "proxy_hide_header X-Content-Type-Options;" in site_code)
cmb = re.search(r"client_max_body_size\s+(\d+)([km]?);", site_code, re.I)
size = cmb and int(cmb.group(1)) * {"": 1, "k": 1024, "m": 1024 ** 2}[cmb.group(2).lower()]
banner = max(int(a) * 1024 for a in re.findall(r"(?:AVATAR|BANNER)_MAX_BYTES\s*=\s*(\d+)\s*\*\s*1024",
                                                 api_src.get(os.path.join("api", "main.py"), "")) or [0])
check("client_max_body_size не больше 1 МБ и не меньше лимита баннера",
      size and banner and banner < size <= 1024 ** 2, (size, banner))
check("large_client_header_buffers задан", "large_client_header_buffers" in site_code)
check("только GET/HEAD/POST/DELETE", re.search(r"\$request_method\s*!~\s*\^\(GET\|HEAD\|POST\|DELETE\)\$", site_code))
check("подозрительный Host получает 400", "$animedik_host_ok" in site_code and "$animedik_host_ok" in limits_code)
check("скрытые файлы закрыты", re.search(r"location\s+~\s+/\\\.\s*\{", site_code) is not None)
check("вход ограничен жёстко (zone=anime_login)", re.search(r"location\s+/api/auth/[\s\S]*?zone=anime_login", site_code))
check("видео — своя зона и без буферизации на диск",
      re.search(r"location\s+/api/hls\s*\{[^}]*zone=anime_hls[^}]*proxy_buffering\s+off;", site_code))
zones_used = set(re.findall(r"zone=(\w+)", site_code)) | set(re.findall(r"limit_conn\s+(\w+)", site_code))
zones_defined = set(re.findall(r"zone=(\w+):", limits_code))
check("все зоны лимитов объявлены в nginx-limits.conf", zones_used <= zones_defined, zones_used - zones_defined)
check("свои ошибки nginx — internal", re.search(r"location\s+\^~\s+/__animedik_err/\s*\{\s*internal;", site_code))

nginx = shutil.which("nginx")
if nginx:
    with tempfile.TemporaryDirectory() as tmp:
        site = re.sub(r"listen 127\.0\.0\.1:80 default_server;", "listen 127.0.0.1:65080 default_server;", nginx_site)
        open(os.path.join(tmp, "site.conf"), "w", encoding="utf-8").write(site)
        open(os.path.join(tmp, "limits.conf"), "w", encoding="utf-8").write(nginx_limits)
        mime = "/etc/nginx/mime.types"
        conf = ("pid %s/n.pid; error_log %s/e.log; events {} http { %s access_log off; "
                "client_body_temp_path %s/b; proxy_temp_path %s/p; fastcgi_temp_path %s/f; "
                "uwsgi_temp_path %s/u; scgi_temp_path %s/s; include %s/limits.conf; include %s/site.conf; }"
                % ((tmp, tmp, ("include %s;" % mime) if os.path.exists(mime) else "") + (tmp,) * 7))
        open(os.path.join(tmp, "nginx.conf"), "w", encoding="utf-8").write(conf)
        r = subprocess.run([nginx, "-t", "-p", tmp, "-c", os.path.join(tmp, "nginx.conf")],
                           capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        check("nginx -t принимает конфиг", r.returncode == 0, out[-300:])
        check("nginx -t без предупреждений", "[warn]" not in out, out[-300:])
else:
    skip("nginx -t", "nginx не установлен")

# ==========================================================================
group("Секреты и концы строк")
# ==========================================================================
gi = code_only(read(".gitignore"))
for pat in (".env", "*.db", "*.db-wal", "*.db-shm"):
    check(".gitignore: " + pat, re.search(r"^" + re.escape(pat) + r"$", gi, re.M) is not None)
check(".gitignore: образцы настроек не спрятаны", "!.env.example" in gi)
ga = read(".gitattributes")
for pat in ("*.sh", "*.service", "*.timer", "*.conf", "Dockerfile", "*.example"):
    check(".gitattributes: " + pat + " с LF", re.search(r"^" + re.escape(pat) + r"\s+text eol=lf", ga, re.M) is not None)
crlf = [p for p in (glob.glob("deploy/*.conf")
                    + ["Dockerfile", "docker-compose.yml", ".env.example"])
        if b"\r" in open(p, "rb").read()]
check("в файлах для Linux нет CRLF", not crlf, crlf)
check("в образце .env нет настоящего секрета",
      re.search(r"^SESSION_PEPPER=замените", env_example, re.M) is not None
      and not re.search(r"^(SMTP_PASS|HELPER_API_KEY)=\S", env_example, re.M))

print("\n" + "=" * 56)
if FAILS:
    print("НЕ ПРОШЛИ: %d" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    sys.exit(1)
print("Настройки запуска проверены: все проверки пройдены.")
