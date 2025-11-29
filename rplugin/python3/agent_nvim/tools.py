"""Tool implementations for agent.nvim plugin."""

import os
import subprocess

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
