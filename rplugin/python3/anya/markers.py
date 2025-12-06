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


def with_fold(text: str, *extra_markers: str) -> str:
    """Wrap text with fold markers.

    Inserts a fold_start marker line after the first line and a fold_end
    marker line at the end. The markers affect the lines above them.

    Args:
        text: Text to wrap with fold markers
        *extra_markers: Additional markers to include with fold_start

    Returns:
        Text with fold marker lines inserted
    """
    lines = text.split("\n")
    if not lines:
        return text

    # Combine fold_start with any extra markers
    start_markers = [FOLD_START] + list(extra_markers)

    # Insert fold_start after first line, fold_end at the end
    result = [lines[0], make_marker(*start_markers)]
    result.extend(lines[1:])
    result.append(make_marker(FOLD_END))

    return "\n".join(result)
