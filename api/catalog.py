"""Справочник аниме: описания, жанры и случайный выбор.

Источники, с которых берётся видео, знают только «название и серии» —
ни описания, ни жанров в удобном виде у них нет. За справочными данными
ходим отдельно.

Основной справочник — Shikimori: он русскоязычный, и описание с жанрами
приходят сразу на русском. Это важнее, чем кажется: английское описание
под плеером читать никто не станет, а «Horror, Supernatural» вместо
«Ужасы, Мистика» не отвечает на вопрос «а что это вообще такое».

Запасной — AniList, на случай если Shikimori молчит. Его ответ на
английском, поэтому жанры переводятся по словарю ниже, а описание
показывается как есть: лучше английское, чем никакого.

Почему через свой сервер, а не прямо из браузера:

  * иначе адрес каждого посетителя уходил бы на чужой сайт при каждом
    открытии страницы — это ровно то, чего сайт не делает нигде больше;
  * ответы можно класть в общий кэш: один запрос на всех вместо одного
    на каждого;
  * чужой ответ разбирается здесь, и наружу уходит только то, что нам
    нужно, — без разметки, без ссылок, без лишних полей.

Ключей и регистрации ни один из них не требует.
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

# Мера совпадения названий живёт рядом с источниками видео: она нужна
# и там, и здесь — понять, какая из частей франшизы открыта сейчас.
from . import anime

log = logging.getLogger("anime.catalog")

SHIKI = "https://shikimori.one/api"
ANILIST = "https://graphql.anilist.co"
TIMEOUT = 12.0
# Shikimori просит представляться. Без этого он вправе отвечать отказом.
#
# Только латиница: заголовки HTTP передаются однобайтовой кодировкой, и
# кириллица в них роняет запрос ещё до отправки — UnicodeEncodeError.
# Ошибка при этом выглядела безобидно: «справочник не ответил», и всё
# молча уходило к запасному англоязычному. То есть русские описания
# не работали вовсе, а причина пряталась в собственном заголовке.
HEADERS = {"User-Agent": "animedik/1.0 (personal home site)"}

# Сколько ждём между обращениями к справочникам. Доступ свободный, но
# злоупотреблять им нельзя: превысим — забанят по адресу сервера,
# и справочник отвалится у всех сразу.
MIN_GAP = 0.6
_last_call = [0.0]
_gate = asyncio.Lock()

_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 24 * 3600          # описания меняются раз в год, если вообще
CACHE_MAX = 500

# Случайное берём из первых страниц по популярности. Дальше начинается
# то, о чём никто не слышал, — «случайное аниме» из такого превращается
# в список неизвестных названий и перестаёт быть интересным.
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
    """Не частим к чужому сайту.

    Замок нужен, потому что запросов может идти несколько разом: без него
    все они увидели бы одно и то же «последнее обращение» и ушли наружу
    одновременно, то есть ограничение не сработало бы вовсе.
    """
    async with _gate:
        gap = MIN_GAP - (time.time() - _last_call[0])
        if gap > 0:
            await asyncio.sleep(gap)
        _last_call[0] = time.time()


async def _get(url: str, params: dict | None = None) -> Any:
    """Обычный запрос. Любая беда — тихо возвращаем ничего.

    Справочник это приятное дополнение, а не то, без чего сайт не
    работает: он не должен ронять страницу просмотра, если у него
    профилактика.
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
# Чистка чужого текста
# --------------------------------------------------------------------------
# Shikimori пишет описания своей разметкой: [b]жирный[/b],
# [url=...]ссылка[/url], [character=123]имя[/character], [spoiler]...
# AniList — обычным html. Всё это уходит в браузер текстом, поэтому
# показалось бы как есть, посреди предложения.
_BB_SPOILER = re.compile(r"\[spoiler[^\]]*\].*?\[/spoiler\]", re.S | re.I)
_BB_PAIR = re.compile(r"\[(\w+)(?:=[^\]]*)?\](.*?)\[/\1\]", re.S)
_BB_ANY = re.compile(r"\[/?[^\]]{0,60}\]")
_HTML_TAG = re.compile(r"<[^>]+>")
_JUNK = re.compile(r"\((?:source|written by|источник)[^)]*\)", re.I)

