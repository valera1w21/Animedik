"""Работа с источниками аниме.

Код взят из рабочей версии почти без изменений: он проверен и делает
ровно то, что нужно. Изменилось одно — модуль больше не создаёт
веб-приложение, а только отдаёт данные.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import importlib.util
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urljoin

from fastapi import HTTPException

from . import anime_demo
from . import store as _store
from .store import clean_poster

log = logging.getLogger("anime.sources")

# Режим работы. По умолчанию — демонстрационный (публичная витрина):
# вместо чужого видео плеер играет фильмы Blender Foundation под Creative
# Commons. Всё остальное — поиск по справочнику, франшизы, список, отметки
# о просмотре — работает как обычно, потому что справочные данные открыты.
#
# MODE=private — личный: видео берётся с источников, которые подключили
# сами (SOURCES ниже). Без них ручки видео отвечают 501.
#
# Переключатель именно здесь, в одном месте: остальной код не должен
# знать, откуда взялось видео, иначе разница расползётся по всему
# проекту и однажды где-нибудь протечёт не тот источник.
MODE = os.getenv("MODE", "demo").strip().lower()
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
внешние источники видео: по умолчанию сайт работает в демонстрационном
режиме (MODE=demo) и крутит свободное видео Blender Foundation (см.
`anime_demo.py`). В личном режиме (MODE=private) без источников ручки
видео честно отвечают 501 «Источники видео не подключены».

Чтобы подключить свой источник, нужны три шага.

1. Добавить запись сюда (имя — латиницей, до 40 знаков):

       SOURCES["moy_istochnik"] = {
           "lang": "ru",                # язык источника: ru или en
           "label": "Мой источник",     # как показать в меню
           "base": "https://…",         # адрес, к которому клеятся пути картинок
           "dubs": "many",              # одна озвучка (one) или много (many)
           "note": "Короткая заметка",
           # необязательные поля:
           # "tracks": ("sub", "dub"),  # для en: что у него бывает —
           #                            # текст поверх оригинала и/или дубляж
           # "rx_only": True,           # только хентай (см. RX_ORDER)
           # "episodes_in": "card",     # список серий приходит вместе с
           #                            # карточкой тайтла ("card") или прямо
           #                            # из поиска ("search") — тогда
           #                            # «свежий» список (колокольчик)
           #                            # выбрасывает из кэша и их
           # "library": "имя_модуля",   # Python-библиотека, без которой
           #                            # разборщик не работает (см. LIBRARIES)
       }

2. Написать разборщик — объект с четырьмя шагами, так же, как это
   делает `anime_demo.py` (его можно взять за образец):

       Extractor.a_search(query)  -> [Search]
       Search.a_get_anime()       -> Anime
       Anime.a_get_episodes()     -> [Episode]
       Episode.a_get_sources()    -> [Source]
       Source.a_get_videos()      -> [Video(url, quality, type)]

   и положить в EXTRACTORS функцию, которая его создаёт:

       EXTRACTORS["moy_istochnik"] = lambda: MoyExtractor()

3. Вписать имя в порядки перебора, если нужно: DEFAULT_SOURCE (источник
   по умолчанию для языка), EN_ORDER (английские), RX_ORDER (только
   хентай) здесь и main.FALLBACK_ORDER (русские, если выбранный молчит),
   main.SUB_ORDER (где искать субтитры). Без этого источник доступен
   только при ручном выборе в меню.

Всё остальное — поиск, франшизы, список, отметки о просмотре, плеер —
работает поверх этого интерфейса и ничего не знает о самом источнике.

Что бы вы ни подключили, вы сами за это отвечаете.

В комментариях и проверках встречаются условные имена источников:
RU-A…RU-H — русские, EN-A…EN-F — английские, RX-A — источник хентая.
Это обезличенные истории из жизни личной версии («у одного источника
было так»); настоящих имён в публичной версии нет. Проверки подставляют
такие подменные источники сами (fake_sources.py).
"""

# Разборщики источников: имя из SOURCES → функция, которая создаёт
# разборщик (см. шаг 2 выше). Пусто, как и SOURCES.
EXTRACTORS: dict[str, Any] = {}

# Источники хентая, по порядку (main.rx_groups, api_resolve). Пусто.
RX_ORDER: list[str] = []

# Показывал ли наш справочник этот хентай чистым (без признаков
# несовершеннолетних): catalog.rx_cleared(название) -> bool. Подключается
# из main (catalog отсюда импортировать нельзя). Источник хентая обязан
# спрашивать её до похода на свой сайт: брать только то, что справочник
# показал, — правило владельца.
RX_CLEARED: list[Any] = [None]


