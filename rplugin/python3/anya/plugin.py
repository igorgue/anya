"""Anya Neovim Plugin"""

import pynvim
import asyncio
import threading
import os
import time
from datetime import datetime, timezone

from . import buffers
from . import db
from . import ids
from . import markers
from . import history
from . import fidget

VERSION = "0.0.1"

DEFAULT_MODEL = os.environ.get("ANYA_MODEL", "gpt-4.1")


def close_open_code_blocks(content: str) -> str:
    """Close any unclosed markdown code blocks in the content.

    Detects code fence markers (```, ```python, etc.) and ensures they're
    properly closed. If a fence is opened but not closed, adds closing
    backticks.

    Args:
        content: The markdown content to check

    Returns:
        The content with any unclosed code blocks closed
    """
    if not content:
        return content

    lines = content.split("\n")
    fence_stack = []  # Stack of backtick counts for open fences

    for line in lines:
        stripped = line.lstrip()

        # Check if this line starts with backticks
        if stripped.startswith("`"):
            # Count consecutive backticks at the start
            tick_count = 0
            for char in stripped:
                if char == "`":
                    tick_count += 1
                else:
                    break

            # Need at least 3 backticks to be a fence
            if tick_count >= 3:
                # Check if this closes the most recent open fence
                if fence_stack and fence_stack[-1] == tick_count:
                    fence_stack.pop()
                else:
                    # This opens a new fence
                    fence_stack.append(tick_count)

    # If there are unclosed fences, add closing backticks
    if fence_stack:
        tick_count = fence_stack[-1]
        closing_fence = "`" * tick_count
        lines.append(closing_fence)

    return "\n".join(lines)


