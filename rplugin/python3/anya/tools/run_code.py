import os
import tempfile
from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from .utils import create_error_handler


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


def _build_python_command(code: str, cwd: str, use_venv: bool) -> tuple[str, str]:
    """Build a shell command to run Python code, detecting virtualenv if needed.

    Returns:
        (command, script_path) - the shell command and temp script path.
    """
    script_fd, script_path = tempfile.mkstemp(suffix=".py", prefix="anya_run_")
    with os.fdopen(script_fd, "w") as f:
        f.write(code)

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


@function_tool(failure_error_function=create_error_handler)
async def run_code(
    ctx: RunContextWrapper[NvimPluginContext],
    title: str,
    code: str,
    cwd: str = None,
    use_venv: bool = True,
) -> str:
    """Runs Python code for the agent to do everything it needs.

    Args:
        ctx: The RunContextWrapper containing the plugin context.
        title: A title or description for the code being run (for logging purposes).
        code: Python code to execute
        cwd: Current working directory for code execution (default: current working directory)
        use_venv: Whether to detect and use virtualenv if available (default: True)

    Returns:
        str: The output of the code execution.
    """
    plugin_context = ctx.context

    if cwd is None:
        cwd = plugin_context.cwd if plugin_context.cwd else os.getcwd()

    command, script_path = _build_python_command(code, cwd, use_venv)

    try:
        if plugin_context.exec_callback:
            result = await plugin_context.exec_callback(command, cwd, 30)

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
            import asyncio

            process = await asyncio.create_subprocess_exec(
                *command.split(),
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=30.0
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            if process.returncode == 0:
                return stdout if stdout.strip() else "Code executed successfully."
            else:
                error_msg = stderr if stderr.strip() else f"Exit code: {process.returncode}"
                return f"Error executing code:\n{error_msg}"

        else:
            return "Error: No execution mechanism available. Cannot run code without client connection."

    except Exception as e:
        return f"Error executing code: {e}"
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
