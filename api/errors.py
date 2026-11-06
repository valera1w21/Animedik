"""Тексты ошибок по-английски.

Сервер пишет ошибки по-русски (detail в ответе), а сайт бывает и
английским. Раньше переводом занималась сама страница — словарём из
семидесяти известных ей фраз (serverText в web/app.js), и каждая новая
фраза на сервере на английской странице превращалась в общее «Error 400».
Теперь перевод живёт рядом с текстами — здесь, на сервере.

Что уходит в ответе с ошибкой:

  * detail    — текст на языке сайта (см. ниже, как он выбирается);
  * detail_en — тот же текст по-английски, ВСЕГДА, на любом языке сайта.

Как выбирается язык detail (по порядку, первое, что нашлось):

  1. заголовок X-UI-Lang: en или ru — его шлёт страница;
  2. кука lang: en или ru;
  3. иначе — русский, как было всегда.

Accept-Language (язык браузера) не спрашиваем намеренно: язык сайта
человек выбирает на самой странице, и он часто не совпадает с языком
браузера. Русскоязычный с английским Chrome получил бы английские ошибки
посреди русской страницы. А detail_en приходит всегда — страница, которая
знает свой язык, может просто взять его.

Неизвестная фраза (её нет в EN и не подходит ни один шаблон из
EN_PATTERNS) остаётся в detail по-русски, а в detail_en — общая
английская фраза по коду ответа. Проверка check35_errors_en.py следит,
чтобы у каждой русской фразы из кода был перевод.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

CYR = re.compile(r"[а-яё]", re.I)

# Русская фраза → английская. Ключ — ровно тот текст, что стоит в коде.
EN: dict[str, str] = {
    # --- вход, сессии, права ---
    "Нужно войти": "Please sign in.",
    "Слишком много запросов": "Too many requests.",
    "Гостевой доступ только на просмотр. За аккаунтом — к администратору.":
        "Guest access is view-only. Ask the administrator for an account.",
    "Не найдено": "Not found.",
    "Запрос отклонён: не сходится метка формы":
        "Request rejected: the form token does not match. Reload the page.",
    "Запрос отклонён": "Request rejected.",
    "Неверный логин или пароль": "Wrong login or password.",
    "Введите код из приложения": "Enter the code from your authenticator app.",
    "Слишком много неверных кодов. Попробуйте через 15 минут.":
        "Too many wrong codes. Try again in 15 minutes.",
    "Этот код уже использован. Дождитесь следующего в приложении.":
        "This code has already been used. Wait for the next one in the app.",
    "Код не подошёл": "The code did not match.",
    "Вы уже вошли в аккаунт. Чтобы зайти гостем, сначала выйдите.":
        "You are already signed in. Sign out first to enter as a guest.",
    "Слишком часто. Попробуйте позже.": "Too often. Please try again later.",
    "Сейчас слишком много гостей. Загляните позже.":
        "There are too many guests right now. Please come back later.",
    "Сервер сейчас занят проверкой паролей. Попробуйте через минуту.":
        "The server is busy checking passwords. Try again in a minute.",
    # --- кабинет: имя, пароль, настройки, картинки, почта ---
    "Цвет должен быть вида #RRGGBB": "The colour must look like #RRGGBB.",
    "Имя не может быть пустым": "The name cannot be empty.",
    "Слишком часто. Попробуйте через несколько минут.":
        "Too often. Try again in a few minutes.",
    "Новый пароль совпадает с текущим": "The new password is the same as the current one.",
    "Текущий пароль не подошёл": "The current password is wrong.",
    "Праздничное оформление выбирает администратор":
        "Only the administrator can choose the holiday theme.",
    "Настройки не помещаются в отведённое место": "The settings are too large to save.",
    "Файл слишком большой": "The file is too large.",
    "Не удалось прочитать изображение": "Could not read the image.",
    "На сервере не установлен обработчик картинок":
        "The server has no image library installed.",
    "Не похоже на адрес почты": "That does not look like an email address.",
    "Слишком большой запрос": "The request is too large.",
    # --- пароли и логины (security.password_problem, login_problem) ---
    "Пароль должен быть строкой": "The password must be text.",
    "Такой пароль подбирают первым же перебором":
        "This password is among the first ones guessed in any attack.",
    "В пароле слишком мало разных символов": "The password has too few different characters.",
    "Пустой логин": "The login is empty.",
    "Логин: латиница, цифры, точка, дефис и подчёркивание. "
    "От 3 до 32 символов, начинается и кончается буквой или цифрой.":
        "Login: Latin letters, digits, dot, hyphen and underscore. "
        "3 to 32 characters, starting and ending with a letter or digit.",
    "пустой пароль": "The password is empty.",
    # --- вход по коду ---
    "Вход по коду уже включён. Чтобы сменить приложение, сначала выключите его.":
        "Two-step sign-in is already on. To switch apps, turn it off first.",
    "Вход по коду уже включён.": "Two-step sign-in is already on.",
    "Пароль не подошёл": "Wrong password.",
    "Настройка устарела. Снимите галочку и начните заново.":
        "The setup has expired. Untick the box and start again.",
    "Код не подошёл. Проверьте цифры.": "The code did not match. Check the digits.",
    # --- список и итоги ---
    "Слишком часто": "Too often.",
    "Неизвестный статус": "Unknown status.",
    "нет ключа тайтла": "The title has no key.",
    # --- администратор ---
    "Роль бывает user или admin": "The role must be user or admin.",
    "роль бывает admin или user": "The role must be admin or user.",
    "пустой хэш пароля": "Empty password hash.",
    "Такой логин уже занят": "This login is already taken.",
    "Нет такого пользователя": "No such user.",
    "Себя выключить нельзя": "You cannot disable yourself.",
    "Себя удалить нельзя": "You cannot delete yourself.",
    "Это последний администратор": "This is the last administrator.",
    "Пустое объявление": "The announcement is empty.",
    "копию нельзя писать поверх самой базы": "The backup cannot overwrite the database itself.",
    # --- источники видео, поиск, плеер ---
    "Неизвестный источник": "Unknown source.",
    "Источники видео не подключены (api/anime.py, SOURCES). Работает демонстрационный режим: MODE=demo.":
        "No video sources are connected (api/anime.py, SOURCES). The demo mode works: MODE=demo.",
    "Слишком часто, подождите минуту": "Too often, please wait a minute.",
    "Слишком частый поиск, подождите минуту": "Too many searches, please wait a minute.",
    "Ни один источник сейчас не отвечает. Попробуйте через несколько минут.":
        "No source is responding right now. Try again in a few minutes.",
    "Источник вернул серии в неизвестном виде":
        "The source returned the episodes in an unknown format.",
    "Этот источник русский, а язык аниме — английский":
        "This source is Russian, but your anime language is English.",
    "Этот источник английский, а язык аниме — русский":
        "This source is English, but your anime language is Russian.",
    "Такой серии нет": "There is no such episode.",
    "Ни один плеер не отдал видео": "None of the players returned a video.",
    "Не удалось получить плееры": "Could not get the players.",
    "Субтитры устарели — откройте серию заново":
        "The subtitles have expired — open the episode again.",
    "Файл субтитров не скачался": "The subtitle file did not download.",
    "Ссылка на видео устарела — откройте серию заново":
        "The video link has expired — open the episode again.",
    "Слишком много потоков видео разом": "Too many video streams at once.",
    "Плейлист слишком большой": "The playlist is too large.",
    "Хостинг видео не ответил": "The video host did not respond.",
    "Пустое название": "The title is empty.",
    "Эту часть ни один источник не отдал. Попробуйте другую часть "
    "или другой источник в панели под плеером.":
        "No source has this part. Try another part or another source "
        "in the panel under the player.",
    "Источник не отвечает. Попробуйте ещё раз через минуту.":
        "The source is not responding. Try again in a minute.",
    "Источник не ответил": "The source did not respond.",
    "Источник хентая сейчас не отвечает": "The hentai source is not responding right now.",
    "Не удалось получить серии": "Could not get the episodes.",
    "Тайтл выпал из памяти сервера. Передай ещё и title, либо найди его заново.":
        "The server no longer remembers this title. Search for it again.",
    "Не нашёл этот тайтл у источника. Возможно, его убрали — выбери заново.":
        "Could not find this title at the source. It may have been removed — pick it again.",
    "На сервере не установлены библиотеки источников "
    "(api/requirements-private.txt). Видео в личном режиме без них не работает.":
        "The source libraries are not installed on the server "
        "(api/requirements-private.txt). Video does not work without them.",
    # --- справочник ---
    "Нужно название": "A title is required.",
    "Каталог сейчас не отвечает. Попробуйте ещё раз.":
        "The catalogue is not responding right now. Try again.",
    "Справочник сейчас не отвечает": "The catalogue is not responding right now.",
    # --- помощник (тексты есть и по-английски в самом коде, helper.say) ---
    "В демонстрационном режиме помощник недоступен.":
        "The helper is not available in demo mode.",
    "Помощник не настроен.": "The helper is not set up.",
    "Напишите вопрос.": "Type a question.",
    "Вы задали много вопросов подряд — помощник передохнёт. "
    "Попробуйте через час (до 30 вопросов в час).":
        "That's a lot of questions in a row — the helper needs a break. "
        "Try again in an hour (up to 30 questions an hour).",
    "Помощник на сегодня устал — загляните завтра.":
        "The helper is worn out for today — come back tomorrow.",
    "Помощник сейчас не отвечает. Попробуйте чуть позже.":
        "The helper is not answering right now. Try again a bit later.",
    "Помощник сейчас перегружен. Попробуйте через минуту.":
        "The helper is overloaded right now. Try again in a minute.",
    "Помощник ответил непонятно. Попробуйте ещё раз.":
        "The helper's answer made no sense. Try again.",
    # --- общее ---
    "Внутренняя ошибка": "Internal server error.",
}

# Фразы с подставленным значением (число, имя озвучки). Шаблон — вся
# фраза целиком; подставленные куски переносятся в английский как есть.
EN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Слишком много попыток\. Попробуйте через (\d+) мин\."),
     "Too many attempts. Try again in {0} min."),
    (re.compile(r"Недопустимое значение: (.+)"), "Invalid value: {0}"),
    (re.compile(r"Озвучка «(.*)» сейчас не отвечает, и другие тоже", re.S),
     "The dub «{0}» is not responding right now, and neither are the others."),
    (re.compile(r"Хостинг видео ответил (\d+)"), "The video host answered {0}."),
    (re.compile(r"Неизвестный источник: (.+)", re.S), "Unknown source: {0}"),
    (re.compile(r"Источник (.+) сейчас недоступен", re.S), "Source {0} is unavailable right now."),
    (re.compile(r"Слишком длинно — уложитесь в (\d+) символов\."),
     "Too long — keep it under {0} characters."),
    (re.compile(r"Пароль короче (\d+) символов"), "The password is shorter than {0} characters."),
    (re.compile(r"Пароль длиннее (\d+) символов"), "The password is longer than {0} characters."),
    (re.compile(r"В списке уже (\d+) тайтлов — это предел\. Удалите что-нибудь ненужное\."),
     "Your list already has {0} titles — that's the limit. Remove something you don't need."),
    (re.compile(r"недопустимое имя в схеме: (.+)", re.S), "Invalid name in the schema: {0}"),
]

# Общая фраза по коду ответа — когда своей нет.
BY_STATUS: dict[int, str] = {
    400: "The request was not accepted.", 401: "Please sign in again.", 403: "Not allowed.",
    404: "Not found.", 405: "Method not allowed.",
    409: "This conflicts with the current state. Reload the page.",
    412: "This source does not match your anime language.", 413: "The request is too large.",
    422: "Check the fields: something is empty or not filled in correctly.",
    429: "Too many requests — please wait a minute.",
    500: "Internal server error.", 502: "A source or the catalogue did not respond.",
    503: "The service is unavailable right now.", 504: "The source took too long to answer.",
}


def by_status(status: int) -> str:
    return BY_STATUS.get(int(status), f"Error {int(status)}.")


def known(text: str) -> str | None:
    """Английский текст для русской фразы из кода или None, если её не знаем."""
    key = text.strip()
    if key in EN:
        return EN[key]
    for pattern, english in EN_PATTERNS:
        m = pattern.fullmatch(key)
        if m:
            return english.format(*m.groups())
    return None


def translate(detail: Any) -> str | None:
    """detail по-английски или None, если перевода нет: английская
    половина «ru · en», сам текст (если он и так не русский) или перевод
    из EN / EN_PATTERNS."""
    if not isinstance(detail, str):
        return None
    text = detail.strip()
    # «по-русски · in English» — так пишутся фразы, которые сервер и
    # раньше отдавал на двух языках (NO_ENGLISH, RX_NOWHERE).
    ru, sep, en = text.partition(" · ")
    if sep and CYR.search(ru) and en and not CYR.search(en):
        return en
    if not CYR.search(text):
        return text
    return known(text)


def english(detail: Any, status: int) -> str:
    """detail по-английски — переводом или общей фразой по коду ответа."""
    return translate(detail) or by_status(status)


def ui_lang(headers: Mapping[str, str], cookies: Mapping[str, str]) -> str:
    """Язык сайта у того, кто прислал запрос: "en" или "ru" (см. начало файла)."""
    for raw in (headers.get("x-ui-lang"), cookies.get("lang")):
        code = (raw or "").strip().lower()[:8]
        if code.startswith("en"):
            return "en"
        if code.startswith("ru"):
            return "ru"
    return "ru"


def payload(detail: Any, status: int, lang: str) -> dict:
    """Тело ответа с ошибкой: detail на языке сайта и detail_en всегда.

    Английской странице — перевод, если он есть; нет перевода — русский
    текст как есть (общая фраза по коду всё равно лежит в detail_en).
    Русской — как было, в том числе двуязычные «ru · en»: их половины
    страница разбирает сама."""
    en = translate(detail)
    shown = en if (lang == "en" and en is not None) else detail
    return {"detail": shown, "detail_en": en or by_status(status)}
