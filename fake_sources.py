"""Подменные источники видео — только для проверок (tests.py, check*.py).

В публичной версии настоящих источников нет (api/anime.py, SOURCES
пустой), а проверки личного режима проверяют общий механизм: перебор
источников, язык аниме, субтитры, хентай, кэш серий. Для этого им нужны
источники — вот они, с условными именами:

    ru_a … ru_h   русские   (в комментариях кода — RU-A … RU-H)
    en_a … en_e   английские (EN-A … EN-E)
    rx_a          только хентай (RX-A)

Сетью они не ходят: разборщик по умолчанию сразу отвечает «источник
молчит». Проверки подменяют нужные шаги сами (try_source, find_episodes,
get_extractor), как и раньше.

Как подключить в проверке — сразу после импорта приложения:

    from api import main
    import fake_sources
    fake_sources.install()

В демо-режиме install() ничего не делает: там источник один, свой.
"""

from __future__ import annotations

from typing import Any

FAKE: dict[str, dict[str, Any]] = {
    "ru_a": {"lang": "ru", "label": "RU-A", "base": "https://ru-a.example",
             "dubs": "one", "note": "Одна своя озвучка"},
    "ru_b": {"lang": "ru", "label": "RU-B", "base": "https://ru-b.example",
             "dubs": "many", "note": "Много озвучек на выбор"},
    "ru_c": {"lang": "ru", "label": "RU-C", "base": "https://ru-c.example",
             "dubs": "many", "note": "Больше всего озвучек"},
    "ru_d": {"lang": "ru", "label": "RU-D", "base": "https://ru-d.example",
             "dubs": "one", "note": "Одна своя озвучка"},
    # Список серий приходит вместе с карточкой тайтла (episodes_in).
    "ru_e": {"lang": "ru", "label": "RU-E", "base": "https://ru-e.example",
             "dubs": "many", "note": "Много озвучек, есть «Оригинал (+субтитры)»",
             "episodes_in": "card"},
    "ru_f": {"lang": "ru", "label": "RU-F", "base": "https://ru-f.example",
             "dubs": "many", "note": "Узкий"},
    "ru_g": {"lang": "ru", "label": "RU-G", "base": "https://ru-g.example",
             "dubs": "one", "note": "Узкий"},
    "ru_h": {"lang": "ru", "label": "RU-H", "base": "https://ru-h.example",
             "dubs": "one", "note": "Узкий"},
    "en_a": {"lang": "en", "label": "EN-A", "base": "https://en-a.example",
             "dubs": "many", "tracks": ("sub", "dub"),
             "note": "По-английски: субтитры и дубляж"},
    "en_b": {"lang": "en", "label": "EN-B", "base": "https://en-b.example",
             "dubs": "one", "tracks": ("sub",),
             "note": "По-английски: только субтитры, отдельным файлом"},
    "en_c": {"lang": "en", "label": "EN-C", "base": "https://en-c.example",
             "dubs": "many", "tracks": ("sub", "dub"),
             "note": "По-английски: вшитые субтитры и дубляж"},
    "en_d": {"lang": "en", "label": "EN-D", "base": "https://en-d.example",
             "dubs": "many", "tracks": ("sub", "dub"),
             "note": "По-английски: субтитры и дубляж"},
    "en_e": {"lang": "en", "label": "EN-E", "base": "https://en-e.example",
             "dubs": "one", "tracks": ("dub",), "note": "Только английский дубляж"},
    # Только хентай; серии собраны прямо поиском (episodes_in).
    "rx_a": {"lang": "en", "label": "RX-A (18+)", "base": "https://rx-a.example",
             "dubs": "one", "tracks": ("sub",), "rx_only": True, "episodes_in": "search",
             "note": "Только хентай: японская речь и английские субтитры"},
}

FALLBACK_ORDER = ["ru_a", "ru_b", "ru_c", "ru_d"]
SUB_ORDER = ["ru_e", "ru_b", "ru_c", "ru_a", "ru_d"]
EN_ORDER = ["en_a", "en_b", "en_c", "en_d", "en_e"]
RX_ORDER = ["rx_a"]
DEFAULT_SOURCE = {"ru": "ru_a", "en": "en_a"}
# Как английский источник подписывает свои два варианта (так их
# подписывал разборщик личной версии).
SUB_LABEL = "Subtitles (English)"
DUB_LABEL = "English dub"
# Картинки одного из источников лежат не на его основном адресе.
POSTER_SITES = ("img-b.example",)


class Silent:
    """Разборщик по умолчанию: сети нет — источник «молчит» сразу."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def a_search(self, query: str) -> list:
        raise ConnectionError(f"подменный источник {self.name}: сети в проверках нет")


def install() -> bool:
    """Подключает подменные источники к уже импортированному приложению.

    Меняет списки на месте (а не подменяет их), потому что на них уже
    держат ссылки другие модули: main.SUB_ORDER_EN — это anime.EN_ORDER.
    Возвращает True, если подключил (в демо-режиме — False)."""
    from api import anime, store
    if anime.DEMO:
        return False
    anime.SOURCES.update(FAKE)                    # в личном режиме это и PRIVATE_SOURCES
    for name in FAKE:
        anime.EXTRACTORS.setdefault(name, lambda name=name: Silent(name))
    anime.EN_DEFAULT_ORDER[:] = EN_ORDER
    anime.EN_ORDER[:] = EN_ORDER
    anime.RX_ORDER[:] = RX_ORDER
    anime.DEFAULT_SOURCE.update(DEFAULT_SOURCE)
    store.SOURCE_LANG.update({k: str(v["lang"]) for k, v in FAKE.items()})
    import sys
    main = sys.modules.get("api.main")
    if main is not None:
        main.FALLBACK_ORDER[:] = FALLBACK_ORDER
        main.SUB_ORDER[:] = SUB_ORDER
        main.POSTER_HOSTS = frozenset(main.POSTER_HOSTS) | frozenset(POSTER_SITES) | frozenset(
            main._site(v["base"].split("//", 1)[1]) for v in FAKE.values())
    return True
