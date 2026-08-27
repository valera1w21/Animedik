"""The anime catalogue: descriptions, genres and a random pick.

The sources the video comes from know only "a name and episodes" —
neither descriptions nor genres in any convenient form. For catalogue
data we go elsewhere.

The main catalogue is Shikimori: it is Russian-language, and description
and genres arrive in Russian straight away. That matters more than it
seems: nobody will read an English description under the player, and
"Horror, Supernatural" instead of "Ужасы, Мистика" does not answer the
question "what is this thing anyway".

The fallback is AniList, for when Shikimori stays silent. Its answer is
in English, so genres are translated through the dictionary below and
the description is shown as it is: English beats none.

Why through our own server rather than straight from the browser:

  * otherwise every visitor's address would go to someone else's site
    every time a page opens — exactly what this site does nowhere else;
  * answers can be put in a shared cache: one request for everybody
    instead of one per person;
  * somebody else's answer is parsed here, and only what we need leaves
    for the outside — no markup, no links, no extra fields.

Neither of them requires keys or registration.
"""

from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from typing import Any

import httpx

# The name-matching measure lives next to the video sources: it is
# needed both there and here — to tell which part of a franchise is open
# right now.
from . import anime

log = logging.getLogger("anime.catalog")

SHIKI = "https://shikimori.one/api"
ANILIST = "https://graphql.anilist.co"
TIMEOUT = 12.0
# Shikimori asks to be introduced to. Without that it is entitled to
# refuse.
#
# Latin letters only: HTTP headers travel in a single-byte encoding, and
# Cyrillic in them kills the request before it is even sent —
# UnicodeEncodeError. The error looked harmless: "the catalogue did not
# answer", and everything quietly went to the English-language fallback.
# So Russian descriptions did not work at all, and the cause was hiding
# in our own header.
HEADERS = {"User-Agent": "animedik/1.0 (personal home site)"}

# How long we wait between calls to the catalogues. Access is free, but
# abusing it is not allowed: exceed it and the server's address gets
# banned, and the catalogue falls away for everyone at once.
MIN_GAP = 0.6
_last_call = [0.0]
_gate = asyncio.Lock()

_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 24 * 3600          # описания меняются раз в год, если вообще
CACHE_MAX = 500

# The random pick is taken from the first pages by popularity. Beyond
# them begins what nobody has heard of — and a "random anime" out of that
# turns into a list of unknown names and stops being interesting.
RANDOM_PAGES = 220


def _cache_get(key: str) -> Any:
    row = _cache.get(key)
    if not row:
        return None
    born, value = row
    if time.time() - born > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: Any) -> None:
    if len(_cache) > CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: CACHE_MAX // 2]
        for k, _ in oldest:
            _cache.pop(k, None)
    _cache[key] = (time.time(), value)


async def _pause() -> None:
    """We do not pester someone else's site.

    The lock is needed because several requests may run at once: without
    it they would all see the same "last call" and go out simultaneously,
    which means the limit would not work at all.
    """
    async with _gate:
        gap = MIN_GAP - (time.time() - _last_call[0])
        if gap > 0:
            await asyncio.sleep(gap)
        _last_call[0] = time.time()


async def _get(url: str, params: dict | None = None) -> Any:
    """An ordinary request. Any trouble — we quietly return nothing.

    The catalogue is a pleasant addition, not something the site cannot
    work without: it must not bring down the watch page because it is
    under maintenance.
    """
    await _pause()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS,
                                     follow_redirects=True) as client:
            r = await client.get(url, params=params)
        if r.status_code != 200:
            log.info("справочник ответил %s на %s", r.status_code, url)
            return None
        return r.json()
    except Exception as exc:                        # noqa: BLE001
        log.info("справочник не ответил: %s", type(exc).__name__)
        return None


async def _ask_anilist(query: str, variables: dict) -> dict | None:
    await _pause()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(ANILIST, json={"query": query, "variables": variables})
        if r.status_code != 200:
            return None
        return r.json().get("data") or None
    except Exception as exc:                        # noqa: BLE001
        log.info("запасной справочник не ответил: %s", type(exc).__name__)
        return None


