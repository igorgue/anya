"""SQLite database storage for Anya conversations and messages.

Uses plain sqlite3 from stdlib. Database stored at:
$XDG_DATA_HOME/anya/conversations.db or ~/.local/share/anya/conversations.db
"""

import os
import pathlib
import re
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


def _memory_regexp(pattern: str, value: str | None) -> int:
    if value is None:
        return 0
    return 1 if re.search(pattern, value, re.IGNORECASE) else 0


def _memory_term_pattern(term: str) -> str:
    escaped = re.escape(term)
    if re.fullmatch(r"[a-z0-9']+", term):
        return rf"(?<![a-z0-9']){escaped}(?![a-z0-9'])"
    return escaped


def get_connection() -> sqlite3.Connection:
    """Get a sqlite3 connection with row_factory set."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("REGEXP", 2, _memory_regexp)
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_current_conversation_id() -> str | None:
    """Return the most recently updated conversation ID."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


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
                hidden INTEGER NOT NULL DEFAULT 0,
                message_type TEXT,
                meta TEXT,
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

        # Migration: Ensure unique index on memories.deduplication_key
        # Remove duplicates first, then create the unique index
        try:
            conn.execute("""
                DELETE FROM memories WHERE id NOT IN (
                    SELECT MIN(id) FROM memories
                    GROUP BY deduplication_key
                )
            """)
            conn.execute("DROP INDEX IF EXISTS idx_memories_dedup")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup_unique "
                "ON memories(deduplication_key)"
            )
            conn.commit()
        except Exception:
            conn.rollback()

        # Migration: Add markers column if it doesn't exist
        cursor = conn.execute("PRAGMA table_info(messages)")
        columns = {row[1] for row in cursor.fetchall()}
        if "markers" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN markers TEXT")
            # Set all existing messages to have empty marker list
            conn.execute("UPDATE messages SET markers = '[]' WHERE markers IS NULL")
            conn.commit()

        # Migration: Add hidden/message_type/meta columns to messages if they don't exist
        cursor = conn.execute("PRAGMA table_info(messages)")
        columns = {row[1] for row in cursor.fetchall()}
        if "hidden" not in columns:
            conn.execute(
                "ALTER TABLE messages ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
            )
            conn.commit()
        if "message_type" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN message_type TEXT")
            conn.commit()
        if "meta" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN meta TEXT")
            conn.commit()

        # Migration: Add cwd column to conversations if it doesn't exist
        cursor = conn.execute("PRAGMA table_info(conversations)")
        columns = {row[1] for row in cursor.fetchall()}
        if "cwd" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN cwd TEXT")
            conn.commit()
    finally:
        conn.close()


