"""Utility functions for text processing."""


def filter_anya_markers(text: str, in_marker: bool) -> tuple[str, bool]:
    """Filter out anya marker comments from streaming text.

    Only filters markers that look like anya internal markers (<!-- am:, <!-- at:, <!-- ac:).
    Preserves legitimate HTML comments that users may want in their output.

    Handles markers that span multiple chunks by tracking state.

    Args:
        text: The text chunk to filter
        in_marker: Whether we're currently inside an anya marker

    Returns:
        Tuple of (filtered_text, still_in_marker)
    """
    import re

    # Only match anya-specific marker patterns
    marker_pattern = re.compile(r"<!-- a[mtc]:")

    result = []
    i = 0
    while i < len(text):
        if in_marker:
            # Look for end of marker
            end_idx = text.find("-->", i)
            if end_idx != -1:
                # Found end, skip to after it
                i = end_idx + 3
                in_marker = False
            else:
                # Still in marker, discard rest of text
                break
        else:
            # Look for start of anya marker (<!-- am:, <!-- at:, <!-- ac:)
            match = marker_pattern.search(text, i)
            if match:
                start_idx = match.start()
                # Add text before marker
                result.append(text[i:start_idx])
                # Check if marker ends in this chunk
                end_idx = text.find("-->", start_idx + 8)
                if end_idx != -1:
                    # Complete marker in this chunk, skip it
                    i = end_idx + 3
                else:
                    # Marker continues beyond this chunk
                    in_marker = True
                    break
            else:
                # No marker start, add rest of text
                result.append(text[i:])
                break
    return "".join(result), in_marker


def close_open_code_blocks(content: str) -> str:
    """Close any unclosed markdown code blocks in the content.

    Detects code fence markers (```, ```python, etc.) and ensures they're
    properly closed. If a fence is opened but not closed, adds closing
    backticks.

    Args:
        content: The markdown content to check

    Returns:
        The content with any unclosed code blocks closed
    """
    if not content:
        return content

    lines = content.split("\n")
    fence_stack = []  # Stack of backtick counts for open fences

    for line in lines:
        stripped = line.lstrip()

        # Check if this line starts with backticks
        if stripped.startswith("`"):
            # Count consecutive backticks at the start
            tick_count = 0
            for char in stripped:
                if char == "`":
                    tick_count += 1
                else:
                    break

            # Need at least 3 backticks to be a fence
            if tick_count >= 3:
                # Check if this closes the most recent open fence
                if fence_stack and fence_stack[-1] == tick_count:
                    fence_stack.pop()
                else:
                    # This opens a new fence
                    fence_stack.append(tick_count)

    # If there are unclosed fences, add closing backticks
    if fence_stack:
        tick_count = fence_stack[-1]
        closing_fence = "`" * tick_count
        lines.append(closing_fence)

    return "\n".join(lines)


def format_tool_header(tool_name: str, tool_args: str) -> str:
    """Format a tool header without markers (for use in parallel tool display).

    Args:
        tool_name: The name of the tool function
        tool_args: The arguments passed to the tool (JSON string)

    Returns:
        Formatted header like **tool_name | arg**
    """
    import json
    from .tools.utils import format_tool_header as fmt_header

    # Try to extract first argument from JSON args
    first_arg = ""
    try:
        if tool_args:
            # Check if tool_args is already a dict (sometimes happens?)
            # or string. Assuming string as per types.
            if isinstance(tool_args, str):
                args_dict = json.loads(tool_args)
            else:
                args_dict = tool_args

            # Special handling for edit tool - extract filename from edit_blocks
            if tool_name == "edit" and "edit_blocks" in args_dict:
                edit_blocks = args_dict["edit_blocks"]
                # Extract filename from first line or before <<<<<<< SEARCH
                lines = edit_blocks.strip().split("\n")
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("<") and not line.startswith("="):
                        # This looks like a filename
                        first_arg = line
                        break
                if not first_arg:
                    first_arg = "(edit)"
            else:
                # Get the first non-empty value
                for key, value in args_dict.items():
                    if isinstance(value, str):
                        # Don't truncate here - let format_tool_header handle it
                        first_arg = value
                        # Remove newlines for display but keep the content
                        first_arg = first_arg.replace("\n", " ").strip()
                    else:
                        first_arg = str(value)
                    break
    except (json.JSONDecodeError, AttributeError):
        first_arg = tool_args if tool_args else ""

    if not first_arg:
        first_arg = "(no args)"

    # Use the utility function with proper truncation (default 60 chars)
    return fmt_header(tool_name, first_arg)


def format_tool_call(tool_name: str, tool_args: str) -> str:
    """Format a tool call as a header with opening fold marker.

    Args:
        tool_name: The name of the tool function
        tool_args: The arguments passed to the tool (JSON string)

    Returns:
        Formatted header with opening fold marker
    """
    return format_tool_call_with_status(tool_name, tool_args, "tool_pending")


def format_tool_call_with_status(tool_name: str, tool_args: str, status: str) -> str:
    """Format a tool call as a header with status marker.

    Args:
        tool_name: The name of the tool function
        tool_args: The arguments passed to the tool (JSON string)
        status: The tool status marker (tool_pending, tool_success, tool_failure)

    Returns:
        Formatted header with status marker
    """
    from . import markers

    header = format_tool_header(tool_name, tool_args)

    return header + "\n" + markers.make_marker(status) + "\n"
