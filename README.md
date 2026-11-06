# animeDik

**[Русская версия → README.ru.md](README.ru.md)**

A personal anime catalogue: your own library, watch progress, search by
franchise, and a player that remembers the second you stopped at. The
server is FastAPI + SQLite; the pages are plain JavaScript with no
frameworks and no build step.

> **Demonstration mode.** This repository contains no access to anyone
> else's video, and never will. Instead of episodes the player plays
> Blender Foundation films under a Creative Commons licence — enough to
> show how search, the player and watch progress work.

---

## What this is and why

I built it for myself. I wanted my anime library to live on my own
hardware instead of someone else's service, and I wanted a player that
never loses the place I stopped at. That grew into a site with accounts,
two-factor sign-in, e-mails about new episodes and a small AI helper.

It is published as a piece of work you can look at. To be honest about its
state: this is an **alpha**, an experiment. It works and is covered by its
own check suites, but it was written for one person and one home server,
not as a product. It was also written together with an AI (Claude) — partly
to find out what such a tool can actually do.

---

## What is not here

**Video sources. None at all.**

The private version takes video from third-party sites. In this repository
all of that is removed: the list of sources, their parsers, their fallback
order and their libraries. It is not legal to publish, and I do not want
the trouble.

The place where a source plugs in is still documented: `api/anime.py` has
an empty `SOURCES` dictionary and an explanation of the four methods a
source module must provide (`api/anime_demo.py` is a working template).
Whoever plugs in a source does it themselves and answers for it themselves.

With `MODE=private` and no source plugged in, every video endpoint answers
an honest **501 Not Implemented**; everything else keeps working.

---

## What it can do

- **Search by franchise.** Typing "naruto" gives one "Naruto" card, not
  twenty lines in a jumble. Opening it shows every part of the story by
  year, with a mark on the one people usually start from. Data comes from
  open catalogues (Shikimori, AniList, Jikan) — no keys, no registration.
- **One title — one card.** Four seasons and two films are still one card
  in your library, with a progress bar and "episode N of M".
- **The player.** HLS and plain files, a draggable seek bar, full screen,
  switching episodes and parts from the player, subtitles (SRT/ASS/VTT are
  converted to WebVTT on the server). The second you stopped at is saved,
  including when you close the page.
- **Two languages, kept separate.** The site language (Russian/English) and
  the anime language (dub or subtitles) are two different settings.
- **Accounts without sign-up.** The owner creates accounts from the console
  or the admin page. Roles: guest, user, admin. Two-factor sign-in with an
  authenticator app (TOTP, implemented here without dependencies), backup
  codes, "sign out everywhere".
- **18+ is hidden by default** and can be turned on in the profile; a
  separate "Hentai" switch exists only for signed-in users, never for
  guests.
- **An AI helper** (the "?" bubble) for signed-in users only: answers
  questions about the site and briefly about anime, knows neither the
  site's name nor its address, has no internet access and cannot do
  anything on the site. Optional — off without an API key.
- **The year in review**, themes, seasonal decorations (New Year,
  Halloween, Sakura) that switch on by date, a notification bell for new
  episodes, optional e-mails about them.

---

## Quick start

With Docker:

```bash
cp .env.example .env
# put a long random string into SESSION_PEPPER (openssl rand -hex 32),
# and set COOKIE_SECURE=0 if you open the site over plain http://localhost
docker compose up -d --build
docker compose exec app python -m api.admin add your_login --admin
```

Open `http://localhost:8080`.

Without Docker, the full instructions for Linux and Windows are in
**[INSTALL.md](INSTALL.md)**.

---

## What lies where

```
api/
  main.py          the web application: pages and API routes
  anime.py         talking to video sources — the place where yours plugs in
  anime_demo.py    the demonstration source: free Blender films
  catalog.py       the catalogue: search, descriptions, genres, franchises
  store.py         SQLite: accounts, library, watch marks, sessions
  security.py      passwords, sessions, CSRF marks, rate limits
  twofa.py, qr.py  sign-in by a code from an app, and its QR code
  hls.py           the video relay for streams that need a Referer
  subtitles.py     subtitles of any format → WebVTT
  netguard.py      outgoing requests may only go to the public internet
  helper.py        the AI helper
  mail.py          e-mails about new episodes
  errors.py        error messages in both languages
  admin.py         managing accounts from the console
web/               pages, styles and scripts — no build step, no frameworks
deploy/            nginx configs
tests.py, check*.py   the check suites
```

---

## Documents

| File | What is in it |
|---|---|
| [INSTALL.md](INSTALL.md) | Installing on a server (Docker, nginx, Cloudflare Tunnel) and running locally |
| [SECURITY.md](SECURITY.md) | How the site protects itself and how to report a problem |
| [TESTING.md](TESTING.md) | How to run the check suites and what each one checks |

---

## Stack

Python 3.12, FastAPI, SQLite (WAL), plain JavaScript (ES5), hls.js, Docker
Compose, nginx, Cloudflare Tunnel.

Runtime dependencies: FastAPI, uvicorn, httpx, Pillow (exact versions in
`api/requirements.txt` and `api/constraints.txt`). Two-factor sign-in, QR
codes and subtitle conversion are written here — there is no reason to
pull in a library for a few dozen lines that would then need updating.

Code comments and the output of the check suites are in Russian.

---

## Licence

The code is [MIT](LICENSE): take it, change it, use it, keeping the
author's name with it.

Two things in the repository are not mine and follow their own licences:
`web/hls.min.js` is [hls.js](https://github.com/video-dev/hls.js) under
Apache 2.0; the clips the demonstration mode plays are Blender Foundation
films under Creative Commons BY 3.0. The Nunito font in `web/fonts/` is
under the SIL Open Font License.
