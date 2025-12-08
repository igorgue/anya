"""Anya Neovim Plugin"""

import pynvim
import asyncio
import threading
import os
import time
import uuid
from datetime import datetime, timezone

from . import buffers
from . import db
from . import ids
from . import markers
from . import history
from . import fidget
from .mcp_loader import MCPManager

VERSION = "0.0.1"

DEFAULT_MODEL = os.environ.get("ANYA_MODEL", "gpt-3-turbo")


def filter_anya_markers(text: str, in_marker: bool) -> tuple[str, bool]:
    """Filter out anya marker comments from streaming text.

    Only filters markers that look like anya internal markers (<!-- am:, <!-- at:, <!-- ac:).
    Preserves legitimate HTML comments that users may want in their output.

    Handles markers that span multiple chunks by tracking state.

    Args:
        text: The text chunk to filter
        in_marker: Whether we're currently inside an anya marker

    Returns:
        Tuple of (filtered_text, still_in_marker)
    """
    import re

    # Only match anya-specific marker patterns
    marker_pattern = re.compile(r"<!-- a[mtc]:")

    result = []
    i = 0
    while i < len(text):
        if in_marker:
            # Look for end of marker
            end_idx = text.find("-->", i)
            if end_idx != -1:
                # Found end, skip to after it
                i = end_idx + 3
                in_marker = False
            else:
                # Still in marker, discard rest of text
                break
        else:
            # Look for start of anya marker (<!-- am:, <!-- at:, <!-- ac:)
            match = marker_pattern.search(text, i)
            if match:
                start_idx = match.start()
                # Add text before marker
                result.append(text[i:start_idx])
                # Check if marker ends in this chunk
                end_idx = text.find("-->", start_idx + 8)
                if end_idx != -1:
                    # Complete marker in this chunk, skip it
                    i = end_idx + 3
                else:
                    # Marker continues beyond this chunk
                    in_marker = True
                    break
            else:
                # No marker start, add rest of text
                result.append(text[i:])
                break
    return "".join(result), in_marker


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
        self._streaming_started = False  # Track if we've received any content
        self._request_cancelled = False  # Flag for async handler to check
        self.session_id = str(uuid.uuid4())  # Session ID for this Neovim instance
        self.allowed_commands = set()  # Persist allowed commands across agent runs
        self._tool_fold_open = False  # Track if a tool fold is currently open
        self._mcp_manager = MCPManager(nvim)  # MCP server manager with caching

        # Start MCP server connection in background on plugin load
        mcp_enabled = os.environ.get("ANYA_DISABLE_MCP", "0") != "1"
        if mcp_enabled and self._mcp_manager.load_configs():
            loop = self._ensure_loop()
            asyncio.run_coroutine_threadsafe(
                self._mcp_manager.get_connected_servers(), loop
            )

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

    def _set_tool_fold_open(self, is_open: bool):
        """Set the tool fold open state and expose it via vim global variable."""
        self._tool_fold_open = bool(is_open)
        self.nvim.vars["anya_tool_fold_open"] = bool(is_open)

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

        # Pre-connect MCP servers in background for faster first message
        if not self._mcp_manager.is_loaded():
            mcp_enabled = os.environ.get("ANYA_DISABLE_MCP", "0") != "1"
            if mcp_enabled:
                loop = self._ensure_loop()
                asyncio.run_coroutine_threadsafe(
                    self._mcp_manager.get_connected_servers(), loop
                )

    def send(self, text, conversation_id=None):
        """Send a prompt to the code agent and stream the response to the chat buffer."""
        # Prevent concurrent requests - check if a task is still running
        if self._current_task is not None and not self._current_task.done():
            self.nvim.err_write(
                "Anya: Please wait for the current response to complete.\n"
            )
            return

        chat_buf = self._get_chat_buffer()
        if not chat_buf:
            self.nvim.err_write("Anya: Chat buffer not found.\n")
            return
        loop = self._ensure_loop()
        request_id = ids.new()
        self._streaming_started = False  # Reset streaming flag for new request
        self._request_cancelled = False  # Reset cancellation flag for new request
        self._current_task = asyncio.run_coroutine_threadsafe(
            self._run_agent_streaming(
                text, conversation_id, chat_buf.number, request_id
            ),
            loop,
        )

    def cancel_agent(self):
        """Cancel the current agent response and flush the queue."""
        # Only allow cancellation if streaming has actually started
        if not self._streaming_started or self._current_task is None:
            return

        # Prevent cancel spam
        if self._cancel_in_progress:
            return

        chat_buf = self._get_chat_buffer()
        if not chat_buf:
            return

        # Mark cancel as in progress to prevent spam
        self._cancel_in_progress = True
        self._request_cancelled = True  # Signal async handler to abort

        # Cancel the task
        try:
            self._current_task.cancel()
        except Exception as e:
            self.nvim.err_write(f"Anya: Failed to cancel task: {e}\n")

        # Flush the streaming queue to finish outputting pending text
        self.nvim.exec_lua("require('anya.text').flush_queue()")

        # Force reset the request state in Lua to unlock the UI
        self.nvim.exec_lua("require('anya.conversation').force_reset_request_state()")

        # Only show cancellation message if streaming actually started
        if self._streaming_started:
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
            cancel_msg = "\n> cancelled 󱋟 "
            self._append_to_chat_buffer(chat_buf.number, cancel_msg)

        # Always emit finish event to notify Lua that request is done
        # This ensures the UI is unlocked even if cancel happened before streaming started
        fidget.emit_user_event(
            self.nvim,
            "AnyaRequestFinished",
            {
                "id": "cancelled",
                "status": "cancelled",
            },
        )

        # Clear the task reference and cancel flag
        self._current_task = None
        self._streaming_started = False
        self._cancel_in_progress = False

    async def _run_agent_streaming(
        self, _text, conversation_id, chat_bufnr, request_id
    ):
        """Run the agent with streaming and write to chat buffer."""
        from agents import Runner
        from openai.types.responses import ResponseTextDeltaEvent
        from .agents import code, create_code_agent
        from .agents.context import NvimPluginContext

        context = NvimPluginContext(
            nvim=self.nvim,
            session_id=self.session_id,
            allowed_commands=self.allowed_commands,
        )

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

        # Initialize tool fold state at start of request
        self.nvim.async_call(self._set_tool_fold_open, False)

        # Collect streamed content for saving
        collected_content: list[str] = []
        parallel_tools: list[dict] = []  # Collect parallel tool calls
        pending_tool_outputs: list[str] = []  # Collect outputs for parallel tools
        expected_outputs = 0  # Number of outputs we're waiting for
        tool_was_called = False  # Track if any tool was called (for unclosed folds)
        in_anya_marker = False  # Track if LLM is outputting an anya marker
        needs_blank_before_text = False  # Add blank line before next text after tool
        last_output_was_marker = (
            True  # Track if last output was a marker (header counts)
        )

        try:
            # Record start time
            start_time = time.time()

            # Get MCP servers (uses cached connections if available)
            agent_for_run = code
            mcp_enabled = os.environ.get("ANYA_DISABLE_MCP", "0") != "1"
            if mcp_enabled:
                connected = await self._mcp_manager.get_connected_servers()
                if connected:
                    agent_for_run = create_code_agent(mcp_servers=connected)

            result = Runner.run_streamed(
                starting_agent=agent_for_run,
                input=llm_history,
                context=context,
            )

            async for event in result.stream_events():
                # Check if cancellation was requested
                if self._request_cancelled:
                    raise asyncio.CancelledError()

                # Handle higher-level run item events for tool calls and outputs
                if event.type == "run_item_stream_event":
                    item = event.item
                    item_type = getattr(item, "type", None)

                    if item_type == "tool_call_item":
                        raw_item = getattr(item, "raw_item", None)
                        tool_name = (
                            getattr(item, "name", None) or getattr(raw_item, "name", "")
                            if raw_item
                            else ""
                        )
                        tool_args = (
                            getattr(item, "arguments", "")
                            or getattr(raw_item, "arguments", "")
                            if raw_item
                            else ""
                        )
                        if tool_name:
                            tool_was_called = True
                            self._streaming_started = True

                            # Use edit_pending for edit tool, tool_pending for others
                            status = (
                                "edit_pending"
                                if tool_name == "edit"
                                else "tool_pending"
                            )
                            parallel_tools.append(
                                {
                                    "name": tool_name,
                                    "args": tool_args,
                                    "status": status,
                                }
                            )
                            expected_outputs += 1

                            # Skip header output for edit tool - edit_view handles its own display
                            if tool_name == "edit":
                                pass  # edit_view will render its own header
                            else:
                                # Build combined header with all tools so far
                                tool_headers = [
                                    self._format_tool_header(t["name"], t["args"])
                                    for t in parallel_tools
                                ]
                                combined_header = " | ".join(tool_headers)

                                if len(parallel_tools) == 1:
                                    # First tool - output header with pending marker
                                    # Add blank line if last output wasn't a marker
                                    prefix = "" if last_output_was_marker else "\n"
                                    # Use the status from the tool (edit_pending or tool_pending)
                                    pending_header = (
                                        prefix
                                        + combined_header
                                        + "\n"
                                        + markers.make_marker("fold_start", status)
                                        + "\n"
                                    )
                                    collected_content.append(pending_header)
                                    if not self._request_cancelled:
                                        self.nvim.async_call(
                                            self._stream_text_to_buffer,
                                            chat_bufnr,
                                            pending_header,
                                        )
                                        # Mark that a tool fold is now open
                                        self.nvim.async_call(
                                            self._set_tool_fold_open, True
                                        )
                                    last_output_was_marker = True
                                else:
                                    # Additional parallel tool - update header line
                                    if not self._request_cancelled:
                                        self.nvim.async_call(
                                            self._update_tool_header_line,
                                            chat_bufnr,
                                            combined_header,
                                        )

                    elif item_type == "tool_call_output_item":
                        tool_output = getattr(item, "output", "")
                        pending_tool_outputs.append(tool_output)

                        if (
                            len(pending_tool_outputs) >= expected_outputs
                            and expected_outputs > 0
                        ):
                            # Check if this is an edit tool (don't auto-update markers)
                            is_edit_tool = any(
                                t["name"] == "edit" for t in parallel_tools
                            )

                            # Check if any output indicates failure
                            has_failure = any(
                                "error" in o.lower()
                                for o in pending_tool_outputs
                                if isinstance(o, str)
                            )

                            # Update pending markers to success or failure
                            # Skip marker update for edit tool - user will approve/reject
                            if not self._request_cancelled and not is_edit_tool:
                                if has_failure:
                                    self.nvim.async_call(
                                        self._flush_and_update_pending_markers_to_failure,
                                        chat_bufnr,
                                    )
                                else:
                                    self.nvim.async_call(
                                        self._flush_and_update_pending_markers,
                                        chat_bufnr,
                                    )

                            all_outputs = "\n".join(
                                o for o in pending_tool_outputs if o
                            )

                            # For edit tool, skip rendering - edit tool handles its own UI
                            # The tool output is the result message (EDIT_APPLIED, etc)
                            if is_edit_tool:
                                # Don't render anything - edit tool already rendered via UI
                                pass
                            elif all_outputs:
                                # Wrap MCP server output with backticks
                                wrapped_output = f"``````\n{all_outputs}\n``````"
                                collected_content.append(wrapped_output)
                                if not self._request_cancelled:
                                    self.nvim.async_call(
                                        self._stream_text_to_buffer,
                                        chat_bufnr,
                                        wrapped_output,
                                    )

                            # Skip fold markers for edit tool - edit_view handles its own display
                            if not is_edit_tool:
                                fold_end_marker = (
                                    "\n" + markers.make_marker("fold_end") + "\n"
                                )
                                collected_content.append(fold_end_marker)
                                if not self._request_cancelled:
                                    self.nvim.async_call(
                                        self._stream_text_to_buffer,
                                        chat_bufnr,
                                        fold_end_marker,
                                    )
                                    # Mark that the tool fold is now closed
                                    self.nvim.async_call(
                                        self._set_tool_fold_open, False
                                    )

                            pending_tool_outputs = []
                            expected_outputs = 0
                            tool_was_called = False
                            parallel_tools = []
                            needs_blank_before_text = True
                            last_output_was_marker = True

                if hasattr(event, "data") and isinstance(
                    event.data, ResponseTextDeltaEvent
                ):
                    delta = event.data.delta
                    if delta:
                        # Filter out anya markers from LLM output
                        # Defense in depth: if markers leak into history, filter them here
                        delta, in_anya_marker = filter_anya_markers(
                            delta, in_anya_marker
                        )
                        if not delta:
                            continue

                        # Mark that streaming has started
                        self._streaming_started = True

                        # Add blank line before text if we just finished a tool call
                        if needs_blank_before_text:
                            collected_content.append("\n")
                            if not self._request_cancelled:
                                self.nvim.async_call(
                                    self._stream_text_to_buffer, chat_bufnr, "\n"
                                )
                            needs_blank_before_text = False

                        # LLM text output - this is the agent's response (after tool results)
                        collected_content.append(delta)
                        last_output_was_marker = False

                        # Don't queue text if cancellation is in progress
                        if not self._request_cancelled:
                            self.nvim.async_call(
                                self._stream_text_to_buffer, chat_bufnr, delta
                            )

            # Flush any remaining parallel tools before message end
            # (Headers already displayed with pending status, just clear the list)
            parallel_tools = []

            end_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            # Close any open tool folds before message end marker
            if tool_was_called:
                fold_end_marker = "\n" + markers.make_marker("fold_end")
                collected_content.append(fold_end_marker)
                self.nvim.async_call(
                    self._stream_text_to_buffer, chat_bufnr, fold_end_marker
                )
                # Mark that the tool fold is now closed
                self.nvim.async_call(self._set_tool_fold_open, False)
            footer = "\n" + markers.make_message_end(msg_id, end_timestamp) + "\n"
            self.nvim.async_call(self._stream_text_to_buffer, chat_bufnr, footer)

            # Flush streaming queue and save agent message to database
            # We do this inline after the footer is sent (but before returning)
            # to ensure the buffer has all content and markers are finalized
            def save_after_streaming():
                self._save_agent_message_to_db(
                    chat_bufnr,
                    msg_id,
                    agent_name,
                    conversation_id,
                    timestamp,
                    end_timestamp,
                )

            self.nvim.async_call(save_after_streaming)

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

            # Add message end marker
            if tool_was_called:
                fold_end_marker = "\n" + markers.make_marker("fold_end")
                full_content = fixed_content + fold_end_marker
                self.nvim.async_call(
                    self._append_to_chat_buffer, chat_bufnr, fold_end_marker
                )
                # Mark that the tool fold is now closed
                self.nvim.async_call(self._set_tool_fold_open, False)
            else:
                full_content = fixed_content
            footer = "\n" + markers.make_message_end(msg_id, end_timestamp) + "\n"
            self.nvim.async_call(self._append_to_chat_buffer, chat_bufnr, footer)

            # Flush streaming queue and save agent message to database
            # We do this inline after the footer is sent (but before returning)
            # to ensure the buffer has all content and markers are finalized
            def save_after_streaming():
                self._save_agent_message_to_db(
                    chat_bufnr,
                    msg_id,
                    agent_name,
                    conversation_id,
                    timestamp,
                    end_timestamp,
                )

            self.nvim.async_call(save_after_streaming)

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
        finally:
            # Always clear the current task reference when done
            self._current_task = None
            self._request_cancelled = False
            # Ensure tool fold state is reset
            self.nvim.async_call(self._set_tool_fold_open, False)

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

    def _render_edit_blocks(self, bufnr, edit_str):
        """Render SEARCH/REPLACE edit blocks using the dedicated edit_view.

        Args:
            bufnr: Buffer number
            edit_str: String containing one or more SEARCH/REPLACE blocks
        """
        if not self.nvim.api.buf_is_valid(bufnr):
            return

        from . import search_replace

        blocks = search_replace.parse_search_replace_blocks(edit_str)

        if not blocks:
            return

        for block in blocks:
            try:
                self.nvim.exec_lua(
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
                self.nvim.err_write(f"Failed to render edit block: {e}\n")

        # Setup keymaps after rendering
        self.nvim.exec_lua(
            "require('anya.edit_view').setup_keymaps(...)",
            bufnr,
        )

        # Autoscroll to show the edit blocks
        self._autoscroll(bufnr)

    def _flush_parallel_tools(
        self,
        tools: list[dict],
        collected_content: list[str],
        chat_bufnr: int,
    ):
        """Flush collected parallel tool calls as a single combined output block.

        Args:
            tools: List of {name, args, status} dicts
            collected_content: Content list to append to
            chat_bufnr: Buffer number for streaming
        """
        if not tools:
            return

        # Format all tools on same line with pipe separators
        tool_headers = []
        for tool in tools:
            formatted = self._format_tool_header(tool["name"], tool["args"])
            tool_headers.append(formatted)

        # Create single combined output with all tools on same line
        combined = " | ".join(tool_headers)
        # Use the status from the first tool (all should be same for parallel execution)
        status = tools[0]["status"]

        # Add fold_start marker after the tool headers
        output = combined + "\n" + markers.make_marker("fold_start", status) + "\n"
        collected_content.append(output)

        # Stream to buffer
        if not self._request_cancelled:
            self.nvim.async_call(
                self._stream_text_to_buffer,
                chat_bufnr,
                output,
            )

    def _format_tool_header(self, tool_name: str, tool_args: str) -> str:
        """Format a tool header without markers (for use in parallel tool display).

        Args:
            tool_name: The name of the tool function
            tool_args: The arguments passed to the tool (JSON string)

        Returns:
            Formatted header like **tool_name | arg**
        """
        import json
        import re

        from .tools.utils import format_tool_header

        # Try to extract first argument from JSON args
        first_arg = ""
        try:
            if tool_args:
                args_dict = json.loads(tool_args)

                # Special handling for edit tool - extract filename from edit_blocks
                if tool_name == "edit" and "edit_blocks" in args_dict:
                    edit_blocks = args_dict["edit_blocks"]
                    # Extract filename from first line or before <<<<<<< SEARCH
                    lines = edit_blocks.strip().split("\n")
                    for line in lines:
                        line = line.strip()
                        if (
                            line
                            and not line.startswith("<")
                            and not line.startswith("=")
                        ):
                            # This looks like a filename
                            first_arg = line
                            break
                    if not first_arg:
                        first_arg = "(edit)"
                else:
                    # Get the first non-empty value
                    for key, value in args_dict.items():
                        if isinstance(value, str):
                            # Truncate long strings (like edit blocks)
                            first_arg = value[:50] if len(value) > 50 else value
                            # Remove newlines for display
                            first_arg = first_arg.replace("\n", " ").strip()
                        else:
                            first_arg = str(value)
                        break
        except (json.JSONDecodeError, AttributeError):
            first_arg = tool_args[:50] if tool_args else ""

        if not first_arg:
            first_arg = "(no args)"

        return format_tool_header(tool_name, first_arg)

    def _format_tool_call(self, tool_name: str, tool_args: str) -> str:
        """Format a tool call as a header with opening fold marker.

        Args:
            tool_name: The name of the tool function
            tool_args: The arguments passed to the tool (JSON string)

        Returns:
            Formatted header with opening fold marker
        """
        return self._format_tool_call_with_status(tool_name, tool_args, "tool_pending")

    def _format_tool_call_with_status(
        self, tool_name: str, tool_args: str, status: str
    ) -> str:
        """Format a tool call as a header with opening fold marker and status.

        Args:
            tool_name: The name of the tool function
            tool_args: The arguments passed to the tool (JSON string)
            status: The tool status marker (tool_pending, tool_success, tool_failure)

        Returns:
            Formatted header with opening fold marker and status
        """
        import json

        from .tools.utils import format_tool_header

        # Try to extract first argument from JSON args
        first_arg = ""
        try:
            if tool_args:
                args_dict = json.loads(tool_args)
                # Get the first non-empty value
                for key, value in args_dict.items():
                    if isinstance(value, str):
                        first_arg = value
                    else:
                        first_arg = str(value)
                    break
        except (json.JSONDecodeError, AttributeError):
            first_arg = tool_args[:50] if tool_args else ""

        if not first_arg:
            first_arg = "(no args)"

        # Format header
        header = format_tool_header(tool_name, first_arg)

        # Add opening fold marker with status and newline so it's on its own line
        return header + "\n" + markers.make_marker("fold_start", status) + "\n"

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

    def _update_pending_markers_to_success(self, bufnr):
        """Update all tool_pending markers to tool_success in the buffer.

        Scans the buffer for fold_start markers with tool_pending status
        and replaces them with tool_success status.
        """
        if not self.nvim.api.buf_is_valid(bufnr):
            return

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)
        pending_marker = markers.make_marker("fold_start", "tool_pending")
        success_marker = markers.make_marker("fold_start", "tool_success")

        for i, line in enumerate(lines):
            if line == pending_marker:
                self.nvim.api.buf_set_lines(bufnr, i, i + 1, False, [success_marker])

        # Reprocess markers to update extmarks
        self._process_markers(bufnr)

    def _flush_and_update_pending_markers(self, bufnr):
        """Flush the streaming queue and update pending markers to success.

        This ensures markers are written to buffer before we try to update them.
        """
        self.nvim.exec_lua("require('anya.text').flush_queue()")
        self._update_pending_markers_to_success(bufnr)

    def _flush_and_update_pending_markers_to_failure(self, bufnr):
        """Flush the streaming queue and update pending markers to failure.

        This ensures markers are written to buffer before we try to update them.
        """
        self.nvim.exec_lua("require('anya.text').flush_queue()")
        self._update_pending_markers_to_failure(bufnr)

    def _update_pending_markers_to_failure(self, bufnr):
        """Update all tool_pending markers to tool_failure in the buffer.

        Scans the buffer for fold_start markers with tool_pending status
        and replaces them with tool_failure status.
        """
        if not self.nvim.api.buf_is_valid(bufnr):
            return

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)
        pending_marker = markers.make_marker("fold_start", "tool_pending")
        failure_marker = markers.make_marker("fold_start", "tool_failure")

        for i, line in enumerate(lines):
            if line == pending_marker:
                self.nvim.api.buf_set_lines(bufnr, i, i + 1, False, [failure_marker])

        # Reprocess markers to update extmarks
        self._process_markers(bufnr)

    def _update_tool_header_line(self, bufnr, new_header: str):
        """Update the most recent tool header line with a new combined header.

        Finds the last line containing a tool header (starting with **) and
        replaces it with the new combined header.
        """
        if not self.nvim.api.buf_is_valid(bufnr):
            return

        self.nvim.exec_lua("require('anya.text').flush_queue()")

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith("**") and lines[i].endswith("**"):
                self.nvim.api.buf_set_lines(bufnr, i, i + 1, False, [new_header])
                self._process_markers(bufnr)
                break

    def _save_agent_message_to_db(
        self,
        chat_bufnr,
        msg_id,
        agent_name,
        conversation_id,
        timestamp,
        end_timestamp,
    ):
        """Save agent message to database reading final buffer content with correct markers.

        This runs on the main thread via async_call, ensuring all Neovim API calls
        are properly synchronized. Ensures pending markers have been updated to
        success/failure before extracting and saving the message.
        """
        # Check if buffer is still valid
        if not self.nvim.api.buf_is_valid(chat_bufnr):
            self.nvim.err_write(
                f"Warning: Chat buffer {chat_bufnr} is no longer valid\n"
            )
            return

        # Flush the streaming queue to ensure all buffer updates are written
        self.nvim.exec_lua("require('anya.text').flush_queue()")

        # Initialize database if needed
        self._ensure_db()

        # Read the final buffer content (after all marker updates)
        # The buffer contains the entire conversation, so we need to extract just
        # this message's content between its start and end markers
        buf_lines = self.nvim.api.buf_get_lines(chat_bufnr, 0, -1, False)
        buf_content = "\n".join(buf_lines)

        # Extract just the message content (between message start and end markers)
        msg_start_marker = markers.make_agent_message_start(
            msg_id, agent_name, DEFAULT_MODEL, timestamp
        )
        msg_end_marker = markers.make_message_end(msg_id, end_timestamp)

        message_content = None
        try:
            # Find start marker (may have extra whitespace due to marker format)
            start_idx = next(
                i for i, line in enumerate(buf_lines) if msg_start_marker in line
            )
            # Find end marker (backwards from end)
            end_idx = next(
                i
                for i in range(len(buf_lines) - 1, start_idx, -1)
                if msg_end_marker in buf_lines[i]
            )
            # Extract content between markers
            message_lines = buf_lines[start_idx + 1 : end_idx]
            message_content = "\n".join(message_lines)
        except (StopIteration, IndexError):
            # Fallback: if we can't find markers, log and abort
            self.nvim.err_write(
                f"Warning: Could not find message markers for {msg_id}\n"
            )
            return

        if not message_content:
            self.nvim.err_write(f"Warning: Empty message content for {msg_id}\n")
            return

        # Extract markers from the message content (markers are removed from content)
        cleaned_content, markers_json = history.extract_markers_from_content(
            message_content
        )

        # Save to database with correct marker status
        db.save_message_dict(
            msg_id=msg_id,
            conversation_id=conversation_id,
            role="assistant",
            content=cleaned_content,
            author=agent_name,
            model=DEFAULT_MODEL,
            created_at=timestamp,
            ended_at=end_timestamp,
            markers=markers_json,
        )

        # Update conversation timestamp
        db.update_conversation_timestamp(conversation_id, end_timestamp)

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
            args[8]: Markers JSON (optional)
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
            markers=args[8] if len(args) > 8 else None,
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

    @pynvim.function("AnyaApplyEdit", sync=True)
    def apply_edit(self, args):
        """Apply a pending edit block from the chat buffer.

        Finds the edit block content at the given line, applies the patch,
        and updates the marker to edit_applied or edit_failed.

        Args:
            args[0]: Buffer number
            args[1]: Line number of the edit header (1-indexed)

        Returns:
            dict with {success: bool, message: str}
        """
        if len(args) < 2:
            return {"success": False, "message": "Requires bufnr and line_num"}

        bufnr = args[0]
        header_line = args[1]  # 1-indexed from Lua

        # Get the buffer content
        if not self.nvim.api.buf_is_valid(bufnr):
            return {"success": False, "message": "Invalid buffer"}

        if header_line is None:
            return {"success": False, "message": "No header line provided"}

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)

        # Find the fold_start marker after the header line
        # header_line is 1-indexed, convert to 0-indexed for array access
        fold_start_idx = None
        fold_end_idx = None

        for i in range(
            header_line - 1, len(lines)
        ):  # header_line - 1 to convert to 0-indexed
            line = lines[i]
            if "<!-- at:" in line and "fold_start" in line:
                fold_start_idx = i
            elif "<!-- at:" in line and "fold_end" in line:
                fold_end_idx = i
                break

        if fold_start_idx is None or fold_end_idx is None:
            return {"success": False, "message": "Could not find edit block boundaries"}

        # Extract the content between fold markers
        edit_content = "\n".join(lines[fold_start_idx + 1 : fold_end_idx])

        # Apply the edit using search_replace
        from . import search_replace

        cwd = self.nvim.call("getcwd")
        results = search_replace.apply_edit_blocks(edit_content, cwd)

        if not results:
            return {"success": False, "message": "No edit blocks found"}

        # Check results and build message
        all_success = all(r.success for r in results)
        messages = [r.message for r in results]

        # Update the marker in the buffer
        if all_success:
            new_marker = markers.make_marker("fold_start", "edit_applied")
        else:
            new_marker = markers.make_marker("fold_start", "edit_failed")

        self.nvim.api.buf_set_lines(
            bufnr, fold_start_idx, fold_start_idx + 1, False, [new_marker]
        )

        # Reprocess markers to update extmarks
        self._process_markers(bufnr)

        return {
            "success": all_success,
            "message": "; ".join(messages),
            "results": [
                {
                    "path": r.path,
                    "success": r.success,
                    "message": r.message,
                    "match_type": r.match_type,
                }
                for r in results
            ],
        }

    @pynvim.function("AnyaRejectEdit", sync=True)
    def reject_edit(self, args):
        """Reject a pending edit block.

        Updates the marker to edit_rejected without applying changes.

        Args:
            args[0]: Buffer number
            args[1]: Line number of the edit header (1-indexed)

        Returns:
            dict with {success: bool, message: str}
        """
        if len(args) < 2:
            return {"success": False, "message": "Requires bufnr and line_num"}

        bufnr = args[0]
        header_line = args[1]  # 1-indexed from Lua

        # Get the buffer content
        if not self.nvim.api.buf_is_valid(bufnr):
            return {"success": False, "message": "Invalid buffer"}

        if header_line is None:
            return {"success": False, "message": "No header line provided"}

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)

        # Find the fold_start marker after the header line
        # header_line is 1-indexed, convert to 0-indexed for array access
        fold_start_idx = None

        for i in range(
            header_line - 1, len(lines)
        ):  # header_line - 1 to convert to 0-indexed
            line = lines[i]
            if "<!-- at:" in line and "fold_start" in line:
                fold_start_idx = i
                break

        if fold_start_idx is None:
            return {"success": False, "message": "Could not find edit marker"}

        # Update the marker to rejected
        new_marker = markers.make_marker("fold_start", "edit_rejected")
        self.nvim.api.buf_set_lines(
            bufnr, fold_start_idx, fold_start_idx + 1, False, [new_marker]
        )

        # Reprocess markers to update extmarks
        self._process_markers(bufnr)

        return {"success": True, "message": "Edit rejected"}

    @pynvim.function("AnyaFindEditAtLine", sync=True)
    def find_edit_at_line(self, args):
        """Find the edit header line for a given cursor position.

        Searches upward from the cursor to find the edit header line.

        Args:
            args[0]: Buffer number
            args[1]: Current line number (1-indexed)

        Returns:
            Line number of edit header (1-indexed) or None if not in an edit block
        """
        if len(args) < 2:
            return None

        bufnr = args[0]
        current_line = args[1]

        if not self.nvim.api.buf_is_valid(bufnr):
            return None

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)

        # Search upward for fold_start with edit_pending
        for i in range(current_line - 1, -1, -1):
            line = lines[i]
            if "<!-- at:" in line and "fold_end" in line:
                # We hit a fold_end, so we're not in an edit block
                return None
            if "<!-- at:" in line and "edit_pending" in line:
                # Found the edit marker (0-indexed at i)
                # The header line is the line above it (0-indexed: i-1, 1-indexed: i)
                # Return 1-indexed header line number
                return i  # This is correct: marker at 0-idx i means header at 0-idx i-1 = 1-idx i

        return None

    @pynvim.function("AnyaApplyEditContent", sync=True)
    def apply_edit_content(self, args):
        """Apply an edit block from its raw content string.

        This is called by the Lua edit_view when user presses 1 to apply.

        Args:
            args[0]: Raw edit block content (the SEARCH/REPLACE text)

        Returns:
            dict with {success: bool, message: str}
        """
        if not args or not args[0]:
            return {"success": False, "message": "No edit content provided"}

        raw_block = args[0]

        # Apply the edit using search_replace
        from . import search_replace

        cwd = self.nvim.call("getcwd")
        results = search_replace.apply_edit_blocks(raw_block, cwd)

        if not results:
            return {"success": False, "message": "No edit blocks found in content"}

        # Check results and build message
        all_success = all(r.success for r in results)
        messages = [r.message for r in results]

        return {
            "success": all_success,
            "message": "; ".join(messages),
            "results": [
                {
                    "path": r.path,
                    "success": r.success,
                    "message": r.message,
                    "match_type": r.match_type,
                }
                for r in results
            ],
        }

    @pynvim.function("AnyaRenderEditBlocks", sync=True)
    def render_edit_blocks(self, args):
        """Render SEARCH/REPLACE edit blocks using Lua edit_view.

        Args:
            args[0]: Buffer number
            args[1]: Edit blocks string content

        Returns:
            True if successful
        """
        if len(args) < 2:
            return False

        bufnr = args[0]
        edit_str = args[1]

        if not self.nvim.api.buf_is_valid(bufnr):
            return False

        from . import search_replace

        blocks = search_replace.parse_search_replace_blocks(edit_str)

        if not blocks:
            return False

        for block in blocks:
            try:
                self.nvim.exec_lua(
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
                self.nvim.err_write(f"Failed to render edit block: {e}\n")

        # Setup keymaps after rendering
        self.nvim.exec_lua(
            "require('anya.edit_view').setup_keymaps(...)",
            bufnr,
        )

        return True

    @pynvim.function("AnyaUnapplyEdit", sync=True)
    def unapply_edit(self, args):
        """Unapply a previously applied edit by swapping SEARCH/REPLACE and reapplying.

        This reverses an edit that was already applied to the file.

        Args:
            args[0]: Raw edit block content (the original SEARCH/REPLACE text)

        Returns:
            dict with {success: bool, message: str}
        """
        if not args or not args[0]:
            return {"success": False, "message": "No edit content provided"}

        raw_block = args[0]

        from . import search_replace

        blocks = search_replace.parse_search_replace_blocks(raw_block)
        if not blocks:
            return {"success": False, "message": "No edit blocks found in content"}

        cwd = self.nvim.call("getcwd")
        results = []

        for block in blocks:
            reversed_block = search_replace.EditBlock(
                path=block.path,
                search=block.replace,
                replace=block.search,
                raw_block=block.raw_block,
            )
            result = search_replace.apply_edit_block(reversed_block, cwd)
            results.append(result)

        all_success = all(r.success for r in results)
        messages = [r.message for r in results]

        return {
            "success": all_success,
            "message": "; ".join(messages),
            "results": [
                {
                    "path": r.path,
                    "success": r.success,
                    "message": r.message,
                    "match_type": r.match_type,
                }
                for r in results
            ],
        }

    @pynvim.function("AnyaUpdateEditMarker", sync=True)
    def update_edit_marker(self, args):
        """Update an edit marker in the database for a message.

        This is called when a user toggles an edit decision, so the
        database reflects the current state.

        Args:
            args[0]: Message ID (find from conversation context)
            args[1]: Old marker name (e.g., "edit_applied")
            args[2]: New marker name (e.g., "edit_rejected")

        Returns:
            dict with {success: bool, message: str}
        """
        if len(args) < 3:
            return {
                "success": False,
                "message": "Requires message_id, old_marker, new_marker",
            }

        import json

        message_id = args[0]
        old_marker = args[1]
        new_marker = args[2]

        self._ensure_db()
        message = db.get_message(message_id)
        if not message:
            return {"success": False, "message": f"Message not found: {message_id}"}

        markers_json = message.get("markers")
        if not markers_json:
            return {"success": False, "message": "Message has no markers"}

        try:
            marker_list = json.loads(markers_json)
        except json.JSONDecodeError:
            return {"success": False, "message": "Failed to parse markers JSON"}

        updated = False
        for marker in marker_list:
            names = marker.get("names", [])
            if old_marker in names:
                idx = names.index(old_marker)
                names[idx] = new_marker
                marker["names"] = names
                updated = True

        if not updated:
            return {
                "success": False,
                "message": f"Marker '{old_marker}' not found in message",
            }

        new_markers_json = json.dumps(marker_list)
        success = db.update_message_markers(message_id, new_markers_json)

        if success:
            return {"success": True, "message": "Marker updated in database"}
        else:
            return {"success": False, "message": "Failed to update marker in database"}
