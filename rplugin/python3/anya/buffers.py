import os
from pynvim import Nvim

CHAT_TITLE = "Chat"
PROMPT_TITLE = "Prompt"
PROMPT_HEIGHT = 1  # Start with 1 line, will grow dynamically
PROMPT_MAX_HEIGHT = 30  # Maximum height for prompt window

_anya_state = {
    "layout_win": None,  # The container window (split)
    "chat_win": None,  # Floating window for chat
    "prompt_win": None,  # Floating window for prompt
    # Persist the last known prompt height (content height, excluding float border)
    "prompt_height": PROMPT_HEIGHT,
}


def _buf_id(buf):
    try:
        return buf.number
    except Exception:
        return buf


def _win_id(win):
    """Extract window ID from Window object or return as-is if already an int."""
    if isinstance(win, int):
        return win
    try:
        return win.handle
    except Exception:
        return int(win) if win else 0


def set_prompt_buffer_options(nvim: Nvim, bufnr: int):
    """Configure buffer-local options for the prompt buffer.

    Args:
        nvim: Neovim instance
        bufnr: Buffer number
    """
    nvim.api.buf_set_option(bufnr, "wrap", True)
    nvim.api.buf_set_option(bufnr, "linebreak", True)
    nvim.api.buf_set_option(bufnr, "number", False)
    nvim.api.buf_set_option(bufnr, "relativenumber", False)
    nvim.api.buf_set_option(bufnr, "signcolumn", "no")
    nvim.api.buf_set_option(bufnr, "spell", False)
    nvim.api.buf_set_option(bufnr, "modifiable", True)


def set_prompt_window_options(nvim: Nvim, winid: int):
    """Configure window-local options for the prompt window.

    Args:
        nvim: Neovim instance
        winid: Window ID
    """
    nvim.api.win_set_option(winid, "wrap", True)
    nvim.api.win_set_option(winid, "linebreak", True)
    nvim.api.win_set_option(winid, "winhighlight", "Normal:Normal")
    # Clear winbar on prompt window to ensure it doesn't interfere
    try:
        nvim.api.win_set_option(winid, "winbar", "")
    except Exception:
        pass  # Ignore if winbar option doesn't exist


def _valid_win(nvim: Nvim, winid: int | None) -> bool:
    return bool(winid) and nvim.api.win_is_valid(winid)


def _find_anya_windows(nvim: Nvim) -> tuple[int | None, int | None, int | None, list]:
    """Find Anya windows by scanning all windows for Anya buffers.

    Chat and Prompt windows are expected to be floating windows (relative to the layout window).
    Layout window is a regular split window containing the Anya Container buffer.
    Also tracks any stray windows showing Chat/Prompt buffers (e.g., from buffer leakage).

    Returns:
        Tuple of (chat_win, prompt_win, layout_win, stray_wins) - any may be None if not found
    """
    chat_win = None
    prompt_win = None
    layout_win = None
    stray_wins = []  # Windows showing Chat/Prompt buffers that aren't the expected floats

    for win in nvim.api.list_wins():
        try:
            if not nvim.api.win_is_valid(win):
                continue
            buf = nvim.api.win_get_buf(win)
            if not nvim.api.buf_is_valid(buf):
                continue
            buf_name = nvim.api.buf_get_name(buf)

            # Check if this is a floating window
            win_config = nvim.api.win_get_config(win)
            is_floating = win_config.get("relative", "") != ""

            if buf_name.endswith(CHAT_TITLE):
                if is_floating:
                    chat_win = win
                else:
                    # Non-floating window showing Chat buffer - this is a leak
                    stray_wins.append(win)
            elif buf_name.endswith(PROMPT_TITLE):
                if is_floating:
                    prompt_win = win
                else:
                    # Non-floating window showing Prompt buffer - this is a leak
                    stray_wins.append(win)
            elif buf_name.endswith("Anya Container") and not is_floating:
                layout_win = win
        except Exception:
            continue

    return (chat_win, prompt_win, layout_win, stray_wins)


