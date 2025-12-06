"""Anya Neovim Plugin"""

import pynvim
import asyncio
import threading
import os
from datetime import datetime, timezone

from . import buffers
from . import ids
from . import markers

VERSION = "0.0.1"

DEFAULT_MODEL = os.environ.get("ANYA_MODEL", "gpt-4.1")


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

    def send(self, text, conversation_id=None):
        """Send a prompt to the code agent and stream the response to the chat buffer."""
        chat_buf = self._get_chat_buffer()
        if not chat_buf:
            self.nvim.err_write("Anya: Chat buffer not found.\n")
            return
        loop = self._ensure_loop()
        asyncio.run_coroutine_threadsafe(
            self._run_agent_streaming(text, conversation_id, chat_buf.number), loop
        )

    async def _run_agent_streaming(self, text, conversation_id, chat_bufnr):
        """Run the agent with streaming and write to chat buffer."""
        from agents import Runner
        from openai.types.responses import ResponseTextDeltaEvent
        from .agents import code
        from .agents.context import NvimPluginContext

        context = NvimPluginContext(nvim=self.nvim)

        msg_id = ids.new(conversation=conversation_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        agent_name = code.name.lower()

        header = f"# {code.name}\n"
        header += markers.make_agent_message_start(
            msg_id, agent_name, DEFAULT_MODEL, timestamp
        )
        header += "\n\n"

        self.nvim.async_call(self._append_to_chat_buffer, chat_bufnr, header)

        try:
            result = Runner.run_streamed(
                starting_agent=code,
                input=text,
                context=context,
            )

            async for event in result.stream_events():
                if event.type == "raw_response_event" and isinstance(
                    event.data, ResponseTextDeltaEvent
                ):
                    delta = event.data.delta
                    if delta:
                        self.nvim.async_call(
                            self._stream_text_to_buffer, chat_bufnr, delta
                        )

            end_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            footer = "\n\n" + markers.make_message_end(msg_id, end_timestamp) + "\n"
            self.nvim.async_call(self._append_to_chat_buffer, chat_bufnr, footer)
            self.nvim.async_call(self._process_markers, chat_bufnr)

        except Exception as e:
            self.nvim.async_call(
                self._append_to_chat_buffer, chat_bufnr, f"\n\n**Error:** {e}\n"
            )
            self.nvim.async_call(self.nvim.err_write, f"Agent error: {e}\n")

    def _get_chat_buffer(self):
        """Find the chat buffer by filetype."""
        for buf in self.nvim.buffers:
            if buf.valid:
                ft = self.nvim.api.buf_get_option(buf, "filetype")
                if ft == "anya-chat":
                    return buf
        return None

    def _append_to_chat_buffer(self, bufnr, text):
        """Append text to the chat buffer (sync, instant)."""
        if not self.nvim.api.buf_is_valid(bufnr):
            return
        self.nvim.api.buf_set_option(bufnr, "modifiable", True)
        lines = text.split("\n")
        line_count = self.nvim.api.buf_line_count(bufnr)
        last_line = self.nvim.api.buf_get_lines(bufnr, line_count - 1, line_count, False)
        last_col = len(last_line[0]) if last_line else 0
        self.nvim.api.buf_set_text(
            bufnr, line_count - 1, last_col, line_count - 1, last_col, lines
        )
        self._autoscroll(bufnr)

    def _stream_text_to_buffer(self, bufnr, text):
        """Stream text to buffer using Lua animation."""
        if not self.nvim.api.buf_is_valid(bufnr):
            return
        self.nvim.exec_lua("require('anya.text').output(...)", bufnr, text)

    def _autoscroll(self, bufnr):
        """Scroll all windows showing buffer to bottom."""
        for win in self.nvim.api.list_wins():
            if self.nvim.api.win_get_buf(win) == bufnr:
                line_count = self.nvim.api.buf_line_count(bufnr)
                try:
                    self.nvim.api.win_set_cursor(win, [line_count, 0])
                except Exception:
                    pass

    def _process_markers(self, bufnr):
        """Process markers in the buffer via Lua."""
        if not self.nvim.api.buf_is_valid(bufnr):
            return
        self.nvim.exec_lua("require('anya.text')._process_markers(...)", bufnr)

    @pynvim.function("AnyaSend", sync=False)
    def anya_send(self, args):
        """Send a prompt to the agent with streaming response.

        Args:
            args[0]: The prompt text
            args[1]: Optional conversation ID
        """
        if not args:
            self.nvim.err_write("AnyaSend requires a prompt argument.\n")
            return
        text = args[0]
        conversation_id = args[1] if len(args) > 1 else None
        self.send(text, conversation_id)

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