def rx_only(source: str) -> bool:
    """Источник только для хентая (поле "rx_only")?"""
    return bool(PRIVATE_SOURCES.get(source, {}).get("rx_only"))


# Английские источники и их порядок — одним списком. Переопределяется
# переменной EN_SOURCES в .env (через запятую) — без перезаписи кода.
# Неизвестные имена отбрасываются; пусто — порядок по умолчанию.
EN_DEFAULT_ORDER: list[str] = []
EN_ORDER = [x for x in (os.getenv("EN_SOURCES") or "").replace(" ", "").split(",")
            if x in EN_DEFAULT_ORDER] or list(EN_DEFAULT_ORDER)


# Источники личной версии — все, какие сайт вообще знает. Нужны и в
# демо-режиме: браузер может прислать имя личного источника (оно у него
# по умолчанию), и это не мусор, а известное имя не из этого режима.
PRIVATE_SOURCES: dict[str, dict[str, Any]] = SOURCES

if DEMO:
    # В демонстрационном режиме источник ровно один, и он один на оба
    # языка: свободное видео не бывает «русским» или «английским».
    SOURCES = dict(anime_demo.SOURCES)

# Склейке записей полки (store.is_twin) нужен язык источника по имени —
# store anime не импортирует (наоборот), поэтому кладём сами.
_store.SOURCE_LANG.update({k: str(v.get("lang") or "ru") for k, v in PRIVATE_SOURCES.items()})

# Что ответить, когда сайт в личном режиме, а источников нет.
NO_SOURCES = ("Источники видео не подключены (api/anime.py, SOURCES). "
              "Работает демонстрационный режим: MODE=demo.")


def no_sources() -> bool:
    """Личный режим без единого подключённого источника?"""
    return not DEMO and not SOURCES


def need_sources() -> None:
    """Ручки видео в личном режиме без источников честно отвечают 501
    («не сделано»), а не «источник не отвечает» и не «неизвестный
    источник»: чинить тут нечего, пока источник не подключён."""
    if no_sources():
        raise HTTPException(status_code=501, detail=NO_SOURCES)


def sources_for(lang: str) -> list[str]:
    """Источники того же языка, что и сайт.

    Зачем разделение. Источники говорят каждый на своём языке: русские
    дают русские озвучки, англоязычные — английские субтитры и английский
    дубляж. Пока они лежали в одной куче, человек, открывший сайт
    по-английски, получал в меню русский источник с одной озвучкой и,
    выбрав его, японскую речь под русский дубляж. Обратное тоже верно:
    русскому в списке предлагался английский источник.

    Ни то, ни другое не ошибка источника — это мы показывали ему то,
    чего он не просил. Теперь язык сайта решает, из чего вообще есть
    выбор.
    """
    if DEMO:
        return list(SOURCES)
    # Принимает и язык источников («ru»/«en»), и выбор человека целиком
    # («ensub», «endub»). Для выбора целиком английские источники ещё и
    # отбираются по варианту: у кого есть только дубляж (поле "tracks"),
    # тот при «English — субтитры» не спрашивается.
    want = anime_lang(lang)
    if want == "en":
        # «en» — старое имя ensub (anime_mode), читается так же.
        raw = (lang or "").strip().lower()
        track = anime_track(raw) if raw in ("en", "ensub", "endub") else ""
        return [sid for sid in EN_ORDER if sid in SOURCES
                and (not track or track in SOURCES[sid].get("tracks", ("sub", "dub")))]
    # Источники только для хентая (rx_only) сюда не попадают никогда.
    return [sid for sid, meta in SOURCES.items() if meta.get("lang", "ru") == want
            and not meta.get("rx_only")]


def menu_sources(mode: str | None) -> list[str]:
    """Что показать в меню «Источник»: источники языка плюс те, что
    подходят этому выбору только отчасти и только для ручного выбора
    (поле "also" — например, русский источник с вариантом «оригинал с
    субтитрами» при «English — субтитры»).

    В перебор (поиск, резолв, «у кого есть», проверка хентая) такие не
    попадают: туда идёт sources_for. Иначе сайт сам уводил бы человека,
    просившего английский текст, туда, где текст может оказаться русским.
    """
    base = sources_for(mode)
    if DEMO:
        return base
    want = anime_mode(mode)
    return base + [sid for sid, meta in SOURCES.items()
                   if sid not in base and want in meta.get("also", ())]


