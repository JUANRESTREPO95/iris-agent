"""Cola persistente y estado de Iris (SQLite en WAL)."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wa_id       TEXT UNIQUE,
    chat_id     TEXT NOT NULL,
    sender_id   TEXT,
    text        TEXT,
    media_path  TEXT,
    media_kind  TEXT,
    status      TEXT NOT NULL DEFAULT 'nuevo',
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status, id);

CREATE TABLE IF NOT EXISTS chats (
    chat_id    TEXT PRIMARY KEY,
    project    TEXT NOT NULL DEFAULT 'general',
    model      TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    chat_id    TEXT NOT NULL,
    project    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (chat_id, project)
);

CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    chat_id TEXT,
    event   TEXT NOT NULL,
    detail  TEXT
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            # Si el proceso murió a mitad de una tarea, ese mensaje vuelve a la cola.
            conn.execute(
                "UPDATE messages SET status = 'nuevo' WHERE status = 'procesando'"
            )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
        finally:
            conn.close()

    # ── mensajes ────────────────────────────────────────────────────────────

    def enqueue(self, payload: dict[str, Any]) -> bool:
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (wa_id, chat_id, sender_id, text, media_path, media_kind,
                     status, attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'nuevo', 0, ?, ?)
                """,
                (
                    payload.get("id"),
                    payload.get("chatId"),
                    payload.get("senderId"),
                    payload.get("text") or "",
                    (payload.get("media") or {}).get("path"),
                    (payload.get("media") or {}).get("kind"),
                    now,
                    now,
                ),
            )
            return cur.rowcount > 0

    def claim_next(self) -> Optional[sqlite3.Row]:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM messages WHERE status = 'nuevo' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE messages SET status = 'procesando', attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
            conn.execute("COMMIT")
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (row["id"],)).fetchone()
            return row

    def mark(self, message_id: int, status: str, error: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE messages SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, error, time.time(), message_id),
            )

    def queue_size(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE status IN ('nuevo', 'procesando')"
            ).fetchone()
            return int(row["n"])

    # ── chats y sesiones ────────────────────────────────────────────────────

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO chats (chat_id, project, model, updated_at) VALUES (?, 'general', NULL, ?)",
                    (chat_id, time.time()),
                )
                return {"chat_id": chat_id, "project": "general", "model": None}
            return dict(row)

    def set_project(self, chat_id: str, project: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO chats (chat_id, project, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET project = excluded.project, updated_at = excluded.updated_at
                """,
                (chat_id, project, time.time()),
            )

    def set_model(self, chat_id: str, model: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO chats (chat_id, project, model, updated_at) VALUES (?, 'general', ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET model = excluded.model, updated_at = excluded.updated_at
                """,
                (chat_id, model, time.time()),
            )

    def get_session(self, chat_id: str, project: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT session_id FROM sessions WHERE chat_id = ? AND project = ?",
                (chat_id, project),
            ).fetchone()
            return row["session_id"] if row else None

    def set_session(self, chat_id: str, project: str, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sessions (chat_id, project, session_id, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, project) DO UPDATE SET session_id = excluded.session_id, updated_at = excluded.updated_at
                """,
                (chat_id, project, session_id, time.time()),
            )

    def clear_session(self, chat_id: str, project: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE chat_id = ? AND project = ?", (chat_id, project)
            )

    # ── auditoría ───────────────────────────────────────────────────────────

    def audit(self, event: str, detail: str = "", chat_id: str | None = None) -> None:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO audit (ts, chat_id, event, detail) VALUES (?, ?, ?, ?)",
                    (time.time(), chat_id, event, detail[:4000]),
                )
        except sqlite3.Error:
            pass
