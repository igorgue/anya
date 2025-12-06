"""SQLite database storage for Anya conversations and messages.

Uses plain sqlite3 from stdlib. Database stored at:
$XDG_DATA_HOME/anya/conversations.db or ~/.local/share/anya/conversations.db
"""

import os
import pathlib
import sqlite3
from datetime import datetime
from typing import Any

from . import markers
from .history import MessageRecord, strip_blockquote

data_dir = os.environ.get("XDG_DATA_HOME")


def get_db_path() -> pathlib.Path:
    """Return the database file path."""
    if data_dir:
        return pathlib.Path(data_dir) / "anya" / "conversations.db"
    else:
        return pathlib.Path.home() / ".local" / "share" / "anya" / "conversations.db"


def get_connection() -> sqlite3.Connection:
    """Get a sqlite3 connection with row_factory set."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                author TEXT,
                model TEXT,
                created_at TEXT NOT NULL,
                ended_at TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
        """)
        conn.commit()
    finally:
        conn.close()


def save_conversation(id: str, timestamp: str) -> bool:
    """Insert a new conversation."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, NULL, ?, ?)",
            (id, timestamp, timestamp),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def update_conversation_title(id: str, title: str) -> bool:
    """Set the title for a conversation."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title, id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_conversation_timestamp(id: str, timestamp: str) -> bool:
    """Update the updated_at timestamp for a conversation."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (timestamp, id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_conversation(id: str) -> dict[str, Any] | None:
    """Get a single conversation by ID."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def list_conversations(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """List recent conversations ordered by updated_at descending."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def delete_conversation(id: str) -> bool:
    """Delete a conversation and cascade delete its messages."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def save_message(record: MessageRecord) -> bool:
    """Insert a message from a MessageRecord."""
    conn = get_connection()
    try:
        content = record.content
        if record.role == "user":
            content = strip_blockquote(content)

        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, author, model, created_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.conversation_id,
                record.role,
                content,
                record.author,
                record.model,
                record.timestamp,
                record.end_timestamp,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def save_message_dict(
    msg_id: str,
    conversation_id: str,
    role: str,
    content: str,
    author: str | None = None,
    model: str | None = None,
    created_at: str | None = None,
    ended_at: str | None = None,
) -> bool:
    """Insert a message from individual fields."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, author, model, created_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id,
                conversation_id,
                role,
                content,
                author,
                model,
                created_at,
                ended_at,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_messages(conversation_id: str) -> list[dict[str, Any]]:
    """Get all messages for a conversation ordered by created_at."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT id, conversation_id, role, content, author, model, created_at, ended_at
               FROM messages WHERE conversation_id = ? ORDER BY created_at ASC""",
            (conversation_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_message(id: str) -> dict[str, Any] | None:
    """Get a single message by ID."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT id, conversation_id, role, content, author, model, created_at, ended_at
               FROM messages WHERE id = ?""",
            (id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def load_conversation(id: str) -> dict[str, Any] | None:
    """Load a full conversation with all its messages."""
    conversation = get_conversation(id)
    if not conversation:
        return None

    messages = get_messages(id)
    return {"conversation": conversation, "messages": messages}


def rebuild_buffer_content(
    conversation: dict[str, Any], messages: list[dict[str, Any]]
) -> str:
    """Rebuild buffer content from a conversation and its messages.

    Args:
        conversation: Dict with id, title, created_at, updated_at
        messages: List of message dicts

    Returns:
        Buffer content string with all markers
    """
    lines: list[str] = []
    lines.append(
        markers.make_conversation_marker(conversation["id"], conversation["created_at"])
    )

    for msg in messages:
        if msg["role"] == "user":
            lines.append(f"# {msg['author'] or 'User'}")
            lines.append(
                markers.make_user_message_start(
                    msg["id"], msg["author"] or "User", msg["created_at"]
                )
            )
            for line in msg["content"].split("\n"):
                lines.append(f"> {line}")
            lines.append(
                markers.make_message_end(
                    msg["id"], msg["ended_at"] or msg["created_at"]
                )
            )
        else:
            lines.append("# Anya")
            lines.append(
                markers.make_agent_message_start(
                    msg["id"],
                    msg["author"] or "code",
                    msg["model"] or "unknown",
                    msg["created_at"],
                )
            )
            lines.append(msg["content"])
            if msg["ended_at"]:
                lines.append(markers.make_message_end(msg["id"], msg["ended_at"]))

    return "\n".join(lines)
