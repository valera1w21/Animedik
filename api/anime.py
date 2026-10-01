"""Работа с источниками аниме.

Код взят из рабочей версии почти без изменений: он проверен и делает
ровно то, что нужно. Изменилось одно — модуль больше не создаёт
веб-приложение, а только отдаёт данные.
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

# Режим работы. По умолчанию — личный: сайт ходит на внешние источники.
#
# MODE=demo переключает его на демонстрационный: вместо чужого видео
# плеер играет фильмы Blender Foundation под Creative Commons. Всё
# остальное — поиск по справочнику, франшизы, список, отметки о
# просмотре — работает как обычно, потому что справочные данные открыты.
#
# Переключатель именно здесь, в одном месте: остальной код не должен
# знать, откуда взялось видео, иначе разница расползётся по всему
# проекту и однажды где-нибудь протечёт не тот источник.
MODE = os.getenv("MODE", "private").strip().lower()
DEMO = MODE == "demo"


def upstream_error(exc: Exception, what: str) -> HTTPException:
    """Подробности в лог, наружу — общая фраза.

    Текст исключения может содержать внутренние адреса и куски чужого
    ответа. В браузер это отдавать незачем.
    """
    log.warning("%s: %s: %s", what, type(exc).__name__, exc)
    return HTTPException(status_code=502, detail=what)


SOURCES: dict[str, dict[str, Any]] = {}
"""Откуда сайт берёт видео.

Здесь пусто, и это намеренно. Публичная версия не ходит ни на какие
внешние источники: приложение работает в демонстрационном режиме и
крутит свободное видео Blender Foundation (см. `anime_demo.py`).

Чтобы подключить свой источник, нужны две вещи.

1. Добавить запись сюда:

       SOURCES["мой_источник"] = {
           "lang": "ru",                # язык озвучки: ru или en
           "label": "Мой источник",     # как показать в меню
           "base": "https://…",         # адрес, к которому клеятся пути картинок
           "dubs": "many",              # одна озвучка или много
           "note": "Короткая заметка",
       }

2. Написать модуль с четырьмя объектами — так же, как это делает
   `anime_demo.py`, его можно взять за образец:

       Extractor.a_search(query)  -> [Search]
       Search.a_get_anime()       -> Anime
       Anime.a_get_episodes()     -> [Episode]
       Episode.a_get_sources()    -> [Source]
       Source.a_get_videos()      -> [Video(url, quality, type)]

   и вернуть его из `get_extractor` ниже.

Всё остальное — поиск, франшизы, список, отметки о просмотре, плеер —
работает поверх этого интерфейса и ничего не знает о самом источнике.

