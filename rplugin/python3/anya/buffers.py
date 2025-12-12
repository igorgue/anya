import os
from pynvim import Nvim

CHAT_TITLE = "Chat"
PROMPT_TITLE = "Prompt"
PROMPT_HEIGHT = 3

_float_state = {
    "chat_win": None,
    "prompt_win": None,
    "container_win": None,
    "container_buf": None,
    "resize_group": None,
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

    # Delay winbar setting to avoid conflicts with other configurations
    nvim.async_call(lambda: nvim.api.win_set_option(winid, "winbar", ""))


def _valid_win(nvim: Nvim, winid: int | None) -> bool:
    return bool(winid) and nvim.api.win_is_valid(winid)


def _build_float_configs(
    nvim: Nvim, container_width: int, container_height: int, container_win: int
):
    # Account for border space in prompt height
    # Prompt takes fixed height at bottom, Chat takes the rest
    prompt_height = min(PROMPT_HEIGHT, max(1, container_height - 3))
    prompt_row = max(0, container_height - prompt_height - 2)  # -2 for border/spacing
    chat_height = max(1, prompt_row)

    chat_config = {
        "relative": "win",
        "win": container_win,
        "row": 0,
        "col": 0,
        "width": container_width,
        "height": chat_height,
        "focusable": True,
        "style": "minimal",
        "zindex": 10,
    }

    prompt_config = {
        "relative": "win",
        "win": container_win,
        "row": prompt_row,
        "col": 1,
        "width": max(1, container_width - 2),
        "height": prompt_height,
        "focusable": True,
        "style": "minimal",
        "zindex": 10,
        "border": "rounded",
        "title": "Prompt",
        "title_pos": "center",
    }

    return chat_config, prompt_config


def _get_container_size(nvim: Nvim) -> tuple[int, int] | None:
    container_win = _float_state["container_win"]
    if not _valid_win(nvim, container_win):
        return None
    width = max(1, nvim.api.win_get_width(container_win))
    height = max(1, nvim.api.win_get_height(container_win))
    return (width, height)


def _close_float_windows(nvim: Nvim):
    if _valid_win(nvim, _float_state["prompt_win"]):
        nvim.api.win_close(_float_state["prompt_win"], True)
    if _valid_win(nvim, _float_state["chat_win"]):
        nvim.api.win_close(_float_state["chat_win"], True)

    # Close container window
    if _valid_win(nvim, _float_state["container_win"]):
        nvim.api.win_close(_float_state["container_win"], True)

    # Remove resize autocommands so they don't fire after the UI is closed.
    # This avoids "Invalid channel" errors if the python host is stopped.
    if _float_state.get("resize_group") is not None:
        try:
            nvim.api.del_augroup_by_id(_float_state["resize_group"])
        except Exception:
            pass
        _float_state["resize_group"] = None

    _float_state["chat_win"] = None
    _float_state["prompt_win"] = None
    _float_state["container_win"] = None
    _float_state["last_size"] = None
    # Don't wipe container_buf, we might reuse it or let it be garbage collected


def _reposition_float_windows(nvim: Nvim):
    if not _valid_win(nvim, _float_state["container_win"]):
        return

    # Ensure container window is still showing the container buffer
    container_win = _float_state["container_win"]
    container_buf = _float_state["container_buf"]

    if not container_buf or not nvim.api.buf_is_valid(container_buf):
        return

    try:
        if nvim.api.win_get_buf(container_win) != _buf_id(container_buf):
            return
    except Exception:
        return

    size = _get_container_size(nvim)
    if size is None:
        return

    width, height = size

    chat_config, prompt_config = _build_float_configs(
        nvim, width, height, container_win
    )

    if _valid_win(nvim, _float_state["chat_win"]):
        nvim.api.win_set_config(_float_state["chat_win"], chat_config)
    if _valid_win(nvim, _float_state["prompt_win"]):
        nvim.api.win_set_config(_float_state["prompt_win"], prompt_config)


def _ensure_resize_autocmd(nvim: Nvim):
    if _float_state["resize_group"] is not None:
        return

    group = nvim.api.create_augroup("AnyaFloatLayout", {"clear": True})
    _float_state["resize_group"] = group

    # Use silent! to avoid noisy errors if the python host is temporarily unavailable.
    nvim.api.create_autocmd(
        "VimResized",
        {
            "group": group,
            "command": "silent! call AnyaRepositionFloats()",
        },
    )
    # Also trigger on WinResized to handle split resizing
    nvim.api.create_autocmd(
        "WinResized",
        {
            "group": group,
            "command": "silent! call AnyaRepositionFloats()",
        },
    )


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


def _get_or_create_container_buf(nvim: Nvim):
    if _float_state["container_buf"] and nvim.api.buf_is_valid(
        _float_state["container_buf"]
    ):
        return _float_state["container_buf"]

    buf = nvim.api.create_buf(False, True)  # No file, scratch
    nvim.api.buf_set_name(buf, "AnyaContainer")
    nvim.api.buf_set_option(buf, "bufhidden", "hide")
    nvim.api.buf_set_option(buf, "filetype", "anya-container")
    _float_state["container_buf"] = buf
    return buf


def new(nvim: Nvim, layout="replace", direction=None) -> tuple[object]:
    """Create the Anya UI layout with chat and prompt buffers.

    Args:
        nvim: Neovim instance
        layout: Layout type
        direction: Unused (kept for compatibility)

    Returns:
        Tuple of (chat_buf, prompt_buf) or (None, None) if operation was blocked
    """
    _ = direction
    chat_buf = None
    prompt_buf = None

    for buf in nvim.buffers:
        if buf.name.endswith(CHAT_TITLE):
            chat_buf = buf
        elif buf.name.endswith(PROMPT_TITLE):
            prompt_buf = buf

    if not chat_buf or not chat_buf.valid:
        chat_buf = nvim.api.create_buf(True, False)
        nvim.api.buf_set_name(chat_buf, CHAT_TITLE)
        nvim.api.buf_set_option(chat_buf, "filetype", "anya-chat")
        nvim.api.buf_set_option(chat_buf, "buftype", "nofile")
        nvim.api.buf_set_option(chat_buf, "swapfile", False)

    if not prompt_buf or not prompt_buf.valid:
        prompt_buf = nvim.api.create_buf(True, False)
        nvim.api.buf_set_name(prompt_buf, PROMPT_TITLE)
        nvim.api.buf_set_option(prompt_buf, "filetype", "anya-prompt")
        nvim.api.buf_set_option(prompt_buf, "buftype", "nofile")
        nvim.api.buf_set_option(prompt_buf, "swapfile", False)
        set_prompt_buffer_options(nvim, prompt_buf)

    # Toggle Check
    if _valid_win(nvim, _float_state["container_win"]):
        current_win = nvim.api.get_current_win()
        container_win = _float_state["container_win"]
        chat_win = _float_state["chat_win"]
        prompt_win = _float_state["prompt_win"]

        # If inside any Anya window, close
        if current_win in (container_win, chat_win, prompt_win):
            _close_float_windows(nvim)
            return (chat_buf, prompt_buf)
        else:
            # If outside, focus prompt
            if _valid_win(nvim, prompt_win):
                nvim.api.set_current_win(prompt_win)
            return (chat_buf, prompt_buf)

    # 1. Create Container Window
    container_buf = _get_or_create_container_buf(nvim)

    container_buf_id = _buf_id(container_buf)

    if layout == "tab":
        nvim.command(f"tab sbuffer {container_buf_id}")
    elif layout == "pane":
        width = max(1, nvim.api.get_option("columns") // 3)
        nvim.command(f"botright vertical sbuffer {container_buf_id}")
        nvim.command(f"vertical resize {width}")
    elif layout == "replace":
        nvim.command(f"buffer {container_buf_id}")
    else:  # split / default
        height = max(1, nvim.api.get_option("lines") // 3)
        nvim.command(f"botright sbuffer {container_buf_id}")
        nvim.command(f"resize {height}")

    container_win = nvim.api.get_current_win()
    _float_state["container_win"] = container_win

    # Configure Container Window (minimal, non-focusable)
    nvim.api.win_set_option(container_win, "number", False)
    nvim.api.win_set_option(container_win, "relativenumber", False)
    nvim.api.win_set_option(container_win, "signcolumn", "no")
    nvim.api.win_set_option(container_win, "foldcolumn", "0")
    nvim.api.win_set_option(
        container_win, "winhighlight", "Normal:NormalNC,EndOfBuffer:NormalNC"
    )
    # Mark container as a layout window (similar to Snacks.nvim approach)
    nvim.api.win_set_var(container_win, "anya_layout", True)

    # Container window acts as a layout host for the floats.
    # If focus ever lands on the container (e.g. via <C-w> navigation),
    # redirect to one of the float windows so navigation between chat/prompt works.
    group = nvim.api.create_augroup(
        f"AnyaContainerFocus_{container_buf_id}", {"clear": True}
    )
    nvim.api.create_autocmd(
        "WinEnter",
        {
            "group": group,
            "buffer": container_buf_id,
            "command": "lua require('anya.float_focus').redirect_to_float()",
        },
    )

    # 2. Create Floats inside Container
    size = _get_container_size(nvim)
    if size is None:
        return (chat_buf, prompt_buf)

    width, height = size
    chat_config, prompt_config = _build_float_configs(
        nvim, width, height, container_win
    )

    chat_win = nvim.api.open_win(chat_buf, False, chat_config)
    nvim.api.win_set_option(chat_win, "wrap", True)
    nvim.api.win_set_option(chat_win, "linebreak", True)
    nvim.api.win_set_option(chat_win, "showbreak", "")
    nvim.api.win_set_option(chat_win, "winhighlight", "Normal:Normal")
    nvim.exec_lua(
        "local win_id = ... vim.defer_fn(function() if vim.api.nvim_win_is_valid(win_id) then vim.api.nvim_set_option_value('winbar', '', {win = win_id}) end end, 100)",
        chat_win,
    )
    nvim.api.win_set_var(chat_win, "snacks_main", True)

    prompt_win = nvim.api.open_win(prompt_buf, False, prompt_config)
    set_prompt_window_options(nvim, prompt_win)

    _float_state["chat_win"] = chat_win
    _float_state["prompt_win"] = prompt_win

    _ensure_resize_autocmd(nvim)

    # Focus prompt window without triggering autocmds to avoid recursion
    if _valid_win(nvim, prompt_win):
        nvim.command(f"noautocmd call nvim_set_current_win({_win_id(prompt_win)})")

    # Set up keymaps for the prompt buffer
    nvim.api.buf_set_keymap(
        prompt_buf,
        "n",
        "<CR>",
        "<cmd>lua require('anya.conversation').send_message()<cr>",
        {"noremap": True, "silent": True, "desc": "Send message"},
    )
    nvim.api.buf_set_keymap(
        prompt_buf,
        "i",
        "<CR>",
        "<cmd>stopinsert<cr><cmd>lua require('anya.conversation').send_message()<cr>",
        {"noremap": True, "silent": True, "desc": "Send message"},
    )

    return (chat_buf, prompt_buf)


def close_pane(nvim: Nvim):
    """Close floating windows (kept for backward compatibility)."""
    _close_float_windows(nvim)


def reposition_floats(nvim: Nvim):
    """Reposition floating windows (used by autocmd callback)."""
    _reposition_float_windows(nvim)


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
