"""Run shell commands and interact with the GitHub CLI.

Usage:
    from anya.libs import shell

    out = shell.run("ls -la")
    out = shell.run("make test", cwd="/path/to/project", timeout=60)
    out = shell.gh("pr list --author @me")
"""

import os
import subprocess


def run(command: str, cwd: str | None = None, timeout: int = 120) -> str:
    """Execute a shell command and return its output.

    stdout is returned on success.  On failure both stdout and stderr are
    included together with the exit code so you can diagnose the problem.

    Args:
        command: Shell command string to execute.
        cwd: Working directory (default: os.getcwd()).
        timeout: Seconds before the command is killed (default 120).

    Returns:
        Command output as a string.
    """
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd or os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError(f"Command timed out after {timeout}s: {command!r}")

    if proc.returncode == 0:
        return stdout if stdout.strip() else "(command completed successfully)"

    parts = []
    if stdout:
        parts.append(f"STDOUT:\n{stdout.rstrip()}")
    if stderr:
        parts.append(f"STDERR:\n{stderr.rstrip()}")
    parts.append(f"Exit code: {proc.returncode}")
    return "\n".join(parts)


def gh(command: str, cwd: str | None = None, timeout: int = 120) -> str:
    """Execute a GitHub CLI (gh) command and return its output.

    Automatically prepends 'gh ' if the command does not already start with it.

    Args:
        command: gh sub-command and flags (e.g. "pr list --author @me").
        cwd: Working directory (default: os.getcwd()).
        timeout: Seconds before the command is killed (default 120).

    Returns:
        Command output as a string.
    """
    if not command.strip().startswith("gh"):
        command = f"gh {command}"
    return run(command, cwd=cwd, timeout=timeout)