# --------------------------------------------------------------------------
# Cleaning up someone else's text
# --------------------------------------------------------------------------
# Shikimori writes descriptions in its own markup: [b]bold[/b],
# [url=...]link[/url], [character=123]name[/character], [spoiler]...
# AniList uses ordinary html. All of it goes to the browser as text, so
# it would show up as is, in the middle of a sentence.
_BB_SPOILER = re.compile(r"\[spoiler[^\]]*\].*?\[/spoiler\]", re.S | re.I)
_BB_PAIR = re.compile(r"\[(\w+)(?:=[^\]]*)?\](.*?)\[/\1\]", re.S)
_BB_ANY = re.compile(r"\[/?[^\]]{0,60}\]")
_HTML_TAG = re.compile(r"<[^>]+>")
_JUNK = re.compile(r"\((?:source|written by|источник)[^)]*\)", re.I)

# The turns of phrase after which descriptions usually start retelling the plot.
_SPOILER_WORDS = re.compile(
    r"(?:however|but then|it turns out|in the end|finally,|"
    r"однако|но затем|оказывается|в финале|в конце концов|"
    r"но всё меняется|но однажды всё)", re.I)

DESC_MAX = 420


def clean_description(raw: str | None) -> str:
    """A short description with no markup and no spoilers.

    We take the beginning only: the first sentences are the setup, which
    is what people read them for. After that the retelling begins, and
    with it what spoils the watching.
    """
    if not raw:
        return ""
    text = str(raw)
    # 1. Pieces marked as a spoiler outright — out whole.
    text = _BB_SPOILER.sub(" ", text)
    # 2. Paired tags: we keep the contents and remove the tags.
    for _ in range(3):                              # вложенность бывает
        new = _BB_PAIR.sub(r"\2", text)
        if new == text:
            break
        text = new
    # 3. Leftovers of single tags and html.
    text = _BB_ANY.sub(" ", text)
    text = html.unescape(_HTML_TAG.sub(" ", text))
    text = _JUNK.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # We cut at the first turn of phrase that usually precedes a plot twist.
    cut = _SPOILER_WORDS.search(text)
    if cut and cut.start() > 120:
        text = text[:cut.start()].strip()

    if len(text) <= DESC_MAX:
        return text.rstrip(" ,;:—-")
    # We trim at a sentence boundary, not in the middle of a word.
    head = text[:DESC_MAX]
    dot = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if dot > 160:
        return head[:dot + 1].strip()
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip(" ,;:—-") + "…"


# AniList genres arrive in English. Its list is closed and short, so a
# dictionary is enough — nothing has to be guessed.
GENRE_RU = {
    "Action": "Экшен", "Adventure": "Приключения", "Comedy": "Комедия",
    "Drama": "Драма", "Ecchi": "Этти", "Fantasy": "Фэнтези",
    "Horror": "Ужасы", "Mahou Shoujo": "Махо-сёдзё", "Mecha": "Меха",
    "Music": "Музыка", "Mystery": "Детектив", "Psychological": "Психологическое",
    "Romance": "Романтика", "Sci-Fi": "Фантастика", "Slice of Life": "Повседневность",
    "Sports": "Спорт", "Supernatural": "Мистика", "Thriller": "Триллер",
}


def ru_genre(name: str) -> str:
    return GENRE_RU.get(name, name)


# --------------------------------------------------------------------------
# Parsing the answers
# --------------------------------------------------------------------------
KIND_RU = {
    "tv": "сериал", "movie": "фильм", "ova": "OVA", "ona": "ONA",
    "special": "спешл", "music": "клип",
    # Recaps and specials shown on television arrive under a separate
    # label. Untranslated it rode into the interface as it was —
    # "tv_special" in the middle of a Russian list of parts.
    "tv_special": "спецвыпуск", "pv": "трейлер", "cm": "реклама",
    "TV": "сериал", "MOVIE": "фильм", "OVA": "OVA", "ONA": "ONA",
    "SPECIAL": "спешл", "MUSIC": "клип", "TV_SHORT": "короткий сериал",
}