def source_lang(source: str) -> str:
    """На каком языке говорит этот источник."""
    return SOURCES.get(source, {}).get("lang", "ru")


# Источник по умолчанию для каждого языка: {"ru": имя, "en": имя}. Пусто —
# тогда берётся первый подходящий из sources_for (а для английского — из
# EN_ORDER).
DEFAULT_SOURCE: dict[str, str] = {}


def default_source(lang: str) -> str:
    if DEMO:
        return anime_demo.NAME
    if anime_lang(lang) == "en":
        mine = sources_for(lang)
        return mine[0] if mine else (EN_ORDER[0] if EN_ORDER else "")
    chosen = DEFAULT_SOURCE.get(anime_lang(lang), "")
    if chosen:
        return chosen
    mine = sources_for(lang)
    return mine[0] if mine else ""


# Язык аниме: что звучит в плеере и каким текстом подписано.
#
# Язык сайта (меню и кнопки) и язык аниме — две разные настройки. Раньше
# они были одной: переключил меню на английский — и поиск ушёл к
# англоязычному источнику, хотя человек хотел только английские кнопки и
# русскую озвучку. Теперь источники выбираются только по языку аниме.
#
# Вариантов три:
#   ru    — русские озвучки (и русские субтитры, если искать их отдельно);
#   endub — английский дубляж;
#   ensub — японская дорожка с английским текстом.
# Оба английских берутся у английских источников (EN_ORDER): у них на
# тайтл варианты sub и dub. Разница
# между ними — что открыть первым (см. main.api_videos) и какой тайтл
# считать «можно посмотреть» (main.rx_check).
#
# "en" — так английский назывался, пока был один. Читается как ensub:
# прежний порядок для английского был «сначала субтитры».
ANIME_LANGS = ("ru", "endub", "ensub")
_LEGACY_MODES = {"en": "ensub"}


def anime_mode(value: str | None) -> str:
    """Выбор человека целиком: ru, endub или ensub. Мусор — русский."""
    value = (value or "").strip().lower()
    value = _LEGACY_MODES.get(value, value)
    return value if value in ANIME_LANGS else "ru"


def anime_lang(value: str | None) -> str:
    """Язык источников для этого выбора: «ru» или «en».

    Почти всему серверу нужен именно он: какие источники спрашивать, в
    каком порядке и какой из них основной."""
    return "ru" if anime_mode(value) == "ru" else "en"


def anime_track(value: str | None) -> str:
    """Что открывать первым: «dub» (озвучку) или «sub» (текст поверх
    оригинала)."""
    return "sub" if anime_mode(value) == "ensub" else "dub"


# Другие имена тайтла по его названию — справочник (catalog.alt_names).
# Подключается из main: импортировать catalog отсюда нельзя, он сам
# импортирует этот модуль.
ALT_LOOKUP: list[Any] = [None]

_extractors: dict[str, Any] = {}


# Какая Python-библиотека нужна какому разборщику: имя модуля → имя пакета
# для pip. Пусто: своих библиотек у публичной версии нет. Подключая
# источник на сторонней библиотеке, впишите её сюда и в поле "library" его
# записи в SOURCES — тогда сервер без неё честно скажет 503 «не установлена
# библиотека», а не «источник молчит», и предупредит об этом при запуске.
LIBRARIES: dict[str, str] = {}
LIBRARIES_HINT = ("На сервере не установлены библиотеки источников "
                  "(api/requirements-private.txt). Видео в личном режиме "
                  "без них не работает.")


class LibrariesMissing(HTTPException):
    """Библиотеки источников не установлены — это не «источник молчит».

    Раньше отсутствие пакета превращалось в «Источник сейчас недоступен»,
    а поиск — в «Ни один источник сейчас не отвечает». Человек ждал, пока
    чужие сайты оживут, хотя они были ни при чём: сломан наш сервер, и
    починить это может только тот, кто его ставил. Отдельный класс нужен,
    чтобы перебор источников не глотал эту ошибку как обычное молчание.
    """

    def __init__(self) -> None:
        super().__init__(status_code=503, detail=LIBRARIES_HINT)


def missing_libraries() -> list[str]:
    """Каких библиотек источников нет на сервере. В демо-режиме — никаких.

    find_spec только ищет пакет и не загружает его: библиотека может быть
    тяжёлой, и грузить её ради проверки при каждом запуске незачем.
    """
    if DEMO:
        return []
    out = []
    for module in LIBRARIES:
        try:
            if importlib.util.find_spec(module) is None:
                out.append(module)
        except (ImportError, ValueError):
            out.append(module)
    return out