Что бы вы ни подключили, вы сами за это отвечаете.
"""


if DEMO:
    # В демонстрационном режиме источник ровно один, и он один на оба
    # языка: свободное видео не бывает «русским» или «английским».
    SOURCES = dict(anime_demo.SOURCES)


def sources_for(lang: str) -> list[str]:
    """Источники того же языка, что и сайт.

    Зачем разделение. Источники говорят каждый на своём языке: русские
    дают русские озвучки, англоязычный — английские субтитры и английский
    дубляж. Если их не разделять, человек, открывший сайт по-английски,
    может получить в меню русскую озвучку, а русскоязычный — источник,
    где всё по-английски.

    Ни то, ни другое не ошибка источника — это мы показываем ему то,
    чего он не просил. Язык сайта решает, из чего вообще есть выбор.
    """
    if DEMO:
        return list(SOURCES)
    want = "en" if lang == "en" else "ru"
    return [sid for sid, meta in SOURCES.items() if meta.get("lang", "ru") == want]


def source_lang(source: str) -> str:
    """На каком языке говорит этот источник."""
    return SOURCES.get(source, {}).get("lang", "ru")


# Источник по умолчанию для каждого языка. Пусто: подключать нечего,
# и любой запрос уходит в демонстрационный режим.
DEFAULT_SOURCE: dict[str, str] = {}


def default_source(lang: str) -> str:
    if DEMO:
        return anime_demo.NAME
    return DEFAULT_SOURCE.get("en" if lang == "en" else "ru", anime_demo.NAME)


_extractors: dict[str, Any] = {}


def get_extractor(source: str):
    """Достаёт разборщик источника. Имя источника обязано быть из SOURCES.

    Загрузка модуля обёрнута намеренно. Раньше не была — и любая беда с
    ним (пакет не установлен, обновление переименовало модуль, внутри
    модуля ошибка) прилетала наверх как ImportError и показывалась
    человеку как «Внутренняя ошибка» нашего сервера. При этом ровно та же
    беда в поиске обрабатывалась правильно, потому что там весь вызов
    обёрнут снаружи. Получалось, что поиск честно говорит «источник не
    отвечает», а список серий у того же источника — «всё сломалось».
    """
    if source not in SOURCES:
        raise HTTPException(status_code=400, detail=f"Неизвестный источник: {source}")
    if source not in _extractors:
        try:
            if DEMO:
                _extractors[source] = anime_demo.Extractor()
            else:
                # Сюда попадает разборщик вашего источника. Пока его нет,
                # остаётся только демонстрационный режим.
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


# ------------------------------------------------------------------- кэш
_cache: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 3 * 60 * 60  # три часа
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


# -------------------------------------------------------------- помощники
def stable_key(item: Any) -> str:
    """Постоянный номер тайтла у источника. Его мы храним в базе."""
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
    """Вытаскивает жанры. У каждого источника они лежат по-своему:
    где-то список строк, где-то список словарей с полем name."""
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
    """Год выпуска. Иногда это число, иногда словарь с полем year."""
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
    """Ищет самую крупную обложку.

    Источники кладут рядом несколько размеров: миниатюру и полную картинку.
    Раньше бралась миниатюра, и на карточке она выглядела мыльной.
    Порядок полей ниже — от большего к меньшему.
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
    """Достаём объект Anime из кэша, а если его там нет — ищем заново.

    Здесь была тихая, но дорогая ошибка. Поиск складывал каждый найденный
    тайтл под ключом `raw:источник:номер`, а эта функция искала под
    `anime:источник:номер`. Совпадения не случалось никогда: записи `raw:`
    не читал никто, они только занимали место в кэше (один поиск на тридцать
    результатов — тридцать одна запись при потолке в восемьсот) и вытесняли
    оттуда всё полезное. В итоге каждый заход в список серий заново ходил
    на внешний сайт, хотя нужный объект лежал рядом.

    Теперь заготовка из поиска используется по назначению.
    """
    cached = cache_get(f"anime:{source}:{key}")
    if cached is not None:
        return cached

    # Заготовка, оставшаяся от поиска: тайтл уже найден, идти в сеть незачем.
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

    # Этот вызов тоже ходит в сеть, и раньше он был единственным во всём
    # файле, не обёрнутым в обработку. Любой сбой источника здесь улетал
    # наверх как внутренняя ошибка сервера: человек видел «Внутренняя
    # ошибка» и решал, что сломан сайт, хотя не отвечал чужой сайт.
    try:
        anime = await match.a_get_anime()
    except Exception as exc:                       # noqa: BLE001
        raise upstream_error(exc, "Источник не ответил")
    cache_put(f"anime:{source}:{key}", anime)
    return anime


