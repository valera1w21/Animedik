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
import hashlib
import html
import json
import logging
import random
import re
import time
import unicodedata
from typing import Any

import httpx

# Мера совпадения названий живёт рядом с источниками видео: она нужна
# и там, и здесь — понять, какая из частей франшизы открыта сейчас.
from . import anime, netguard
from .store import clean_poster, is_placeholder

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

_cache: dict[str, tuple[float, Any, float]] = {}
CACHE_TTL = 24 * 3600          # описания меняются раз в год, если вообще
# Было 500 (fix-21): франшизы, описания, поиски, обложки и случайные
# страницы делят один кэш, и на длинной сессии он вытеснял франшизу,
# открытую пять минут назад, — снова поход к справочнику с паузой MIN_GAP.
CACHE_MAX = 3000
# Честное «не нашлось» тоже запоминаем, но ненадолго: справочник могут
# пополнить, а мы могли спросить неудачно. Сбой сети не запоминаем вовсе
# (см. _remember) — раньше пустой ответ после сбоя лежал в кэше сутки, и
# сутки у тайтла не было описания, хотя справочник давно ожил.
NOT_FOUND_TTL = 30 * 60

# Случайное берём из первых страниц по популярности. Дальше начинается
# то, о чём никто не слышал, — «случайное аниме» из такого превращается
# в список неизвестных названий и перестаёт быть интересным.
RANDOM_PAGES = 220
# Сериалов в жанре Ecchi у справочника — пара сотен; берём из первых
# шестидесяти по популярности, чтобы не попадать в совсем безвестное.
RANDOM_GENRE_PAGES = 60


def _cache_get(key: str) -> Any:
    row = _cache.get(key)
    if not row:
        return None
    born, value, ttl = row
    if time.time() - born > ttl:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: Any, ttl: float = CACHE_TTL) -> None:
    if len(_cache) > CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: CACHE_MAX // 2]
        for k, _ in oldest:
            _cache.pop(k, None)
    _cache[key] = (time.time(), value, ttl)


def _remember(key: str, packed: dict | None, failed: bool) -> None:
    """Кладёт справку в кэш — но только если ей можно верить.

    Три случая, и раньше они были неотличимы — всё ложилось пустым {}
    на сутки:
      * нашлось с описанием — запоминаем надолго;
      * справочники честно ответили «такого нет» — запоминаем ненадолго;
      * справочник не ответил (сеть, профилактика, 5xx) — не запоминаем
        ничего, иначе сутки показывали бы «описания нет» из-за минутного
        сбоя. Неполная карточка (без описания) после сбоя — тоже.
    """
    if packed and (packed.get("about") or not failed):
        _cache_put(key, packed)
    elif not packed and not failed:
        _cache_put(key, {}, NOT_FOUND_TTL)


# --------------------------------------------------------------------------
# Чужие данные не того вида
# --------------------------------------------------------------------------
# Ответ справочника — чужие данные. Раньше разбор верил, что название —
# строка, синонимы — список, число серий — число: одна запись, где вместо
# строки пришёл список («kind»: [...]) или вместо списка число
# («synonyms»: 5), роняла разбор ВСЕГО ответа (TypeError) — и поиск,
# франшиза или справка отвечали «внутренней ошибкой» всем, пока ответ
# лежал в кэше справочника. Теперь такая запись пропускается, а остальные
# показываются (проверка — check41_sources.py, группа 11).
def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _strs(value: object) -> list[str]:
    return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _tolerant(fn):
    """Разборщик одной записи: запись не того вида — None, а не падение."""
    import functools

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (TypeError, AttributeError, ValueError, KeyError, IndexError) as exc:
            log.info("справочник: запись не того вида (%s) — пропускаем", type(exc).__name__)
            return None
    return wrapped


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


# Самый большой ответ справочника, который готовы держать в памяти.
# Настоящие — десятки и сотни килобайт (две страницы франшизы по
# полсотни частей); восемь мегабайт — с огромным запасом. Раньше ответ
# читался целиком, сколько бы его ни прислали.
MAX_RESPONSE = 8 * 1024 * 1024


async def _send(method: str, url: str, **kw: Any) -> tuple[int, Any]:
    """Запрос к справочнику: (код ответа, разобранный JSON или None).

    Адреса справочников записаны в коде, но ответ может переадресовать
    куда угодно — поэтому и здесь работает сторож (netguard), а ответ
    читается с потолком. Прокси из окружения не берётся (trust_env).
    """
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True,
                                 max_redirects=3, trust_env=False) as client:
        async with client.stream(method, url, **kw) as r:
            raw = await netguard.read_capped(r, MAX_RESPONSE)
            status = r.status_code
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


async def _get(url: str, params: dict | None = None) -> Any:
    """Обычный запрос. Любая беда — тихо возвращаем ничего.

    Справочник это приятное дополнение, а не то, без чего сайт не
    работает: он не должен ронять страницу просмотра, если у него
    профилактика.
    """
    await _pause()
    try:
        status, body = await _send("GET", url, params=params)
        if status != 200:
            log.info("справочник ответил %s на %s", status, url)
            return None
        return body
    except Exception as exc:                        # noqa: BLE001
        log.info("справочник не ответил: %s", type(exc).__name__)
        return None


async def _ask_anilist(query: str, variables: dict) -> dict | None:
    await _pause()
    try:
        status, body = await _send("POST", ANILIST,
                                   json={"query": query, "variables": variables})
        body = body if isinstance(body, dict) else {}
        if status == 404:
            # Так AniList отвечает на «ничего не нашлось»: 404 и data с
            # пустым Media. Это ответ, а не сбой, — отличаем его от
            # молчания, чтобы не путать «нет такого» с «не дозвонились».
            data = body.get("data")
            return data if isinstance(data, dict) else None
        if status != 200:
            return None
        data = body.get("data")
        return data if isinstance(data, dict) and data else None
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
# Сколько чужого текста вообще разбирать. Наружу уходит DESC_MAX знаков
# от начала, так что хвост длинного описания не нужен ни для чего, — а
# метки [spoiler] и [b]…[/b] без пары на огромном тексте разбирались бы
# время, растущее как квадрат длины, прямо в цикле событий.
DESC_INPUT_MAX = 6000


def clean_description(raw: str | None) -> str:
    """Короткое описание без разметки и без спойлеров.

    Берём только начало: первые предложения — это завязка, ради которой
    и читают. Дальше начинается пересказ, а с ним и то, что портит
    просмотр.
    """
    if not raw:
        return ""
    text = str(raw)[:DESC_INPUT_MAX]
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
    # Заглушку «нет картинки» Shikimori отдаёт обычным путём
    # (/assets/globals/missing/main.png). Для нас это «обложки нет»:
    # иначе карточка честно показывала бы серый квадрат справочника,
    # и до запасных вариантов дело бы не доходило.
    if is_placeholder(tail):
        return ""
    if tail.startswith("/") and not tail.startswith("//"):
        return SHIKI_HOST + tail
    if tail.startswith("https://"):
        return clean_poster(tail)
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


def catalog_id(shiki_id: object = None, anilist_id: object = None,
               mal_id: object = None) -> str:
    """Номер тайтла для полки (store.clean_cid): «mal:<id>».

    У Shikimori номера те же, что у MyAnimeList, у AniList свои — но он
    обычно знает и номер MAL (idMal). Поэтому одно и то же аниме из обоих
    справочников получает один номер, и в полке одна карточка. Номера MAL
    у записи AniList нет — «al:<id>»."""
    for raw, pre in ((shiki_id, "mal:"), (mal_id, "mal:"), (anilist_id, "al:")):
        v = str(raw or "").strip()
        if v.isdigit():
            return pre + v
    return ""


@_tolerant
def pack_shiki(a: dict | None, full: dict | None = None,
               hentai: bool = False) -> dict | None:
    """Ответ Shikimori. Он русскоязычный, переводить ничего не нужно.

    Хентай (rx) — только при hentai=True (его спрашивали без цензуры) и
    без признаков несовершеннолетних (minors_flagged)."""
    if not a:
        return None
    name = a.get("russian") or a.get("name") or ""
    if not name:
        return None
    d = full or a
    rating = _rating(d.get("rating") or a.get("rating"))
    if rating in HENTAI_RATINGS:
        # В подробной карточке названия — то строкой, то списком
        # (english, japanese, synonyms): собираем всё в один список.
        names: list = [name, a.get("name"), d.get("license_name_ru")]
        for field in ("english", "japanese", "synonyms"):
            v = d.get(field)
            names += v if isinstance(v, list) else [v]
        if not hentai:
            return None
        if minors_flagged(d.get("genres") or [], names, d.get("description") or ""):
            _rx_mark(names, False)          # см. rx_cleared: источник хентая по этим именам не пойдёт
            return None
    raw = [g for g in (d.get("genres") or []) if isinstance(g, dict)]
    genres = [g for g in (x.get("russian") or x.get("name") or "" for x in raw) if g][:5]
    # Английские имена жанров у Shikimori свои (поле name): для английской
    # страницы (fix-21). Русские — для русской, как и было.
    genres_en = [g for g in (x.get("name") or "" for x in raw) if g][:5]
    # Официальное английское название (english) — в подробной карточке,
    # строкой или списком. Нет — латинское (name).
    eng = d.get("english")
    eng = eng[0] if isinstance(eng, list) and eng else eng
    eng = eng if isinstance(eng, str) and eng.strip() else ""
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
        "cid": catalog_id(a.get("id")),
        "title_eng": eng.strip(),
        "genres_en": genres_en,
        "year": year,
        "episodes": _whole(a.get("episodes")) or _whole(a.get("episodes_aired")),
        "kind": KIND_RU.get(a.get("kind", ""), a.get("kind") or ""),
        "genres": genres,
        "about": clean_description(d.get("description")),
        # На каком языке описание. Страница по нему подписывает
        # «Описание на английском», если пришлось взять запасное.
        "about_lang": "ru",
        "poster": poster,
        # У Shikimori оценка десятибалльная, приводим к сотне: так же,
        # как у запасного справочника, чтобы показывать одинаково.
        "score": int(round(score * 10)) if score else None,
        # Рейтинг есть только в подробной карточке. Не пришла — неизвестно,
        # и тогда тайтл не прячется: прятать по догадке нечестно.
        "rating": rating,
    }


MEDIA_FIELDS = """
  id idMal title { romaji english native }
  seasonYear episodes format genres
  description(asHtml: false) coverImage { extraLarge large medium }
  averageScore isAdult
"""
Q_SEARCH = "query ($s: String) { Media(type: ANIME, search: $s, isAdult: false) {" + MEDIA_FIELDS + "} }"
# Для того, у кого включён хентай: без условия на isAdult, с тегами — для
# проверки на несовершеннолетних (minors_flagged).
Q_SEARCH_X = ("query ($s: String) { Media(type: ANIME, search: $s) {" + MEDIA_FIELDS
              + " synonyms tags { name } } }")
Q_RANDOM = ("query ($p: Int) { Page(page: $p, perPage: 1) {"
            " media(type: ANIME, sort: POPULARITY_DESC, isAdult: false) {" + MEDIA_FIELDS + "} } }")


@_tolerant
def pack_anilist(media: dict | None, hentai: bool = False) -> dict | None:
    """Ответ запасного справочника. Жанры переводим, описание оставляем.
    Хентай (isAdult) — только при hentai=True и без признаков
    несовершеннолетних."""
    if not media or (media.get("isAdult") and (not hentai or _anilist_minors(media))):
        if media and media.get("isAdult") and hentai:
            t = media.get("title") or {}
            t = t if isinstance(t, dict) else {}
            _rx_mark([t.get("romaji"), t.get("english")] + list(media.get("synonyms") or []),
                     False)
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
        "cid": catalog_id(anilist_id=media.get("id"), mal_id=media.get("idMal")),
        "genres_en": [g for g in (media.get("genres") or []) if isinstance(g, str)][:5],
        "year": _whole(media.get("seasonYear")),
        "episodes": _whole(media.get("episodes")),
        "kind": KIND_RU.get(media.get("format", ""), media.get("format") or ""),
        "genres": [ru_genre(g) for g in (media.get("genres") or [])][:5],
        "about": clean_description(media.get("description")),
        "about_lang": "en",
        "poster": (_poster_url(cover.get("extraLarge")) or _poster_url(cover.get("large"))
                   or _poster_url(cover.get("medium"))),
        "score": int(score) if isinstance(score, int) else None,
        "rating": anilist_rating(media),
    }


def anilist_rating(media: dict) -> str:
    """Рейтинг для записи AniList. Своей шкалы у него нет — есть только
    признак isAdult (хентай: rx, только при включённом «Хентай») и жанр
    Ecchi (этти — то же, что r_plus у Shikimori: прячется, пока не
    включено 18+)."""
    if media.get("isAdult"):
        return "rx"
    if "Ecchi" in (media.get("genres") or []):
        return "r_plus"
    return ""


# --------------------------------------------------------------------------
# Упрощение названия
# --------------------------------------------------------------------------
# Хвост сезона или вида: «2», «II», «3 сезон», «Фильм», «TV 2».
#
# Хвост обязан быть отдельным словом — перед ним пробел или начало
# строки. Раньше этого не требовалось, а флаг re.I делал [IVX] ещё и
# строчными, и регулярка откусывала конец обычных слов: «Mushoku Tensei»
# превращалось в «Mushoku Tense», «Persona» — в «Pers» (хвост «ona»).
# Справочник по таким огрызкам находил что угодно, кроме нужного.
# Римские цифры — только заглавными: (?-i:…) выключает re.I внутри.
_TAIL = re.compile(
    r"(?:^|\s+)(?:\d+|(?-i:[IVX]+))(?:\s*(?:сезон|season))?\s*$|"
    r"(?:^|\s+)(?:фильм|movie|тв|tv|ova|ona|спешл|special)(?:\s*\d+)?\s*$",
    re.I)


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
            shorter = _TAIL.sub("", cut).strip()
            if shorter == cut:
                break
            cut = shorter
        if cut and cut not in out and len(cut) >= 3:
            out.append(cut)
    return out[:3]                                  # больше трёх запросов не делаем