def _library_for(source: str) -> str:
    """Библиотека, без которой разборщик источника не работает ("" — никакой)."""
    return str(SOURCES.get(source, {}).get("library") or "")


def get_extractor(source: str):
    """Достаёт разборщик источника. Имя источника обязано быть из SOURCES.

    Создание разборщика обёрнуто намеренно. Раньше не было — и любая беда
    с ним (пакет не установлен, обновление переименовало модуль, внутри
    модуля ошибка) прилетала наверх как ImportError и показывалась
    человеку как «Внутренняя ошибка» нашего сервера. При этом ровно та же
    беда в поиске обрабатывалась правильно, потому что там весь вызов
    обёрнут снаружи. Получалось, что поиск честно говорит «источник не
    отвечает», а список серий у того же источника — «всё сломалось».

    Отсутствие самой библиотеки — отдельный случай: это не источник
    молчит, а сервер собран без неё. Об этом говорим прямо, кодом 503.
    """
    need_sources()
    if source not in SOURCES:
        raise HTTPException(status_code=400, detail=f"Неизвестный источник: {source}")
    if source not in _extractors:
        if not DEMO and _library_for(source) and _library_for(source) in missing_libraries():
            log.error("нет библиотеки %s для источника %s — поставьте "
                      "api/requirements-private.txt", _library_for(source), source)
            raise LibrariesMissing()
        try:
            if DEMO:
                _extractors[source] = anime_demo.Extractor()
            elif source in EXTRACTORS:
                _extractors[source] = EXTRACTORS[source]()
            else:
                # Запись в SOURCES есть, а разборщика нет (шаг 2 в описании
                # SOURCES не сделан) — это «не сделано», а не «молчит».
                raise HTTPException(status_code=501, detail=NO_SOURCES)
        except HTTPException:
            raise
        except Exception as exc:                   # noqa: BLE001
            raise upstream_error(exc, f"Источник {source} сейчас недоступен")
    return _extractors[source]


# ---------------------------------------------------------- сроки ожидания
# Сколько ждём одну операцию у источника: поиск, карточку тайтла, список
# серий. Живой источник отвечает за одну-семь секунд.
CALL_TIMEOUT = 20.0
# Сколько всего может занять один запрос к нашему сайту, если он ходит по
# нескольким источникам подряд (поиск с перебором, резолв, субтитры).
# nginx ждёт 120 секунд; половины хватает с запасом, а человек, который
# минуту смотрит на крутилку, уже и так решил, что сайт сломан.
BUDGET = 60.0
TIMEOUT_DETAIL = "Источник не отвечает. Попробуйте ещё раз через минуту."

# Когда истекает общий срок текущего запроса. Переменная контекста, а не
# параметр: срок нужен глубоко внутри (find_episodes зовёт find_anime, тот
# — поиск), и тащить его аргументом через каждую функцию значило бы
# переписать половину файла. asyncio сам раздаёт копию контекста каждой
# задаче, так что параллельные проверки в /api/where видят тот же срок.
_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "source_deadline", default=None)


@contextlib.contextmanager
def budget(seconds: float = BUDGET):
    """Общий срок на всё, что внутри. Вложенный срок не продлевает внешний."""
    edge = time.monotonic() + seconds
    outer = _deadline.get()
    if outer is not None:
        edge = min(edge, outer)
    token = _deadline.set(edge)
    try:
        yield
    finally:
        _deadline.reset(token)


def time_left() -> float:
    """Сколько можно ждать следующую операцию: её срок или остаток общего."""
    left = CALL_TIMEOUT
    edge = _deadline.get()
    if edge is not None:
        left = min(left, edge - time.monotonic())
    return left


async def call(awaitable: Any, what: str = "источник") -> Any:
    """Ждёт ответа источника, но не дольше положенного.

    Без этого запрос висел, пока библиотека не исчерпает свои повторы, —
    до полутора минут на один вызов. Теперь по истечении срока человек
    получает понятное «Источник не отвечает», а не обрыв от nginx.
    """
    left = time_left()
    if left <= 0:
        # Срок вышел ещё до начала. Корутину закрываем, иначе Python
        # ругается в лог, что её так и не дождались.
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        raise HTTPException(status_code=504, detail=TIMEOUT_DETAIL)
    try:
        return await asyncio.wait_for(awaitable, left)
    except asyncio.TimeoutError:
        log.warning("%s не ответил за %.0f с", what, left)
        raise HTTPException(status_code=504, detail=TIMEOUT_DETAIL) from None