def pack_shiki(a: dict | None, full: dict | None = None) -> dict | None:
    """Shikimori's answer. It is Russian-language, nothing needs translating."""
    if not a:
        return None
    name = a.get("russian") or a.get("name") or ""
    if not name:
        return None
    d = full or a
    genres = [g.get("russian") or g.get("name") or ""
              for g in (d.get("genres") or [])]
    genres = [g for g in genres if g][:5]
    try:
        score = float(a.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    year = None
    aired = a.get("aired_on") or ""
    if isinstance(aired, str) and len(aired) >= 4 and aired[:4].isdigit():
        year = int(aired[:4])
    poster = ""
    image = a.get("image") or {}
    if isinstance(image, dict):
        tail = image.get("original") or image.get("preview") or ""
        if tail:
            poster = tail if tail.startswith("http") else "https://shikimori.one" + tail
    return {
        "title": name,
        "title_en": a.get("name") or "",
        "year": year,
        "episodes": a.get("episodes") or a.get("episodes_aired") or None,
        "kind": KIND_RU.get(a.get("kind", ""), a.get("kind") or ""),
        "genres": genres,
        "about": clean_description(d.get("description")),
        "poster": poster,
        # Shikimori's score is out of ten, we bring it to a hundred: the
        # same as the fallback catalogue, so they can be shown alike.
        "score": int(round(score * 10)) if score else None,
    }


MEDIA_FIELDS = """
  id title { romaji english native }
  seasonYear episodes format genres
  description(asHtml: false) coverImage { large medium }
  averageScore isAdult
"""
Q_SEARCH = "query ($s: String) { Media(type: ANIME, search: $s, isAdult: false) {" + MEDIA_FIELDS + "} }"
Q_RANDOM = ("query ($p: Int) { Page(page: $p, perPage: 1) {"
            " media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {" + MEDIA_FIELDS + "} } }")


def pack_anilist(media: dict | None) -> dict | None:
    """The fallback catalogue's answer. Genres we translate, the description we leave."""
    if not media or media.get("isAdult"):
        return None
    titles = media.get("title") or {}
    name = titles.get("romaji") or titles.get("english") or titles.get("native") or ""
    if not name:
        return None
    cover = media.get("coverImage") or {}
    score = media.get("averageScore")
    return {
        "title": name,
        "title_en": titles.get("english") or "",
        "year": media.get("seasonYear"),
        "episodes": media.get("episodes"),
        "kind": KIND_RU.get(media.get("format", ""), media.get("format") or ""),
        "genres": [ru_genre(g) for g in (media.get("genres") or [])][:5],
        "about": clean_description(media.get("description")),
        "poster": cover.get("large") or cover.get("medium") or "",
        "score": int(score) if isinstance(score, int) else None,
    }


# --------------------------------------------------------------------------
# Simplifying the name
# --------------------------------------------------------------------------
def simpler_titles(title: str) -> list[str]:
    """Variants of the name from the full one to the shortest.

    Video sources write a name their own way: "Магическая битва 2",
    "Магическая битва 0 Фильм", "Магическая академия Атараксия: Гибрид x
    Сердце". The catalogue knows no such strings — it knows "Магическая
    битва". Without simplification no description was found for anything
    that ran longer than one season, and that is a good half of the list.
    """
    title = (title or "").strip()
    out = [title]
    head = re.split(r"\s*[:—–]\s*", title)[0].strip()
    if head and head != title:
        out.append(head)
    for base in list(out):
        cut = base
        for _ in range(2):                          # хвостов бывает два: «0 Фильм»
            shorter = re.sub(
                r"\s*(?:\d+|[IVX]+)\s*(?:сезон|season)?\s*$|"
                r"\s*(?:фильм|movie|тв|tv|ova|ona|спешл|special)\s*\d*\s*$",
                "", cut, flags=re.I).strip()
            if shorter == cut:
                break
            cut = shorter
        if cut and cut not in out and len(cut) >= 3:
            out.append(cut)
    return out[:3]                                  # больше трёх запросов не делаем


# --------------------------------------------------------------------------
# What the endpoints call
# --------------------------------------------------------------------------
async def _shiki_about(title: str) -> dict | None:
    found = await _get(SHIKI + "/animes", {"search": title, "limit": 1, "censored": "true"})
    if not isinstance(found, list) or not found:
        return None
    brief = found[0]
    # Genres and description sit in the detailed card only.
    full = await _get(SHIKI + "/animes/" + str(brief.get("id")))
    return pack_shiki(brief, full if isinstance(full, dict) else None)


async def about(title: str, lang: str = "ru") -> dict | None:
    """Reference data by name: description, year, genres, score.

    First the Russian catalogue, then the fallback. If the full name is
    not found — we try the simplified variants (see simpler_titles).

    lang="en" flips the order: AniList is asked first, being the
    English-language one. A Russian description is exactly as useless to
    a person who switched the site to English as an English one is to a
    Russian speaker.
    """
    title = (title or "").strip()[:120]
    if len(title) < 2:
        return None
    if lang == "en":
        return await _about_en(title)
    key = "about:" + title.lower()
    hit = _cache_get(key)
    if hit is not None:
        return hit or None

    packed = None
    for variant in simpler_titles(title):
        packed = await _shiki_about(variant)
        if packed and packed.get("about"):
            break
        # There is no Russian description — let us ask the fallback, it may have one.
        data = await _ask_anilist(Q_SEARCH, {"s": variant})
        fallback = pack_anilist((data or {}).get("Media"))
        if fallback and fallback.get("about"):
            # The name and genres we still prefer in Russian.
            if packed:
                fallback["title"] = packed["title"]
                fallback["genres"] = packed["genres"] or fallback["genres"]
            packed = fallback
            break
        packed = packed or fallback
        if packed:
            break

    _cache_put(key, packed or {})
    return packed


async def _about_en(title: str) -> dict | None:
    """The same, but in English: AniList first, the Russian catalogue as fallback.

    AniList has description and genres in English to begin with, nothing
    needs translating. If it stays silent we take the Russian card — with
    it at least the year, the episode count and the cover will be right.
    """
    key = "about:en:" + title.lower()
    hit = _cache_get(key)
    if hit is not None:
        return hit or None

    packed = None
    for variant in simpler_titles(title):
        data = await _ask_anilist(Q_SEARCH, {"s": variant})
        media = (data or {}).get("Media")
        if media:
            found = pack_anilist(media)
            if found:
                # Our genres get translated into Russian while parsing —
                # here that is exactly what is not wanted.
                found["genres"] = [g for g in (media.get("genres") or [])][:5]
                titles = media.get("title") or {}
                found["title"] = (titles.get("english") or titles.get("romaji")
                                  or found["title"])
                packed = found
                break
    if packed is None:
        packed = await about(title, "ru")

    _cache_put(key, packed or {})
    return packed


async def random_anime() -> dict | None:
    """A random anime from the catalogue — not from your library."""
    page = random.randint(1, RANDOM_PAGES)
    key = f"page:{page}"
    hit = _cache_get(key)
    if hit is not None:
        return hit or None

    packed = None
    found = await _get(SHIKI + "/animes",
                       {"order": "popularity", "limit": 1, "page": page,
                        "censored": "true", "kind": "tv"})
    if isinstance(found, list) and found:
        brief = found[0]
        full = await _get(SHIKI + "/animes/" + str(brief.get("id")))
        packed = pack_shiki(brief, full if isinstance(full, dict) else None)

    if not packed:
        data = await _ask_anilist(Q_RANDOM, {"p": page})
        items = ((data or {}).get("Page") or {}).get("media") or []
        packed = pack_anilist(items[0]) if items else None

    _cache_put(key, packed or {})
    return packed


# ==========================================================================
# Searching by franchise
# ==========================================================================
# How this search differs from the one that was here.
#
# The search box used to send a query to the video site, and the person
# got what that site considers similar. For "Наруто" it looked like this:
# source A knows "Наруто Ураганные хроники" and "Боруто", but does not
# know "Наруто". Source B dumps twenty-one lines in a jumble: first
# "Боруто: Фильм", then the second season, then a special about a sports
# festival. Which of these is "Наруто" itself and in what order to watch
# it all — such a list gives no way of telling.
#
# The reason is simple: video sites have no notion of a franchise. Every
# season, film and OVA sits there as a separate record with an arbitrary
# name, and the links between them are stored nowhere.
#
# The catalogue does have them. At Shikimori every card is marked with a
# franchise field: "Наруто", "Ураганные хроники" and every film and
# special all carry one label, "naruto". So search now goes through the
# catalogue: a query returns one card per franchise, and inside it all
# the parts in order of release.
#
# Shikimori was chosen as the main one over AniList for two reasons: it
# gives Russian names (the site is Russian, and "Атака титанов" reads
# better than "Shingeki no Kyojin"), and only it has the franchise label.
# AniList stays as the fallback for when Shikimori does not answer; it
# knows no franchises, so parts there are gathered by a shared beginning
# of the name — worse, but better than nothing.

SHIKI_GQL = "https://shikimori.one/api/graphql"

# Labels that have nothing to do with watching: trailers, promotional
# clips and music videos. In a list of parts they are only in the way —
# nobody watches them, yet they take up room.
JUNK_KINDS = {"pv", "cm", "music"}

# How many parts of a franchise the catalogue gives at once. Fifty is its
# own ceiling; it will not give more however much you want.
FRANCHISE_LIMIT = 50

GQL_FIELDS = """
    id name russian franchise kind episodes episodesAired status score
    airedOn { year }
    poster { mainUrl originalUrl }
"""


async def _ask_shiki_gql(query: str, variables: dict | None = None) -> dict | None:
    """A request to the catalogue's GraphQL. Silence is not an error, it is None."""
    await _pause()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS,
                                     follow_redirects=True) as client:
            r = await client.post(SHIKI_GQL,
                                  json={"query": query, "variables": variables or {}})
        if r.status_code != 200:
            log.info("справочник ответил %s на graphql", r.status_code)
            return None
        body = r.json()
        if body.get("errors"):
            log.info("справочник вернул ошибку: %s", str(body["errors"])[:200])
        return body.get("data") or None
    except Exception as exc:                        # noqa: BLE001
        log.info("справочник не ответил: %s", type(exc).__name__)
        return None


