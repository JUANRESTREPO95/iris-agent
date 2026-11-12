"""Sincronización de proyectos entre el workspace de Iris y Notas."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from . import bridge, notas
from .config import Config

log = logging.getLogger("iris.sync")
SYNC_INTERVAL = 300


class ProjectSync(threading.Thread):
    """Avisa cuando un proyecto desaparece de Notas y sigue en el workspace."""

    def __init__(self, cfg: Config, interval: int = SYNC_INTERVAL):
        super().__init__(name="iris-sync", daemon=True)
        self.cfg = cfg
        self.interval = interval
        self._stop = threading.Event()
        self._state_path = Path(cfg.data_dir) / "sync-notified.json"
        self._notified = self._load_state()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                log.warning("no pude sincronizar los proyectos: %s", exc)

    def _load_state(self) -> set[str]:
        """Proyectos ya avisados; sobrevive a reinicios del núcleo."""
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        if not isinstance(data, list):
            return set()
        return {str(item) for item in data if isinstance(item, str)}

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(sorted(self._notified)), encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError as exc:
            log.warning("no pude guardar el estado de sincronización: %s", exc)

    def _tick(self) -> None:
        if not notas.configured(self.cfg):
            return
        remote = set(notas.list_projects(self.cfg))
        try:
            local = {
                path.name
                for path in Path(self.cfg.workspace).iterdir()
                if path.is_dir() and not path.name.startswith(".")
            }
        except OSError:
            return
        local -= {"general"}
        missing = local - remote
        if self._notified - missing:
            # El proyecto volvió a Notas (o desapareció el código): se olvida
            # para poder avisar de nuevo si vuelve a faltar.
            self._notified &= missing
            self._save_state()
        new = sorted(missing - self._notified)
        if not new:
            return
        chat = self._target_chat()
        if not chat:
            return
        for name in new:
            sent = bridge.send_text(
                self.cfg,
                chat,
                f"El proyecto *{name}* ya no está en Notas. Si quieres borrar "
                f"también sus archivos, responde `/borrarproyecto {name} codigo`.",
            )
            if sent:
                self._notified.add(name)
                self._save_state()

    def _target_chat(self) -> str | None:
        channels = (self.cfg.channels or "").lower()
        if ("telegram" in channels or "both" in channels) and self.cfg.telegram_user:
            return f"tg:{self.cfg.telegram_user}"
        if self.cfg.phone:
            digits = "".join(char for char in self.cfg.phone if char.isdigit())
            return digits or None
        return None