# ------------------------------------------------------------------- кэш
_cache: dict[str, tuple[float, Any, float]] = {}
CACHE_TTL = 3 * 60 * 60  # три часа
# Потолок поднят с 800 (fix-21): сюда же теперь ложатся готовые резолвы,
# списки плееров и ссылки серий (main.resolve_cached, main.api_videos), а
# вытеснение полезного ради нового — это снова походы к источникам.
CACHE_MAX = 3000
# Сколько живёт неполный ответ: например, у англоязычного источника
# субтитры отдались, а дубляж — нет. Совсем без кэша такой тайтл ходил бы
# в сеть при каждом открытии, а на три часа — три часа без дубляжа.
SHORT_TTL = 5 * 60


def cache_put(key: str, value: Any, ttl: float = CACHE_TTL) -> None:
    if len(_cache) > CACHE_MAX:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: CACHE_MAX // 2]
        for k, _ in oldest:
            _cache.pop(k, None)
    _cache[key] = (time.time(), value, ttl)


def cache_get(key: str) -> Any:
    row = _cache.get(key)
    if not row:
        return None
    born, value, ttl = row
    if time.time() - born > ttl:
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
    """Делает из ссылки источника полный адрес.

    Источники отдают картинки по-разному: полным адресом, путём от корня
    сайта («/posters/1.jpg») или адресом без схемы («//static.img-b.example/…»).
    Последний раньше склеивался с адресом сайта как есть и превращался в
    «https://ru-b.example//static.img-b.example/…» — такой картинки нет, и
    в карточке оставалась пустота. urljoin понимает все три вида.

    Результат проходит ещё и через store.clean_poster: источник может
    прислать уже склеенный кем-то адрес или картинку-заглушку, а такие
    в карточку пускать незачем — пусть лучше сработает запасной путь.
    """
    if not path:
        return ""
    path = str(path).strip()
    if path.startswith(("http://", "https://")):
        return clean_poster(path)
    base = (SOURCES.get(source) or PRIVATE_SOURCES.get(source) or {}).get("base", "")
    if not base and not path.startswith("//"):
        return ""
    return clean_poster(urljoin(base + "/", path))


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
    out = {
        "key": stable_key(item),
        "title": getattr(item, "title", "") or "Без названия",
        "poster": absolute(source, poster),
        "year": dig_year(data),
        "genres": dig_genres(data),
        "episodes_total": eps,
    }
    # Какие варианты у тайтла есть («sub», «dub») — знает только
    # англоязычный источник, и только он кладёт это поле (в data["langs"]).
    langs = data.get("langs")
    if isinstance(langs, list) and langs:
        out["langs"] = [str(x) for x in langs][:4]
    return out


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
            anime = await call(raw.a_get_anime(), source)
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
        results = await call(extractor.a_search(title), source)
    except HTTPException:
        raise
    except Exception as exc:
        raise upstream_error(exc, "Источник не ответил")

    match = next((r for r in results if stable_key(r) == str(key)), None)
    if match is None and ALT_LOOKUP[0] is not None:
        # Страница знает тайтл под названием из справочника («Старшая школа
        # DxD»), а источник — под своим («Демоны старшей школы»). Пока
        # заготовка поиска жива в кэше, это неважно; через три часа кэш
        # пустеет, и поиск по названию справочника не находит ничего. Тогда
        # спрашиваем другие имена тайтла (синонимы из справочника).
        try:
            others = await asyncio.wait_for(ALT_LOOKUP[0](title), 15)
        except Exception as exc:                   # noqa: BLE001
            log.info("другие имена не нашлись: %s", type(exc).__name__)
            others = []
        for other in (others or [])[:3]:
            try:
                results = await call(extractor.a_search(other), source)
            except HTTPException:
                raise
            except Exception:                      # noqa: BLE001
                continue
            match = next((r for r in results if stable_key(r) == str(key)), None)
            if match is not None:
                break
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
        anime = await call(match.a_get_anime(), source)
    except HTTPException:
        raise
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
        results = await call(extractor.a_search(title), source)
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

    # Общий срок на всю цепочку: поиск, карточка и список серий — это три
    # похода к источнику, и каждый по отдельности укладывается в свой
    # срок, а вместе могли бы занять минуту. Внутри резолва действует
    # его срок, если он короче.
    with budget():
        anime = await find_anime(source, key, title)
        try:
            episodes = await call(anime.a_get_episodes(), source)
        except HTTPException:
            raise
        except Exception as exc:
            raise upstream_error(exc, "Не удалось получить серии")

    episodes = list(episodes)
    # Пустой список в кэш не кладём. Почти всегда он значит не «серий
    # нет», а «источник не ответил»: так было у англоязычного источника,
    # который глотал сбой сети и отдавал пустоту, — и три часа тайтл
    # показывал «серий нет», хотя через минуту всё уже работало.
    if not episodes:
        return episodes
    # Неполный ответ (часть списков не отдалась) держим недолго.
    ttl = SHORT_TTL if getattr(anime, "incomplete", False) else CACHE_TTL
    cache_put(f"eps:{source}:{key}", episodes, ttl)
    return episodes