# --------------------------------------------------------------------------
# Третий справочник: Jikan (открытая обёртка над MyAnimeList)
# --------------------------------------------------------------------------
# Включается последним — когда ни Shikimori, ни AniList ничего не дали.
# У MyAnimeList самая большая база: там есть и старые OVA, и то, что
# вышло вчера, а у первых двух такое бывает пустым. Описание у него
# только по-английски, поэтому и помечаем его так же, как AniList.
JIKAN = "https://api.jikan.moe/v4"
# Jikan просит не больше трёх запросов в секунду и шестидесяти в минуту.
# Свой замок и шаг чуть больше секунды — так не выйти ни за одно, ни за
# другое, сколько бы страниц ни спрашивало разом: запросы встают в
# очередь и идут строго по одному.
JIKAN_GAP = 1.05
_jikan_last = [0.0]
_jikan_gate = asyncio.Lock()

JIKAN_KIND = {"TV": "tv", "MOVIE": "movie", "OVA": "ova", "ONA": "ona",
              "SPECIAL": "special", "TV SPECIAL": "tv_special", "MUSIC": "music",
              "PV": "pv", "CM": "cm"}

_CYRILLIC = re.compile(r"[а-яё]", re.I)


def _jikan_names(d: dict) -> list[str]:
    """Все названия записи MyAnimeList: основное, английское, синонимы."""
    names = [d.get("title"), d.get("title_english"), d.get("title_japanese")]
    for t in d.get("titles") or []:
        if isinstance(t, dict):
            names.append(t.get("title"))
    names += [s for s in (d.get("title_synonyms") or []) if isinstance(s, str)]
    out: list[str] = []
    for n in names:
        if isinstance(n, str) and n.strip() and n.strip() not in out:
            out.append(n.strip())
    return out


# Рейтинг MyAnimeList словами — к шкале Shikimori.
JIKAN_RATING = {"g": "g", "pg": "pg", "pg-13": "pg_13", "r": "r", "r+": "r_plus", "rx": "rx"}


def jikan_rating(d: dict) -> str:
    """«Rx - Hentai» → rx, «R+ - Mild Nudity» → r_plus, «PG-13 - …» → pg_13."""
    raw = d.get("rating") if isinstance(d.get("rating"), str) else ""
    return JIKAN_RATING.get(raw.split(" - ")[0].strip().lower(), "")


