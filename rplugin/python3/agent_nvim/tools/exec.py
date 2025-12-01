import os
import subprocess


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