# Сколько может быть лет списку серий, когда проверяем, не вышла ли новая.
#
# Обычной странице три часа кэша — в самый раз: список серий меняется
# раз в неделю, а ходить за ним при каждом открытии незачем. Но
# колокольчик с тем же кэшем узнавал о новой серии через три часа после
# выхода — то есть вечером того дня, когда все уже посмотрели. Для
# проверки новинок список берётся свежий, не старше четверти часа.
FRESH_TTL = 15 * 60

# Источники, у которых список серий лежит прямо в карточке тайтла, а не
# запрашивается отдельно (поле "episodes_in": "card" в SOURCES): здесь
# мало выбросить список, надо выбросить и саму карточку, иначе «свежий»
# список соберётся из старой. "search" — ещё дальше: серии собраны прямо
# поиском, так что выбрасывать надо и заготовку поиска (raw:), иначе
# карточка соберётся из неё.
def _episodes_in(source: str) -> str:
    return str((SOURCES.get(source) or {}).get("episodes_in") or "")


# Разборщик может и сам помнить списки серий (свой кэш). Тогда «свежий»
# список собирался бы из его памяти, и о новой серии колокольчик узнавал бы
# через часы. Такой разборщик даёт метод forget_episodes(key, max_age) —
# его зовёт fresh_episodes.


def cache_age(key: str) -> float | None:
    """Сколько секунд записи в кэше. None — записи нет."""
    row = _cache.get(key)
    return None if not row else time.time() - row[0]


async def fresh_episodes(source: str, key: str, title: str,
                         max_age: float = FRESH_TTL) -> list:
    """Список серий не старше max_age секунд.

    Отдельная функция, а не параметр у find_episodes, по одной причине:
    find_episodes подменяют в проверках, и у подмены параметра нет.
    Здесь только решаем, годится ли то, что в кэше; сам поход к
    источнику — тот же find_episodes, с теми же сроками и ошибками.
    Свежий ответ заодно ложится в общий кэш на обычные три часа: страница
    просмотра, открытая по уведомлению, увидит новую серию сразу.
    """
    names = [f"eps:{source}:{key}"]
    if _episodes_in(source) in ("card", "search"):
        names.append(f"anime:{source}:{key}")
    if _episodes_in(source) == "search":
        names.append(f"raw:{source}:{key}")
    for name in names:
        age = cache_age(name)
        if age is not None and age > max_age:
            _cache.pop(name, None)
    # Свою память о сериях разборщик забывает сам (см. выше).
    forget = getattr(_extractors.get(source), "forget_episodes", None)
    if callable(forget):
        try:
            forget(key, max_age)
        except Exception as exc:                   # noqa: BLE001
            log.info("разборщик не забыл серии: %s", type(exc).__name__)
    return await find_episodes(source, key, title)


# ------------------------------------------------------- совпадение названий
# Зачем это здесь.
#
# Поиск у источников — не поиск, а «покажи что-нибудь похожее». На запрос
# «Атака титанов» RU-A и RU-C оба отвечают одной строкой: «Не
# издевайся, Нагаторо: Вторая атака». Совпало слово «атака», и этого им
# хватило. А перебор источников останавливался на первом, кто вообще
# ответил хоть чем-то, — то есть на этой самой Нагаторо. Дальше по списку
# лежал RU-B, у которого «Атака титанов» есть со всеми сезонами,
# но до него очередь не доходила никогда.
#
# Поэтому ответ источника теперь взвешивается: насколько название похоже
# на то, что просили. Мусор уезжает вниз, а источник, у которого нет
# ничего похожего, считается не ответившим.

