"""SQLite database for the Telegram Router.

Stores:
- Client registrations (client_id -> public_key, metadata)
- Pairings (telegram_chat_id -> client_id)
- Offline message queue (messages for disconnected daemons)
"""

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ClientState:
    """State of a registered daemon client."""

    client_id: str
    public_key: bytes
    display_name: str
    created_at: float
    last_seen_at: float
    is_connected: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class Pairing:
    """A pairing between a Telegram chat and a daemon client."""

    chat_id: int
    client_id: str
    paired_at: float
    active: bool = True


@dataclass
class QueuedMessage:
    """A message queued for delivery to an offline daemon."""

    id: int
    client_id: str
    chat_id: int
    text: str
    queued_at: float
    delivered: bool = False


def get_db_path() -> Path:
    """Get the database path for the router."""
    data_dir = os.environ.get("XDG_DATA_HOME")
    if data_dir:
        base = Path(data_dir)
    else:
        base = Path.home() / ".local" / "share"
    return base / "anya" / "router.db"


class RouterDB:
    """SQLite database manager for the router."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else get_db_path()
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        """Get or create the database connection."""
        if self._conn is not None:
            return self._conn

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        self._conn = conn
        self._init_tables()
        return conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _init_tables(self):
        conn = self.connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS clients (
                client_id TEXT PRIMARY KEY,
                public_key BLOB NOT NULL,
                display_name TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS pairings (
                chat_id INTEGER NOT NULL,
                client_id TEXT NOT NULL,
                paired_at REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (chat_id, client_id),
                FOREIGN KEY (client_id) REFERENCES clients(client_id)
            );

            CREATE TABLE IF NOT EXISTS queued_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                queued_at REAL NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (client_id) REFERENCES clients(client_id)
            );

            CREATE INDEX IF NOT EXISTS idx_queued_client
                ON queued_messages(client_id, delivered);
            CREATE INDEX IF NOT EXISTS idx_pairings_chat
                ON pairings(chat_id);
        """)

    # --- Client operations ---

    def register_client(
        self,
        client_id: str,
        public_key: bytes,
        display_name: str = "",
        metadata: dict | None = None,
    ) -> ClientState:
        """Register (or update) a daemon client.

        Preserves the original created_at if the client already exists.
        """
        conn = self.connect()
        now = time.time()

        # Check if client already exists to preserve created_at
        existing = self.get_client(client_id)
        created_at = existing.created_at if existing else now

        conn.execute(
            """INSERT OR REPLACE INTO clients
               (client_id, public_key, display_name, created_at, last_seen_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                client_id,
                public_key,
                display_name or client_id[:12],
                created_at,
                now,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        return ClientState(
            client_id=client_id,
            public_key=public_key,
            display_name=display_name or client_id[:12],
            created_at=created_at,
            last_seen_at=now,
        )

    def get_client(self, client_id: str) -> ClientState | None:
        """Get a client by ID."""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        if not row:
            return None
        return ClientState(
            client_id=row["client_id"],
            public_key=row["public_key"],
            display_name=row["display_name"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            metadata=json.loads(row["metadata"]),
        )

    def touch_client(self, client_id: str):
        """Update last_seen_at for a client."""
        conn = self.connect()
        conn.execute(
            "UPDATE clients SET last_seen_at = ? WHERE client_id = ?",
            (time.time(), client_id),
        )
        conn.commit()

    # --- Pairing operations ---

    def create_pairing(self, chat_id: int, client_id: str) -> Pairing:
        """Pair a Telegram chat with a daemon client."""
        conn = self.connect()
        now = time.time()
        conn.execute(
            """INSERT OR REPLACE INTO pairings
               (chat_id, client_id, paired_at, active)
               VALUES (?, ?, ?, 1)""",
            (chat_id, client_id, now),
        )
        conn.commit()
        return Pairing(
            chat_id=chat_id,
            client_id=client_id,
            paired_at=now,
        )

    def get_pairing(self, chat_id: int) -> Pairing | None:
        """Get the active pairing for a Telegram chat."""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM pairings WHERE chat_id = ? AND active = 1",
            (chat_id,),
        ).fetchone()
        if not row:
            return None
        return Pairing(
            chat_id=row["chat_id"],
            client_id=row["client_id"],
            paired_at=row["paired_at"],
            active=bool(row["active"]),
        )

    def get_chat_ids_for_client(self, client_id: str) -> list[int]:
        """Get all Telegram chat IDs paired to a client."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT chat_id FROM pairings WHERE client_id = ? AND active = 1",
            (client_id,),
        ).fetchall()
        return [row["chat_id"] for row in rows]

    def remove_pairing(self, chat_id: int, client_id: str):
        """Deactivate a pairing."""
        conn = self.connect()
        conn.execute(
            "UPDATE pairings SET active = 0 WHERE chat_id = ? AND client_id = ?",
            (chat_id, client_id),
        )
        conn.commit()

    # --- Message queue operations ---

    def queue_message(
        self, client_id: str, chat_id: int, text: str
    ) -> QueuedMessage:
        """Queue a message for an offline daemon."""
        conn = self.connect()
        now = time.time()
        cursor = conn.execute(
            """INSERT INTO queued_messages
               (client_id, chat_id, text, queued_at)
               VALUES (?, ?, ?, ?)""",
            (client_id, chat_id, text, now),
        )
        conn.commit()
        return QueuedMessage(
            id=cursor.lastrowid,
            client_id=client_id,
            chat_id=chat_id,
            text=text,
            queued_at=now,
        )

    def get_pending_messages(self, client_id: str, limit: int = 50) -> list[QueuedMessage]:
        """Get undelivered messages for a client (oldest first)."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT * FROM queued_messages
               WHERE client_id = ? AND delivered = 0
               ORDER BY queued_at ASC LIMIT ?""",
            (client_id, limit),
        ).fetchall()
        return [
            QueuedMessage(
                id=row["id"],
                client_id=row["client_id"],
                chat_id=row["chat_id"],
                text=row["text"],
                queued_at=row["queued_at"],
            )
            for row in rows
        ]

    def mark_delivered(self, message_ids: list[int]):
        """Mark messages as delivered."""
        if not message_ids:
            return
        conn = self.connect()
        placeholders = ",".join("?" for _ in message_ids)
        conn.execute(
            f"UPDATE queued_messages SET delivered = 1 WHERE id IN ({placeholders})",
            message_ids,
        )
        conn.commit()

    def cleanup_old_messages(self, max_age_hours: int = 24):
        """Delete messages older than max_age_hours."""
        conn = self.connect()
        cutoff = time.time() - (max_age_hours * 3600)
        conn.execute(
            "DELETE FROM queued_messages WHERE queued_at < ?", (cutoff,)
        )
        conn.commit()
