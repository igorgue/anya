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


def get_prompt_buffer(nvim):
    """Find the prompt buffer by filetype."""
    for buf in nvim.buffers:
        if buf.valid:
            ft = nvim.api.buf_get_option(buf, "filetype")
            if ft == "anya-prompt":
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

    # Ensure marker isolation before appending
    lua_code = """
    local bufnr, text = ...
    vim.schedule(function()
        if not vim.api.nvim_buf_is_valid(bufnr) then return end
        vim.api.nvim_buf_set_option(bufnr, "modifiable", true)

        -- Ensure marker isolation
        local markers = require("anya.markers")
        text = markers.ensure_marker_line_isolation(text)

        local lines = vim.split(text, "\\n", {plain=true})
        local line_count = vim.api.nvim_buf_line_count(bufnr)

        -- Check if the last line has content - if so, use buf_set_lines to append on new line
        -- Otherwise, use buf_set_text to append to the current (empty/partial) line
        if line_count > 0 then
          local last_line = vim.api.nvim_buf_get_lines(bufnr, line_count - 1, line_count, false)[1] or ""
          if last_line:match("%S") then
            -- Last line has content - append on a new line using buf_set_lines
            vim.api.nvim_buf_set_lines(bufnr, line_count, line_count, false, lines)
          else
            -- Last line is empty or whitespace - append to it using buf_set_text
            local last_col = #last_line
            vim.api.nvim_buf_set_text(bufnr, line_count - 1, last_col, line_count - 1, last_col, lines)
          end
        else
          -- Buffer is empty - set lines directly
          vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, lines)
        end
    end)
    """
    nvim.exec_lua(lua_code, bufnr, text)
    autoscroll(nvim, bufnr)


def append_to_prompt_buffer(nvim, bufnr, text):
    """Append text to the prompt buffer and move cursor to end.

    Handles empty buffer case effectively.
    """
    if not nvim.api.buf_is_valid(bufnr):
        return
    nvim.api.buf_set_option(bufnr, "modifiable", True)

    lines = text.split("\n")
    line_count = nvim.api.buf_line_count(bufnr)

    # Check if buffer is effectively empty (one empty line)
    first_line = nvim.api.buf_get_lines(bufnr, 0, 1, False)[0]
    if line_count == 1 and first_line == "":
        nvim.api.buf_set_lines(bufnr, 0, 1, False, lines)
    else:
        # Check if we need a newline content separator
        last_line_content = nvim.api.buf_get_lines(
            bufnr, line_count - 1, line_count, False
        )[0]
        if last_line_content != "":
            # Append starting on the next line
            # We use buf_set_lines to append
            nvim.api.buf_set_lines(bufnr, line_count, line_count, False, [""] + lines)
        else:
            # Overwrite the empty last line? No, just append?
            # If last line is empty, we can start writing there?
            # But buf_set_lines at line_count inserts AFTER.
            # We want to replace the last empty line or insert at it.
            # Let's just append.
            nvim.api.buf_set_lines(bufnr, line_count, line_count, False, lines)

    # Move cursor to end of new content
    try:
        new_line_count = nvim.api.buf_line_count(bufnr)
        for win in nvim.api.list_wins():
            if nvim.api.win_get_buf(win) == bufnr:
                nvim.api.set_current_win(win)
                # Use a large column value to forcibly set cursor to the end of the line
                # row is 1-indexed.
                nvim.api.win_set_cursor(win, [new_line_count, 100000])
    except Exception:
        pass


def stream_text_to_buffer(nvim, bufnr, text):
    """Stream text to buffer using Lua animation."""
    if not nvim.api.buf_is_valid(bufnr):
        return
    nvim.exec_lua("require('anya.text').output(...)", bufnr, text)


def stream_text_to_buffer_sync(nvim, bufnr, text, skip_process_markers=False):
    """Output text to buffer immediately without animation.

    Args:
        nvim: Neovim instance
        bufnr: Buffer number
        text: Text to write
        skip_process_markers: If True, skip processing markers (caller will do it)
    """
    if not nvim.api.buf_is_valid(bufnr):
        return
    nvim.exec_lua(
        "require('anya.text').output_sync(...)", bufnr, text, None, skip_process_markers
    )


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
    # Don't process markers here - just need to write text to check state
    nvim.exec_lua("require('anya.text').flush_queue(...)", False)
    # Check if last line has content
    line_count = nvim.api.buf_line_count(bufnr)
    if line_count > 0:
        last_line = nvim.api.buf_get_lines(bufnr, line_count - 1, line_count, False)
        if last_line and last_line[0] != "":
            # Add blank line
            nvim.api.buf_set_lines(bufnr, line_count, line_count, False, [""])


