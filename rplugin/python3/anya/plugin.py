"""Anya Neovim Plugin"""

import pynvim
import asyncio
import threading
from datetime import datetime, timezone

from . import buffers
from . import ids

VERSION = "0.0.1"


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
        """Open the Anya interface with chat and prompt buffers."""
        self.chat_buf, self.prompt_buf = buffers.new(self.nvim)

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

    @pynvim.function("AnyaNewConversationId", sync=True)
    def new_conversation_id(self, args):
        """Generate a new conversation ID."""
        return ids.new()

    @pynvim.function("AnyaNewMessageId", sync=True)
    def new_message_id(self, args):
        """Generate a new message ID within a conversation."""
        conversation_id = args[0] if args else None
        return ids.new(conversation=conversation_id)

    @pynvim.function("AnyaTimestamp", sync=True)
    def timestamp(self, args):
        """Get current UTC timestamp in ISO 8601 format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _help_text(self):
        return f"""anya v{VERSION}

Usage:
    :Anya                Open the Anya interface
    :Anya help           Show this help message
    :Anya open           Open the Anya interface
    :Anya send <prompt>  Send a prompt to the agent
"""