# Обороты, за которыми в описаниях обычно начинается пересказ сюжета.
_SPOILER_WORDS = re.compile(
    r"(?:however|but then|it turns out|in the end|finally,|"
    r"однако|но затем|оказывается|в финале|в конце концов|"
    r"но всё меняется|но однажды всё)", re.I)

DESC_MAX = 420


def clean_description(raw: str | None) -> str:
    """Короткое описание без разметки и без спойлеров.

    Берём только начало: первые предложения — это завязка, ради которой
    и читают. Дальше начинается пересказ, а с ним и то, что портит
    просмотр.
    """
    if not raw:
        return ""
    text = str(raw)
    # 1. Куски, прямо помеченные как спойлер, — целиком вон.
    text = _BB_SPOILER.sub(" ", text)
    # 2. Парные метки: оставляем содержимое, метки убираем.
    for _ in range(3):                              # вложенность бывает
        new = _BB_PAIR.sub(r"\2", text)
        if new == text:
            break
        text = new
    # 3. Остатки одиночных меток и html.
    text = _BB_ANY.sub(" ", text)
    text = html.unescape(_HTML_TAG.sub(" ", text))
    text = _JUNK.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Режем по первому обороту, за которым обычно идёт поворот сюжета.
    cut = _SPOILER_WORDS.search(text)
    if cut and cut.start() > 120:
        text = text[:cut.start()].strip()

    if len(text) <= DESC_MAX:
        return text.rstrip(" ,;:—-")
    # Обрезаем по границе предложения, а не посреди слова.
    head = text[:DESC_MAX]
    dot = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if dot > 160:
        return head[:dot + 1].strip()
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip(" ,;:—-") + "…"


# Жанры AniList приходят по-английски. Список у него закрытый и короткий,
# поэтому словаря достаточно — гадать ничего не нужно.
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
# Разбор ответов
# --------------------------------------------------------------------------
KIND_RU = {
    "tv": "сериал", "movie": "фильм", "ova": "OVA", "ona": "ONA",
    "special": "спешл", "music": "клип",
    # Пересказы и спецвыпуски, показанные по телевизору, приходят
    # отдельной меткой. Без перевода она уезжала в интерфейс как есть —
    # «tv_special» посреди русского списка частей.
    "tv_special": "спецвыпуск", "pv": "трейлер", "cm": "реклама",
    "TV": "сериал", "MOVIE": "фильм", "OVA": "OVA", "ONA": "ONA",
    "SPECIAL": "спешл", "MUSIC": "клип", "TV_SHORT": "короткий сериал",
}


SHIKI_HOST = "https://shikimori.one"


def _poster_url(tail: object) -> str:
    """Адрес обложки — или пустая строка.

    Раньше домен дописывался всему, что не начинается с "http". Строка
    "javascript:alert(1)" превращалась в "https://shikimori.onejavascript:..."
    — мусор, собранный из чужого ответа. Признаём только две формы:
    путь от корня и готовый https-адрес.
    """
    if not isinstance(tail, str):
        return ""
    tail = tail.strip()
    if not tail:
        return ""
    if tail.startswith("/") and not tail.startswith("//"):
        return SHIKI_HOST + tail
    if tail.startswith("https://"):
        return tail
    return ""


