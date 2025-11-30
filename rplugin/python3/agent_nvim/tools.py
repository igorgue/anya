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
            end_idx = (end_line or total_lines)  # 1-based line number, need to include it
            
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
        info_parts = [f"File: {path}\nTotal lines: {total_lines} | File size: {file_size} bytes\nShowing lines {actual_start}-{actual_end}\n"]
        
        if is_truncated:
            info_parts.append(
                f"[FILE TOO LARGE] This file has {total_lines} lines total. "
                f"Currently showing lines {actual_start}-{actual_end}.\n"
                f"To read more: use syntax like @start-end (whole file), @{actual_end + 1}-{min(actual_end + 100, total_lines)}, or @1-{total_lines} to read entire file.\n"
            )
        
        info_parts.append("--- FILE CONTENT ---\n")
        info_parts.append(content)
        info_parts.append("\n--- END FILE ---")
        
        return "".join(info_parts)
    except Exception as e:
        return f"Error reading file: {e}"


def list_files(path: str = ".", cwd: str = None) -> str:
    """Lists files in a directory (recursive, respects gitignore if possible).
    
    Args:
        path: Directory path to list (default current directory)
        cwd: Current working directory for relative path resolution
        
    Returns:
        Newline-separated list of file paths, or error message
    """
    try:
        if cwd is None:
            cwd = os.getcwd()
        target_dir = os.path.join(cwd, path)

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


def apply_patch_proposal(patch_str: str, create_diff_buffer_callback) -> str:
    """Proposes a patch to be applied. Creates a diff buffer for review.
    
    Args:
        patch_str: The patch content as a string
        create_diff_buffer_callback: Callback function to create the diff buffer
        
    Returns:
        Message indicating patch was proposed
    """
    try:
        create_diff_buffer_callback(patch_str)
        return "Patch proposed. Please review the 'AgentDiff' buffer and run :AgentApply to apply it."
    except Exception as e:
        return f"Error proposing patch: {e}"


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
            text=True
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
    with tempfile.NamedTemporaryFile(mode='w', suffix='.lua', delete=False) as f:
        temp_lua = f.name
        temp_result = temp_lua + '.result'
        
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
            nvim.command(f'luafile {temp_lua}')
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
            with open(temp_result, 'r') as f:
                result = json.load(f)
            os.unlink(temp_result)
            
            parts = []
            if result.get('output'):
                parts.append(result['output'])
            
            if result.get('ok'):
                if 'value' in result:
                    val = result['value']
                    try:
                        parts.append(f"=> {json.dumps(val, indent=2, default=str)}")
                    except (TypeError, ValueError):
                        parts.append(f"=> {repr(val)}")
            else:
                parts.append(f"Error: {result.get('error', 'unknown error')}")
            
            return '\n'.join(parts) if parts else "nil"
        else:
            return "Error: No result file created"
    except Exception as e:
        return f"Error reading result: {type(e).__name__}: {e}"