def pack_part(node: dict | None) -> dict | None:
    """One part of a franchise: a season, a film, an OVA or a special."""
    if not isinstance(node, dict):
        return None
    title = node.get("russian") or node.get("name") or ""
    if not title:
        return None
    kind = node.get("kind") or ""
    poster = node.get("poster") or {}
    poster = poster if isinstance(poster, dict) else {}
    year = (node.get("airedOn") or {}).get("year")
    try:
        score = float(node.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "id": str(node.get("id") or ""),
        "title": title,
        "title_en": node.get("name") or "",
        "franchise": node.get("franchise") or "",
        "year": year if isinstance(year, int) else None,
        "kind": kind,
        "kind_ru": KIND_RU.get(kind, kind),
        # A series still airing has no total episode count set: "Ван-Пис"
        # ran for twenty-six years, and how many there will be in the end
        # nobody knows. Then we show how many are out.
        "episodes": node.get("episodes") or node.get("episodesAired") or None,
        "ongoing": node.get("status") == "ongoing",
        "poster": (poster.get("mainUrl") or poster.get("originalUrl") or ""),
        "score": int(round(score * 10)) if score else None,
    }


# Within one year the series comes first, then in descending order of
# "mainness": what people watch above what they watch afterwards. The
# catalogue gives no date finer than the year, so there is nothing to
# order 2013 "by day of release" with — but ordering by sense is possible.
KIND_RANK = {"tv": 0, "ona": 1, "movie": 2, "tv_special": 3, "ova": 3,
             "special": 4}


