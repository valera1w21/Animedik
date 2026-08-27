"""Talking to anime sources.

The code comes from the working version almost unchanged: it is tested
and does exactly what is needed. One thing is different — this module no
longer builds a web application, it only returns data.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from fastapi import HTTPException

from . import anime_demo

log = logging.getLogger("anime.sources")

# Operating mode. Private by default: the site goes out to external
# sources.
#
# MODE=demo switches it to demonstration mode: instead of someone else's
# video the player runs Blender Foundation films under Creative Commons.
# Everything else — catalogue search, franchises, the library, watch
# progress — works as usual, because catalogue data is free.
#
# The switch lives here, in one place: the rest of the code must not
# know where the video came from, otherwise the difference spreads
# across the whole project and one day the wrong source leaks somewhere.
MODE = os.getenv("MODE", "private").strip().lower()
DEMO = MODE == "demo"


def upstream_error(exc: Exception, what: str) -> HTTPException:
    """Details to the log, a general phrase to the outside.

    The exception text can contain internal addresses and fragments of
    someone else's response. There is no reason to hand that to a browser.
    """
    log.warning("%s: %s: %s", what, type(exc).__name__, exc)
    return HTTPException(status_code=502, detail=what)


SOURCES: dict[str, dict[str, Any]] = {}
"""Where the site takes video from.

This is empty, and deliberately so. The public version has no external
sources: the application runs in demonstration mode and plays free video
by Blender Foundation (see `anime_demo.py`).

If you want to plug in your own source, two things are needed.

1. Add an entry here:

       SOURCES["my_source"] = {
           "lang": "ru",              # language of the dubs: ru or en
           "label": "My source",      # how to show it in the menu
           "base": "https://…",       # address image paths are glued to
           "dubs": "many",            # one or many
           "note": "A short note",
       }

2. Write a module with four objects — the same way `anime_demo.py` does
   it, which you can take as a template:

       Extractor.a_search(query)  -> [Search]
       Search.a_get_anime()       -> Anime
       Anime.a_get_episodes()     -> [Episode]
       Episode.a_get_sources()    -> [Source]
       Source.a_get_videos()      -> [Video(url, quality, type)]

   and return it from `get_extractor` below.

Everything else — search, franchises, the library, watch progress, the
player — works on top of this interface and knows nothing about the
source itself.

Whatever you plug in, you answer for it.
"""



if DEMO:
    # In demonstration mode there is exactly one source, and it is the
    # same for both languages: free video is neither "Russian" nor
    # "English".
    SOURCES = dict(anime_demo.SOURCES)


def sources_for(lang: str) -> list[str]:
    """Sources that speak the same language as the site.

    Why the split. Sources each speak their own language: the Russian
    ones give Russian dubs, an English-language one gives English
    subtitles and an English dub. While they sat in one heap, a person
    who opened the site in English got "source A — one dub" in the menu
    and, choosing it, Japanese speech under a Russian dub. The reverse
    held too: a Russian speaker was offered an English-language source
    where everything is in English.

    Neither is the source's fault — we were showing it something it
    never asked for. Now the site language decides what there is to
    choose from at all.
    """
    if DEMO:
        return list(SOURCES)
    want = "en" if lang == "en" else "ru"
    return [sid for sid, meta in SOURCES.items() if meta.get("lang", "ru") == want]


def source_lang(source: str) -> str:
    """What language this source speaks."""
    return SOURCES.get(source, {}).get("lang", "ru")


# Default source for each language. Empty: there is nothing to plug in,
# and every request goes to demonstration mode.
DEFAULT_SOURCE: dict[str, str] = {}


def default_source(lang: str) -> str:
    if DEMO:
        return anime_demo.NAME
    return DEFAULT_SOURCE.get("en" if lang == "en" else "ru", anime_demo.NAME)


_extractors: dict[str, Any] = {}


def get_extractor(source: str):
    """Fetches the source's parser. The source name must come from SOURCES.

    Loading the module is wrapped on purpose. It used not to be — and any
    trouble with it (package not installed, an update renamed the module,
    an error inside the module) flew up as an ImportError and was shown
    to the person as "Internal error" on our side. Meanwhile the very
    same trouble during search was handled properly, because there the
    whole call is wrapped from the outside. So search honestly said "the
    source is not responding", while the episode list of that same
    source said "everything is broken".
    """
    if source not in SOURCES:
        raise HTTPException(status_code=400, detail=f"Неизвестный источник: {source}")
    if source not in _extractors:
        try:
            if DEMO:
                _extractors[source] = anime_demo.Extractor()
            else:
                # Your source's parser gets returned here. Until there is
                # one, only demonstration mode remains.
                raise HTTPException(
                    status_code=501,
                    detail="Источники видео не подключены. "
                           "Работает демонстрационный режим (MODE=demo).",
                )
        except HTTPException:
            raise
        except Exception as exc:                   # noqa: BLE001
            raise upstream_error(exc, f"Источник {source} сейчас недоступен")
    return _extractors[source]


# ----------------------------------------------------------------- cache
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 3 * 60 * 60  # three hours
CACHE_MAX = 800


def cache_put(key: str, value: Any) -> None:
    if len(_cache) > CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: CACHE_MAX // 2]
        for k, _ in oldest:
            _cache.pop(k, None)
    _cache[key] = (time.time(), value)


def cache_get(key: str) -> Any:
    row = _cache.get(key)
    if not row:
        return None
    born, value = row
    if time.time() - born > CACHE_TTL:
        _cache.pop(key, None)
        return None
    return value


# ------------------------------------------------------------- helpers
def stable_key(item: Any) -> str:
    """The title's permanent number at the source. This is what we store."""
    data = getattr(item, "data", None)
    if isinstance(data, dict):
        for field in ("id", "slug_url", "alias", "code", "url"):
            if data.get(field):
                return str(data[field])
    return str(getattr(item, "url", "") or "")


