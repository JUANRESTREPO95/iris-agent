"""Autoguardado silencioso del contexto del proyecto en Notas."""

from __future__ import annotations

import logging
import threading
import time

from . import notas
from .config import Config
from .db import DB

log = logging.getLogger("iris.autosave")

CHECK_INTERVAL = 300  # cada 5 minutos se mira si toca guardar
ACTIVE_DAYS = 30
IDLE_SECONDS = 600  # no guardar mientras el usuario está escribiendo


class AutoSaveWatcher(threading.Thread):
    """Encola un /guardar cada N horas, sin avisar por el chat.

    Solo mira el proyecto activo de cada chat con sesión y actividad real
    (los propios guardados no cuentan como actividad).
    """

    def __init__(
        self,
        cfg: Config,
        db: DB,
        interval: int = CHECK_INTERVAL,
        idle_seconds: int = IDLE_SECONDS,
    ):
        super().__init__(name="iris-autosave", daemon=True)
        self.cfg = cfg
        self.db = db
        self.interval = max(30, int(interval))
        self.idle_seconds = max(0, int(idle_seconds))
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _hours(self) -> float:
        return max(0.0, float(getattr(self.cfg, "autosave_hours", 0.0) or 0.0))

    def run(self) -> None:
        hours = self._hours()
        if hours <= 0:
            log.info("autoguardado desactivado (IRIS_AUTOSAVE_HOURS=0)")
            return
        if not notas.configured(self.cfg):
            log.info("autoguardado desactivado: Notas no está configurado")
            return
        log.info("autoguardado activo cada %.1f h, sin avisar", hours)
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("no pude autoguardar: %s", exc)

    def _tick(self) -> None:
        hours = self._hours()
        if hours <= 0:
            return
        now = time.time()
        for target in self.db.autosave_targets(now - ACTIVE_DAYS * 86400):
            chat_id = str(target.get("chat_id") or "")
            project = str(target.get("project") or "general")
            if not chat_id:
                continue
            last = self.db.get_autosave(chat_id, project)
            if last is None:
                # Primera vez: contar desde ahora, sin lanzar un trabajo de
                # inmediato (el próximo mensaje o /guardar ya cubre lo actual).
                self.db.set_autosave(chat_id, project, now)
                continue
            if now - last < hours * 3600:
                continue
            last_user = self.db.last_user_message_at(chat_id)
            if last_user is None or last_user <= last:
                continue
            if now - last_user < self.idle_seconds:
                continue
            if self.db.has_pending(chat_id, "guardar_auto"):
                continue
            if self.db.enqueue(
                {
                    "chatId": chat_id,
                    "senderId": chat_id,
                    "text": notas.guardar_prompt(project),
                    "kind": "guardar_auto",
                    "project": project,
                }
            ):
                self.db.set_autosave(chat_id, project, now)
                log.info(
                    "autoguardado encolado chat=%s proj=%s", chat_id, project
                )
