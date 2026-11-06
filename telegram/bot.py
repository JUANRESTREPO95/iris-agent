#!/usr/bin/env python3
"""Iris Agent — puente de Telegram (Bot API, long polling).

Proceso aislado que habla con Telegram y reenvía los mensajes al núcleo por
HTTP local, igual que el puente de WhatsApp.

Variables (vienen de /etc/iris/iris.env):
  IRIS_TELEGRAM_TOKEN          token de @BotFather
  IRIS_TELEGRAM_USER           id numérico autorizado (o "*")
                               (también acepta IRIS_TELEGRAM_ALLOWED_USER)
  IRIS_TELEGRAM_BRIDGE_PORT    puerto local (3005)
  IRIS_TELEGRAM_API_BASE       por defecto https://api.telegram.org
  IRIS_CORE_URL                http://127.0.0.1:3004
  IRIS_BRIDGE_TOKEN            token compartido con el núcleo
  IRIS_DATA                    /var/lib/iris
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = (os.environ.get("IRIS_TELEGRAM_TOKEN") or "").strip()
ALLOWED = (
    os.environ.get("IRIS_TELEGRAM_ALLOWED_USER")
    or os.environ.get("IRIS_TELEGRAM_USER")
    or ""
).strip()
PORT = int((os.environ.get("IRIS_TELEGRAM_BRIDGE_PORT") or "3005").strip())
API_BASE = (os.environ.get("IRIS_TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")
CORE_URL = (os.environ.get("IRIS_CORE_URL") or "http://127.0.0.1:3004").rstrip("/")
BRIDGE_TOKEN = (os.environ.get("IRIS_BRIDGE_TOKEN") or "").strip()
DATA_DIR = Path(os.environ.get("IRIS_DATA") or "/var/lib/iris")
MEDIA_DIR = DATA_DIR / "telegram-media"
OFFSET_FILE = DATA_DIR / "telegram-offset.json"
MAX_TEXT = 4000
SEND_LOCK = threading.Lock()
BOT_USERNAME = ""
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [iris-telegram] %(levelname)s %(message)s",
)
log = logging.getLogger("iris.telegram")

if not TOKEN:
    log.error("Falta IRIS_TELEGRAM_TOKEN")
    sys.exit(2)
if not BRIDGE_TOKEN:
    log.error("Falta IRIS_BRIDGE_TOKEN")
    sys.exit(2)

MEDIA_DIR.mkdir(parents=True, exist_ok=True)


# ── Telegram API ────────────────────────────────────────────────────────────

def api(method: str, payload: dict | None = None, timeout: float = 70) -> dict:
    url = f"{API_BASE}/bot{TOKEN}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") or "{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"ok": False, "description": raw[:300]}
        if exc.code == 429:
            retry = int(((body.get("parameters") or {}).get("retry_after")) or 1)
            log.warning("Telegram 429; espero %ss", retry)
            time.sleep(min(retry, 30))
        raise RuntimeError(body.get("description") or f"HTTP {exc.code}")
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or f"Telegram {method} falló")
    return body.get("result") or {}


def download(file_id: str, suffix: str) -> Path | None:
    try:
        info = api("getFile", {"file_id": file_id}, timeout=30)
        file_path = info.get("file_path")
        if not file_path:
            return None
        url = f"{API_BASE}/file/bot{TOKEN}/{file_path}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
        safe = "".join(ch for ch in file_id if ch.isalnum())[:16]
        name = f"{int(time.time())}_{safe}.{suffix.lstrip('.') or 'bin'}"
        target = MEDIA_DIR / name
        target.write_bytes(data)
        return target
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude descargar el archivo %s: %s", file_id[:12], exc)
        return None


# ── Mensajes ────────────────────────────────────────────────────────────────

def extract_message(msg: dict) -> tuple[str, dict | None]:
    text = str(msg.get("text") or msg.get("caption") or "")
    media = None

    if msg.get("photo"):
        photo = msg["photo"][-1]
        path = download(photo.get("file_id", ""), "jpg")
        if path:
            media = {"path": str(path), "kind": "image"}
    elif msg.get("document"):
        doc = msg["document"]
        file_name = str(doc.get("file_name") or "")
        suffix = Path(file_name).suffix or ".bin"
        path = download(doc.get("file_id", ""), suffix)
        if path:
            media = {"path": str(path), "kind": "document"}
    elif msg.get("voice"):
        voice = msg["voice"]
        path = download(voice.get("file_id", ""), "ogg")
        if path:
            media = {"path": str(path), "kind": "audio"}
    elif msg.get("audio"):
        audio = msg["audio"]
        suffix = Path(str(audio.get("file_name") or "")).suffix or ".mp3"
        path = download(audio.get("file_id", ""), suffix)
        if path:
            media = {"path": str(path), "kind": "audio"}
    elif msg.get("video"):
        video = msg["video"]
        path = download(video.get("file_id", ""), "mp4")
        if path:
            media = {"path": str(path), "kind": "video"}

    return text, media


def post_core(payload: dict) -> None:
    url = f"{CORE_URL}/inbox"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BRIDGE_TOKEN}",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def process_update(update: dict) -> bool:
    msg = update.get("message")
    if not isinstance(msg, dict):
        return True  # nada que hacer; se puede avanzar el offset

    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    chat_id = chat.get("id")
    sender_id = sender.get("id")
    if chat_id is None or sender_id is None:
        return True

    if ALLOWED != "*" and str(sender_id) != ALLOWED:
        log.warning("ignorado: usuario de Telegram no autorizado (%s)", sender_id)
        return True

    text, media = extract_message(msg)
    if not text and not media:
        return True

    payload = {
        "id": f"tg-{update.get('update_id')}",
        "chatId": f"tg:{chat_id}",
        "senderId": str(sender_id),
        "senderName": " ".join(
            part for part in (sender.get("first_name"), sender.get("last_name")) if part
        ),
        "text": text,
        "media": media,
        "channel": "telegram",
        "ts": int(time.time()),
    }

    for attempt in range(60):
        try:
            post_core(payload)
            return True
        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                log.warning("núcleo no disponible; reintentando: %s", exc)
            time.sleep(5)
    return False


# ── Offset persistente ──────────────────────────────────────────────────────

def load_offset() -> int:
    try:
        return int(json.loads(OFFSET_FILE.read_text(encoding="utf-8")).get("offset", 0))
    except Exception:  # noqa: BLE001
        return 0


def save_offset(offset: int) -> None:
    try:
        tmp = OFFSET_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"offset": offset}), encoding="utf-8")
        tmp.replace(OFFSET_FILE)
    except OSError as exc:
        log.warning("no pude guardar el offset: %s", exc)


def poll_loop() -> None:
    offset = load_offset()
    if offset == 0:
        # Primera vez: saltar el historial previo del bot.
        try:
            recent = api("getUpdates", {"offset": -1, "limit": 1, "timeout": 0}, timeout=20)
            if recent:
                offset = int(recent[-1].get("update_id", 0)) or 0
                if offset:
                    offset += 1
                save_offset(offset)
        except Exception as exc:  # noqa: BLE001
            log.warning("no pude inicializar el offset: %s", exc)

    while True:
        try:
            updates = api(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                },
                timeout=45,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("getUpdates falló: %s", exc)
            time.sleep(5)
            continue

        for update in updates or []:
            update_id = int(update.get("update_id") or 0)
            if process_update(update):
                offset = update_id + 1
                save_offset(offset)


# ── HTTP local para el núcleo ───────────────────────────────────────────────

def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    text = str(text or "")
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def send_text(chat_id: str, text: str) -> list[str]:
    target = str(chat_id)
    if target.startswith("tg:"):
        target = target[3:]
    ids: list[str] = []
    with SEND_LOCK:
        for chunk in split_text(text):
            result = api(
                "sendMessage",
                {
                    "chat_id": target,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )
            if result.get("message_id") is not None:
                ids.append(str(result["message_id"]))
            if len(chunk) and chunk != text:
                time.sleep(0.2)
    return ids


class Handler(BaseHTTPRequestHandler):
    server_version = "IrisTelegram/1.0"

    def log_message(self, *args):  # noqa: D401
        return

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth(self) -> bool:
        header = self.headers.get("Authorization") or ""
        given = header[7:] if header.startswith("Bearer ") else ""
        return hmac.compare_digest(given.encode(), BRIDGE_TOKEN.encode())

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._json(400, {"error": "Host no permitido"})
        if self.path == "/health":
            return self._json(
                200,
                {"status": "ok", "channel": "telegram", "bot": BOT_USERNAME},
            )
        return self._json(404, {"error": "no encontrado"})

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return self._json(400, {"error": "Host no permitido"})
        if not self._auth():
            return self._json(401, {"error": "No autorizado"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return self._json(400, {"error": "JSON inválido"})

        if self.path == "/send":
            chat_id = body.get("chatId")
            text = body.get("text")
            if not chat_id or not isinstance(text, str):
                return self._json(400, {"error": "chatId y text son obligatorios"})
            try:
                ids = send_text(str(chat_id), text)
                return self._json(200, {"ok": True, "messageIds": ids})
            except Exception as exc:  # noqa: BLE001
                log.warning("sendMessage falló: %s", exc)
                return self._json(502, {"error": str(exc)})

        if self.path == "/typing":
            chat_id = str(body.get("chatId") or "")
            if chat_id.startswith("tg:"):
                chat_id = chat_id[3:]
            if not chat_id:
                return self._json(400, {"error": "chatId es obligatorio"})
            try:
                api("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=15)
                return self._json(200, {"ok": True})
            except Exception as exc:  # noqa: BLE001
                return self._json(200, {"ok": False, "error": str(exc)})

        return self._json(404, {"error": "no encontrado"})


def main() -> None:
    global BOT_USERNAME
    try:
        me = api("getMe", timeout=20)
        BOT_USERNAME = str(me.get("username") or "")
    except Exception as exc:  # noqa: BLE001
        log.error("token de Telegram inválido o sin red: %s", exc)
        sys.exit(2)

    log.info("bot @%s; permitido: %s", BOT_USERNAME, ALLOWED or "(nadie)")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="http").start()
    log.info("escuchando en 127.0.0.1:%s", PORT)

    def stop(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    poll_loop()


if __name__ == "__main__":
    main()
