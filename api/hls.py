"""Прокладка для видео, которое чужой сервер отдаёт только «своим».

Зачем она.

Некоторые источники (в личной версии — английские) отдают поток HLS
с хостинга, который проверяет заголовок Referer: видео играет, только если
запрос пришёл «со страницы плеера» (embed-сайта). Браузер такой
заголовок подставить не может — его не задать из JavaScript, а наш сайт
и вовсе отправляет Referrer-Policy: no-referrer. Значит, напрямую из
браузера такой поток не откроется.

Поэтому для таких потоков (и только для них: у кого Referer не нужен,
тот играет напрямую, как раньше) плейлист и куски видео идут через наш
сервер: он спрашивает чужой хостинг с нужным Referer и отдаёт ответ
браузеру как есть. Плейлисты (.m3u8) при этом переписываются: каждый
адрес внутри заменяется на наш.

Безопасность.

Сервер не ходит по адресу, который прислал браузер. Каждый адрес
прокладки подписан (HMAC от секрета сервера, SESSION_PEPPER): подписать
новый может только сервер — когда сам получил адрес от источника или
нашёл его в плейлисте, который сам скачал. Подделанный или изменённый
адрес отвергается. Подпись живёт LINK_TTL (полсуток): утёкшая ссылка
не работает вечно, а свежие адреса кусков выдаются при каждом чтении
плейлиста.

Вдобавок адрес обязан вести в интернет (не localhost, не домашняя
сеть): строкой — здесь, до подписи, и по настоящему адресу — в момент
соединения (api/netguard.py), на каждом шаге переадресации.

И последнее: ответ чужого хостинга отдаётся с нашего адреса. Поэтому
его тип не берётся на веру (safe_media_type): «картинка SVG» или
«скрипт» с нашего адреса — это чужой код, исполненный от имени сайта.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
from typing import Any
from urllib.parse import urljoin

from .netguard import url_ok

# Сколько ждать чужой хостинг: соединение и каждый кусок ответа.
TIMEOUT = 20.0
# Плейлист больше этого — не плейлист.
MAX_PLAYLIST = 2 * 1024 * 1024
# Сколько адресов может быть в одном плейлисте. Серия на 24 минуты при
# кусках по 4 секунды — 360 адресов, трёхчасовой фильм по 2 секунды —
# 5400. Потолок нужен из-за раздувания: каждый адрес мы заменяем своим,
# подписанным, и он втрое-вчетверо длиннее. Два мегабайта строк вида
# «a» — это миллион адресов и сотни мегабайт ответа в памяти сервера.
MAX_PLAYLIST_ENTRIES = 30_000
# Сколько живёт подпись адреса.
LINK_TTL = 12 * 3600
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_KEY = hashlib.sha256(("hls-proxy|" + os.getenv("SESSION_PEPPER", "")).encode("utf-8")).digest()


class PlaylistTooBig(ValueError):
    """В плейлисте больше адресов, чем бывает у настоящего."""


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(text: str) -> str:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii")).decode("utf-8")


def _mac(u: str, r: str, until: int) -> str:
    msg = (u + "|" + r + "|" + str(int(until))).encode("ascii")
    return hmac.new(_KEY, msg, hashlib.sha256).hexdigest()[:32]


def _sign(u: str, r: str, now: float | None = None) -> str:
    """Подпись вида «срок.отпечаток». Срок — внутри подписи: продлить
    ссылку, поменяв число, нельзя — отпечаток перестанет сходиться."""
    until = int((time.time() if now is None else now) + LINK_TTL)
    return f"{until}.{_mac(u, r, until)}"


def proxied(url: str, referer: str = "") -> str:
    """Наш адрес для чужого потока. Без Referer — адрес как есть."""
    if not referer or not url:
        return url
    u, r = _b64(url), _b64(referer)
    return f"/api/hls?u={u}&r={r}&s={_sign(u, r)}"


def unpack(u: str, r: str, s: str) -> tuple[str, str] | None:
    """Адрес и Referer из нашего адреса — только если подпись верна и не
    просрочена."""
    if not (u and s) or len(u) > 4000 or len(r) > 1000 or len(s) > 64:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]+", u) or not re.fullmatch(r"[A-Za-z0-9_-]*", r):
        return None
    m = re.fullmatch(r"(\d{1,12})\.([0-9a-f]{32})", s or "")
    if not m:
        return None
    until = int(m.group(1))
    if not hmac.compare_digest(_mac(u, r, until), m.group(2)):
        return None
    if until < time.time():
        return None
    try:
        url, ref = _unb64(u), _unb64(r) if r else ""
    except (ValueError, UnicodeDecodeError):
        return None
    if not url_ok(url):
        return None
    return url, ref


_URI_ATTR = re.compile(r'URI="([^"]+)"')


def rewrite_playlist(text: str, base: str, referer: str) -> str:
    """Каждый адрес в плейлисте — через прокладку (с тем же Referer).

    Адрес, который строкой ведёт не в интернет (локальный, служебный
    порт, чужая схема), не подписывается вовсе: строка остаётся пустой.
    Подписывать то, по чему сервер всё равно не пойдёт, незачем, а
    оставлять как есть нельзя — тогда за ним пошёл бы уже браузер.
    """
    out = []
    entries = [0]

    def wrap(raw: str) -> str:
        entries[0] += 1
        if entries[0] > MAX_PLAYLIST_ENTRIES:
            raise PlaylistTooBig("в плейлисте слишком много адресов")
        full = urljoin(base, raw)
        return proxied(full, referer) if url_ok(full) else ""

    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
            continue
        if s.startswith("#"):
            # Ключи шифрования, отдельные дорожки, карты инициализации.
            out.append(_URI_ATTR.sub(lambda m: 'URI="' + wrap(m.group(1)) + '"', line))
            continue
        out.append(wrap(s))
    return "\n".join(out) + "\n"


def looks_like_playlist(url: str, content_type: str, head: bytes) -> bool:
    if head.lstrip().startswith(b"#EXTM3U"):
        return True
    ct = (content_type or "").lower()
    return "mpegurl" in ct or url.lower().split("?")[0].endswith(".m3u8")


# Какие типы ответа чужого хостинга можно отдать браузером как есть.
# Видео и звук — да. Картинки — тоже: хостинги часто маскируют куски
# потока под .jpg и .png, а картинка браузером только рисуется. Всё
# остальное — «просто байты» (application/octet-stream): браузер не
# станет ни показывать это страницей, ни исполнять скриптом
# (X-Content-Type-Options: nosniff запрещает ему угадывать).
#
# Раньше подменялись только text/* и application/json, а остальное
# уходило как есть. image/svg+xml, application/xhtml+xml или
# application/javascript с нашего адреса — это чужой код от имени сайта:
# ссылку на такой «кусок видео» можно прислать вошедшему человеку, и
# скрипт получал бы его сессию.
_SAFE_EXACT = {
    "application/octet-stream", "binary/octet-stream", "application/mp4",
    "application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl",
    "audio/x-mpegurl", "image/jpeg", "image/jpg", "image/png", "image/gif",
    "image/webp", "image/avif", "image/bmp",
}


def safe_media_type(content_type: str) -> str:
    media = (content_type or "").split(";")[0].strip().lower()
    if media in _SAFE_EXACT:
        return media
    if re.fullmatch(r"(?:video|audio)/[a-z0-9.+-]{1,60}", media):
        return media
    return "application/octet-stream"


def _check(request: Any) -> None:
    import httpx

    if not url_ok(str(request.url)):
        raise httpx.RequestError("адрес не из интернета", request=request)


async def _hook(request: Any) -> None:
    _check(request)


def client(referer: str) -> Any:
    import httpx

    # Accept-Encoding: identity — просим хостинг НЕ сжимать ответ. Иначе
    # httpx сам распаковывает gzip на лету, а в main.api_hls наружу уходил
    # Content-Length хостинга — длина СЖАТОГО тела. Браузер получал тело
    # длиннее заявленного, uvicorn обрывал ответ с ошибкой «Response
    # content longer than Content-Length», и кусок видео с такого хостинга
    # не доезжал никогда (проверено: check37_server.py, группа A-02).
    headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"}
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = referer.rstrip("/")
    # trust_env=False: прокси из окружения сервер не подхватывает — через
    # прокси сторож (netguard) не видел бы, куда на самом деле идёт запрос.
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, max_redirects=3,
                             headers=headers, event_hooks={"request": [_hook]},
                             trust_env=False)