@_tolerant
def pack_jikan(d: dict | None, hentai: bool = False) -> dict | None:
    """Ответ Jikan в том же виде, что и у двух других справочников.
    Хентай — как у остальных: только при hentai=True и без признаков
    несовершеннолетних."""
    if not isinstance(d, dict):
        return None
    name = d.get("title") or d.get("title_english") or ""
    if not isinstance(name, str) or not name.strip():
        return None
    rating = jikan_rating(d)
    if rating in HENTAI_RATINGS and (not hentai or minors_flagged(
            [g for k in ("genres", "explicit_genres", "themes", "demographics")
             for g in (d.get(k) or []) if isinstance(g, dict)],
            _jikan_names(d), d.get("synopsis") or "")):
        if hentai:
            _rx_mark(_jikan_names(d), False)
        return None
    year = _whole(d.get("year"))
    if not year:
        aired = d.get("aired") if isinstance(d.get("aired"), dict) else {}
        start = ((aired.get("prop") or {}).get("from") or {}) if isinstance(aired.get("prop"), dict) else {}
        year = _whole(start.get("year")) if isinstance(start, dict) else None
    images = d.get("images") if isinstance(d.get("images"), dict) else {}
    webp = images.get("webp") if isinstance(images.get("webp"), dict) else {}
    jpg = images.get("jpg") if isinstance(images.get("jpg"), dict) else {}
    poster = (_poster_url(webp.get("large_image_url")) or _poster_url(jpg.get("large_image_url"))
              or _poster_url(webp.get("image_url")) or _poster_url(jpg.get("image_url")))
    try:
        score = float(d.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    kind = JIKAN_KIND.get(str(d.get("type") or "").upper(), "")
    raw_genres = [g.get("name") for g in (d.get("genres") or [])
                  if isinstance(g, dict) and isinstance(g.get("name"), str)][:5]
    genres = [ru_genre(g) for g in raw_genres]
    return {
        "title": name.strip(),
        "title_en": d.get("title_english") or "",
        "genres_en": raw_genres,
        "year": year,
        "episodes": _whole(d.get("episodes")),
        "kind": KIND_RU.get(kind, kind),
        "genres": genres,
        "about": clean_description(d.get("synopsis")),
        "about_lang": "en",
        "poster": poster,
        "score": int(round(score * 10)) if score else None,
        "rating": rating,
    }


async def _ask_jikan(q: str, hentai: bool = False) -> list[tuple[dict, list[str]]] | None:
    """Поиск у Jikan: [(карточка, все её названия)], [] — честно ничего,
    None — не дозвонились (сбой не путаем с «не нашлось», см. _remember).
    sfw=true прячет хентай на стороне Jikan; без него — только при hentai."""
    async with _jikan_gate:
        gap = JIKAN_GAP - (time.time() - _jikan_last[0])
        if gap > 0:
            await asyncio.sleep(gap)
        _jikan_last[0] = time.time()
        ask = {"q": q[:100], "limit": 5}
        if not hentai:
            ask["sfw"] = "true"
        body = await _get(JIKAN + "/anime", ask)
    if body is None:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    out = []
    for d in data if isinstance(data, list) else []:
        packed = pack_jikan(d, hentai)
        # Трейлеры, реклама и клипы — не то, что ищут (см. JUNK_KINDS).
        if packed and JIKAN_KIND.get(str(d.get("type") or "").upper()) not in JUNK_KINDS:
            out.append((packed, _jikan_names(d)))
    return out


# --------------------------------------------------------------------------
# Какой из найденных — тот самый
# --------------------------------------------------------------------------
def _year_of(value: object) -> int | None:
    n = _whole(value)
    return n if n and 1900 <= n <= 2200 else None


def _match_score(queries: list[str], names: list[str], year: int | None,
                 cand_year: object) -> float:
    """Похожесть кандидата на то, что ищем: название, а при известном
    годе — ещё и год. Совпавший год добавляет немного, чужой — заметно
    отнимает: «Хеллсинг» 2001 и «Хеллсинг» 2006 называются одинаково,
    и различить их можно только по году."""
    best = 0.0
    for q in queries:
        for n in names:
            if q and n:
                best = max(best, anime.relevance(q, n))
    cy = _year_of(cand_year)
    if best and year and cy:
        diff = abs(year - cy)
        best += 0.1 if diff == 0 else (0.03 if diff == 1 else -0.25)
    return best


def _pick(cands: list[tuple[dict, list[str], object]], queries: list[str],
          year: int | None) -> dict | None:
    """Лучший кандидат не ниже порога похожести — или ничего.

    Порог тот же, что у поиска источников (anime.MIN_RELEVANCE): чужая
    обложка или чужое описание хуже, чем никакого."""
    best, best_score = None, 0.0
    for item, names, cy in cands:
        score = _match_score(queries, names, year, cy)
        if score > best_score:
            best, best_score = item, score
    return best if best_score >= anime.MIN_RELEVANCE else None


def _names(title: str, title_en: str = "") -> list[str]:
    """Под какими именами спрашивать справочники, по порядку.

    Точное название, потом упрощённые (simpler_titles), потом латинское
    имя и его упрощение. Повторы выкидываем: каждый запрос — это
    обращение к чужому сайту."""
    out: list[str] = []
    seen: set[str] = set()
    for n in simpler_titles(title) + simpler_titles(title_en)[:2]:
        n = (n or "").strip()
        if len(n) >= 2 and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out[:5]


def _latin(names: list[str]) -> list[str]:
    """Имена без кириллицы — их понимают AniList и Jikan.

    Русское название этим двоим отдавать бесполезно и даже вредно:
    сверить ответ с запросом нечем, и наугад взятая чужая карточка
    выглядела бы как найденная."""
    out: list[str] = []
    for n in names:
        n = (n or "").strip()
        if len(n) >= 2 and not _CYRILLIC.search(n) and n.lower() not in [o.lower() for o in out]:
            out.append(n)
    return out


@_tolerant
def _anilist_ok(media: dict | None, query: str, year: int | None) -> bool:
    """Подходит ли ответ AniList к тому, что спрашивали.

    AniList на любой запрос отдаёт одну самую похожую карточку, даже
    очень непохожую. Латинский запрос сверяем с её названиями, а при
    известном годе отбрасываем карточку другого года (с запасом в год:
    премьера в декабре у разных справочников записана то так, то так)."""
    if not isinstance(media, dict):
        return False
    my = _year_of(media.get("seasonYear"))
    if year and my and abs(year - my) > 1:
        return False
    if _CYRILLIC.search(query or ""):
        return True                    # сверить не с чем — как было раньше
    titles = media.get("title") or {}
    names = [titles.get("romaji"), titles.get("english"), titles.get("native")]
    return any(anime.relevance(query, n) >= anime.MIN_RELEVANCE
               for n in names if isinstance(n, str) and n)


# --------------------------------------------------------------------------
# То, что вызывают ручки
# --------------------------------------------------------------------------
async def _shiki_about(title: str, year: int | None = None,
                       hentai: bool = False) -> tuple[dict | None, bool]:
    """Карточка из Shikimori и признак сбоя: (что нашлось, был ли сбой).

    None от _get — это сбой (сеть, не 200), а пустой список — честное
    «не нашлось». Раньше оба случая возвращались одинаковым None.

    Берём пять первых, а не одну: если известен год, среди одноимённых
    выбираем того же года («Хеллсинг» 2001 и 2006, «Охотник х Охотник»
    1999 и 2011). Без года — первая, как Shikimori и отсортировал.
    """
    found = await _get(SHIKI + "/animes", {"search": title, "limit": 5,
                                           "censored": _censored(hentai)})
    if found is None or (found and not isinstance(found, list)):
        return None, True
    if not found:
        return None, False
    brief = found[0]
    if year:
        cands = []
        for a in found:
            if isinstance(a, dict):
                aired = a.get("aired_on") or ""
                cy = aired[:4] if isinstance(aired, str) else None
                cands.append((a, [a.get("russian") or "", a.get("name") or ""], cy))
        brief = _pick(cands, [title], year) or brief
    if not isinstance(brief, dict):
        return None, False
    # Жанры и описание лежат только в подробной карточке.
    full = await _get(SHIKI + "/animes/" + str(brief.get("id")))
    full = full if isinstance(full, dict) else None
    return pack_shiki(brief, full, hentai), full is None


async def about(title: str, lang: str = "ru", title_en: str = "",
                year: object = None, hentai: bool = False) -> dict | None:
    """Справка по названию: описание, год, жанры, оценка.

    Порядок поиска описания (русская страница):
      1. Shikimori — по точному названию, по упрощённым вариантам
         (simpler_titles), по латинскому имени; если известен год —
         среди одноимённых берётся тот же год;
      2. AniList — описание английское, в ответе about_lang="en";
      3. Jikan (MyAnimeList) — тоже английское, самый последний шанс.
    Найденная раньше карточка без описания не выбрасывается: название,
    жанры и обложка берутся из неё, описание — из того, кто его дал.

    lang="en" переворачивает порядок: первым спрашивается AniList, он
    англоязычный. Русское описание человеку, переключившему сайт на
    английский, бесполезно ровно так же, как английское — русскому.

    hentai=True — для того, у кого включён хентай (решает main.py):
    справочники спрашиваются без цензуры, ответ лежит в кэше отдельно.
    """
    title = (title or "").strip()[:120]
    title_en = (title_en or "").strip()[:120]
    year = _year_of(year)
    if len(title) < 2:
        return None
    try:
        # Общий срок на всю цепочку (ABOUT_BUDGET). Не уложились — «описания
        # нет»: ничего не запоминается (_remember не дошёл), в следующий раз
        # спросим снова.
        if lang == "en":
            return await asyncio.wait_for(_about_en(title, title_en, year, hentai), ABOUT_BUDGET)
        packed, _ = await asyncio.wait_for(_about_ru(title, title_en, year, hentai), ABOUT_BUDGET)
    except asyncio.TimeoutError:
        log.info("справка не уложилась в %.0f с", ABOUT_BUDGET)
        return None
    return packed


# Сколько всего может длиться поиск справки или обложки по справочникам.
#
# Цепочка длинная: до четырёх вариантов имени у Shikimori (поиск и карточка
# на каждый), до трёх запросов к AniList и двух к Jikan, у каждого запроса
# свой срок TIMEOUT (12 с) и пауза MIN_GAP между ними. Когда справочник не
# падает, а «висит» (отвечает на пределе срока), это 16 запросов по 12
# секунд — больше трёх минут, а nginx рвёт запрос через 120 секунд: человек
# смотрел на пустое место под плеером и получал обрыв. Описание — не то,
# ради чего открыли страницу: за полминуты не нашлось — значит, нет.
ABOUT_BUDGET = 30.0
POSTER_BUDGET = 25.0


def _about_key(lang: str, title: str, title_en: str = "", year: int | None = None,
               hentai: bool = False) -> str:
    """Ключ кэша справки.

    Язык стоит отдельным полем фиксированной длины. Раньше ключи были
    "about:" + название и "about:en:" + название, и русская справка по
    названию «en:naruto» ложилась ровно туда же, где английская по
    «naruto», — одна подменяла другую.

    Латинское имя и год дописываются, только если их передали: по одному
    названию ключ тот же, что и раньше.
    """
    key = "about:" + ("en" if lang == "en" else "ru") + ":" + title.lower()
    if title_en or year:
        key += "\x00" + (title_en or "").lower() + "\x00" + str(year or "")
    return _ck(key, hentai)


def _merge(card: dict | None, other: dict) -> dict:
    """Карточка одного справочника, описание — другого.

    Название и жанры предпочитаем первой найденной (обычно это русский
    Shikimori), описание и пустые поля берём у второй. Пометка языка —
    от описания: подпись «на английском» должна быть правдой."""
    if not card:
        return dict(other)
    merged = dict(card)
    merged["about"] = other.get("about") or ""
    merged["about_lang"] = other.get("about_lang") or "en"
    for field in ("title_en", "year", "episodes", "kind", "poster", "score"):
        if not merged.get(field) and other.get(field):
            merged[field] = other[field]
    if not merged.get("genres"):
        merged["genres"] = other.get("genres") or []
    if not merged.get("genres_en"):
        merged["genres_en"] = other.get("genres_en") or []
    return merged


async def _about_ru(title: str, title_en: str = "", year: int | None = None,
                    hentai: bool = False) -> tuple[dict | None, bool]:
    """Русская справка и признак сбоя. Из кэша — без сбоя по определению."""
    key = _about_key("ru", title, title_en, year, hentai)
    hit = _cache_get(key)
    if hit is not None:
        return hit or None, False

    names = _names(title, title_en)
    card = None
    failed = False

    # 1. Shikimori: все варианты имени по очереди, пока не найдётся
    #    описание. Карточку без описания запоминаем — пригодится.
    for variant in names[:4]:
        packed, shiki_failed = await _shiki_about(variant, year, hentai)
        failed = failed or shiki_failed
        if not packed:
            continue
        if card is None or (packed.get("about") and not card.get("about")):
            card = packed
        if packed.get("about"):
            break

    # 2–3. Описания по-русски нет — английское лучше, чем никакого.
    if not (card and card.get("about")):
        # Латинское имя из найденной карточки Shikimori ценнее всего:
        # это ровно то название, под которым тайтл знают AniList и MAL.
        latin = _latin([(card or {}).get("title_en", ""), title_en] + names)
        english = None
        for variant in latin[:3]:
            data = await _ask_anilist(Q_SEARCH_X if hentai else Q_SEARCH, {"s": variant})
            if data is None:
                failed = True
                continue
            media = data.get("Media")
            if not _anilist_ok(media, variant, year):
                continue
            found = pack_anilist(media, hentai)
            if found and found.get("about"):
                english = found
                break
            card = card or found
        if english is None:
            for variant in latin[:2]:
                res = await _ask_jikan(variant, hentai)
                if res is None:
                    failed = True
                    continue
                best = _pick([(p, n, p.get("year")) for p, n in res], [variant] + latin, year)
                if best and best.get("about"):
                    english = best
                    break
                card = card or best
        if english is not None:
            card = _merge(card, english)

    _remember(key, card, failed)
    return card, failed


async def _about_en(title: str, title_en: str = "", year: int | None = None,
                    hentai: bool = False) -> dict | None:
    """То же, но по-английски: сначала AniList, русский справочник запасным.

    У AniList описание и жанры на английском изначально, переводить
    ничего не нужно. Если он молчит, берём русскую цепочку (_about_ru) —
    в ней есть и Jikan с английским описанием, а без описания с ней хотя
    бы год, число серий и обложка будут верными.
    """
    key = _about_key("en", title, title_en, year, hentai)
    hit = _cache_get(key)
    if hit is not None:
        return hit or None

    packed = None
    failed = False
    names = _names(title, title_en)
    # Латинские имена вперёд: по ним AniList находит лучше всего.
    order = _latin([title_en] + names) + [n for n in names if _CYRILLIC.search(n)]
    for variant in order[:3]:
        data = await _ask_anilist(Q_SEARCH_X if hentai else Q_SEARCH, {"s": variant})
        if data is None:
            failed = True
        media = (data or {}).get("Media")
        if media and _anilist_ok(media, variant, year):
            found = pack_anilist(media, hentai)
            if found:
                # Жанры у нас переводятся на русский при разборе — здесь
                # это ровно то, чего не надо.
                found["genres"] = [g for g in (media.get("genres") or [])][:5]
                titles = media.get("title") or {}
                found["title"] = (titles.get("english") or titles.get("romaji")
                                  or found["title"])
                packed = found
                break
    if packed is None or not packed.get("about"):
        ru, ru_failed = await _about_ru(title, title_en, year, hentai)
        failed = failed or ru_failed
        # Английское описание: из русской цепочки (там оно бывает, если
        # русского нет), иначе — у Jikan (MyAnimeList, synopsis). Раньше
        # при молчащем AniList английская страница получала русскую
        # карточку целиком: русское название, русское описание, русские
        # жанры (fix-21, скриншот хозяина с «Демонами старшей школы»).
        en_text = ru.get("about") if ru and ru.get("about_lang") == "en" else ""
        if not en_text:
            latin = _latin([(ru or {}).get("title_en", ""), title_en] + names)
            for variant in latin[:2]:
                res = await _ask_jikan(variant, hentai)
                if res is None:
                    failed = True
                    continue
                best = _pick([(p, n, p.get("year")) for p, n in res], [variant] + latin, year)
                if best and best.get("about"):
                    en_text = best["about"]
                    if packed is None and ru is None:
                        packed = dict(best)
                        packed["genres"] = best.get("genres_en") or []
                    break
        if packed is None and ru:
            packed = dict(ru)
            # Карточка Shikimori — по-английски: название (официальное
            # английское, иначе латинское) и английские имена жанров.
            packed["title"] = ru.get("title_eng") or ru.get("title_en") or packed["title"]
            packed["genres"] = ru.get("genres_en") or []
        if packed is not None:
            if en_text:
                packed["about"], packed["about_lang"] = en_text, "en"
            elif ru and ru.get("about") and not packed.get("about"):
                # Английского описания нет нигде. Пусто — хуже, чем по-русски:
                # отдаём русское с пометкой языка (страница подпишет
                # «Only in Russian»).
                packed["about"], packed["about_lang"] = ru["about"], ru.get("about_lang") or "ru"
            elif not packed.get("about"):
                packed["about_lang"] = "en"

    _remember(key, packed, failed)
    return packed


# --------------------------------------------------------------------------
# Обложка «любой ценой»
# --------------------------------------------------------------------------
# Источники видео теряют картинки: адрес собран неправильно, сайт сменил
# домен, картинку удалили. Тогда страница спрашивает сервер (/api/poster),
# и он ищет обложку по справочникам: Shikimori → AniList → Jikan. Чужая
# обложка хуже буквы-заглушки, поэтому кандидат принимается, только если
# похож по названию (и по году, если он известен).
POSTER_MISS_TTL = 20 * 60


def _poster_key(title: str, title_en: str, year: int | None) -> str:
    return ("poster:" + anime.normalize_title(title) + "\x00"
            + anime.normalize_title(title_en) + "\x00" + str(year or ""))


def _poster_args(title: object, title_en: object, year: object) -> tuple[str, str, int | None]:
    return ((str(title or "")).strip()[:120], (str(title_en or "")).strip()[:120],
            _year_of(year))


def poster_cached(title: str, title_en: str = "", year: object = None) -> str | None:
    """Обложка из кэша, "" — «недавно искали, нет», None — ещё не искали."""
    return _cache_get(_poster_key(*_poster_args(title, title_en, year)))


async def _shiki_found(q: str, hentai: bool = False) -> list[dict] | None:
    """Поиск Shikimori списком частей (pack_part). None — не ответил."""
    key = _ck("found:" + q.lower(), hentai)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    data = await _ask_shiki_gql(GQL_SEARCH_X if hentai else GQL_SEARCH, {"s": q})
    nodes = (data or {}).get("animes")
    if not isinstance(nodes, list):
        return None
    parts = [p for p in (pack_part(n) for n in nodes) if p and p["kind"] not in JUNK_KINDS]
    _cache_put(key, parts, CACHE_TTL if parts else NOT_FOUND_TTL)
    return parts


async def _poster_chain(title: str, title_en: str, year: int | None) -> tuple[str, bool]:
    """(адрес обложки или "", был ли по дороге сбой сети)."""
    names = _names(title, title_en) or _names(title_en)
    # 0. Справка по этому тайтлу уже лежит в кэше — обложка в ней есть.
    for lang in ("ru", "en"):
        for k in (_about_key(lang, title, title_en, year), _about_key(lang, title)):
            hit = _cache_get(k)
            if hit and hit.get("poster"):
                return hit["poster"], False

    failed = False
    extra: list[str] = []
    # 1. Shikimori.
    for q in names[:3]:
        parts = await _shiki_found(q)
        if parts is None:
            failed = True
            continue
        cands = [(p, [p.get("title") or "", p.get("title_en") or ""], p.get("year")) for p in parts]
        best = _pick(cands, names, year)
        if best and best.get("poster"):
            return best["poster"], failed
        if best and best.get("title_en"):
            # Тайтл в справочнике есть, а обложки у него нет. Зато теперь
            # известно латинское имя — по нему спросим остальных.
            extra.append(best["title_en"])
            break

    latin = _latin(extra + [title_en] + names)
    # 2. AniList.
    for q in latin[:2]:
        found = await _anilist_search(q)
        if found is None:
            failed = True
            continue
        cands = [(p, [p.get("title") or "", p.get("title_en") or ""], p.get("year"))
                 for p in found if p.get("poster")]
        best = _pick(cands, latin, year)
        if best:
            return best["poster"], failed
    # 3. Jikan.
    for q in latin[:2]:
        res = await _ask_jikan(q)
        if res is None:
            failed = True
            continue
        best = _pick([(p, n, p.get("year")) for p, n in res if p.get("poster")], latin, year)
        if best:
            return best["poster"], failed
    return "", failed


async def find_poster(title: str, title_en: str = "", year: object = None) -> str:
    """Обложка по названию из справочников — или "".

    Найденное помним сутки, честное «нигде нет» — двадцать минут (вдруг
    справочник пополнят), а промах из-за сбоя сети не помним вовсе: иначе
    минутная профилактика у Shikimori оставила бы тайтл без картинки.
    """
    title, title_en, year = _poster_args(title, title_en, year)
    if len(title) < 2 and len(title_en) < 2:
        return ""
    if len(title) < 2:
        title = title_en
    key = _poster_key(title, title_en, year)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    try:
        url, failed = await asyncio.wait_for(_poster_chain(title, title_en, year), POSTER_BUDGET)
    except asyncio.TimeoutError:
        log.info("обложка не нашлась за %.0f с", POSTER_BUDGET)
        return ""                       # не запоминаем: это сбой, а не «нет»
    url = clean_poster(url)
    if url:
        _cache_put(key, url)
    elif not failed:
        _cache_put(key, "", POSTER_MISS_TTL)
    return url


async def random_anime(genre: str | None = None) -> dict | None:
    """Случайное аниме из справочника — не из вашего списка.

    genre="ecchi" — случайное из жанра (только для тех, у кого включено
    18+, решает main.py). Страниц в жанре меньше, и запасного справочника
    для него нет: не ответил — вызывающий просто возьмёт обычное."""
    if genre:
        gid = SHIKI_GENRES.get(genre)
        if not gid:
            return None
        page = random.randint(1, RANDOM_GENRE_PAGES)
        key = f"page:{genre}:{page}"
    else:
        page = random.randint(1, RANDOM_PAGES)
        key = f"page:{page}"
    hit = _cache_get(key)
    if hit is not None:
        return hit or None

    packed = None
    failed = False
    ask = {"order": "popularity", "limit": 1, "page": page,
           "censored": "true", "kind": "tv"}
    if genre:
        ask["genre"] = gid
    found = await _get(SHIKI + "/animes", ask)
    if found is None:
        failed = True
    if isinstance(found, list) and found and isinstance(found[0], dict):
        brief = found[0]
        full = await _get(SHIKI + "/animes/" + str(brief.get("id")))
        if not isinstance(full, dict):
            failed, full = True, None
        packed = pack_shiki(brief, full)

    if not packed and not genre:
        data = await _ask_anilist(Q_RANDOM, {"p": page})
        if data is None:
            failed = True
        page = (data or {}).get("Page")
        items = _list(page.get("media")) if isinstance(page, dict) else []
        packed = pack_anilist(items[0]) if items else None

    # Страница, не отдавшаяся из-за сбоя, раньше запоминалась пустой на
    # сутки — и каждое случайное попадание в неё давало «каталог не
    # отвечает». Теперь сбой не запоминается.
    _remember(key, packed, failed)
    return packed


# ==========================================================================
# Поиск по франшизам
# ==========================================================================
# Чем этот поиск отличается от того, что был.
#
# Раньше в строку поиска уходил запрос к сайту с видео, и человек получал
# то, что этот сайт считает похожим. У «Наруто» это выглядело так:
# RU-A знает «Наруто Ураганные хроники» и «Боруто», но не знает
# «Наруто». RU-C вываливает двадцать одну строку вперемешку: сначала
# «Боруто: Фильм», потом второй сезон, потом спешл про спортивный
# фестиваль. Где здесь сам «Наруто» и в каком порядке всё это смотреть —
# по такому списку не понять никак.
#
# Причина простая: у сайтов с видео нет понятия «франшиза». Каждый сезон,
# фильм и OVA лежат у них отдельными записями с произвольными названиями,
# и связи между ними не хранится нигде.
#
# А у справочника она есть. У Shikimori каждая карточка помечена полем
# franchise: и «Наруто», и «Ураганные хроники», и все фильмы со спешлами
# несут одну метку «naruto». Поэтому поиск теперь идёт по справочнику:
# на запрос возвращается одна карточка на франшизу, а внутри неё — все
# части в порядке просмотра (см. order_parts).
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

# Поля одной карточки. Дата выхода — полная (date вида «2012-10-06»),
# а не только год: в 2022 году у «ДжоДжо» вышли сразу две части «Каменного
# океана», и по одному году их не различить. Строка короткая, так что
# полную дату берёт и поиск — ему она нужна для того же запасного порядка.
#
# rating — возрастной рейтинг: по нему прячется 18+ (см. ADULT_RATINGS).
# synonyms, licenseNameRu, english, japanese — другие имена тайтла. У
# источников видео тайтл часто лежит не под тем русским названием, что в
# справочнике: «Старшая школа DxD» у RU-D, RU-B и RU-A — это
# «Демоны старшей школы», и без синонимов резолв отвечал «ни один
# источник не отдал».
GQL_FIELDS = """
    id name russian licenseNameRu english japanese synonyms
    franchise kind rating episodes episodesAired status score
    airedOn { year date }
    poster { mainUrl originalUrl }
"""

# Список частей франшизы дополнительно спрашивает связи: кто кому
# продолжение, побочная история, пересказ. Из них и собирается порядок
# просмотра (см. order_parts). Поиску связи не нужны — он показывает
# одну карточку на франшизу, — поэтому там поле не запрашивается: на
# двадцать четыре найденных тайтла это был бы лишний вес ни за чем.
GQL_FRANCHISE_FIELDS = GQL_FIELDS + """
    related { relationKind anime { id } }
"""

# Для тех, у кого включён хентай, запросы идут с censored: false — и тогда
# в ответ попадает rx. Ему нужна проверка на несовершеннолетних
# (minors_flagged), а для неё — жанры с темами и описание. Остальным эти
# поля не нужны (rx им не приходит), и их запросы остались прежними.
GQL_ADULT_EXTRA = """
    genres { name russian kind }
    description
"""
GQL_FIELDS_X = GQL_FIELDS + GQL_ADULT_EXTRA
GQL_FRANCHISE_FIELDS_X = GQL_FRANCHISE_FIELDS + GQL_ADULT_EXTRA


def _censored(hentai: bool) -> str:
    """Флаг справочника: censored: true прячет хентай на его стороне."""
    return "false" if hentai else "true"


async def _ask_shiki_gql(query: str, variables: dict | None = None) -> dict | None:
    """Запрос к GraphQL справочника. Молчание — не ошибка, а None."""
    await _pause()
    try:
        status, body = await _send("POST", SHIKI_GQL,
                                   json={"query": query, "variables": variables or {}})
        if status != 200:
            log.info("справочник ответил %s на graphql", status)
            return None
        if not isinstance(body, dict):
            return None
        if body.get("errors"):
            log.info("справочник вернул ошибку: %s", str(body["errors"])[:200])
        data = body.get("data")
        return data if isinstance(data, dict) and data else None
    except Exception as exc:                        # noqa: BLE001
        log.info("справочник не ответил: %s", type(exc).__name__)
        return None


@_tolerant
def pack_part(node: dict | None) -> dict | None:
    """Одна часть франшизы: сезон, фильм, OVA или спешл."""
    if not isinstance(node, dict):
        return None
    title = _str(node.get("russian")) or _str(node.get("name"))
    if not title:
        return None
    kind = _str(node.get("kind"))
    poster = node.get("poster") or {}
    poster = poster if isinstance(poster, dict) else {}
    aired = node.get("airedOn") or {}
    aired = aired if isinstance(aired, dict) else {}
    year = aired.get("year")
    date = aired.get("date")
    try:
        score = float(node.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    rating = _rating(node.get("rating"))
    if rating in HENTAI_RATINGS and minors_flagged(
            node.get("genres") or [],
            [title, node.get("name"), node.get("english"), node.get("japanese"),
             node.get("licenseNameRu")] + _strs(node.get("synonyms")),
            node.get("description") or ""):
        # Хентай с признаками несовершеннолетних не показывается никому и
        # никогда — в кэш он не попадает вовсе (см. minors_flagged). Его
        # имена запоминаются как «отброшенные»: источник хентая по ним
        # не пойдёт, даже если чистый тайтл носит то же имя (rx_cleared).
        _rx_mark([title, node.get("name"), node.get("english"), node.get("licenseNameRu")]
                 + _strs(node.get("synonyms")), False)
        return None
    packed = {
        "id": str(node.get("id") or ""),
        "cid": catalog_id(node.get("id")),
        "title": title,
        "title_en": _str(node.get("name")),
        "alt": alt_from_node(node, title, _str(node.get("name"))),
        "rating": rating,
        # adult — «18+ в широком смысле» (этти и хентай), hentai — только rx.
        # Что из этого видно человеку, решают visible и cards_for на выходе.
        "adult": rating in ADULT_RATINGS or rating in HENTAI_RATINGS,
        "hentai": rating in HENTAI_RATINGS,
        "franchise": _str(node.get("franchise")),
        "year": year if isinstance(year, int) else None,
        "kind": kind,
        "kind_ru": KIND_RU.get(kind, kind),
        # У выходящего сейчас сериала общее число серий не проставлено:
        # «Ван-Пис» шёл двадцать шесть лет, и сколько их будет всего, не
        # знает никто. Тогда показываем, сколько вышло.
        "episodes": _whole(node.get("episodes")) or _whole(node.get("episodesAired")) or None,
        "ongoing": node.get("status") == "ongoing",
        "poster": (_poster_url(poster.get("mainUrl")) or _poster_url(poster.get("originalUrl"))),
        "score": int(round(score * 10)) if score else None,
    }
    # Полная дата нужна, чтобы различать части одного года. Берём только
    # то, что похоже на дату: чужая строка не должна портить сортировку.
    packed["date"] = (date if isinstance(date, str)
                      and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None)
    # Связи кладём, только если справочник их прислал. «Не спрашивали»
    # (поиск, запасной справочник) и «спросили — связей нет» — разные
    # вещи: по первому порядок строится по датам, по второму — по связям.
    if isinstance(node.get("related"), list):
        links = []
        for rel in node["related"]:
            if not isinstance(rel, dict):
                continue
            other = rel.get("anime")
            # У экранизируемой манги anime пустой — это связь не с аниме.
            if not isinstance(other, dict) or not other.get("id"):
                continue
            kind_rel = rel.get("relationKind")
            if isinstance(kind_rel, str) and kind_rel:
                links.append({"kind": kind_rel.lower(), "id": str(other["id"])})
        packed["related"] = links
    remember_names(packed, node.get("japanese"))
    if packed["hentai"]:
        _rx_mark(_part_names(packed) + [node.get("english")], True)
    return packed


# --------------------------------------------------------------------------
# 18+ и хентай
# --------------------------------------------------------------------------
# Рейтинги Shikimori: g, pg, pg_13, r (17+), r_plus («R+» — откровенные
# сцены без порнографии, этти: «Старшая школа DxD»), rx (хентай).
#
# Две ступени, и вторая стоит на первой:
#   r_plus — прячется по умолчанию, показывается тому, кто сам включил
#            «Показывать 18+» (adult_ok);
#   rx     — прячется ещё строже: только тому, у кого включены и 18+, и
#            «Хентай» (hentai_ok). Решает main.hentai_ok — гостю нельзя
#            никогда, что бы ни прислала страница.
#
# Раньше rx не показывался никому и выбрасывался прямо при разборе ответа
# (pack_part). Теперь хозяин решил иначе — но выбрасывание при разборе
# никуда не делось для того, что не показывается НИКОМУ: тайтлы с
# признаками несовершеннолетних (minors_flagged ниже). Остальной rx
# лежит в кэше с пометкой hentai и отсеивается на выходе (visible,
# cards_for) — как и этти.
ADULT_RATINGS = {"r_plus"}
HENTAI_RATINGS = {"rx"}


def _rating(value: object) -> str:
    """Рейтинг одним видом: «R_PLUS», «r_plus», «R+» → «r_plus»."""
    text = str(value or "").strip().lower().replace("+", "_plus").replace("-", "_")
    return text if re.fullmatch(r"[a-z0-9_]{1,12}", text) else ""


def is_hentai(item: dict | None) -> bool:
    return bool(item) and (bool(item.get("hentai")) or item.get("rating") in HENTAI_RATINGS)


def is_adult(item: dict | None) -> bool:
    """18+ в широком смысле: этти и хентай. Хентай — тоже «взрослое»:
    тому, у кого 18+ выключено, он не виден уже поэтому."""
    return bool(item) and (bool(item.get("adult")) or item.get("rating") in ADULT_RATINGS
                           or is_hentai(item))


def visible(items: list[dict] | None, adult_ok: bool, hentai_ok: bool = False) -> list[dict]:
    """Что из списка показать этому человеку. Списки в кэше общие на всех —
    поэтому фильтр здесь, на выходе, а не при складывании в кэш: иначе
    первый спросивший решал бы за всех, что увидят остальные.

    hentai_ok без adult_ok не бывает (см. main.hentai_ok), но и здесь
    одно без другого rx не пропустит: хентай ещё и «взрослый»."""
    return [i for i in (items or [])
            if (hentai_ok or not is_hentai(i)) and (adult_ok or not is_adult(i))]


# --------------------------------------------------------------------------
# Несовершеннолетние: не показывается никогда, никому, никакой настройкой
# --------------------------------------------------------------------------
# Хентай теперь можно включить. Но тайтл, где по данным справочника есть
# сексуальный контент с детьми, не показывается ни при каких настройках:
# он выбрасывается прямо при разборе ответа и в кэш не попадает вовсе.
#
# Проверяется только то, что помечено rx (хентай): у остального рейтинг
# сам говорит, что откровенного там нет.
#
# Что у справочников есть на этот счёт (и чего нет):
#   * Shikimori — жанры и темы (genres: name/russian, в том числе
#     демография «Kids»/«Детское»). Отдельных тегов «лоли/сёта» у него
#     нет. Поэтому для rx ещё и описание (description) проверяется на
#     слова из _MINORS_WORDS;
#   * AniList — теги (tags: «Primarily Child Cast», «Primarily Teen Cast»,
#     «Elementary School», «Kids» и др.) плюс жанры. Самый подробный;
#   * Jikan (MyAnimeList) — genres, explicit_genres, themes, demographics.
#   * Названия и синонимы у всех трёх проверяются на те же слова.
# Ограничение: если справочник не дал ни тегов, ни жанров, ни описания
# (бывает у свежих и малоизвестных записей), проверять нечего — такой
# тайтл остаётся. Списки ниже сделаны с запасом: лишний раз спрятанный
# хентай — не потеря, пропущенный такой тайтл — недопустимо.
MINORS_TAGS = {
    "loli", "lolicon", "lolikon", "shota", "shotacon", "shotakon", "лоли", "лоликон",
    "сёта", "шота", "сётакон", "шотакон", "kid", "kids", "kodomo", "детское", "дети",
    "child", "children", "young", "primarily child cast", "primarily teen cast",
    "elementary", "elementary school", "middle school", "kindergarten", "age regression",
    "school girl", "school girls", "schoolgirl", "schoolgirls", "school boy", "schoolboy",
    "школьница", "школьницы", "школьник",
}
# Те же признаки в теге, написанном по-своему: «Loli-Con», «school_girl»,
# «Young Girl», «Child (Female)». Это для тегов сайтов-источников (источник
# хентая, anime.RX_ORDER), у которых нет общего словаря, как у справочников: теги там
# пишет кто как хочет. Проверяется ЦЕЛОЕ слово (?<!\w)…(?!\w): «child»
# не цепляет «Childhood Friends», «kid» — «Kidnapping».
_MINORS_TAG_WORDS = re.compile(
    r"(?<!\w)(?:loli\w*|shota(?:con|kon)?s?|child|children|kids?|young\w*|elementary"
    r"|school ?(?:girl|boy)s?|underage|minors?|лол[иь]\w*|с[её]та\w*|шота\w*"
    r"|школьни\w*|дети|детей|детск\w*)(?!\w)", re.I)


def minors_tag(tag: object) -> bool:
    """Один тег (жанр, тема) — признак несовершеннолетних?

    Общая проверка для всех: справочники (minors_flagged ниже) и теги
    источника хентая (anime.RX_ORDER). Тег приводится к одному виду —
    строчные буквы, «ё» как «е», «-» и «_» как пробел, пробелы по
    одному — и сверяется со списком MINORS_TAGS целиком, а потом по
    словам (_MINORS_TAG_WORDS). Длина тега обрезается: тег — пара слов,
    а не страница, и разбирать мегабайт «тега» незачем.
    """
    if not isinstance(tag, str):
        return False
    for variant in _variants(tag[:200]):
        text = " ".join(variant.replace("_", " ").replace("-", " ").split())
        if not text:
            continue
        if text in _MINORS_TAGS_E or _MINORS_TAG_WORDS.search(text) \
                or _MINORS_EXTRA.search(text) or _MINORS_SPACED.search(text):
            return True
    return False
# Слова в названии, синонимах и описании. Только начала слов: «loli»
# ловит и «lolicon», и «lolis»; «shota» — только целиком или «shotacon»,
# чтобы не цеплять имя «Shotaro».
_MINORS_WORDS = re.compile(
    r"(?<![\w])(?:"
    r"loli\w*|shota(?:con|kon)?(?!\w)|лол[иь]\w*|с[её]та(?:кон)?(?!\w)|шота(?:кон)?(?!\w)"
    r"|underage|under-age|несовершеннолетн\w*|младшеклассни\w*"
    r"|elementary[- ]school\w*|grade[- ]school\w*|primary[- ]school\w*|middle[- ]school\w*"
    r"|kindergarten\w*|начальн\w+ школ\w*|средн\w+ школ\w*|детск\w+ сад\w*"
    r"|(?:1[0-7]|[1-9])[- ]?(?:year|yr)s?[- ]old|(?:1[0-7]|[1-9])[- ]?летн\w*"
    r")", re.I)


def minors_flagged(tags: object = (), names: object = (), text: object = "") -> bool:
    """Есть ли у тайтла признаки сексуального контента с несовершеннолетними.

    tags  — жанры, темы и теги из справочника (строки или {name, russian});
    names — названия и синонимы;
    text  — описание.
    Регистр не важен. Совпадение тега — по имени целиком, слова в
    названиях и описании — по _MINORS_WORDS."""
    for tag in tags if isinstance(tags, (list, tuple, set)) else ():
        variants = ([tag.get("name"), tag.get("russian")] if isinstance(tag, dict) else [tag])
        for v in variants:
            if minors_tag(v):
                return True
    for n in names if isinstance(names, (list, tuple, set)) else ():
        if isinstance(n, str) and _words_flagged(n[:600]):
            return True
    return isinstance(text, str) and _words_flagged(text[:DESC_INPUT_MAX * 2])


def _words_flagged(text: str) -> bool:
    return any(_MINORS_WORDS.search(v) or _MINORS_EXTRA.search(v) or _MINORS_SPACED.search(v)
               for v in _variants(text))


# Те же теги, но «ё» приведено к «е»: «сёта» и «сета» в справочнике
# пишут по-разному.
_MINORS_TAGS_E = {t.replace("ё", "е") for t in MINORS_TAGS}

# --- Обходы (проверяющий C, ноябрь 2026) ---------------------------------
# Раньше проверка сравнивала строку как есть, и её обходило то, что пишут
# сайты, где теги ставит кто угодно (источники хентая), и что бывает в синонимах:
#   «lоli» и «лoли» (буква из другой раскладки), «ｌｏｌｉ» (широкие буквы),
#   «lo\u200bli» (невидимый пробел), «shōta» (буква с чёрточкой),
#   «lo li», «Lo-li», «l o l i», «С Ё Т А» (буквы через разделители),
#   «12yo», «12 y.o.», «twelve-year-old», «девочка 12 лет», «3rd grader»,
#   «preteen», «toddler», «jailbait».
# Теперь текст сначала приводится к одному виду (_variants: без невидимых
# знаков и чёрточек над буквами, строчными, и ещё в двух вариантах — с
# похожими буквами, сведёнными к латинице и к кириллице), и каждый вариант
# проверяется прежними списками, дополнительными словами (_MINORS_EXTRA) и
# словами-основами с разделителями между буквами (_MINORS_SPACED).
# Лишний раз спрятанный хентай — не потеря (см. выше), поэтому с запасом.
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"), None)
_LOOK_LATIN = str.maketrans("аеорсухкіјӏ0", "aeopcyxkijlo")
_LOOK_CYR = str.maketrans("aeopcyxk", "аеорсухк")


def _variants(text: str) -> set[str]:
    # Чёрточки и точки снимаются только с латинских букв («shōta» → «shota»):
    # у русских это другие буквы («й» — не «и»). «ё» — как «е» (см. выше).
    out, latin = [], False
    for ch in unicodedata.normalize("NFKD", str(text or "")).translate(_INVISIBLE):
        if unicodedata.combining(ch):
            if latin:
                continue
        else:
            latin = ch.isascii()
        out.append(ch)
    folded = unicodedata.normalize("NFC", "".join(out)).lower().replace("ё", "е")
    return {folded, folded.translate(_LOOK_LATIN), folded.translate(_LOOK_CYR)}


_NUM_WORD = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
             r"|fourteen|fifteen|sixteen|seventeen)")
_MINORS_EXTRA = re.compile(
    r"(?<!\w)(?:" + _NUM_WORD + r"[- ]?(?:year|yr)s?[- ]?old"
    r"|(?:1[0-7]|[1-9])\s?y\.?\s?o\.?(?!\w)"
    # «12 лет» — только с подсказкой, что это возраст: «через 5 лет» и
    # «спустя 3 года» в описаниях обычны и о возрасте не говорят.
    r"|(?:ей|ему|исполнил\w*|в возрасте|возрастом|девоч\w*|мальчик\w*|сестр\w*|доч\w*)"
    r"\s+(?:всего\s+|только\s+)?(?:1[0-7]|[1-9])\s?(?:лет|года?)(?!\w)"
    r"|(?:1[0-7]|[1-9])\s?(?:лет|года?)\s+от\s+роду"
    r"|(?:[1-8](?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth)"
    r"[- ]grade(?:r|rs)?"
    r"|pre-?teens?|toddlers?|infants?|jailbait|pedo\w*|педо\w*)(?!\w)", re.I)


def _spaced(word: str) -> str:
    return r"[\W_]{0,3}".join(re.escape(ch) for ch in word)


# Основы с разделителями между буквами («lo li», «С Ё Т А»). Начало — с
# начала слова; «shota» и «сета» — ещё и до конца слова, чтобы не цеплять
# «Shotaro».
_MINORS_SPACED = re.compile(
    r"(?<![^\W\d_])(?:" + _spaced("loli") + "|" + _spaced("лоли") + "|" + _spaced("лоль")
    + "|(?:" + "|".join(_spaced(w) for w in ("shota", "сета", "шота")) + r")(?![^\W\d_])"
    + ")", re.I)


# --------------------------------------------------------------------------
# Какой хентай справочник «пропустил»: память для источника хентая
# --------------------------------------------------------------------------
# Источник хентая (anime.RX_ORDER, anime.RX_CLEARED) знает свой каталог, и в нём может быть
# что угодно. Хозяин решил: брать оттуда только то, что сначала показал
# НАШ справочник — то есть rx, прошедший minors_flagged. Источник видит
# только название (так устроен весь путь «название → источник»), поэтому
# здесь память «под каким именем справочник показал чистый rx»:
#   * _RX_OK  — имена rx-частей, прошедших проверку (pack_part,
#               _anilist_parts и карточки pack_*);
#   * _RX_BAD — имена rx, отброшенных проверкой. Плохое сильнее хорошего:
#               одно имя у чистого и у отброшенного тайтла — значит «нет».
# Имена — в виде anime.normalize_title. Память живёт неделю и не растёт
# без счёта.
RX_MARK_TTL = 7 * 24 * 3600
RX_MARK_MAX = 20_000
_RX_OK: dict[str, float] = {}
_RX_BAD: dict[str, float] = {}


def _rx_names(names: object) -> list[str]:
    out = []
    for n in names if isinstance(names, (list, tuple, set)) else ():
        if isinstance(n, str) and n.strip():
            key = anime.normalize_title(n[:300])
            if key and key not in out:
                out.append(key)
    return out


def _rx_mark(names: object, ok: bool) -> None:
    """Запоминает имена rx: прошёл проверку (ok) или отброшен."""
    table = _RX_OK if ok else _RX_BAD
    now = time.time()
    if len(table) > RX_MARK_MAX:
        for k in [k for k, until in table.items() if until < now]:
            table.pop(k, None)
        if len(table) > RX_MARK_MAX:
            for k in list(table)[: RX_MARK_MAX // 2]:
                table.pop(k, None)
    for key in _rx_names(names):
        table[key] = now + RX_MARK_TTL


def _part_names(part: dict) -> list:
    return [part.get("title"), part.get("title_en"), part.get("title_eng")] + \
        list(part.get("alt") or [])


def rx_known_clean(names: object) -> bool | None:
    """True — справочник показывал rx под одним из этих имён и он чист;
    False — под одним из них отброшенный; None — не знаем."""
    now = time.time()
    keys = _rx_names(names)
    if any(_RX_BAD.get(k, 0) > now for k in keys):
        return False
    if any(_RX_OK.get(k, 0) > now for k in keys):
        return True
    return None


async def rx_cleared(title: str, title_en: str = "", year: object = None) -> bool:
    """Показывал ли наш справочник этот хентай (rx без признаков
    несовершеннолетних)? Это условие для источника хентая.

    Сначала память (_RX_OK/_RX_BAD). Пусто — сервер мог перезапуститься,
    а человек открыл серию из полки, — тогда один поиск по справочнику
    без цензуры (тот же, что у поиска на сайте, и тот же кэш): всё, что
    он вернёт с пометкой hentai, уже прошло minors_flagged при разборе
    (отброшенное в кэш не попадает вовсе). Справочник молчит — «нет»:
    не узнали — значит не показываем.
    """
    names = [n for n in (title, title_en) if isinstance(n, str) and len(n.strip()) >= 2]
    if not names:
        return False
    known = rx_known_clean(names)
    if known is not None:
        return known
    for q in names[:2]:
        parts = await _shiki_found(q.strip()[:120], True)
        if parts is None:
            parts = await _anilist_search(q.strip()[:120], True)
        for p in parts or []:
            if not is_hentai(p):
                continue
            pn = _part_names(p)
            if any(anime.relevance(n, x) >= 0.9 for n in names for x in pn if isinstance(x, str) and x):
                _rx_mark(pn, True)
        known = rx_known_clean(names)
        if known is not None:
            return known
    return False


# --------------------------------------------------------------------------
# Другие имена тайтла
# --------------------------------------------------------------------------
_CYR = re.compile(r"[а-яё]", re.I)
_ALT_INDEX: dict[str, list[dict]] = {}
ALT_INDEX_MAX = 4000


def alt_from_node(node: dict, title: str, name: str) -> list[str]:
    """Другие имена из карточки справочника, в том порядке, в каком их
    стоит искать у источников: сначала русские (лицензионное название,
    синонимы), потом латинские (английское, латинские синонимы).
    Японское имя источникам не нужно — они его не знают."""
    ru: list[str] = []
    latin: list[str] = []
    seen = {anime.normalize_title(title), anime.normalize_title(name)}
    raw = [node.get("licenseNameRu")] + _strs(node.get("synonyms")) + [node.get("english")]
    for n in raw:
        if not isinstance(n, str):
            continue
        n = n.strip()[:200]
        key = anime.normalize_title(n)
        if len(n) < 2 or not key or key in seen:
            continue
        seen.add(key)
        if _CYR.search(n):
            ru.append(n)
        elif not re.search(r"[぀-ヿ一-鿿]", n):
            latin.append(n)
    return (ru + latin)[:6]


def remember_names(part: dict, japanese: object = None) -> None:
    """Запоминает, под какими именами известна часть: по названию из
    справочника потом можно спросить её синонимы, не ходя в сеть."""
    entry = {"title": part.get("title") or "", "title_en": part.get("title_en") or "",
             "alt": list(part.get("alt") or []), "year": part.get("year"),
             "episodes": part.get("episodes")}
    keys = [entry["title"], entry["title_en"]] + entry["alt"]
    if isinstance(japanese, str):
        keys.append(japanese)
    for k in keys:
        nk = anime.normalize_title(k)
        if not nk:
            continue
        bucket = _ALT_INDEX.setdefault(nk, [])
        if not any(e["title"] == entry["title"] and e["year"] == entry["year"] for e in bucket):
            bucket.append(entry)
            del bucket[:-4]                         # одноимённых больше четырёх не держим
    if len(_ALT_INDEX) > ALT_INDEX_MAX:
        for k in list(_ALT_INDEX)[: ALT_INDEX_MAX // 2]:
            _ALT_INDEX.pop(k, None)


def _alt_lookup(title: str, title_en: str, year: int | None) -> dict | None:
    """Запись из памяти по названию: сначала то же название, потом латинское."""
    for name in (title, title_en):
        bucket = _ALT_INDEX.get(anime.normalize_title(name or ""))
        if not bucket:
            continue
        if year:
            same = [e for e in bucket if e.get("year") and abs(e["year"] - year) <= 1]
            if same:
                return same[0]
        # Без года: только если запись одна — иначе не угадать, какая.
        if len(bucket) == 1 or not year:
            return bucket[0]
    return None


def _names_of(entry: dict, title: str, title_en: str) -> list[str]:
    asked = {anime.normalize_title(title), anime.normalize_title(title_en)}
    out = []
    for n in [entry.get("title")] + list(entry.get("alt") or []) + [entry.get("title_en")]:
        if isinstance(n, str) and n.strip() and anime.normalize_title(n) not in asked \
                and n not in out:
            out.append(n)
    return out


async def alt_names(title: str, title_en: str = "", year: object = None,
                    hentai: bool = False) -> list[str]:
    """Другие имена тайтла: русские синонимы, потом латинские.

    Сначала — из памяти: всё, что прошло через поиск и списки частей,
    уже запомнено (remember_names). Нет в памяти — один поиск по
    справочнику (он кэшируется на сутки). Справочник молчит — пустой
    список: резолв просто обойдётся без синонимов, как раньше.

    hentai=True — искать и среди хентая (у того, кому он включён): иначе
    справочник с цензурой такого тайтла просто не знает.
    """
    title = (title or "").strip()[:120]
    title_en = (title_en or "").strip()[:120]
    year = _year_of(year)
    if len(title) < 2 and len(title_en) < 2:
        return []
    hit = _alt_lookup(title, title_en, year)
    miss_key = _ck("altmiss:" + title.lower() + "\x00" + title_en.lower(), hentai)
    if hit is None and _cache_get(miss_key):
        return []
    if hit is None:
        failed = False
        for q in [t for t in (title, title_en) if len(t) >= 2][:2]:
            parts = await _shiki_found(q, hentai)
            if parts is None:
                failed = True
                continue
            # Не всё, что нашёл поиск, — тот же тайтл: берём только то,
            # чьё название (или синоним) совпало точно.
            for p in parts:
                names = [p.get("title"), p.get("title_en")] + list(p.get("alt") or [])
                if any(anime.relevance(t, n) >= 0.9 for t in (title, title_en) if t
                       for n in names if n):
                    remember_names(p)
            hit = _alt_lookup(title, title_en, year)
            if hit is not None:
                break
        if hit is None:
            # Не нашлось или справочник молчит — какое-то время не
            # спрашиваем снова: резолв одного и того же тайтла не должен
            # каждый раз стучаться в чужой сайт.
            _cache_put(miss_key, True, 120 if failed else NOT_FOUND_TTL)
    return _names_of(hit, title, title_en) if hit else []


# Внутри одной даты сериал идёт первым, дальше по убыванию «основного»:
# то, что смотрят, выше того, что смотрят после.
KIND_RANK = {"tv": 0, "ona": 1, "movie": 2, "tv_special": 3, "ova": 3,
             "special": 4}

# Виды, которые считаются «самой историей», когда связей нет и порядок
# приходится угадывать по датам (см. order_by_date).
MAIN_KINDS = ("tv", "ona")


def _date_key(part: dict) -> str:
    """Дата выхода строкой, которую можно сравнивать.

    Полная дата — как есть. Если известен только год (запасной
    справочник, старые записи кэша), часть встаёт в конец своего года:
    про неё известно меньше всех, угадывать ей место внутри года нечем.
    Без даты вовсе — «9999»: анонсы уезжают в самый конец.
    """
    date = part.get("date")
    if isinstance(date, str) and len(date) == 10:
        return date
    year = part.get("year")
    if isinstance(year, int) and year > 0:
        return "%04d-99-99" % year
    return "9999"


def _sort_key(part: dict) -> tuple:
    """Порядок частей — по выходу, с точностью до дня.

    Раньше сравнивался только год, и у «ДжоДжо» «Каменный океан. Часть 3»
    вставал раньше «Части 2»: обе 2022 года, а дальше решало что попало.
    Теперь сравнивается полная дата.

    Ровесники одного дня разбираются по виду: сериал выше пересказа и
    спешла. Последним стоит id — чтобы порядок был одним и тем же при
    каждом запросе, даже когда всё остальное совпало.
    """
    rank = KIND_RANK.get(part.get("kind") or "", 5)
    size = -(part.get("episodes") or 0)
    return (_date_key(part), rank, size, str(part.get("id") or ""))


def sort_parts(parts: list[dict]) -> list[dict]:
    return sorted(parts, key=_sort_key)


def main_part(parts: list[dict]) -> dict:
    """Часть, по которой франшиза узнаётся в карточке поиска.

    Это почти всегда первый сериал: «Наруто», а не «Наруто: Ураганные
    хроники» и не «Боруто».

    Сериалы отбираются строго, отдельной ступенью, а не вместе с
    околосериальным. Иначе выходит вот что: у «Атаки титанов» первый
    сезон и нарезка-пересказ вышли в один и тот же год, нарезка помечена
    как tv_special, и франшиза называлась «Атака титанов: Рекап».

    Внутри ступени при равной дате побеждает то, где больше серий:
    основная история почти всегда длиннее сопровождающего её спешла.
    """
    for tier in ([p for p in parts if p.get("kind") == "tv"],
                 [p for p in parts if p.get("kind") in ("tv_special", "ona")],
                 parts):
        if tier:
            return sorted(tier, key=_sort_key)[0]
    return parts[0]


def mark_main(parts: list[dict]) -> list[dict]:
    """Помечает часть, с которой начинают смотреть, — когда связей нет.

    Правило: первый по порядку сериал (tv или ona), а если сериалов нет
    вовсе (франшиза из одних фильмов) — просто первая строка.

    Почему не «самое раннее». У «Ван-Пис» самое раннее — OVA 1998 года
    «Победить пирата Ганзака!», снятая за год до сериала, короткая
    пробная серия. Человек, которому сказали «начни с неё», получает
    тридцать минут не того и решает, что сайт врёт. Начинают «Ван-Пис»
    с сериала 1999 года — значит, и отметка там. Строка с OVA при этом
    остаётся выше по списку: порядок по датам честный, а «начни отсюда»
    стоит отдельной заметной пометкой, так что разночтения нет.

    Когда связи есть, отметку ставит order_parts — там «начало» это
    первый шаг основной истории.
    """
    if not parts:
        return parts
    start = next((p for p in parts if p.get("kind") in MAIN_KINDS), parts[0])
    for part in parts:
        part["main"] = part is start
    return parts


# --------------------------------------------------------------------------
# Порядок просмотра
# --------------------------------------------------------------------------
# Раньше части стояли просто по годам выхода, а «начало» ставилось на
# самую раннюю. На «ДжоДжо» это ломалось сразу: самое раннее там — OVA
# 1993 года, старая отдельная экранизация третьей части с VHS-качеством.
# Её и предлагали смотреть первой, хотя начинают «ДжоДжо» с сериала 2012
# года.
#
# Теперь порядок строится по связям, которые знает справочник. У каждой
# части он хранит, кем ей приходятся другие части: sequel
# (продолжение), prequel (предыдущая часть), side_story (побочная
# история), summary (пересказ), alternative_version (другая экранизация
# того же) и так далее. Дальше три шага.
#
# 1. Основная история. Из связей «продолжение / предыдущая часть»
#    получаются цепочки: часть → её продолжение → его продолжение…
#    Цепочка начинается с части, у которой внутри франшизы нет
#    предыдущей. Таких цепочек обычно несколько (у «ДжоДжо» отдельно
#    идут OVA 1993 → OVA 2000), и основной считается та, в которой
#    больше всего серий: сама история всегда длиннее своих ответвлений.
#    Если у части два продолжения (у «Ураганных хроник» это фильм
#    «Боруто» и сериал «Боруто: Новое поколение»), цепочка идёт в
#    сериал, а фильм уходит в дополнения.
#
# 2. Дополнения. Каждая часть, связанная с основной историей (побочная
#    история, пересказ, спин-офф, фильм…), встаёт сразу после той части
#    основной истории, к которой относится. Если она связана с
#    несколькими — после ближайшей по дате из тех, что вышли не позже
#    неё. Если связана только с другим дополнением — встаёт туда же, куда
#    и оно. Внутри одного места дополнения идут по дате.
#
# 3. Прочее. Части, которые с основной историей связаны только как
#    «другая экранизация», «другая вселенная» или «те же герои», — или не
#    связаны вовсе, — уходят в конец отдельной группой. Их можно
#    смотреть, но начинать с них и вставлять их посреди сезонов не надо.
#    Туда и попадает та самая OVA 1993 года.
#
# Если связей нет совсем (запасной справочник их не отдаёт, а старые
# записи кэша их не хранят), работает запасной порядок — по датам, см.
# order_by_date.

# Связь, увиденная с другой стороны. Справочник пишет связь от лица
# каждой части: у A записано «B — моё продолжение», у B — «A — моя
# предыдущая часть». Одна из записей иногда отсутствует, поэтому
# каждую связь достраиваем в обе стороны сами.
REL_INVERSE = {
    "sequel": "prequel", "prequel": "sequel",
    "side_story": "parent_story", "parent_story": "side_story",
    "summary": "full_story", "full_story": "summary",
    "spin_off": "parent_story",
}

# Связи, которые не делают часть дополнением к основной истории: это
# не «смотри после такого-то сезона», а «есть ещё и такое».
WEAK_LINKS = {"alternative_version", "alternative_setting", "character", "adaptation"}

# Если часть связана с местом своей привязки несколькими способами,
# подпись берётся по самому говорящему из них.
REL_PRIORITY = ("sequel", "prequel", "spin_off", "summary", "side_story",
                "full_story", "parent_story", "other",
                "alternative_version", "alternative_setting", "character")

# Короткие подписи для окна частей. Код (rel) — для страницы и проверок,
# подписи — для человека, на двух языках.
REL_LABELS = {
    "sequel": ("продолжение", "sequel"),
    "prequel": ("предыстория", "prequel"),
    "side_story": ("побочная история", "side story"),
    "movie_extra": ("фильм по мотивам", "side movie"),
    "summary": ("пересказ", "recap"),
    "spin_off": ("спин-офф", "spin-off"),
    "full_story": ("полная версия", "full version"),
    "parent_story": ("основа истории", "parent story"),
    "other": ("дополнение", "extra"),
    "alternative_version": ("другая экранизация", "alternative version"),
    "alternative_setting": ("другая вселенная", "alternative setting"),
    "character": ("те же герои", "same characters"),
    "unlinked": ("отдельная история", "separate story"),
}


def _link_map(parts: list[dict]) -> dict[str, dict[str, set]]:
    """links[a][b] — кем часть b приходится части a («b — продолжение a»).

    Ссылки на тайтлы вне списка (отсеянные трейлеры, чужие франшизы)
    отбрасываются: ставить часть «после» того, чего в списке нет, нельзя.
    """
    ids = {p["id"] for p in parts}
    links: dict[str, dict[str, set]] = {i: {} for i in ids}
    for part in parts:
        for rel in part.get("related") or []:
            other, kind = rel.get("id"), rel.get("kind")
            if other not in ids or other == part["id"] or not kind:
                continue
            links[part["id"]].setdefault(other, set()).add(kind)
            links[other].setdefault(part["id"], set()).add(REL_INVERSE.get(kind, kind))
    return links


def _main_chain(parts: list[dict], links: dict) -> list[str]:
    """Цепочка основной истории — список id по порядку просмотра."""
    by_id = {p["id"]: p for p in parts}
    sequels = {i: [b for b, kinds in links[i].items() if "sequel" in kinds] for i in by_id}
    starts = [p for p in sort_parts(parts)
              if not any("prequel" in kinds for kinds in links[p["id"]].values())]
    if not starts:
        # Связи замкнулись в круг — у каждой части есть предыдущая.
        # Начинаем с самой ранней, чтобы хоть что-то было.
        starts = sort_parts(parts)[:1]

    def walk(first: str) -> list[str]:
        chain, seen = [first], {first}
        while True:
            nxt = [b for b in sequels[chain[-1]] if b not in seen]
            if not nxt:
                return chain
            # Два продолжения: сериал важнее фильма, фильм важнее спешла,
            # при равенстве — то, что вышло раньше.
            pick = min(nxt, key=lambda i: (KIND_RANK.get(by_id[i].get("kind") or "", 5),)
                       + _sort_key(by_id[i]))
            chain.append(pick)
            seen.add(pick)

    def weight(chain: list[str]) -> tuple:
        episodes = sum(by_id[i].get("episodes") or 0 for i in chain)
        has_tv = any(by_id[i].get("kind") == "tv" for i in chain)
        return (-episodes, 0 if has_tv else 1) + _sort_key(by_id[chain[0]])

    return min((walk(p["id"]) for p in starts), key=weight)


def _closest(part: dict, anchors: list[str], step: dict[str, int],
             by_id: dict[str, dict]) -> str:
    """Ближайшая часть основной истории из нескольких кандидатов.

    «Ближайшая» — последняя из тех, что вышли не позже самой части:
    «Хроника» «Атаки титанов» пересказывает первые три сезона и вышла
    после них — её место после третьего, а не после первого. Если часть
    вышла раньше всех кандидатов — встаёт после самого первого.
    """
    mine = _date_key(part)
    before = [a for a in anchors if _date_key(by_id[a]) <= mine]
    if before:
        return max(before, key=lambda a: step[a])
    return min(anchors, key=lambda a: step[a])


def _rel_code(kinds: set, part: dict) -> str:
    kind = next((k for k in REL_PRIORITY if k in kinds), "other")
    if kind == "side_story" and part.get("kind") == "movie":
        return "movie_extra"
    return kind


def _label(part: dict, rel: str) -> None:
    ru, en = REL_LABELS.get(rel, ("", ""))
    part["rel"], part["rel_ru"], part["rel_en"] = rel, ru, en


def has_links(parts: list[dict]) -> bool:
    """Есть ли среди частей хоть одна связь внутри этого же списка."""
    return any(kinds for row in _link_map(parts).values() for kinds in row.values())


def order_by_date(parts: list[dict]) -> list[dict]:
    """Запасной порядок — когда связей нет.

    Части идут по дате выхода. Основной историей считаются сериалы (tv и
    ona) по порядку, остальное — дополнениями. Это догадка, и страница так
    и говорит (поле guess): «порядок по датам выхода».

    «Начало» — первый сериал, а не самое раннее (почему — см. mark_main).
    """
    ordered = mark_main(sort_parts(parts))
    has_main_kind = any(p.get("kind") in MAIN_KINDS for p in ordered)
    step, anchor = 0, None
    for part in ordered:
        if not has_main_kind or part.get("kind") in MAIN_KINDS:
            step += 1
            part.update(role="main", step=step, anchor=None)
            anchor = part["id"]
            _label(part, "")
        else:
            part.update(role="extra", step=None, anchor=anchor)
            _label(part, "")
        part["guess"] = True
        part.pop("related", None)
    return ordered


def order_parts(parts: list[dict]) -> list[dict]:
    """Порядок просмотра: основная история, дополнения, прочее.

    Как он строится — в большом комментарии выше. Каждая часть получает:

      role   — "main" (основная история), "extra" (дополнение к ней) или
               "other" (другие версии и прочее, в конце списка);
      step   — номер в основной истории, 1..N (у остальных None);
      anchor — id части основной истории, после которой стоит дополнение;
      rel, rel_ru, rel_en — кем часть приходится основной истории:
               код («side_story») и короткая подпись на двух языках;
      main   — «начни отсюда», то есть шаг 1;
      guess  — False: порядок по связям, а не догадка по датам.

    Части меняются на месте — передавать сюда нужно копии, не кэш.
    Порядок не зависит от того, в каком виде части пришли: всё
    сортируется по дате и id.
    """
    if not parts:
        return parts
    if not has_links(parts):
        return order_by_date(parts)

    links = _link_map(parts)
    by_id = {p["id"]: p for p in parts}
    chain = _main_chain(parts, links)
    step = {pid: n + 1 for n, pid in enumerate(chain)}
    for pid, n in step.items():
        by_id[pid].update(role="main", step=n, anchor=None, main=n == 1)
        _label(by_id[pid], "")

    anchor: dict[str, str] = {}      # дополнение → часть основной истории
    others: dict[str, str] = {}      # часть из «прочего» → код связи
    rest = [p for p in sort_parts(parts) if p["id"] not in step]

    # Шаг 1: прямые связи с основной историей.
    for part in rest:
        pid = part["id"]
        to_main = [m for m in links[pid] if m in step]
        strong = [m for m in to_main if links[pid][m] - WEAK_LINKS]
        if strong:
            where = _closest(part, strong, step, by_id)
            anchor[pid] = where
            _label(part, _rel_code(links[where][pid] - WEAK_LINKS, part))
        elif to_main:
            # С основной историей связана только как «другая версия» —
            # в «прочее», даже если через соседей связана с чем-то ещё.
            kinds = set().union(*(links[m][pid] for m in to_main))
            others[pid] = _rel_code(kinds, part)

    # Шаг 2: связи через дополнения, волнами. Каждая волна видит только
    # то, что было привязано до неё, — порядок обхода ни на что не влияет.
    while True:
        wave = {}
        for part in rest:
            pid = part["id"]
            if pid in anchor or pid in others:
                continue
            via = sorted((y for y in links[pid]
                          if y in anchor and links[pid][y] - WEAK_LINKS),
                         key=lambda y: _sort_key(by_id[y]))
            if via:
                where = _closest(part, sorted({anchor[y] for y in via}), step, by_id)
                through = next(y for y in via if anchor[y] == where)
                wave[pid] = (where, _rel_code(links[through][pid] - WEAK_LINKS, part))
        if not wave:
            break
        for pid, (where, rel) in wave.items():
            anchor[pid] = where
            _label(by_id[pid], rel)

    # Шаг 3: всё остальное — в «прочее». Подпись — по «слабой» связи,
    # если она есть хоть с кем-то (пилот «Призрачной крови» — другая
    # экранизация того же, что и фильм 2007 года). Сильные связи здесь
    # не подписываем: «продолжение» внутри «прочего» читалось бы как
    # «продолжение основной истории», а это неправда.
    for part in rest:
        pid = part["id"]
        if pid not in anchor and pid not in others:
            kinds = set().union(set(), *links[pid].values()) & WEAK_LINKS
            others[pid] = _rel_code(kinds, part) if kinds else "unlinked"

    out: list[dict] = []
    for pid in chain:
        out.append(by_id[pid])
        for part in rest:                      # rest уже по дате
            if anchor.get(part["id"]) == pid:
                part.update(role="extra", step=None, anchor=pid, main=False)
                out.append(part)
    for part in rest:
        if part["id"] in others:
            part.update(role="other", step=None, anchor=None, main=False)
            _label(part, others[part["id"]])
            out.append(part)
    for part in out:
        part["guess"] = False
        part.pop("related", None)
    return out


def group_key(part: dict) -> str:
    """Под какой меткой часть попадёт в общую карточку.

    У большинства карточек справочника метка франшизы проставлена. Там,
    где её нет (одиночные фильмы, свежие анонсы), меткой становится само
    название: такая франшиза состоит из одной части, и это правда.
    """
    return short_id(part.get("franchise") or ("title:" + part["title"].lower()))


# Самая длинная метка франшизы, которая уходит наружу. Столько же
# оставляет от присланной метки franchise_parts, и меньше, чем принимает
# ручка /api/franchise.
ID_MAX = 80


def short_id(raw: str) -> str:
    """Метка франшизы не длиннее ID_MAX — одна и та же с обеих сторон.

    Раньше у тайтлов без франшизы меткой было «title:» и название целиком,
    любой длины, а franchise_parts обрезала присланную метку до 80
    знаков. У названий длиннее ~74 знаков (ранобэ, «Перерождение в ...»)
    метка в кэше и метка в запросе расходились, и список частей
    приходил пустым — и в поиске, и под плеером (/api/related).

    Длинную метку укорачиваем сами и дописываем к ней кусок отпечатка
    полного названия: два разных длинных названия с общим началом не
    склеятся в одну франшизу.
    """
    raw = str(raw or "")
    if len(raw) <= ID_MAX:
        return raw
    # Отпечаток здесь — не защита, а короткое имя: sha1 годится, и
    # usedforsecurity=False говорит об этом прямо (и анализатору тоже).
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return raw[:ID_MAX - 9] + "~" + digest


async def _franchise_pages(fid: str, hentai: bool = False) -> list[dict] | None:
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
        " censored: %s) { %s }" % (n, safe, FRANCHISE_LIMIT, n, _censored(hentai),
                                   GQL_FRANCHISE_FIELDS_X if hentai else GQL_FRANCHISE_FIELDS)
        for n in (1, 2)
    )
    data = await _ask_shiki_gql("query { " + ask + " }")
    if data is None:
        return None
    nodes = _list(data.get("p1")) + _list(data.get("p2"))
    if not nodes:
        return None

    parts: dict[str, dict] = {}
    for node in nodes:
        packed = pack_part(node)
        if packed and packed["kind"] not in JUNK_KINDS:
            parts[packed["id"]] = packed        # один и тот же id на стыке страниц
    # В кэш уходят части как есть — с датами и связями, но без пометок:
    # порядок просмотра собирается из них при каждом ответе (order_parts),
    # на копиях. Сотня частей сортируется за доли миллисекунды, а общий
    # кэш остаётся нетронутым.
    return sort_parts(list(parts.values()))


GQL_SEARCH = ("query ($s: String) { animes(search: $s, limit: 24, censored: true) {"
              + GQL_FIELDS + "} }")
# То же для тех, у кого включён хентай: без цензуры справочника и с полями
# для проверки на несовершеннолетних.
GQL_SEARCH_X = ("query ($s: String) { animes(search: $s, limit: 24, censored: false) {"
                + GQL_FIELDS_X + "} }")


def _ck(key: str, hentai: bool) -> str:
    """Ключ кэша. Ответы «с хентаем» и «без» лежат отдельно: это разные
    запросы к справочнику (censored), и в двадцать четыре места выдачи
    без цензуры влезает меньше обычного — делить их нельзя."""
    return ("x" + key) if hentai else key


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
        # Метка вида как есть («tv», «movie»), как у частей франшизы: по
        # ней страница сама пишет «сериал» или «TV series» на языке сайта.
        # Без неё английская версия показывала русское kind_ru.
        "kind": head.get("kind") or "",
        "kind_ru": head.get("kind_ru") or "",
        "episodes": head.get("episodes"),
        "score": head.get("score"),
        "rating": head.get("rating") or "",
        # Карточка «взрослая», только если взрослые ВСЕ найденные части:
        # франшиза, где 18+ только одна OVA, должна находиться и без 18+.
        "adult": bool(parts) and all(is_adult(p) for p in parts),
        # Хентай — по главной части: её название и обложка на карточке.
        # Франшизу, где хентай не главная часть, видят все (сам он из
        # списка частей уберётся), а где главная — см. nox в franchise_cards.
        "hentai": is_hentai(head),
        "alt": list(head.get("alt") or []),
    }


def franchise_cards(fid: str, parts: list[dict]) -> dict:
    """Карточка франшизы с заготовками для тех, кому видно не всё.

    safe — без 18+ вовсе (для тех, у кого оно выключено), если главная
           часть взрослая, а невзрослые части есть;
    nox  — без хентая (для тех, у кого он выключен), если главная часть —
           хентай, а остальные нет. У nox своя safe.
    Наружу заготовки не уходят: cards_for выбирает нужную и убирает их."""
    card = franchise_card(fid, parts)
    head = main_part(parts)
    if is_hentai(head):
        rest = [p for p in parts if not is_hentai(p)]
        if rest:
            card["nox"] = franchise_cards(fid, rest)
    safe = [p for p in parts if not is_adult(p)]
    if safe and is_adult(head):
        card["safe"] = franchise_card(fid, safe)
    return card


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


async def search_franchises(q: str, hentai: bool = False) -> list[dict] | None:
    """Поиск по справочнику: одна карточка на франшизу.

    Ровно один запрос наружу. Раскрывать здесь каждую найденную франшизу
    целиком — заманчиво и неправильно: на запрос «битва» их приходит
    одиннадцать, и человек ждал бы, пока подгрузятся все одиннадцать
    списков, чтобы прочитать первую строку и нажать на неё. Списки
    подгружаются по нажатию, по одному.

    None означает «справочник не ответил» — это не то же самое, что
    пустой список. По пустому списку сайт скажет «ничего не нашлось»,
    а по None уйдёт искать старым способом, прямо у источников видео.

    hentai=True — поиск для того, у кого включён хентай: справочник
    спрашивается без цензуры, ответ лежит в кэше отдельно (см. _ck).
    """
    q = (q or "").strip()[:100]
    if len(q) < 2:
        return []
    key = _ck("find:" + q.lower(), hentai)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    data = await _ask_shiki_gql(GQL_SEARCH_X if hentai else GQL_SEARCH, {"s": q})
    nodes = (data or {}).get("animes")
    if nodes is None:
        found = await _anilist_search(q, hentai)
        if found is None:
            return None
    else:
        found = [p for p in (pack_part(n) for n in nodes)
                 if p and p["kind"] not in JUNK_KINDS]

    if not found:
        # Справочник ответил, но ничего не нашёл. Запоминаем ненадолго.
        _cache_put(key, [], NOT_FOUND_TTL)
        return []

    order, grouped = group_found(found)
    cards = []
    for k in order:
        # Найденное по этой группе кладём в кэш заранее: если справочник
        # ляжет между поиском и нажатием, список частей всё равно
        # откроется — пусть неполный, но не пустой.
        _cache_put(_ck("part:" + k, hentai), sort_parts(grouped[k]))
        # Главная часть взрослая, а остальные нет — для тех, у кого 18+
        # выключено, заготовлена та же карточка без взрослых частей: иначе
        # в выдаче стояли бы их название и обложка. То же для хентая.
        cards.append(franchise_cards(k, grouped[k]))
    _cache_put(key, cards)
    return cards


def cards_for(cards: list[dict] | None, adult_ok: bool,
              hentai_ok: bool = False) -> list[dict]:
    """Карточки поиска для этого человека: копии, без служебных полей
    safe и nox (см. franchise_cards)."""
    out = []
    for card in cards or []:
        if not hentai_ok and is_hentai(card):
            card = card.get("nox")
            if not card:
                continue
        if not adult_ok:
            if card.get("adult"):
                continue
            if card.get("safe"):
                card = card["safe"]
        out.append({k: v for k, v in card.items() if k not in ("safe", "nox")})
    return out


# --------------------------------------------------------------------------
# Поиск по жанру 18+: «этти» и «хентай»
# --------------------------------------------------------------------------
# Настройка «Показывать 18+» была, а пользы от неё — почти никакой: поиск
# идёт по названию, и чтобы увидеть хоть один этти-тайтл, надо было заранее
# знать, как он называется. Человек включал настройку, ничего нового не
# видел и решал, что она не работает.
#
# Теперь запрос «этти» (и «ecchi», «эччи», «18+») — это не поиск по
# названию, а список самого популярного в жанре Ecchi. Только тому, у кого
# 18+ включено (решает main.py); остальным — подсказка, где включить.
#
# Хентай (rx) в список этти не попадает: censored: true в запросе, и ещё
# раз visible/cards_for на выходе — на случай, если флаг однажды забудут.
#
# Запрос «хентай» («hentai», «хент») — так же список самого популярного,
# но в жанре Hentai и без цензуры справочника. Только тому, у кого
# включены и 18+, и «Хентай» (main.hentai_ok); остальным — подсказка.
# Номера жанров у Shikimori те же, что у MyAnimeList: Ecchi — 9,
# Hentai — 12. У AniList жанр — словом, а хентай помечен ещё и isAdult.
SHIKI_GENRES = {"ecchi": "9", "hentai": "12"}
ANILIST_GENRES = {"ecchi": "Ecchi", "hentai": "Hentai"}
_GENRE_WORDS = {
    "этти": "ecchi", "эччи": "ecchi", "ecchi": "ecchi",
    "etti": "ecchi", "echi": "ecchi", "18+": "ecchi", "18 +": "ecchi",
    "хентай": "hentai", "хентаи": "hentai", "хент": "hentai", "hentai": "hentai",
    "hentay": "hentai",
}
# Жанры, которые ищутся без цензуры справочника.
UNCENSORED_GENRES = {"hentai"}
# Слова вокруг, которые смысла не меняют: «аниме этти», «жанр ecchi».
_GENRE_NOISE = {"аниме", "anime", "жанр", "genre", "тайтлы", "titles"}


def genre_query(q: str) -> str | None:
    """Если запрос — это жанр 18+, а не название, вернёт имя жанра."""
    words = [w for w in re.split(r"[\s,.!?]+", str(q or "").lower().replace("ё", "е"))
             if w and w not in _GENRE_NOISE]
    return _GENRE_WORDS.get(" ".join(words))


# Сколько страниц жанра хентай брать сверх первых двух. Хентай в выдаче
# остаётся только тот, что есть у источников (main.rx_cards), а это
# меньшинство: кандидатов нужно больше, чтобы список не вышел куцым.
HENTAI_MORE_PAGES = (3, 4)


def _gql_genre(gid: str, hentai: bool = False, pages: tuple = (1, 2)) -> str:
    """Две страницы жанра одним запросом, как у франшизы (_franchise_pages).
    Псевдонимы — p1, p2… по номеру страницы."""
    safe = re.sub(r"[^0-9]", "", gid)[:6]
    return "query { " + " ".join(
        'p%d: animes(genre: "%s", order: popularity, limit: 50, page: %d,'
        " censored: %s) { %s }" % (n, safe, n, _censored(hentai),
                                   GQL_FIELDS_X if hentai else GQL_FIELDS)
        for n in pages) + " }"


async def genre_parts(genre: str) -> list[dict] | None:
    """Самое популярное в жанре — части, по популярности. None — справочники
    молчат (оба), [] — ответили, но пусто."""
    key = "genre:" + genre
    hit = _cache_get(key)
    if hit is not None:
        return hit
    gid = SHIKI_GENRES.get(genre)
    if not gid:
        return []
    uncensored = genre in UNCENSORED_GENRES
    data = await _ask_shiki_gql(_gql_genre(gid, uncensored))
    if data is not None and (data.get("p1") is not None):
        nodes = _list(data.get("p1")) + _list(data.get("p2"))
        if uncensored and len(nodes) >= 100:
            # Ещё две страницы — отдельным запросом того же вида, что
            # первый: один запрос на двести карточек справочник может и
            # не принять, а без них список всё равно будет.
            more = await _ask_shiki_gql(_gql_genre(gid, True, HENTAI_MORE_PAGES))
            for n in HENTAI_MORE_PAGES:
                nodes += _list((more or {}).get("p%d" % n))
        found, seen = [], set()
        for node in nodes:
            part = pack_part(node)
            if part and part["kind"] not in JUNK_KINDS and part["id"] not in seen:
                seen.add(part["id"])
                found.append(part)
    else:
        found = await _anilist_genre(ANILIST_GENRES.get(genre, genre), uncensored)
        if found is None:
            return None
    if not uncensored:
        # Список этти — без хентая, кому бы он ни показывался.
        found = [p for p in found if not is_hentai(p)]
    _cache_put(key, found, CACHE_TTL if found else NOT_FOUND_TTL)
    return found


async def genre_cards(genre: str) -> list[dict] | None:
    """Жанр карточками франшиз — как обычный поиск, одна карточка на
    историю, в порядке популярности главной части."""
    found = await genre_parts(genre)
    if found is None:
        return None
    order, grouped = group_found(found)
    hentai = genre in UNCENSORED_GENRES
    cards = []
    for k in order:
        if not _cache_get(_ck("part:" + k, hentai)):
            _cache_put(_ck("part:" + k, hentai), sort_parts(grouped[k]))
        cards.append(franchise_cards(k, grouped[k]))
    return cards


async def _franchise_raw(fid: str, hentai: bool = False) -> list[dict] | None:
    """Части франшизы как они есть — без пометок, прямо из кэша или сети.
    hentai=True — без цензуры справочника, в своём кэше (см. _ck)."""
    hit = _cache_get(_ck("fr:" + fid, hentai))
    if hit is not None:
        return hit

    # Метки с двоеточием собраны нами, а не справочником: «title:…» —
    # группа из одного тайтла, «al:…» — склейка запасного справочника.
    # Спрашивать про них Shikimori бессмысленно, он таких не знает.
    if ":" not in fid:
        full = await _franchise_pages(fid, hentai)
        if full:
            _cache_put(_ck("fr:" + fid, hentai), full)
            return full

    known = _cache_get(_ck("part:" + fid, hentai))
    if known:
        return known
    # Кэш поиска рассыпался — соберём группу заново по названию, которое
    # осталось в самой метке.
    # У длинной метки на конце отпечаток (см. short_id) — в поиск он не
    # нужен, нужно только название.
    if ":" in fid and await search_franchises(
            re.sub(r"~[0-9a-f]{8}$", "", fid.split(":", 1)[1]), hentai) is not None:
        return _cache_get(_ck("part:" + fid, hentai)) or []
    return None


async def franchise_parts(fid: str, adult_ok: bool = False,
                          hentai_ok: bool = False, rx_ok: Any = None) -> list[dict] | None:
    """Все части одной франшизы в порядке просмотра (см. order_parts).

    Наружу уходят копии, а не записи из кэша: пометки — «начало» здесь и
    «сейчас смотрите» на странице просмотра — своё дело каждого ответа,
    а список лежит один на всех. Мутировать общий кэш под каждого —
    верный способ показать одному человеку то, что выбрал другой.

    rx_ok — проверка хентай-частей у источников (main.rx_parts_filter):
    получает список rx-частей, отвечает списком «оставить ли». Хентай,
    которого нет на языке человека, в списке частей не показывается.
    """
    fid = (fid or "").strip()[:ID_MAX]
    if not fid:
        return None
    parts = await _franchise_raw(fid, hentai_ok)
    if parts is None:
        return None
    # Порядок собирается заново на копиях. У записей без связей (найдены
    # поиском, пришли из запасного справочника, лежат в кэше с прошлой
    # версии) он сам откатится к порядку по датам.
    #
    # Взрослые части убираются ДО того, как строится порядок: иначе в
    # списке остались бы дополнения, привязанные к убранной части, и
    # дыры в номерах шагов.
    shown = visible(parts, adult_ok, hentai_ok)
    rx = [p for p in shown if is_hentai(p)] if hentai_ok and rx_ok else []
    if rx:
        keep = await rx_ok(rx)
        gone = {id(p) for p, ok in zip(rx, keep) if not ok}
        shown = [p for p in shown if id(p) not in gone]
    return order_parts([dict(p) for p in shown])


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


async def related_parts(title: str, adult_ok: bool = False,
                        hentai_ok: bool = False, rx_ok: Any = None) -> list[dict] | None:
    """Все части истории, к которой относится этот тайтл.

    На вход — название так, как его знает источник видео или полка;
    на выход — весь список франшизы в том же порядке и с теми же полями,
    что и в окне частей (role, step, rel…), где часть, открытая сейчас,
    помечена полем current.

    None означает «справочник не ответил»: страница по такому ответу
    просто не покажет блок, а не покажет пустой.
    """
    title = (title or "").strip()[:120]
    if len(title) < 2:
        return []

    cards = await search_franchises(title, hentai_ok)
    if cards is None:
        return None
    if not cards:
        return []

    # Справочник сортирует ответ по близости к запросу, поэтому нужная
    # франшиза — первая. Проверять это ещё раз своими силами нечем: у
    # карточки на руках только название главной части, а совпасть должно
    # с любой из них.
    out = await franchise_parts(cards[0]["id"], adult_ok, hentai_ok, rx_ok)
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
          " id idMal title { romaji english native } synonyms seasonYear episodes format"
          " genres isAdult startDate { year month day }"
          " averageScore coverImage { extraLarge large medium } } } }")


