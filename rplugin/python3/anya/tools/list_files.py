import os
import shutil
import subprocess
from agents import function_tool

from .utils import create_error_handler


@function_tool(failure_error_function=create_error_handler)
async def list_files(path: str = ".", cwd: str = None, max_results: int = 100) -> str:
    """Lists files in a directory (recursive, respects gitignore if possible).

    Args:
        path: Directory path to list (default current directory)
        cwd: Current working directory for relative path resolution
        max_results: Maximum number of files to return (default 100)

    Returns:
        Newline-separated list of file paths, or error message
    """
    # Expand ~ to home directory and environment variables
    path = os.path.expandvars(os.path.expanduser(path))
    if cwd is None:
        cwd = os.getcwd()
    else:
        cwd = os.path.expandvars(os.path.expanduser(cwd))

    if not os.path.isabs(path):
        target_dir = os.path.join(cwd, path)
    else:
        target_dir = path

    # Check if directory exists
    if not os.path.exists(target_dir):
        raise Exception(f"Directory does not exist: {path}")

    if not os.path.isdir(target_dir):
        raise Exception(f"Path is not a directory: {path}")

    # Find fd executable
    fd_path = shutil.which("fd")
    if not fd_path:
        raise Exception(
            "fd not found. Please install fd: https://github.com/sharkdp/fd"
        )

    # Use fd to list files (respects .gitignore by default)
    result = subprocess.run(
        [fd_path, "--type", "f", ".", target_dir],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        lines = result.stdout.strip().split("\n")
        if len(lines) > max_results:
            output = (
                "\n".join(lines[:max_results])
                + f"\n... (truncated, showing {max_results} of {len(lines)} files)"
            )
        else:
            output = result.stdout.strip()
        return f"``````\n{output}\n``````"
    else:
        raise Exception(f"fd command failed: {result.stderr}")
