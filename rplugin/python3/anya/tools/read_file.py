import os
from agents import function_tool, RunContextWrapper
from typing import Any

from .utils import create_error_handler


@function_tool(failure_error_function=create_error_handler)
async def read_file(path_with_range: str, cwd: str = None) -> str:
    """Reads file content with optional line range specifications.

    CRITICAL - read_file tool behavior:
    - Files with <= 100 lines: always shown in full
    - Files with > 100 lines: only first 100 lines shown by default with "[FILE TOO LARGE]" message
    - When you see "[FILE TOO LARGE]", use syntax like read_file("file.py@start-end") to read the full file
    - The @start-end syntax ALWAYS works to read the entire file, regardless of size
    - Do NOT make multiple calls to read_file with the same path without changing the range - you'll get the same truncated output

    Syntax:
        filename.py              - Read first 100 lines (default truncation)
        filename.py @start-end   - Read entire file
        filename.py @32-234      - Read lines 32-234
        filename.py @start-100   - Read lines 1-100
        filename.py @3202-end    - Read from line 3202 to end

    Args:
        path_with_range: File path with optional @start-end range specification
        cwd: Current working directory for relative path resolution

    Returns:
        File content as string with metadata about size and range, or error message
    """
    # Parse path and range specification
    path = path_with_range
    start_line = None
    end_line = None
    force_full = False

    if "@" in path_with_range:
        path, range_spec = path_with_range.rsplit("@", 1)
        path = path.strip()
        range_spec = range_spec.strip()

        if "-" in range_spec:
            parts = range_spec.split("-", 1)
            start_part = parts[0].strip()
            end_part = parts[1].strip()

            # Parse start
            if start_part == "start":
                start_line = 1
            elif start_part.isdigit():
                start_line = int(start_part)
            else:
                raise Exception(
                    f"Invalid range specification '{range_spec}'. Use 'start-end', 'start-100', '32-234', or '3202-end'"
                )

            # Parse end
            if end_part == "end":
                end_line = None  # Will read to end
                force_full = True
            elif end_part.isdigit():
                end_line = int(end_part)
            else:
                raise Exception(
                    f"Invalid range specification '{range_spec}'. Use 'start-end', 'start-100', '32-234', or '3202-end'"
                )

    # Expand ~ to home directory and environment variables
    path = os.path.expandvars(os.path.expanduser(path))

    if not os.path.isabs(path):
        if cwd is None:
            cwd = os.getcwd()
        path = os.path.join(cwd, path)

    if not os.path.exists(path):
        raise Exception(f"File {path} does not exist.")

    # Read file and count lines
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    total_lines = len(all_lines)
    file_size = os.path.getsize(path)

    # Determine which lines to return
    if start_line is not None or end_line is not None:
        # Explicit range requested
        start_idx = (start_line or 1) - 1  # Convert to 0-based index
        end_idx = end_line or total_lines  # 1-based line number, need to include it

        # Clamp to valid range
        start_idx = max(0, min(start_idx, total_lines - 1))
        end_idx = min(end_idx, total_lines)

        selected_lines = all_lines[start_idx:end_idx]
        actual_start = start_idx + 1
        actual_end = end_idx
        is_truncated = False
    else:
        # Default behavior: show first 100 lines if file is large
        lines_to_show = 100
        selected_lines = all_lines[:lines_to_show]
        actual_start = 1
        actual_end = min(lines_to_show, total_lines)
        is_truncated = total_lines > lines_to_show

    content = "".join(selected_lines)

    # Build response with metadata
    info_parts = [
        f"File: {path}\nTotal lines: {total_lines} | File size: {file_size} bytes\nShowing lines {actual_start}-{actual_end}\n"
    ]

    if is_truncated:
        # Extract the relative path for the message (undo any cwd joining)
        display_path = (
            path_with_range.split("@")[0]
            if "@" in path_with_range
            else path_with_range
        )
        info_parts.append(
            f"[FILE TOO LARGE] File has {total_lines} lines total, showing lines {actual_start}-{actual_end}.\n"
            f"  READ THE FULL FILE: Call read_file('{display_path}@start-end') to get all {total_lines} lines.\n"
            f"Or use specific ranges: @{actual_end + 1}-{min(actual_end + 100, total_lines)} (next 100) or @{max(1, total_lines - 100)}-end (last 100)\n"
        )

    info_parts.append("--- FILE CONTENT ---\n")
    info_parts.append(content)
    info_parts.append("\n--- END FILE ---")

    return "".join(info_parts)
