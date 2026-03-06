"""SQLite database storage for Anya conversations and messages.

Uses plain sqlite3 from stdlib. Database stored at:
$XDG_DATA_HOME/anya/conversations.db or ~/.local/share/anya/conversations.db
"""

import os
import pathlib
import sqlite3
from datetime import datetime, timezone
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
    """Create tables if they don't exist and add new columns if missing."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                cwd TEXT,
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
                markers JSON,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                deduplication_key TEXT,
                conversation_id TEXT,
                message_id TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_memories_dedup ON memories(deduplication_key);

            CREATE TABLE IF NOT EXISTS tool_outputs (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                message_id TEXT,
                tool_name TEXT NOT NULL,
                content TEXT NOT NULL,
                line_count INTEGER,
                filetype TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tool_outputs_conversation ON tool_outputs(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_tool_outputs_message ON tool_outputs(message_id);
        """)
        conn.commit()

        # Migration: Add markers column if it doesn't exist
        cursor = conn.execute("PRAGMA table_info(messages)")
        columns = {row[1] for row in cursor.fetchall()}
        if "markers" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN markers TEXT")
            # Set all existing messages to have empty marker list
            conn.execute("UPDATE messages SET markers = '[]' WHERE markers IS NULL")
            conn.commit()

        # Migration: Add cwd column to conversations if it doesn't exist
        cursor = conn.execute("PRAGMA table_info(conversations)")
        columns = {row[1] for row in cursor.fetchall()}
        if "cwd" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN cwd TEXT")
            conn.commit()
    finally:
        conn.close()