def cleanup_trailing_blanks(nvim, bufnr):
    """Remove trailing blank lines from the buffer."""
    if not nvim.api.buf_is_valid(bufnr):
        return

    lua_code = """
    local bufnr = ...
    vim.schedule(function()
        if not vim.api.nvim_buf_is_valid(bufnr) then return end
        vim.api.nvim_buf_set_option(bufnr, "modifiable", true)
        local line_count = vim.api.nvim_buf_line_count(bufnr)
        while line_count > 0 do
            local last_line = vim.api.nvim_buf_get_lines(bufnr, line_count - 1, line_count, false)[1] or ""
            -- Match any whitespace-only line, including empty lines
            if last_line:match("^%s*$") then
                vim.api.nvim_buf_set_lines(bufnr, line_count - 1, line_count, false, {})
                line_count = line_count - 1
            else
                -- Also strip trailing whitespace from the last non-empty line
                local stripped = last_line:gsub("%s+$", "")
                if stripped ~= last_line then
                    vim.api.nvim_buf_set_lines(bufnr, line_count - 1, line_count, false, { stripped })
                end
                break
            end
        end
    end)
    """
    nvim.exec_lua(lua_code, bufnr)


def update_tool_header_line(nvim, bufnr, new_header: str):
    """Update the most recent tool header line with a new combined header.

    Finds the last line containing a tool header (starting with **) and
    replaces it with the new combined header.

    Uses vim.schedule() to defer buffer modifications to avoid E565 errors.
    """
    if not nvim.api.buf_is_valid(bufnr):
        return

    lua_code = """
    local bufnr, new_header = ...
    -- Flush queue first
    require('anya.text').flush_queue(false)
    
    vim.schedule(function()
        if not vim.api.nvim_buf_is_valid(bufnr) then return end
        
        local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
        for i = #lines, 1, -1 do
            local line = lines[i]
            if line:match("^%*%*.*%*%*$") then
                vim.api.nvim_buf_set_lines(bufnr, i - 1, i, false, {new_header})
                require('anya.text')._process_markers(bufnr)
                break
            end
        end
    end)
    """
    nvim.exec_lua(lua_code, bufnr, new_header)


def process_markers(nvim, bufnr):
    """Process markers in the buffer via Lua."""
    if not nvim.api.buf_is_valid(bufnr):
        return
    nvim.exec_lua("require('anya.text')._process_markers(...)", bufnr)


def flush_queue(nvim):
    """Flush the streaming queue."""
    nvim.exec_lua("require('anya.text').flush_queue()")


def update_last_tool_pending_marker(
    nvim, bufnr: int, new_status: str, sync: bool = False
) -> bool:
    """Update the most recent tool_pending marker line to the given status.

    This only edits buffer text (no marker processing - caller should do it).
    Returns True if a marker was updated, False otherwise.

    Uses vim.schedule() to defer buffer modifications to avoid E565 errors
    when called from async contexts. If sync=True, executes immediately
    without vim.schedule() (for use when already on main thread).
    """
    if not nvim.api.buf_is_valid(bufnr):
        return False

    # Use Lua with vim.schedule to safely modify buffer from async context
    # Marker format: <!-- at: fold_start, tool_pending --> or just <!-- at: tool_pending -->
    lua_code = """
    local bufnr, new_status, pending_marker, success_marker, failure_marker, fold_start_marker, use_sync = ...

    local function do_update()
        if not vim.api.nvim_buf_is_valid(bufnr) then return end

        local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
        for i = #lines, 1, -1 do
            local line = lines[i]
            -- Check if line has tool_pending marker
            if line:find(pending_marker, 1, true) then
                -- Update pending/success/failure markers with proper comma handling
                local new_line = line
                -- Remove ", tool_pending" or "tool_pending, " or just "tool_pending"
                new_line = new_line:gsub(", " .. pending_marker, "")
                new_line = new_line:gsub(pending_marker .. ", ", "")
                new_line = new_line:gsub(pending_marker, "")
                new_line = new_line:gsub(", " .. success_marker, "")
                new_line = new_line:gsub(success_marker .. ", ", "")
                new_line = new_line:gsub(success_marker, "")
                new_line = new_line:gsub(", " .. failure_marker, "")
                new_line = new_line:gsub(failure_marker .. ", ", "")
                new_line = new_line:gsub(failure_marker, "")
                -- Add new status before the closing -->
                new_line = new_line:gsub(" %-%->", ", " .. new_status .. " -->")
                vim.api.nvim_buf_set_lines(bufnr, i - 1, i, false, {new_line})
                return true
            end
        end
        return false
    end

    if use_sync then
        do_update()
    else
        vim.schedule(do_update)
    end
    """
    nvim.exec_lua(
        lua_code,
        bufnr,
        new_status,
        markers.TOOL_PENDING,
        markers.TOOL_SUCCESS,
        markers.TOOL_FAILURE,
        markers.FOLD_START,
        sync,
    )
    return True


