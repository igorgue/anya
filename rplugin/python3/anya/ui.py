"""UI interaction functions for Anya."""

import asyncio
import concurrent.futures
from . import buffers
from . import markers


def get_chat_buffer(nvim):
    """Find the chat buffer by filetype."""
    for buf in nvim.buffers:
        if buf.valid:
            ft = nvim.api.buf_get_option(buf, "filetype")
            if ft == "anya-chat":
                return buf
    return None


async def get_buffer_content_async(nvim, bufnr: int) -> str:
    """Get buffer content from async context using a future."""
    future: concurrent.futures.Future[str] = concurrent.futures.Future()

    def get_content():
        content = buffers.get_buffer_content(nvim, bufnr)
        future.set_result(content)

    nvim.async_call(get_content)

    while not future.done():
        await asyncio.sleep(0.01)

    return future.result()


def append_to_chat_buffer(nvim, bufnr, text):
    """Append text to the chat buffer (sync, instant)."""
    if not nvim.api.buf_is_valid(bufnr):
        return
    nvim.api.buf_set_option(bufnr, "modifiable", True)
    lines = text.split("\n")
    line_count = nvim.api.buf_line_count(bufnr)
    last_line = nvim.api.buf_get_lines(
        bufnr, line_count - 1, line_count, False
    )
    last_col = len(last_line[0]) if last_line else 0
    nvim.api.buf_set_text(
        bufnr, line_count - 1, last_col, line_count - 1, last_col, lines
    )
    autoscroll(nvim, bufnr)


def stream_text_to_buffer(nvim, bufnr, text):
    """Stream text to buffer using Lua animation."""
    if not nvim.api.buf_is_valid(bufnr):
        return
    nvim.exec_lua("require('anya.text').output(...)", bufnr, text)


def stream_text_to_buffer_sync(nvim, bufnr, text):
    """Output text to buffer immediately without animation."""
    if not nvim.api.buf_is_valid(bufnr):
        return
    nvim.exec_lua("require('anya.text').output_sync(...)", bufnr, text)


def autoscroll(nvim, bufnr):
    """Scroll all windows showing buffer to bottom."""
    for win in nvim.api.list_wins():
        if nvim.api.win_get_buf(win) == bufnr:
            line_count = nvim.api.buf_line_count(bufnr)
            try:
                nvim.api.win_set_cursor(win, [line_count, 0])
            except Exception:
                pass


def ensure_blank_line_before_tool(nvim, bufnr):
    """Ensure there's a blank line before tool output.

    Flushes the streaming queue and adds a blank line if the last
    line has content.
    """
    if not nvim.api.buf_is_valid(bufnr):
        return
    # Flush the streaming queue first so we can check actual buffer state
    nvim.exec_lua("require('anya.text').flush_queue()")
    # Check if last line has content
    line_count = nvim.api.buf_line_count(bufnr)
    if line_count > 0:
        last_line = nvim.api.buf_get_lines(
            bufnr, line_count - 1, line_count, False
        )
        if last_line and last_line[0] != "":
            # Add blank line
            nvim.api.buf_set_lines(bufnr, line_count, line_count, False, [""])


def update_tool_header_line(nvim, bufnr, new_header: str):
    """Update the most recent tool header line with a new combined header.

    Finds the last line containing a tool header (starting with **) and
    replaces it with the new combined header.
    """
    if not nvim.api.buf_is_valid(bufnr):
        return

    nvim.exec_lua("require('anya.text').flush_queue()")

    lines = nvim.api.buf_get_lines(bufnr, 0, -1, False)
    # Scan backwards
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("**") and lines[i].endswith("**"):
            nvim.api.buf_set_lines(bufnr, i, i + 1, False, [new_header])
            process_markers(nvim, bufnr)
            break


