import asyncio

from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from .utils import create_error_handler


async def _wait_for_tool_folds_to_close(nvim, timeout: float = 300.0) -> None:
    """Wait until there are no open tool folds AND the streaming queue is empty.

    This ensures edit blocks are rendered after other tool outputs have completed,
    their folds have been closed, AND the streaming animation has finished writing
    all text to the buffer.
    """
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        state = [{"fold_open": False, "queue_length": 0}]

        def get_state():
            try:
                fold_open = nvim.eval("get(g:, 'anya_tool_fold_open', v:false)")
                # Get queue status from Lua
                queue_status = nvim.exec_lua(
                    "return require('anya.text').get_queue_status()"
                )
                state[0] = {
                    "fold_open": bool(fold_open),
                    "queue_length": queue_status.get("queue_length", 0),
                }
            except Exception:
                state[0] = {"fold_open": False, "queue_length": 0}

        nvim.async_call(get_state)
        await asyncio.sleep(0.05)

        # Wait until fold is closed AND queue is empty
        if not state[0]["fold_open"] and state[0]["queue_length"] == 0:
            return

    # If we time out, just continue; better to show UI than hang
    return


async def _wait_for_edit_decision(nvim, edit_id: str, timeout: float = 300.0) -> dict:
    """Wait for user to apply or reject an edit.

    Polls vim.g.anya_edit_result_{id} for the user's decision.

    Returns:
        dict with {action: str, success: bool, message: str}
    """
    var_name = f"anya_edit_result_{edit_id}"

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        result = [None]

        def get_result():
            try:
                result[0] = nvim.eval(f"get(g:, '{var_name}', v:null)")
            except Exception:
                pass

        nvim.async_call(get_result)
        await asyncio.sleep(0.1)

        if result[0] is not None:
            # Clean up the global variable
            def cleanup():
                try:
                    nvim.command(f"unlet g:{var_name}")
                except Exception:
                    pass

            nvim.async_call(cleanup)
            return result[0]

    return {"action": "timeout", "success": False, "message": "Edit timed out"}


@function_tool(failure_error_function=create_error_handler)
async def edit(
    ctx: RunContextWrapper[NvimPluginContext],
    edit_blocks: str,
) -> str:
    """Propose code edits using SEARCH/REPLACE blocks.

    Use this tool to make precise code modifications. Each edit block specifies:
    - The file path
    - A SEARCH section with the exact code to find
    - A REPLACE section with the new code

    The tool will display the edit to the user and wait for them to approve (1)
    or reject (2) it. You will receive one of these responses:
    - EDIT_APPLIED: The patch was successfully applied - continue with next steps
    - EDIT_REJECTED: The user rejected - ask what they want changed
    - EDIT_FAILED: The patch could not be applied - re-read the file and try again

    Example:
    ```
    src/utils.py
    <<<<<<< SEARCH
    def calculate_total(items):
        total = 0
        for item in items:
            total += item['price']
        return total
    =======
    def calculate_total(items):
        total = 0
        for item in items:
            total += item['price'] * item['quantity']
        return total
    >>>>>>> REPLACE
    ```

    Rules:
    - The SEARCH section must EXACTLY match existing code (including whitespace)
    - Include enough context lines to uniquely identify the location
    - Keep blocks small and focused on specific changes
    - Use multiple blocks for multiple changes
    - For new files: use empty SEARCH section

    Args:
        edit_blocks: String containing one or more SEARCH/REPLACE blocks

    Returns:
        Result of the edit operation (applied, rejected, or failed)
    """
    plugin_context = ctx.context

    # Clean up - remove outer markdown code fences if present
    edit_blocks = edit_blocks.strip()
    if edit_blocks.startswith("```") and not edit_blocks.startswith("<<<"):
        lines = edit_blocks.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        edit_blocks = "\n".join(lines)

    # Ensure trailing newline
    if not edit_blocks.endswith("\n"):
        edit_blocks += "\n"

    # Check YOLO mode from context
    yolo_mode = plugin_context.yolo_mode

    # Daemon mode - use edit_confirmation_callback
    if plugin_context.edit_confirmation_callback:
        result = await plugin_context.edit_confirmation_callback(
            edit_blocks,
            yolo_mode,
        )
        return _format_edit_result(result)

    # Direct Neovim mode - use UI directly
    if plugin_context.has_nvim:
        nvim = plugin_context.nvim
        result = await _handle_edit_with_nvim(nvim, edit_blocks, yolo_mode)
        return _format_edit_result(result)

    # No way to handle edits
    raise Exception(
        "edit tool requires either direct Neovim access or daemon mode with "
        "edit_confirmation_callback. Neither is available."
    )


def _format_edit_result(result: dict) -> str:
    """Format edit result dict into tool response string."""
    action = result.get("action", "timeout")
    success = result.get("success", False)
    message = result.get("message", "")

    if action == "apply" and success:
        return "EDIT_APPLIED: The SEARCH/REPLACE edits were successfully applied."
    elif action == "apply" and not success:
        return f"EDIT_FAILED: The edits could not be applied. {message}. Please read the target file again and regenerate the edit with correct content."
    elif action == "failed":
        return f"EDIT_FAILED: The edits could not be applied. {message}. Please read the target file again and regenerate the edit with correct content."
    elif action == "reject":
        return "EDIT_REJECTED: The user rejected the edits. Ask if they want changes or a different approach."
    elif action == "timeout":
        return "EDIT_TIMEOUT: The edit timed out waiting for user response."
    else:
        return f"EDIT_ERROR: Unexpected action '{action}'"


async def _handle_edit_with_nvim(nvim, edit_blocks: str, yolo_mode: bool) -> dict:
    """Handle edit using direct Neovim access."""
    import uuid

    edit_id = uuid.uuid4().hex[:8]

    # Wait for any other tool folds to close before rendering
    await _wait_for_tool_folds_to_close(nvim)

    # Render edit blocks in UI and wait for user decision
    def render_and_setup():
        # Get chat buffer
        chat_bufnr = None
        for buf in nvim.buffers:
            try:
                ft = nvim.api.buf_get_option(buf.number, "filetype")
                if ft == "anya-chat":
                    chat_bufnr = buf.number
                    break
            except Exception:
                pass

        if chat_bufnr is None:
            return

        # Render edit blocks
        nvim.call("AnyaRenderEditBlocks", chat_bufnr, edit_blocks)

        # Setup callback for when user makes decision
        nvim.exec_lua(
            f"""
            local edit_view = require('anya.edit_view')
            edit_view.set_decision_callback(function(action, success, message)
                vim.g.anya_edit_result_{edit_id} = {{
                    action = action,
                    success = success,
                    message = message
                }}
            end)
            """
        )

    nvim.async_call(render_and_setup)

    # If YOLO mode is enabled, auto-apply the edit
    if yolo_mode:
        await asyncio.sleep(0.2)

        def auto_apply():
            try:
                nvim.exec_lua(
                    """
                    local edit_view = require('anya.edit_view')
                    edit_view.handle_keypress_any_edit('1')
                    """
                )
            except Exception as e:
                nvim.exec_lua(
                    f"""
                    vim.g.anya_edit_result_{edit_id} = {{
                        action = "failed",
                        success = false,
                        message = "Error: {str(e)}"
                    }}
                    """
                )

        nvim.async_call(auto_apply)
        await asyncio.sleep(0.1)

    # Wait for user decision
    return await _wait_for_edit_decision(nvim, edit_id)
