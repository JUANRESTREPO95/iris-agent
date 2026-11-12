"""Núcleo de Iris: API local + worker que ejecuta Claude Code."""

from __future__ import annotations

import hmac
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from . import bridge, commands, mail, reminders
from .config import load
from .db import DB
from .sync import ProjectSync
from .worker import Worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("iris")

cfg = load()
db = DB(cfg.data_dir / "iris.db")
worker = Worker(cfg, db)
sync = ProjectSync(cfg)
reminder_watcher = reminders.ReminderWatcher(cfg, db)
mail_watcher = mail.MailWatcher(cfg, db)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("Iris core iniciando (modelo=%s, workspace=%s)", cfg.model, cfg.workspace)
    try:
        cfg.outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        log.warning("no pude crear el outbox %s: %s", cfg.outbox, exc)
    worker.start()
    sync.start()
    reminder_watcher.start()
    mail_watcher.start()
    try:
        yield
    finally:
        log.info("Iris core detenido")
        mail_watcher.stop()
        reminder_watcher.stop()
        sync.stop()
        worker.stop()


app = FastAPI(
    title="Iris Agent",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _auth(authorization: str) -> None:
    expected = f"Bearer {cfg.token}"
    if not cfg.token or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="No autorizado")


def _digits(value: str) -> str:
    head = str(value or "").split("@")[0].split(":")[0]
    return re.sub(r"\D+", "", head)


def _sender_allowed(channel: str, sender: str) -> bool:
    if channel == "telegram":
        allowed = (cfg.telegram_user or "").strip()
        if allowed == "*":
            return True
        return bool(allowed) and str(sender or "").strip() == allowed
    phone = re.sub(r"\D+", "", cfg.phone or "")
    if (cfg.phone or "").strip() == "*":
        return True
    if not phone:
        return False
    return _digits(sender) == phone


@app.post("/inbox")
def inbox(payload: dict, authorization: str = Header(default="")):
    """Recibe un mensaje del puente. Responde rápido; el trabajo pesado va a la cola."""
    _auth(authorization)

    channel = str(payload.get("channel") or "whatsapp").strip().lower()
    if channel not in {"whatsapp", "telegram"}:
        raise HTTPException(status_code=400, detail="canal no válido")

    chat_id = str(payload.get("chatId") or "").strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chatId es obligatorio")
    if channel == "telegram" and not chat_id.startswith("tg:"):
        if not chat_id.lstrip("-").isdigit():
            raise HTTPException(status_code=400, detail="chatId de Telegram no válido")
        chat_id = f"tg:{chat_id}"

    if not _sender_allowed(channel, payload.get("senderId")):
        db.audit("rejected_sender", f"{channel}:{payload.get('senderId')}", chat_id)
        log.warning("remitente no autorizado (%s): %s", channel, payload.get("senderId"))
        return {"ok": False, "error": "remitente no autorizado"}

    payload["chatId"] = chat_id
    payload["channel"] = channel
    text = str(payload.get("text") or "")

    reminder_reply = reminders.handle_reply(cfg, db, chat_id, text)
    if reminder_reply is not None:
        bridge.send_text(cfg, chat_id, reminder_reply)
        db.audit("reminder_reply", text.strip()[:200], chat_id)
        return {"ok": True, "reminder": True}

    if commands.is_iris_command(text):
        reply = commands.handle(cfg, db, worker, chat_id, text)
        if reply:
            bridge.send_text(cfg, chat_id, reply)
        db.audit("command", text.strip()[:200], chat_id)
        return {"ok": True, "command": True}

    queued = db.enqueue(payload)
    db.audit("inbox", f"wa={payload.get('id')} queued={queued}", chat_id)
    return {"ok": True, "queued": queued}


@app.get("/health")
def health():
    status = worker.status()
    return {
        "status": "ok",
        "queue": status["queue"],
        "running": status["running"],
        "bridge": (bridge.health(cfg) or {}).get("status", "desconocido"),
        "telegram_bridge": (bridge.telegram_health(cfg) or {}).get(
            "status", "desconocido"
        ),
        "channels": cfg.channels,
        "provider": cfg.provider,
        "model": cfg.model,
        "version": "0.1.0",
    }


@app.get("/status")
def status(authorization: str = Header(default="")):
    _auth(authorization)
    return {
        "worker": worker.status(),
        "bridge": bridge.health(cfg),
        "telegram_bridge": bridge.telegram_health(cfg),
        "channels": cfg.channels,
        "model": cfg.model,
        "workspace": str(cfg.workspace),
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=cfg.bind, port=cfg.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