# Жанр у AniList — словом, а не номером. isAdult: false — хентая нет и здесь.
Q_GENRE_AL = ("query ($g: String) { Page(page: 1, perPage: 50) {"
              " media(type: ANIME, genre: $g, isAdult: false, sort: POPULARITY_DESC) {"
              " id idMal title { romaji english native } synonyms seasonYear episodes format"
              " genres isAdult startDate { year month day }"
              " averageScore coverImage { extraLarge large medium } } } }")

# Те же два запроса для того, у кого включён хентай. Поиск — без условия
# на isAdult (приходит и обычное, и хентай), жанр «Hentai» — только
# isAdult: true. В обоих — теги и описание: по ним minors_flagged.
_AL_X_FIELDS = (" id idMal title { romaji english native } synonyms seasonYear episodes format"
                " genres isAdult tags { name } description(asHtml: false)"
                " startDate { year month day }"
                " averageScore coverImage { extraLarge large medium } } } }")
Q_FIND_X = ("query ($s: String) { Page(page: 1, perPage: 25) {"
            " media(type: ANIME, search: $s) {" + _AL_X_FIELDS)
Q_GENRE_AL_X = ("query ($g: String) { Page(page: 1, perPage: 50) {"
                " media(type: ANIME, genre: $g, isAdult: true, sort: POPULARITY_DESC) {"
                + _AL_X_FIELDS)


