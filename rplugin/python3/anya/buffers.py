from pynvim import Nvim

CHAT_TITLE = "Chat"
PROMPT_TITLE = "Prompt"
PROMPT_HEIGHT = 8


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


def new(nvim: Nvim) -> tuple[object]:
    """Create the Anya UI layout with chat and prompt buffers."""
    chat_buf = None
    prompt_buf = None

    for buf in nvim.buffers:
        if buf.name.endswith(CHAT_TITLE):
            chat_buf = buf
        elif buf.name.endswith(PROMPT_TITLE):
            prompt_buf = buf

    if not chat_buf or not chat_buf.valid:
        chat_buf = nvim.api.create_buf(False, True)
        nvim.api.buf_set_name(chat_buf, CHAT_TITLE)
        nvim.api.buf_set_option(chat_buf, "filetype", "anya-chat")
        nvim.api.buf_set_option(chat_buf, "buftype", "nofile")
        nvim.api.buf_set_option(chat_buf, "swapfile", False)

    if not prompt_buf or not prompt_buf.valid:
        prompt_buf = nvim.api.create_buf(False, True)
        nvim.api.buf_set_name(prompt_buf, PROMPT_TITLE)
        nvim.api.buf_set_option(prompt_buf, "filetype", "anya-prompt")
        nvim.api.buf_set_option(prompt_buf, "buftype", "nofile")
        nvim.api.buf_set_option(prompt_buf, "swapfile", False)

    nvim.command("enew")

    if len(nvim.api.list_wins()) > 1:
        nvim.command("only")

    chat_win = nvim.api.get_current_win()

    nvim.api.win_set_buf(chat_win, chat_buf)
    nvim.api.win_set_option(chat_win, "wrap", True)
    nvim.api.win_set_option(chat_win, "linebreak", True)

    nvim.command("botright split")
    nvim.command(f"resize {PROMPT_HEIGHT}")
    nvim.api.win_set_option(0, "winfixheight", True)
    nvim.api.win_set_buf(0, prompt_buf)

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
