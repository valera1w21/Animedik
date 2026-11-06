# Installing and running animeDik

Three ways to run the site:

1. [On a server with Docker](#1-on-a-server-with-docker) — how it runs for real.
2. [Locally on Linux/macOS without Docker](#2-locally-on-linux-or-macos-without-docker).
3. [Locally on Windows without Docker](#3-locally-on-windows-without-docker).

All three start in **demonstration mode** (`MODE=demo`): the player plays
free Blender Foundation films. There are no video sources in this
repository — see "What is not here" in [README.md](README.md).

---

## 1. On a server with Docker

Tested on Debian 13 and Ubuntu 24.04. You need Docker with the Compose
plugin and, for a public site, nginx.

### 1.1. Settings

```bash
cp .env.example .env
chmod 600 .env        # only you can read it: there is a secret inside
nano .env
```

Set `SESSION_PEPPER` to a long random string — at least 16 characters. With
the placeholder or a short string a real site (`COOKIE_SECURE=1`) refuses
to start; a local test over http (`COOKIE_SECURE=0`) only warns in the log:

```bash
openssl rand -hex 32
```

What it is for: the string is mixed into session fingerprints and is the
key that seals two-factor secrets in the database. If the database is
stolen, nobody can sign in with what is inside it. If you change the string
later, everyone is signed out and two-factor sign-in has to be set up again
(`python -m api.admin 2fa-off LOGIN` removes it for one account).

Keep `COOKIE_SECURE=1` (the default) when the site is served over https.
Set it to `0` only to test over plain `http://` — otherwise the browser
does not keep the sign-in cookie and signing in "silently does nothing".

The rest of `.env.example` is optional and described in the file itself
(e-mail via SMTP, the AI helper key, log level).

### 1.2. Start

```bash
docker compose up -d --build
docker compose ps          # after ~30 seconds the app should be (healthy)
```

The container listens only on `127.0.0.1:8080` — it is never exposed to
the network directly; nginx sits in front of it.

### 1.3. The first account

There is no sign-up. Create yourself an administrator:

```bash
docker compose exec app python -m api.admin add your_login --admin
```

The password is asked twice and is not shown while typing (not even as
asterisks — that is normal). At least 10 characters, not from the list of
the most common passwords.

Other account commands (all run the same way, `docker compose exec app
python -m api.admin …`):

| Command | What it does |
|---|---|
| `list` | show accounts |
| `add LOGIN [--admin]` | create an account |
| `passwd LOGIN` | change a password |
| `disable LOGIN` / `enable LOGIN` | close / reopen sign-in |
| `delete LOGIN` | delete an account |
| `2fa-off LOGIN` | turn off sign-in by a code from an app |
| `backup` | a consistent copy of the database without stopping the site |

### 1.4. nginx

```bash
sudo cp deploy/nginx-anime.conf  /etc/nginx/sites-available/anime
sudo cp deploy/nginx-limits.conf /etc/nginx/conf.d/anime-limits.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/anime /etc/nginx/sites-enabled/anime
sudo nginx -t && sudo systemctl reload nginx
```

The config listens on `127.0.0.1:80` (for a tunnel on the same machine),
limits request rates, overwrites the visitor-address headers so they cannot
be forged, and passes `Host` to the app. **Do not remove**
`proxy_set_header Host $host;` — the app compares it with the `Origin` of
every POST/DELETE request, and without it every button answers 403.

Check:

```bash
curl -s  http://127.0.0.1:8080/api/health     # the app directly: {"status":"ok",…}
curl -si http://127.0.0.1/ | head -1          # through nginx: HTTP/1.1 200 OK
```

### 1.5. Putting it on the internet without opening ports

The easiest safe way is **Cloudflare Tunnel**: `cloudflared` on your server
connects *out* to Cloudflare, so you open no ports on your router and your
home IP address is not visible to visitors. Point the tunnel at
`http://localhost:80`.

If you want one public address and one private one (for example a demo on
`example.com` and your own copy on `my.example.com`), add the private
hostname to the same tunnel and protect it with **Cloudflare Access**
(Zero Trust → Access → Applications). Visitors then have to confirm an
e-mail address from your allow-list *before* the request ever reaches your
server; the site's own sign-in is the second lock behind it.

### 1.6. Updating

```bash
git pull
docker compose build --pull && docker compose up -d
```

`--pull` also brings the latest patches of the base image. The database
lives in a Docker volume and survives rebuilds.

---

## 2. Locally on Linux or macOS without Docker

Python 3.12 is needed.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r api/requirements.txt -c api/constraints.txt

export MODE=demo SESSION_PEPPER="$(openssl rand -hex 32)" COOKIE_SECURE=0 DB_PATH=anime.db
python -m api.admin add your_login --admin
uvicorn api.main:app --port 8000
```

Open `http://localhost:8000`. Stop with `Ctrl+C`.

Keep the same `SESSION_PEPPER` between runs (write it down), or your
sessions and two-factor setup will not survive a restart.

---

## 3. Locally on Windows without Docker

Everything in PowerShell, in the project folder.

**Once** — a separate Python environment for the project:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r api\requirements.txt -c api\constraints.txt
```

**Every time, in every new PowerShell window** — the settings. The `.env`
file is **not** read when you run the site like this; only `docker compose`
reads it. Variables set with `$env:` live only in the current window:

```powershell
$env:MODE="demo"; $env:SESSION_PEPPER="your-long-random-string"; $env:COOKIE_SECURE="0"; $env:DB_PATH="anime.db"
```

A random string: `.venv\Scripts\python -c "import secrets; print(secrets.token_hex(32))"`.
Use the same one every time.

**Once** — an account (in the same window):

```powershell
.venv\Scripts\python -m api.admin add your_login --admin
```

**Run:**

```powershell
.venv\Scripts\python -m uvicorn api.main:app --port 8000
```

Open `http://localhost:8000`. Stop with `Ctrl+C`.

Note: commands with `&&` and `sudo` in section 1 are for a Linux server;
PowerShell does not understand them.

---

## If something does not work

**"I can't sign in, the password is right."** Either the attempt limit
kicked in (wait 15 minutes), or the site is opened over `http://` while
`COOKIE_SECURE=1` — the browser drops the cookie.

**"Signed in, but every button answers 403 'Request rejected'."** nginx is
not passing the site name: `proxy_set_header Host $host;` is missing from
the nginx config.

**"The site does not start: SESSION_PEPPER …"** The string is missing, is
the placeholder from `.env.example`, or is shorter than 16 characters (with
`COOKIE_SECURE=1` this stops the start; with `0` it is only a warning).

**"Port 8080 is already in use."** Something else is listening there:
`sudo ss -ltnp | grep 8080`.

**Logs:**

```bash
docker compose logs --tail 50
```
