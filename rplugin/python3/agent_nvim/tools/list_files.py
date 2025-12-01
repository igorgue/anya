import os


def list_files(path: str = ".", cwd: str = None) -> str:
    """Lists files in a directory (recursive, respects gitignore if possible).

    Args:
        path: Directory path to list (default current directory)
        cwd: Current working directory for relative path resolution

    Returns:
        Newline-separated list of file paths, or error message
    """
    try:
        # Expand ~ to home directory
        path = os.path.expanduser(path)

        if cwd is None:
            cwd = os.getcwd()

        if not os.path.isabs(path):
            target_dir = os.path.join(cwd, path)
        else:
            target_dir = path

        # Check if directory exists
        if not os.path.exists(target_dir):
            return f"Error: Directory does not exist: {path}"

        if not os.path.isdir(target_dir):
            return f"Error: Path is not a directory: {path}"

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