def save_conversation(id: str, timestamp: str, cwd: str | None = None) -> bool:
    """Insert a new conversation."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO conversations (id, title, cwd, created_at, updated_at) VALUES (?, NULL, ?, ?, ?)",
            (id, cwd, timestamp, timestamp),
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
            "SELECT id, title, cwd, created_at, updated_at FROM conversations WHERE id = ?",
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
            "SELECT id, title, cwd, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ? OFFSET ?",
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
    import json

    conn = get_connection()
    try:
        content = record.content
        if record.role == "user":
            content = strip_blockquote(content)

        markers_json = None
        if record.markers:
            markers_json = json.dumps(
                [
                    {"name": m.ids[0] if m.ids else m.type, "pos": m.pos}
                    for m in record.markers
                ]
            )

        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, author, model, created_at, ended_at, markers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.conversation_id,
                record.role,
                content,
                record.author,
                record.model,
                record.timestamp,
                record.end_timestamp,
                markers_json,
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
    markers: str | None = None,
) -> bool:
    """Insert a message from individual fields."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, author, model, created_at, ended_at, markers)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id,
                conversation_id,
                role,
                content,
                author,
                model,
                created_at,
                ended_at,
                markers,
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
            """SELECT id, conversation_id, role, content, author, model, created_at, ended_at, markers
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
            """SELECT id, conversation_id, role, content, author, model, created_at, ended_at, markers
               FROM messages WHERE id = ?""",
            (id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def update_message(
    id: str,
    *,
    content: str | None = None,
    ended_at: str | None = None,
    markers: str | None = None,
) -> bool:
    """Update an existing message's content, end time, or markers."""
    conn = get_connection()
    try:
        fields = []
        params: list[Any] = []
        if content is not None:
            fields.append("content = ?")
            params.append(content)
        if ended_at is not None:
            fields.append("ended_at = ?")
            params.append(ended_at)
        if markers is not None:
            fields.append("markers = ?")
            params.append(markers)

        if not fields:
            return False

        params.append(id)
        cursor = conn.execute(
            f"UPDATE messages SET {', '.join(fields)} WHERE id = ?", params
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def save_memory(memory: dict) -> bool:
    """
    Insert a memory item into the memories table.
    Fields: id, text, category, source, timestamp, deduplication_key, conversation_id, message_id
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO memories (id, text, category, source, timestamp, deduplication_key, conversation_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.get("id"),
                memory.get("text"),
                memory.get("category"),
                memory.get("source"),
                memory.get("timestamp"),
                memory.get("deduplication_key"),
                memory.get("conversation_id"),
                memory.get("message_id"),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def search_memories(
    query: str | None = None, category: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """
    Search memories by text query and/or category.

    Args:
        query: Optional text to search for (case-insensitive LIKE match)
        category: Optional category filter (personal, skill, project, task)
        limit: Maximum number of results

    Returns:
        List of memory dicts ordered by timestamp descending
    """
    conn = get_connection()
    try:
        conditions = []
        params: list[Any] = []

        if query:
            conditions.append("text LIKE ?")
            params.append(f"%{query}%")

        if category:
            conditions.append("category = ?")
            params.append(category.lower())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        cursor = conn.execute(
            f"""SELECT id, text, category, source, timestamp 
               FROM memories 
               WHERE {where_clause}
               ORDER BY timestamp DESC
               LIMIT ?""",
            params + [limit],
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_message_markers(id: str, markers_json: str) -> bool:
    """Update the markers JSON for a message.

    Args:
        id: Message ID
        markers_json: JSON string of markers list

    Returns:
        True if updated successfully
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE messages SET markers = ? WHERE id = ?",
            (markers_json, id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def load_conversation(id: str) -> dict[str, Any] | None:
    """Load a full conversation with all its messages."""
    conversation = get_conversation(id)
    if not conversation:
        return None

    messages = get_messages(id)
    return {"conversation": conversation, "messages": messages}


def save_tool_output(
    id: str,
    conversation_id: str,
    message_id: str,
    tool_name: str,
    content: str,
    filetype: str | None = None,
) -> bool:
    """Insert a tool output record."""
    conn = get_connection()
    try:
        line_count = content.count("\n") + 1 if content else 0
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO tool_outputs (id, conversation_id, message_id, tool_name, content, line_count, filetype, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                id,
                conversation_id,
                message_id,
                tool_name,
                content,
                line_count,
                filetype,
                now,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError as e:
        import logging

        logging.getLogger("anya.db").warning(
            f"Failed to save tool output {id}: {e} (conv={conversation_id}, msg={message_id})"
        )
        return False
    finally:
        conn.close()


def get_tool_output(id: str) -> dict[str, Any] | None:
    """Get a tool output by ID."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT id, conversation_id, message_id, tool_name, content, line_count, filetype, created_at
               FROM tool_outputs WHERE id = ?""",
            (id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def get_tool_outputs_for_message(message_id: str) -> list[dict[str, Any]]:
    """Get all tool outputs for a message."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT id, conversation_id, message_id, tool_name, content, line_count, filetype, created_at
               FROM tool_outputs WHERE message_id = ? ORDER BY created_at ASC""",
            (message_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def replace_messages_with_summary(
    conversation_id: str,
    summary_msg_id: str,
    summary_content: str,
    timestamp: str,
) -> bool:
    """Delete all messages for a conversation and insert a single summary message.

    Used during context compaction to replace the full history with a summary.

    Args:
        conversation_id: The conversation to compact
        summary_msg_id: New message ID for the summary
        summary_content: The summary text to store
        timestamp: ISO 8601 timestamp for the new message

    Returns:
        True if successful
    """
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, author, model, created_at, ended_at, markers)
               VALUES (?, ?, 'assistant', ?, 'Code', NULL, ?, NULL, NULL)""",
            (summary_msg_id, conversation_id, summary_content, timestamp),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


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
    import json

    lines: list[str] = []

    for idx, msg in enumerate(messages):
        lines.append(markers.make_message_marker(msg["id"]))

        if msg["role"] == "user":
            for line in msg["content"].split("\n"):
                lines.append(f"> {line}")
        else:
            if msg.get("markers"):
                try:
                    marker_list = json.loads(msg["markers"])
                    marker_list = sorted(
                        marker_list, key=lambda x: x["pos"], reverse=True
                    )
                    content_lines = msg["content"].split("\n")

                    for marker in marker_list:
                        pos = marker["pos"]
                        names = marker.get("names") or [marker.get("name", "")]
                        if names and 0 <= pos <= len(content_lines):
                            content_lines.insert(pos, markers.make_marker(*names))

                    lines.extend(content_lines)
                except (json.JSONDecodeError, KeyError):
                    lines.append(msg["content"])
            else:
                lines.append(msg["content"])

    return "\n".join(lines)
