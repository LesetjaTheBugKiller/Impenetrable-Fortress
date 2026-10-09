"""Remote Desktop probe.

A timeout or a dead route stays silent. Only an active refusal means the
machine answered and the desktop port is closed.
"""

from __future__ import annotations

import errno
import socket

REFUSED = "refused"
OPEN = "open"
SILENT = "silent"

_REFUSED_ERRNOS = {errno.ECONNREFUSED}
if hasattr(errno, "WSAECONNREFUSED"):
    _REFUSED_ERRNOS.add(errno.WSAECONNREFUSED)


def probe(host: str, port: int, timeout: float, connect=None) -> str:
    connect = connect or _connect
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return SILENT
    saw_refused = False
    for family, _socktype, _proto, _canon, sockaddr in infos:
        try:
            connect(family, sockaddr, timeout)
            return OPEN
        except ConnectionRefusedError:
            saw_refused = True
        except OSError as exc:
            if _is_refused(exc):
                saw_refused = True
    return REFUSED if saw_refused else SILENT


def _connect(family: int, sockaddr, timeout: float) -> None:
    connection = socket.socket(family, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(sockaddr)
    finally:
        connection.close()


def _is_refused(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror == 10061:
        return True
    return exc.errno in _REFUSED_ERRNOS
