"""Newest provider message, and the https links inside it."""

from __future__ import annotations

import imaplib
import re
from email import message_from_bytes
from email.message import Message
from urllib.parse import urlparse

from desk.shared.errors import DeskError

_URL = re.compile(r"https://[^\s<>\"']+")


def extract_links(raw: bytes, host: str = "") -> list[str]:
    message = message_from_bytes(raw)
    found: list[str] = []
    seen: set[str] = set()
    for text in _texts(message):
        for match in _URL.findall(text):
            url = match.rstrip(").,>;\"'")
            if host and not _host_matches(url, host):
                continue
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def newest_links(address: str, password: bytes, sender: str, host: str = "", timeout: float = 20.0) -> list[str]:
    client = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=timeout)
    try:
        client.login(address, password.decode("utf-8"))
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise DeskError("Could not read mail.")
        status, data = client.search(None, "FROM", f'"{sender}"')
        if status != "OK" or not data or not data[0]:
            return []
        numbers = data[0].split()
        if not numbers:
            return []
        latest = numbers[-1]
        status, fetched = client.fetch(latest, "(BODY.PEEK[])")
        if status != "OK" or not fetched:
            raise DeskError("Could not read mail.")
        raw = _first_body(fetched)
        if raw is None:
            raise DeskError("Could not read mail.")
        return extract_links(raw, host)
    except DeskError:
        raise
    except Exception as exc:
        raise DeskError("Could not read mail.") from exc
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _first_body(fetched) -> bytes | None:
    for item in fetched:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _texts(message: Message) -> list[str]:
    parts: list[str] = []
    chunks = message.walk() if message.is_multipart() else [message]
    for part in chunks:
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, (bytes, bytearray)):
            continue
        charset = part.get_content_charset() or "utf-8"
        parts.append(bytes(payload).decode(charset, errors="replace"))
    return parts


def _host_matches(url: str, required: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    wanted = required.lower().rstrip(".")
    return hostname == wanted or hostname.endswith("." + wanted)