def update_pending_markers_to_success(nvim, bufnr, sync: bool = False):
    """Update the most recent tool_pending marker to tool_success."""
    update_last_tool_pending_marker(nvim, bufnr, markers.TOOL_SUCCESS, sync=sync)


def update_pending_markers_to_failure(nvim, bufnr, sync: bool = False):
    """Update the most recent tool_pending marker to tool_failure."""
    update_last_tool_pending_marker(nvim, bufnr, markers.TOOL_FAILURE, sync=sync)


def finalize_storage_tool_output(nvim, bufnr, ato_marker: str):
    """Finalize a storage-based tool output by updating marker and appending ato reference.

    For storage-based tools, buffer has:
      **tool header**
      <!-- at: tool_pending -->

    This function transforms it to:
      **tool header**<!-- at: tool_success --><!-- ato: id, tool, lines -->

    The header and markers are merged onto one line so virtual text appears inline.
    """
    lua_code = """
    local bufnr, ato_marker, pending_marker, success_marker = ...

    -- Helpers
    local function merge_header_and_delete_marker(header_idx, marker_idx, marker_line)
        -- Merge marker into header and delete the old marker line in one operation
        local merged = lines[header_idx] .. marker_line
        -- Replace both lines (header and marker) with just the merged header
        vim.api.nvim_buf_set_lines(bufnr, header_idx - 1, marker_idx, false, { merged })
    end

    local function replace_line(idx, new_line)
        vim.api.nvim_buf_set_lines(bufnr, idx - 1, idx, false, { new_line })
    end

    -- Try to finalize by upgrading pending->success and appending ato.
    local function finalize_once()
        if not vim.api.nvim_buf_is_valid(bufnr) then return true end

        lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)

        for i = #lines, 1, -1 do
            local line = lines[i]
            if line:find(pending_marker, 1, true) then
                local new_marker = line:gsub(pending_marker, success_marker) .. ato_marker
                local header_line_idx = i - 1
                if header_line_idx >= 1 and lines[header_line_idx]:match("^%*%*") then
                    -- Merge into header AND delete the old marker line
                    merge_header_and_delete_marker(header_line_idx, i, new_marker)
                else
                    replace_line(i, new_marker)
                end
                require('anya.markers_ui')._process_markers(bufnr)
                return true
            end
        end

        -- Fallback: if a tool_success exists without ato, append ato there
        for i = #lines, 1, -1 do
            local line = lines[i]
            local has_success = line:find(success_marker, 1, true)
            local has_ato = line:find("<!%-%- ato:", 1, true)
            if has_success and not has_ato then
                local new_line = line .. ato_marker
                replace_line(i, new_line)
                require('anya.markers_ui')._process_markers(bufnr)
                return true
            end
        end

        return false
    end

    -- Flush queue synchronously first
    require('anya.text').flush_queue(false)

    -- Debug: log what we're looking for
    vim.notify("finalize_storage: looking for '" .. pending_marker .. "' in buffer " .. bufnr, vim.log.levels.DEBUG)

    if finalize_once() then
        vim.notify("finalize_storage: found and updated marker", vim.log.levels.DEBUG)
        return
    end

    vim.notify("finalize_storage: marker not found on first try, scheduling retry", vim.log.levels.WARN)

    -- Retry once on next tick
    vim.schedule(function()
        if finalize_once() then
            vim.notify("finalize_storage: found marker on retry", vim.log.levels.DEBUG)
        else
            vim.notify("finalize_storage: marker still not found after retry!", vim.log.levels.ERROR)
            -- Debug: dump last 10 lines of buffer
            local lines = vim.api.nvim_buf_get_lines(bufnr, -10, -1, false)
            for i, line in ipairs(lines) do
                vim.notify("  Line " .. i .. ": " .. line:sub(1, 80), vim.log.levels.ERROR)
            end
        end
    end)
    """
    nvim.exec_lua(
        lua_code,
        bufnr,
        ato_marker,
        markers.TOOL_PENDING,
        markers.TOOL_SUCCESS,
    )


