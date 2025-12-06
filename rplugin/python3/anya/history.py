"""Conversation history parsing and building from buffer content with markers.

This module extracts messages and metadata from buffer content containing
anya markers, and builds clean history for LLM input.
"""

import re
from dataclasses import dataclass, field
from typing import Any

CONVERSATION_PATTERN = re.compile(r"^<!-- anya__conversation: ([^,]+), ([^-]+) -->$")
MESSAGE_START_PATTERN = re.compile(r"^<!-- anya__message: ([^,]+), start, (.+) -->$")
MESSAGE_END_PATTERN = re.compile(r"^<!-- anya__message: ([^,]+), end, ([^-]+) -->$")
TOOLS_PATTERN = re.compile(r"^<!-- anya__tools: (.+) -->$")


@dataclass
class Marker:
    """A marker within message content."""

    type: str
    ids: list[str]
    pos: int


@dataclass
class MessageRecord:
    """A message record in the conversation history."""

    type: str
    id: str
    role: str | None = None
    content: str = ""
    author: str | None = None
    model: str | None = None
    timestamp: str | None = None
    end_timestamp: str | None = None
    conversation_id: str | None = None
    markers: list[Marker] = field(default_factory=list)


@dataclass
class ConversationRecord:
    """A conversation marker record."""

    type: str
    id: str
    timestamp: str
    content: str = ""
    markers: list[Marker] = field(default_factory=list)


def parse_conversation_marker(line: str) -> dict[str, str] | None:
    """Parse a conversation marker line.

    Format: <!-- anya__conversation: {id}, {timestamp} -->

    Returns:
        Dict with 'id' and 'timestamp', or None if not a valid marker.
    """
    match = CONVERSATION_PATTERN.match(line.strip())
    if not match:
        return None
    return {"id": match.group(1).strip(), "timestamp": match.group(2).strip()}


def parse_message_start_marker(line: str) -> dict[str, Any] | None:
    """Parse a message start marker line.

    User format: <!-- anya__message: {id}, start, {author}, {timestamp} -->
    Agent format: <!-- anya__message: {id}, start, {agent_type}, {model}, {timestamp} -->

    Returns:
        Dict with parsed fields, or None if not a valid marker.
    """
    match = MESSAGE_START_PATTERN.match(line.strip())
    if not match:
        return None

    msg_id = match.group(1).strip()
    rest = match.group(2).strip()

    parts = [p.strip() for p in rest.split(",")]

    if len(parts) == 2:
        return {
            "id": msg_id,
            "is_agent": False,
            "author": parts[0],
            "model": None,
            "timestamp": parts[1],
        }
    elif len(parts) == 3:
        return {
            "id": msg_id,
            "is_agent": True,
            "author": parts[0],
            "model": parts[1],
            "timestamp": parts[2],
        }
    return None


def parse_message_end_marker(line: str) -> dict[str, str] | None:
    """Parse a message end marker line.

    Format: <!-- anya__message: {id}, end, {timestamp} -->

    Returns:
        Dict with 'id' and 'timestamp', or None if not a valid marker.
    """
    match = MESSAGE_END_PATTERN.match(line.strip())
    if not match:
        return None
    return {"id": match.group(1).strip(), "timestamp": match.group(2).strip()}


def parse_tool_marker(line: str) -> list[str] | None:
    """Parse a tool marker line.

    Format: <!-- anya__tools: {id1}, {id2}, ... -->

    Returns:
        List of marker IDs, or None if not a valid marker.
    """
    match = TOOLS_PATTERN.match(line.strip())
    if not match:
        return None
    return [id.strip() for id in match.group(1).split(",")]


def is_marker_line(line: str) -> bool:
    """Check if a line is any type of marker."""
    stripped = line.strip()
    return stripped.startswith("<!-- anya") and stripped.endswith("-->")


def is_header_line(line: str) -> bool:
    """Check if a line is a markdown header (# Username or # Anya)."""
    stripped = line.strip()
    return stripped.startswith("# ") and not stripped.startswith("## ")


