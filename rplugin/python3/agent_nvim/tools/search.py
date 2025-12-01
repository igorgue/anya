import os
import subprocess


def search(query: str, cwd: str = None) -> str:
    """Searches the project for a string using grep/ripgrep.

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
        return f"Error searching: {e}"
