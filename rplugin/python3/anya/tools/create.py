import os

from agents import function_tool

from .utils import create_error_handler


@function_tool(failure_error_function=create_error_handler)
async def create(path: str, content: str = "", cwd: str = None) -> str:
    """Creates a new file at the specified path with optional content.

    This tool creates a new file with the given content. If the file already exists,
    it will return an error. Use the edit tool to modify existing files.

    Args:
        path: File path where the new file should be created (supports ~ expansion, and environment variables)
        content: Optional content to write to the file (default: empty file)
        cwd: Current working directory for relative path resolution

    Returns:
        Success message with absolute path, or error message

    Examples:
        create("~/notes.txt", "Hello, World!")
        create("$PROJECT_DIR/main.py", "# Main application file\\n")
        create("src/new_module.py", "# New module\\n")
        create("config/settings.json", "{}")
        create("logs/app.log", "", "/var/www/myapp")
    """
    # Expand ~ to home directory and environment variables
    path = os.path.expandvars(os.path.expanduser(path))

    # Resolve relative paths
    if not os.path.isabs(path):
        if cwd is None:
            cwd = os.getcwd()
        path = os.path.join(cwd, path)

    # Check if file already exists
    if os.path.exists(path):
        raise Exception(
            f"File {path} already exists. Use the edit tool to modify existing files."
        )

    # Create parent directories if they don't exist
    parent_dir = os.path.dirname(path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    # Create the file with the specified content
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    file_size = os.path.getsize(path)
    return f"Successfully created file: {path}\nSize: {file_size} bytes"