def _sort_key(part: dict) -> tuple:
    """The order of parts — by release.

    What is not out yet goes to the end: it has no year, and putting a
    zero there is not allowed — an announcement would then stand ahead of
    the first season.

    Parts of the same age within a year are sorted out by kind. Without
    that, "Атака титанов" led with four lines of OVA, a theatrical short
    and a recap — all from 2013 — while the first season itself stood
    fourth. Formally correct, reads like a list of anything except what
    the person came for.
    """
    rank = KIND_RANK.get(part.get("kind") or "", 5)
    size = -(part.get("episodes") or 0)
    return (0, part["year"], rank, size) if part.get("year") else (1, 0, rank, size)


def sort_parts(parts: list[dict]) -> list[dict]:
    return sorted(parts, key=_sort_key)


def main_part(parts: list[dict]) -> dict:
    """The part a franchise is recognised by.

    It is almost always the first series: "Наруто", not "Наруто:
    Ураганные хроники" and not "Боруто".

    Series are picked strictly, as a separate tier, rather than together
    with the series-adjacent. Otherwise this happens: for "Атака титанов"
    the first season and a recap cut came out in the same year, the cut
    is marked tv_special, and sorting by the year alone made the
    franchise be called "Атака титанов: Рекап". For "Судьба" a special
    about the "Grand Order" climbed to the top the same way.

    Within a tier, at an equal year, whichever has more episodes wins:
    the main story is almost always longer than the special accompanying
    it.
    """
    for tier in ([p for p in parts if p.get("kind") == "tv"],
                 [p for p in parts if p.get("kind") in ("tv_special", "ona")],
                 parts):
        if tier:
            return sorted(tier, key=lambda p: (_sort_key(p),
                                               -(p.get("episodes") or 0)))[0]
    return parts[0]


