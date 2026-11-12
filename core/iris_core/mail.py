"""Avisos de correo nuevo de Gmail por el chat configurado.

Se conecta por IMAP con una contraseña de aplicación (requiere 2FA), revisa la
bandeja cada `IRIS_MAIL_INTERVAL` segundos y avisa solo de los correos que
llegan después de la primera ejecución. Marca el correo como leído únicamente
cuando el aviso se envió bien.

Variables (todas opcionales y apagadas por defecto):

    IRIS_MAIL=on
    IRIS_MAIL_USER=juan@gmail.com
    IRIS_MAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx   # o ~/.config/iris/mail-app-password
    IRIS_MAIL_INTERVAL=60
    IRIS_MAIL_CHANNEL=auto                    # auto|telegram|whatsapp
    IRIS_MAIL_FOLDER=INBOX
    IRIS_MAIL_QUIET=23:00-07:00               # opcional, hora local
    IRIS_MAIL_MAX_PER_TICK=5                  # avisos máximos por revisión
"""

from __future__ import annotations

import base64
import email
import html
import imaplib
import logging
import quopri
import re
import threading
import time
from datetime import datetime
from datetime import time as dtime
from email.header import decode_header, make_header
from email.message import Message
from zoneinfo import ZoneInfo

from . import bridge
from .config import Config
from .db import DB
from .reminders import target_chat

log = logging.getLogger("iris.mail")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_TIMEOUT = 30
BODY_LIMIT = 65536
SUMMARY_CHARS = 400
AUTH_BACKOFF = 300
NETWORK_BACKOFF = 60


