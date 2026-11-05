"""Comandos de control que responde el propio núcleo (sin pasar por Claude)."""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config
from .db import DB
from .worker import PROJECT_RE, Worker

HELP = """*Iris — comandos*

/proyecto [nombre] — ver o cambiar de proyecto
/nuevo — sesión nueva en el proyecto actual
/modelo [nombre] — ver o cambiar el modelo
/estado — cola, proyecto, modelo y sesión
/detener — detener la tarea en curso
/ayuda — esta lista

Todo lo demás se lo envío a Claude Code."""

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,79}$")


def is_command(text: str) -> bool:
    return str(text or "").lstrip().startswith("/")


def handle(cfg: Config, db: DB, worker: Worker, chat_id: str, text: str) -> str:
    parts = str(text or "").strip().split(maxsplit=1)
    command = parts[0].lower().lstrip("/")
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command in {"ayuda", "help", "start"}:
        return HELP

    chat = db.get_chat(chat_id)

    if command in {"proyecto", "project"}:
        if not arg:
            current = chat.get("project") or "general"
            dirs = _list_projects(cfg)
            listing = "\n".join(f"• {d}" for d in dirs) if dirs else "(ninguno todavía)"
            return f"Proyecto actual: *{current}*\n\nDisponibles:\n{listing}"
        if not PROJECT_RE.match(arg):
            return "Nombre no válido. Usa letras, números, punto, guion o guion bajo (máx. 40)."
        (Path(cfg.workspace) / arg).mkdir(parents=True, exist_ok=True)
        db.set_project(chat_id, arg)
        has_session = db.get_session(chat_id, arg) is not None
        estado = "con sesión previa" if has_session else "sesión nueva"
        return f"Proyecto: *{arg}* ({estado})."

    if command in {"nuevo", "new", "reset"}:
        project = chat.get("project") or "general"
        db.clear_session(chat_id, project)
        return f"Sesión nueva en *{project}*."

    if command in {"modelo", "model"}:
        if not arg:
            current = chat.get("model") or cfg.model or "(predeterminado del proveedor)"
            source = "del chat" if chat.get("model") else "predeterminado"
            return f"Modelo: *{current}* ({source})."
        if arg.lower() in {"default", "predeterminado", "reset"}:
            db.set_model(chat_id, None)
            return f"Modelo restablecido a *{cfg.model or 'predeterminado del proveedor'}*."
        if not MODEL_RE.match(arg):
            return "Nombre de modelo no válido."
        db.set_model(chat_id, arg)
        return f"Modelo: *{arg}*."

    if command in {"estado", "status"}:
        status = worker.status()
        model = chat.get("model") or cfg.model
        project = chat.get("project") or "general"
        session = db.get_session(chat_id, project)
        lines = [
            "*Estado de Iris*",
            f"• Proveedor: {cfg.provider}",
            f"• Proyecto: {project}",
            f"• Modelo: {model or '(predeterminado del proveedor)'}",
            f"• Sesión: {session[:8] if session else 'nueva'}",
            f"• Cola: {status['queue']}",
        ]
        if status["running"] and status["current"]:
            cur = status["current"]
            lines.append(f"• Ejecutando: {cur.get('project')} desde hace {int(_age(cur))} s")
        else:
            lines.append("• Ejecutando: nada")
        return "\n".join(lines)

    if command in {"detener", "stop", "cancelar"}:
        if worker.cancel():
            return "Deteniendo la tarea…"
        return "No hay ninguna tarea en curso."

    if command in {"id", "chatid"}:
        return f"Chat: `{chat_id}`"

    return "Comando no reconocido.\n\n" + HELP


def _list_projects(cfg: Config) -> list[str]:
    try:
        return sorted(
            p.name
            for p in Path(cfg.workspace).iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
    except OSError:
        return []


def _age(current: dict) -> float:
    import time

    return max(0.0, time.time() - float(current.get("started") or time.time()))
