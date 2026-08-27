"""The demonstration source: free video instead of someone else's.

Why it exists.

The site can search for titles, unfold a franchise into its parts,
remember the second you stopped at and play a stream. There are two ways
to show that: give access to someone else's video — or take video you
are allowed to take.

The public version takes the second. In demonstration mode the player
runs Blender Foundation films: they are released under Creative Commons
and may be shown, embedded and shared. The player is the real one — with
HLS, seeking, quality and track selection — because it plays real video
rather than a placeholder.

The catalogue (descriptions, years, posters, links between seasons)
stays fully working: that is open data, and taking it is allowed.

How to turn it on: the MODE=demo environment variable. Then `anime.py`
puts this module in place of the work with external sites, and the rest
of the code notices no difference — the shape of the answers is the same.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("anime.demo")

NAME = "demo"

SOURCES: dict[str, dict[str, Any]] = {
    NAME: {
        "lang": "ru",
        "label": "Демо",
        "base": "https://www.blender.org",
        "dubs": "many",
        "note": "Свободное видео Blender Foundation (Creative Commons)",
    },
}


# Tracks the site will show in the "Dub" list.
#
# There are two of them, and not for show: one demonstrates that HLS
# works — a stream assembled from chunks that can change quality on the
# fly — and the other that a plain file works. Those are two different
# branches of code in the player, and both are worth showing alive.
CLIPS = [
    {
        "id": "bunny",
        "title": "Big Buck Bunny",
        "year": 2008,
        "author": "Blender Foundation",
        "licence": "CC BY 3.0",
        "episodes": 3,
        "tracks": [
            {
                "name": "HLS-поток",
                "videos": [
                    {"quality": 720,
                     "url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
                     "type": "m3u8"},
                ],
            },
            {
                "name": "Обычный файл",
                "videos": [
                    {"quality": 1080,
                     "url": "https://media.w3.org/2010/05/bunny/movie.mp4",
                     "type": "mp4"},
                    {"quality": 480,
                     "url": "https://media.w3.org/2010/05/bunny/trailer.mp4",
                     "type": "mp4"},
                ],
            },
        ],
    },
    {
        "id": "sintel",
        "title": "Sintel",
        "year": 2010,
        "author": "Blender Foundation",
        "licence": "CC BY 3.0",
        "episodes": 1,
        "tracks": [
            {
                "name": "Обычный файл",
                "videos": [
                    {"quality": 720,
                     "url": "https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4",
                     "type": "mp4"},
                    {"quality": 480,
                     "url": "https://media.w3.org/2010/05/sintel/trailer.mp4",
                     "type": "mp4"},
                ],
            },
        ],
    },
    {
        "id": "steel",
        "title": "Tears of Steel",
        "year": 2012,
        "author": "Blender Foundation",
        "licence": "CC BY 3.0",
        "episodes": 1,
        "tracks": [
            {
                "name": "HLS-поток",
                "videos": [
                    {"quality": 720,
                     "url": "https://test-streams.mux.dev/test_001/stream.m3u8",
                     "type": "m3u8"},
                ],
            },
        ],
    },
]

_BY_ID = {c["id"]: c for c in CLIPS}


def pick_clip(query: str) -> dict:
    """Which clip to hand out for this query.

    The clip is chosen from the query itself rather than at random: the
    same name always gets the same one. Otherwise the "Continue
    watching" list would lead to a different video every time, and it
    would be unclear whether that was intended or broken.
    """
    text = (query or "").strip().lower()
    if not text:
        return CLIPS[0]
    return CLIPS[sum(ord(c) for c in text) % len(CLIPS)]


class Video:
    def __init__(self, row: dict) -> None:
        self.url = row["url"]
        self.quality = row["quality"]
        self.type = row["type"]


class Source:
    """A single track: an HLS stream or a plain file."""

    def __init__(self, track: dict) -> None:
        self.title = track["name"]
        self._videos = track["videos"]

    async def a_get_videos(self) -> list[Video]:
        return [Video(v) for v in self._videos]


class Episode:
    def __init__(self, clip: dict, ordinal: int) -> None:
        self.ordinal = ordinal
        self.title = ""
        self._clip = clip

    async def a_get_sources(self) -> list[Source]:
        return [Source(t) for t in self._clip["tracks"]]


class Anime:
    def __init__(self, clip: dict) -> None:
        self._clip = clip
        self.title = clip["title"]
        self.url = clip["id"]
        self.data = {"id": clip["id"]}

    async def a_get_episodes(self) -> list[Episode]:
        # Several "episodes" for one clip — so that the episode list,
        # moving to the next one and the watch mark are all visible in
        # action. The same video plays behind them: there would be
        # nowhere to invent different ones from.
        return [Episode(self._clip, n) for n in range(1, self._clip["episodes"] + 1)]


class Search:
    def __init__(self, clip: dict, asked: str = "") -> None:
        self._clip = clip
        # We show the name that was searched for: a card in the search
        # results has to answer the query, otherwise the results look
        # broken. What the video actually turns out to be is written
        # honestly in the player.
        self.title = asked or clip["title"]
        self.url = clip["id"]
        self.thumbnail = ""
        self.data = {
            "id": clip["id"],
            "year": clip["year"],
            "genres": clip["licence"] + "," + clip["author"],
        }

    async def a_get_anime(self) -> Anime:
        return Anime(self._clip)


class Extractor:
    """The same shape as the real sources: one search method."""

    async def a_search(self, query: str) -> list[Search]:
        return [Search(pick_clip(query), query)]


def clip_by_key(key: str) -> dict:
    """A clip by stored key — for links from the library and bookmarks."""
    return _BY_ID.get(str(key), CLIPS[0])
