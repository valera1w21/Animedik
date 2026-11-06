# Check suites

There are **44 suites**: `tests.py` and 43 `check*.py` files. Each one is a
standalone program: it starts the site itself on a temporary database
(your own `anime.db` is never touched), runs its checks and ends with
"все проверки пройдены" (all checks passed) or a list of failures. Exit
code 0 means green, 1 means something failed.

The suites are not about coverage percentages. Most of them start from
something that really broke once and must not come back. Their output and
comments are in Russian.

## Running everything

You need the same environment as for running the site (see
[INSTALL.md](INSTALL.md)). The browser parts of `check33_frontend.py` and
`check39_live.py` also need Playwright with Chromium (once):

```bash
pip install playwright
python -m playwright install chromium
```

Without Playwright these two suites skip their browser part and say so
loudly.

**Linux / macOS**, both modes:

```bash
mkdir -p test-logs
for mode in demo private; do
  for f in tests.py check*.py; do
    MODE=$mode SESSION_PEPPER=test-pepper-0123456789 COOKIE_SECURE=0 \
      python "$f" > "test-logs/$mode-$f.log" 2>&1 || echo "$mode: FAILED $f"
  done
done
```

**Windows (PowerShell)**, both modes:

```powershell
New-Item -ItemType Directory -Force test-logs | Out-Null
foreach ($mode in "demo","private") {
  Get-ChildItem tests.py, check*.py | ForEach-Object {
    $env:MODE=$mode; $env:SESSION_PEPPER="test-pepper-0123456789"; $env:COOKIE_SECURE="0"
    .venv\Scripts\python $_.Name *> "test-logs\$mode-$($_.Name).log"
    if ($LASTEXITCODE -ne 0) { Write-Host "$mode: FAILED $($_.Name)" }
  }
}
```

No output except the loop itself means everything passed. A full run takes
roughly 15 minutes per mode. In `MODE=private` the suites that need a video
source plug in fake ones (`fake_sources.py`) — no network access to any
video site is ever made.

## What each suite checks

| Suite | What it checks |
|---|---|
| `tests.py` | The main suite: what really works and what is really closed — sign-in, sessions, library, progress, admin |
| `check1_static.py` | Errors found without running the code: references to missing elements, handlers without buttons, dangerous patterns, lost translations, duplicate CSS rules |
| `check2_live.py` | A real server: what it actually answers |
| `check3_attack.py` | Deliberate attempts to break it: injections, path traversal, forged headers |
| `check4_growth.py` | Things that pile up for months and then go off — invisible in a five-minute test |
| `check5_resilience.py` | Resilience: lost database, file permissions, unexpected server answers on the page |
| `check6_audit.py` | Regressions from the first code review |
| `check7_new.py` | Attempts to break the newest features |
| `check8_admin.py` | Nobody but an administrator reaches the admin routes (404 for everyone else) |
| `check9_qr.py` | The built-in QR generator against a reference library (if installed) |
| `check10_demo.py` | Demonstration mode: Blender films, nothing else |
| `check11_auth.py` | Sign-in, sessions, two-factor codes, guests, limits |
| `check12_sources.py` | The source layer: timeouts, cache, failures (on fake sources) |
| `check13_watch.py` | What the watch page sends to the server |
| `check14_order.py` | The watching order of a franchise's parts |
| `check16_updates.py` | New episodes: the bell learns about them by itself |
| `check17_posters.py` | Posters and descriptions "at any cost" |
| `check18_lang_adult.py` | Site language separate from anime language; the 18+ filter |
| `check19_synonyms.py` | A title that sources know under another name |
| `check20_spare.py` | A title missing from the main sources but present in a spare one |
| `check21_plans.py` | "Plan to watch" and avatars in the list |
| `check22_adult_genre.py` | 18+: genre search, hints, the random pick |
| `check23_helper.py` | The AI helper: who may use it, what it may say |
| `check24_hentai.py` | The "Hentai" setting: only for signed-in users, never for guests |
| `check25_banner.py` | The profile banner |
| `check25_english.py` | English anime language and separate subtitle files |
| `check26_english.py` | Strict English mode, the video relay, cache reset |
| `check27_one_card.py` | One anime — one card |
| `check28_css.py` | Styles are intact: brackets match, every animation has keyframes |
| `check29_speed.py` | Speed of opening a title and of the random pick |
| `check30_lang_text.py` | The English page: English titles, descriptions, genres |
| `check31_security_be.py` | Server security review |
| `check32_db.py` | The database: connection settings, no orphan rows after deleting an account, integrity check, indexes, merging duplicate cards |
| `check33_frontend.py` | The page in a real browser: security, languages, accessibility |
| `check34_ops.py` | Docker, nginx, dependency versions, file permissions |
| `check35_be_fixes.py` | Small server fixes |
| `check36_hentai_source.py` | An 18+-only source (a fake one): kept out of normal lists and menus, tag filter, the outgoing-request guard |
| `check37_server.py` | Server and security: database copies, the relay, Origin checks, HEAD |
| `check39_live.py` | A full click-through of the site in a browser: every button, both languages, phone widths |
| `check41_sources.py` | The helper, the minors filter, catalogue time limits and broken records |
| `check42_demo_offline.py` | Demonstration mode makes no connections to video sources or the helper |
| `check43_crosszone.py` | Subtitles, the episode time limit, source language, avatars, the random pick, `<video>` without crossorigin |
| `check44_public.py` | This public copy contains nothing private and no traces of real video sources |
| `check45_no_sources.py` | Private mode with no sources answers an honest 501 |

The numbers 15, 38 and 40 are gaps in the numbering, not lost files.