def _whole(value: object) -> int | None:
    """Целое число или ничего.

    Справочник — чужой сервер. Если вместо количества серий придёт
    "много", а вместо года — "недавно", это уедет прямо на страницу.
    Всё, что не приводится к целому, превращается в «неизвестно»:
    отсутствие числа страница переживает, а мусор вместо числа — нет.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 100000 else None


def pack_shiki(a: dict | None, full: dict | None = None) -> dict | None:
    """Ответ Shikimori. Он русскоязычный, переводить ничего не нужно."""
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
        poster = _poster_url(image.get("original") or image.get("preview"))
    return {
        "title": name,
        "title_en": a.get("name") or "",
        "year": year,
        "episodes": _whole(a.get("episodes")) or _whole(a.get("episodes_aired")),
        "kind": KIND_RU.get(a.get("kind", ""), a.get("kind") or ""),
        "genres": genres,
        "about": clean_description(d.get("description")),
        "poster": poster,
        # У Shikimori оценка десятибалльная, приводим к сотне: так же,
        # как у запасного справочника, чтобы показывать одинаково.
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
    """Ответ запасного справочника. Жанры переводим, описание оставляем."""
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
        "year": _whole(media.get("seasonYear")),
        "episodes": _whole(media.get("episodes")),
        "kind": KIND_RU.get(media.get("format", ""), media.get("format") or ""),
        "genres": [ru_genre(g) for g in (media.get("genres") or [])][:5],
        "about": clean_description(media.get("description")),
        "poster": _poster_url(cover.get("large")) or _poster_url(cover.get("medium")),
        "score": int(score) if isinstance(score, int) else None,
    }


# --------------------------------------------------------------------------
# Упрощение названия
# --------------------------------------------------------------------------
def simpler_titles(title: str) -> list[str]:
    """Варианты названия от полного к самому короткому.

    Источники видео пишут название по-своему: «Магическая битва 2»,
    «Магическая битва 0 Фильм», «Магическая академия Атараксия: Гибрид
    x Сердце». Справочник таких строк не знает — он знает «Магическая
    битва». Без упрощения описание не находилось у всего, что вышло
    больше одного сезона, а это добрая половина списка.
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
# То, что вызывают ручки
# --------------------------------------------------------------------------
async def _shiki_about(title: str) -> dict | None:
    found = await _get(SHIKI + "/animes", {"search": title, "limit": 1, "censored": "true"})
    if not isinstance(found, list) or not found:
        return None
    brief = found[0]
    # Жанры и описание лежат только в подробной карточке.
    full = await _get(SHIKI + "/animes/" + str(brief.get("id")))
    return pack_shiki(brief, full if isinstance(full, dict) else None)


async def about(title: str, lang: str = "ru") -> dict | None:
    """Справка по названию: описание, год, жанры, оценка.

    Сначала русский справочник, потом запасной. Если полное название не
    нашлось — пробуем упрощённые варианты (см. simpler_titles).

    lang="en" переворачивает порядок: первым спрашивается AniList, он
    англоязычный. Русское описание человеку, переключившему сайт на
    английский, бесполезно ровно так же, как английское — русскому.
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
        # Русского описания нет — спросим запасной, вдруг там есть.
        data = await _ask_anilist(Q_SEARCH, {"s": variant})
        fallback = pack_anilist((data or {}).get("Media"))
        if fallback and fallback.get("about"):
            # Название и жанры всё равно предпочитаем русские.
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
    """То же, но по-английски: сначала AniList, русский справочник запасным.

    У AniList описание и жанры на английском изначально, переводить
    ничего не нужно. Если он молчит, берём русскую карточку — с ней
    хотя бы год, число серий и обложка будут верными.
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
                # Жанры у нас переводятся на русский при разборе — здесь
                # это ровно то, чего не надо.
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
    """Случайное аниме из справочника — не из вашего списка."""
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
# Поиск по франшизам
# ==========================================================================
# Чем этот поиск отличается от того, что был.
#
# Раньше в строку поиска уходил запрос к источнику видео, и человек
# получал то, что этот источник считает похожим. У «Наруто» это
# выглядело так: один источник знает «Наруто Ураганные хроники» и
# «Боруто», но не знает «Наруто». Другой вываливает двадцать одну строку
# вперемешку: сначала «Боруто: Фильм», потом второй сезон, потом спешл
# про спортивный фестиваль. Где здесь сам «Наруто» и в каком порядке всё
# это смотреть — по такому списку не понять никак.
#
# Причина простая: у сайтов с видео нет понятия «франшиза». Каждый сезон,
# фильм и OVA лежат у них отдельными записями с произвольными названиями,
# и связи между ними не хранится нигде.
#
# А у справочника она есть. У Shikimori каждая карточка помечена полем
# franchise: и «Наруто», и «Ураганные хроники», и все фильмы со спешлами
# несут одну метку «naruto». Поэтому поиск теперь идёт по справочнику:
# на запрос возвращается одна карточка на франшизу, а внутри неё — все
# части по порядку выхода.
#
# Shikimori выбран основным, а не AniList, по двум причинам: он отдаёт
# русские названия (сайт русский, и «Атака титанов» читается лучше, чем
# «Shingeki no Kyojin»), и метка франшизы есть только у него. AniList
# остаётся запасным на случай, когда Shikimori не отвечает; франшиз он
# не знает, поэтому там части собираются по совпадению начала названия —
# хуже, но лучше, чем ничего.

SHIKI_GQL = "https://shikimori.one/api/graphql"

