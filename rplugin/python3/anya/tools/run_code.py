import asyncio
import hashlib
import os
import re
import tempfile
import uuid
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


def _build_python_command(code: str, cwd: str, use_venv: bool, extra_env: dict | None = None) -> tuple[str, str]:
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


async def _monitor_background_process(
    process_id: str,
    process: asyncio.subprocess.Process,
    command: str,
    cwd: str,
    script_path: str,
    output_file: str,
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
        
        # Update registry
        if process_id in _background_processes:
            _background_processes[process_id].update({
                "status": "completed" if process.returncode == 0 else "failed",
                "end_time": end_time,
                "returncode": process.returncode,
            })
    except Exception as e:
        if process_id in _background_processes:
            _background_processes[process_id].update({
                "status": "error",
                "error": str(e),
            })
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
    return [
        {"process_id": pid, **info}
        for pid, info in _background_processes.items()
    ]


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
    """Run exec_task while serving any ui.ask/ui.input requests from the subprocess.

    The subprocess writes  <id>.request.json  to ui_dir and waits for
    <id>.response.json.  We poll for request files at 50 ms intervals
    while the subprocess is running, dispatch them through
    confirmation_callback, and write response files back.

    Args:
        exec_task: Coroutine task for the exec_callback call.
        ui_dir: Path to the UI rendezvous directory.
        plugin_context: Agent context (for confirmation_callback access).

    Returns:
        The dict result from exec_task (stdout/stderr/returncode).
    """
    import glob
    import json as _json

    while not exec_task.done():
        await asyncio.sleep(0.05)
        await _serve_ui_requests(ui_dir, plugin_context)

    # Drain any final requests written just before the subprocess exited
    await _serve_ui_requests(ui_dir, plugin_context)
    return exec_task.result()


async def _run_with_ui_requests_nvim(
    comm_task: "asyncio.Task",
    ui_dir: str,
    plugin_context: "NvimPluginContext",
) -> tuple:
    """Like _run_with_ui_requests but for the has_nvim direct path.

    Returns the (stdout_bytes, stderr_bytes) tuple from process.communicate().
    """
    while not comm_task.done():
        await asyncio.sleep(0.05)
        await _serve_ui_requests(ui_dir, plugin_context)

    await _serve_ui_requests(ui_dir, plugin_context)
    return comm_task.result()


async def _serve_ui_requests(ui_dir: str, plugin_context: "NvimPluginContext"):
    """Scan ui_dir for pending request files and serve each one.

    Handles both "select" (vim.ui.select) and "input" (vim.ui.input) kinds.
    Each request file is removed after reading; the response is written to
    a matching .response.json file for the subprocess to pick up.
    """
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
                if plugin_context.confirmation_callback:
                    result = await plugin_context.confirmation_callback(prompt, options)
                elif plugin_context.has_nvim:
                    from ..utils import nvim_ui_select
                    result = await nvim_ui_select(plugin_context.nvim, options, prompt)
            elif kind == "input":
                default = req.get("default", "")
                if plugin_context.has_nvim:
                    result = await _nvim_ui_input(plugin_context.nvim, prompt, default)
                elif plugin_context.confirmation_callback:
                    # Daemon mode: present as a single-option select with a text field
                    # We send a special marker so plugin.py can render vim.ui.input
                    result = await plugin_context.confirmation_callback(
                        f"__input__:{default}:{prompt}", []
                    )
        except Exception:
            result = ""

        try:
            with open(response_file, "w") as f:
                _json.dump({"result": result}, f)
        except Exception:
            pass


async def _nvim_ui_input(nvim, prompt: str, default: str = "") -> str:
    """Ask user for free-form text using vim.ui.input (direct Neovim mode)."""
    lua_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")
    lua_default = default.replace('"', '\\"')

    result = [None]

    def run_input():
        nvim.exec_lua(
            f"""
vim.g.anya_input_result = nil
vim.ui.input(
    {{prompt = "{lua_prompt}", default = "{lua_default}"}},
    function(value)
        vim.g.anya_input_result = value or ""
    end)
"""
        )

    nvim.async_call(run_input)

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 300.0:
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

    return ""


@function_tool(failure_error_function=create_error_handler)
async def run_code(
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
            When True, returns immediately with a process ID. The process continues
            running and output is written to .anya/background/<process-id>.log

    Returns:
        str: The output of the code execution, or process ID if background=True.
    """
    plugin_context = ctx.context

    if cwd is None:
        cwd = plugin_context.cwd if plugin_context.cwd else os.getcwd()

    # Save code to project directory for later viewing
    _save_code_to_project(code, title, cwd)

    # Set up UI rendezvous directory for anya.libs.ui
    ui_dir = os.path.join(cwd, ".anya", "ui", str(uuid.uuid4())[:8])
    os.makedirs(ui_dir, exist_ok=True)

    command, script_path = _build_python_command(code, cwd, use_venv, extra_env={"ANYA_UI_DIR": ui_dir})

    try:
        # Background execution
        if background:
            process_id = str(uuid.uuid4())[:8]
            start_time = datetime.now().isoformat()
            
            # Create output file
            bg_dir = _get_background_dir(cwd)
            output_file = os.path.join(bg_dir, f"{process_id}.log")
            
            # Write initial info to output file
            with open(output_file, "w") as f:
                f.write(f"Process ID: {process_id}\n")
                f.write(f"Started: {start_time}\n")
                f.write(f"Title: {title}\n")
                f.write(f"CWD: {cwd}\n")
                f.write(f"Command: {command}\n")
                f.write("\n--- OUTPUT ---\n")
            
            if plugin_context.exec_callback:
                # Daemon mode - use background exec callback if available
                if hasattr(plugin_context, 'background_exec_callback') and plugin_context.background_exec_callback:
                    result = await plugin_context.background_exec_callback(
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
                    return f"Background process started. Process ID: {process_id}\nOutput file: {output_file}"
                else:
                    # Fallback: start via normal exec but tell it to run in background
                    # This requires the plugin to support background execution
                    result = await plugin_context.exec_callback(
                        f"nohup {command} > {output_file} 2>&1 & echo $!",
                        cwd,
                        5  # Short timeout just to launch
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
                    
                    return f"Background process started. Process ID: {process_id}\nOutput file: {output_file}"
            
            elif plugin_context.has_nvim:
                # Direct Neovim mode - spawn async process
                process = await asyncio.create_subprocess_exec(
                    *command.split(),
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                
                # Store in registry
                _background_processes[process_id] = {
                    "process": process,
                    "command": command,
                    "cwd": cwd,
                    "start_time": start_time,
                    "output_file": output_file,
                    "status": "running",
                    "title": title,
                }
                
                # Start monitoring task
                asyncio.create_task(
                    _monitor_background_process(
                        process_id, process, command, cwd, script_path, output_file
                    )
                )
                
                return f"Background process started. Process ID: {process_id}\nOutput file: {output_file}"
            
            else:
                return "Error: No execution mechanism available for background process."

        # Foreground execution (original behavior)
        if plugin_context.exec_callback:
            exec_task = asyncio.create_task(
                plugin_context.exec_callback(command, cwd, 30, ui_dir=ui_dir)
            )
            result = await _run_with_ui_requests(exec_task, ui_dir, plugin_context)

            if result.get("error"):
                return f"Error executing code:\n{result['error']}"

            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            returncode = result.get("returncode", 0)

            if returncode == 0:
                return stdout if stdout.strip() else "Code executed successfully."
            else:
                error_msg = stderr if stderr.strip() else f"Exit code: {returncode}"
                return f"Error executing code:\n{error_msg}"

        elif plugin_context.has_nvim:
            process = await asyncio.create_subprocess_exec(
                *command.split(),
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            comm_task = asyncio.create_task(
                asyncio.wait_for(process.communicate(), timeout=30.0)
            )
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
                return stdout if stdout.strip() else "Code executed successfully."
            else:
                error_msg = (
                    stderr if stderr.strip() else f"Exit code: {process.returncode}"
                )
                return f"Error executing code:\n{error_msg}"

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
