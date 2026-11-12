"""Memoria compartida en Notas.

Lee y escribe en la app Notas: `configuracion.md`, los `contexto.md` de los
proyectos y el registro de conversaciones. Todo es best-effort: si Notas no
responde, Iris sigue funcionando con lo que tenga en caché.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Config

log = logging.getLogger("iris.notas")

_CACHE: dict[str, object] = {"at": 0.0, "url": "", "content": ""}
_PROJECT_CACHE: dict[str, tuple[float, str]] = {}
NOTE_PATH = "configuracion.md"
PROJECTS = "proyectos"


# Última URL que respondió y cuándo, para no pagar un timeout en cada llamada.
_ACTIVE: dict[str, object] = {"url": "", "at": 0.0}


def _urls(cfg: Config) -> tuple[str, ...]:
    """URLs configuradas, en orden (tolera Config sin `notas_urls`)."""
    urls = tuple(getattr(cfg, "notas_urls", ()) or ())
    if not urls:
        urls = (cfg.notas_url,) if cfg.notas_url else ()
    clean: list[str] = []
    for url in urls:
        value = str(url or "").strip().rstrip("/")
        if value and value not in clean:
            clean.append(value)
    return tuple(clean)


def configured(cfg: Config) -> bool:
    return bool(_urls(cfg) and cfg.notas_token)


def _active_url(cfg: Config) -> str:
    urls = _urls(cfg)
    active = str(_ACTIVE.get("url") or "")
    return active if active in urls else (urls[0] if urls else "")


def _ordered_urls(cfg: Config) -> tuple[str, ...]:
    """Empieza por la última que funcionó; reintenta la primera cada cierto tiempo."""
    urls = _urls(cfg)
    active = str(_ACTIVE.get("url") or "")
    if active not in urls:
        return urls
    try:
        retry = max(0.0, float(os.environ.get("IRIS_NOTAS_RETRY_PRIMARY", "300")))
    except ValueError:
        retry = 300.0
    if active != urls[0] and time.monotonic() - float(_ACTIVE.get("at") or 0.0) >= retry:
        return urls
    return (active,) + tuple(url for url in urls if url != active)


def _remember(url: str) -> None:
    _ACTIVE.update({"url": url, "at": time.monotonic()})


def _ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("IRIS_NOTAS_TTL", "60")))
    except ValueError:
        return 60.0


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("IRIS_NOTAS_TIMEOUT", "10")))
    except ValueError:
        return 10.0


def _request_once(
    cfg: Config, url: str, method: str, path: str, payload: dict | None
) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{url}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {cfg.notas_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        body = response.read().decode("utf-8", "replace")
        return json.loads(body) if body else {}


def _request(cfg: Config, method: str, path: str, payload: dict | None = None) -> dict:
    """Prueba las URLs en orden: si una falla (red, timeout o 5xx), pasa a la siguiente.

    Un 4xx significa que el servidor respondió: se propaga el error sin cambiar
    de URL (no es un problema de conectividad).
    """
    urls = _ordered_urls(cfg)
    if not urls:
        raise RuntimeError("Notas no está configurado")
    last: Exception | None = None
    for url in urls:
        try:
            data = _request_once(cfg, url, method, path, payload)
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                _remember(url)
                raise
            last = exc
        except Exception as exc:  # noqa: BLE001
            last = exc
        else:
            _remember(url)
            return data
    raise last if last is not None else RuntimeError("Notas no respondió")


def _get(cfg: Config, path: str) -> dict:
    return _request(cfg, "GET", path)


def _find_note(tree: dict, path: str) -> dict | None:
    for note in tree.get("notes") or []:
        if str(note.get("path") or "") == path:
            return note
    return None


def memory_context(cfg: Config) -> str:
    """Contenido de configuracion.md, o cadena vacía si no hay memoria."""
    if not configured(cfg):
        return ""
    now = time.monotonic()
    if _CACHE["url"] == _active_url(cfg) and now - float(_CACHE["at"]) < _ttl():
        return str(_CACHE["content"])
    try:
        tree = _get(cfg, "/api/tree")
        note = _find_note(tree, NOTE_PATH)
        if note is None:
            for item in tree.get("notes") or []:
                if str(item.get("title") or "").strip().lower() == "configuración":
                    note = item
                    break
        if note is None:
            log.warning("no encontré %s en Notas", NOTE_PATH)
            return str(_CACHE["content"])
        data = _get(cfg, f"/api/notes/{note['id']}")
        content = str(data.get("content") or "")
        _CACHE.update({"at": now, "url": _active_url(cfg), "content": content})
        return content
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude leer la memoria de Notas: %s", exc)
        return str(_CACHE["content"])


def list_projects(cfg: Config) -> list[str]:
    """Nombres de los proyectos (hijos directos de proyectos/)."""
    if not configured(cfg):
        return []
    try:
        tree = _get(cfg, "/api/tree")
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude listar los proyectos de Notas: %s", exc)
        return []
    prefix = f"{PROJECTS}/"
    names = {
        str(folder)[len(prefix):]
        for folder in tree.get("folders") or []
        if str(folder).startswith(prefix) and str(folder).count("/") == 1
    }
    return sorted(name for name in names if name)


def ensure_project(cfg: Config, name: str) -> bool:
    """Crea proyectos/<name> en Notas. Un 409 significa que ya existía."""
    if not configured(cfg):
        return False
    try:
        _request(cfg, "POST", "/api/folders", {"path": f"{PROJECTS}/{name}"})
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return True
        log.warning("no pude crear el proyecto %s en Notas: HTTP %s", name, exc.code)
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude crear el proyecto %s en Notas: %s", name, exc)
        return False


def guardar_prompt(project: str) -> str:
    """Instrucción para que Claude resuma la conversación en contexto.md."""
    return f"""Guarda la conversación actual en la memoria del proyecto «{project}».

