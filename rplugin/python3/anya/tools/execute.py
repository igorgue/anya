import asyncio
import hashlib
import os
import re
import tempfile
import uuid
import json
from datetime import datetime
from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from ..utils import create_error_handler

# Directory containing the `anya` package (rplugin/python3/).
# Injected into generated scripts so they can `from anya.libs import ...`.
_ANYA_PYTHON_PATH = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_ANYA_PATH_PREAMBLE = (
    f"import sys as _sys\n"
    f"if {repr(_ANYA_PYTHON_PATH)} not in _sys.path:\n"
    f"    _sys.path.insert(0, {repr(_ANYA_PYTHON_PATH)})\n"
    f"del _sys\n"
)

# Global registry for background processes
# Maps process_id -> {process, command, cwd, start_time, output_file, status}
_background_processes: dict[str, dict] = {}


def _sanitize_title(title: str) -> str:
    """Sanitize a title for use as a filename (lowercase, hyphens for non-alphanumeric)."""
    title = title.lower().strip()
    title = re.sub(r"[^a-z0-9]+", "-", title)
    title = title.strip("-")
    return title or "untitled"


def _save_code_to_project(code: str, title: str, cwd: str) -> str | None:
    """Save code to .anya/code/<sanitized-title>-<hash>.py in the project directory.

    The filename includes a short MD5 hash of the code content so identical
    code reuses the same file while different code produces a new file.

    Returns:
        Path to the saved file, or None on failure.
    """
    try:
        sanitized = _sanitize_title(title)
        code_hash = hashlib.md5(code.encode()).hexdigest()[:8]
        code_dir = os.path.join(cwd, ".anya", "code")
        os.makedirs(code_dir, exist_ok=True)
        file_path = os.path.join(code_dir, f"{sanitized}-{code_hash}.py")
        with open(file_path, "w") as f:
            f.write(code)
        return file_path
    except OSError:
        return None


def _save_output_to_project(output: str, code: str, title: str, cwd: str) -> str | None:
    """Save tool output to .anya/output/<sanitized-title>-<hash>.txt in the project directory.

    Uses the same MD5 hash as the code file so they can be correlated.

    Returns:
        Path to the saved file, or None on failure.
    """
    try:
        sanitized = _sanitize_title(title)
        code_hash = hashlib.md5(code.encode()).hexdigest()[:8]
        output_dir = os.path.join(cwd, ".anya", "output")
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, f"{sanitized}-{code_hash}.txt")
        with open(file_path, "w") as f:
            f.write(output)
        return file_path
    except OSError:
        return None


def _detect_virtualenv(cwd: str) -> str | None:
    """Detect the current virtualenv from environment or common locations.

    Returns:
        Path to the virtualenv directory, or None if not found.
    """
    venv_path = os.environ.get("VIRTUAL_ENV")
    if venv_path and os.path.exists(venv_path):
        return venv_path

    common_names = ["venv", ".venv", "env", ".env", "virtualenv"]
    for name in common_names:
        path = os.path.join(cwd, name)
        if os.path.exists(path):
            return path

    check_dir = cwd
    for _ in range(3):
        parent = os.path.dirname(check_dir)
        if parent == check_dir:
            break
        check_dir = parent
        for name in common_names:
            path = os.path.join(check_dir, name)
            if os.path.exists(path):
                return path

    return None


def _build_python_command(
    code: str, cwd: str, use_venv: bool, extra_env: dict | None = None
) -> tuple[str, str]:
    """Build a shell command to run Python code, detecting virtualenv if needed.

    Returns:
        (command, script_path) - the shell command and temp script path.
    """
    script_fd, script_path = tempfile.mkstemp(suffix=".py", prefix="anya_run_")
    with os.fdopen(script_fd, "w") as f:
        preamble = _ANYA_PATH_PREAMBLE
        if extra_env:
            env_lines = "import os as _os\n"
            for k, v in extra_env.items():
                env_lines += f"_os.environ[{repr(k)}] = {repr(v)}\n"
            env_lines += "del _os\n"
            preamble = env_lines + preamble
        f.write(preamble + "\n" + code)

    python_exe = "python3"
    if use_venv:
        venv_path = _detect_virtualenv(cwd)
        if venv_path:
            if os.name == "nt":
                candidate = os.path.join(venv_path, "Scripts", "python.exe")
            else:
                candidate = os.path.join(venv_path, "bin", "python")
            if os.path.exists(candidate):
                python_exe = candidate

    command = f"{python_exe} {script_path}"
    return command, script_path


