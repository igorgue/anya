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
    import concurrent.futures

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        state_future: concurrent.futures.Future = concurrent.futures.Future()

        def get_state():
            try:
                fold_open = nvim.eval("get(g:, 'anya_tool_fold_open', v:false)")
                # Get queue status from Lua
                queue_status = nvim.exec_lua(
                    "return require('anya.text').get_queue_status()"
                )
                state_future.set_result(
                    {
                        "fold_open": bool(fold_open),
                        "queue_length": queue_status.get("queue_length", 0),
                    }
                )
            except Exception:
                state_future.set_result({"fold_open": False, "queue_length": 0})

        nvim.async_call(get_state)

        # Wait for the async_call to complete (with mini timeout)
        wait_count = 0
        while not state_future.done() and wait_count < 10:
            await asyncio.sleep(0.01)
            wait_count += 1

        if state_future.done():
            state = state_future.result()
            # Wait until fold is closed AND queue is empty
            if not state["fold_open"] and state["queue_length"] == 0:
                return

        # Small delay before next poll
        await asyncio.sleep(0.02)

    # If we time out, just continue; better to show UI than hang
    return


async def _wait_for_edit_decision(nvim, edit_id: str, timeout: float = 300.0) -> dict:
    """Wait for user to apply or reject an edit.

    Polls vim.g.anya_edit_result_{id} for the user's decision.

    Returns:
        dict with {action: str, success: bool, message: str}
    """
    import concurrent.futures

    var_name = f"anya_edit_result_{edit_id}"

    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        result_future: concurrent.futures.Future = concurrent.futures.Future()

        def get_result():
            try:
                val = nvim.eval(f"get(g:, '{var_name}', v:null)")
                result_future.set_result(val)
            except Exception:
                result_future.set_result(None)

        nvim.async_call(get_result)

        # Wait for the async_call to complete
        wait_count = 0
        while not result_future.done() and wait_count < 20:
            await asyncio.sleep(0.01)
            wait_count += 1

        if result_future.done():
            result = result_future.result()
            if result is not None:
                # Clean up the global variable
                def cleanup():
                    try:
                        nvim.command(f"unlet g:{var_name}")
                    except Exception:
                        pass

                nvim.async_call(cleanup)
                return result

        # Small delay before next poll
        await asyncio.sleep(0.05)

    return {"action": "timeout", "success": False, "message": "Edit timed out"}


@function_tool(failure_error_function=create_error_handler)
async def edit(
    ctx: RunContextWrapper[NvimPluginContext],
    edit_blocks: str,
) -> str:
    """Make precise, reviewable code changes using SEARCH/REPLACE blocks.

    This tool is ideal for small, focused edits where you want the user to see
    exactly what changes before applying them. It requires exact matching and is
    best for surgical modifications.

    **ALTERNATIVE:** You can always use the write_file tool instead to replace
    the entire file content. Use write_file when:
    - Completely rewriting a file
    - Making many scattered changes across a large file
    - The code structure is unrecognizable or heavily changing
    - You prefer a simpler approach (write_file requires no exact matching)

    **When to use THIS tool (edit):**
    - Show the user exactly what will change before applying it
    - Make small, focused changes (fix a bug, add a line, update a variable)
    - Apply multiple specific edits in different locations within files
    - Make surgical changes while preserving the rest of the file intact

    **How it works:**
    Each edit block contains:
    - File path (on the line before <<<<<<<)
    - SEARCH section: the exact existing code to find
    - REPLACE section: the new code to insert

    The edit is displayed to the user and requires approval. You'll receive:
    - EDIT_APPLIED: Changes were accepted - continue with next steps
    - EDIT_REJECTED: User declined - ask what they want changed
    - EDIT_FAILED: Could not apply (match not found) - re-read the file and try again

    **Format:**
    ```
    path/to/file.py
    <<<<<<< SEARCH
    [exact existing code to replace]
    =======
    [new code to insert]
    >>>>>>> REPLACE
    ```

    **Example:**
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

    **Important rules:**
    - SEARCH must EXACTLY match existing code (including whitespace)
    - Include enough context lines to uniquely identify the location
    - Keep blocks small and focused on specific changes
    - Use multiple blocks for multiple independent changes
    - For creating new files: use write_file instead (or empty SEARCH section)

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

    # Parse into individual blocks and process ONE AT A TIME
    # This ensures each edit is confirmed before the next one is shown
    from ..search_replace import parse_search_replace_blocks

    parsed_blocks = parse_search_replace_blocks(edit_blocks)

    if not parsed_blocks:
        return "EDIT_FAILED: No valid SEARCH/REPLACE blocks found in the input."

    results = []
    for block in parsed_blocks:
        # Format single block for confirmation
        single_block = block.raw_block
        if not single_block.endswith("\n"):
            single_block += "\n"

        # Daemon mode - use edit_confirmation_callback
        if plugin_context.edit_confirmation_callback:
            result = await plugin_context.edit_confirmation_callback(
                single_block,
                yolo_mode,
            )
            results.append(result)
            # If user rejected or edit failed, stop processing
            if result.get("action") != "apply" or not result.get("success", False):
                break
        # Direct Neovim mode - use UI directly
        elif plugin_context.has_nvim:
            nvim = plugin_context.nvim
            result = await _handle_edit_with_nvim(nvim, single_block, yolo_mode)
            results.append(result)
            # If user rejected or edit failed, stop processing
            if result.get("action") != "apply" or not result.get("success", False):
                break
        else:
            # No way to handle edits
            raise Exception(
                "edit tool requires either direct Neovim access or daemon mode with "
                "edit_confirmation_callback. Neither is available."
            )

    # Combine results
    if not results:
        return "EDIT_FAILED: No edits were processed."

    # Return the last result (which determines overall success/failure)
    return _format_edit_result(results[-1])


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
