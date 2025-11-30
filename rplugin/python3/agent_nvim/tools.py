"""Tool implementations for agent.nvim plugin."""

import asyncio
import json
import os
import subprocess
import tempfile

# File reading limits (configurable via environment)
MAX_READ_BYTES = int(os.environ.get("AGENT_MAX_READ_BYTES", 64000))  # ~16k tokens


def read_file(path: str, cwd: str = None) -> str:
    """Reads the content of a file, with truncation for very large files.
    
    Args:
        path: Path to the file (absolute or relative)
        cwd: Current working directory for relative path resolution
        
    Returns:
        File content as string (truncated if large), or error message
    """
    try:
        if not os.path.isabs(path):
            if cwd is None:
                cwd = os.getcwd()
            path = os.path.join(cwd, path)

        if not os.path.exists(path):
            return f"Error: File {path} does not exist."

        size = os.path.getsize(path)
        truncated = size > MAX_READ_BYTES

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_READ_BYTES)

        if truncated:
            return (
                f"NOTE: File {path} is large ({size} bytes). "
                f"Only the first {MAX_READ_BYTES} bytes were read.\n"
                f"--- FILE START ---\n"
                f"{content}\n"
                f"--- FILE TRUNCATED ---\n"
                f"To inspect other parts, focus on specific sections or "
                f"relevant regions instead of the whole file."
            )
        return content
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
