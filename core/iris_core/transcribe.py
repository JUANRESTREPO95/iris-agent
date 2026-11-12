"""Transcripción local de audios con faster-whisper (opcional).

Se usa antes de armar el prompt: si llega una nota de voz por Telegram o
WhatsApp y `IRIS_TRANSCRIBE=on`, el audio se convierte a texto para que el
modelo pueda leerlo. Todo es best-effort: si falla, el mensaje sigue su curso.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

log = logging.getLogger("iris.transcribe")

_MODEL = None
_MODEL_NAME = ""
_LOCK = threading.Lock()


def configured(cfg) -> bool:
    return bool(getattr(cfg, "transcribe", False))


def _model(cfg):
    """Carga el modelo una sola vez (CPU, int8) y lo reutiliza."""
    global _MODEL, _MODEL_NAME
    name = str(getattr(cfg, "transcribe_model", "") or "base")
    with _LOCK:
        if _MODEL is not None and _MODEL_NAME == name:
            return _MODEL
        from faster_whisper import WhisperModel  # import perezoso

        log.info("cargando modelo de transcripción %s (cpu/int8)", name)
        _MODEL = WhisperModel(name, device="cpu", compute_type="int8")
        _MODEL_NAME = name
        return _MODEL


def transcribe(cfg, path: str) -> str:
    """Devuelve el texto del audio, o cadena vacía si no se pudo."""
    audio = Path(str(path or ""))
    if not audio.is_file():
        log.warning("audio no encontrado: %s", audio)
        return ""
    try:
        model = _model(cfg)
        language = str(getattr(cfg, "transcribe_lang", "") or "") or None
        segments, _info = model.transcribe(
            str(audio), language=language, vad_filter=True, beam_size=1
        )
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        return " ".join(text.split())
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude transcribir %s: %s", audio, exc)
        return ""