def strip_blockquote(text: str) -> str:
    """Remove blockquote prefix (> ) from each line."""
    lines = text.split("\n")
    result = []
    for line in lines:
        if line.startswith("> "):
            result.append(line[2:])
        elif line == ">":
            result.append("")
        else:
            result.append(line)
    return "\n".join(result)


def parse_buffer_content(
    buffer_content: str,
) -> list[MessageRecord | ConversationRecord]:
    """Parse buffer content with markers into a list of records.

    Args:
        buffer_content: Raw buffer text with markers

    Returns:
        List of MessageRecord and ConversationRecord objects
    """
    lines = buffer_content.split("\n")
    records: list[MessageRecord | ConversationRecord] = []

    current_conversation_id: str | None = None
    current_message: MessageRecord | None = None
    content_lines: list[str] = []
    in_message = False

    for line in lines:
        conv_marker = parse_conversation_marker(line)
        if conv_marker:
            current_conversation_id = conv_marker["id"]
            records.append(
                ConversationRecord(
                    type="anya__conversation",
                    id=conv_marker["id"],
                    timestamp=conv_marker["timestamp"],
                )
            )
            continue

        start_marker = parse_message_start_marker(line)
        if start_marker:
            if current_message and in_message:
                current_message.content = "\n".join(content_lines).strip()
                records.append(current_message)
                content_lines = []

            role = "assistant" if start_marker["is_agent"] else "user"
            current_message = MessageRecord(
                type="anya__message",
                id=start_marker["id"],
                role=role,
                author=start_marker["author"],
                model=start_marker["model"],
                timestamp=start_marker["timestamp"],
                conversation_id=current_conversation_id,
            )
            in_message = True
            content_lines = []
            continue

        end_marker = parse_message_end_marker(line)
        if end_marker:
            if current_message and current_message.id == end_marker["id"]:
                current_message.content = "\n".join(content_lines).strip()
                current_message.end_timestamp = end_marker["timestamp"]
                records.append(current_message)
                current_message = None
                in_message = False
                content_lines = []
            continue

        if is_marker_line(line):
            tool_ids = parse_tool_marker(line)
            if tool_ids and current_message:
                pos = len("\n".join(content_lines))
                current_message.markers.append(
                    Marker(type="anya__tools", ids=tool_ids, pos=pos)
                )
            continue

        if in_message:
            if is_header_line(line):
                continue
            content_lines.append(line)

    if current_message and in_message:
        current_message.content = "\n".join(content_lines).strip()
        records.append(current_message)

    return records


def build_llm_history(
    records: list[MessageRecord | ConversationRecord],
) -> list[dict[str, str]]:
    """Build clean history for LLM input from parsed records.

    Args:
        records: List of parsed records

    Returns:
        List of dicts with only 'role' and 'content' for the LLM
    """
    history: list[dict[str, str]] = []

    for record in records:
        if isinstance(record, ConversationRecord):
            continue

        if record.role and record.content:
            content = record.content
            if record.role == "user":
                content = strip_blockquote(content)
            history.append({"role": record.role, "content": content})

    return history


def build_full_history(
    records: list[MessageRecord | ConversationRecord],
) -> list[dict[str, Any]]:
    """Build full history with all metadata from parsed records.

    Args:
        records: List of parsed records

    Returns:
        List of dicts with all message fields
    """
    history: list[dict[str, Any]] = []

    for record in records:
        if isinstance(record, ConversationRecord):
            history.append(
                {
                    "type": record.type,
                    "id": record.id,
                    "content": "",
                    "timestamp": record.timestamp,
                    "markers": [],
                }
            )
        else:
            history.append(
                {
                    "type": record.type,
                    "id": record.id,
                    "role": record.role,
                    "content": record.content,
                    "author": record.author,
                    "model": record.model,
                    "timestamp": record.timestamp,
                    "end_timestamp": record.end_timestamp,
                    "conversation_id": record.conversation_id,
                    "markers": [
                        {"type": m.type, "ids": m.ids, "pos": m.pos}
                        for m in record.markers
                    ],
                }
            )

    return history
