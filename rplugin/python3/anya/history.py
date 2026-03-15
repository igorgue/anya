"""Conversation history parsing and building from buffer content with markers.

This module extracts messages and metadata from buffer content containing
anya markers, and builds clean history for LLM input.
"""

import re
from dataclasses import dataclass, field
from typing import Any

MESSAGE_PATTERN = re.compile(r"^<!-- am: (.+) -->$")
TOOLS_PATTERN = re.compile(r"^<!-- at: (.+) -->$")

# Pattern for conversation mentions: #abc12345
CONV_MENTION_PATTERN = re.compile(r"#([A-Za-z0-9]+)")


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


def parse_message_marker(line: str) -> str | None:
    """Parse a simplified message marker line containing only the message ID."""
    match = MESSAGE_PATTERN.match(line.strip())
    if not match:
        return None
    return match.group(1).strip()


def parse_tool_marker(line: str) -> list[str] | None:
    """Parse a tool marker line.

    Format: <!-- at: {id1}, {id2}, ... -->

    Returns:
        List of marker IDs, or None if not a valid marker.
    """
    match = TOOLS_PATTERN.match(line.strip())
    if not match:
        return None
    return [id.strip() for id in match.group(1).split(",")]


def is_marker_line(line: str) -> bool:
    """Check if a line is any type of anya marker (at:, am:)."""
    stripped = line.strip()
    if not stripped.endswith("-->"):
        return False
    return stripped.startswith("<!-- at:") or stripped.startswith("<!-- am:")


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


def clean_assistant_content(text: str, record_markers: list | None = None) -> str:
    """Clean assistant message content for LLM history.

    Removes thinking blocks that were rendered as tool output in the UI
    but should not be included in conversation history sent to the LLM.

    Args:
        text: The raw assistant message content (markers already stripped)
        record_markers: Optional list of Marker objects from the MessageRecord.
            Used to identify thinking block boundaries.

    Returns:
        Cleaned content with thinking blocks removed
    """
    # Build sets of line positions that should be hidden from LLM history.
    # Markers reference positions in the cleaned content (without marker lines).
    thinking_ranges: list[tuple[int, int]] = []
    if record_markers:
        thinking_start: int | None = None
        for m in record_markers:
            if "thinking" in m.ids:
                # fold_start+thinking marker: the **thinking** header is at pos-1
                thinking_start = max(0, m.pos - 1)
            elif "fold_end" in m.ids and thinking_start is not None:
                thinking_ranges.append((thinking_start, m.pos))
                thinking_start = None

    lines = text.split("\n")
    result = []

    for i, line in enumerate(lines):
        # Skip lines within synthetic UI blocks
        if any(start <= i < end for start, end in thinking_ranges):
            continue
        result.append(line)

    # Strip cancellation markers (added by cancel_agent)
    # These are visual indicators that shouldn't be sent to the LLM
    while result and result[-1].strip().startswith("> cancelled"):
        result.pop()

    # Clean up result - remove leading/trailing empty lines
    cleaned = "\n".join(result).strip()
    return cleaned


def parse_buffer_content(
    buffer_content: str,
) -> list[MessageRecord]:
    """Parse buffer content with markers into a list of records.

    Args:
        buffer_content: Raw buffer text with markers

    Returns:
        List of MessageRecord objects
    """
    from . import db

    lines = buffer_content.split("\n")
    records: list[MessageRecord] = []

    current_message: MessageRecord | None = None
    content_lines: list[str] = []

    for line in lines:
        msg_id = parse_message_marker(line)
        if msg_id:
            if current_message:
                current_message.content = "\n".join(content_lines).strip()
                records.append(current_message)
                content_lines = []

            message_row = db.get_message(msg_id)
            role = None
            author = None
            model = None
            created_at = None
            ended_at = None
            conversation_id = None

            if message_row:
                role = message_row.get("role")
                author = message_row.get("author")
                model = message_row.get("model")
                created_at = message_row.get("created_at")
                ended_at = message_row.get("ended_at")
                conversation_id = message_row.get("conversation_id")

            current_message = MessageRecord(
                type="am",
                id=msg_id,
                role=role,
                author=author,
                model=model,
                timestamp=created_at,
                end_timestamp=ended_at,
                conversation_id=conversation_id,
            )
            continue

        if is_marker_line(line):
            tool_ids = parse_tool_marker(line)
            if tool_ids and current_message:
                pos = len(content_lines)
                current_message.markers.append(Marker(type="at", ids=tool_ids, pos=pos))
            continue

        if current_message:
            content_lines.append(line)

    if current_message:
        if not current_message.role:
            all_blockquote = all(
                line.startswith(">") or line.strip() == "" for line in content_lines
            )
            current_message.role = "user" if all_blockquote else "assistant"
        current_message.content = "\n".join(content_lines).strip()
        records.append(current_message)

    return records


