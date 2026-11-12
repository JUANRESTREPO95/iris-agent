"""Cliente del puente de WhatsApp (HTTP local con token)."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config

log = logging.getLogger("iris.bridge")

# Extensiones que se pueden enviar como documento. Deliberadamente acotada:
# el agente no debe poder reenviar binarios, claves ni bases de datos.
ALLOWED_EXTENSIONS = frozenset(
    {
        "pdf", "txt", "md", "markdown", "csv", "json", "xml", "html", "htm",
        "rtf", "epub", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt",
        "ods", "odp", "png", "jpg", "jpeg", "webp", "gif", "bmp", "svg",
        "heic", "heif", "mp3", "m4a", "ogg", "opus", "wav", "aac", "flac",
        "mp4", "mov", "m4v", "webm", "mkv", "3gp", "zip", "tar", "gz",
        "tgz", "7z", "rar", "py", "js", "mjs", "cjs", "ts", "tsx", "jsx",
        "sh", "sql", "yaml", "yml", "toml", "ini", "cfg", "ipynb",
    }
)
SENSITIVE_PARTS = frozenset({".ssh", ".aws", ".gnupg", ".git", ".config"})
SENSITIVE_NAMES = frozenset(
    {".env", "creds.json", "id_rsa", "id_ed25519", "token", "tokens"}
)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".token", ".secrets")

ATTACHMENT_RE = re.compile(r"\[\[\s*ADJUNTO\s*:\s*([^\]]+?)\s*\]\]", re.I)


def extract_attachments(text: str) -> tuple[str, list[str]]:
    """Separa los marcadores [[ADJUNTO:/ruta]] del texto de respuesta."""
    paths: list[str] = []

    def take(match: re.Match) -> str:
        value = match.group(1).strip().strip("\"'")
        if value:
            paths.append(value)
        return ""

    clean = ATTACHMENT_RE.sub(take, str(text or ""))
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip(), paths


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_sensitive(relative: Path) -> None:
    for part in relative.parts:
        low = part.lower()
        if (
            low in SENSITIVE_PARTS
            or low in SENSITIVE_NAMES
            or low.startswith(".env")
            or low.endswith(SENSITIVE_SUFFIXES)
        ):
            raise ValueError("ruta sensible no permitida")


def _safe_name(raw: str) -> str:
    name = Path(str(raw or "")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    name = name.lstrip(".")
    if len(name) > 80:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 79 - len(ext)] if dot else name[:80]) + (dot + ext if dot else "")
    return name or "adjunto"


def prepare_attachment(cfg: Config, raw: str) -> tuple[Path, bool]:
    """Valida una ruta y la deja visible para los puentes.

    Devuelve (ruta dentro del outbox, se_copió). Lanza ValueError con un
    mensaje claro si la ruta no es válida, no existe o supera el límite.
    """
    value = str(raw or "").strip()
    if not value:
        raise ValueError("ruta vacía")

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cfg.workspace) / candidate
    candidate = Path(os.path.abspath(str(candidate)))

    workspace = Path(os.path.abspath(str(cfg.workspace)))
    outbox = Path(os.path.abspath(str(cfg.outbox)))
    root = next(
        (base for base in (workspace, outbox) if _is_inside(candidate, base)), None
    )
    if root is None:
        raise ValueError("ruta fuera del workspace/outbox")

    relative = candidate.relative_to(root)
    if not relative.parts:
        raise ValueError("ruta no permitida")
    _reject_sensitive(relative)

    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("no se permiten symlinks")

    if not candidate.exists():
        raise ValueError("el archivo no existe")
    if not candidate.is_file():
        raise ValueError("no es un archivo")
    real = candidate.resolve()
    if not _is_inside(real, root.resolve()):
        raise ValueError("ruta fuera del workspace/outbox")

    extension = real.suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(f"extensión no permitida: .{extension or '?'}")

    size = real.stat().st_size
    limit = cfg.max_file_mb * 1024 * 1024
    if size > limit:
        raise ValueError(f"archivo demasiado grande (máximo {cfg.max_file_mb} MB)")

    outbox_real = outbox.resolve()
    if _is_inside(real, outbox_real):
        return real, False

    outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = outbox / f"{int(time.time())}-{secrets.token_hex(4)}-{_safe_name(real.name)}"
    try:
        shutil.copyfile(real, destination)
        os.chmod(destination, 0o600)
    except OSError as exc:
        raise ValueError(f"no pude preparar el archivo: {exc}") from exc
    return destination, True


def _route(cfg: Config, chat_id: str) -> tuple[str, str]:
    """Devuelve (url del puente, chat_id nativo del canal)."""
    target = str(chat_id)
    if target.startswith("tg:"):
        return cfg.telegram_bridge_url, target[3:]
    return cfg.bridge_url, target


def _post_url(
    cfg: Config, base: str, path: str, payload: dict, timeout: float = 15.0
) -> dict | None:
    url = f"{base}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        log.warning("bridge HTTP %s en %s: %s", exc.code, path, detail)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("bridge no disponible (%s): %s", path, exc)
        return None


def _post(cfg: Config, path: str, payload: dict, timeout: float = 15.0) -> dict | None:
    return _post_url(cfg, cfg.bridge_url, path, payload, timeout)


def chunk_text(text: str, limit: int) -> list[str]:
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
    return [c for c in chunks if c]


def send_text(cfg: Config, chat_id: str, text: str) -> bool:
    parts = chunk_text(text, cfg.max_chars)
    if not parts:
        return True
    base, target = _route(cfg, chat_id)
    ok_all = True
    for i, part in enumerate(parts):
        result = _post_url(
            cfg, base, "/send", {"chatId": target, "text": part}, timeout=30
        )
        if result is None:
            ok_all = False
            break
        if i < len(parts) - 1:
            time.sleep(0.3)
    return ok_all


def send_document(
    cfg: Config, chat_id: str, path: str, caption: str = ""
) -> bool:
    """Copia el archivo al outbox y lo envía por el puente del canal."""
    destination, copied = prepare_attachment(cfg, path)
    base, target = _route(cfg, chat_id)
    payload = {"chatId": target, "path": str(destination)}
    if caption:
        payload["caption"] = str(caption)[:1024]
    try:
        result = _post_url(cfg, base, "/send_document", payload, timeout=120.0)
    finally:
        if copied:
            try:
                destination.unlink()
            except OSError as exc:
                log.warning("no pude borrar el temporal %s: %s", destination, exc)
    return result is not None and bool(result.get("ok", True))


def send_typing(cfg: Config, chat_id: str) -> None:
    base, target = _route(cfg, chat_id)
    _post_url(cfg, base, "/typing", {"chatId": target}, timeout=5)


def _health_url(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def health(cfg: Config) -> dict | None:
    return _health_url(f"{cfg.bridge_url}/health")


def telegram_health(cfg: Config) -> dict | None:
    return _health_url(f"{cfg.telegram_bridge_url}/health")