def _base_name(title: str) -> str:
    """Название без хвоста сезона: «Jujutsu Kaisen 2nd Season» → «jujutsu
    kaisen». По нему части и склеиваются в одну франшизу.

    Пробелы сначала схлопываются, а длина режется: в шаблоне ниже два
    «\\s*» стоят рядом, и на названии из тысячи пробелов от справочника
    разбор занимал время, растущее как куб длины."""
    title = " ".join(str(title or "").split())[:200]
    text = re.split(r"\s*[:—–]\s*", title)[0].strip().lower()
    text = re.sub(r"\s*(?:\d+(?:st|nd|rd|th)?|[ivx]+)?\s*"
                  r"(?:season|part|movie|film|ova|ona|special|special edition)\s*\d*$",
                  "", text).strip()
    text = re.sub(r"\s+\d+$", "", text).strip()
    return text or (title or "").strip().lower()


async def _anilist_search(q: str, hentai: bool = False) -> list[dict] | None:
    """Тот же ответ, что у основного справочника, но из запасного."""
    data = await _ask_anilist(Q_FIND_X if hentai else Q_FIND, {"s": q})
    media = ((data or {}).get("Page") or {}).get("media")
    if media is None:
        return None
    return _anilist_parts(media, hentai)


async def _anilist_genre(genre: str, hentai: bool = False) -> list[dict] | None:
    """Самое популярное в жанре — из запасного справочника (см. genre_cards)."""
    data = await _ask_anilist(Q_GENRE_AL_X if hentai else Q_GENRE_AL, {"g": genre})
    media = ((data or {}).get("Page") or {}).get("media")
    if media is None:
        return None
    return _anilist_parts(media, hentai)