def absolute(source: str, path: str | None) -> str:
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return SOURCES[source]["base"] + path


def first_of(data: dict, *fields) -> Any:
    for f in fields:
        value = data.get(f)
        if value not in (None, "", 0):
            return value
    return None


def dig_genres(data: dict) -> str:
    """Digs out the genres. Every source keeps them its own way:
    somewhere a list of strings, somewhere a list of dicts with a name."""
    raw = first_of(data, "genres", "genre", "categories", "tags")
    names: list[str] = []
    if isinstance(raw, str):
        names = [g.strip() for g in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        for g in raw:
            if isinstance(g, str):
                names.append(g.strip())
            elif isinstance(g, dict):
                n = g.get("name") or g.get("title") or g.get("genre")
                if isinstance(n, dict):
                    n = n.get("main") or n.get("ru") or n.get("en")
                if n:
                    names.append(str(n).strip())
    return ",".join(dict.fromkeys(n for n in names if n))[:300]


def dig_year(data: dict) -> Any:
    """Release year. Sometimes a number, sometimes a dict with a year field."""
    value = first_of(data, "year", "release_year", "aired_on", "season")
    if isinstance(value, dict):
        value = value.get("year") or value.get("value")
    if isinstance(value, str):
        digits = "".join(c for c in value if c.isdigit())[:4]
        value = int(digits) if len(digits) == 4 else None
    if isinstance(value, int) and 1900 <= value <= 2200:
        return value
    return None


def dig_poster(data: dict, fallback: str = "") -> str:
    """Looks for the largest cover image.

    Sources keep several sizes side by side: a thumbnail and the full
    picture. The thumbnail used to be taken, and on a card it looked
    blurry. The field order below runs from larger to smaller.
    """
    obj = data.get("poster") or data.get("image") or data.get("cover")
    if isinstance(obj, str) and obj:
        return obj
    if isinstance(obj, dict):
        for field in ("src", "original", "preview", "optimized", "thumbnail"):
            value = obj.get(field)
            if isinstance(value, dict):
                value = value.get("src") or value.get("preview")
            if isinstance(value, str) and value:
                return value
    for field in ("poster_url", "image_url", "cover_url"):
        value = data.get(field)
        if isinstance(value, str) and value:
            return value
    return fallback


def pack(source: str, item: Any) -> dict:
    data = getattr(item, "data", None)
    data = data if isinstance(data, dict) else {}
    poster = dig_poster(data, getattr(item, "thumbnail", "") or "")
    eps = first_of(data, "episodes_total", "episodes_count", "count_episodes", "episodes")
    if isinstance(eps, (list, tuple)):
        eps = len(eps)
    if not isinstance(eps, int):
        eps = None
    return {
        "key": stable_key(item),
        "title": getattr(item, "title", "") or "Без названия",
        "poster": absolute(source, poster),
        "year": dig_year(data),
        "genres": dig_genres(data),
        "episodes_total": eps,
    }


async def find_anime(source: str, key: str, title: str):
    """Takes the Anime object from the cache, and searches again if it is gone.

    There was a quiet but expensive mistake here. Search put every title
    it found under the key `raw:source:number`, while this function
    looked under `anime:source:number`. They never matched: nobody read
    the `raw:` entries, they only took up cache space (one search of
    thirty results — thirty one entries against a ceiling of eight
    hundred) and pushed everything useful out. As a result every visit to
    an episode list went out to the external site again, although the
    needed object was lying right there.

    Now the blank left by the search is used for what it is for.
    """
    cached = cache_get(f"anime:{source}:{key}")
    if cached is not None:
        return cached

    # The blank left over from the search: the title is already found,
    # there is no reason to go to the network.
    raw = cache_get(f"raw:{source}:{key}")
    if raw is not None:
        try:
            anime = await raw.a_get_anime()
        except Exception as exc:                   # noqa: BLE001
            log.info("заготовка не развернулась: %s", type(exc).__name__)
        else:
            cache_put(f"anime:{source}:{key}", anime)
            return anime

    if not title:
        raise HTTPException(
            status_code=409,
            detail="Тайтл выпал из памяти сервера. Передай ещё и title, либо найди его заново.",
        )

    extractor = get_extractor(source)
    try:
        results = await extractor.a_search(title)
    except Exception as exc:
        raise upstream_error(exc, "Источник не ответил")

    match = next((r for r in results if stable_key(r) == str(key)), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail="Не нашёл этот тайтл у источника. Возможно, его убрали — выбери заново.",
        )

    # This call goes to the network too, and it used to be the only one
    # in the whole file left unwrapped. Any source failure here flew up
    # as an internal server error: the person saw "Internal error" and
    # decided the site was broken, when it was someone else's site that
    # had not answered.
    try:
        anime = await match.a_get_anime()
    except Exception as exc:                       # noqa: BLE001
        raise upstream_error(exc, "Источник не ответил")
    cache_put(f"anime:{source}:{key}", anime)
    return anime


async def find_poster(source: str, key: str, title: str) -> str:
    """Looks for a title's cover image at the source.

    Needed when a title landed in the library without a picture: from a
    bookmark, from someone else's link, or because an older version saved
    it. Such a title used to keep a grey rectangle forever — nobody ever
    asked for the cover again.

    First we look in the search cache: parsed cards are already there,
    and no network call is needed at all. Only if nothing is found — one
    search by name.
    """
    raw = cache_get(f"raw:{source}:{key}")
    if raw is not None:
        packed = pack(source, raw)
        if packed.get("poster"):
            return packed["poster"]

    if not title:
        return ""

    key_s = str(key)
    cached = cache_get(f"poster:{source}:{key_s}")
    if cached is not None:
        return cached

    extractor = get_extractor(source)
    try:
        results = await extractor.a_search(title)
    except Exception as exc:                       # noqa: BLE001
        log.info("обложка: источник %s не ответил: %s", source, type(exc).__name__)
        return ""

    found = ""
    for r in results:
        if stable_key(r) == key_s:
            found = pack(source, r).get("poster") or ""
            break
    # We cache even an empty answer: otherwise the page will go to the
    # network for the same non-existent picture every time it opens.
    cache_put(f"poster:{source}:{key_s}", found)
    return found


async def find_episodes(source: str, key: str, title: str) -> list:
    cached = cache_get(f"eps:{source}:{key}")
    if cached is not None:
        return cached

    anime = await find_anime(source, key, title)
    try:
        episodes = await anime.a_get_episodes()
    except Exception as exc:
        raise upstream_error(exc, "Не удалось получить серии")

    episodes = list(episodes)
    cache_put(f"eps:{source}:{key}", episodes)
    return episodes


# --------------------------------------------------------- title matching
# Why this is here.
#
# Search at a source is not search, it is "show me something similar".
# For the query "Attack on Titan" source A and source B both answer with
# a single line: "Don't Toy With Me, Miss Nagatoro: Second Attack". The
# word "attack" matched, and that was enough for them. And the walk over
# sources stopped at the first one that answered with anything at all —
# that is, at this very Nagatoro. Further down the list sat source C,
# which has "Attack on Titan" with every season, but its turn never came.
#
# So a source's answer is now weighed: how close is the name to what was
# asked for. Junk sinks, and a source with nothing similar counts as not
# having answered.

_TAIL_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_PUNCT = re.compile(r"[^\w\s]+", re.U)


def normalize_title(text: str) -> str:
    """The name in a form fit for comparison.

    Source E writes names like this: "Наруто / Naruto [1-220 из 220]" —
    the Russian name, the Latin name and an episode counter on one line.
    Comparing against that is pointless, so the tails are cut off:
    counters in brackets, and after a slash the same title's second name.
    """
    text = (text or "").strip()
    text = _TAIL_BRACKETS.sub(" ", text)
    head = text.split(" / ")[0] if " / " in text else text
    head = head.lower().replace("ё", "е")
    head = _PUNCT.sub(" ", head)
    return re.sub(r"\s+", " ", head).strip()


def relevance(query: str, title: str) -> float:
    """How well the name answers the query: from 0 to 1.

    The numbers are picked so that "Naruto" for the query "naruto" ranks
    above "Boruto: Naruto Next Generations", and "Don't Toy With Me,
    Miss Nagatoro: Second Attack" for the query "attack on titan" does
    not pass the threshold at all.
    """
    q = normalize_title(query)
    t = normalize_title(title)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    if t.startswith(q):
        # The whole query at the start of the name: "naruto" → "naruto
        # shippuden". We count extra words, not extra letters: one word
        # of tail is usually a note like "(TV)", while two or three mean
        # a different season or a different story.
        #
        # The measure used to be by letters, and that broke silently:
        # "Naruto Shippuden" scored 0.855 for the query "Naruto" — above
        # the threshold at which the walk over sources stopped. So a
        # person picked the first season in the catalogue and the second
        # one opened, which looked exactly like the trouble being fixed.
        extra = max(0, len(t.split()) - len(q.split()))
        return max(0.72, 0.90 - 0.06 * extra)
    if q in t:
        return 0.70
    if len(t) >= 4 and q.startswith(t + " "):
        # The other way round: the source writes the name SHORTER than
        # the catalogue. "Neon Genesis Evangelion" sits at all three
        # sources simply as "Evangelion", and by words that gave 1 out of
        # 3 — below the threshold. The title exists everywhere, and the
        # site answered "no source has it".
        #
        # The score is deliberately low, and not out of caution for
        # caution's sake. There is no way to tell "Neon Genesis
        # Evangelion" → "Evangelion" (the same thing, just shorter) from
        # "Naruto Shippuden" → "Naruto" (a different season) by strings
        # alone. So such a match passes the threshold but does not count
        # as exact: if a real match turns up nearby, it wins, and if not,
        # the page shows what was found and asks whether that is it.
        return 0.50
    q_words = q.split()
    t_words = t.split()

    def known(word: str) -> bool:
        for other in t_words:
            if other == word:
                return True
            # A prefix of four letters or more catches inflections:
            # "титанов" and "титаны" are the same word, while "атака"
            # and "академия" are different ones.
            if len(word) >= 4 and len(other) >= 4:
                if other.startswith(word) or word.startswith(other):
                    return True
        return False

    hit = sum(1 for w in q_words if known(w))
    return 0.62 * hit / len(q_words)


# Below this score a source's answer counts as beside the point. Half the
# query's words give 0.31 — "Second Attack" for "Attack on Titan" is
# filtered out. One word out of one gives 0.62 and passes.
MIN_RELEVANCE = 0.45