def extract_conv_mentions(text: str) -> list[str]:
    """Extract conversation IDs from # mentions in text.

    Args:
        text: Text that may contain #conversation_id mentions

    Returns:
        List of unique conversation IDs found
    """
    matches = CONV_MENTION_PATTERN.findall(text)
    # Return unique IDs in order of first appearance
    seen = set()
    result = []
    for conv_id in matches:
        if conv_id not in seen:
            seen.add(conv_id)
            result.append(conv_id)
    return result


def inject_conversation_context(text: str, max_chars_per_conv: int = 8000) -> str:
    """Inject context from # mentions into the text.

    For each #conversation_id mention in the text, fetches the
    conversation content and injects it as context before the message.

    Args:
        text: Text that may contain #conversation_id mentions
        max_chars_per_conv: Maximum characters to include per conversation

    Returns:
        Text with conversation context injected
    """
    from . import db

    conv_ids = extract_conv_mentions(text)
    if not conv_ids:
        return text

    context_parts = []
    for conv_id in conv_ids:
        try:
            content = db.get_conversation_content_for_mention(
                conv_id, max_chars_per_conv
            )
            if content:
                context_parts.append(
                    f"<context from conversation {conv_id}>\n{content}\n</context>"
                )
        except Exception:
            # Silently skip conversations that can't be loaded
            pass

    if not context_parts:
        return text

    # Inject context at the beginning of the message
    context_block = "\n\n".join(context_parts)
    return f"{context_block}\n\n{text}"


def build_llm_history(
    records: list[MessageRecord],
) -> list[dict[str, str]]:
    """Build clean history for LLM input from parsed records.

    Args:
        records: List of parsed records

    Returns:
        List of dicts with only 'role' and 'content' for the LLM
    """
    history: list[dict[str, str]] = []

    for record in records:
        if record.role and record.content:
            content = record.content
            if record.role == "user":
                content = strip_blockquote(content)
                # Inject conversation context from # mentions
                content = inject_conversation_context(content)
            elif record.role == "assistant":
                # Clean assistant content to remove thinking blocks
                content = clean_assistant_content(content, record.markers)
            # Skip empty messages after cleaning
            if not content.strip():
                continue
            history.append({"role": record.role, "content": content})

    return history


def build_full_history(
    records: list[MessageRecord],
) -> list[dict[str, Any]]:
    """Build full history with all metadata from parsed records.

    Args:
        records: List of parsed records

    Returns:
        List of dicts with all message fields
    """
    history: list[dict[str, Any]] = []

    for record in records:
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
                    {"type": m.type, "ids": m.ids, "pos": m.pos} for m in record.markers
                ],
            }
        )

    return history


def extract_markers_from_content(content: str) -> tuple[str, str]:
    """Extract tool markers from message content.

    Parses the content to find all `<!-- at: ... -->` markers and returns
    them as a JSON string and the cleaned content (without markers).

    Args:
        content: The message content with embedded markers

    Returns:
        Tuple of (cleaned_content, markers_json) where markers_json is
        [{"names": ["marker1", "marker2"], "pos": line_number}, ...] or None
    """
    import json

    lines = content.split("\n")
    cleaned_lines = []
    markers_list = []

    for line in lines:
        tool_ids = parse_tool_marker(line)
        if tool_ids:
            # Store all marker names together at the same position
            pos = len(cleaned_lines)
            markers_list.append({"names": tool_ids, "pos": pos})
        else:
            cleaned_lines.append(line)

    cleaned_content = "\n".join(cleaned_lines)
    markers_json = json.dumps(markers_list) if markers_list else None

    return (cleaned_content, markers_json)