# Метки, которые не имеют отношения к просмотру: трейлеры, рекламные
# ролики и музыкальные клипы. В списке частей они только мешают — их
# не смотрят, а место в списке занимают.
JUNK_KINDS = {"pv", "cm", "music"}

# Сколько частей франшизы справочник отдаёт за один раз. Полсотни —
# его собственный потолок, больше он не отдаст при всём желании.
FRANCHISE_LIMIT = 50

GQL_FIELDS = """
    id name russian franchise kind episodes episodesAired status score
    airedOn { year }
    poster { mainUrl originalUrl }
"""


async def _ask_shiki_gql(query: str, variables: dict | None = None) -> dict | None:
    """Запрос к GraphQL справочника. Молчание — не ошибка, а None."""
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
    """Одна часть франшизы: сезон, фильм, OVA или спешл."""
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
        # У выходящего сейчас сериала общее число серий не проставлено:
        # «Ван-Пис» шёл двадцать шесть лет, и сколько их будет всего, не
        # знает никто. Тогда показываем, сколько вышло.
        "episodes": node.get("episodes") or node.get("episodesAired") or None,
        "ongoing": node.get("status") == "ongoing",
        "poster": (poster.get("mainUrl") or poster.get("originalUrl") or ""),
        "score": int(round(score * 10)) if score else None,
    }


# Внутри одного года сериал идёт первым, дальше по убыванию «основного»:
# то, что смотрят, выше того, что смотрят после. Точнее года справочник
# дату не отдаёт, поэтому упорядочить 2013 год «по дням выхода» нечем —
# а упорядочить по смыслу можно.
KIND_RANK = {"tv": 0, "ona": 1, "movie": 2, "tv_special": 3, "ova": 3,
             "special": 4}


def _sort_key(part: dict) -> tuple:
    """Порядок частей — по выходу.

    Ещё не вышедшее уезжает в конец: года у него нет, и подставлять ноль
    нельзя — иначе анонс встал бы впереди первого сезона.

    Ровесники внутри года разбираются по виду. Без этого у «Атаки
    титанов» первыми четырьмя строками шли OVA, театральная миниатюра и
    пересказ — всё 2013 года, — а сам первый сезон стоял четвёртым.
    Формально верно, читается как список чего угодно, кроме того, за чем
    пришли.
    """
    rank = KIND_RANK.get(part.get("kind") or "", 5)
    size = -(part.get("episodes") or 0)
    return (0, part["year"], rank, size) if part.get("year") else (1, 0, rank, size)


def sort_parts(parts: list[dict]) -> list[dict]:
    return sorted(parts, key=_sort_key)


def main_part(parts: list[dict]) -> dict:
    """Часть, по которой франшиза узнаётся.

    Это почти всегда первый сериал: «Наруто», а не «Наруто: Ураганные
    хроники» и не «Боруто».

    Сериалы отбираются строго, отдельной ступенью, а не вместе с
    околосериальным. Иначе выходит вот что: у «Атаки титанов» первый
    сезон и нарезка-пересказ вышли в один и тот же год, нарезка помечена
    как tv_special, и при сортировке по одному только году франшиза
    называлась «Атака титанов: Рекап». У «Судьбы» тем же образом наверх
    вылезал спешл про «Великий приказ».

    Внутри ступени при равном годе побеждает то, где больше серий:
    основная история почти всегда длиннее сопровождающего её спешла.
    """
    for tier in ([p for p in parts if p.get("kind") == "tv"],
                 [p for p in parts if p.get("kind") in ("tv_special", "ona")],
                 parts):
        if tier:
            return sorted(tier, key=lambda p: (_sort_key(p),
                                               -(p.get("episodes") or 0)))[0]
    return parts[0]


def mark_main(parts: list[dict]) -> list[dict]:
    """Помечает часть, с которой начинают смотреть.

    Правило простое: самая ранняя. Список и так отсортирован по годам,
    поэтому отметка встаёт на первую строку — что написано в списке, то
    и помечено, разночтений нет.

    Здесь стояло правило похитрее: «первый сериал франшизы». Придумано
    оно было ради «Ван-Пис», у которого самое раннее — OVA 1998 года,
    снятая до сериала, а сам сериал вышел годом позже. Правило и правда
    отправляло к сериалу, но ценой того, что отметка «начало» уезжала
    со строки, которая в списке первая, — а объяснить это глядящему на
    список нечем.

    Внутри одного года порядок не случаен (см. _sort_key): сериал стоит
    выше OVA и пересказов. Поэтому у «Атаки титанов», где в 2013 году
    вышли и сериал, и OVA, и нарезка, отметка всё равно на сериале.
    """
    if not parts:
        return parts
    for i, part in enumerate(parts):
        part["main"] = i == 0
    return parts