def update_storage_tool_output_inline(nvim, bufnr, ato_marker: str):
    """Update pending marker to success and append ato marker on separate line.

    For storage-based tools, buffer has:
      **tool header**
      <!-- at: tool_pending -->

    This function transforms it to:
      **tool header**
      <!-- at: tool_success -->
      <!-- ato: id, tool, lines -->

    Used as an alternative to finalize_storage_tool_output when markers should be on separate lines.
    """
    lua_code = """
    local bufnr, ato_marker, pending_marker, success_marker = ...

    -- Flush queue synchronously first
    require('anya.text').flush_queue(false)

    -- Run immediately (not scheduled) to avoid race with text deltas
    if not vim.api.nvim_buf_is_valid(bufnr) then return end

    local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)

    -- Find the last tool_pending marker line (working backwards)
    for i = #lines, 1, -1 do
        local line = lines[i]
        if line:find(pending_marker, 1, true) then
            -- Found the pending marker line
            -- Replace tool_pending with tool_success
            local new_marker = line:gsub(pending_marker, success_marker)
            vim.api.nvim_buf_set_lines(bufnr, i - 1, i, false, {new_marker})

            -- Append ato marker on new line
            vim.api.nvim_buf_set_lines(bufnr, i, i, false, {ato_marker})

            -- Process markers to create virtual text
            require('anya.markers_ui')._process_markers(bufnr)
            return
        end
    end
    """
    nvim.exec_lua(
        lua_code,
        bufnr,
        ato_marker,
        markers.TOOL_PENDING,
        markers.TOOL_SUCCESS,
    )


def append_to_last_line(nvim, bufnr, text):
    """Append text to the last line of the buffer (no new line)."""
    if not nvim.api.buf_is_valid(bufnr):
        return
    lua_code = """
    local bufnr, text = ...
    local line_count = vim.api.nvim_buf_line_count(bufnr)
    local last_line = vim.api.nvim_buf_get_lines(bufnr, line_count - 1, line_count, false)[1] or ""
    vim.api.nvim_buf_set_lines(bufnr, line_count - 1, line_count, false, {last_line .. text})
    """
    nvim.exec_lua(lua_code, bufnr, text)


def remove_last_fold_markers(nvim, bufnr):
    """Remove the most recent fold_start marker line (for storage-based tool outputs).

    When using storage, we don't want a fold - just the header.
    This deletes the fold_start marker line that was written in TOOL_CALL_START.
    """
    lua_code = """
    local bufnr = select(1, ...)
    local fold_start_marker = select(2, ...)
    
    vim.api.nvim_buf_call(bufnr, function()
        local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
        
        -- Find the last fold_start marker line and DELETE it entirely
        for i = #lines, 1, -1 do
            local line = lines[i]
            if line:find(fold_start_marker, 1, true) then
                -- Delete this marker line
                vim.api.nvim_buf_set_lines(bufnr, i - 1, i, false, {})
                return
            end
        end
    end)
    """
    nvim.exec_lua(
        lua_code,
        bufnr,
        markers.FOLD_START,
    )


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
                
                table.insert(visible_lines, string.format("- @%s lines %d-%d of %d (cursor at %d)\\n\\n```%s\\n%s\\n```", rel_path, start_display, end_display, line_count, row, ft, snippet))
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
                local line_count = vim.api.nvim_buf_line_count(buf)
                table.insert(other_lines, string.format("- @%s line %d of %d", rel_path, row, line_count))
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
