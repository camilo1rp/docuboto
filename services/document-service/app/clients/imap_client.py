# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente IMAP para la ingesta de correo (E19, F2).

`parse_email` convierte un mensaje RFC 822 crudo en un `EmailMessage` (testeable
sin servidor). `ImaplibClient` envuelve `imaplib` para traer los no leídos y
marcarlos como leídos; las llamadas bloqueantes se ejecutan en un hilo. El
`ImapClient` Protocol permite inyectar un doble en pruebas.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from typing import Protocol


@dataclass
class EmailMessage:
    uid: str
    from_addr: str
    from_name: str
    subject: str
    body: str
    attachments: list[str] = field(default_factory=list)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace").strip()
        return ""
    payload = msg.get_payload(decode=True)
    if payload:
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace").strip()
    return ""


def _extract_attachments(msg: Message) -> list[str]:
    names: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            filename = part.get_filename()
            if filename:
                names.append(_decode(filename))
    return names


def parse_email(uid: str, raw: bytes) -> EmailMessage:
    msg = email.message_from_bytes(raw)
    from_name, from_addr = parseaddr(msg.get("From", ""))
    return EmailMessage(
        uid=uid,
        from_addr=from_addr,
        from_name=_decode(from_name) or from_addr,
        subject=_decode(msg.get("Subject")),
        body=_extract_body(msg),
        attachments=_extract_attachments(msg),
    )


class ImapClient(Protocol):
    async def fetch_unseen(self) -> list[EmailMessage]: ...
    async def mark_seen(self, uid: str) -> None: ...


class ImaplibClient:
    """Implementación real sobre imaplib (llamadas bloqueantes en hilo aparte)."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        *,
        use_ssl: bool = True,
        mailbox: str = "INBOX",
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._use_ssl = use_ssl
        self._mailbox = mailbox

    def _connect(self) -> imaplib.IMAP4:
        cls = imaplib.IMAP4_SSL if self._use_ssl else imaplib.IMAP4
        conn = cls(self._host, self._port)
        conn.login(self._user, self._password)
        conn.select(self._mailbox)
        return conn

    def _fetch_unseen_sync(self) -> list[EmailMessage]:
        conn = self._connect()
        try:
            _, data = conn.search(None, "UNSEEN")
            uids = data[0].split() if data and data[0] else []
            messages: list[EmailMessage] = []
            for uid in uids:
                # BODY.PEEK no marca como leído; el \Seen se aplica tras ingestar.
                _, msg_data = conn.fetch(uid, "(BODY.PEEK[])")
                if msg_data and msg_data[0]:
                    raw = msg_data[0][1]
                    messages.append(parse_email(uid.decode(), raw))
            return messages
        finally:
            conn.logout()

    def _mark_seen_sync(self, uid: str) -> None:
        conn = self._connect()
        try:
            conn.store(uid.encode(), "+FLAGS", "\\Seen")
        finally:
            conn.logout()

    async def fetch_unseen(self) -> list[EmailMessage]:
        return await asyncio.to_thread(self._fetch_unseen_sync)

    async def mark_seen(self, uid: str) -> None:
        await asyncio.to_thread(self._mark_seen_sync, uid)