def group_key(part: dict) -> str:
    """Под какой меткой часть попадёт в общую карточку.

    У большинства карточек справочника метка франшизы проставлена. Там,
    где её нет (одиночные фильмы, свежие анонсы), меткой становится само
    название: такая франшиза состоит из одной части, и это правда.
    """
    return part.get("franchise") or ("title:" + part["title"].lower())


async def _franchise_pages(fid: str) -> list[dict] | None:
    """Все части одной франшизы, обеими страницами сразу.

    Ограничение справочника — полсотни карточек за раз, а порядок у него
    от новых к старым. На этом обжигаешься не сразу: у «Наруто» частей
    двадцать девять, всё влезает, и кажется, что так и надо. А у «Ван-Пис»
    их девяносто — и первые полсотни это 2015–2027 годы, то есть сам
    «Ван-Пис» 1999 года в ответ не попадает вовсе. Франшиза называлась
    «Ван-Пис: Остров Рыболюдей», и первая серия в списке была из середины.

    Поэтому страниц берётся две. Обе — одним запросом, через псевдонимы
    полей: два обращения к чужому сайту заняли бы вдвое больше времени
    ровно ни за чем.
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
    """Карточка франшизы: то, что видно в выдаче до нажатия.

    Числа в карточке — про главную часть, а не про франшизу целиком: год
    выхода первого сезона и сколько в нём серий. Это то, что знает поиск,
    не спрашивая справочник второй раз.

    Соблазн написать здесь «29 частей» был, и от него пришлось отказаться:
    при поиске известны не все части франшизы, а только те, что попали
    в ответ на запрос. Число вышло бы то верным, то нет, а проверить его
    глазами нельзя — как раз тот случай, когда лучше не писать ничего.
    Полный список открывается по нажатию, и там он полный.
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
    """Раскладывает найденное по франшизам, сохраняя порядок выдачи.

    Порядок групп — по тому, как рано в ответе справочника встретилась
    первая часть группы. Справочник уже отсортировал ответ по близости
    к запросу, и переупорядочивать его своими силами незачем.
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
    """Поиск по справочнику: одна карточка на франшизу.

    Ровно один запрос наружу. Раскрывать здесь каждую найденную франшизу
    целиком — заманчиво и неправильно: на запрос «битва» их приходит
    одиннадцать, и человек ждал бы, пока подгрузятся все одиннадцать
    списков, чтобы прочитать первую строку и нажать на неё. Списки
    подгружаются по нажатию, по одному.

    None означает «справочник не ответил» — это не то же самое, что
    пустой список. По пустому списку сайт скажет «ничего не нашлось»,
    а по None уйдёт искать старым способом, прямо у источников видео.
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
        # Найденное по этой группе кладём в кэш заранее: если справочник
        # ляжет между поиском и нажатием, список частей всё равно
        # откроется — пусть неполный, но не пустой.
        _cache_put("part:" + k, sort_parts(grouped[k]))
        cards.append(franchise_card(k, grouped[k]))
    _cache_put(key, cards)
    return cards


async def _franchise_raw(fid: str) -> list[dict] | None:
    """Части франшизы как они есть — без пометок, прямо из кэша или сети."""
    hit = _cache_get("fr:" + fid)
    if hit is not None:
        return hit

    # Метки с двоеточием собраны нами, а не справочником: «title:…» —
    # группа из одного тайтла, «al:…» — склейка запасного справочника.
    # Спрашивать про них Shikimori бессмысленно, он таких не знает.
    if ":" not in fid:
        full = await _franchise_pages(fid)
        if full:
            _cache_put("fr:" + fid, full)
            return full

    known = _cache_get("part:" + fid)
    if known:
        return known
    # Кэш поиска рассыпался — соберём группу заново по названию, которое
    # осталось в самой метке.
    if ":" in fid and await search_franchises(fid.split(":", 1)[1]) is not None:
        return _cache_get("part:" + fid) or []
    return None


