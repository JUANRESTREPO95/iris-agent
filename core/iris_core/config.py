"""Configuración del núcleo de Iris (todo por variables de entorno)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = (
    "Eres Iris, un agente personal que trabaja por WhatsApp. "
    "Hablas español, de forma breve y directa. "
    "Tienes acceso a un directorio de trabajo; úsalo para crear y modificar archivos. "
    "Cuando una tarea sea larga, resume el resultado al final en pocas líneas. "
    "No inventes resultados: si un comando falla, dilo con el error. "
    "No compartas secretos ni tokens en tus respuestas."
)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


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
    bridge_url: str
    token: str
    bind: str
    port: int
    claude_bin: str
    timeout: int
    permission_mode: str
    system_prompt: str
    max_chars: int


def load() -> Config:
    return Config(
        phone=_env("IRIS_PHONE"),
        channels=_env("IRIS_CHANNELS", "whatsapp"),
        telegram_user=_env("IRIS_TELEGRAM_USER"),
        telegram_bridge_url=_env(
            "IRIS_TELEGRAM_BRIDGE_URL", "http://127.0.0.1:3005"
        ).rstrip("/"),
        provider=_env("IRIS_PROVIDER", "ollama-local"),
        model=_env("IRIS_MODEL"),
        workspace=Path(_env("IRIS_WORKSPACE", "/home/iris/workspace")).expanduser(),
        data_dir=Path(_env("IRIS_DATA", "/var/lib/iris")).expanduser(),
        bridge_url=_env("IRIS_BRIDGE_URL", "http://127.0.0.1:3003").rstrip("/"),
        token=_env("IRIS_BRIDGE_TOKEN"),
        bind=_env("IRIS_CORE_BIND", "127.0.0.1"),
        port=int(_env("IRIS_CORE_PORT", "3004")),
        claude_bin=_env("IRIS_CLAUDE_BIN", "/usr/local/bin/claude"),
        timeout=int(_env("IRIS_CLAUDE_TIMEOUT", "1800")),
        permission_mode=_env("IRIS_PERMISSION_MODE", "bypassPermissions"),
        system_prompt=_env("IRIS_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        max_chars=int(_env("IRIS_MAX_CHARS", "4000")),
    )
