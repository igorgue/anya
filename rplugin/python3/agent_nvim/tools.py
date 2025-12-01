"""Tool implementations for agent.nvim plugin."""

import asyncio
import json
import os
import subprocess
import tempfile

# File reading limits (configurable via environment)
MAX_READ_BYTES = int(os.environ.get("AGENT_MAX_READ_BYTES", 64000))  # ~16k tokens


def read_file(path_with_range: str, cwd: str = None) -> str:
    """Reads file content with optional line range specifications.

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
    try:
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
                    return f"Error: Invalid range specification '{range_spec}'. Use 'start-end', 'start-100', '32-234', or '3202-end'"

                # Parse end
                if end_part == "end":
                    end_line = None  # Will read to end
                    force_full = True
                elif end_part.isdigit():
                    end_line = int(end_part)
                else:
                    return f"Error: Invalid range specification '{range_spec}'. Use 'start-end', 'start-100', '32-234', or '3202-end'"

        # Expand ~ to home directory
        path = os.path.expanduser(path)

        if not os.path.isabs(path):
            if cwd is None:
                cwd = os.getcwd()
            path = os.path.join(cwd, path)

        if not os.path.exists(path):
            return f"Error: File {path} does not exist."

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
                f"  READ THE FULL FILE: Call read_file('{display_path}@start-end') to get all {total_lines} lines.\n"
                f"Or use specific ranges: @{actual_end + 1}-{min(actual_end + 100, total_lines)} (next 100) or @{max(1, total_lines - 100)}-end (last 100)\n"
            )

        info_parts.append("--- FILE CONTENT ---\n")
        info_parts.append(content)
        info_parts.append("\n--- END FILE ---")

        return "".join(info_parts)
    except Exception as e:
        return f"Error reading file: {e}"


def read_many_files(files: list, cwd: str = None) -> str:
    """Reads multiple files in a single call, supporting line ranges.

    Each file in the list can include optional @range specification:
        "filename.py"              - Read first 100 lines (default)
        "filename.py@start-end"    - Read entire file
        "filename.py@32-234"       - Read lines 32-234
        "path/to/file.py@1-50"     - Read lines 1-50

    Args:
        files: List of file paths with optional @range specifications
        cwd: Current working directory for relative path resolution

    Returns:
        Combined content from all files with metadata, or error messages
    """
    if not files:
        return "Error: No files specified."

    if not isinstance(files, list):
        return f"Error: Expected list of files, got {type(files).__name__}"

    results = []
    file_count = 0
    error_count = 0

    for file_spec in files:
        if not isinstance(file_spec, str):
            results.append(
                f"[Skipped: Invalid file spec type {type(file_spec).__name__}]"
            )
            error_count += 1
            continue

        file_spec = file_spec.strip()
        if not file_spec:
            continue

        # Use the read_file function for each file
        content = read_file(file_spec, cwd)

        # Add separator between files
        results.append(content)
        results.append("\n" + "=" * 70 + "\n")
        file_count += 1

    # Build header with summary
    header = f"Reading {file_count} file(s)...\n{'=' * 70}\n\n"

    return header + "\n".join(results)


def list_files(path: str = ".", cwd: str = None) -> str:
    """Lists files in a directory (recursive, respects gitignore if possible).

    Args:
        path: Directory path to list (default current directory)
        cwd: Current working directory for relative path resolution

    Returns:
        Newline-separated list of file paths, or error message
    """
    try:
        # Expand ~ to home directory
        path = os.path.expanduser(path)
        
        if cwd is None:
            cwd = os.getcwd()
        
        if not os.path.isabs(path):
            target_dir = os.path.join(cwd, path)
        else:
            target_dir = path

        # Check if directory exists
        if not os.path.exists(target_dir):
            return f"Error: Directory does not exist: {path}"

        if not os.path.isdir(target_dir):
            return f"Error: Path is not a directory: {path}"

        # Use os.walk but limit depth/count
        files = []
        for root, _, filenames in os.walk(target_dir):
            if ".git" in root:
                continue
            for filename in filenames:
                rel_path = os.path.relpath(os.path.join(root, filename), cwd)
                files.append(rel_path)
                if len(files) > 100:
                    return "\n".join(files) + "\n... (truncated)"
        return "\n".join(files)
    except Exception as e:
        return f"Error listing files: {e}"


def search_repo(query: str, cwd: str = None) -> str:
    """Searches the repository for a string using grep/ripgrep.

    Args:
        query: Search query string
        cwd: Current working directory to search in

    Returns:
        Search results with line numbers, or error message
    """
    try:
        if cwd is None:
            cwd = os.getcwd()
        
        # Expand ~ to home directory
        cwd = os.path.expanduser(cwd)

        # Try ripgrep first
        cmd = ["rg", "--line-number", "--no-heading", "--smart-case", query, cwd]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout[:2000]  # Limit output
        except FileNotFoundError:
            # Fallback to grep
            cmd = ["grep", "-rn", query, cwd]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout[:2000]

        return "No matches found."
    except Exception as e:
        return f"Error searching repo: {e}"


def patch(patch_str: str) -> str:
    """Proposes a patch to be applied.

    DEPRECATED: Use the `edit` tool with SEARCH/REPLACE blocks instead.
    This tool is kept for backwards compatibility.

    The agent stops after calling this tool, waiting for user to apply (1) or reject (2).
    The conversation will continue automatically with the result.

    Args:
        patch_str: The patch content as a string

    Returns:
        The patch content (to be rendered by the UI)
    """
    # Clean up patch string - remove markdown code fences if present
    patch_str = patch_str.strip()
    if patch_str.startswith("```"):
        # Remove opening fence (```diff, ```patch, or just ```)
        lines = patch_str.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        patch_str = "\n".join(lines)

    # Ensure patch ends with newline (required by git apply)
    if not patch_str.endswith("\n"):
        patch_str += "\n"

    return patch_str


def edit(edit_blocks: str) -> str:
    """Propose code edits using SEARCH/REPLACE blocks.

    Use this tool to make precise code modifications. Each edit block specifies:
    - The file path
    - A SEARCH section with the exact code to find
    - A REPLACE section with the new code

    Format:
    ```
    path/to/file.py
    <<<<<<< SEARCH
    exact code to find
    =======
    replacement code
    >>>>>>> REPLACE
    ```

    Rules:
    - The SEARCH section must EXACTLY match existing code (including whitespace)
    - Include enough context lines to uniquely identify the location
    - Keep blocks small and focused on specific changes
    - Use multiple blocks for multiple changes
    - For new files: use empty SEARCH section

    Args:
        edit_blocks: String containing one or more SEARCH/REPLACE blocks

    Returns:
        The edit blocks content (to be rendered and applied by the UI)
    """
    # Clean up - remove outer markdown code fences if present
    edit_blocks = edit_blocks.strip()
    if edit_blocks.startswith("```") and not edit_blocks.startswith("<<<"):
        lines = edit_blocks.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        edit_blocks = "\n".join(lines)

    # Ensure trailing newline
    if not edit_blocks.endswith("\n"):
        edit_blocks += "\n"

    return edit_blocks


def exec(command: str, cwd: str = None, timeout: int = 30) -> str:
    """Execute a shell command and return stdout and stderr.

    Args:
        command: Shell command to execute
        cwd: Current working directory for the command (defaults to current directory)
        timeout: Timeout in seconds (default 30)

    Returns:
        Combined output with stdout and stderr, or error message
    """
    try:
        if cwd is None:
            cwd = os.getcwd()

        # Use Popen to get full control over stdout/stderr
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            return f"Error: Command timed out after {timeout} seconds"

        # Build output with both stdout and stderr
        output_parts = []

        if stdout:
            output_parts.append(f"STDOUT:\n{stdout}")

        if stderr:
            if output_parts:
                output_parts.append("")  # Add blank line separator
            output_parts.append(f"STDERR:\n{stderr}")

        if process.returncode != 0:
            if output_parts:
                output_parts.append("")
            output_parts.append(f"Exit code: {process.returncode}")

        return "\n".join(output_parts) if output_parts else "(no output)"

    except Exception as e:
        return f"Error executing command: {e}"


async def exec_lua(code: str, nvim=None, logger=None) -> str:
    """Execute Lua code inside Neovim.

    Args:
        code: Lua code to execute
        nvim: Neovim instance for executing commands
        logger: Logger instance for error logging

    Returns:
        Result of Lua execution or error message
    """
    if nvim is None:
        return "Error: Neovim instance not available"

    code = code.strip()

    # Use a temp file to communicate results since we can't do sync RPC
    # from the async context (greenlet context issue)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".lua", delete=False) as f:
        temp_lua = f.name
        temp_result = temp_lua + ".result"

        # Write wrapper that captures output and writes to file
        wrapper_code = f'''
local _output = {{}}
local _old_print = print
print = function(...)
    local args = {{...}}
    local parts = {{}}
    for i, v in ipairs(args) do
        parts[i] = tostring(v)
    end
    table.insert(_output, table.concat(parts, "\\t"))
end

local _ok, _result = pcall(function()
    {code}
end)

print = _old_print

local result = {{
    output = table.concat(_output, "\\n"),
    ok = _ok,
}}
if _ok then
    if _result ~= nil then
        result.value = _result
    end
else
    result.error = tostring(_result)
end

-- Write result to file as JSON
local json_result = vim.fn.json_encode(result)
local file = io.open("{temp_result}", "w")
if file then
    file:write(json_result)
    file:close()
end
'''
        f.write(wrapper_code)

    lua_done = asyncio.Event()

    def run_lua():
        try:
            nvim.command(f"luafile {temp_lua}")
        except Exception as e:
            if logger:
                logger.error(f"exec_lua error: {e}")
        finally:
            # Set the event from the main thread
            asyncio.get_event_loop().call_soon_threadsafe(lua_done.set)

    nvim.async_call(run_lua)

    # Wait for completion with async timeout
    try:
        await asyncio.wait_for(lua_done.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        try:
            os.unlink(temp_lua)
        except:
            pass
        return "Error: Lua execution timed out"

    # Read result from temp file
    try:
        os.unlink(temp_lua)

        if os.path.exists(temp_result):
            with open(temp_result, "r") as f:
                result = json.load(f)
            os.unlink(temp_result)

            parts = []
            if result.get("output"):
                parts.append(result["output"])

            if result.get("ok"):
                if "value" in result:
                    val = result["value"]
                    try:
                        parts.append(f"=> {json.dumps(val, indent=2, default=str)}")
                    except (TypeError, ValueError):
                        parts.append(f"=> {repr(val)}")
            else:
                parts.append(f"Error: {result.get('error', 'unknown error')}")

            return "\n".join(parts) if parts else "nil"
        else:
            return "Error: No result file created"
    except Exception as e:
        return f"Error reading result: {type(e).__name__}: {e}"