def _anilist_minors(m: dict) -> bool:
    """minors_flagged для записи AniList: теги, жанры, все названия, описание."""
    titles = m.get("title") or {}
    titles = titles if isinstance(titles, dict) else {}
    tags = [t for t in (m.get("tags") or []) if isinstance(t, dict)]
    return minors_flagged(tags + list(m.get("genres") or []),
                          [titles.get("romaji"), titles.get("english"), titles.get("native")]
                          + list(m.get("synonyms") or []),
                          m.get("description") or "")


def _anilist_parts(media: list, hentai: bool = False) -> list[dict]:
    """Записи AniList в том же виде, что части у Shikimori (pack_part).
    Хентай (isAdult) — только при hentai=True и без признаков
    несовершеннолетних; иначе отбрасывается сразу."""
    out: list[dict] = []
    for m in _list(media):
        part = _anilist_part(m, hentai)
        if part is not None:
            out.append(part)
            remember_names(part)
            if part["hentai"]:
                _rx_mark(_part_names(part), True)
    return out


@_tolerant
def _anilist_part(m: object, hentai: bool = False) -> dict | None:
    """Одна запись AniList (см. _anilist_parts): часть или None."""
    if not isinstance(m, dict):
        return None
    titles = m.get("title") if isinstance(m.get("title"), dict) else {}
    name = (_str(titles.get("romaji")) or _str(titles.get("english"))
            or _str(titles.get("native")))
    if not name:
        return None
    kind = ANILIST_KIND.get(_str(m.get("format")), "")
    if kind in JUNK_KINDS:
        return None
    rating = anilist_rating(m)
    if rating in HENTAI_RATINGS and (not hentai or _anilist_minors(m)):
        if hentai:
            _rx_mark([titles.get("romaji"), titles.get("english")] + _strs(m.get("synonyms")),
                     False)
        return None
    cover = m.get("coverImage") if isinstance(m.get("coverImage"), dict) else {}
    score = m.get("averageScore")
    start = m.get("startDate") if isinstance(m.get("startDate"), dict) else {}
    try:
        date = "%04d-%02d-%02d" % (start["year"], start["month"], start["day"])
    except (KeyError, TypeError):
        date = None
    return {
        "id": str(m.get("id") or ""),
        "cid": catalog_id(anilist_id=m.get("id"), mal_id=m.get("idMal")),
        "title": name,
        "title_en": _str(titles.get("english")),
        # Метка склеивает части одной истории между собой и ничего
        # не значит за пределами этого ответа — отсюда и приставка.
        "franchise": "al:" + _base_name(name),
        "year": _year_of(m.get("seasonYear")),
        # Связей между частями AniList в этом запросе не отдаёт, так что
        # порядок здесь — запасной, по датам. Полная дата делает его
        # хотя бы точным внутри года.
        "date": date,
        "kind": kind,
        "kind_ru": KIND_RU.get(_str(m.get("format")), kind),
        "episodes": _whole(m.get("episodes")) or None,
        "poster": (_poster_url(cover.get("extraLarge")) or _poster_url(cover.get("large"))
                   or _poster_url(cover.get("medium"))),
        "score": int(score) if isinstance(score, int) and not isinstance(score, bool) else None,
        "rating": rating,
        "adult": rating in ADULT_RATINGS or rating in HENTAI_RATINGS,
        "hentai": rating in HENTAI_RATINGS,
        "alt": [n for n in ([titles.get("english")] + _strs(m.get("synonyms")))
                if isinstance(n, str) and n.strip() and n != name][:6],
    }