Usa la API de Notas que ya tienes en el entorno:
- Base: $NOTAS_API_URL o la primera de $NOTAS_API_URLS (separadas por comas)
  que responda; cabecera: Authorization: Bearer $NOTAS_API_TOKEN.
- GET /api/tree para encontrar la nota con path `proyectos/{project}/contexto.md`.
- GET /api/notes/<id> para leerla.
- PUT /api/notes/<id> con {{"content": "<contenido completo>"}} para actualizarla.
- Si no existe, créala con POST /api/notes
  {{"folder": "proyectos/{project}", "title": "Contexto", "content": "..."}}.

Si no tienes la conversación en el contexto de esta sesión, lee
`proyectos/{project}/conversacion.md` y resume su último tramo.

Resume solo lo importante: contexto, decisiones, pendientes y referencias.
No copies la conversación entera ni repitas lo que ya esté.
Después responde únicamente: Guardado."""


def project_context(cfg: Config, project: str) -> str:
    """Contenido de proyectos/<project>/contexto.md (con caché corta)."""
    if not configured(cfg):
        return ""
    path = f"{PROJECTS}/{project}/contexto.md"
    now = time.monotonic()
    cached = _PROJECT_CACHE.get(path)
    if cached and now - cached[0] < _ttl():
        return cached[1]
    try:
        tree = _get(cfg, "/api/tree")
        note = _find_note(tree, path)
        if note is None:
            return ""
        data = _get(cfg, f"/api/notes/{note['id']}")
        content = str(data.get("content") or "")
        _PROJECT_CACHE[path] = (now, content)
        return content
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude leer %s: %s", path, exc)
        return cached[1] if cached else ""


def _append_note(
    cfg: Config, folder: str, slug: str, title: str, block: str
) -> bool:
    path = f"{folder}/{slug}.md"
    try:
        tree = _get(cfg, "/api/tree")
        note = _find_note(tree, path)
        if note is None:
            _request(
                cfg,
                "POST",
                "/api/notes",
                {"folder": folder, "title": title, "content": block},
            )
            return True
        data = _get(cfg, f"/api/notes/{note['id']}")
        content = str(data.get("content") or "").rstrip()
        _request(
            cfg,
            "PUT",
            f"/api/notes/{note['id']}",
            {"content": f"{content}\n\n{block}\n" if content else f"{block}\n"},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude actualizar %s: %s", path, exc)
        return False


def conversation_tail(cfg: Config, project: str, limit: int = 4000) -> str:
    """Último tramo de `conversacion.md`, para dar contexto a una sesión nueva."""
    if limit <= 0 or not configured(cfg):
        return ""
    path = f"{PROJECTS}/{project}/conversacion.md"
    try:
        tree = _get(cfg, "/api/tree")
        note = _find_note(tree, path)
        if note is None:
            return ""
        data = _get(cfg, f"/api/notes/{note['id']}")
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude leer %s: %s", path, exc)
        return ""
    content = str(data.get("content") or "").strip()
    if not content:
        return ""
    if len(content) <= limit:
        return content
    tail = content[-limit:]
    # Empezar en un bloque completo si se puede; si no, en la primera línea.
    block = tail.find("\n### ")
    if block != -1:
        tail = tail[block + 1 :]
    else:
        line = tail.find("\n")
        if line != -1:
            tail = tail[line + 1 :]
    return tail.strip()


def save_conversation(cfg: Config, project: str, user_text: str, reply: str) -> bool:
    """Añade el intercambio a proyectos/<project>/conversacion.md."""
    if not configured(cfg):
        return False
    stamp = time.strftime("%Y-%m-%d %H:%M")
    block = (
        f"### {stamp}\n\n"
        f"**Usuario:** {(user_text or '').strip()[:4000]}\n\n"
        f"**Iris:** {(reply or '').strip()[:4000]}\n"
    )
    return _append_note(
        cfg, f"{PROJECTS}/{project}", "conversacion", "Conversación", block
    )


def list_reminders(cfg: Config) -> list[dict]:
    """Recordatorios pendientes (no completados)."""
    if not configured(cfg):
        return []
    try:
        data = _get(cfg, "/api/reminders?done=false")
        return data.get("reminders") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude leer los recordatorios de Notas: %s", exc)
        return []


def set_reminder_done(cfg: Config, reminder_id: str) -> bool:
    if not configured(cfg):
        return False
    try:
        _request(cfg, "PATCH", f"/api/reminders/{reminder_id}", {"done": True})
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude completar el recordatorio %s: %s", reminder_id, exc)
        return False


def delete_project(cfg: Config, name: str) -> bool:
    """Borra proyectos/<name> en Notas (sin tocar el código local)."""
    if not configured(cfg):
        return False
    query = urllib.parse.urlencode(
        {"path": f"{PROJECTS}/{name}", "recursive": "true"}
    )
    try:
        _request(cfg, "DELETE", f"/api/folders?{query}")
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        log.warning("no pude borrar el proyecto %s en Notas: HTTP %s", name, exc.code)
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude borrar el proyecto %s en Notas: %s", name, exc)
        return False