def process_markers(nvim, bufnr):
    """Process markers in the buffer via Lua."""
    if not nvim.api.buf_is_valid(bufnr):
        return
    nvim.exec_lua("require('anya.text')._process_markers(...)", bufnr)


def flush_queue(nvim):
    """Flush the streaming queue."""
    nvim.exec_lua("require('anya.text').flush_queue()")


def update_pending_markers_to_success(nvim, bufnr):
    """Update all tool_pending markers to tool_success in the buffer.
    
    Also flushes queue first.
    """
    flush_queue(nvim)
    
    if not nvim.api.buf_is_valid(bufnr):
        return

    lines = nvim.api.buf_get_lines(bufnr, 0, -1, False)
    pending_marker = markers.make_marker("fold_start", "tool_pending")
    success_marker = markers.make_marker("fold_start", "tool_success")

    for i, line in enumerate(lines):
        if line == pending_marker:
            nvim.api.buf_set_lines(bufnr, i, i + 1, False, [success_marker])

    # Reprocess markers to update extmarks
    process_markers(nvim, bufnr)


def update_pending_markers_to_failure(nvim, bufnr):
    """Update all tool_pending markers to tool_failure in the buffer.
    
    Also flushes queue first.
    """
    flush_queue(nvim)

    if not nvim.api.buf_is_valid(bufnr):
        return

    lines = nvim.api.buf_get_lines(bufnr, 0, -1, False)
    pending_marker = markers.make_marker("fold_start", "tool_pending")
    failure_marker = markers.make_marker("fold_start", "tool_failure")

    for i, line in enumerate(lines):
        if line == pending_marker:
            nvim.api.buf_set_lines(bufnr, i, i + 1, False, [failure_marker])

    # Reprocess markers to update extmarks
    process_markers(nvim, bufnr)


def render_edit_blocks(nvim, bufnr, edit_str):
    """Render SEARCH/REPLACE edit blocks using the dedicated edit_view.

    Args:
        nvim: Neovim instance
        bufnr: Buffer number
        edit_str: String containing one or more SEARCH/REPLACE blocks
    """
    if not nvim.api.buf_is_valid(bufnr):
        return

    from . import search_replace

    blocks = search_replace.parse_search_replace_blocks(edit_str)

    if not blocks:
        return

    for block in blocks:
        try:
            nvim.exec_lua(
                """
                local args = {...}
                require('anya.edit_view').render_edit(
                    args[1], args[2], args[3], args[4], args[5]
                )
                """,
                bufnr,
                block.path,
                block.search,
                block.replace,
                block.raw_block,
            )
        except Exception as e:
            nvim.err_write(f"Failed to render edit block: {e}\n")

    # Setup keymaps after rendering
    nvim.exec_lua(
        "require('anya.edit_view').setup_keymaps(...)",
        bufnr,
    )

    # Autoscroll to show the edit blocks
    autoscroll(nvim, bufnr)