def search_conversation_mentions(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search conversations for mention completion.

    Searches conversation titles for fuzzy matching.

    Args:
        query: Search query (title text)
        limit: Maximum number of results

    Returns:
        List of conversation dicts with id, title, updated_at, cwd
    """
    conn = get_connection()
    try:
        # Split query into words and require each word to match independently
        words = query.split()
        if words:
            where_clauses = " AND ".join("title LIKE ?" for _ in words)
            params: list[Any] = [f"%{w}%" for w in words]
            params.append(limit)
            cursor = conn.execute(
                f"""SELECT id, title, updated_at, cwd FROM conversations
                   WHERE {where_clauses}
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                params,
            )
        else:
            cursor = conn.execute(
                """SELECT id, title, updated_at, cwd FROM conversations
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (limit,),
            )
        results = [dict(row) for row in cursor.fetchall()]

        # Set default title for untitled conversations
        for conv in results:
            if not conv.get("title"):
                conv["title"] = "Untitled conversation"

        return results
    finally:
        conn.close()


def get_conversation_content_for_mention(
    conversation_id: str, max_chars: int = 8000
) -> str | None:
    """Get bounded conversation content for mention context.

    Rebuilds the conversation content from the database, strips markers,
    and caps to a maximum character count.

    Args:
        conversation_id: The conversation ID
        max_chars: Maximum characters to include

    Returns:
        Formatted conversation content string, or None if not found
    """
    conv = load_conversation(conversation_id)
    if not conv:
        return None

    conversation = conv["conversation"]
    messages = conv["messages"]

    # Rebuild buffer content
    buffer_content = rebuild_buffer_content(conversation, messages)

    # Strip marker lines
    lines = buffer_content.split("\n")
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip marker lines (at: and am:)
        if stripped.startswith("<!-- at:") or stripped.startswith("<!-- am:"):
            continue
        clean_lines.append(line)

    content = "\n".join(clean_lines).strip()

    # Cap to max_chars
    if len(content) > max_chars:
        content = content[:max_chars] + "\n... (truncated)"

    return content


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


def count_conversations() -> int:
    """Return the total number of conversations."""
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM conversations")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def list_conversations(limit: int | None = 50, offset: int = 0) -> list[dict[str, Any]]:
    """List recent conversations ordered by updated_at descending."""
    conn = get_connection()
    try:
        query = "SELECT id, title, cwd, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        params: tuple[int, ...] = ()
        if limit is not None and limit >= 0:
            query += " LIMIT ? OFFSET ?"
            params = (limit, offset)
        cursor = conn.execute(query, params)
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
            """INSERT INTO messages (id, conversation_id, role, content, author, model, created_at, ended_at, markers, hidden, message_type, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                int(record.hidden),
                record.message_type,
                record.meta,
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
    hidden: bool = False,
    message_type: str | None = None,
    meta: str | None = None,
) -> bool:
    """Insert a message from individual fields."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, author, model, created_at, ended_at, markers, hidden, message_type, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                int(hidden),
                message_type,
                meta,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_messages(
    conversation_id: str, include_hidden: bool = True
) -> list[dict[str, Any]]:
    """Get all messages for a conversation ordered by created_at."""
    conn = get_connection()
    try:
        query = """SELECT id, conversation_id, role, content, author, model, created_at, ended_at, markers, hidden, message_type, meta
               FROM messages WHERE conversation_id = ?"""
        params: list[Any] = [conversation_id]
        if not include_hidden:
            query += " AND hidden = 0"
        query += " ORDER BY created_at ASC"
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_message(id: str) -> dict[str, Any] | None:
    """Get a single message by ID."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT id, conversation_id, role, content, author, model, created_at, ended_at, markers, hidden, message_type, meta
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


def delete_message(id: str) -> bool:
    """Delete a message by ID. Returns True if a row was deleted."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM messages WHERE id = ?", (id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def save_memory(memory: dict) -> bool:
    """
    Insert or update a memory item.

    The deduplication key represents a replaceable semantic fact (for example
    ``favorite-programming-language``). If the user later gives the actual value
    after an older placeholder/negative memory, update the row instead of
    dropping the new fact as a duplicate.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO memories (id, text, category, source, timestamp, deduplication_key, conversation_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET
                text = excluded.text,
                category = excluded.category,
                source = excluded.source,
                timestamp = excluded.timestamp,
                conversation_id = excluded.conversation_id,
                message_id = excluded.message_id
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


def memory_exists(deduplication_key: str) -> bool:
    """Return True if a memory with this deduplication key already exists."""
    if not deduplication_key:
        return False
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT 1 FROM memories WHERE deduplication_key = ? LIMIT 1",
            (deduplication_key,),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


_MEMORY_QUERY_STOP_WORDS = {
    "a",
    "about",
    "am",
    "an",
    "and",
    "are",
    "can",
    "could",
    "do",
    "does",
    "for",
    "have",
    "how",
    "i",
    "is",
    "it",
    "know",
    "me",
    "my",
    "of",
    "please",
    "remember",
    "tell",
    "that",
    "the",
    "to",
    "was",
    "what",
    "whats",
    "what's",
    "when",
    "with",
    "where",
    "who",
    "why",
    "you",
}

_MEMORY_QUERY_SYNONYMS = {
    "age": ("age", "birth", "birthday", "born", "birthdate", "date of birth"),
    "birthday": ("birthday", "birth", "born", "birthdate", "date of birth", "age"),
    "birthdate": ("birthdate", "birth", "birthday", "born", "date of birth", "age"),
    "born": ("born", "birth", "birthday", "birthdate", "date of birth", "age"),
    "called": ("called", "name", "full name", "prefer", "preferred"),
    "job": ("job", "work", "role", "career", "employment"),
    "name": ("name", "full name", "called", "prefer", "preferred"),
    "salary": ("salary", "pay", "compensation", "rate"),
    "work": ("work", "job", "role", "career", "employment"),
}


def _memory_query_terms(query: str) -> list[str]:
    """Return useful LIKE terms for natural-language memory questions."""
    normalized = query.strip().lower()
    if not normalized:
        return []

    terms: list[str] = []

    def add(term: str) -> None:
        term = term.strip().lower()
        if term and term not in terms:
            terms.append(term)

    add(normalized)

    # Keep a few meaningful quoted / multi-word phrases intact.
    for phrase in re.findall(r"[a-z0-9]+(?:[\s-]+[a-z0-9]+){1,3}", normalized):
        words = [
            w
            for w in re.findall(r"[a-z0-9']+", phrase)
            if w not in _MEMORY_QUERY_STOP_WORDS
        ]
        if len(words) >= 2:
            add(" ".join(words))

    for word in re.findall(r"[a-z0-9']+", normalized):
        if len(word) < 3:
            continue
        if word == "old" and "how" in normalized:
            add("age")
            for synonym in _MEMORY_QUERY_SYNONYMS["age"]:
                add(synonym)
            continue
        if word in _MEMORY_QUERY_STOP_WORDS:
            continue
        add(word)
        for synonym in _MEMORY_QUERY_SYNONYMS.get(
            word, ()
        ):  # targeted durable-fact aliases
            add(synonym)

    return terms[:20]


def search_memories(
    query: str | None = None, category: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """
    Search memories by text query and/or category.

    Query matching is intentionally forgiving for natural-language prompts. A
    prompt like "what's my full name?" should match stored facts such as
    "User's full name is ..." even though the full prompt is not a literal
    substring of the memory text.
    """
    conn = get_connection()
    try:
        conditions = []
        where_params: list[Any] = []

        terms = _memory_query_terms(query or "")
        if terms:
            conditions.append("(" + " OR ".join("text REGEXP ?" for _ in terms) + ")")
            where_params.extend(_memory_term_pattern(term) for term in terms)

        if category:
            conditions.append("category = ?")
            where_params.append(category.lower())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        score_expr = "0"
        score_params: list[Any] = []
        if terms:
            score_parts = []
            for index, term in enumerate(terms):
                weight = max(len(terms) - index, 1)
                score_parts.append(f"CASE WHEN text REGEXP ? THEN {weight} ELSE 0 END")
                score_params.append(_memory_term_pattern(term))
            score_expr = " + ".join(score_parts)

        cursor = conn.execute(
            f"""SELECT id, text, category, source, timestamp, ({score_expr}) AS relevance
               FROM memories 
               WHERE {where_clause}
               ORDER BY relevance DESC, timestamp DESC
               LIMIT ?""",
            score_params + where_params + [limit],
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


def load_conversation(id: str, include_hidden: bool = True) -> dict[str, Any] | None:
    """Load a full conversation with all its messages."""
    conversation = get_conversation(id)
    if not conversation:
        return None

    messages = get_messages(id, include_hidden=include_hidden)
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
        if msg.get("hidden"):
            continue
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


def find_hidden_message(
    conversation_id: str,
    message_type: str,
    meta_substring: str | None = None,
) -> dict[str, Any] | None:
    """Find the newest hidden message of a given type for a conversation."""
    conn = get_connection()
    try:
        query = (
            "SELECT id, conversation_id, role, content, author, model, created_at, ended_at, "
            "markers, hidden, message_type, meta FROM messages "
            "WHERE conversation_id = ? AND hidden = 1 AND message_type = ?"
        )
        params: list[Any] = [conversation_id, message_type]
        if meta_substring:
            query += " AND meta LIKE ?"
            params.append(f"%{meta_substring}%")
        query += " ORDER BY created_at DESC LIMIT 1"
        row = conn.execute(query, params).fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()