async def find_poster(source: str, key: str, title: str) -> str:
    """Ищет обложку тайтла у источника.

    Нужна, когда тайтл попал в список без картинки: по закладке, по чужой
    ссылке, или он сохранялся ещё старой версией. Раньше такой тайтл
    оставался с серым прямоугольником навсегда — обложку никто и нигде
    не переспрашивал.

    Сначала смотрим в кэше поиска: там уже лежат разобранные карточки,
    и в сеть идти не придётся вовсе. Только если не нашлось — один поиск
    по названию.
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
    # Кладём в кэш даже пустой ответ: иначе страница будет ходить в сеть
    # за одной и той же несуществующей картинкой при каждом открытии.
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


# ------------------------------------------------------- совпадение названий
# Зачем это здесь.
#
# Поиск у источников — не поиск, а «покажи что-нибудь похожее». На запрос
# «Атака титанов» источник может ответить одной строкой: «Не издевайся,
# Нагаторо: Вторая атака». Совпало слово «атака», и этого ему хватило.
# А перебор источников останавливался на первом, кто вообще ответил
# хоть чем-то, — то есть на этой самой Нагаторо. Дальше по списку мог
# лежать источник, у которого «Атака титанов» есть со всеми сезонами,
# но до него очередь не доходила никогда.
#
# Поэтому ответ источника теперь взвешивается: насколько название похоже
# на то, что просили. Мусор уезжает вниз, а источник, у которого нет
# ничего похожего, считается не ответившим.

_TAIL_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_PUNCT = re.compile(r"[^\w\s]+", re.U)


def normalize_title(text: str) -> str:
    """Название в виде, пригодном для сравнения.

    Некоторые источники пишут названия так: «Наруто / Naruto [1-220 из
    220]» — русское имя, латинское имя и счётчик серий в одной строке.
    Сравнивать с этим бесполезно, поэтому хвосты отрезаются: в скобках
    счётчики, после косой черты — второе имя того же тайтла.
    """
    text = (text or "").strip()
    text = _TAIL_BRACKETS.sub(" ", text)
    head = text.split(" / ")[0] if " / " in text else text
    head = head.lower().replace("ё", "е")
    head = _PUNCT.sub(" ", head)
    return re.sub(r"\s+", " ", head).strip()


def relevance(query: str, title: str) -> float:
    """Насколько название отвечает запросу: от 0 до 1.

    Числа подобраны так, чтобы «Наруто» на запрос «наруто» стояло выше
    «Боруто: Новое поколение Наруто», а «Не издевайся, Нагаторо: Вторая
    атака» на запрос «атака титанов» не проходило порог вовсе.
    """
    q = normalize_title(query)
    t = normalize_title(title)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    if t.startswith(q):
        # Запрос целиком в начале названия: «наруто» → «наруто ураганные
        # хроники». Считаем лишние слова, а не лишние буквы: одно слово
        # хвоста это обычно приписка вроде «(ТВ)», а два-три — уже другой
        # сезон или другая история.
        #
        # Мера была по буквам, и на этом всё ломалось молча: «Наруто
        # Ураганные хроники» набирало 0.855 на запрос «Наруто» — выше
        # порога, на котором перебор источников останавливался. То есть
        # человек выбирал в справочнике первый сезон, а открывался второй,
        # и выглядело это ровно как та беда, которую чинили.
        extra = max(0, len(t.split()) - len(q.split()))
        return max(0.72, 0.90 - 0.06 * extra)
    if q in t:
        return 0.70
    if len(t) >= 4 and q.startswith(t + " "):
        # Наоборот: источник пишет название КОРОЧЕ, чем справочник.
        # «Евангелион нового поколения» у всех трёх источников лежит просто
        # как «Евангелион», и по словам это давало 1 из 3 — ниже порога.
        # Тайтл существует у всех, а сайт отвечал «ни один источник его не
        # выложил».
        #
        # Оценка нарочно низкая, и это не осторожность ради осторожности.
        # Отличить «Евангелион нового поколения» → «Евангелион» (то же
        # самое, просто короче) от «Наруто Ураганные хроники» → «Наруто»
        # (другой сезон) по строкам нельзя никак. Поэтому такое совпадение
        # проходит порог, но точным не считается: если рядом найдётся
        # настоящее совпадение, победит оно, а если нет — страница
        # покажет найденное название и спросит, то ли это.
        return 0.50
    q_words = q.split()
    t_words = t.split()

    def known(word: str) -> bool:
        for other in t_words:
            if other == word:
                return True
            # Приставка длиной от четырёх букв ловит падежи: «титанов»
            # и «титаны» — одно и то же слово, а «атака» и «академия» —
            # разные.
            if len(word) >= 4 and len(other) >= 4:
                if other.startswith(word) or word.startswith(other):
                    return True
        return False

    hit = sum(1 for w in q_words if known(w))
    return 0.62 * hit / len(q_words)


# Ниже этого совпадения ответ источника считается не относящимся к делу.
# Половина слов запроса даёт 0.31 — «Вторая атака» на «Атака титанов»
# отсеивается. Одно слово из одного даёт 0.62 и проходит.
MIN_RELEVANCE = 0.45