_TAIL_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_SQUARE = re.compile(r"\[([^\]]*)\]")
_PUNCT = re.compile(r"[^\w\s]+", re.U)

# Как источники пишут номер сезона и вид тайтла.
#
# Живые строки, ради которых всё это написано («Старшая школа DxD»):
#   RU-D  «Демоны старшей школы / High School DxD [1-12 из 12]»
#              «Демоны старшей школы (второй сезон) / High School DxD New [1-12 из 12]»
#   RU-E    «Высшая школа DxD [ТВ-1] 1 сезон, 18 серия»
#              «Высшая школа DxD [ТВ-2] Завершен (все серии)»
#
# Раньше всё в скобках просто вырезалось. Для счётчика «[1-12 из 12]» это
# правильно, а для «(второй сезон)» — нет: второй сезон превращался в
# «демоны старшей школы» и совпадал с первым буква в букву. Теперь номер
# сезона не выбрасывается, а становится отдельным словом «s2». Первый
# сезон словом не становится — «Наруто» и «Наруто [ТВ-1]» одно и то же.
# Вид (OVA, спешл) тоже остаётся словом: «X [OVA-2]» — не сериал «X».
_ORDINAL = {
    "перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "шест": 6,
    "седьм": 7, "восьм": 8, "девят": 9, "десят": 10,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_ORD_WORD = r"(?:перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят)\w*" \
            r"|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
_SEASON_PATTERNS = [
    # «ТВ-2», «TV-2», «TV 2» — так пишет RU-E (в квадратных скобках,
    # но скобки к этому моменту уже сняты).
    re.compile(r"(?<!\w)(?:тв|tv)\s*-?\s*(\d{1,2})(?!\w)", re.I),
    # «2 сезон», «2-й сезон», «второй сезон», «second season», «2nd season»
    re.compile(r"(?<!\w)(\d{1,2}|" + _ORD_WORD + r")(?:-?(?:й|ый|ой|st|nd|rd|th))?"
               r"\s+(?:сезон|season)(?!\w)", re.I),
    # «сезон 2», «season 2»
    re.compile(r"(?<!\w)(?:сезон|season)\s+(\d{1,2})(?!\w)", re.I),
]
_KIND_PATTERNS = [
    (re.compile(r"(?<!\w)(?:ova|ова)(?:\s*-?\s*\d+)?(?!\w)", re.I), "ova"),
    (re.compile(r"(?<!\w)(?:ona)(?:\s*-?\s*\d+)?(?!\w)", re.I), "ona"),
    (re.compile(r"(?<!\w)(?:sp|спешл|special)(?!\w)", re.I), "sp"),
]
# Хвосты, которые про тайтл ничего не говорят: счётчики и статус выпуска.
_NOISE = re.compile(
    r"(?<!\w)\d+\s*(?:-\s*\d+\s*)?(?:из\s+\d+\s*)?сери[яийю]\w*(?!\w)"   # 18 серия, 1-12 серии
    r"|(?<!\w)все\s+серии(?!\w)"
    r"|(?<!\w)заверш[её]н\w*(?!\w)"
    r"|(?<!\w)\d+\s*-\s*\d+\s+из\s+\d+(?!\w)",                           # 1-12 из 12
    re.I)
_ROMAN = {"ii": 2, "iii": 3, "iv": 4}
# Сколько знаков названия сравнивать (см. title_variants).
TITLE_MAX = 300


def _season_number(raw: str) -> int | None:
    raw = (raw or "").lower()
    if raw.isdigit():
        n = int(raw)
        return n if 0 < n < 100 else None
    for stem, n in _ORDINAL.items():
        if raw.startswith(stem):
            return n
    return None


def _markers(text: str) -> tuple[str, int | None, set[str]]:
    """Вынимает из строки номер сезона и вид. Возвращает остаток строки."""
    season = None
    kinds: set[str] = set()
    for rx in _SEASON_PATTERNS:
        def take(m: re.Match) -> str:
            nonlocal season
            n = _season_number(m.group(1))
            if n and season is None:
                season = n
            return " "
        text = rx.sub(take, text)
    for rx, kind in _KIND_PATTERNS:
        if rx.search(text):
            kinds.add(kind)
            text = rx.sub(" ", text)
    return text, season, kinds


def _clean(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _with_tokens(base: str, season: int | None, kinds: set[str]) -> str:
    words = base.split()
    # Голая цифра в конце — тоже номер сезона: «Магическая битва 2».
    # Только от 2 до 9 и только после других слов: «86» или «Моб Психо
    # 100» — это названия, а не сезоны. Римские II–IV — так же.
    if season is None and len(words) >= 2:
        last = words[-1]
        if last.isdigit() and 2 <= int(last) <= 9:
            season = int(last)
            words = words[:-1]
        elif last in _ROMAN:
            season = _ROMAN[last]
            words = words[:-1]
    if season and season >= 2:
        words.append(f"s{season}")
    for kind in sorted(kinds):
        if kind not in words:
            words.append(kind)
    return " ".join(words)


def title_variants(text: str) -> list[str]:
    """Все имена из одной строки источника — нормализованные, со «s2» и «ova».

    RU-D пишет два имени через « / »: «Демоны старшей школы / High
    School DxD». Раньше второе отбрасывалось, и тайтл находился только по
    русскому. Теперь сравниваем с каждым: справочник может знать тайтл
    как раз под латинским именем.

    Метки в квадратных скобках («[ТВ-2]», «[OVA-1]», «[1-12 из 12]»)
    относятся ко всей строке — их получают обе половины. Круглые скобки и
    слова («(второй сезон)», «2 сезон») — только та половина, где стоят:
    у RU-D «(второй сезон)» стоит у русского имени, а латинское
    «High School DxD New» отличает себя от первого сезона само.
    """
    # Пробелы схлопываем, длину режем — до всех регулярок ниже. Строки
    # приходят от чужих сайтов, а в шаблонах сезона «\s*-?\s*» стоят
    # рядом: на «tv» и тысяче пробелов после него время разбора росло как
    # квадрат длины, прямо в цикле событий. Настоящее название короче
    # трёхсот знаков, а «a  /  b» после схлопывания — то же «a / b».
    text = " ".join(str(text or "").split())[:TITLE_MAX]
    if not text:
        return []
    shared_season: int | None = None
    shared_kinds: set[str] = set()
    for inner in _SQUARE.findall(text):
        _, s, k = _markers(inner)
        if s and shared_season is None:
            shared_season = s
        shared_kinds |= k
    text = _SQUARE.sub(" ", text)
    out: list[str] = []
    for half in (text.split(" / ") if " / " in text else [text]):
        season = shared_season
        kinds = set(shared_kinds)
        # Круглые скобки: «(второй сезон)» — метка, «(ТВ)», «(все серии)» —
        # шум. Сначала метки, потом всё остальное в скобках вон.
        for inner in re.findall(r"\(([^)]*)\)", half):
            _, s, k = _markers(inner)
            if s and season is None:
                season = s
            kinds |= k
        half = re.sub(r"\([^)]*\)", " ", half)
        half = _NOISE.sub(" ", half)
        half, s, k = _markers(half)
        if s and season is None:
            season = s
        kinds |= k
        norm = _with_tokens(_clean(half), season, kinds)
        if norm and norm not in out:
            out.append(norm)
    return out


def normalize_title(text: str) -> str:
    """Название в виде, пригодном для сравнения: первое из title_variants.

    RU-D пишет названия так: «Наруто / Naruto [1-220 из 220]» —
    русское имя, латинское имя и счётчик серий в одной строке. Счётчики
    и статус выпуска отрезаются, номер сезона остаётся словом «s2».
    """
    variants = title_variants(text)
    return variants[0] if variants else ""


def relevance(query: str, title: str) -> float:
    """Насколько название отвечает запросу: от 0 до 1.

    Числа подобраны так, чтобы «Наруто» на запрос «наруто» стояло выше
    «Боруто: Новое поколение Наруто», а «Не издевайся, Нагаторо: Вторая
    атака» на запрос «атака титанов» не проходило порог вовсе.

    Строка источника может нести два имени («Русское / English») — берётся
    лучшее совпадение с любым из них.
    """
    best = 0.0
    for q in title_variants(query):
        for t in title_variants(title):
            best = max(best, _relevance_norm(q, t))
            if best >= 1.0:
                return 1.0
    return best


def _relevance_norm(q: str, t: str) -> float:
    """Совпадение двух уже нормализованных строк."""
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    # Запрос должен стоять в начале названия ЦЕЛЫМИ словами. Без пробела
    # после него «монстр» считался началом «монстры» — 0.90, ровно планка
    # точного совпадения, — и перебор останавливался на чужом тайтле.
    # После normalize_title знаки препинания уже стали пробелами, так что
    # пробела достаточно.
    if t.startswith(q + " "):
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
