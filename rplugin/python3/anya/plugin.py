"""Anya Neovim Plugin"""

import pynvim
import asyncio
import threading

VERSION = "0.0.1"

CHAT_TITLE = "Chat"
PROMPT_TITLE = "Prompt"
PROMPT_HEIGHT = 8


@pynvim.plugin
class AnyaPlugin:
    def __init__(self, nvim):
        self.nvim = nvim
        self.chat_buf = None
        self.prompt_buf = None
        self._loop = None
        self._loop_thread = None

    def _ensure_loop(self):
        """Ensure the asyncio event loop is running (lazy initialization)."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
            self._loop_thread.start()
        return self._loop

    def _run_loop(self):
        """Run the event loop forever in a background thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @pynvim.command("Anya", nargs="*", range="", sync=False)
    def main_cmd(self, args, _range):
        subcommand = args[0] if args else None

        if subcommand is None:
            self.nvim.async_call(self._open_interface)
        elif subcommand == "help":
            self.nvim.out_write(self._help_text())
        elif subcommand == "open":
            self.nvim.async_call(self._open_interface)
        elif subcommand == "send":
            if len(args) < 2:
                self.nvim.err_write("'send' command requires text argument.\n")
                return
            text = " ".join(args[1:])
            self.send(text)

    def _open_interface(self):
        """Create the Anya UI layout with chat and prompt buffers."""
        chat_buf = None
        prompt_buf = None

        for buf in self.nvim.buffers:
            if buf.name.endswith(CHAT_TITLE):
                chat_buf = buf
            elif buf.name.endswith(PROMPT_TITLE):
                prompt_buf = buf

        if not chat_buf or not chat_buf.valid:
            chat_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(chat_buf, CHAT_TITLE)
            self.nvim.api.buf_set_option(chat_buf, "filetype", "anya-chat")
            self.nvim.api.buf_set_option(chat_buf, "buftype", "nofile")
            self.nvim.api.buf_set_option(chat_buf, "swapfile", False)

        if not prompt_buf or not prompt_buf.valid:
            prompt_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(prompt_buf, PROMPT_TITLE)
            self.nvim.api.buf_set_option(prompt_buf, "filetype", "anya-prompt")
            self.nvim.api.buf_set_option(prompt_buf, "buftype", "nofile")
            self.nvim.api.buf_set_option(prompt_buf, "swapfile", False)

        self.nvim.command("enew")

        if len(self.nvim.api.list_wins()) > 1:
            self.nvim.command("only")

        chat_win = self.nvim.api.get_current_win()

        self.nvim.api.win_set_buf(chat_win, chat_buf)
        self.nvim.api.win_set_option(chat_win, "wrap", True)
        self.nvim.api.win_set_option(chat_win, "linebreak", True)

        self.nvim.command("botright split")
        self.nvim.command(f"resize {PROMPT_HEIGHT}")
        self.nvim.api.win_set_buf(0, prompt_buf)

        self.chat_buf = chat_buf
        self.prompt_buf = prompt_buf

    def _help_text(self):
        return f"""anya v{VERSION}

Usage:
    :Anya                Open the Anya interface
    :Anya help           Show this help message
    :Anya open           Open the Anya interface
    :Anya send <prompt>  Send a prompt to the agent
"""

    def send(self, text):
        """Send a prompt to the code agent and display the response."""
        # Lazy import heavy dependencies
        from agents import Runner
        from .agents import code
        from .agents.context import NvimPluginContext

        self.nvim.out_write(f"Sending: {text}\n")
        context = NvimPluginContext(nvim=self.nvim)
        loop = self._ensure_loop()

        async def run_agent():
            try:
                result = await Runner.run(
                    starting_agent=code,
                    input=text,
                    context=context,
                )
                response = result.final_output or "[No response from agent]"
                self.nvim.async_call(self.nvim.out_write, f"\n{response}\n")
            except Exception as e:
                self.nvim.async_call(self.nvim.err_write, f"Agent error: {e}\n")

        asyncio.run_coroutine_threadsafe(run_agent(), loop)