def mark_main(parts: list[dict]) -> list[dict]:
    """Marks the part people start watching from.

    The rule is simple: the earliest one. The list is sorted by year
    anyway, so the mark lands on the first line — what is written in the
    list is what is marked, with no discrepancy.

    A cleverer rule used to stand here: "the franchise's first series".
    It was invented for the sake of "Ван-Пис", whose earliest part is an
    OVA from 1998, shot before the series, while the series itself came
    out a year later. The rule did send people to the series, but at the
    cost of the "start" mark leaving the line that is first in the list —
    and there is nothing to explain that with to someone looking at it.

    Within one year the order is not accidental (see _sort_key): a series
    stands above OVAs and recaps. So for "Атака титанов", where 2013
    brought a series, an OVA and a cut, the mark is on the series anyway.
    """
    if not parts:
        return parts
    for i, part in enumerate(parts):
        part["main"] = i == 0
    return parts


def group_key(part: dict) -> str:
    """Under which label a part lands in a shared card.

    Most catalogue cards have the franchise label set. Where there is
    none (standalone films, fresh announcements), the name itself becomes
    the label: such a franchise consists of one part, and that is true.
    """
    return part.get("franchise") or ("title:" + part["title"].lower())


async def _franchise_pages(fid: str) -> list[dict] | None:
    """Every part of one franchise, both pages at once.

    The catalogue's limit is fifty cards at a time, and its order runs
    from new to old. You do not get burned by this immediately: "Наруто"
    has twenty-nine parts, everything fits, and it seems that is how it
    should be. But "Ван-Пис" has ninety — and the first fifty are the
    years 2015–2027, meaning "Ван-Пис" itself, from 1999, does not make
    it into the answer at all. The franchise was called "Ван-Пис: Остров
    Рыболюдей", and the first episode in the list came from the middle.

    So two pages are taken. Both in one request, through field aliases:
    two calls to someone else's site would take twice as long for exactly
    nothing.
    """
    safe = re.sub(r"[^a-z0-9_-]", "", str(fid).lower())[:60]
    if not safe:
        return None
    ask = " ".join(
        'p%d: animes(franchise: "%s", limit: %d, page: %d, order: aired_on,'
        " censored: true) { %s }" % (n, safe, FRANCHISE_LIMIT, n, GQL_FIELDS)
        for n in (1, 2)
    )
    data = await _ask_shiki_gql("query { " + ask + " }")
    if data is None:
        return None
    nodes = list(data.get("p1") or []) + list(data.get("p2") or [])
    if not nodes:
        return None

    parts: dict[str, dict] = {}
    for node in nodes:
        packed = pack_part(node)
        if packed and packed["kind"] not in JUNK_KINDS:
            parts[packed["id"]] = packed        # один и тот же id на стыке страниц
    return sort_parts(list(parts.values()))


GQL_SEARCH = ("query ($s: String) { animes(search: $s, limit: 24, censored: true) {"
              + GQL_FIELDS + "} }")


def franchise_card(fid: str, parts: list[dict]) -> dict:
    """A franchise card: what is visible in the results before a click.

    The numbers on the card are about the main part, not the franchise as
    a whole: the first season's year of release and how many episodes it
    has. That is what search knows without asking the catalogue a second
    time.

    The temptation to write "29 parts" here was there, and had to be
    given up: at search time not all the franchise's parts are known,
    only those that made it into the answer. The number would come out
    right sometimes and wrong others, and there is no checking it by eye
    — exactly the case where it is better to write nothing. The full list
    opens on a click, and there it is full.
    """
    head = main_part(parts)
    poster = head.get("poster") or next((p["poster"] for p in parts if p.get("poster")), "")
    return {
        "id": fid,
        "title": head["title"],
        "title_en": head.get("title_en") or "",
        "poster": poster,
        "year": head.get("year"),
        "kind_ru": head.get("kind_ru") or "",
        "episodes": head.get("episodes"),
        "score": head.get("score"),
    }