async def franchise_parts(fid: str) -> list[dict] | None:
    """Все части одной франшизы по порядку выхода.

    Наружу уходят копии, а не записи из кэша: пометки — «начало» здесь и
    «сейчас смотрите» на странице просмотра — своё дело каждого ответа,
    а список лежит один на всех. Мутировать общий кэш под каждого —
    верный способ показать одному человеку то, что выбрал другой.
    """
    fid = (fid or "").strip()[:80]
    if not fid:
        return None
    parts = await _franchise_raw(fid)
    if parts is None:
        return None
    # Отметка ставится по порядку, поэтому сортировка обязана быть уже
    # применена. Из кэша части приходят отсортированными; пересортировать
    # ещё раз дешевле, чем однажды забыть.
    return mark_main(sort_parts([dict(p) for p in parts]))


# --------------------------------------------------------------------------
# Части той же истории — для страницы просмотра
# --------------------------------------------------------------------------
# Поиск отвечает на вопрос «что посмотреть». Этот кусок отвечает на другой,
# который возникает уже в плеере: «а это какой сезон и что дальше».
#
# Раньше ответа не было нигде. Тайтл, попавший в полку, жил там отдельной
# записью с одним названием, и узнать, что у него есть второй сезон и три
# фильма, было неоткуда — надо было вспомнить, что они существуют, и
# поискать их руками.


async def related_parts(title: str) -> list[dict] | None:
    """Все части истории, к которой относится этот тайтл.

    На вход — название так, как его знает источник видео или полка;
    на выход — весь список франшизы, где часть, открытая сейчас, помечена
    полем current.

    None означает «справочник не ответил»: страница по такому ответу
    просто не покажет блок, а не покажет пустой.
    """
    title = (title or "").strip()[:120]
    if len(title) < 2:
        return []

    cards = await search_franchises(title)
    if cards is None:
        return None
    if not cards:
        return []

    # Справочник сортирует ответ по близости к запросу, поэтому нужная
    # франшиза — первая. Проверять это ещё раз своими силами нечем: у
    # карточки на руках только название главной части, а совпасть должно
    # с любой из них.
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
# Запасной справочник
# --------------------------------------------------------------------------
# У AniList метки франшизы нет вовсе — там связи между частями хранятся
# рёбрами графа (продолжение, приквел, побочная история), и чтобы собрать
# из них франшизу, пришлось бы обходить граф отдельными запросами на
# каждую часть. Ради запасного пути, который включается раз в год, это
# слишком дорого.
#
# Поэтому здесь части собираются по названию: «Магическая битва»,
# «Магическая битва 2», «Магическая битва 0» — общее начало. Разбивку
# такой способ даёт грубее, но когда основной справочник лежит, выбор
# стоит не между хорошим и грубым, а между грубым и пустым экраном.

ANILIST_KIND = {
    "TV": "tv", "TV_SHORT": "tv", "MOVIE": "movie", "OVA": "ova",
    "ONA": "ona", "SPECIAL": "special", "MUSIC": "music",
}

Q_FIND = ("query ($s: String) { Page(page: 1, perPage: 25) {"
          " media(type: ANIME, search: $s, isAdult: false) {"
          " id title { romaji english native } seasonYear episodes format"
          " averageScore coverImage { large medium } } } }")


def _base_name(title: str) -> str:
    """Название без хвоста сезона: «Jujutsu Kaisen 2nd Season» → «jujutsu
    kaisen». По нему части и склеиваются в одну франшизу."""
    text = re.split(r"\s*[:—–]\s*", title or "")[0].strip().lower()
    text = re.sub(r"\s*(?:\d+(?:st|nd|rd|th)?|[ivx]+)?\s*"
                  r"(?:season|part|movie|film|ova|ona|special|special edition)\s*\d*$",
                  "", text).strip()
    text = re.sub(r"\s+\d+$", "", text).strip()
    return text or (title or "").strip().lower()


async def _anilist_search(q: str) -> list[dict] | None:
    """Тот же ответ, что у основного справочника, но из запасного."""
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
            # Метка склеивает части одной истории между собой и ничего
            # не значит за пределами этого ответа — отсюда и приставка.
            "franchise": "al:" + _base_name(name),
            "year": m.get("seasonYear"),
            "kind": kind,
            "kind_ru": KIND_RU.get(m.get("format") or "", kind),
            "episodes": m.get("episodes") or None,
            "poster": cover.get("large") or cover.get("medium") or "",
            "score": int(score) if isinstance(score, int) else None,
        })
    return out
