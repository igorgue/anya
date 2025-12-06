"""Text markers for embedding metadata in buffer content.

These HTML comment markers are invisible in markdown renderers and allow
reconstructing UI state (folds, extmarks, widgets) from pure text content.
They are human-readable and safe to edit.
"""

# Marker names
FOLD_START = "fold_start"
FOLD_END = "fold_end"
TOOL_PENDING = "tool_pending"
TOOL_SUCCESS = "tool_success"
TOOL_FAILURE = "tool_failure"
EDIT_PENDING = "edit_pending"
EDIT_APPLIED = "edit_applied"
EDIT_REJECTED = "edit_rejected"
EDIT_FAILED = "edit_failed"

PREFIX = "<!-- anya:"
SUFFIX = "-->"


def make_marker(*names: str) -> str:
    """Create a marker line with the given marker names.

    Args:
        *names: One or more marker names to include

    Returns:
        A marker line like '<!-- anya: fold_start, tool_pending -->'
    """
    return f"{PREFIX} {', '.join(names)} {SUFFIX}"


def make_agent_message_start(msg_id: str, agent_type: str, model: str, timestamp: str) -> str:
    """Create a message start marker line for an agent message.

    Args:
        msg_id: The message ID
        agent_type: The agent type (e.g., "code", "plan")
        model: The model name (e.g., "gpt-4.1")
        timestamp: ISO 8601 UTC timestamp

    Returns:
        A marker line like '<!-- anya__message: f13e20, start, code, gpt-4.1, 2024-06-27T14:30:00Z -->'
    """
    return f"<!-- anya__message: {msg_id}, start, {agent_type}, {model}, {timestamp} -->"


def make_message_end(msg_id: str, timestamp: str) -> str:
    """Create a message end marker line.

    Args:
        msg_id: The message ID
        timestamp: ISO 8601 UTC timestamp

    Returns:
        A marker line like '<!-- anya__message: 604c2d, end, 2024-06-27T14:30:00Z -->'
    """
    return f"<!-- anya__message: {msg_id}, end, {timestamp} -->"


def with_markers(text: str, marker_list: list[str]) -> str:
    """Inject markers into text.

    If marker_list includes "fold", inserts fold_start after first line
    and fold_end at the end. All markers are combined into a single
    marker line after the first line.

    Args:
        text: Text to inject markers into
        marker_list: List of marker names (e.g., ["fold", "tool_success"])

    Returns:
        Text with marker lines inserted
    """
    lines = text.split("\n")
    if not lines or not marker_list:
        return text

    # Check if fold is requested and build start markers
    has_fold = False
    start_markers = []

    for m in marker_list:
        if m == "fold":
            has_fold = True
            start_markers.append(FOLD_START)
        else:
            start_markers.append(m)

    # Insert marker line after first line
    result = [lines[0], make_marker(*start_markers)]
    result.extend(lines[1:])

    # Add fold_end if fold was requested
    if has_fold:
        result.append(make_marker(FOLD_END))

    return "\n".join(result)