async def get_open_buffers_context_async(nvim) -> str:
    """Get context about open buffers via Lua for performance (async-safe).

    Collects visible buffers and other open buffers with their current cursor
    positions (line numbers) to provide context to the LLM.
    """
    lua_code = """
    local visible_lines = {}
    local other_lines = {}
    local processed_bufs = {}
    
    -- Visible buffers
    local wins = vim.api.nvim_tabpage_list_wins(0)
    for _, win in ipairs(wins) do
        local buf = vim.api.nvim_win_get_buf(win)
        -- Only process if not already processed (deduplicate by buffer)
        if not processed_bufs[buf] then
            local ft = vim.api.nvim_get_option_value("filetype", {buf=buf})
            local buftype = vim.api.nvim_get_option_value("buftype", {buf=buf})
            local name = vim.api.nvim_buf_get_name(buf)
            
            if name ~= "" and buftype == "" and ft ~= "anya-chat" and ft ~= "anya-prompt" then
                processed_bufs[buf] = true
                local cursor = vim.api.nvim_win_get_cursor(win)
                local row = cursor[1] -- 1-indexed cursor position
                local rel_path = vim.fn.fnamemodify(name, ":.")
                
                -- Get snippet (approx 20 lines surrounding cursor)
                local line_count = vim.api.nvim_buf_line_count(buf)
                -- start_line is 0-indexed for nvim_buf_get_lines
                local start_line = math.max(0, row - 11)
                local end_line_exclusive = math.min(line_count, row + 10)
                
                local lines = vim.api.nvim_buf_get_lines(buf, start_line, end_line_exclusive, false)
                
                -- Format lines with line numbers
                local formatted_lines = {}
                for i, line in ipairs(lines) do
                    local line_num = start_line + i -- 1-indexed line number
                    local prefix = (line_num == row) and ">" or " "
                    table.insert(formatted_lines, string.format("%s %4d | %s", prefix, line_num, line))
                end
                
                local snippet = table.concat(formatted_lines, "\\n")
                local start_display = start_line + 1
                local end_display = start_line + #lines
                
                table.insert(visible_lines, string.format("- @%s lines %d-%d (cursor at %d)\\n\\n```%s\\n%s\\n```", rel_path, start_display, end_display, row, ft, snippet))
            end
        end
    end
    
    -- Other buffers
    local bufs = vim.api.nvim_list_bufs()
    for _, buf in ipairs(bufs) do
        if not processed_bufs[buf] and vim.api.nvim_buf_is_valid(buf) and vim.api.nvim_get_option_value("buflisted", {buf=buf}) then
            local ft = vim.api.nvim_get_option_value("filetype", {buf=buf})
            local buftype = vim.api.nvim_get_option_value("buftype", {buf=buf})
            local name = vim.api.nvim_buf_get_name(buf)
            
            if name ~= "" and buftype == "" and ft ~= "anya-chat" and ft ~= "anya-prompt" then
                processed_bufs[buf] = true
                local mark = vim.api.nvim_buf_get_mark(buf, '"')
                local row = mark[1]
                if row < 1 then row = 1 end
                local rel_path = vim.fn.fnamemodify(name, ":.")
                table.insert(other_lines, string.format("- @%s line %d", rel_path, row))
            end
        end
    end
    
    return {visible_lines, other_lines}
    """

    future: concurrent.futures.Future[str] = concurrent.futures.Future()

    def run_on_main():
        try:
            visible, other = nvim.exec_lua(lua_code, [])

            parts = []
            if visible:
                parts.append("Visible buffers:\n\n" + "\n\n".join(visible))
            if other:
                parts.append("Other buffers:\n\n" + "\n".join(other))

            if not parts:
                future.set_result("")
                return

            result = "```\n" + "\n\n".join(parts) + "\n```\n\n"
            future.set_result(result)
        except Exception as e:
            # nvim.err_write(f"Error getting buffer context: {e}\n")
            future.set_result("")

    nvim.async_call(run_on_main)

    while not future.done():
        await asyncio.sleep(0.01)

    return future.result()


def is_anya_open(nvim) -> bool:
    """Check if Anya chat or prompt windows are currently open.

    Returns:
        True if any Anya window is open
    """
    for win in nvim.api.list_wins():
        try:
            if not nvim.api.win_is_valid(win):
                continue
            buf = nvim.api.win_get_buf(win)
            ft = nvim.api.buf_get_option(buf, "filetype")
            if ft in ("anya-chat", "anya-prompt"):
                return True
        except Exception:
            continue
    return False


def is_anya_pane_open(nvim, last_layout: str) -> bool:
    """Check if Anya is currently open as a pane (not floating/tab).

    Args:
        nvim: Neovim instance
        last_layout: The last used layout ("pane", "float", etc)

    Returns:
        True if Anya is open as a pane
    """
    # If last layout was pane and Anya is open, it's a pane
    return last_layout == "pane" and is_anya_open(nvim)
