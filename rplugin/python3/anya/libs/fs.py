"""File system utilities: read, write, create, list, and search files.

Usage:
    from anya.libs import fs

    print(fs.read_file("src/main.py"))
    print(fs.read_file("src/main.py@100-200"))   # line range
    fs.write_file("out.txt", "hello")
    fs.create_file("new.py", "# stub")
    files = fs.list_files(".")
    results = fs.search_code("def my_func")
"""

import os
import shutil
import subprocess


def read_file(path_with_range: str, cwd: str | None = None) -> str:
    """Read a file with optional line range, returning content with line numbers.

    Syntax:
        "filename.py"            - Read first 300 lines (default limit)
        "filename.py@start-end"  - Read entire file
        "filename.py@32-234"     - Read lines 32 to 234
        "filename.py@start-800"  - Read lines 1 to 800
        "filename.py@3202-end"   - Read from line 3202 to end

    Args:
        path_with_range: File path with optional @start-end range specification.
        cwd: Base directory for relative paths (default: os.getcwd()).

    Returns:
        File content with line numbers (format: "123. content") and metadata header.
    """
    path = path_with_range
    start_line = None
    end_line = None

    if "@" in path_with_range:
        path, range_spec = path_with_range.rsplit("@", 1)
        path = path.strip()
        range_spec = range_spec.strip()

        if "-" in range_spec:
            parts = range_spec.split("-", 1)
            start_part = parts[0].strip()
            end_part = parts[1].strip()

            if start_part == "start":
                start_line = 1
            elif start_part.isdigit():
                start_line = int(start_part)
            else:
                raise ValueError(f"Invalid range specification: '{range_spec}'")

            if end_part == "end":
                end_line = None
            elif end_part.isdigit():
                end_line = int(end_part)
            else:
                raise ValueError(f"Invalid range specification: '{range_spec}'")

    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)

    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    total_lines = len(all_lines)
    file_size = os.path.getsize(path)

    if start_line is not None or end_line is not None:
        start_idx = (start_line or 1) - 1
        end_idx = end_line or total_lines
        start_idx = max(0, min(start_idx, total_lines - 1))
        end_idx = min(end_idx, total_lines)
        selected_lines = all_lines[start_idx:end_idx]
        actual_start = start_idx + 1
        actual_end = end_idx
        is_truncated = False
    else:
        lines_to_show = 300
        selected_lines = all_lines[:lines_to_show]
        actual_start = 1
        actual_end = min(lines_to_show, total_lines)
        is_truncated = total_lines > lines_to_show

    # Calculate line number width for alignment
    max_line_num = actual_start + len(selected_lines) - 1
    line_num_width = len(str(max_line_num))

    # Format lines with line numbers
    numbered_lines = []
    for i, line in enumerate(selected_lines):
        line_num = actual_start + i
        # Strip trailing newline, we'll add our own
        stripped = line.rstrip("\n")
        numbered_lines.append(f"{line_num:>{line_num_width}}. {stripped}")

    content = "\n".join(numbered_lines)

    header = (
        f"File: {path}\n"
        f"Total lines: {total_lines} | File size: {file_size} bytes\n"
        f"Showing lines {actual_start}-{actual_end}\n"
    )

    if is_truncated:
        display_path = (
            path_with_range.split("@")[0] if "@" in path_with_range else path_with_range
        )
        header += (
            f"[TRUNCATED] File has {total_lines} lines total.\n"
            f"  Read full file: fs.read_file('{display_path}@start-end')\n"
            f"  Next chunk: fs.read_file('{display_path}@{actual_end + 1}-{min(actual_end + 300, total_lines)}')\n"
        )

    return header + "\n" + content


def read_many_files(files: list[str], cwd: str | None = None) -> str:
    """Read multiple files in a single call, supporting line ranges.

    Each entry may include an optional @range specification (see read_file).

    Args:
        files: List of file path strings, each optionally with @start-end range.
        cwd: Base directory for relative paths (default: os.getcwd()).

    Returns:
        Combined content from all files separated by dividers.
    """
    if not files:
        return "(no files specified)"

    parts = []
    for spec in files:
        sep = "=" * 70
        try:
            parts.append(read_file(spec, cwd=cwd))
        except Exception as exc:
            parts.append(f"Error reading {spec}: {exc}")
        parts.append("\n" + sep + "\n")

    return f"Reading {len(files)} file(s)...\n{'=' * 70}\n\n" + "\n".join(parts)


def write_file(path: str, content: str, cwd: str | None = None) -> str:
    """Write content to a file, creating parent directories as needed.

    If the file does not exist it will be created. If it does exist its
    content will be replaced entirely.

    Args:
        path: Destination file path (supports ~ and env-var expansion).
        content: Text to write.
        cwd: Base directory for relative paths (default: os.getcwd()).

    Returns:
        A success message with the absolute path and file size.
    """
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return f"Wrote {os.path.getsize(path)} bytes to {path}"


def create_file(path: str, content: str = "", cwd: str | None = None) -> str:
    """Create a new file.  Raises if the file already exists.

    Args:
        path: New file path (supports ~ and env-var expansion).
        content: Optional initial content (default: empty).
        cwd: Base directory for relative paths (default: os.getcwd()).

    Returns:
        A success message with the absolute path and file size.
    """
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)

    if os.path.exists(path):
        raise FileExistsError(
            f"File already exists: {path}. Use write_file() to overwrite."
        )

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return f"Created {path} ({os.path.getsize(path)} bytes)"


def list_files(path: str = ".", max_results: int = 200, cwd: str | None = None) -> str:
    """List files in a directory recursively (respects .gitignore via fd).

    Falls back to os.walk if fd is not installed.

    Args:
        path: Directory to list (default: current directory).
        max_results: Maximum number of files to return (default 200).
        cwd: Base directory for relative paths (default: os.getcwd()).

    Returns:
        Newline-separated list of file paths.
    """
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)

    if not os.path.isdir(path):
        raise NotADirectoryError(f"Not a directory: {path}")

    fd_exe = shutil.which("fd")
    if fd_exe:
        result = subprocess.run(
            [fd_exe, "--type", "f", ".", path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            lines = [line for line in result.stdout.strip().splitlines() if line]
            if len(lines) > max_results:
                lines = lines[:max_results]
                lines.append(f"... (truncated at {max_results} results)")
            return "\n".join(lines)

    # Fallback: os.walk
    found: list[str] = []
    for root, _dirs, filenames in os.walk(path):
        for fn in filenames:
            found.append(os.path.join(root, fn))
            if len(found) >= max_results:
                found.append(f"... (truncated at {max_results} results)")
                return "\n".join(found)
    return "\n".join(found)


def search_code(
    query: str, path: str | None = None, max_chars: int = 4000, cwd: str | None = None
) -> str:
    """Search files for a string using ripgrep (or grep as fallback).

    Args:
        query: The search pattern.
        path: Directory or file to search (default: cwd).
        max_chars: Maximum characters of output to return (default 4000).
        cwd: Base directory (default: os.getcwd()).

    Returns:
        Matching lines with file paths and line numbers.
    """
    base = cwd or os.getcwd()
    target = path or base
    target = os.path.expandvars(os.path.expanduser(target))
    if not os.path.isabs(target):
        target = os.path.join(base, target)

    for cmd in (
        ["rg", "--line-number", "--no-heading", "--smart-case", query, target],
        ["grep", "-rn", query, target],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                output = result.stdout[:max_chars]
                return output or "(no matches)"
        except FileNotFoundError:
            continue

    return "No matches found."
