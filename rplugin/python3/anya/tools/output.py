"""Tool output detection and formatting."""

import re


def extract_tool_call(text: str) -> tuple[str, str] | None:
    """Extract tool name and first arg from tool call header.

    Looks for: **tool_name | first_arg**

    Returns:
        (tool_name, first_arg) or None
    """
    match = re.match(r"\*\*(\w+(?:-\w+)*)\s*\|\s*([^*]+)\*\*", text)
    if match:
        return match.group(1), match.group(2).strip()
    return None


def format_tool_header(tool_name: str, first_arg: str, max_len: int = 30) -> str:
    """Format tool header with trimmed argument.

    Args:
        tool_name: Tool function name
        first_arg: First argument to tool
        max_len: Max length before trimming

    Returns:
        **tool_name | trimmed_arg**
    """
    if len(first_arg) > max_len:
        trimmed = first_arg[: max_len - 3] + "..."
    else:
        trimmed = first_arg

    return f"**{tool_name} | {trimmed}**"


def is_tool_header(text: str) -> bool:
    """Check if text line is a tool header."""
    return extract_tool_call(text) is not None
