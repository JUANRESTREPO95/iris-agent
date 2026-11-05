"""Worker: toma mensajes de la cola y los ejecuta con Claude Code."""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import bridge, claude
from .config import Config
from .db import DB

log = logging.getLogger("iris.worker")

PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


class Worker(threading.Thread):
    def __init__(self, cfg: Config, db: DB):
        super().__init__(name="iris-worker", daemon=True)
        self.cfg = cfg
        self.db = db
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._current: Optional[dict[str, Any]] = None
        self._cancel = False

    # ── API para el resto del núcleo ────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        with self._lock:
            cur = dict(self._current) if self._current else None
        if cur:
            cur.pop("proc", None)
        return {"running": cur is not None, "current": cur, "queue": self.db.queue_size()}

    def cancel(self) -> bool:
        with self._lock:
            cur = dict(self._current) if self._current else None
            if not cur or not cur.get("proc"):
                return False
            self._cancel = True
        log.info("cancelando tarea de %s", cur.get("chat_id"))
        claude._kill_group(cur["proc"])
        return True

    def stop(self) -> None:
        self._stop.set()
        self.cancel()

    # ── bucle ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        log.info("worker iniciado")
        while not self._stop.is_set():
            try:
                row = self.db.claim_next()
            except Exception as exc:  # noqa: BLE001
                log.exception("error leyendo la cola: %s", exc)
                self._stop.wait(2)
                continue
            if row is None:
                self._stop.wait(1)
                continue
            try:
                self._process(row)
            except Exception as exc:  # noqa: BLE001
                log.exception("error procesando mensaje %s", row["id"])
                self.db.mark(row["id"], "error", str(exc)[:1000])
                bridge.send_text(
                    self.cfg,
                    row["chat_id"],
                    f"⚠️ Error interno: {exc}",
                )

    # ── procesamiento ───────────────────────────────────────────────────────

    def _process(self, row) -> None:
        chat_id = row["chat_id"]
        chat = self.db.get_chat(chat_id)
        project = chat.get("project") or "general"
        if not PROJECT_RE.match(project):
            project = "general"
        model = chat.get("model") or self.cfg.model

        project_dir = Path(self.cfg.workspace) / project
        project_dir.mkdir(parents=True, exist_ok=True)

        session_id = self.db.get_session(chat_id, project)
        is_new = session_id is None
        if is_new:
            session_id = str(uuid.uuid4())

        prompt = row["text"] or ""
        if row["media_path"]:
            prompt += (
                f"\n\n[Adjunto: {row['media_path']} ({row['media_kind'] or 'archivo'})]"
            )
        if not prompt.strip():
            prompt = "(mensaje vacío)"

        self.db.audit("job_start", f"proj={project} model={model}", chat_id)
        typing_stop = threading.Event()
        threading.Thread(
            target=self._typing_loop, args=(chat_id, typing_stop), daemon=True
        ).start()

        started = time.time()
        with self._lock:
            self._cancel = False
            self._current = {
                "chat_id": chat_id,
                "message_id": row["id"],
                "project": project,
                "model": model,
                "started": started,
                "proc": None,
            }

        def on_start(proc) -> None:
            with self._lock:
                if self._current:
                    self._current["proc"] = proc

        result = claude.run(
            self.cfg,
            prompt,
            project_dir,
            model,
            session_id,
            is_new,
            on_start=on_start,
        )

        # Si la sesión guardada ya no existe, se reintenta una vez con sesión nueva.
        if (
            not result.ok
            and not is_new
            and not result.cancelled
            and _looks_like_missing_session(result.error or "")
        ):
            log.warning("sesión %s no encontrada; creando una nueva", session_id[:8])
            self.db.clear_session(chat_id, project)
            session_id = str(uuid.uuid4())
            is_new = True
            result = claude.run(
                self.cfg,
                prompt,
                project_dir,
                model,
                session_id,
                is_new,
                on_start=on_start,
            )

        typing_stop.set()

        with self._lock:
            cancelled = self._cancel
            self._current = None
            self._cancel = False

        if result.session_id:
            self.db.set_session(chat_id, project, result.session_id)

        if cancelled or result.cancelled:
            self.db.mark(row["id"], "cancelado")
            self.db.audit("job_cancelled", f"{time.time() - started:.1f}s", chat_id)
            bridge.send_text(self.cfg, chat_id, "⏹ Tarea detenida.")
            return

        if result.ok:
            reply = result.text
            self.db.mark(row["id"], "respondido")
            self.db.audit(
                "job_ok",
                f"proj={project} dur={result.duration:.1f}s chars={len(reply)}",
                chat_id,
            )
        else:
            reply = f"⚠️ {result.error or 'No pude completar la tarea.'}"
            self.db.mark(row["id"], "error", (result.error or "")[:1000])
            self.db.audit(
                "job_error",
                f"proj={project} dur={result.duration:.1f}s err={(result.error or '')[:500]}",
                chat_id,
            )

        if not bridge.send_text(self.cfg, chat_id, reply):
            log.warning("no pude entregar la respuesta por WhatsApp")

    def _typing_loop(self, chat_id: str, stop: threading.Event) -> None:
        while not stop.wait(4):
            bridge.send_typing(self.cfg, chat_id)


def _looks_like_missing_session(error: str) -> bool:
    lowered = error.lower()
    return any(
        needle in lowered
        for needle in (
            "no conversation found",
            "session not found",
            "could not find session",
            "invalid session",
        )
    )