def _get_background_dir(cwd: str) -> str:
    """Get the background process output directory."""
    bg_dir = os.path.join(cwd, ".anya", "background")
    os.makedirs(bg_dir, exist_ok=True)
    return bg_dir


def _write_job_meta(cwd: str, process_id: str, data: dict) -> None:
    """Write job metadata to .anya/background/<process_id>.meta.json."""
    bg_dir = _get_background_dir(cwd)
    meta_path = os.path.join(bg_dir, f"{process_id}.meta.json")
    data["updated_at"] = datetime.now().isoformat()
    # Write atomically via temp file
    fd, temp_path = tempfile.mkstemp(dir=bg_dir, prefix=".tmp_meta_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, meta_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _normalize_file_path(path: str) -> str:
    """Normalize a file path for reliable open-buffer matching."""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _resolve_open_buffer_path(
    target_path: str | None, plugin_context: NvimPluginContext
) -> str | None:
    """Resolve a requested path to an actual open buffer path from context."""
    requested = target_path or plugin_context.current_buffer
    if not requested:
        return None

    try:
        normalized_requested = _normalize_file_path(requested)
    except Exception:
        normalized_requested = requested

    current_buffer = plugin_context.current_buffer
    if current_buffer:
        try:
            if _normalize_file_path(current_buffer) == normalized_requested:
                return current_buffer
        except Exception:
            if current_buffer == requested:
                return current_buffer

    for buf in plugin_context.open_buffers:
        candidate = buf.get("path") or buf.get("name")
        if not candidate:
            continue
        try:
            if _normalize_file_path(candidate) == normalized_requested:
                return candidate
        except Exception:
            if candidate == requested:
                return candidate

    return None


async def _monitor_background_process(
    process_id: str,
    process: asyncio.subprocess.Process,
    command: str,
    cwd: str,
    script_path: str,
    output_file: str,
    title: str,
):
    """Monitor a background process and update its status when done."""
    try:
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        end_time = datetime.now().isoformat()

        # Write final output to file
        with open(output_file, "a") as f:
            f.write(f"\n--- PROCESS ENDED: {end_time} ---\n")
            f.write(f"Exit code: {process.returncode}\n")
            if stdout:
                f.write(f"\nSTDOUT:\n{stdout}\n")
            if stderr:
                f.write(f"\nSTDERR:\n{stderr}\n")

        # Determine final status
        final_status = "completed" if process.returncode == 0 else "failed"

        # Update in-memory registry
        if process_id in _background_processes:
            _background_processes[process_id].update(
                {
                    "status": final_status,
                    "end_time": end_time,
                    "returncode": process.returncode,
                }
            )

        # Write metadata file for persistence
        _write_job_meta(
            cwd,
            process_id,
            {
                "process_id": process_id,
                "title": title,
                "command": command,
                "cwd": cwd,
                "start_time": _background_processes.get(process_id, {}).get(
                    "start_time", ""
                ),
                "end_time": end_time,
                "status": final_status,
                "returncode": process.returncode,
                "pid": process.pid,
            },
        )

    except Exception as e:
        if process_id in _background_processes:
            _background_processes[process_id].update(
                {
                    "status": "error",
                    "error": str(e),
                }
            )
        # Write error metadata
        try:
            _write_job_meta(
                cwd,
                process_id,
                {
                    "process_id": process_id,
                    "title": title,
                    "command": command,
                    "cwd": cwd,
                    "start_time": _background_processes.get(process_id, {}).get(
                        "start_time", ""
                    ),
                    "end_time": datetime.now().isoformat(),
                    "status": "error",
                    "returncode": None,
                    "pid": getattr(process, "pid", None),
                    "error": str(e),
                },
            )
        except Exception:
            pass
    finally:
        # Clean up temp script
        try:
            os.unlink(script_path)
        except OSError:
            pass


def get_background_process_status(process_id: str) -> dict | None:
    """Get the status of a background process.

    Returns:
        Dict with process info, or None if not found.
    """
    return _background_processes.get(process_id)


def list_background_processes() -> list[dict]:
    """List all background processes."""
    return [{"process_id": pid, **info} for pid, info in _background_processes.items()]


def cleanup_completed_processes(max_age_hours: int = 24) -> int:
    """Remove completed processes older than max_age_hours from registry.

    Returns:
        Number of processes removed.
    """
    from datetime import datetime, timedelta

    to_remove = []
    cutoff = datetime.now() - timedelta(hours=max_age_hours)

    for pid, info in _background_processes.items():
        if info.get("status") in ("completed", "failed", "error"):
            end_time_str = info.get("end_time")
            if end_time_str:
                try:
                    end_time = datetime.fromisoformat(end_time_str)
                    if end_time < cutoff:
                        to_remove.append(pid)
                except ValueError:
                    pass

    for pid in to_remove:
        del _background_processes[pid]

    return len(to_remove)


async def _run_with_ui_requests(
    exec_task: "asyncio.Task[dict]",
    ui_dir: str,
    plugin_context: "NvimPluginContext",
) -> dict:
    """Run exec_task while serving side-channel requests from the subprocess."""
    while not exec_task.done():
        await asyncio.sleep(0.05)
        await _serve_ui_side_channel(ui_dir, plugin_context)

    await _serve_ui_side_channel(ui_dir, plugin_context)
    return exec_task.result()


async def _run_with_ui_requests_nvim(
    comm_task: "asyncio.Task",
    ui_dir: str,
    plugin_context: "NvimPluginContext",
) -> tuple:
    """Like _run_with_ui_requests but for the has_nvim direct path."""
    while not comm_task.done():
        await asyncio.sleep(0.05)
        await _serve_ui_side_channel(ui_dir, plugin_context)

    await _serve_ui_side_channel(ui_dir, plugin_context)
    return comm_task.result()


async def _serve_ui_side_channel(ui_dir: str, plugin_context: "NvimPluginContext"):
    """Serve request/response UI calls and fire-and-forget execute events."""
    await _serve_ui_requests(ui_dir, plugin_context)
    await _serve_ui_events(ui_dir, plugin_context)


async def _serve_ui_requests(ui_dir: str, plugin_context: "NvimPluginContext"):
    """Scan ui_dir for pending request files and serve each one."""
    import glob
    import json as _json

    pattern = os.path.join(ui_dir, "*.request.json")
    for request_file in glob.glob(pattern):
        try:
            with open(request_file) as f:
                req = _json.load(f)
            os.unlink(request_file)
        except Exception:
            continue

        request_id = req.get("id", "")
        kind = req.get("kind", "select")
        prompt = req.get("prompt", "")
        response_file = os.path.join(ui_dir, f"{request_id}.response.json")

        result = ""
        try:
            if kind == "select":
                options = req.get("options", [])
                if plugin_context.detached:
                    result = "Cancel"
                elif plugin_context.confirmation_callback:
                    result = await plugin_context.confirmation_callback(prompt, options)
                elif plugin_context.has_nvim:
                    from ..utils import nvim_ui_select

                    result = await nvim_ui_select(plugin_context.nvim, options, prompt)
            elif kind == "input":
                default = req.get("default", "")
                if plugin_context.detached:
                    result = ""
                elif plugin_context.has_nvim:
                    result = await _nvim_ui_input(plugin_context.nvim, prompt, default)
                elif plugin_context.confirmation_callback:
                    # Daemon mode: present as a single-option select with a text field
                    # We send a special marker so plugin.py can render vim.ui.input
                    result = await plugin_context.confirmation_callback(
                        f"__input__:{default}:{prompt}", []
                    )
            elif kind == "modify_buffer":
                # Handle buffer modification from anya.libs.buffer
                buf_content = req.get("content", "")
                buf_mode = req.get("mode", "replace")
                target_path = req.get("target_path") or req.get("buf_path")
                set_modified = req.get("set_modified", True)
                buf_path = _resolve_open_buffer_path(target_path, plugin_context)

                if not buf_path:
                    if target_path:
                        result = f"Error: Open buffer not found for path: {target_path}"
                    else:
                        result = "Error: No current buffer available"
                elif plugin_context.detached:
                    result = "Error: Cannot modify buffers after client disconnect"
                elif plugin_context.modify_buffer_callback:
                    # Daemon mode: use the callback to request modification
                    result = await plugin_context.modify_buffer_callback(
                        buf_path, buf_content, buf_mode, set_modified
                    )
                elif plugin_context.has_nvim and plugin_context.nvim:
                    # Direct nvim mode: modify buffer directly
                    result = await _nvim_modify_buffer(
                        plugin_context.nvim,
                        buf_path,
                        buf_content,
                        buf_mode,
                        set_modified,
                    )
                else:
                    result = "Error: No method available to modify buffer"
        except Exception as e:
            result = f"Error: {e}"

        try:
            with open(response_file, "w") as f:
                _json.dump({"result": result}, f)
        except Exception:
            pass


async def _serve_ui_events(ui_dir: str, plugin_context: "NvimPluginContext"):
    """Scan ui_dir for fire-and-forget event files emitted by execute libs."""
    import glob
    import json as _json

    pattern = os.path.join(ui_dir, "*.event.json")
    for event_file in glob.glob(pattern):
        try:
            with open(event_file) as f:
                event = _json.load(f)
        except Exception:
            continue

        try:
            kind = event.get("kind", "")
            if kind == "task_list_update":
                payload = {
                    "title": event.get("title", ""),
                    "items": event.get("items", []),
                }
                if plugin_context.task_list_callback:
                    await plugin_context.task_list_callback(payload)
                elif plugin_context.has_nvim and plugin_context.nvim:
                    await _nvim_task_list_update(
                        plugin_context.nvim, payload["title"], payload["items"]
                    )
            elif kind == "notify":
                msg = event.get("message", "")
                level = event.get("level", "info")
                title = event.get("title", "Anya")
                if plugin_context.has_nvim and plugin_context.nvim:
                    level_map = {
                        "info": "vim.log.levels.INFO",
                        "warn": "vim.log.levels.WARN",
                        "error": "vim.log.levels.ERROR",
                    }
                    lvl = level_map.get(level, "vim.log.levels.INFO")
                    plugin_context.nvim.exec_lua(
                        "vim.notify(..., %s, {title = ...})" % lvl,
                        msg,
                        title,
                    )
        finally:
            try:
                os.unlink(event_file)
            except OSError:
                pass


def _replace_lines_with_diff(nvim_api, buf_number, current_lines, new_lines):
    """Replace buffer content with minimal edit by finding common prefix/suffix.

    Instead of replacing every line, this finds the longest matching prefix
    and suffix between current and new content, then only replaces the
    changed region in the middle.
    """
    # Find common prefix length
    prefix_len = 0
    for i in range(min(len(current_lines), len(new_lines))):
        if current_lines[i] == new_lines[i]:
            prefix_len = i + 1
        else:
            break

    # Find common suffix length
    suffix_len = 0
    current_end = len(current_lines)
    new_end = len(new_lines)
    for i in range(min(current_end - prefix_len, new_end - prefix_len)):
        if current_lines[current_end - 1 - i] == new_lines[new_end - 1 - i]:
            suffix_len = i + 1
        else:
            break

    # Replace only the changed region
    start = prefix_len
    end_current = current_end - suffix_len
    replacement = new_lines[prefix_len : new_end - suffix_len]
    nvim_api.buf_set_lines(buf_number, start, end_current, False, replacement)


async def _nvim_modify_buffer(
    nvim,
    buf_path: str,
    content: str,
    mode: str,
    set_modified: bool = True,
) -> str:
    """Modify a Neovim buffer directly (direct nvim mode)."""
    import os

    result_container = [None]

    def apply_modification():
        try:
            # Find the buffer by name
            target_buf = None
            for buf in nvim.buffers:
                if buf.valid and buf.name == buf_path:
                    target_buf = buf
                    break

            if not target_buf:
                result_container[0] = f"Error: Buffer not found: {buf_path}"
                return

            lines = content.split("\n")
            was_modifiable = nvim.api.buf_get_option(target_buf.number, "modifiable")
            nvim.api.buf_set_option(target_buf.number, "modifiable", True)

            if mode == "replace":
                current = nvim.api.buf_get_lines(target_buf.number, 0, -1, False)
                _replace_lines_with_diff(nvim.api, target_buf.number, current, lines)
            elif mode == "append":
                lc = nvim.api.buf_line_count(target_buf.number)
                nvim.api.buf_set_lines(target_buf.number, lc, lc, False, lines)
            elif mode == "prepend":
                nvim.api.buf_set_lines(target_buf.number, 0, 0, False, lines)

            nvim.api.buf_set_option(target_buf.number, "modifiable", was_modifiable)
            nvim.api.buf_set_option(target_buf.number, "modified", set_modified)
            result_container[0] = (
                f"Successfully modified buffer: {os.path.basename(buf_path)}"
            )
        except Exception as e:
            result_container[0] = f"Error: {e}"

    nvim.async_call(apply_modification)

    # Wait for the async call to complete
    for _ in range(100):
        await asyncio.sleep(0.05)
        if result_container[0] is not None:
            break

    return result_container[0] or "Error: timeout"


async def _nvim_task_list_update(nvim, title: str, items: list[dict]) -> str:
    """Show the task-list snapshot as a Neovim notification (direct execute mode)."""
    result = [None]

    def apply_update():
        try:
            from .. import ui as _ui

            _ui.notify_task_list(nvim, title, items)
            result[0] = "ok"
        except Exception as e:
            result[0] = f"Error: {e}"

    nvim.async_call(apply_update)

    for _ in range(100):
        await asyncio.sleep(0.05)
        if result[0] is not None:
            break

    return result[0] or "Error: timeout"


async def _nvim_ui_input(nvim, prompt: str, default: str = "") -> str:
    """Ask user for free-form text using vim.ui.input (direct Neovim mode)."""
    lua_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")
    lua_default = default.replace('"', '\\"')

    result = [None]

    def run_input():
        nvim.exec_lua(
            f"""
vim.g.anya_input_result = nil
pcall(function() require('anya.text').pause_queue() end)
local _ok, _err = pcall(function()
  vim.ui.input(
      {{prompt = "{lua_prompt}", default = "{lua_default}"}},
      function(value)
          vim.g.anya_input_result = value or ""
          pcall(function() require('anya.text').resume_queue() end)
      end)
end)
if not _ok then
  pcall(function() require('anya.text').resume_queue() end)
  vim.g.anya_input_result = ""
end
"""
        )

    nvim.async_call(run_input)

    while True:

        def get_result():
            try:
                val = nvim.eval("get(g:, 'anya_input_result', v:null)")
                if val is not None and val != "v:null" and val != "null":
                    result[0] = str(val)
            except Exception:
                pass

        nvim.async_call(get_result)
        await asyncio.sleep(0.1)

        if result[0] is not None:
            return result[0]


@function_tool(failure_error_function=create_error_handler)
async def execute(
    ctx: RunContextWrapper[NvimPluginContext],
    title: str,
    code: str,
    cwd: str = None,
    use_venv: bool = True,
    background: bool = False,
) -> str:
    """Runs Python code for the agent to do everything it needs.

    Args:
        ctx: The RunContextWrapper containing the plugin context.
        title: A title or description for the code being run (for logging purposes), use lowercase.
        code: Python code to execute
        cwd: Current working directory for code execution (default: current working directory)
        use_venv: Whether to detect and use virtualenv if available (default: True)
        background: Run the code in the background without blocking (default: False).
            Use this for servers, watchers, and any command that may run indefinitely.
            When True, returns immediately with a process ID. The process continues
            running and output is written to .anya/background/<process-id>.log.
            Do not use shell backgrounding (`&`, `nohup`, `disown`, `setsid`,
            `tmux`, or `screen`) inside the code; keep commands foreground-style
            and set this argument to True so Anya can track logs, status, and stops.

    Returns:
        str: The output of the code execution, or process ID if background=True.
            Use `from anya.libs import background` to monitor background jobs:
            - background.list_jobs() - list all jobs
            - background.tail_logs(process_id) - get last N lines of output
            - background.is_running(process_id) - check if job is still running
            - background.stop_job(process_id) - stop a running job
    """
    plugin_context = ctx.context

    if re.search(
        r"from\s+anya\.libs\s+import\s+[^\n]*\bmemory\b|import\s+anya\.libs\.memory\b",
        code,
    ):
        return (
            "The memory library is internal and not available from execute(). "
            "Relevant memories are injected automatically before each run."
        )

    if cwd is None:
        cwd = plugin_context.cwd if plugin_context.cwd else os.getcwd()

    # Save code to project directory for later viewing.
    # Always use the Neovim cwd (plugin_context.cwd) so the Lua-side
    # open_code_at_cursor (which globs under vim.fn.getcwd()) can find it,
    # even when the tool receives a different `cwd` for execution.
    save_cwd = plugin_context.cwd if plugin_context.cwd else cwd
    _save_code_to_project(code, title, save_cwd)

    # Set up UI rendezvous directory for anya.libs.ui
    ui_dir = os.path.join(cwd, ".anya", "ui", str(uuid.uuid4())[:8])
    os.makedirs(ui_dir, exist_ok=True)

    extra_env = {
        "ANYA_UI_DIR": ui_dir,
        "ANYA_CURRENT_BUFFER": plugin_context.current_buffer or "",
        "ANYA_OPEN_BUFFERS": json.dumps(plugin_context.open_buffers),
    }

    command, script_path = _build_python_command(
        code, cwd, use_venv, extra_env=extra_env
    )

    try:
        # Background execution
        if background:
            process_id = str(uuid.uuid4())[:8]
            start_time = datetime.now().isoformat()

            bg_dir = _get_background_dir(cwd)
            output_file = os.path.join(bg_dir, f"{process_id}.log")

            with open(output_file, "w") as f:
                f.write(f"Process ID: {process_id}\n")
                f.write(f"Started: {start_time}\n")
                f.write(f"Title: {title}\n")
                f.write(f"CWD: {cwd}\n")
                f.write(f"Command: {command}\n")
                f.write("\n--- OUTPUT ---\n")

            if plugin_context.exec_callback:
                if (
                    hasattr(plugin_context, "background_exec_callback")
                    and plugin_context.background_exec_callback
                ):
                    await plugin_context.background_exec_callback(
                        command, cwd, process_id, output_file
                    )
                    _background_processes[process_id] = {
                        "command": command,
                        "cwd": cwd,
                        "start_time": start_time,
                        "output_file": output_file,
                        "status": "running",
                        "title": title,
                    }
                    _write_job_meta(
                        cwd,
                        process_id,
                        {
                            "process_id": process_id,
                            "title": title,
                            "command": command,
                            "cwd": cwd,
                            "start_time": start_time,
                            "end_time": None,
                            "status": "running",
                            "returncode": None,
                            "pid": None,
                        },
                    )
                    return f"Background process started. Process ID: {process_id}\nOutput file: {output_file}"
                else:
                    result = await plugin_context.exec_callback(
                        f"nohup {command} > {output_file} 2>&1 & echo $!",
                        cwd,
                        5,
                    )

                    if result.get("error"):
                        return f"Error starting background process:\n{result['error']}"

                    _background_processes[process_id] = {
                        "command": command,
                        "cwd": cwd,
                        "start_time": start_time,
                        "output_file": output_file,
                        "status": "running",
                        "title": title,
                    }
                    _write_job_meta(
                        cwd,
                        process_id,
                        {
                            "process_id": process_id,
                            "title": title,
                            "command": command,
                            "cwd": cwd,
                            "start_time": start_time,
                            "end_time": None,
                            "status": "running",
                            "returncode": None,
                            "pid": None,
                        },
                    )
                    return f"Background process started. Process ID: {process_id}\nOutput file: {output_file}"

            elif plugin_context.has_nvim:
                process = await asyncio.create_subprocess_exec(
                    *command.split(),
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                _background_processes[process_id] = {
                    "process": process,
                    "command": command,
                    "cwd": cwd,
                    "start_time": start_time,
                    "output_file": output_file,
                    "status": "running",
                    "title": title,
                }
                _write_job_meta(
                    cwd,
                    process_id,
                    {
                        "process_id": process_id,
                        "title": title,
                        "command": command,
                        "cwd": cwd,
                        "start_time": start_time,
                        "end_time": None,
                        "status": "running",
                        "returncode": None,
                        "pid": process.pid,
                    },
                )

                asyncio.create_task(
                    _monitor_background_process(
                        process_id,
                        process,
                        command,
                        cwd,
                        script_path,
                        output_file,
                        title,
                    )
                )

                return f"Background process started. Process ID: {process_id}\nOutput file: {output_file}"

            else:
                return "Error: No execution mechanism available for background process."

        # Foreground execution (original behavior)
        if plugin_context.detached and not background:
            return "Error: Client disconnected. Foreground execute is unavailable; finish with a best-effort assistant reply without further interaction."

        if plugin_context.exec_callback:
            exec_task = asyncio.create_task(
                plugin_context.exec_callback(command, cwd, 600, ui_dir=ui_dir)
            )
            result = await _run_with_ui_requests(exec_task, ui_dir, plugin_context)

            if result.get("error"):
                _save_output_to_project(result["error"], code, title, save_cwd)
                return f"Error executing code:\n{result['error']}"

            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            returncode = result.get("returncode", 0)

            if returncode == 0:
                output = stdout if stdout.strip() else "Code executed successfully."
                _save_output_to_project(output, code, title, save_cwd)
                return output
            else:
                error_msg = stderr if stderr.strip() else f"Exit code: {returncode}"
                output = f"Error executing code:\n{error_msg}"
                _save_output_to_project(output, code, title, save_cwd)
                return output

        elif plugin_context.has_nvim:
            process = await asyncio.create_subprocess_exec(
                *command.split(),
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            comm_task = asyncio.create_task(process.communicate())
            stdout_bytes, stderr_bytes = await _run_with_ui_requests_nvim(
                comm_task, ui_dir, plugin_context
            )
            stdout = (
                stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            )
            stderr = (
                stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            )

            if process.returncode == 0:
                output = stdout if stdout.strip() else "Code executed successfully."
                _save_output_to_project(output, code, title, save_cwd)
                return output
            else:
                error_msg = (
                    stderr if stderr.strip() else f"Exit code: {process.returncode}"
                )
                output = f"Error executing code:\n{error_msg}"
                _save_output_to_project(output, code, title, save_cwd)
                return output

        else:
            return "Error: No execution mechanism available. Cannot run code without client connection."

    except Exception as e:
        return f"Error executing code: {e}"
    finally:
        # Only clean up script for foreground execution
        # Background execution cleans up in _monitor_background_process
        if not background:
            try:
                os.unlink(script_path)
            except OSError:
                pass
            try:
                import shutil as _shutil

                _shutil.rmtree(ui_dir, ignore_errors=True)
            except Exception:
                pass
