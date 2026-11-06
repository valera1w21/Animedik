"""Сторож исходящих запросов: сервер ходит только в интернет.

Зачем он нужен.

Сервер сам ходит по адресам, которые ему дали чужие сайты: плейлист
видео перечисляет куски потока, плеер называет адрес файла субтитров,
страница источника — адрес плеера. Если такой сайт взломан или просто
недобросовестен, он может подсунуть адрес из ВАШЕЙ домашней сети:
роутер, Proxmox, сам этот сервер изнутри (http://127.0.0.1:8080/...).
Сервер честно сходит туда — он же внутри сети — и отдаст ответ тому,
кто попросил. Это называется SSRF: чужими руками заглянуть туда, куда
снаружи не пускают.

Раньше проверялась только строка адреса: «не localhost и не 192.168…».
Обходилось это тремя способами сразу:
  * имя, которое ведёт внутрь: «127.0.0.1.nip.io», «router.мойдомен.ру»
    — по строке это обычный сайт, а соединение уходит на 127.0.0.1;
  * адрес в другой записи: «2130706433», «0x7f000001», «127.1» — это всё
    тот же 127.0.0.1, но строковая проверка его не узнаёт;
  * подмена ответа DNS между проверкой и соединением (DNS rebinding):
    при проверке имя ведёт в интернет, через секунду — в домашнюю сеть.

Как теперь. Проверяется не строка, а тот адрес, с которым сервер
соединяется НА САМОМ ДЕЛЕ, — в момент соединения, после того как имя
превратилось в адрес. Соединение идёт ровно на проверенный адрес, так
что подменить его между проверкой и соединением нельзя. Это работает
для каждого шага переадресации и для всех клиентов httpx в процессе
(install ниже), в том числе внутри библиотек источников.

Одно осознанное исключение — помощник (helper.py): его адрес задаёт
хозяин в .env, и это может быть Ollama в домашней сети. Он проходит
через allow_private().

Второе, ещё уже, — свой сервис-источник хозяина в его же сети (адрес
вписан в .env): ему открыт ровно один узел и порт и ничего больше
(trusted_service). В публичной версии его никто не зовёт — это дверь для
того, кто подключит такой источник сам.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import ipaddress
import logging
import re
import socket
from typing import Any, Iterator
from urllib.parse import urlsplit

log = logging.getLogger("anime.netguard")

# Порты, на которые браузеры не ходят никогда (список из стандарта Fetch,
# «bad ports»): почта, SSH, базы данных и прочее, что не HTTP. Видео с
# таких портов не раздают, а запрос туда — это попытка поговорить с
# чужой службой на её языке.
BAD_PORTS = frozenset({
    1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77,
    79, 87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123,
    135, 137, 139, 143, 161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530,
    531, 532, 540, 548, 554, 556, 563, 587, 601, 636, 989, 990, 993, 995, 1719,
    1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566, 6665, 6666,
    6667, 6668, 6669, 6679, 6697, 10080,
})

# Окончания имён, которые бывают только внутри домашних и рабочих сетей.
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home",
                  ".home.arpa", ".corp", ".intranet", ".localdomain")

_NAT64 = ipaddress.ip_network("64:ff9b::/96")

# Разрешение ходить в домашнюю сеть — только внутри allow_private().
_ALLOW_PRIVATE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "netguard_allow_private", default=False)

# Один-единственный узел домашней сети, куда можно, — только внутри
# trusted_service(): пара (имя, порт) того сервиса, адрес которого хозяин
# сам вписал в .env. Всё остальное в домашней сети
# остаётся закрытым даже внутри этого блока.
_TRUSTED: contextvars.ContextVar[tuple[str, int] | None] = contextvars.ContextVar(
    "netguard_trusted", default=None)


class Blocked(Exception):
    """Адрес ведёт не в интернет."""


def ip_ok(ip: Any) -> bool:
    """Можно ли серверу соединяться с этим адресом: только интернет.

    Адреса IPv6, внутри которых спрятан IPv4 (::ffff:127.0.0.1, NAT64
    64:ff9b::7f00:1, 6to4 2002:7f00:1::), проверяются по спрятанному
    адресу: иначе 127.0.0.1 проходил бы под видом «какого-то IPv6».
    """
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip.split("%", 1)[0].strip("[]"))
        except ValueError:
            return False
    if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return ip_ok(ip.ipv4_mapped)
        if ip in _NAT64:
            return ip_ok(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip.sixtofour is not None:
            return ip_ok(ip.sixtofour)
        if ip.teredo is not None:
            return False
    # is_global сам по себе пропускает групповую рассылку (224.0.0.0/4),
    # поэтому перечисляем всё явно.
    return bool(ip.is_global and not ip.is_private and not ip.is_multicast
                and not ip.is_reserved and not ip.is_loopback
                and not ip.is_link_local and not ip.is_unspecified)


def literal_ip(host: str) -> Any:
    """Адрес, если имя узла — это адрес в любой записи, иначе None.

    «127.1», «2130706433», «0x7f000001», «0177.0.0.1» — всё это 127.0.0.1:
    так их понимает и система при соединении. Обычная проверка видела в
    них «имя сайта» и пропускала.
    """
    h = (host or "").strip().strip("[]").split("%", 1)[0]
    if not h:
        return None
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-fA-FxX.]+", h) and re.search(r"\d", h):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(h))
        except OSError:
            return None
    return None


def url_ok(url: str) -> bool:
    """Первая, строковая проверка адреса — до всякой сети.

    Отсекает очевидное (не http, домашние имена, адреса внутренней сети,
    служебные порты) ещё до подписи адреса прокладкой. Главная проверка —
    при соединении (GuardedAsyncBackend): только она видит, куда имя
    ведёт на самом деле.
    """
    try:
        parts = urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if port is not None and (port in BAD_PORTS or not 0 < port < 65536):
        return False
    host = parts.hostname.lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return False
    ip = literal_ip(host)
    if ip is not None:
        return ip_ok(ip)
    # Имя из одного слова («router», «nas», «proxmox») система дополняет
    # своим домашним доменом. В интернете таких имён не бывает.
    return "." in host


# --------------------------------------------------------------------------
# Проверка в момент соединения
# --------------------------------------------------------------------------
async def _resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _resolve_sync(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


# Подменяется в проверках: чтобы «имя ведёт на 127.0.0.1» не требовало
# настоящего DNS.
RESOLVE: list[Any] = [_resolve]
RESOLVE_SYNC: list[Any] = [_resolve_sync]


def _checked(host: str, addrs: list[str]) -> list[str]:
    """Адреса, если ВСЕ они в интернете. Иначе — Blocked.

    Именно «все»: ответ DNS, где рядом с настоящим адресом стоит
    внутренний, — это уже попытка обмануть, а не случайность.
    """
    out: list[str] = []
    for raw in addrs:
        a = str(raw).split("%", 1)[0]
        if not ip_ok(a):
            raise Blocked(f"{host} ведёт в закрытую сеть ({a})")
        if a not in out:
            out.append(a)
    if not out:
        raise Blocked(f"{host}: адресов нет")
    return out


def _port_checked(port: int) -> None:
    if port in BAD_PORTS:
        raise Blocked(f"порт {port} — не для веба")


def _make_backends() -> tuple[type, type]:
    """Классы сторожей. Строятся при первом вызове: httpcore ставится
    вместе с httpx, но импортировать его ради модуля, который проверки
    могут и не тронуть, незачем."""
    import httpcore

    class GuardedAsyncBackend(httpcore.AsyncNetworkBackend):
        """Обёртка над обычным соединением: сначала адрес, потом сокет."""

        def __init__(self, inner: Any = None) -> None:
            self._inner = inner if inner is not None else httpcore.AnyIOBackend()

        async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                              local_address: str | None = None,
                              socket_options: Any = None) -> Any:
            if _ALLOW_PRIVATE.get() or _is_trusted(host, port):
                return await self._inner.connect_tcp(
                    host, port, timeout=timeout, local_address=local_address,
                    socket_options=socket_options)
            try:
                _port_checked(port)
                lit = literal_ip(host)
                if lit is not None:
                    addrs = _checked(host, [str(lit)])
                else:
                    ask = RESOLVE[0](host, port)
                    raw = await (asyncio.wait_for(ask, timeout) if timeout else ask)
                    addrs = _checked(host, raw)
            except Blocked as exc:
                log.warning("сторож: не пускаю наружу: %s", exc)
                raise httpcore.ConnectError(f"адрес не из интернета: {exc}") from None
            except (OSError, asyncio.TimeoutError) as exc:
                raise httpcore.ConnectError(f"имя {host} не разрешилось: {exc}") from None
            last: Exception | None = None
            # Соединяемся с ПРОВЕРЕННЫМ адресом, а не с именем: второй раз
            # имя не разрешается, и подменить ответ DNS между проверкой и
            # соединением нельзя. Сертификат при этом сверяется с именем —
            # httpcore передаёт его отдельно (server_hostname).
            for a in addrs:
                try:
                    return await self._inner.connect_tcp(
                        a, port, timeout=timeout, local_address=local_address,
                        socket_options=socket_options)
                except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                    last = exc
            raise last or httpcore.ConnectError("не соединилось")

        async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                      socket_options: Any = None) -> Any:
            raise httpcore.ConnectError("локальные сокеты наружу не нужны")

        async def sleep(self, seconds: float) -> None:
            await self._inner.sleep(seconds)

    class GuardedSyncBackend(httpcore.NetworkBackend):
        """То же для синхронных клиентов (их создают библиотеки источников)."""

        def __init__(self, inner: Any = None) -> None:
            self._inner = inner if inner is not None else httpcore.SyncBackend()

        def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                        local_address: str | None = None, socket_options: Any = None) -> Any:
            if _ALLOW_PRIVATE.get() or _is_trusted(host, port):
                return self._inner.connect_tcp(host, port, timeout=timeout,
                                               local_address=local_address,
                                               socket_options=socket_options)
            try:
                _port_checked(port)
                lit = literal_ip(host)
                addrs = _checked(host, [str(lit)] if lit is not None
                                 else RESOLVE_SYNC[0](host, port))
            except Blocked as exc:
                log.warning("сторож: не пускаю наружу: %s", exc)
                raise httpcore.ConnectError(f"адрес не из интернета: {exc}") from None
            except OSError as exc:
                raise httpcore.ConnectError(f"имя {host} не разрешилось: {exc}") from None
            last: Exception | None = None
            for a in addrs:
                try:
                    return self._inner.connect_tcp(a, port, timeout=timeout,
                                                   local_address=local_address,
                                                   socket_options=socket_options)
                except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                    last = exc
            raise last or httpcore.ConnectError("не соединилось")

        def connect_unix_socket(self, path: str, timeout: float | None = None,
                                socket_options: Any = None) -> Any:
            raise httpcore.ConnectError("локальные сокеты наружу не нужны")

        def sleep(self, seconds: float) -> None:
            self._inner.sleep(seconds)

    return GuardedAsyncBackend, GuardedSyncBackend


_BACKENDS: list[Any] = []


def backends() -> tuple[type, type]:
    if not _BACKENDS:
        _BACKENDS.extend(_make_backends())
    return _BACKENDS[0], _BACKENDS[1]


def install() -> None:
    """Ставит сторожа на все пулы соединений httpx в этом процессе.

    Подменяется только СТАНДАРТНЫЙ способ соединения: кто передал свой
    (проверки с подменённой сетью), того сторож не трогает. Пулы через
    прокси — тоже: там соединение идёт к прокси, а куда дальше — решает
    он, и проверять здесь нечего. Прокси в docker-compose не задан.

    Вызывается один раз при запуске (main.py); повторный вызов ничего не
    меняет.
    """
    import httpcore

    guarded_async, guarded_sync = backends()
    pairs = ((httpcore.AsyncConnectionPool, guarded_async,
              (httpcore.AsyncHTTPProxy, httpcore.AsyncSOCKSProxy)),
             (httpcore.ConnectionPool, guarded_sync,
              (httpcore.HTTPProxy, httpcore.SOCKSProxy)))
    for cls, guard, proxies in pairs:
        if getattr(cls.__init__, "_netguard", False):
            continue
        original = cls.__init__

        def __init__(self, *args: Any, _orig: Any = original, _guard: type = guard,
                     _proxies: tuple = proxies, **kwargs: Any) -> None:
            _orig(self, *args, **kwargs)
            if isinstance(self, _proxies):
                return
            backend = getattr(self, "_network_backend", None)
            if type(backend).__name__ in ("AutoBackend", "AnyIOBackend", "SyncBackend"):
                self._network_backend = _guard(backend)

        __init__._netguard = True          # type: ignore[attr-defined]
        cls.__init__ = __init__            # type: ignore[method-assign]


@contextlib.contextmanager
def allow_private() -> Iterator[None]:
    """Внутри — можно в домашнюю сеть. Только для адреса, который задал
    сам хозяин (помощник на своей Ollama), и ни для чего чужого."""
    token = _ALLOW_PRIVATE.set(True)
    try:
        yield
    finally:
        _ALLOW_PRIVATE.reset(token)


def service_target(url: str) -> tuple[str, int] | None:
    """(имя, порт) из адреса своего сервиса; None — адрес негодный.

    Годится только http(s) без логина-пароля в адресе и не на служебном
    порту. Порт без явного указания — 80 или 443, как и при соединении.
    """
    try:
        parts = urlsplit(str(url or "").strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not host:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    port = port or (443 if parts.scheme == "https" else 80)
    if port in BAD_PORTS or not 0 < port < 65536:
        return None
    return host, port


def _is_trusted(host: str, port: int) -> bool:
    """Соединение ровно с тем узлом и портом, что открыл trusted_service."""
    want = _TRUSTED.get()
    return want is not None and ((host or "").lower().rstrip("."), int(port)) == want


@contextlib.contextmanager
def trusted_service(url: str) -> Iterator[None]:
    """Внутри — можно соединиться с узлом и портом из url, и только с ним.

    Зачем. Источником может быть сервис самого хозяина в его же сети
    docker, по адресу вроде http://my-service:4000. Сторож такой адрес
    правильно не пускает: это домашняя сеть. allow_private() открыл бы её
    всю, а здесь нужна одна дверь. Поэтому разрешение узкое:
      * только пара (имя, порт), которую хозяин сам вписал в .env;
      * только внутри этого блока (переменная контекста, как у
        allow_private): соседние запросы того же процесса его не видят;
      * клиент такого сервиса не должен ходить по переадресациям — тогда
        ответ «302 на http://192.168.1.1/» дверь в другое место не откроет.
    Любой другой адрес внутри блока проверяется как обычно.
    """
    target = service_target(url)
    token = _TRUSTED.set(target)
    try:
        yield
    finally:
        _TRUSTED.reset(token)


def check_url_sync(url: str) -> None:
    """Проверка для клиентов на requests (если разборщик источника на нём).

    Сторож httpx до них не дотягивается: у requests свои соединения.
    Здесь имя разрешается и проверяется прямо перед запросом — это
    закрывает и «имя ведёт внутрь», и «адрес в другой записи». Подмену
    DNS в долю секунды между проверкой и соединением это не ловит, но
    для библиотеки, в чьё соединение не влезть, это лучшее доступное.
    """
    if _ALLOW_PRIVATE.get():
        return
    if not url_ok(url):
        raise Blocked(f"адрес не из интернета: {str(url)[:120]}")
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    lit = literal_ip(host)
    _checked(host, [str(lit)] if lit is not None else RESOLVE_SYNC[0](host, port))


# --------------------------------------------------------------------------
# Ответ с потолком
# --------------------------------------------------------------------------
class TooBig(Exception):
    """Ответ длиннее, чем мы готовы держать в памяти."""


async def read_capped(response: Any, limit: int) -> bytes:
    """Тело ответа целиком, но не больше limit байт.

    httpx по умолчанию читает ответ целиком, сколько бы его ни было.
    Взломанный сайт может отдать гигабайт — и единственный процесс
    сервера съест память и упадёт вместе с сайтом у всех. Поэтому читаем
    по кускам и обрываем, как только вышли за потолок.
    """
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise TooBig(f"ответ {declared} байт — больше {limit}")
    buf = bytearray()
    async for chunk in response.aiter_bytes():
        buf += chunk
        if len(buf) > limit:
            raise TooBig(f"ответ больше {limit} байт")
    return bytes(buf)