def _close_anya_windows(nvim: Nvim):
    """Close Anya chat and prompt windows."""
    # Find windows dynamically in case state is stale
    found_chat_win, found_prompt_win, found_layout_win, stray_wins = _find_anya_windows(
        nvim
    )

    # Use found windows or fall back to state
    chat_win = found_chat_win or _anya_state.get("chat_win")
    prompt_win = found_prompt_win or _anya_state.get("prompt_win")
    layout_win = found_layout_win or _anya_state.get("layout_win")

    # Collect all Anya window IDs for exclusion
    anya_wins = set()
    if chat_win:
        anya_wins.add(_win_id(chat_win))
    if prompt_win:
        anya_wins.add(_win_id(prompt_win))
    if layout_win:
        anya_wins.add(_win_id(layout_win))
    for sw in stray_wins:
        anya_wins.add(_win_id(sw))

    # Switch focus to a non-Anya window first to prevent buffer leakage
    # When we close a focused window, Neovim may move its buffer to the next window
    current_win = nvim.api.get_current_win()
    if _win_id(current_win) in anya_wins:
        # Find a non-Anya window to switch to
        for win in nvim.api.list_wins():
            if _win_id(win) not in anya_wins and nvim.api.win_is_valid(win):
                try:
                    nvim.api.set_current_win(win)
                    break
                except Exception:
                    pass

    # Handle stray windows showing Chat/Prompt buffers (buffer leakage)
    # Close them instead of switching to previous buffer to fully clean up
    for stray_win in stray_wins:
        try:
            if nvim.api.win_is_valid(stray_win):
                # Close the stray window if there's more than one window
                if len(nvim.api.list_wins()) > 1:
                    nvim.api.win_close(stray_win, True)
        except Exception:
            pass

    # Capture the current prompt height (if open) so it can be restored on reopen.
    if _valid_win(nvim, prompt_win):
        try:
            _anya_state["prompt_height"] = max(
                1,
                min(
                    nvim.api.win_get_height(prompt_win),
                    PROMPT_MAX_HEIGHT,
                ),
            )
        except Exception:
            pass

    # Find the container buffer to ensure layout window doesn't inherit chat/prompt buffer
    container_buf = None
    for buf in nvim.buffers:
        try:
            if nvim.api.buf_get_name(buf).endswith("Anya Container"):
                container_buf = buf
                break
        except Exception:
            continue

    # If layout window exists and might inherit a floating buffer when closed,
    # ensure it shows the container buffer first
    if _valid_win(nvim, layout_win) and container_buf:
        try:
            current_layout_buf = nvim.api.win_get_buf(layout_win)
            layout_buf_name = nvim.api.buf_get_name(current_layout_buf)
            # If layout window is somehow showing chat/prompt buffer, fix it
            if layout_buf_name.endswith(CHAT_TITLE) or layout_buf_name.endswith(
                PROMPT_TITLE
            ):
                nvim.api.win_set_buf(layout_win, container_buf)
        except Exception:
            pass

    # Clear the WinClosed autocmd group to prevent recursive close calls
    try:
        nvim.api.create_augroup("AnyaWindowClose", {"clear": True})
    except Exception:
        pass

    # Before closing floating windows, ensure the layout window shows the container buffer
    # This prevents the chat/prompt buffers from "escaping" to the layout window
    if _valid_win(nvim, layout_win) and container_buf:
        try:
            nvim.api.win_set_buf(layout_win, container_buf)
        except Exception:
            pass

    # Close floating windows first, then the layout window
    if _valid_win(nvim, prompt_win):
        try:
            nvim.api.win_close(prompt_win, True)
        except Exception:
            pass
    if _valid_win(nvim, chat_win):
        try:
            nvim.api.win_close(chat_win, True)
        except Exception:
            pass
    if _valid_win(nvim, layout_win):
        try:
            # Check if layout window is still valid and not the only window
            if len(nvim.api.list_wins()) > 1:
                nvim.api.win_close(layout_win, True)
        except Exception:
            pass

    # Final cleanup: close any remaining stray windows that might have the chat/prompt buffers
    _, _, _, remaining_strays = _find_anya_windows(nvim)
    for stray_win in remaining_strays:
        try:
            if nvim.api.win_is_valid(stray_win) and len(nvim.api.list_wins()) > 1:
                nvim.api.win_close(stray_win, True)
        except Exception:
            pass

    _anya_state["chat_win"] = None
    _anya_state["prompt_win"] = None
    _anya_state["layout_win"] = None


