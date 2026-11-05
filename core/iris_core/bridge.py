"""Cliente del puente de WhatsApp (HTTP local con token)."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from .config import Config

log = logging.getLogger("iris.bridge")


def _post(cfg: Config, path: str, payload: dict, timeout: float = 15.0) -> dict | None:
    url = f"{cfg.bridge_url}{path}"
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
    ok_all = True
    for i, part in enumerate(parts):
        result = _post(cfg, "/send", {"chatId": chat_id, "text": part}, timeout=30)
        if result is None:
            ok_all = False
            break
        if i < len(parts) - 1:
            time.sleep(0.3)
    return ok_all


def send_typing(cfg: Config, chat_id: str) -> None:
    _post(cfg, "/typing", {"chatId": chat_id}, timeout=5)


def health(cfg: Config) -> dict | None:
    url = f"{cfg.bridge_url}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
