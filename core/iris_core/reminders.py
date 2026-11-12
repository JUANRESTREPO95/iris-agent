"""Recordatorios de Notas: avisos y reprogramación por chat."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from . import bridge, notas
from .config import Config
from .db import DB

log = logging.getLogger("iris.reminders")
PENDING_TTL = 3600
YES = {"si", "sí", "s", "yes", "y", "ok", "vale", "listo", "hecho"}
NO = {"no", "n", "todavia", "todavía", "luego", "despues", "después"}


class ReminderWatcher(threading.Thread):
    """Revisa cada intervalo los recordatorios vencidos de Notas."""

    def __init__(self, cfg: Config, db: DB):
        super().__init__(name="iris-reminders", daemon=True)
        self.cfg = cfg
        self.db = db
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if not self.cfg.reminders:
            log.info("recordatorios desactivados")
            return
        if not notas.configured(self.cfg):
            log.info("recordatorios sin Notas configurado")
            return
        while not self._stop.wait(self.cfg.reminder_interval):
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("no pude revisar los recordatorios: %s", exc)

    def _tick(self) -> None:
        chat = target_chat(self.cfg, self.db)
        if not chat:
            return
        now = datetime.now(dt_timezone.utc)
        for item in notas.list_reminders(self.cfg):
            due_raw = str(item.get("due_at") or "")
            if not due_raw:
                continue
            try:
                due = datetime.fromisoformat(due_raw)
            except ValueError:
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=dt_timezone.utc)
            if due > now:
                continue
            if self.db.was_notified(str(item["id"]), due_raw):
                continue
            if bridge.send_text(self.cfg, chat, _message(self.cfg, item)):
                self.db.mark_notified(str(item["id"]), due_raw)
                self.db.set_pending(chat, str(item["id"]), "confirm")


def target_chat(
    cfg: Config, db: DB | None = None, channel: str | None = None
) -> str | None:
    choice = (channel or cfg.reminder_channel or "auto").lower()
    channels = (cfg.channels or "").lower()

    def telegram() -> str | None:
        user = (cfg.telegram_user or "").strip()
        return f"tg:{user}" if user else None

    def whatsapp() -> str | None:
        # Prefiere el chat real (LID incluido): así el estado pendiente coincide
        # con el id desde el que responde el usuario.
        if db is not None:
            stored = db.last_chat("whatsapp")
            if stored:
                return stored
        digits = "".join(char for char in (cfg.phone or "") if char.isdigit())
        # Baileys rechaza números sueltos: el chat debe ser un JID.
        return f"{digits}@s.whatsapp.net" if digits else None

    if choice == "telegram":
        return telegram()
    if choice in {"whatsapp", "whats"}:
        return whatsapp()
    if ("telegram" in channels or "both" in channels) and telegram():
        return telegram()
    return whatsapp()


def _message(cfg: Config, item: dict) -> str:
    title = str(item.get("title") or "Recordatorio")
    body = str(item.get("body") or "").strip()
    due = str(item.get("due_at") or "")
    try:
        local = datetime.fromisoformat(due).astimezone(ZoneInfo(cfg.timezone))
        when = local.strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        when = due
    parts = [f"⏰ *Recordatorio:* {title}"]
    if body:
        parts.append(body)
    if when:
        parts.append(f"Vence: {when}")
    parts.append("¿Lo marco como listo? (sí/no)")
    return "\n\n".join(parts)


def handle_reply(cfg: Config, db: DB, chat_id: str, text: str) -> str | None:
    """Responde al flujo sí/no/reprogramar. None = sigue el flujo normal."""
    pending = db.get_pending(chat_id)
    if not pending:
        return None
    if time.time() - float(pending["updated_at"]) > PENDING_TTL:
        db.clear_pending(chat_id)
        return None
    answer = (text or "").strip().lower()
    reminder_id = str(pending["reminder_id"])

    if pending["state"] == "confirm":
        if answer in YES:
            db.clear_pending(chat_id)
            if notas.set_reminder_done(cfg, reminder_id):
                return "Listo, recordatorio marcado como hecho ✅."
            return "No pude marcarlo como hecho; inténtalo de nuevo."
        if answer in NO:
            db.set_pending(chat_id, reminder_id, "reschedule")
            return "¿Para cuándo lo reprogramo? Dime la nueva fecha y hora."
        db.clear_pending(chat_id)
        return None

    if pending["state"] == "reschedule":
        if answer.startswith("/"):
            db.clear_pending(chat_id)
            return None
        db.clear_pending(chat_id)
        if not answer:
            return "No entendí la fecha; el recordatorio sigue igual."
        queued = db.enqueue(
            {
                "chatId": chat_id,
                "senderId": chat_id,
                "text": reschedule_prompt(cfg, reminder_id, text),
                "kind": "reschedule",
            }
        )
        if not queued:
            return "No pude reprogramar el recordatorio."
        return "Reprogramando el recordatorio…"

    db.clear_pending(chat_id)
    return None


def reschedule_prompt(cfg: Config, reminder_id: str, text: str) -> str:
    now = datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d %H:%M")
    return f"""El usuario quiere reprogramar el recordatorio con id {reminder_id}.

Ahora es {now} ({cfg.timezone}). Lo que dijo: "{text}".

Usa la API de Notas que ya tienes en el entorno:
- Base: $NOTAS_API_URL o la primera de $NOTAS_API_URLS (separadas por comas)
  que responda; cabecera: Authorization: Bearer $NOTAS_API_TOKEN.
- PATCH /api/reminders/{reminder_id} con
  {{"due_at": "<fecha ISO con zona>", "timezone": "{cfg.timezone}"}}.

Calcula la fecha y hora que pidió el usuario y actualízala. Después responde
únicamente: Listo, reprogramado para <fecha y hora>."""