def get_buffer_content(nvim: Nvim, bufnr: int) -> str:
    """Read entire buffer content as a single string with markers intact.

    Args:
        nvim: Neovim instance
        bufnr: Buffer number

    Returns:
        Complete buffer content as a string
    """
    if not nvim.api.buf_is_valid(bufnr):
        return ""
    lines = nvim.api.buf_get_lines(bufnr, 0, -1, False)
    return "\n".join(lines)


def is_in_anya_buffer(nvim: Nvim) -> bool:
    """Check if the current buffer is an Anya buffer (chat or prompt).

    Args:
        nvim: Neovim instance

    Returns:
        True if current buffer is an Anya buffer
    """
    current_buf = nvim.api.get_current_buf()
    ft = nvim.api.buf_get_option(current_buf, "filetype")
    return ft in ("anya-chat", "anya-prompt")


def new(
    nvim: Nvim, layout="replace", direction=None, force_open=False
) -> tuple[object]:
    """Create the Anya UI layout with chat and prompt buffers.

    Args:
        nvim: Neovim instance
        layout: Layout type ("replace", "pane", "tab", "split")
        direction: For pane layout, "left" or "right" (default)
        force_open: If True, force opening (and re-layout) instead of toggling closed.

    Returns:
        Tuple of (chat_buf, prompt_buf)
    """
    # Find or create buffers
    chat_buf = None
    prompt_buf = None

    for buf in nvim.buffers:
        if buf.name.endswith(CHAT_TITLE):
            chat_buf = buf
        elif buf.name.endswith(PROMPT_TITLE):
            prompt_buf = buf

    if not chat_buf or not chat_buf.valid:
        chat_buf = nvim.api.create_buf(False, True)  # unlisted, scratch
        nvim.api.buf_set_name(chat_buf, CHAT_TITLE)
        nvim.api.buf_set_option(chat_buf, "filetype", "anya-chat")
        nvim.api.buf_set_option(chat_buf, "buftype", "nofile")
        nvim.api.buf_set_option(chat_buf, "bufhidden", "hide")
        nvim.api.buf_set_option(chat_buf, "swapfile", False)
    else:
        # Ensure bufhidden is set on existing buffer
        nvim.api.buf_set_option(chat_buf, "bufhidden", "hide")

    if not prompt_buf or not prompt_buf.valid:
        prompt_buf = nvim.api.create_buf(False, True)  # unlisted, scratch
        nvim.api.buf_set_name(prompt_buf, PROMPT_TITLE)
        nvim.api.buf_set_option(prompt_buf, "filetype", "anya-prompt")
        nvim.api.buf_set_option(prompt_buf, "buftype", "nofile")
        nvim.api.buf_set_option(prompt_buf, "bufhidden", "hide")
        nvim.api.buf_set_option(prompt_buf, "swapfile", False)
        set_prompt_buffer_options(nvim, prompt_buf)
    else:
        # Ensure bufhidden is set on existing buffer
        nvim.api.buf_set_option(prompt_buf, "bufhidden", "hide")

    # Create or identify layout buffer (container)
    layout_buf = None
    for buf in nvim.buffers:
        if buf.name.endswith("Anya Container"):
            layout_buf = buf
            break

    if not layout_buf or not layout_buf.valid:
        layout_buf = nvim.api.create_buf(False, True)  # No file, scratch
        nvim.api.buf_set_name(layout_buf, "Anya Container")
        nvim.api.buf_set_option(layout_buf, "buftype", "nofile")
        nvim.api.buf_set_option(layout_buf, "bufhidden", "hide")
        nvim.api.buf_set_option(layout_buf, "swapfile", False)

    layout_buf_id = _buf_id(layout_buf)

    # Toggle: close if already open
    current_win = nvim.api.get_current_win()

    # First, try to find existing Anya windows dynamically (handles state out-of-sync)
    found_chat_win, found_prompt_win, found_layout_win, stray_wins = _find_anya_windows(
        nvim
    )

    # Clean up any stray windows immediately (buffer leaked to non-floating windows)
    for stray_win in stray_wins:
        try:
            if nvim.api.win_is_valid(stray_win):
                if len(nvim.api.list_wins()) > 1:
                    nvim.api.win_close(stray_win, True)
        except Exception:
            pass

    # Use found windows if state is stale, otherwise use cached state
    chat_win = found_chat_win if found_chat_win else _anya_state["chat_win"]
    prompt_win = found_prompt_win if found_prompt_win else _anya_state["prompt_win"]
    layout_win = found_layout_win if found_layout_win else _anya_state.get("layout_win")

    # Sync state with what we found
    if found_chat_win:
        _anya_state["chat_win"] = found_chat_win
    if found_prompt_win:
        _anya_state["prompt_win"] = found_prompt_win
    if found_layout_win:
        _anya_state["layout_win"] = found_layout_win

    # If windows exist and are valid...
    if _valid_win(nvim, chat_win) or _valid_win(nvim, prompt_win):
        if not force_open:
            # If pane layout is requested, always toggle (close) even if not focused
            if layout == "pane":
                _close_anya_windows(nvim)
                return (chat_buf, prompt_buf)

            # Check if we're in an Anya window
            if current_win in (chat_win, prompt_win, layout_win):
                _close_anya_windows(nvim)
                return (chat_buf, prompt_buf)
            else:
                # Focus prompt if outside
                if _valid_win(nvim, prompt_win):
                    nvim.api.set_current_win(prompt_win)
                return (chat_buf, prompt_buf)
        else:
            # Force open: If windows are already valid, ensure prompt focus and return
            # This avoids closing and reopening the pane (flicker)
            if _valid_win(nvim, prompt_win) and _valid_win(nvim, chat_win):
                if _valid_win(nvim, prompt_win):
                    nvim.api.set_current_win(prompt_win)
                return (chat_buf, prompt_buf)

            # If windows are in partial state (e.g. invalid), close to rebuild
            _close_anya_windows(nvim)

    # Create layout based on type
    chat_buf_id = _buf_id(chat_buf)
    prompt_buf_id = _buf_id(prompt_buf)

    if layout == "tab":
        nvim.command("tabnew")
        nvim.command(f"buffer {layout_buf_id}")
    elif layout == "pane":
        width = max(1, nvim.api.get_option("columns") // 3)
        if direction == "left":
            nvim.command(f"topleft vertical {width}split | buffer {layout_buf_id}")
        else:
            nvim.command(f"botright vertical {width}split | buffer {layout_buf_id}")
    elif layout == "replace":
        nvim.command(f"buffer {layout_buf_id}")
    else:  # split / default
        height = max(1, nvim.api.get_option("lines") // 3)
        nvim.command(f"botright {height}split | buffer {layout_buf_id}")

    layout_win = nvim.api.get_current_win()
    _anya_state["layout_win"] = layout_win

    # Configure layout window (minimal UI)
    nvim.api.win_set_option(layout_win, "number", False)
    nvim.api.win_set_option(layout_win, "relativenumber", False)
    nvim.api.win_set_option(layout_win, "signcolumn", "no")
    # Explicitly clear winbar on layout window to prevent navic/other plugins from affecting floating windows
    # This is critical - if the layout window has a winbar, it affects floating window positioning
    try:
        nvim.api.win_set_option(layout_win, "winbar", "")
    except Exception:
        pass  # Ignore if winbar option doesn't exist
    # Also set up an autocmd to keep it cleared in case navic tries to set it again
    layout_win_id = _win_id(layout_win)
    nvim.exec_lua(
        f"""
    vim.api.nvim_create_autocmd("WinEnter", {{
      buffer = {layout_buf_id},
      callback = function()
        local winid = vim.api.nvim_get_current_win()
        local layout_winid = {layout_win_id}
        if winid == layout_winid then
          vim.api.nvim_win_set_option(winid, "winbar", "")
        end
      end,
    }})
    """,
        [],
    )
    nvim.api.win_set_option(layout_win, "statusline", "")
    nvim.api.win_set_option(layout_win, "wrap", False)

    # Get layout dimensions
    layout_width = nvim.api.win_get_width(layout_win)
    layout_height = nvim.api.win_get_height(layout_win)

    # Calculate heights
    # Prefer persisted height from the last open prompt window; fall back to buffer line count.
    try:
        buf_line_count = nvim.api.buf_line_count(prompt_buf_id)
    except Exception:
        buf_line_count = PROMPT_HEIGHT

    prompt_height = int(_anya_state.get("prompt_height") or PROMPT_HEIGHT)
    prompt_height = max(1, min(prompt_height, PROMPT_MAX_HEIGHT))

    # If the prompt buffer already has content, ensure we don't reopen at 1 line.
    # This also covers the common case where the prompt grew via blank lines.
    if buf_line_count > prompt_height:
        prompt_height = min(buf_line_count, PROMPT_MAX_HEIGHT)

    chat_height = max(1, layout_height - prompt_height)

    # Create Chat Window (Floating inside layout)
    # Note: winbar doesn't need height adjustment - it's part of the window's content area
    # If parent has winbar, floating windows start below it automatically
    chat_config = {
        "relative": "win",
        "win": layout_win,
        "row": 0,
        "col": 0,
        "width": max(1, layout_width),
        "height": chat_height,
        "focusable": True,
        "style": "minimal",
        "zindex": 1,  # Lower z-index
        "border": "none",
    }

    chat_win = nvim.api.open_win(chat_buf_id, True, chat_config)

    # Configure chat window
    nvim.api.win_set_option(chat_win, "wrap", True)
    nvim.api.win_set_option(
        chat_win,
        "winhighlight",
        "Normal:Normal,NormalFloat:Normal,WinBar:AnyaWinBar,WinBarNC:AnyaWinBar",
    )
    nvim.api.win_set_option(chat_win, "linebreak", True)
    nvim.api.win_set_option(chat_win, "showbreak", "")
    nvim.api.win_set_option(chat_win, "number", False)
    nvim.api.win_set_option(chat_win, "relativenumber", False)
    nvim.api.win_set_option(chat_win, "signcolumn", "no")
    # Clear winbar immediately to prevent any interference
    try:
        nvim.api.win_set_option(chat_win, "winbar", "")
    except Exception:
        pass  # Ignore if winbar option doesn't exist
    nvim.api.win_set_var(chat_win, "snacks_main", True)

    # Create Prompt Window (Floating inside layout, below Chat)
    prompt_config = {
        "relative": "win",
        "win": layout_win,
        "row": chat_height,
        "col": 0,
        "width": max(1, layout_width),
        "height": prompt_height,
        "focusable": True,
        "style": "minimal",
        "zindex": 10,
        "border": "top",  # Top border to separate from chat
        "title": " Prompt ",
        "title_pos": "center",
    }

    # Adjust height/row slightly for border?
    # If border is "top", it takes 1 line. So row needs to be chat_height.
    # But if we want it seamlessly, maybe calculate exactly.
    # The 'border' property adds lines to the window footprint if using 'rounded' etc.
    # If 'border'='top', it adds 1 line at the top.
    # Let's use 'rounded' as before but maybe handle resizing carefully?
    # User said "See how Snacks does it". Snacks uses split-like layouts.
    # Let's keep "rounded" for prompt as it looks nice, but maybe "none" for Chat.

    # UPDATE: If using border='rounded', it adds 2 to height/width.
    # We want prompt to be EXACTLY at bottom.
    # Let's use border='rounded' for prompt, but accounting for it might be tricky with simple math.
    # Let's try border='single' or 'rounded' purely on prompt, and shrink chat accordingly.

    # Actually, let's stick to the previous style but positioned correctly.
    prompt_config["border"] = "rounded"
    # With border, the window takes up height+2 lines.
    # So if prompt_height (content) is 1. Total height is 3.
    # chat_height should be layout_height - (prompt_height + 2).

    real_prompt_height = prompt_height + 2
    chat_height = max(1, layout_height - real_prompt_height)

    # Update configs with refined math
    chat_config["height"] = chat_height
    prompt_config["row"] = chat_height
    prompt_config["height"] = prompt_height
    prompt_config["width"] = max(
        1, layout_width
    )  # Full width (minus border? No, prompt inside layout)

    # Fix prompt width to account for its own border if we want it to fit perfectly?
    # Usually border is "outside" content dimensions in nvim float config?
    # Yes, width/height specify content size.
    # So if container width is 100. Prompt width 100 + border = 102 -> overflow.
    # So prompt width should be layout_width - 2.

    prompt_config["width"] = max(1, layout_width - 2)
    prompt_config["col"] = 0  # It will be centered? No, manual col.
    # If width is W-2, and we put it at col 0, it takes 0..W-2.
    # Border adds 1 left, 1 right. Total coverage: -1 to W-1? (relative)
    # Relative floats with borders are tricky.
    # Let's simplify: Use border="none" for prompt and draw a separator line in Chat?
    # OR rely on matching layout_width.

    # If we use border='rounded':
    # We need width = layout_width - 2 (for left/right border).
    # col = 0.
    # Actually, standard is to inset.
    # But let's try just setting it to layout_width - 2.

    # What about Chat? border='none'. Width = layout_width.

    prompt_win = nvim.api.open_win(prompt_buf_id, False, prompt_config)
    set_prompt_window_options(nvim, prompt_win)

    _anya_state["chat_win"] = chat_win
    _anya_state["prompt_win"] = prompt_win

    # Recalculate height based on actual display height (accounts for wrapped lines)
    reposition_floats(nvim)

    # Focus the prompt window initially
    nvim.api.set_current_win(prompt_win)

    # Set winbar after everything is done - use Lua schedule to defer
    # This ensures all window operations are complete before setting winbar
    chat_win_id = _win_id(chat_win)
    nvim.exec_lua(
        f"""
    vim.schedule(function()
      local winid = {chat_win_id}
      if vim.api.nvim_win_is_valid(winid) then
        local bufnr = vim.api.nvim_win_get_buf(winid)
        -- Ensure syntax is loaded and highlights are set up
        if vim.bo[bufnr].filetype == "anya-chat" then
          -- Force syntax loading and highlight setup
          vim.cmd("doautocmd FileType anya-chat")
          require("anya.ui_utils").setup_highlights()
        end
        -- Always clear winbar first, then set ours to replace any existing/global winbar
        -- This ensures we don't stack winbars
        vim.api.nvim_win_set_option(winid, "winbar", "")
        vim.api.nvim_win_set_option(winid, "winbar", "%{{%v:lua.require('anya.ui_utils').get_winbar()%}}")
        -- Re-apply winhighlight to ensure AnyaWinBar highlight is used
        vim.api.nvim_win_set_option(winid, "winhighlight", "Normal:Normal,NormalFloat:Normal,WinBar:AnyaWinBar,WinBarNC:AnyaWinBar")
        -- Force a redraw to apply highlights
        vim.cmd("redrawstatus")
      end
    end)
    """,
        [],
    )

    # Set up resize autocmd to keep floats positioned
    group = nvim.api.create_augroup("AnyaFloatPrompt", {"clear": True})
    nvim.api.create_autocmd(
        ["VimResized", "WinResized"],
        {
            "group": group,
            "command": "silent! call AnyaRepositionFloats()",
        },
    )

    # Set up autocmd to close all Anya windows when chat or prompt window is closed
    # This ensures consistent behavior when user closes either window
    chat_win_id = _win_id(chat_win)
    prompt_win_id = _win_id(prompt_win)
    nvim.exec_lua(
        f"""
    local group = vim.api.nvim_create_augroup("AnyaWindowClose", {{ clear = true }})
    vim.api.nvim_create_autocmd("WinClosed", {{
        group = group,
        pattern = "{chat_win_id},{prompt_win_id}",
        callback = function(ev)
            -- Schedule to run after the window is actually closed
            vim.schedule(function()
                -- Check if the other Anya windows are still valid
                local chat_valid = vim.api.nvim_win_is_valid({chat_win_id})
                local prompt_valid = vim.api.nvim_win_is_valid({prompt_win_id})
                -- If one is closed but not through :Anya close, close the other
                if not chat_valid or not prompt_valid then
                    vim.cmd("silent! Anya close")
                end
            end)
        end,
        desc = "Close all Anya windows when chat or prompt is closed",
    }})
    """,
        [],
    )

    # Force keymap precedence for Ctrl+j
    # We use an autocmd on InsertEnter/BufEnter to ensure our mapping overrides
    # any completion plugins (like blink.cmp) that might try to take over Ctrl+j.
    # We also apply it immediately to catch the current state (since we just entered/focused).
    prompt_keys_cmd = f"""
    local group = vim.api.nvim_create_augroup("AnyaPromptKeys_{prompt_buf_id}", {{ clear = true }})

    local function set_prompt_keys()
        local opts = {{ buffer = {prompt_buf_id}, silent = true, nowait = true, desc = "Insert blank line" }}
        vim.keymap.set("n", "<C-j>", "o<Esc>", opts)
        vim.keymap.set("i", "<C-j>", "<C-o>o", opts)
        vim.keymap.set("n", "<S-CR>", "o<Esc>", opts)
        vim.keymap.set("i", "<S-CR>", "<C-o>o", opts)

        -- Focus chat window with Ctrl+k
        local focus_opts = {{ buffer = {prompt_buf_id}, silent = true, nowait = true, desc = "Focus chat window" }}
        vim.keymap.set("n", "<C-k>", function()
            for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
                local buf = vim.api.nvim_win_get_buf(win)
                local ft = vim.api.nvim_get_option_value("filetype", {{ buf = buf }})
                if ft == "anya-chat" then
                    vim.api.nvim_set_current_win(win)
                    return
                end
            end
        end, focus_opts)
        vim.keymap.set("i", "<C-k>", function()
            for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
                local buf = vim.api.nvim_win_get_buf(win)
                local ft = vim.api.nvim_get_option_value("filetype", {{ buf = buf }})
                if ft == "anya-chat" then
                    vim.api.nvim_set_current_win(win)
                    return
                end
            end
        end, focus_opts)
    end

    -- Apply immediately
    set_prompt_keys()

    vim.api.nvim_create_autocmd({{"BufEnter", "InsertEnter"}}, {{
        buffer = {prompt_buf_id},
        group = group,
        callback = set_prompt_keys
    }})
    """
    nvim.exec_lua(prompt_keys_cmd, [])

    # Set up focus trap for the layout container
    # Redirect calls to the container buffer back to the prompt window
    nvim.api.buf_set_var(layout_buf, "anya_prompt_win", _win_id(prompt_win))

    trap_cmd = f"""
    local group = vim.api.nvim_create_augroup("AnyaLayoutFocusTrap_{layout_buf_id}", {{ clear = true }})
    vim.api.nvim_create_autocmd("WinEnter", {{
        buffer = {layout_buf_id},
        group = group,
        callback = function()
            vim.schedule(function()
                if vim.api.nvim_get_current_buf() == {layout_buf_id} then
                    local ok, win = pcall(vim.api.nvim_buf_get_var, {layout_buf_id}, "anya_prompt_win")
                    if ok and vim.api.nvim_win_is_valid(win) then
                        pcall(vim.api.nvim_set_current_win, win)
                    end
                end
            end)
        end
    }})
    """
    nvim.exec_lua(trap_cmd, [])

    return (chat_buf, prompt_buf)


def close_pane(nvim: Nvim):
    """Close Anya windows."""
    _close_anya_windows(nvim)


def reposition_floats(nvim: Nvim):
    """Reposition the floating prompt and chat windows when layout window is resized."""
    layout_win = _anya_state.get("layout_win")
    chat_win = _anya_state.get("chat_win")
    prompt_win = _anya_state.get("prompt_win")

    if (
        not _valid_win(nvim, layout_win)
        or not _valid_win(nvim, chat_win)
        or not _valid_win(nvim, prompt_win)
    ):
        return

    try:
        layout_width = nvim.api.win_get_width(layout_win)
        layout_height = nvim.api.win_get_height(layout_win)

        # Calculate prompt height based on actual display height (including wrapped lines)
        prompt_buf = nvim.api.win_get_buf(prompt_win)
        line_count = nvim.api.buf_line_count(prompt_buf)

        # Use nvim_win_text_height to get the actual display height (accounts for wrapping)
        try:
            result = nvim.api.win_text_height(
                prompt_win,
                {
                    "start_row": 0,
                    "end_row": line_count - 1 if line_count > 0 else 0,
                },
            )
            # win_text_height returns a dict with 'all' key
            display_height = (
                result.get("all", line_count) if isinstance(result, dict) else result
            )
        except Exception:
            # Fallback to line count if win_text_height is not available
            display_height = line_count

        # Use content height, but respect manual height override if set (C-Up/C-Down)
        # Manual override allows expanding beyond content; shrinks when content is deleted
        manual_override = _anya_state.get("manual_prompt_height")
        if manual_override is not None:
            prompt_height = min(
                max(1, max(display_height, manual_override)), PROMPT_MAX_HEIGHT
            )
        else:
            prompt_height = min(max(1, display_height), PROMPT_MAX_HEIGHT)
        _anya_state["prompt_height"] = prompt_height

        # Calculate sizes (prompt border takes 2 lines)
        real_prompt_height = prompt_height + 2
        chat_height = max(1, layout_height - real_prompt_height)

        # Update Chat Float
        chat_config = {
            "relative": "win",
            "win": layout_win,
            "row": 0,
            "col": 0,
            "width": max(1, layout_width),
            "height": chat_height,
        }
        nvim.api.win_set_config(chat_win, chat_config)

        # Update Prompt Float
        prompt_config = {
            "relative": "win",
            "win": layout_win,
            "row": chat_height,
            "col": 0,
            "width": max(1, layout_width - 2),
            "height": prompt_height,
        }

        nvim.api.win_set_config(prompt_win, prompt_config)
    except Exception:
        pass


def get_file_completions_async(nvim: Nvim, base: str, callback_id: str):
    """Get file completions and call back to Lua.

    Args:
        nvim: Neovim instance
        base: Base path to complete from
        callback_id: Callback ID to pass back to Lua
    """
    matches = []
    limit = 50

    # Handle empty base - show files in current directory
    if base == "":
        base = ""

    # Determine the directory to search and the prefix to match
    dir_part = ""
    prefix = base

    if "/" in base:
        # Path contains directory separator
        parts = base.rsplit("/", 1)
        if len(parts) == 2:
            dir_part = parts[0]
            prefix = parts[1]

    # Convert to absolute path for scanning
    if dir_part == "":
        search_dir = nvim.funcs.getcwd()
    elif dir_part.startswith("/"):
        search_dir = dir_part
    else:
        search_dir = os.path.join(nvim.funcs.getcwd(), dir_part)

    # Remove trailing slash for directory scanning
    if search_dir.endswith("/"):
        search_dir = search_dir[:-1]

    # Try to use fd first (much faster and respects gitignore)
    import subprocess

    try:
        # Use fd to get files and directories
        cmd = ["fd", "--max-depth", "3", "--type", "f", "--type", "d", ".", search_dir]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=2,  # 2 second timeout
        )

        if result.returncode == 0:
            # Process fd output
            for line in result.stdout.strip().split("\n"):
                if not line or len(matches) >= limit:
                    break

                # Convert to relative path
                if line.startswith(search_dir):
                    relative = line[len(search_dir) :]
                    if relative.startswith("/"):
                        relative = relative[1:]
                else:
                    relative = line

                # Check if it matches our prefix
                name = relative.split("/")[-1]
                if prefix == "" or name.lower().startswith(prefix.lower()):
                    display_name = relative
                    # Add trailing slash for directories
                    if line.strip().endswith("/"):
                        display_name = relative + "/"
                    completion = dir_part + display_name if dir_part else display_name
                    matches.append(completion)

            # Sort results: directories first, then files, alphabetically
            matches.sort(key=lambda x: (not x.endswith("/"), x.lower()))
        else:
            raise Exception("fd command failed")

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        # Fallback to os.walk if fd is not available or fails
        try:
            count = 0
            for root, dirs, files in os.walk(search_dir):
                # Skip hidden directories and .git
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != ".git"]
                # Also skip common ignore patterns
                dirs[:] = [
                    d
                    for d in dirs
                    if d
                    not in [
                        "node_modules",
                        "__pycache__",
                        "target",
                        "build",
                        "dist",
                        "venv",
                        ".venv",
                        "vendor",
                    ]
                ]

                # Process directories first
                all_items = [(d, "directory") for d in dirs] + [
                    (f, "file") for f in files
                ]

                for name, item_type in all_items:
                    if count >= limit:
                        break

                    # Skip hidden files
                    if name.startswith("."):
                        continue

                    # Check if the name matches our prefix
                    if prefix == "" or name.lower().startswith(prefix.lower()):
                        display_name = name

                        # For directories, add trailing slash
                        if item_type == "directory":
                            display_name = name + "/"

                        # Reconstruct the completion path
                        completion = (
                            dir_part + display_name if dir_part else display_name
                        )
                        matches.append(completion)
                        count += 1

                if count >= limit:
                    break

                # Limit depth to avoid scanning too deep
                level = root[len(search_dir) :].count(os.sep)
                if level >= 3:
                    dirs[:] = []  # Don't recurse further

        except (OSError, PermissionError):
            # Handle permission errors gracefully
            pass

    # Call back to Lua with the results
    nvim.exec_lua("ananya_blink_file_completion_callback(...)", [matches, callback_id])
