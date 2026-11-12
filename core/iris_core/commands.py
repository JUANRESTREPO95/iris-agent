"""Comandos de control que responde el propio núcleo (sin pasar por Claude)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import notas
from .config import Config
from .db import DB
from .worker import PROJECT_RE, Worker

HELP = """*Iris — comandos*

/proyecto [nombre] — ver o cambiar de proyecto
/nuevo — sesión nueva en el proyecto actual
/modelo [nombre] — ver o cambiar el modelo
/estado — cola, proyecto, modelo y sesión
/detener — detener la tarea en curso
/guardar — guardar lo importante en la memoria del proyecto
/borrarproyecto <nombre> [codigo] — borrar el proyecto (y su código)
/comandos — ver comandos y skills de Claude Code (alias /recargar)
/ayuda — esta lista

Cualquier otro /comando se envía a Claude Code."""

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,79}$")

# Solo estos los responde Iris. Cualquier otro /... va a Claude Code.
IRIS_COMMANDS = {
    "ayuda", "help", "start",
    "proyecto", "project",
    "nuevo", "new", "reset",
    "modelo", "model",
    "estado", "status",
    "detener", "stop", "cancelar",
    "id", "chatid",
    "guardar", "save",
    "borrarproyecto", "deleteproject",
    "comandos", "commands", "skills", "recargar",
}


def is_iris_command(text: str) -> bool:
    stripped = str(text or "").lstrip()
    if not stripped.startswith("/"):
        return False
    name = stripped[1:].split(maxsplit=1)[0].lower()
    return name in IRIS_COMMANDS


# Compatibilidad con llamadas antiguas.
def is_command(text: str) -> bool:
    return is_iris_command(text)


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
            dirs = _all_projects(cfg)
            listing = "\n".join(f"• {d}" for d in dirs) if dirs else "(ninguno todavía)"
            return f"Proyecto actual: *{current}*\n\nDisponibles:\n{listing}"
        if not PROJECT_RE.match(arg):
            return "Nombre no válido. Usa letras, números, punto, guion o guion bajo (máx. 40)."
        (Path(cfg.workspace) / arg).mkdir(parents=True, exist_ok=True)
        db.set_project(chat_id, arg)
        has_session = db.get_session(chat_id, arg) is not None
        estado = "con sesión previa" if has_session else "sesión nueva"
        if notas.configured(cfg):
            if notas.ensure_project(cfg, arg):
                estado += " · sincronizado en Notas"
            else:
                estado += " · sin conexión con Notas"
        return f"Proyecto: *{arg}* ({estado})."

    if command in {"guardar", "save"}:
        if not notas.configured(cfg):
            return "Notas no está configurado: no puedo guardar la memoria."
        project = chat.get("project") or "general"
        notas.ensure_project(cfg, project)
        queued = db.enqueue(
            {
                "chatId": chat_id,
                "senderId": chat_id,
                "text": _guardar_prompt(project),
                "kind": "guardar",
            }
        )
        if not queued:
            return "No pude encolar el guardado."
        return f"Guardando lo importante en *{project}*…"

    if command in {"borrarproyecto", "deleteproject"}:
        parts = arg.split()
        if not parts:
            return "Uso: /borrarproyecto <nombre> [codigo]"
        name = parts[0]
        if name == "general" or not PROJECT_RE.match(name):
            return "Nombre de proyecto no válido."
        with_code = any(p.lower() in {"codigo", "código", "code"} for p in parts[1:])
        removed_memory = False
        if notas.configured(cfg):
            if not notas.delete_project(cfg, name):
                return f"No pude borrar *{name}* de Notas."
            removed_memory = True
        workspace = Path(cfg.workspace).resolve()
        target = (workspace / name).resolve()
        has_code = target.is_dir() and target.is_relative_to(workspace)
        if with_code and has_code:
            shutil.rmtree(target)
        db.reset_project(name)
        lines = [f"Proyecto *{name}* eliminado."]
        lines.append("Memoria: borrada de Notas." if removed_memory else "Notas no estaba configurado.")
        if has_code:
            lines.append("Código: eliminado." if with_code else "Código: conservado.")
        return "\n".join(lines)

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

    if command in {"comandos", "commands", "skills", "recargar"}:
        project = chat.get("project") or "general"
        return _list_custom_commands(cfg, project)

    return "Comando no reconocido.\n\n" + HELP


def _list_custom_commands(cfg: Config, project: str) -> str:
    entries = _discover_custom_commands(cfg, project)
    if not entries:
        return (
            "No hay comandos ni skills personalizados instalados en Claude Code.\n\n"
            "Cuando los instales se descubren solos en el siguiente mensaje; "
            "no hace falta reiniciar."
        )

    lines = ["*Comandos y skills de Claude Code*", ""]
    for name in sorted(entries):
        desc, scope = entries[name]
        if len(desc) > 120:
            desc = desc[:117].rstrip() + "…"
        suffix = f" — {desc}" if desc else ""
        scope_tag = f" _{scope}_" if scope else ""
        lines.append(f"• /{name}{suffix}{scope_tag}")
    lines += ["", "Se descubren en cada mensaje; no hace falta reiniciar."]
    reply = "\n".join(lines)
    if len(reply) > 3500:
        reply = reply[:3400] + "\n… (lista recortada)"
    return reply


def _discover_custom_commands(cfg: Config, project: str) -> dict[str, tuple[str, str]]:
    home = Path.home()
    project_dir = Path(cfg.workspace) / project
    found: dict[str, tuple[str, str]] = {}

    command_bases = [
        (home / ".claude" / "commands", "usuario"),
        (project_dir / ".claude" / "commands", "proyecto"),
    ]
    for base, scope in command_bases:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(base).with_suffix("")
            name = rel.as_posix().replace("/", ":")
            found[name] = (_frontmatter_description(path), scope)

    skill_bases = [
        (home / ".claude" / "skills", "usuario"),
        (home / ".agents" / "skills", "usuario"),
        (project_dir / ".claude" / "skills", "proyecto"),
    ]
    for base, scope in skill_bases:
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*/SKILL.md")):
            name = path.parent.name
            found.setdefault(name, (_frontmatter_description(path), scope))

    return found


def _frontmatter_description(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8-sig", errors="replace")[:4000]
    except OSError:
        return ""
    match = re.search(r"^---\s*$(.*?)^---\s*$", head, re.S | re.M)
    block = match.group(1) if match else head
    desc = re.search(r"^description:\s*(.*)$", block, re.M)
    if not desc:
        return ""
    value = desc.group(1).strip()
    if value in {"", ">", ">-", ">+", "|", "|-", "|+"}:
        # YAML block scalar: unir las líneas indentadas que siguen.
        lines = block.splitlines()
        for i, line in enumerate(lines):
            if re.match(r"^description:\s*", line):
                parts: list[str] = []
                for nxt in lines[i + 1:]:
                    if nxt.startswith((" ", "\t")):
                        parts.append(nxt.strip())
                    elif not nxt.strip():
                        parts.append("")
                    else:
                        break
                value = " ".join(p for p in parts if p).strip()
                break
    return value.strip().strip("\"'")


def _list_projects(cfg: Config) -> list[str]:
    try:
        return sorted(
            p.name
            for p in Path(cfg.workspace).iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
    except OSError:
        return []


def _all_projects(cfg: Config) -> list[str]:
    names = set(_list_projects(cfg)) | {"general"}
    if notas.configured(cfg):
        names |= set(notas.list_projects(cfg))
    return sorted(names)


def _guardar_prompt(project: str) -> str:
    return f"""Guarda la conversación actual en la memoria del proyecto «{project}».

Usa la API de Notas que ya tienes en el entorno:
- Base: $NOTAS_API_URL o la primera de $NOTAS_API_URLS (separadas por comas)
  que responda; cabecera: Authorization: Bearer $NOTAS_API_TOKEN.
- GET /api/tree para encontrar la nota con path `proyectos/{project}/contexto.md`.
- GET /api/notes/<id> para leerla.
- PUT /api/notes/<id> con {{"content": "<contenido completo>"}} para actualizarla.
- Si no existe, créala con POST /api/notes
  {{"folder": "proyectos/{project}", "title": "Contexto", "content": "..."}}.

Resume solo lo importante: contexto, decisiones, pendientes y referencias.
No copies la conversación entera ni repitas lo que ya esté.
Después responde únicamente: Guardado."""


def _age(current: dict) -> float:
    import time

    return max(0.0, time.time() - float(current.get("started") or time.time()))
