"""Ejecutor de Claude Code en modo headless."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import Config

log = logging.getLogger("iris.claude")


@dataclass
class ClaudeResult:
    ok: bool
    text: str
    session_id: Optional[str] = None
    error: Optional[str] = None
    cancelled: bool = False
    duration: float = 0.0


def build_command(
    cfg: Config,
    prompt: str,
    model: str,
    session_id: str,
    is_new: bool,
    memory: str = "",
) -> list[str]:
    cmd = [
        cfg.claude_bin,
        "-p",
        prompt,
        "--output-format",
        "json",
    ]
    if model:
        cmd += ["--model", model]
    if cfg.permission_mode == "bypassPermissions":
        cmd.append("--dangerously-skip-permissions")
    elif cfg.permission_mode in {"acceptEdits", "plan", "default"}:
        cmd += ["--permission-mode", cfg.permission_mode]

    if is_new:
        cmd += ["--session-id", session_id]
    else:
        cmd += ["--resume", session_id]

    system_parts = []
    if cfg.system_prompt:
        system_parts.append(cfg.system_prompt)
    if memory.strip():
        system_parts.append(
            "Memoria compartida (Notas, nota configuracion.md):\n\n"
            + memory.strip()
        )
    if system_parts:
        cmd += ["--append-system-prompt", "\n\n".join(system_parts)]

    return cmd


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:  # noqa: BLE001
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def run(
    cfg: Config,
    prompt: str,
    project_dir: Path,
    model: str,
    session_id: str,
    is_new: bool,
    on_start: Optional[Callable[[subprocess.Popen], None]] = None,
    memory: str = "",
) -> ClaudeResult:
    started = time.time()
    cmd = build_command(cfg, prompt, model, session_id, is_new, memory=memory)
    env = os.environ.copy()
    env.pop("ANTHROPIC_MODEL", None)
    if model:
        env["ANTHROPIC_MODEL"] = model
    env.setdefault("HOME", "/home/iris")
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")

    log.info(
        "lanzando claude (%s, sesión %s)",
        model or "modelo predeterminado",
        "nueva" if is_new else session_id[:8],
    )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        return ClaudeResult(ok=False, text="", error=f"No pude lanzar Claude Code: {exc}")

    if on_start:
        on_start(proc)

    try:
        stdout, stderr = proc.communicate(timeout=cfg.timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return ClaudeResult(
            ok=False,
            text="",
            error=f"La tarea superó el límite de {cfg.timeout // 60} minutos.",
            duration=time.time() - started,
        )

    duration = time.time() - started
    if proc.returncode is not None and proc.returncode < 0:
        return ClaudeResult(
            ok=False,
            text="",
            error="Tarea detenida.",
            cancelled=True,
            duration=duration,
        )

    result, parse_error = _parse_output(stdout)
    if result is None:
        detail = (stderr or stdout or "").strip()
        return ClaudeResult(
            ok=False,
            text="",
            error=parse_error or (detail[-800:] if detail else "Claude no devolvió respuesta."),
            duration=duration,
        )

    text = str(result.get("result") or "").strip()
    is_error = bool(result.get("is_error")) or result.get("subtype") == "error"
    session = result.get("session_id") or session_id

    if is_error:
        return ClaudeResult(
            ok=False,
            text=text,
            session_id=session,
            error=text or "Claude devolvió un error.",
            duration=duration,
        )

    return ClaudeResult(
        ok=True,
        text=text or "Listo.",
        session_id=session,
        duration=duration,
    )


def _parse_output(stdout: str) -> tuple[Optional[dict], Optional[str]]:
    raw = (stdout or "").strip()
    if not raw:
        return None, "Claude no devolvió salida."
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data, None
    except json.JSONDecodeError:
        pass
    # Algunos errores llegan como texto plano.
    return None, raw[-800:]
