import os
from pynvim import Nvim

CHAT_TITLE = "Chat"
PROMPT_TITLE = "Prompt"
PROMPT_HEIGHT = 8

# Track pane state for toggling
_pane_state = {
    "is_open": False,
    "direction": "right",
    "chat_win": None,
    "prompt_win": None,
    "original_win": None,
}


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


def new(nvim: Nvim, layout="split", direction=None) -> tuple[object]:
    """Create the Anya UI layout with chat and prompt buffers.

    Args:
        nvim: Neovim instance
        layout: Layout type - "split" (default), "tab", or "pane"
        direction: For pane layout - "right" (default) or "left"

    Returns:
        Tuple of (chat_buf, prompt_buf) or (None, None) if operation was blocked
    """
    global _pane_state

    # For pane layout: if user is in an Anya buffer but pane is not open,
    # they're using a different layout - show error instead of opening pane
    if layout == "pane" and is_in_anya_buffer(nvim) and not _pane_state["is_open"]:
        nvim.err_write("Anya: Already open\n")
        return (None, None)

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

    if layout == "tab":
        # Create a new tab
        nvim.command("tabnew")
        nvim.command("enew")

        # Create prompt window first (at bottom)
        nvim.command("botright split")
        nvim.command(f"resize {PROMPT_HEIGHT}")
        prompt_win = nvim.api.get_current_win()
        nvim.api.win_set_option(prompt_win, "winfixheight", True)
        nvim.api.win_set_buf(prompt_win, prompt_buf)
        set_prompt_window_options(nvim, prompt_win)

        # Go back to top window and set up chat
        nvim.command("wincmd k")
        chat_win = nvim.api.get_current_win()
        nvim.api.win_set_buf(chat_win, chat_buf)
        nvim.api.win_set_option(chat_win, "wrap", True)
        nvim.api.win_set_option(chat_win, "linebreak", True)
        nvim.api.win_set_option(chat_win, "showbreak", "")
        # Mark chat window as preferred main for Snacks.picker
        nvim.api.win_set_var(chat_win, "snacks_main", True)

        # Focus the prompt window so user can start typing
        nvim.api.set_current_win(prompt_win)

    elif layout == "pane":
        # Handle pane toggling
        direction = direction or "right"

        if _pane_state["is_open"]:
            # Close the pane
            close_pane(nvim)
            _pane_state["is_open"] = False
            # Return existing buffers (they're still valid)
            for buf in nvim.buffers:
                if buf.name.endswith(CHAT_TITLE):
                    chat_buf = buf
                elif buf.name.endswith(PROMPT_TITLE):
                    prompt_buf = buf
            return (chat_buf, prompt_buf)
        else:
            # Remember current window
            _pane_state["original_win"] = nvim.api.get_current_win()

            # Create a vertical pane for Anya (don't close existing windows)
            if direction == "left":
                nvim.command("topleft vsplit")
            else:  # default to right
                nvim.command("botright vsplit")

            # This is our main Anya pane window (will become chat)
            pane_win = nvim.api.get_current_win()

            # Set pane width to 30% of screen width
            screen_width = nvim.api.get_option("columns")
            pane_width = max(1, int(screen_width * 0.3))
            nvim.command(f"vertical resize {pane_width}")

            # Create prompt window first (at bottom of pane)
            nvim.command("split")
            prompt_win = nvim.api.get_current_win()
            nvim.command(f"resize {PROMPT_HEIGHT}")
            nvim.api.win_set_option(prompt_win, "winfixheight", True)
            nvim.api.win_set_buf(prompt_win, prompt_buf)
            set_prompt_window_options(nvim, prompt_win)

            # Go back to top window and set up chat
            nvim.command("wincmd k")
            chat_win = nvim.api.get_current_win()
            nvim.api.win_set_buf(chat_win, chat_buf)
            nvim.api.win_set_option(chat_win, "wrap", True)
            nvim.api.win_set_option(chat_win, "linebreak", True)
            nvim.api.win_set_option(chat_win, "showbreak", "")
            # Mark chat window as preferred main for Snacks.picker
            nvim.api.win_set_var(chat_win, "snacks_main", True)

            # Focus the prompt window so user can start typing
            nvim.api.set_current_win(prompt_win)

            # Store pane state for toggling
            _pane_state["is_open"] = True
            _pane_state["direction"] = direction
            _pane_state["chat_win"] = chat_win
            _pane_state["prompt_win"] = prompt_win
    else:  # default "split" layout
        nvim.command("enew")

        if len(nvim.api.list_wins()) > 1:
            nvim.command("only")

        # Create prompt window first (at bottom)
        nvim.command("botright split")
        nvim.command(f"resize {PROMPT_HEIGHT}")
        prompt_win = nvim.api.get_current_win()
        nvim.api.win_set_option(prompt_win, "winfixheight", True)
        nvim.api.win_set_buf(prompt_win, prompt_buf)
        set_prompt_window_options(nvim, prompt_win)

        # Go back to top window and set up chat
        nvim.command("wincmd k")
        chat_win = nvim.api.get_current_win()
        nvim.api.win_set_buf(chat_win, chat_buf)
        nvim.api.win_set_option(chat_win, "wrap", True)
        nvim.api.win_set_option(chat_win, "linebreak", True)
        nvim.api.win_set_option(chat_win, "showbreak", "")
        # Mark chat window as preferred main for Snacks.picker
        nvim.api.win_set_var(chat_win, "snacks_main", True)

        # Focus the prompt window so user can start typing
        nvim.api.set_current_win(prompt_win)

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
    """Close the pane layout and return to the original window."""
    global _pane_state

    # Close the prompt window first (it's inside the pane)
    if _pane_state["prompt_win"] and nvim.api.win_is_valid(_pane_state["prompt_win"]):
        nvim.api.win_close(_pane_state["prompt_win"], True)

    # Close the chat window (the main pane)
    if _pane_state["chat_win"] and nvim.api.win_is_valid(_pane_state["chat_win"]):
        nvim.api.win_close(_pane_state["chat_win"], True)

    # Return to the original window
    if _pane_state["original_win"] and nvim.api.win_is_valid(
        _pane_state["original_win"]
    ):
        nvim.api.set_current_win(_pane_state["original_win"])

    # Reset pane state
    _pane_state["chat_win"] = None
    _pane_state["prompt_win"] = None
    _pane_state["original_win"] = None


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