def group_found(found: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    """Sorts what was found into franchises, keeping the order of the results.

    The groups are ordered by how early the group's first part appeared
    in the catalogue's answer. The catalogue has already sorted the
    answer by closeness to the query, and there is no reason to reorder
    it by our own devices.
    """
    order: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for part in found:
        k = group_key(part)
        if k not in grouped:
            grouped[k] = []
            order.append(k)
        grouped[k].append(part)
    return order, grouped


async def search_franchises(q: str) -> list[dict] | None:
    """Search through the catalogue: one card per franchise.

    Exactly one request to the outside. Unfolding every franchise found
    here in full is tempting and wrong: the query "битва" brings back
    eleven of them, and the person would wait for all eleven lists to
    load in order to read the first line and click it. Lists load on a
    click, one at a time.

    None means "the catalogue did not answer" — which is not the same as
    an empty list. On an empty list the site will say "nothing found",
    while on None it goes off to search the old way, straight at the
    video sources.
    """
    q = (q or "").strip()[:100]
    if len(q) < 2:
        return []
    key = "find:" + q.lower()
    hit = _cache_get(key)
    if hit is not None:
        return hit

    data = await _ask_shiki_gql(GQL_SEARCH, {"s": q})
    nodes = (data or {}).get("animes")
    if nodes is None:
        found = await _anilist_search(q)
        if found is None:
            return None
    else:
        found = [p for p in (pack_part(n) for n in nodes)
                 if p and p["kind"] not in JUNK_KINDS]

    if not found:
        _cache_put(key, [])
        return []

    order, grouped = group_found(found)
    cards = []
    for k in order:
        # What was found for this group we put in the cache in advance:
        # if the catalogue goes down between the search and the click,
        # the list of parts will open anyway — incomplete, perhaps, but
        # not empty.
        _cache_put("part:" + k, sort_parts(grouped[k]))
        cards.append(franchise_card(k, grouped[k]))
    _cache_put(key, cards)
    return cards


async def _franchise_raw(fid: str) -> list[dict] | None:
    """The franchise's parts as they are — with no marks, straight from cache or network."""
    hit = _cache_get("fr:" + fid)
    if hit is not None:
        return hit

    # Labels with a colon are assembled by us, not by the catalogue:
    # "title:…" is a group of one title, "al:…" is a splice from the
    # fallback catalogue. Asking Shikimori about those is pointless, it
    # knows no such thing.
    if ":" not in fid:
        full = await _franchise_pages(fid)
        if full:
            _cache_put("fr:" + fid, full)
            return full

    known = _cache_get("part:" + fid)
    if known:
        return known
    # The search cache fell apart — we will gather the group again by
    # the name that stayed in the label itself.
    if ":" in fid and await search_franchises(fid.split(":", 1)[1]) is not None:
        return _cache_get("part:" + fid) or []
    return None


async def franchise_parts(fid: str) -> list[dict] | None:
    """Every part of one franchise in order of release.

    Copies leave for the outside, not the records from the cache: the
    marks — "start" here and "watching now" on the watch page — are each
    answer's own business, while the list is shared by everybody.
    Mutating a shared cache for each person is a sure way to show one
    person what another one chose.
    """
    fid = (fid or "").strip()[:80]
    if not fid:
        return None
    parts = await _franchise_raw(fid)
    if parts is None:
        return None
    # The mark is placed by position, so the sorting must already be
    # applied. Parts come from the cache sorted; sorting once more is
    # cheaper than forgetting once.
    return mark_main(sort_parts([dict(p) for p in parts]))


# --------------------------------------------------------------------------
# Parts of the same story — for the watch page
# --------------------------------------------------------------------------
# Search answers the question "what to watch". This piece answers a
# different one, which arises once you are in the player: "which season
# is this and what comes next".
#
# There used to be no answer anywhere. A title that landed on a shelf
# lived there as a separate record with one name, and there was nowhere
# to learn it had a second season and three films — you had to remember
# they existed and look for them by hand.


async def related_parts(title: str) -> list[dict] | None:
    """Every part of the story this title belongs to.

    In: the name as the video source or the shelf knows it; out: the
    whole franchise list, in which the part open right now is marked with
    the current field.

    None means "the catalogue did not answer": on such an answer the page
    simply does not show the block, rather than showing an empty one.
    """
    title = (title or "").strip()[:120]
    if len(title) < 2:
        return []

    cards = await search_franchises(title)
    if cards is None:
        return None
    if not cards:
        return []

    # The catalogue sorts its answer by closeness to the query, so the
    # franchise wanted is the first one. There is nothing to check that
    # with by our own devices: the card has only the main part's name in
    # hand, while the match may be with any of them.
    out = await franchise_parts(cards[0]["id"])
    if out is None:
        return None

    best = None
    best_score = 0.0
    for part in out:
        score = max(anime.relevance(title, part["title"]),
                    anime.relevance(title, part.get("title_en") or ""))
        if score > best_score:
            best, best_score = part, score
    for part in out:
        part["current"] = part is best and best_score >= anime.MIN_RELEVANCE
    return out


# --------------------------------------------------------------------------
# The fallback catalogue
# --------------------------------------------------------------------------
# AniList has no franchise label at all — there the links between parts
# are kept as graph edges (sequel, prequel, side story), and gathering a
# franchise out of them would mean walking the graph with a separate
# request per part. For a fallback path that switches on once a year that
# is far too expensive.
#
# So parts here are gathered by name: "Магическая битва", "Магическая
# битва 2", "Магическая битва 0" — a shared beginning. The split such a
# method gives is rougher, but when the main catalogue is down the choice
# is not between good and rough, it is between rough and an empty screen.

ANILIST_KIND = {
    "TV": "tv", "TV_SHORT": "tv", "MOVIE": "movie", "OVA": "ova",
    "ONA": "ona", "SPECIAL": "special", "MUSIC": "music",
}

Q_FIND = ("query ($s: String) { Page(page: 1, perPage: 25) {"
          " media(type: ANIME, search: $s, isAdult: false) {"
          " id title { romaji english native } seasonYear episodes format"
          " averageScore coverImage { large medium } } } }")


def _base_name(title: str) -> str:
    """The name without the season tail: "Jujutsu Kaisen 2nd Season" →
    "jujutsu kaisen". Parts are glued into one franchise by it."""
    text = re.split(r"\s*[:—–]\s*", title or "")[0].strip().lower()
    text = re.sub(r"\s*(?:\d+(?:st|nd|rd|th)?|[ivx]+)?\s*"
                  r"(?:season|part|movie|film|ova|ona|special|special edition)\s*\d*$",
                  "", text).strip()
    text = re.sub(r"\s+\d+$", "", text).strip()
    return text or (title or "").strip().lower()


async def _anilist_search(q: str) -> list[dict] | None:
    """The same answer as the main catalogue's, but from the fallback."""
    data = await _ask_anilist(Q_FIND, {"s": q})
    media = ((data or {}).get("Page") or {}).get("media")
    if media is None:
        return None
    out: list[dict] = []
    for m in media:
        if not isinstance(m, dict):
            continue
        titles = m.get("title") or {}
        name = (titles.get("romaji") or titles.get("english")
                or titles.get("native") or "")
        if not name:
            continue
        kind = ANILIST_KIND.get(m.get("format") or "", "")
        if kind in JUNK_KINDS:
            continue
        cover = m.get("coverImage") or {}
        score = m.get("averageScore")
        out.append({
            "id": str(m.get("id") or ""),
            "title": name,
            "title_en": titles.get("english") or "",
            # The label glues the parts of one story to each other and
            # means nothing outside this answer — hence the prefix.
            "franchise": "al:" + _base_name(name),
            "year": m.get("seasonYear"),
            "kind": kind,
            "kind_ru": KIND_RU.get(m.get("format") or "", kind),
            "episodes": m.get("episodes") or None,
            "poster": cover.get("large") or cover.get("medium") or "",
            "score": int(score) if isinstance(score, int) else None,
        })
    return out
