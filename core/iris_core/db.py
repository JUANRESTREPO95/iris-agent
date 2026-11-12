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
    kind        TEXT,
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
    created_at REAL NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS reminders_notified (
    id          TEXT PRIMARY KEY,
    due_at      TEXT,
    notified_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reminder_pending (
    chat_id     TEXT PRIMARY KEY,
    reminder_id TEXT NOT NULL,
    state       TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_state (
    account     TEXT NOT NULL,
    folder      TEXT NOT NULL,
    uidvalidity TEXT,
    last_uid    INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (account, folder)
);

CREATE TABLE IF NOT EXISTS mail_notified (
    message_id  TEXT PRIMARY KEY,
    notified_at REAL NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
            if "kind" not in columns:
                conn.execute("ALTER TABLE messages ADD COLUMN kind TEXT")
            session_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(sessions)")
            }
            if "created_at" not in session_columns:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN created_at REAL NOT NULL DEFAULT 0"
                )
                conn.execute(
                    "UPDATE sessions SET created_at = updated_at WHERE created_at = 0"
                )
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
                    (wa_id, chat_id, sender_id, text, media_path, media_kind, kind,
                     status, attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'nuevo', 0, ?, ?)
                """,
                (
                    payload.get("id"),
                    payload.get("chatId"),
                    payload.get("senderId"),
                    payload.get("text") or "",
                    (payload.get("media") or {}).get("path"),
                    (payload.get("media") or {}).get("kind"),
                    payload.get("kind"),
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
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sessions (chat_id, project, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, project) DO UPDATE SET
                    session_id = excluded.session_id,
                    updated_at = excluded.updated_at
                """,
                (chat_id, project, session_id, now, now),
            )

    def session_created_at(self, chat_id: str, project: str) -> Optional[float]:
        """Momento en que nació la sesión actual (0/None si no hay)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT created_at FROM sessions WHERE chat_id = ? AND project = ?",
                (chat_id, project),
            ).fetchone()
        if not row:
            return None
        return float(row["created_at"] or 0) or None

    def clear_session(self, chat_id: str, project: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE chat_id = ? AND project = ?", (chat_id, project)
            )

    def reset_project(self, project: str) -> None:
        """Devuelve a 'general' los chats de un proyecto y borra sus sesiones."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE chats SET project = 'general', updated_at = ? WHERE project = ?",
                (time.time(), project),
            )
            conn.execute("DELETE FROM sessions WHERE project = ?", (project,))

    def last_chat(self, channel: str = "whatsapp") -> Optional[str]:
        """Chat directo más reciente del canal, para avisos proactivos.

        En WhatsApp el id puede ser un LID; es el mismo que usa el usuario al
        responder, así que sirve para guardar el estado pendiente.
        """
        with self._conn() as conn:
            if str(channel).lower() == "telegram":
                row = conn.execute(
                    "SELECT chat_id FROM chats WHERE chat_id LIKE 'tg:%' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT chat_id FROM chats WHERE chat_id NOT LIKE 'tg:%' "
                    "AND chat_id NOT LIKE '%@g.us' "
                    "AND chat_id NOT LIKE '%@broadcast' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        return row["chat_id"] if row else None

    # ── recordatorios ───────────────────────────────────────────────────────

    def was_notified(self, reminder_id: str, due_at: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT due_at FROM reminders_notified WHERE id = ?", (reminder_id,)
            ).fetchone()
        return bool(row) and str(row["due_at"] or "") == str(due_at or "")

    def mark_notified(self, reminder_id: str, due_at: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO reminders_notified (id, due_at, notified_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    due_at = excluded.due_at, notified_at = excluded.notified_at
                """,
                (reminder_id, due_at, time.time()),
            )

    def set_pending(self, chat_id: str, reminder_id: str, state: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO reminder_pending (chat_id, reminder_id, state, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    reminder_id = excluded.reminder_id,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (chat_id, reminder_id, state, time.time()),
            )

    def get_pending(self, chat_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM reminder_pending WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return dict(row) if row else None

    def clear_pending(self, chat_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM reminder_pending WHERE chat_id = ?", (chat_id,))

    # ── correo ──────────────────────────────────────────────────────────────

    def get_mail_state(self, account: str, folder: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM mail_state WHERE account = ? AND folder = ?",
                (account, folder),
            ).fetchone()
        return dict(row) if row else None

    def set_mail_state(
        self, account: str, folder: str, uidvalidity: str, last_uid: int
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO mail_state (account, folder, uidvalidity, last_uid, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account, folder) DO UPDATE SET
                    uidvalidity = excluded.uidvalidity,
                    last_uid = excluded.last_uid,
                    updated_at = excluded.updated_at
                """,
                (account, folder, str(uidvalidity or ""), int(last_uid), time.time()),
            )

    def was_mail_notified(self, message_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM mail_notified WHERE message_id = ?", (message_id,)
            ).fetchone()
        return bool(row)

    def mark_mail_notified(self, message_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO mail_notified (message_id, notified_at) VALUES (?, ?)",
                (message_id, time.time()),
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
