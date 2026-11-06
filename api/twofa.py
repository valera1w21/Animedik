"""Вход по коду из приложения-аутентификатора (TOTP).

Стандарт RFC 6238, тот самый, по которому работают Google Authenticator,
Aegis, 1Password и прочие. Реализован здесь целиком — это тридцать строк
арифметики, и ради них незачем тянуть зависимость, которую потом надо
обновлять и за которой надо следить.

Почему код из приложения, а не письмо на почту: письмо идёт через чужой
сервер, попадает в спам и запирает снаружи ровно тогда, когда войти
нужнее всего. Приложение работает без сети и ни от кого не зависит.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import unicodedata
from urllib.parse import quote

from . import qr

# Шаг времени и допуск. Один шаг в обе стороны — это ±30 секунд:
# столько нужно, чтобы человек успел переписать цифры, и достаточно
# мало, чтобы подсмотренный код быстро протухал.
STEP = 30
WINDOW = 1

SECRET_BYTES = 20          # 160 бит, как советует стандарт
ISSUER = "anime Dick"


def new_secret() -> str:
    """Секрет в виде base32 — приложения принимают только его."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    part = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{part % 1_000_000:06d}"


def matched_step(secret: str, code: str, at: float | None = None) -> int:
    """Номер шага времени, которому соответствует код. 0 — код не подошёл.

    Номер нужен, чтобы не пускать дважды по одному и тому же коду. Из-за
    допуска в шаг в обе стороны код живёт до полутора минут, и раньше всё
    это время им можно было войти сколько угодно раз: подсмотрел цифры
    через плечо или перехватил — и успевай. Теперь вход запоминает номер
    шага, и всё, что не новее его, отклоняется (см. store.claim_totp_step).

    Сравнение через compare_digest: обычное сравнение строк выходит из
    цикла на первом несовпавшем символе, и по времени ответа код можно
    подбирать по одной цифре.
    """
    if not secret or not code:
        return 0
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:
        return 0
    now = int((at if at is not None else time.time()) // STEP)
    for shift in range(-WINDOW, WINDOW + 1):
        try:
            if hmac.compare_digest(_code_at(secret, now + shift), code):
                return now + shift
        except Exception:                            # noqa: BLE001
            return 0
    return 0


def verify(secret: str, code: str, at: float | None = None) -> bool:
    """Подходит ли код. Без защиты от повтора — она живёт во входе."""
    return matched_step(secret, code, at) > 0


def otpauth_uri(secret: str, login: str) -> str:
    """Строка, которую приложение читает из QR-кода."""
    label = quote(f"{ISSUER}:{login}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}"
            f"&issuer={quote(ISSUER, safe='')}&algorithm=SHA1&digits=6&period={STEP}")


def qr_data_uri(data: str) -> str | None:
    """QR-код картинкой, готовой для <img src>.

    Рисуем у себя, а не через чужой сервис вроде api.qrserver.com:
    отправить туда ссылку — значит отправить туда секрет от чужого
    аккаунта. Ради удобства так делать нельзя ни при каких условиях.

    Рисует свой модуль `api/qr.py`, без единой зависимости. Раньше здесь
    была библиотека `qrcode`, а за ней Pillow, и отсутствие любой из них
    роняло настройку защиты целиком — из-за необязательной картинки.

    Если что-то всё-таки пойдёт не так, возвращаем None: ключ в
    приложение всегда можно ввести руками, и терять из-за картинки
    возможность защитить вход куда хуже.
    """
    import base64

    try:
        картинка = qr.svg(data, box=8, border=2)
    except Exception:                                # noqa: BLE001
        return None
    return ("data:image/svg+xml;base64,"
            + base64.b64encode(картинка.encode("utf-8")).decode("ascii"))


# --------------------------------------------------------------------------
# Одноразовые коды на случай потери телефона
# --------------------------------------------------------------------------
BACKUP_COUNT = 8


def new_backup_codes() -> list[str]:
    """Запасные коды: по одному разу каждый.

    Без них потеря телефона означает потерю аккаунта — и разбираться
    придётся через консоль сервера.
    """
    return ["-".join((secrets.token_hex(2), secrets.token_hex(2)))
            for _ in range(BACKUP_COUNT)]


def normalize_backup(code: str) -> str:
    """Приводит запасной код к виду, в котором его выдали: «abcd-ef01».

    Раньше код принимался только буква в букву, с дефисом посередине.
    А переписывают его с бумажки, диктуют по телефону, вставляют из
    заметок — и дефис теряется, превращается в пробел или в длинное тире.
    Человек вводит верный код и получает «не подошёл» ровно в тот момент,
    когда телефона нет и войти иначе нельзя.

    Оставляем только латинские буквы и цифры и ставим дефис на место сами.
    Именно ставим, а не выбрасываем: в базе уже лежат отпечатки кодов
    с дефисом, и они должны продолжать совпадать.
    """
    raw = unicodedata.normalize("NFKC", str(code or "")).lower()
    raw = "".join(ch for ch in raw if ch in "0123456789abcdefghijklmnopqrstuvwxyz")
    if len(raw) == 8:
        return raw[:4] + "-" + raw[4:]
    return raw


def hash_backup(code: str) -> str:
    """Запасные коды в базе тоже не лежат открытыми."""
    pepper = os.getenv("SESSION_PEPPER", "")
    return hashlib.sha256((pepper + "backup|" + normalize_backup(code)).encode()).hexdigest()
