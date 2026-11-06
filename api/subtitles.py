"""Субтитры отдельными файлами: откуда берутся и как доходят до плеера.

Два вида текста поверх японской дорожки.

Вшитый в картинку — так у большинства сайтов. С ним делать нечего:
видео уже с текстом, плеер просто играет.

Отдельным файлом (.vtt, .srt, .ass) — бывает, например, в варианте
«Оригинал (+субтитры)» у некоторых русских сайтов и у части английских.
Разборщик источника кладёт такие файлы в поле subtitles объекта плеера
(Source) — списком словарей:

    {"label": "English", "lang": "en", "url": "https://…/en.vtt",
     "codec": "vtt", "referer": ""}

lang можно не знать — его угадает guess_lang по подписи.

Почему файлы идут через наш сервер, а не напрямую.
  * <track> с чужого домена браузер грузит только с разрешающим
    заголовком CORS, а у чужих хостингов его обычно нет;
  * плеер понимает только WebVTT, а .srt и .ass надо переделать;
  * адрес файла от источника наружу не уходит: страница получает
    короткий номер, а по номеру сервер отдаёт только то, что сам получил
    от источника, — подсунуть ему чужой адрес нельзя.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any

from . import anime, netguard

log = logging.getLogger("anime.subs")

# Сколько ждать файл субтитров и какого он может быть размера. Серия на
# 24 минуты — это 20–80 КБ текста; три мегабайта — с огромным запасом.
FETCH_TIMEOUT = 12.0
MAX_BYTES = 3 * 1024 * 1024
# Сколько на одну серию берём дорожек. Больше — это уже не выбор языка,
# а мусор в меню.
MAX_TRACKS = 8

# Как называются языки у источников. Русские сайты пишут по-русски
# («Русский», «Английский», «Украинский»), английские — по-английски.
_LANG_WORDS = (
    ("en", re.compile(r"англ|english|\beng?\b", re.I)),
    ("ru", re.compile(r"рус|russian|\brus?\b", re.I)),
    ("uk", re.compile(r"укр|ukrain", re.I)),
    ("ja", re.compile(r"япон|japan|\bjp\b|\bja\b", re.I)),
)


def guess_lang(label: str, code: str = "") -> str:
    """Двухбуквенный код языка по подписи и коду, если он есть."""
    code = (code or "").strip().lower()[:2]
    if re.fullmatch(r"[a-z]{2}", code):
        return code
    for lang, rx in _LANG_WORDS:
        if rx.search(label or ""):
            return lang
    return ""


async def videos_and_subs(source: str, host: Any) -> tuple[list, list[dict]]:
    """Ссылки на видео одного плеера и отдельные файлы субтитров к нему.

    Субтитры — из поля subtitles плеера (см. начало файла); нет поля —
    нет и файлов. source здесь не нужен самому коду, но остаётся в
    подписи: так разборщику с особым путём за субтитрами есть где его
    подключить, не трогая main.
    """
    videos = await host.a_get_videos()
    return videos, list(getattr(host, "subtitles", None) or [])


# ----------------------------------------------------------- регистрация
def _safe_remote(url: str) -> bool:
    """Адрес из интернета, а не наш собственный и не домашняя сеть.

    Раньше здесь была своя проверка строки, и она пропускала и имена,
    которые ведут внутрь («127.0.0.1.nip.io»), и адреса в другой записи
    («2130706433» — это 127.0.0.1). Теперь проверка одна на весь сервер
    (api/netguard.py): строкой — здесь, настоящим адресом — при
    соединении.
    """
    return netguard.url_ok(url)


def token_for(url: str) -> str:
    return hashlib.sha256(("sub|" + url).encode("utf-8")).hexdigest()[:32]


def register(subs: list[dict]) -> list[dict]:
    """Запоминает дорожки и отдаёт странице только номер, подпись и язык."""
    out, seen = [], set()
    for sub in subs or []:
        url = str(sub.get("url") or "")
        if not url or not _safe_remote(url):
            continue
        token = token_for(url)
        if token in seen:
            continue
        seen.add(token)
        anime.cache_put(f"subfile:{token}", {
            "url": url, "codec": str(sub.get("codec") or ""),
            "referer": str(sub.get("referer") or ""),
        })
        lang = sub.get("lang") or guess_lang(str(sub.get("label") or ""))
        out.append({
            "id": token,
            "label": str(sub.get("label") or lang or "?")[:60],
            "lang": str(lang or "")[:2],
            "src": f"/api/subtitle/{token}.vtt",
        })
        if len(out) >= MAX_TRACKS:
            break
    return out


def lookup(token: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None
    return anime.cache_get(f"subfile:{token}")


# ------------------------------------------------------------ перевод в VTT
_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_ASS_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{1,3})")
# Теги ASS — «{…}». Внутри не бывает второй «{», и длинными они не
# бывают: с этими двумя ограничениями разбор линейный. Раньше было
# «{[^}]*}»: строка из тысяч «{» без «}» заставляла от КАЖДОЙ «{» искать
# «}» до конца строки — время росло как квадрат длины (60 000 знаков —
# 1,7 с, файл 3 МБ — около часа), и злой хостинг субтитров мог занять
# сервер надолго (аудит 3 ноября 2026, C-12).
_ASS_TAGS = re.compile(r"\{[^{}]{0,1000}\}")
# Теги HTML в SRT (<i>, <b>, <u>, <font …>). «<» внутри не бывает — по
# той же причине, что выше: иначе строка из «<i» без «>» разбиралась бы
# квадратично.
_SRT_TAGS = re.compile(r"</?(?:i|b|u|font)[^<>]{0,300}>")
# «Рисунок» ASS: {\p1}…{\p0} — между ними не текст, а координаты фигуры
# (так фансаберы рисуют вывески). Показанные текстом, они превращаются
# в строку «m 0 0 l 100 0 …» поверх видео.
_ASS_DRAWING = re.compile(r"\{[^{}]{0,200}\\p[1-9]")
# Надпись сверху: \an7, \an8, \an9 (цифры как на калькуляторе — верхний
# ряд). Без этого вывеска уходила вниз и закрывала обычные субтитры.
_ASS_TOP = re.compile(r"\{[^{}]{0,200}\\an[789]")


def _vtt_text(text: str) -> str:
    """Текст реплики так, чтобы его нельзя было принять за разметку VTT."""
    # Сначала стрелка, потом экранирование: после замены «>» на «&gt;»
    # строки «-->» в тексте уже нет, и замена раньше не срабатывала никогда.
    text = text.replace("-->", "→")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _stamp(h: str, m: str, s: str, frac: str) -> str:
    ms = int((frac + "00")[:3])
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}.{ms:03d}"


def srt_to_vtt(text: str) -> str:
    blocks = re.split(r"\n\s*\n", text.strip())
    out = ["WEBVTT", ""]
    for block in blocks:
        lines = [x for x in block.split("\n")]
        at = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if at is None:
            continue
        a, _, b = lines[at].partition("-->")
        ma, mb = _SRT_TIME.search(a), _SRT_TIME.search(b)
        if not ma or not mb:
            continue
        body = [_SRT_TAGS.sub("", x).strip() for x in lines[at + 1:]]
        body = [_vtt_text(x) for x in body if x]
        if not body:
            continue
        out.append(f"{_stamp(*ma.groups())} --> {_stamp(*mb.groups())}")
        out.extend(body)
        out.append("")
    return "\n".join(out) + "\n"


def ass_to_vtt(text: str) -> str:
    fields: list[str] = []
    cues: list[tuple[str, str, str, str]] = []
    in_events = False
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("["):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        if line.lower().startswith("format:"):
            fields = [f.strip().lower() for f in line[7:].split(",")]
            continue
        if not line.lower().startswith("dialogue:") or not fields:
            continue
        parts = line[9:].split(",", len(fields) - 1)
        if len(parts) < len(fields):
            continue
        row = dict(zip(fields, parts))
        ma = _ASS_TIME.search(row.get("start", ""))
        mb = _ASS_TIME.search(row.get("end", ""))
        if not ma or not mb:
            continue
        raw_text = row.get("text", "")
        if _ASS_DRAWING.search(raw_text):
            continue
        # Настройка реплики VTT: «line:10%» — строка у верхнего края.
        where = " line:10%" if _ASS_TOP.search(raw_text) else ""
        body = _ASS_TAGS.sub("", raw_text)
        body = body.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        lines = [_vtt_text(x.strip()) for x in body.split("\n") if x.strip()]
        if lines:
            cues.append((_stamp(*ma.groups()), _stamp(*mb.groups()), "\n".join(lines), where))
    cues.sort(key=lambda c: c[0])
    out = ["WEBVTT", ""]
    for start, end, body, where in cues:
        out += [f"{start} --> {end}{where}", body, ""]
    return "\n".join(out) + "\n"


def clean_vtt(text: str) -> str:
    """Готовый VTT — как есть, только без мусора в начале."""
    text = text.lstrip()
    return text if text.endswith("\n") else text + "\n"


def to_vtt(raw: bytes) -> str:
    """Что бы ни прислали (VTT, SRT, ASS/SSA) — WebVTT для плеера."""
    text = None
    # UTF-16 с меткой порядка байтов (BOM) — так сохраняют «Блокнот» и
    # редакторы субтитров на Windows. Ни utf-8, ни cp1251 его не читают:
    # latin-1 «читал» всё, но получалась каша с нулями, и субтитры молча
    # пропадали.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace").lstrip("\ufeff")
    else:
        for enc in ("utf-8-sig", "cp1251", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    head = text.lstrip()[:2000]
    if head.startswith("WEBVTT"):
        return clean_vtt(text)
    if "[script info]" in head.lower() or "dialogue:" in text[:20000].lower():
        return ass_to_vtt(text)
    return srt_to_vtt(text)


async def fetch_vtt(meta: dict) -> str:
    """Скачивает файл (не больше MAX_BYTES) и переводит в VTT."""
    import httpx

    def check(request: Any) -> None:
        # И исходный адрес, и каждый адрес после переадресации.
        if not _safe_remote(str(request.url)):
            raise httpx.RequestError("адрес не из интернета", request=request)

    headers = {"User-Agent": "Mozilla/5.0"}
    if meta.get("referer"):
        headers["Referer"] = meta["referer"]
    # trust_env=False — см. hls.client: через прокси сторож не видит адрес.
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True,
                                 max_redirects=3, headers=headers, trust_env=False,
                                 event_hooks={"request": [_async(check)]}) as client:
        async with client.stream("GET", meta["url"]) as resp:
            resp.raise_for_status()
            raw = await netguard.read_capped(resp, MAX_BYTES)
    # Перевод в VTT — в отдельном потоке: файл в 3 МБ (потолок MAX_BYTES)
    # разбирается треть секунды, и это не должно останавливать сайт у всех.
    return await asyncio.to_thread(to_vtt, raw)


def _async(fn):
    async def wrapped(request: Any) -> None:
        fn(request)
    return wrapped
