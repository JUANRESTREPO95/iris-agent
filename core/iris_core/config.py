"""Configuración del núcleo de Iris (todo por variables de entorno)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = (
    "Eres Iris, un agente personal que trabaja por Telegram (o WhatsApp). "
    "Hablas español, de forma breve y directa. "
    "Tienes acceso a un directorio de trabajo; úsalo para crear y modificar archivos. "
    "Cuando una tarea sea larga, resume el resultado al final en pocas líneas. "
    "No inventes resultados: si un comando falla, dilo con el error. "
    "No compartas secretos ni tokens en tus respuestas. "
    "Si el usuario te pide un archivo, guárdalo y añade en tu respuesta el marcador "
    "[[ADJUNTO:/ruta/absoluta]] (con ruta absoluta dentro del workspace). "
    "Iris enviará ese documento y luego el texto restante; no inventes el marcador "
    "si no hay un archivo real."
)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _secret(name: str) -> str:
    """Lee un secreto de ~/.config/iris/<name>, pensado para permisos 600."""
    base = Path(_env("IRIS_CONFIG_DIR", str(Path.home() / ".config" / "iris")))
    try:
        return (base / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _hours(name: str, default: float) -> float:
    """Horas como número positivo; 0 desactiva la función."""
    try:
        return max(0.0, float(_env(name, str(default)) or default))
    except ValueError:
        return default


def _count(name: str, default: int) -> int:
    """Entero no negativo; 0 desactiva la función."""
    try:
        return max(0, int(_env(name, str(default)) or default))
    except ValueError:
        return default


def _notas_urls() -> tuple[str, ...]:
    """URLs de Notas en orden de preferencia.

    Se leen de `NOTAS_API_URLS` (varias separadas por comas o espacios) o, si
    no existe, de `NOTAS_API_URL`. Ejemplo:

        NOTAS_API_URLS=https://txt.midominio.com,http://host.docker.internal:3001
    """
    raw = _env("NOTAS_API_URLS") or _env("NOTAS_API_URL")
    urls: list[str] = []
    for chunk in re.split(r"[,\s]+", raw):
        url = chunk.strip().rstrip("/")
        if url and url not in urls:
            urls.append(url)
    return tuple(urls)


@dataclass(frozen=True)
class Config:
    phone: str
    channels: str
    telegram_user: str
    telegram_bridge_url: str
    provider: str
    model: str
    workspace: Path
    data_dir: Path
    outbox: Path
    max_file_mb: int
    bridge_url: str
    token: str
    bind: str
    port: int
    claude_bin: str
    timeout: int
    permission_mode: str
    system_prompt: str
    max_chars: int
    notas_url: str
    notas_urls: tuple[str, ...]
    notas_token: str
    memory_mode: str
    reminders: bool
    reminder_interval: int
    reminder_channel: str
    timezone: str
    mail: bool
    mail_user: str
    mail_password: str
    mail_interval: int
    mail_channel: str
    mail_folder: str
    mail_quiet: str
    mail_max_per_tick: int
    session_max_hours: float
    session_tail_chars: int
    autosave_hours: float
    transcribe: bool
    transcribe_model: str
    transcribe_lang: str


def load() -> Config:
    notas_urls = _notas_urls()
    data_dir = Path(_env("IRIS_DATA", "/var/lib/iris")).expanduser()
    outbox = Path(_env("IRIS_OUTBOX", str(data_dir / "outbox"))).expanduser()
    return Config(
        phone=_env("IRIS_PHONE"),
        channels=_env("IRIS_CHANNELS", "telegram"),
        telegram_user=_env("IRIS_TELEGRAM_USER"),
        telegram_bridge_url=_env(
            "IRIS_TELEGRAM_BRIDGE_URL", "http://127.0.0.1:3005"
        ).rstrip("/"),
        provider=_env("IRIS_PROVIDER", "ollama-local"),
        model=_env("IRIS_MODEL"),
        workspace=Path(_env("IRIS_WORKSPACE", "/home/iris/workspace")).expanduser(),
        data_dir=data_dir,
        outbox=outbox,
        max_file_mb=max(1, int(_env("IRIS_MAX_FILE_MB", "100") or 100)),
        bridge_url=_env("IRIS_BRIDGE_URL", "http://127.0.0.1:3003").rstrip("/"),
        token=_env("IRIS_BRIDGE_TOKEN"),
        bind=_env("IRIS_CORE_BIND", "127.0.0.1"),
        port=int(_env("IRIS_CORE_PORT", "3004")),
        claude_bin=_env("IRIS_CLAUDE_BIN", "/usr/local/bin/claude"),
        timeout=int(_env("IRIS_CLAUDE_TIMEOUT", "1800")),
        permission_mode=_env("IRIS_PERMISSION_MODE", "bypassPermissions"),
        system_prompt=_env("IRIS_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        max_chars=int(_env("IRIS_MAX_CHARS", "4000")),
        notas_url=notas_urls[0] if notas_urls else "",
        notas_urls=notas_urls,
        notas_token=_env("NOTAS_API_TOKEN"),
        memory_mode=_env("IRIS_MEMORY_MODE", "manual").lower(),
        reminders=_env("IRIS_REMINDERS", "off").lower() in {"1", "on", "true", "yes", "si"},
        reminder_interval=max(10, int(_env("IRIS_REMINDER_INTERVAL", "60") or 60)),
        reminder_channel=_env("IRIS_REMINDER_CHANNEL", "auto").lower(),
        timezone=_env("IRIS_TIMEZONE", "America/Bogota"),
        mail=_env("IRIS_MAIL", "off").lower() in {"1", "on", "true", "yes", "si"},
        mail_user=_env("IRIS_MAIL_USER"),
        mail_password=_env("IRIS_MAIL_APP_PASSWORD") or _secret("mail-app-password"),
        mail_interval=max(30, int(_env("IRIS_MAIL_INTERVAL", "60") or 60)),
        mail_channel=_env("IRIS_MAIL_CHANNEL", "auto").lower(),
        mail_folder=_env("IRIS_MAIL_FOLDER", "INBOX"),
        mail_quiet=_env("IRIS_MAIL_QUIET"),
        mail_max_per_tick=max(1, int(_env("IRIS_MAIL_MAX_PER_TICK", "5") or 5)),
        session_max_hours=_hours("IRIS_SESSION_MAX_HOURS", 12.0),
        session_tail_chars=_count("IRIS_SESSION_TAIL_CHARS", 4000),
        autosave_hours=_hours("IRIS_AUTOSAVE_HOURS", 6.0),
        transcribe=_env("IRIS_TRANSCRIBE", "off").lower() in {"1", "on", "true", "yes", "si"},
        transcribe_model=_env("IRIS_TRANSCRIBE_MODEL", "base").strip() or "base",
        transcribe_lang=_env("IRIS_TRANSCRIBE_LANG", "es").strip(),
    )
