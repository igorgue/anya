import os


def get_instructions(prompt_filename: str) -> str:
    """Read instructions from a file and return as a string."""
    prompt_path = _expand_file_path(os.path.join("prompts", prompt_filename))

    with open(prompt_path, "r") as file:
        instructions = file.read()

    return instructions


def _get_plugin_root() -> str:
    """Get the root directory of the plugin."""
    # This file is at: rplugin/python3/anya/agents/utils.py
    # Plugin root is 4 levels up
    return os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
    )


def _expand_file_path(file_path: str) -> str:
    """Expand file path relative to the plugin root.

    Handles:
    - Paths starting with ~ (user home directory)
    - Paths starting with $ (environment variables)
    - Relative paths (resolved from plugin root)
    - Absolute paths (returned as-is)
    """
    # First expand user and environment variables
    expanded = os.path.expandvars(os.path.expanduser(file_path))

    # If it's already absolute, return as-is
    if os.path.isabs(expanded):
        return expanded

    # Otherwise, resolve relative to plugin root
    return os.path.join(_get_plugin_root(), expanded)