class MailWatcher(threading.Thread):
    """Hilo que revisa el buzón de Gmail y avisa de los correos nuevos."""

    def __init__(self, cfg: Config, db: DB):
        super().__init__(name="iris-mail", daemon=True)
        self.cfg = cfg
        self.db = db
        self._stop = threading.Event()
        self._client: imaplib.IMAP4_SSL | None = None
        self._next_retry = 0.0

    def stop(self) -> None:
        self._stop.set()
        self._close()

    def run(self) -> None:
        if not self.cfg.mail:
            log.info("correo desactivado")
            return
        if not self.cfg.mail_user or not self.cfg.mail_password:
            log.warning(
                "correo activado sin credenciales: faltan IRIS_MAIL_USER o "
                "IRIS_MAIL_APP_PASSWORD"
            )
            return
        log.info("correo activo para %s (cada %ss)", self.cfg.mail_user, self.cfg.mail_interval)
        while not self._stop.is_set():
            if time.time() >= self._next_retry:
                try:
                    self._tick()
                except Exception as exc:  # noqa: BLE001
                    self._close()
                    log.warning("no pude revisar el correo: %s", exc)
            if self._stop.wait(self.cfg.mail_interval):
                break

    # ── ciclo ────────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        chat = target_chat(self.cfg, self.db, self.cfg.mail_channel)
        if not chat:
            return
        if _quiet(self.cfg):
            return

        client = self._connect()
        if client is None:
            return

        folder = self.cfg.mail_folder or "INBOX"
        uidvalidity, uidnext = self._status(client, folder)
        state = self.db.get_mail_state(self.cfg.mail_user, folder)

        if not state or str(state.get("uidvalidity") or "") != uidvalidity:
            # Primera vez o buzón recreado: no avisar del correo que ya existía.
            self._select(client, folder)
            self.db.set_mail_state(
                self.cfg.mail_user, folder, uidvalidity, max(0, uidnext - 1)
            )
            log.info("correo: base en UID %s (UIDVALIDITY %s)", uidnext - 1, uidvalidity)
            return

        last_uid = int(state.get("last_uid") or 0)
        uids = self._new_uids(client, folder, last_uid)
        sent = 0
        for uid in uids:
            if self._stop.is_set() or sent >= self.cfg.mail_max_per_tick:
                break
            result = self._process(client, chat, folder, uidvalidity, uid)
            if result is None:
                break
            if result == "sent":
                sent += 1

    def _connect(self) -> imaplib.IMAP4_SSL | None:
        if self._client is not None:
            try:
                typ, _ = self._client.noop()
                if typ == "OK":
                    return self._client
            except Exception:  # noqa: BLE001
                self._close()

        password = (self.cfg.mail_password or "").replace(" ", "")
        try:
            client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT)
            client.login(self.cfg.mail_user, password)
            self._client = client
            return client
        except imaplib.IMAP4.error as exc:
            self._close()
            text = str(exc).upper()
            if "AUTHENTICATIONFAILED" in text or "INVALID" in text:
                log.error("Gmail rechazó la contraseña de aplicación: %s", exc)
                self._next_retry = time.time() + AUTH_BACKOFF
            else:
                log.warning("Gmail rechazó la conexión: %s", exc)
                self._next_retry = time.time() + NETWORK_BACKOFF
            return None
        except OSError as exc:
            self._close()
            log.warning("no pude conectar a Gmail: %s", exc)
            self._next_retry = time.time() + NETWORK_BACKOFF
            return None

    def _close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass

    # ── IMAP ─────────────────────────────────────────────────────────────────

    def _status(self, client: imaplib.IMAP4_SSL, folder: str) -> tuple[str, int]:
        typ, data = client.status(_quote(folder), "(UIDVALIDITY UIDNEXT)")
        raw = b" ".join(item for item in (data or []) if isinstance(item, bytes))
        text = raw.decode("utf-8", "replace")
        validity = re.search(r"UIDVALIDITY\s+(\d+)", text)
        next_uid = re.search(r"UIDNEXT\s+(\d+)", text)
        if typ != "OK" or not validity or not next_uid:
            raise imaplib.IMAP4.error(f"no pude leer UIDVALIDITY/UIDNEXT: {text[:120]}")
        return validity.group(1), int(next_uid.group(1))

    def _select(self, client: imaplib.IMAP4_SSL, folder: str) -> None:
        typ, _ = client.select(_quote(folder))
        if typ != "OK":
            raise imaplib.IMAP4.error(f"no pude abrir la carpeta {folder}")

    def _new_uids(
        self, client: imaplib.IMAP4_SSL, folder: str, last_uid: int
    ) -> list[int]:
        self._select(client, folder)
        typ, data = client.uid("search", None, f"UID {last_uid + 1}:*")
        if typ != "OK":
            raise imaplib.IMAP4.error("no pude buscar correos nuevos")
        return sorted(uid for uid in _parse_uids(data) if uid > last_uid)

    def _process(
        self,
        client: imaplib.IMAP4_SSL,
        chat: str,
        folder: str,
        uidvalidity: str,
        uid: int,
    ) -> str | None:
        """Procesa un UID. Devuelve 'sent', 'skipped' o None si hay que parar."""
        # Se piden TODAS las cabeceras: sin Content-Type, boundary ni
        # Content-Transfer-Encoding el parser no puede deshacer quoted-printable
        # ni base64 y el aviso acaba mostrando el MIME en crudo.
        header = _fetch(client, uid, "(BODY.PEEK[HEADER])")
        body = _fetch(client, uid, f"(BODY.PEEK[TEXT]<0.{BODY_LIMIT}>)")
        if not header and not body:
            # El correo pudo borrarse entre la búsqueda y el fetch: no bloquees
            # los siguientes UIDs por uno que ya no existe.
            log.warning("correo uid=%s: no pude leerlo, lo salto", uid)
            self.db.set_mail_state(self.cfg.mail_user, folder, uidvalidity, uid)
            return "skipped"
        message = email.message_from_bytes(header.rstrip() + b"\r\n\r\n" + body)

        subject = _header(message, "Subject") or "(sin asunto)"
        sender = _header(message, "From") or "(sin remitente)"
        message_id = _header(message, "Message-ID") or (
            f"{self.cfg.mail_user}:{uidvalidity}:{uid}"
        )

        sent = False
        if not self.db.was_mail_notified(message_id):
            text = _format(subject, sender, _summary(message))
            if not bridge.send_text(self.cfg, chat, text):
                log.warning("correo uid=%s: no pude enviar el aviso", uid)
                return None
            self.db.mark_mail_notified(message_id)
            sent = True

        # El estado avanza solo cuando el correo quedó resuelto (avisado o ya
        # avisado antes), para no repetir ni perder correos tras un fallo.
        self.db.set_mail_state(self.cfg.mail_user, folder, uidvalidity, uid)
        try:
            client.uid("store", str(uid), "+FLAGS", "(\\Seen)")
        except imaplib.IMAP4.error as exc:
            log.warning("correo uid=%s: no pude marcarlo como leído: %s", uid, exc)
        return "sent" if sent else "skipped"


# ── utilidades ───────────────────────────────────────────────────────────────


def _quote(folder: str) -> str:
    return '"' + str(folder).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fetch(client: imaplib.IMAP4_SSL, uid: int, spec: str) -> bytes:
    typ, data = client.uid("fetch", str(uid), spec)
    if typ != "OK" or not data:
        return b""
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return b""


def _parse_uids(data: list | tuple | None) -> list[int]:
    raw = b" ".join(item for item in (data or []) if isinstance(item, bytes))
    return [int(part) for part in raw.split() if part.isdigit()]