@pynvim.plugin
class AnyaPlugin:
    def __init__(self, nvim):
        self.nvim = nvim
        self.chat_buf = None
        self.prompt_buf = None
        self._loop = None
        self._loop_thread = None
        self._db_initialized = False
        self._current_task = None  # Track current agent task for cancellation
        self._cancel_in_progress = False  # Prevent cancel spam

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

    def _ensure_db(self):
        """Ensure the database is initialized (lazy initialization)."""
        if not self._db_initialized:
            db.init_db()
            self._db_initialized = True

    @pynvim.command(
        "Anya", nargs="*", range="", sync=False, complete="customlist,AnyaComplete"
    )
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
        elif subcommand == "tab":
            self.nvim.async_call(self._open_interface, "tab")
        elif subcommand == "pane":
            # Check for direction argument
            direction = (
                args[1] if len(args) > 1 and args[1] in ["right", "left"] else "right"
            )
            self.nvim.async_call(self._open_interface, "pane", direction)
        elif subcommand == "history":
            self.nvim.exec_lua("require('anya.picker').open()")
        elif subcommand == "cancel":
            self.cancel_agent()

    def _open_interface(self, layout="split", direction=None):
        """Open the Anya interface with chat and prompt buffers.

        Args:
            layout: The layout type - "split" (default), "tab", or "pane"
            direction: For "pane" layout, the direction - "right" (default) or "left"
        """
        self.chat_buf, self.prompt_buf = buffers.new(self.nvim, layout, direction)

    def send(self, text, conversation_id=None):
        """Send a prompt to the code agent and stream the response to the chat buffer."""
        chat_buf = self._get_chat_buffer()
        if not chat_buf:
            self.nvim.err_write("Anya: Chat buffer not found.\n")
            return
        loop = self._ensure_loop()
        request_id = ids.new()
        self._current_task = asyncio.run_coroutine_threadsafe(
            self._run_agent_streaming(
                text, conversation_id, chat_buf.number, request_id
            ),
            loop,
        )

    def cancel_agent(self):
        """Cancel the current agent response and flush the queue."""
        # Prevent cancel spam
        if self._cancel_in_progress:
            return

        if self._current_task is None:
            self.nvim.err_write("Anya: No request to cancel.\n")
            return

        chat_buf = self._get_chat_buffer()
        if not chat_buf:
            self.nvim.err_write("Anya: Chat buffer not found.\n")
            return

        # Mark cancel as in progress to prevent spam
        self._cancel_in_progress = True

        # Cancel the task
        try:
            self._current_task.cancel()
        except Exception as e:
            self.nvim.err_write(f"Anya: Failed to cancel task: {e}\n")

        # Flush the streaming queue to finish outputting pending text
        self.nvim.exec_lua("require('anya.text').flush_queue()")

        # Close any open code blocks in the buffer before adding cancellation message
        buffer_content = buffers.get_buffer_content(self.nvim, chat_buf.number)
        fixed_content = close_open_code_blocks(buffer_content)

        # If blocks were closed, we need to append the closing fences
        if len(fixed_content) > len(buffer_content):
            original_lines = buffer_content.split("\n")
            fixed_lines = fixed_content.split("\n")
            if len(fixed_lines) > len(original_lines):
                added_lines = fixed_lines[len(original_lines) :]
                added_content = "\n".join(added_lines)
                self._append_to_chat_buffer(chat_buf.number, added_content + "\n")

        # Write cancellation message to chat buffer
        cancel_msg = "\n*Request cancelled by user.*\n"
        self._append_to_chat_buffer(chat_buf.number, cancel_msg)

        # Clear the task reference and cancel flag
        self._current_task = None
        self._cancel_in_progress = False

    async def _run_agent_streaming(
        self, _text, conversation_id, chat_bufnr, request_id
    ):
        """Run the agent with streaming and write to chat buffer."""
        from agents import Runner
        from openai.types.responses import ResponseTextDeltaEvent
        from .agents import code
        from .agents.context import NvimPluginContext

        context = NvimPluginContext(nvim=self.nvim)

        # Emit fidget start event
        fidget.emit_user_event(
            self.nvim,
            "AnyaRequestStarted",
            {
                "id": request_id,
                "model": DEFAULT_MODEL,
            },
        )

        buffer_content = await self._get_buffer_content_async(chat_bufnr)
        records = history.parse_buffer_content(buffer_content or "")
        llm_history = history.build_llm_history(records)

        msg_id = ids.new(conversation=conversation_id)
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        agent_name = code.name.lower()

        header = "# Anya\n"
        header += markers.make_agent_message_start(
            msg_id, agent_name, DEFAULT_MODEL, timestamp
        )
        header += "\n"

        self.nvim.async_call(self._append_to_chat_buffer, chat_bufnr, header)

        # Collect streamed content for saving
        collected_content: list[str] = []

        try:
            # Record start time
            start_time = time.time()

            result = Runner.run_streamed(
                starting_agent=code,
                input=llm_history,
                context=context,
            )

            async for event in result.stream_events():
                if event.type == "raw_response_event" and isinstance(
                    event.data, ResponseTextDeltaEvent
                ):
                    delta = event.data.delta
                    if delta:
                        collected_content.append(delta)
                        self.nvim.async_call(
                            self._stream_text_to_buffer, chat_bufnr, delta
                        )

            # Calculate duration
            end_time = time.time()
            duration_seconds = end_time - start_time

            # Format duration
            if duration_seconds >= 60:
                minutes = int(duration_seconds // 60)
                seconds = duration_seconds % 60
                duration_str = f"{minutes}m{seconds:.1f}s"
            else:
                duration_str = f"{duration_seconds:.1f}s"

            end_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            footer = "\n" + markers.make_message_end(msg_id, end_timestamp) + "\n"
            self.nvim.async_call(self._stream_text_to_buffer, chat_bufnr, footer)

            # Save agent message to database
            self._ensure_db()
            full_content = "".join(collected_content)
            db.save_message_dict(
                msg_id=msg_id,
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                author=agent_name,
                model=DEFAULT_MODEL,
                created_at=timestamp,
                ended_at=end_timestamp,
            )
            # Update conversation timestamp
            db.update_conversation_timestamp(conversation_id, end_timestamp)

            # Emit fidget finish event
            fidget.emit_user_event(
                self.nvim,
                "AnyaRequestFinished",
                {
                    "id": request_id,
                    "status": "success",
                },
            )

        except asyncio.CancelledError:
            # Handle cancellation
            end_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Close any open code blocks in the collected content
            original_content = "".join(collected_content)
            fixed_content = close_open_code_blocks(original_content)

            # If closing fences were added, append them to the buffer
            if len(fixed_content) > len(original_content):
                # Extract only what was added (the closing fence)
                original_lines = original_content.split("\n")
                fixed_lines = fixed_content.split("\n")
                if len(fixed_lines) > len(original_lines):
                    added_lines = fixed_lines[len(original_lines) :]
                    added_content = "\n".join(added_lines)
                    self.nvim.async_call(
                        self._append_to_chat_buffer, chat_bufnr, added_content + "\n"
                    )

            full_content = fixed_content

            # Add message end marker
            footer = "\n" + markers.make_message_end(msg_id, end_timestamp) + "\n"
            self.nvim.async_call(self._append_to_chat_buffer, chat_bufnr, footer)

            # Save agent message to database with whatever content was collected
            self._ensure_db()
            db.save_message_dict(
                msg_id=msg_id,
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                author=agent_name,
                model=DEFAULT_MODEL,
                created_at=timestamp,
                ended_at=end_timestamp,
            )
            # Update conversation timestamp
            db.update_conversation_timestamp(conversation_id, end_timestamp)

            # Emit fidget finish event
            fidget.emit_user_event(
                self.nvim,
                "AnyaRequestFinished",
                {
                    "id": request_id,
                    "status": "cancelled",
                },
            )

        except Exception as e:
            self.nvim.async_call(
                self._append_to_chat_buffer, chat_bufnr, f"\n\n**Error:** {e}\n"
            )
            self.nvim.async_call(self.nvim.err_write, f"Agent error: {e}\n")

            # Emit fidget error event
            fidget.emit_user_event(
                self.nvim,
                "AnyaRequestFinished",
                {
                    "id": request_id,
                    "status": "error",
                },
            )

    def _get_chat_buffer(self):
        """Find the chat buffer by filetype."""
        for buf in self.nvim.buffers:
            if buf.valid:
                ft = self.nvim.api.buf_get_option(buf, "filetype")
                if ft == "anya-chat":
                    return buf
        return None

    async def _get_buffer_content_async(self, bufnr: int) -> str:
        """Get buffer content from async context using a future."""
        import concurrent.futures

        future: concurrent.futures.Future[str] = concurrent.futures.Future()

        def get_content():
            content = buffers.get_buffer_content(self.nvim, bufnr)
            future.set_result(content)

        self.nvim.async_call(get_content)

        while not future.done():
            await asyncio.sleep(0.01)

        return future.result()

    def _append_to_chat_buffer(self, bufnr, text):
        """Append text to the chat buffer (sync, instant)."""
        if not self.nvim.api.buf_is_valid(bufnr):
            return
        self.nvim.api.buf_set_option(bufnr, "modifiable", True)
        lines = text.split("\n")
        line_count = self.nvim.api.buf_line_count(bufnr)
        last_line = self.nvim.api.buf_get_lines(
            bufnr, line_count - 1, line_count, False
        )
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

        # Save to history via Lua
        if text and text.strip():
            self.nvim.exec_lua(
                """
                local prompt_text = select(1, ...)
                if prompt_text and prompt_text ~= "" then
                    require("anya.history").add(prompt_text)
                end
                """,
                text,
            )

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
    :Anya                    Open the Anya interface (split layout)
    :Anya help               Show this help message
    :Anya open               Open the Anya interface (split layout)
    :Anya tab                Open the Anya interface in a new tab
    :Anya pane [right|left]  Open the Anya interface in a pane (default: right)
    :Anya send <prompt>      Send a prompt to the agent
    :Anya history            Open the conversation history picker
    :Anya cancel             Cancel the current agent response (Ctrl+C)
"""

    @pynvim.function("AnyaSaveConversation", sync=True)
    def save_conversation(self, args):
        """Save a new conversation to the database.

        Args:
            args[0]: Conversation ID
            args[1]: Timestamp (ISO 8601)
        """
        if len(args) < 2:
            self.nvim.err_write("AnyaSaveConversation requires (id, timestamp).\n")
            return False
        self._ensure_db()
        return db.save_conversation(args[0], args[1])

    @pynvim.function("AnyaSaveMessage", sync=True)
    def save_message(self, args):
        """Save a message to the database.

        Args:
            args[0]: Message ID
            args[1]: Conversation ID
            args[2]: Role ('user' or 'assistant')
            args[3]: Content
            args[4]: Author (optional)
            args[5]: Model (optional)
            args[6]: Created at timestamp (optional)
            args[7]: Ended at timestamp (optional)
        """
        if len(args) < 4:
            self.nvim.err_write(
                "AnyaSaveMessage requires (msg_id, conv_id, role, content).\n"
            )
            return False
        self._ensure_db()
        return db.save_message_dict(
            msg_id=args[0],
            conversation_id=args[1],
            role=args[2],
            content=args[3],
            author=args[4] if len(args) > 4 else None,
            model=args[5] if len(args) > 5 else None,
            created_at=args[6] if len(args) > 6 else None,
            ended_at=args[7] if len(args) > 7 else None,
        )

    @pynvim.function("AnyaListConversations", sync=True)
    def list_conversations(self, args):
        """List recent conversations.

        Args:
            args[0]: Limit (optional, default 50)
            args[1]: Offset (optional, default 0)

        Returns:
            List of {id, title, created_at, updated_at}
        """
        self._ensure_db()
        limit = args[0] if args else 50
        offset = args[1] if len(args) > 1 else 0
        return db.list_conversations(limit, offset)

    @pynvim.function("AnyaLoadConversation", sync=True)
    def load_conversation(self, args):
        """Load a full conversation with messages.

        Args:
            args[0]: Conversation ID

        Returns:
            {conversation: {...}, messages: [...]} or None
        """
        if not args:
            self.nvim.err_write("AnyaLoadConversation requires a conversation ID.\n")
            return None
        self._ensure_db()
        return db.load_conversation(args[0])

    @pynvim.function("AnyaUpdateConversationTitle", sync=True)
    def update_conversation_title(self, args):
        """Update a conversation's title.

        Args:
            args[0]: Conversation ID
            args[1]: Title
        """
        if len(args) < 2:
            self.nvim.err_write("AnyaUpdateConversationTitle requires (id, title).\n")
            return False
        self._ensure_db()
        return db.update_conversation_title(args[0], args[1])

    @pynvim.function("AnyaDeleteConversation", sync=True)
    def delete_conversation(self, args):
        """Delete a conversation and its messages.

        Args:
            args[0]: Conversation ID
        """
        if not args:
            self.nvim.err_write("AnyaDeleteConversation requires a conversation ID.\n")
            return False
        self._ensure_db()
        return db.delete_conversation(args[0])

    @pynvim.function("AnyaRebuildBufferContent", sync=True)
    def rebuild_buffer_content(self, args):
        """Rebuild buffer content from a conversation ID.

        Args:
            args[0]: Conversation ID

        Returns:
            Buffer content string or None
        """
        if not args:
            self.nvim.err_write(
                "AnyaRebuildBufferContent requires a conversation ID.\n"
            )
            return None
        self._ensure_db()
        data = db.load_conversation(args[0])
        if not data:
            return None
        return db.rebuild_buffer_content(data["conversation"], data["messages"])

    @pynvim.function("AnyaCompleteAsync", sync=False)
    def anya_complete_async(self, args):
        """Provide async file path completions for @mentions.

        Args:
            args[0]: Base path to complete
            args[1]: Callback ID
        """
        if len(args) < 2:
            self.nvim.err_write("AnyaCompleteAsync requires base and callback_id.\n")
            return
        base, callback_id = args
        buffers.get_file_completions_async(self.nvim, base, callback_id)
