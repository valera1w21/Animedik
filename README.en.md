# animeDik

**[Русская версия → README.md](README.md)**

A personal anime catalogue: your own library, watch progress, search by
franchise, and a player that remembers the second you stopped at. The site
runs on FastAPI and SQLite; the interface is plain JavaScript with no
frameworks.

> **Demonstration mode.** There is no access to anyone else's video in this
> repository, and there will not be. Instead of episodes the player runs
> Blender Foundation films under a Creative Commons licence — enough to
> show how search, the player and watch progress work.

---

## What this is and why

I made it for myself. I wanted one thing: to keep my anime library on my
own hardware rather than in somebody else's service, and to have a player
that does not lose the place I stopped at. Out of that grew a site with
accounts, two-factor sign-in, letters about new episodes and a Telegram
bot that repairs the server while I sleep.

Now I am publishing it — as work that can be looked at. To be honest about
its state: this is **alpha**, an experiment. It works and is covered by
check suites of my own, but it was written for one person and one machine,
not as a product.

---

## What is not here

Video sources. None at all.

The site used to take video from other people's sites through third-party
libraries. For the public repository that has been removed entirely: the
list of sources, the wrappers, the fallback orders and the dependencies
themselves. Technically it is not legal, and I do not want the trouble.

The place where your own source plugs in is still documented — in
[`api/anime.py`](api/anime.py) there is an empty `SOURCES` dictionary and
an explanation of the four methods your module has to provide. Whoever
understands it will do it themselves, and will answer for it themselves.

Everything else — catalogue search, franchises, the library, watch marks,
the player, accounts — works on top of that place and knows nothing about
the source itself.

---

## What it can do

**Search by franchise.** The query "naruto" brings back one "Naruto" card,
not twenty-one lines in a jumble. A click opens every part of the story by
year, with a mark on the one people start watching from. The data comes
from open catalogues (Shikimori, AniList) — they need neither keys nor
registration.

**A library of boxes.** One title, one card, even if it has four seasons
and two films. At the bottom of the card, a watch bar and "episode N of M".

**The player.** HLS and plain files, a draggable seek bar, click the screen
to pause, full screen with every button, switching episodes and parts
straight from the player. The second you stopped at is saved — including
when you leave the page.

**Two languages.** Russian and English, including anime names, the kind of
title and descriptions. The switch is in plain sight and works before
signing in: someone who does not read Russian should not have to find the
settings first.

**Accounts.** There is no sign-up — accounts are created by the owner with
a console command or from their own account page. Passwords use PBKDF2
with 600,000 rounds, two-factor sign-in by a code from an app (TOTP,
implemented here with no dependencies), CSRF markers, sign-in attempt
limits and session fingerprints. A guest gets an hour-long pass that lives
only in the process's memory.

**The year in review.** A calendar of what you watched, genres, records —
counted from real records rather than round numbers.

**Letters about new episodes.** Optional: with no SMTP set, letters simply
do not go out and the site works as usual.

---

## Running it

### With Docker (which is how it runs for me)

```bash
cp .env.example .env
# put your own string in SESSION_PEPPER, and set COOKIE_SECURE=0 if you
# open the site over http://localhost — otherwise the browser keeps no
# session cookie and signing in looks broken for no visible reason
docker compose up -d --build
```

Open `http://localhost:8080`. Create yourself an account:

```bash
docker compose exec app python -m api.admin add your_login --admin
```

### Without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r api/requirements.txt
MODE=demo SESSION_PEPPER=any-string COOKIE_SECURE=0 uvicorn api.main:app --port 8080
```

Python 3.12 is needed — the image is built on it. `MODE=demo` is the
demonstration mode itself;
`MODE=private` with no source plugged in answers with an honest 501.

---

## What lies where

```
api/
  main.py          the web application, 46 routes
  anime.py         talking to video sources — this is where yours plugs in
  anime_demo.py    the demonstration source: free video
  catalog.py       the catalogue: descriptions, genres, franchises
  store.py         SQLite: accounts, the library, watch marks, sessions
  security.py      passwords, sessions, CSRF, rate limits
  twofa.py         sign-in by a code from an app (TOTP)
  mail.py          letters about new episodes
  admin.py         managing accounts from the console
web/               pages, styles and scripts — no build step, no frameworks
deploy/            nginx configs
check1..8, tests   check suites of my own
```

---

## Checks

There are nine suites. They are not about percentages of coverage but
about what has actually broken once already: a forged visitor address,
password guessing, a cache nobody read, a watch bar that ran off the edge.

```bash
python tests.py
python check1_static.py     # and so on up to check10_demo.py
```

`check10_demo.py` separately makes sure that in demonstration mode the site
really does not go anywhere for someone else's video, and that it works
without the libraries this repository does not have.

The checks print their output in Russian.

---

## Stack

Python 3.12, FastAPI, SQLite (WAL), plain JavaScript, hls.js, Docker
Compose, nginx, Cloudflare Tunnel.

There are five dependencies: FastAPI, uvicorn, httpx, pillow, qrcode.
Two-factor sign-in, QR codes and the parsing of catalogue answers are
written here — there is no reason to drag in a library for thirty lines of
arithmetic that would then have to be kept up to date.

---

## Licence

The code is [MIT](LICENSE): take it, change it, use it, keeping the
author's name with it.

Two things in the repository are not mine and live by their own rules:
`web/hls.min.js` is [hls.js](https://github.com/video-dev/hls.js) under
Apache 2.0, and the clips the demonstration mode plays are Blender
Foundation films under Creative Commons BY 3.0.
