import os
from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from .utils import create_error_handler
from .exec import _nvim_ui_select


@function_tool(failure_error_function=create_error_handler)
async def write_file(
    ctx: RunContextWrapper[NvimPluginContext],
    path: str,
    content: str,
) -> str:
    """Replace the entire content of a file.

    SAFETY: This tool requires user confirmation before replacing file content.
    Operations can be allowed for the current session.

    IMPORTANT: Use this tool to override existing files instead of trying to use the `create_file` tool that would return an error if the file already exists. This tool, doesn't do that intead it replaces the content of existing files.

    Args:
        path: File path to replace (supports ~ expansion and environment variables)
        content: New content to write to the file

    Returns:
        Success message with file path and size, or error message

    Examples:
        replace_file("~/config.txt", "new config content")
        replace_file("src/main.py", "# New file content\\n")
    """
    plugin_context = ctx.context

    # Expand ~ to home directory and environment variables
    path = os.path.expandvars(os.path.expanduser(path))

    # Resolve relative paths using context.cwd (from user's Neovim)
    if not os.path.isabs(path):
        cwd = plugin_context.cwd if plugin_context.cwd else os.getcwd()
        path = os.path.join(cwd, path)

    # Check if file exists
    if not os.path.exists(path):
        raise Exception(
            f"File {path} does not exist. Use the create_file tool to create new files."
        )

    # Check YOLO mode from context
    yolo_mode = plugin_context.yolo_mode

    # Generate a safe identifier for this file operation
    file_key = f"replace:{path}"

    # Check if this file replacement is already allowed in this session
    if file_key in plugin_context.allowed_commands:
        # Replace without asking
        pass
    elif yolo_mode:
        # YOLO mode: auto-allow and execute without asking
        plugin_context.allowed_commands.add(file_key)
    else:
        # Request user confirmation
        choice = None

        # Truncate content for preview if too long
        content_preview = content[:200]
        if len(content) > 200:
            content_preview += f"\n... ({len(content) - 200} more characters)"

        prompt = f"Replace file content?\n\n{path}\n\nNew content preview:\n{content_preview}"

        if plugin_context.has_nvim:
            # Direct Neovim access - use UI select
            nvim = plugin_context.nvim
            choice = await _nvim_ui_select(
                nvim,
                ["Replace", "Allow for this session", "Cancel"],
                prompt,
            )
        elif plugin_context.confirmation_callback:
            # Daemon mode with confirmation callback
            choice = await plugin_context.confirmation_callback(
                prompt,
                ["Replace", "Allow for this session", "Cancel"],
            )
        else:
            # No confirmation mechanism available - require YOLO mode
            raise Exception(
                f"File replacement for '{path}' requires user confirmation. "
                "Run in YOLO mode (set g:anya_yolo_mode=1) to auto-approve, "
                "or use direct Neovim mode."
            )

        if choice == "Allow for this session":
            # Add to allowed commands and execute
            plugin_context.allowed_commands.add(file_key)
        elif choice and choice != "Replace":
            raise Exception("File replacement cancelled by user")
        elif not choice:
            raise Exception("No response received from user confirmation")

    # Replace the file content
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        file_size = os.path.getsize(path)
        return f"Successfully replaced file: {path}\nSize: {file_size} bytes"
    except Exception as e:
        raise Exception(f"Failed to replace file {path}: {str(e)}")
