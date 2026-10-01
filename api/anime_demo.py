"""Демонстрационный источник: свободное видео вместо чужого.

Зачем он есть.

Сайт умеет искать тайтлы, раскрывать франшизы по частям, помнить, на
какой секунде вы остановились, и проигрывать поток. Показать это можно
двумя способами: дать доступ к чужому видео — или взять видео, которое
разрешено брать.

Публичная версия берёт второе. В демонстрационном режиме плеер играет
фильмы Blender Foundation: они выпущены под Creative Commons, их можно
показывать, встраивать и раздавать. Плеер при этом настоящий — с HLS,
перемоткой, выбором качества и дорожек, — потому что играет настоящее
видео, а не заглушку.

Справочник (описания, годы, постеры, связи между сезонами) остаётся
рабочим: это открытые данные, и брать их можно.

Как включается: переменная окружения MODE=demo. Тогда `anime.py`
подставляет этот модуль вместо работы с внешними сайтами, и остальной
код разницы не замечает — формат ответов тот же.
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


# Дорожки, которые сайт покажет в списке «Озвучка».
#
# Их две не для вида: на одной видно, что работает HLS — поток, который
# собирается из кусков и умеет менять качество на ходу, — а на второй,
# что работает обычный файл. Это две разные ветки кода в плеере, и обе
# стоит показать живыми.
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
    """Какой ролик отдать на этот запрос.

    Ролик выбирается по самому запросу, а не наугад: одному и тому же
    названию всегда достаётся один и тот же. Иначе список «Продолжить
    смотреть» вёл бы каждый раз на другое видео, и было бы непонятно,
    это задумано или сломалось.
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
    """Одна дорожка: HLS-поток или обычный файл."""

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
        # Несколько «серий» у одного ролика — чтобы было видно, как
        # работает список серий, переход к следующей и отметка о
        # просмотре. Играет при этом одно и то же видео: выдумывать
        # разные было бы неоткуда.
        return [Episode(self._clip, n) for n in range(1, self._clip["episodes"] + 1)]


class Search:
    def __init__(self, clip: dict, asked: str = "") -> None:
        self._clip = clip
        # Название показываем то, которое искали: карточка в поиске
        # должна отвечать запросу, иначе выдача выглядит сломанной.
        # Чем на самом деле окажется видео, честно написано в плеере.
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
    """Тот же вид, что у настоящих источников: один метод поиска."""

    async def a_search(self, query: str) -> list[Search]:
        return [Search(pick_clip(query), query)]


def clip_by_key(key: str) -> dict:
    """Ролик по сохранённому ключу — для ссылок из списка и закладок."""
    return _BY_ID.get(str(key), CLIPS[0])