def _header(message: Message, name: str) -> str:
    raw = message.get(name, "")
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(str(raw)))).strip()
    except Exception:  # noqa: BLE001
        return str(raw).strip()


_ZERO_WIDTH = re.compile(r"[​‌‍﻿­]")
_IMAGE_TAG = re.compile(r"\[image:[^\]]*\]", re.IGNORECASE)
_MIME_HEADER = re.compile(
    r"(?im)^(content-type|content-transfer-encoding|content-disposition|"
    r"mime-version):.*$"
)
_MIME_BOUNDARY = re.compile(r"(?m)^--[=\-_A-Za-z0-9.]+.*$")


def _summary(message: Message, limit: int = SUMMARY_CHARS) -> str:
    text = _text_body(message)
    text = _ZERO_WIDTH.sub("", text)
    text = _IMAGE_TAG.sub(" ", text)
    if _looks_like_raw_mime(text):
        text = _clean_raw_mime(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _looks_like_raw_mime(text: str) -> bool:
    lowered = text.lower()
    return "content-transfer-encoding:" in lowered or "content-type:" in lowered


def _clean_raw_mime(text: str) -> str:
    """Quita cabeceras/boundaries y deshace la codificación si quedó en crudo."""
    match = re.search(
        r"(?im)^content-transfer-encoding:\s*([^\s;]+)", text
    )
    encoding = match.group(1).lower() if match else ""
    text = _MIME_HEADER.sub(" ", text)
    text = _MIME_BOUNDARY.sub(" ", text)
    if encoding == "quoted-printable":
        return quopri.decodestring(text.encode("utf-8", "replace")).decode(
            "utf-8", "replace"
        )
    if encoding == "base64":
        cleaned = re.sub(r"\s+", "", text)
        cleaned += "=" * (-len(cleaned) % 4)
        try:
            return base64.b64decode(cleaned, validate=False).decode(
                "utf-8", "replace"
            )
        except Exception:  # noqa: BLE001
            return ""
    return text


def _text_body(message: Message) -> str:
    if message.is_multipart():
        plain: list[str] = []
        rich: list[str] = []
        for part in message.walk():
            if part.is_multipart():
                continue
            if "attachment" in str(
                part.get("Content-Disposition") or ""
            ).lower():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                text = _decode(part).strip()
                if text:
                    plain.append(text)
            elif ctype == "text/html":
                text = _strip_html(_decode(part)).strip()
                if text:
                    rich.append(text)
        # Primero el texto plano; si solo hay HTML, se limpia el HTML.
        return "\n\n".join(plain or rich)
    content_type = message.get_content_type()
    if not content_type.startswith("text/"):
        return ""
    text = _decode(message)
    return _strip_html(text) if content_type == "text/html" else text


def _decode(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True) or b""
    except Exception:  # noqa: BLE001
        # Base64 truncado por el límite de cuerpo: se intenta a mano.
        payload = _raw_payload(part)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def _raw_payload(part: Message) -> bytes:
    raw = part.get_payload(decode=False)
    if not isinstance(raw, str):
        return b""
    encoding = str(part.get("Content-Transfer-Encoding") or "").lower()
    if "base64" in encoding:
        cleaned = re.sub(r"\s+", "", raw)
        cleaned += "=" * (-len(cleaned) % 4)
        try:
            return base64.b64decode(cleaned, validate=False)
        except Exception:  # noqa: BLE001
            return b""
    if "quoted-printable" in encoding:
        return quopri.decodestring(raw.encode("utf-8", "replace"))
    return raw.encode("utf-8", "replace")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return html.unescape(text)


def _format(subject: str, sender: str, summary: str) -> str:
    parts = [f"📬 *{subject}*", f"De: {sender}"]
    parts.append(summary or "(sin texto; puede traer adjuntos)")
    return "\n\n".join(parts)


def _quiet(cfg: Config) -> bool:
    """True si estamos dentro del horario silencioso (hora local)."""
    spec = (cfg.mail_quiet or "").strip()
    if not spec:
        return False
    try:
        start_raw, end_raw = spec.split("-", 1)
        start_h, start_m = (int(value) for value in start_raw.split(":"))
        end_h, end_m = (int(value) for value in end_raw.split(":"))
        start = dtime(start_h, start_m)
        end = dtime(end_h, end_m)
    except (ValueError, TypeError):
        log.warning("IRIS_MAIL_QUIET no válido: %s (usa HH:MM-HH:MM)", spec)
        return False
    now = datetime.now(ZoneInfo(cfg.timezone)).time()
    if start <= end:
        return start <= now < end
    return now >= start or now < end
