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
THINKING = "thinking"
TASK_LIST_START = "task_list_start"
TASK_LIST_END = "task_list_end"

PREFIX = "<!-- at:"
SUFFIX = "-->"

MESSAGE_PREFIX = "<!-- am:"
MESSAGE_SUFFIX = "-->"


def make_marker(*names: str) -> str:
    """Create a tool marker line with the given marker names."""
    return f"{PREFIX} {', '.join(names)} {SUFFIX}"


def make_message_marker(msg_id: str) -> str:
    """Create a simplified message marker line with only the message ID."""
    return f"{MESSAGE_PREFIX} {msg_id} {MESSAGE_SUFFIX}"


def parse_marker(line: str) -> list[str] | None:
    """Parse a marker line and return the list of marker names.

    Args:
        line: The line to parse (e.g., "<!-- at: fold_start, tool_pending -->")

    Returns:
        List of marker names, or None if not a marker line
    """
    line = line.strip()
    if not line.startswith(PREFIX) or not line.endswith(SUFFIX):
        return None

    # Extract content between PREFIX and SUFFIX
    content = line[len(PREFIX) : -len(SUFFIX)].strip()
    if not content:
        return None

    # Split by comma and strip whitespace
    markers = [m.strip() for m in content.split(",") if m.strip()]
    return markers if markers else None


def has_marker(line: str, marker_name: str) -> bool:
    """Check if a line contains a specific marker.

    Args:
        line: The line to check
        marker_name: The marker name to look for (e.g., "tool_pending")

    Returns:
        True if the line is a marker line containing the specified marker
    """
    parsed = parse_marker(line)
    return parsed is not None and marker_name in parsed


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
