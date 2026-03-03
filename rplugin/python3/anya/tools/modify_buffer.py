"""Tool for modifying the current Neovim buffer."""

import os
from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from ..utils import create_error_handler


@function_tool(failure_error_function=create_error_handler)
async def modify_buffer(
    ctx: RunContextWrapper[NvimPluginContext],
    content: str,
    mode: str = "replace",
) -> str:
    """Modify the current Neovim buffer content.
    
    This tool writes content directly to the current buffer in Neovim.
    Use this when the user wants Anya to modify the file they're currently editing
    based on the instruction provided.
    
    Args:
        ctx: The RunContextWrapper containing the plugin context.
        content: The content to write to the buffer.
        mode: How to modify the buffer:
            - "replace": Replace entire buffer content (default)
            - "append": Append to the end of the buffer
            - "prepend": Insert at the beginning of the buffer
    
    Returns:
        str: Success message or error description.
    """
    plugin_context = ctx.context
    
    # Get buffer info from context
    buf_path = plugin_context.current_buffer
    
    if not buf_path:
        return "Error: No current buffer found."
    
    if mode not in ("replace", "append", "prepend"):
        return f"Error: Unknown mode: {mode}. Use 'replace', 'append', or 'prepend'."
    
    # Check if we have the modify_buffer_callback (daemon mode)
    if plugin_context.modify_buffer_callback:
        result = await plugin_context.modify_buffer_callback(buf_path, content, mode)
        return result
    
    # Direct nvim mode
    if plugin_context.has_nvim and plugin_context.nvim:
        nvim = plugin_context.nvim
        try:
            # Find the buffer by name
            target_buf = None
            for buf in nvim.buffers:
                if buf.valid and buf.name == buf_path:
                    target_buf = buf
                    break
            
            if not target_buf:
                return f"Error: Buffer not found: {buf_path}"
            
            # Split content into lines
            lines = content.split("\n")
            
            # Make buffer modifiable
            was_modifiable = nvim.api.buf_get_option(target_buf.number, "modifiable")
            nvim.api.buf_set_option(target_buf.number, "modifiable", True)
            
            if mode == "replace":
                # Replace entire buffer
                nvim.api.buf_set_lines(target_buf.number, 0, -1, False, lines)
            elif mode == "append":
                # Append to end
                line_count = nvim.api.buf_line_count(target_buf.number)
                nvim.api.buf_set_lines(target_buf.number, line_count, line_count, False, lines)
            elif mode == "prepend":
                # Insert at beginning
                nvim.api.buf_set_lines(target_buf.number, 0, 0, False, lines)
            
            # Restore modifiable state
            nvim.api.buf_set_option(target_buf.number, "modifiable", was_modifiable)
            
            # Trigger checktime to update file status
            nvim.command("silent! checktime")
            
            return f"Successfully modified buffer: {os.path.basename(buf_path)}"
            
        except Exception as e:
            return f"Error modifying buffer: {e}"
    
    return "Error: No method available to modify buffer"
