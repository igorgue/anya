"""Anya Neovim Plugin"""

import pynvim
import asyncio
import threading
import os
import uuid
from datetime import datetime, timezone

from . import buffers
from . import db
from . import ids
from . import markers
from . import history
from . import fidget
from . import ui
from . import utils
from . import daemon as daemon_mgmt
from .client import AnyaClient, StreamSubscriber
from .protocol import (
    NvimContext,
    StreamEventType,
)

VERSION = "0.0.1"

DEFAULT_MODEL = os.environ.get("ANYA_MODEL", "gpt-4.1")
DEFAULT_THINKING_BUDGET = os.environ.get("ANYA_THINKING_BUDGET")


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
        self._current_request_id = None  # Track current request ID
        self._cancel_in_progress = False  # Prevent cancel spam
        self._streaming_started = False  # Track if we've received any content
        self._request_cancelled = False  # Flag for async handler to check
        self.session_id = str(uuid.uuid4())  # Session ID for this Neovim instance
        self.allowed_commands = set()  # Persist allowed commands across agent runs
        self._tool_fold_open = False  # Track if a tool fold is currently open
        self._last_layout = "replace"  # Remember the last layout used
        # Initialize YOLO mode from environment variable
        self._yolo_mode = os.environ.get("ANYA_YOLO", "").lower() == "true"

        # Daemon client
        self._client = AnyaClient()
        self._daemon_check_done = False

        # Start daemon check in background on plugin load
        try:
            loop = self._ensure_loop()
            asyncio.run_coroutine_threadsafe(self._ensure_daemon_running(), loop)
        except Exception as e:
            self.nvim.err_write(f"Anya: Error starting daemon check: {e}\n")

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

    async def _ensure_daemon_running(self):
        """Ensure the daemon is running, starting it if necessary."""
        if self._daemon_check_done:
            return

        try:
            # Run blocking daemon check in thread pool to avoid blocking event loop
            loop = asyncio.get_event_loop()
            is_running = await loop.run_in_executor(None, daemon_mgmt.is_daemon_running)

            if not is_running:
                self.nvim.async_call(
                    self.nvim.out_write,
                    "Anya: Starting daemon...\n",
                )
                started = await loop.run_in_executor(None, daemon_mgmt.start_daemon)
                if started:
                    self.nvim.async_call(
                        self.nvim.out_write,
                        "Anya: Daemon started.\n",
                    )
                else:
                    self.nvim.async_call(
                        self.nvim.err_write,
                        "Anya: Failed to start daemon. Run manually with: python -m anya.server.main -f\n",
                    )
            self._daemon_check_done = True
        except Exception as e:
            self.nvim.async_call(
                self.nvim.err_write,
                f"Anya: Error checking daemon: {e}\n",
            )

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
            # Reopen with the last layout used
            self.nvim.async_call(self._open_interface, self._last_layout)
        elif subcommand == "help":
            self.nvim.out_write(self._help_text())
        elif subcommand == "open":
            # Reopen with the last layout used
            self.nvim.async_call(self._open_interface, self._last_layout)
        elif subcommand == "send":
            if len(args) < 2:
                self.nvim.err_write("'send' command requires text argument.\n")
                return
            text = " ".join(args[1:])
            self.send(text)
        elif subcommand == "tab":
            self.nvim.async_call(self._open_interface, "tab")
        elif subcommand == "pane":
            # Check for selected code
            selection = None
            is_selection = False

            start_l = _range[0]
            end_l = _range[1]

            # Detect Selection vs Toggle Intent
            current_mode = self.nvim.api.get_mode()["mode"]

            if current_mode in ["v", "V", "\x16"]:
                # 1. Active Visual Mode (<cmd> mapping)
                is_selection = True
                try:
                    v_pos = self.nvim.fn.getpos("v")
                    c_pos = self.nvim.fn.getpos(".")
                    start_l, end_l = v_pos[1], c_pos[1]
                    if start_l > end_l:
                        start_l, end_l = end_l, start_l
                except Exception:
                    pass
            elif end_l > start_l:
                # 2. Explicit Multi-line Range (:'<,'>Anya or :10,20Anya)
                is_selection = True
            else:
                # 3. Single-line Range fallback
                # Only treat as selection if marks exactly match the range.
                # This supports single-line selections via ':' command.
                try:
                    start_mark = self.nvim.call("getpos", "'<")[1]
                    end_mark = self.nvim.call("getpos", "'>")[1]
                    if start_mark == start_l and end_mark == end_l:
                        is_selection = True
                except Exception:
                    pass

            # Process Selection
            if is_selection:
                try:
                    lines = self.nvim.api.buf_get_lines(0, start_l - 1, end_l, False)
                    if lines:
                        content = "\n".join(lines)
                        buf_name = self.nvim.api.buf_get_name(0)
                        rel_path = self.nvim.call("fnamemodify", buf_name, ":.")
                        ft = self.nvim.api.buf_get_option(0, "filetype")

                        selection = {
                            "text": content,
                            "path": rel_path,
                            "line": start_l,
                            "ft": ft,
                        }

                        # Exit Visual Mode to clean up
                        if current_mode in ["v", "V", "\x16"]:
                            self.nvim.command("normal! \x1b")
                except Exception:
                    pass

            if selection:
                # Handle Selection Flow (Ensure Open -> Append)
                def handle_selection_flow():
                    try:
                        # 1. Ensure Pane is Open
                        pane_open = ui.is_anya_pane_open(self.nvim, self._last_layout)
                        if not pane_open:
                            # Force open pane
                            direction = (
                                args[1]
                                if len(args) > 1 and args[1] in ["right", "left"]
                                else "right"
                            )
                            self._open_interface("pane", direction, True)

                        # 2. Ensure Focus on Prompt
                        p_win = buffers._anya_state.get("prompt_win")
                        if p_win and self.nvim.api.win_is_valid(p_win):
                            self.nvim.api.set_current_win(p_win)

                        # 3. Append Snippet to Prompt
                        p_buf = ui.get_prompt_buffer(self.nvim)
                        if p_buf:
                            snippet = f"From @{selection['path']} line {selection['line']}:\n\n```{selection['ft']}\n{selection['text']}\n```"
                            ui.append_to_prompt_buffer(self.nvim, p_buf.number, snippet)

                        # Ensure floats are resized immediately after programmatic changes.
                        # (Relying solely on TextChanged autocmd can be racy during open+append flows.)
                        buffers.reposition_floats(self.nvim)

                    except Exception as e:
                        self.nvim.err_write(f"Anya: Error processing selection: {e}\n")

                self.nvim.async_call(handle_selection_flow)
                return

            # Default Toggle Behavior
            # Check if Anya is open as a pane - if so, allow toggling via buffers.new()
            # If Anya is open in a different layout, prevent opening as pane
            if ui.is_anya_open(self.nvim) and not ui.is_anya_pane_open(
                self.nvim, self._last_layout
            ):
                self.nvim.out_write("Anya is already open\n")
                return
            # Check for direction argument
            direction = (
                args[1] if len(args) > 1 and args[1] in ["right", "left"] else "right"
            )
            self.nvim.async_call(self._open_interface, "pane", direction)
        elif subcommand == "history":
            self.nvim.exec_lua("require('anya.picker').open()")
        elif subcommand == "cancel":
            self.cancel_agent()
        elif subcommand == "daemon":
            # Daemon management subcommands
            if len(args) < 2:
                self.nvim.out_write("Usage: :Anya daemon [status|start|stop|restart]\n")
                return
            daemon_cmd = args[1]
            if daemon_cmd == "status":
                status = daemon_mgmt.get_daemon_status()
                self.nvim.out_write(f"Anya daemon status: {status}\n")
            elif daemon_cmd == "start":
                if daemon_mgmt.start_daemon():
                    self.nvim.out_write("Anya: Daemon started.\n")
                else:
                    self.nvim.err_write("Anya: Failed to start daemon.\n")
            elif daemon_cmd == "stop":
                if daemon_mgmt.stop_daemon():
                    self.nvim.out_write("Anya: Daemon stopped.\n")
                else:
                    self.nvim.err_write("Anya: Failed to stop daemon.\n")
            elif daemon_cmd == "restart":
                if daemon_mgmt.restart_daemon():
                    self.nvim.out_write("Anya: Daemon restarted.\n")
                else:
                    self.nvim.err_write("Anya: Failed to restart daemon.\n")

    def _open_interface(self, layout="split", direction=None, force_open=False):
        """Open the Anya interface with floating chat and prompt windows.

        Args:
            layout: Layout hint (kept for compatibility; "pane" toggles, "tab" opens a new tab)
            direction: Layout hint (kept for compatibility)
            force_open: Ensure interface opens (switches layout if needed) instead of closing.
        """
        # Remember the layout for reopening
        self._last_layout = layout

        self.chat_buf, self.prompt_buf = buffers.new(
            self.nvim, layout, direction, force_open
        )

    def send(self, text, conversation_id=None):
        """Send a prompt to the code agent and stream the response to the chat buffer."""
        # Prevent concurrent requests - check if a task is still running
        if self._current_task is not None and not self._current_task.done():
            self.nvim.err_write(
                "Anya: Please wait for the current response to complete.\n"
            )
            return

        chat_buf = ui.get_chat_buffer(self.nvim)
        if not chat_buf:
            self.nvim.err_write("Anya: Chat buffer not found.\n")
            return

        loop = self._ensure_loop()
        request_id = ids.new()
        self._current_request_id = request_id
        self._streaming_started = False  # Reset streaming flag for new request
        self._request_cancelled = False  # Reset cancellation flag for new request
        self._current_task = asyncio.run_coroutine_threadsafe(
            self._run_agent_via_daemon(
                text, conversation_id, chat_buf.number, request_id
            ),
            loop,
        )

    async def _run_agent_via_daemon(
        self, text, conversation_id, chat_bufnr, request_id
    ):
        """Run the agent via the daemon and handle streaming responses."""
        # Ensure daemon is running (run blocking check in thread pool)
        loop = asyncio.get_event_loop()
        is_running = await loop.run_in_executor(None, daemon_mgmt.is_daemon_running)

        if not is_running:
            self.nvim.async_call(
                self.nvim.out_write,
                "Anya: Starting daemon...\n",
            )
            started = await loop.run_in_executor(None, daemon_mgmt.start_daemon)
            if not started:
                self.nvim.async_call(
                    self.nvim.err_write,
                    "Anya: Failed to start daemon. Run: python -m anya.server.main -f\n",
                )
                return
            self.nvim.async_call(
                self.nvim.out_write,
                "Anya: Daemon started.\n",
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

        # Get buffer content and build history
        buffer_content = await ui.get_buffer_content_async(self.nvim, chat_bufnr)
        records = history.parse_buffer_content(buffer_content or "")
        llm_history = history.build_llm_history(records)

        # Prepend open buffer context to the last user message
        if llm_history and llm_history[-1]["role"] == "user":
            buffer_context = await ui.get_open_buffers_context_async(self.nvim)
            if buffer_context:
                llm_history[-1]["content"] = buffer_context + llm_history[-1]["content"]

        # Generate message ID and timestamp
        msg_id = ids.new(conversation=conversation_id)
        now = datetime.now(timezone.utc)
        timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        )

        # Output message header
        header = markers.make_message_marker(msg_id) + "\n"
        self.nvim.async_call(ui.append_to_chat_buffer, self.nvim, chat_bufnr, header)

        # Ensure DB has a placeholder message row
        if conversation_id:
            try:
                self._ensure_db()
                inserted = db.save_message_dict(
                    msg_id=msg_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content="",
                    author="Code",
                    model=DEFAULT_MODEL,
                    created_at=timestamp,
                    ended_at=None,
                    markers=None,
                )
                if not inserted:
                    db.update_message(msg_id, content="", ended_at=None, markers=None)
            except Exception:
                pass

        # Initialize tool fold state at start of request
        self.nvim.async_call(self._set_tool_fold_open, False)

        # Build nvim context for daemon
        cwd = ""
        current_buffer = ""
        current_buffer_content = ""
        open_buffers = []

        def get_nvim_context():
            nonlocal cwd, current_buffer, current_buffer_content, open_buffers
            try:
                cwd = self.nvim.call("getcwd")
                current_buffer = self.nvim.api.buf_get_name(0)
                # Get open buffers info
                for buf in self.nvim.buffers:
                    if buf.valid and buf.name:
                        open_buffers.append(
                            {
                                "name": buf.name,
                                "bufnr": buf.number,
                            }
                        )
            except Exception:
                pass

        self.nvim.async_call(get_nvim_context)
        await asyncio.sleep(0.05)  # Allow async_call to complete

        nvim_context = NvimContext(
            session_id=self.session_id,
            cwd=cwd,
            current_buffer=current_buffer,
            current_buffer_content=current_buffer_content,
            open_buffers=open_buffers,
            yolo_mode=self._yolo_mode,
            allowed_commands=list(self.allowed_commands),
        )

        # Subscribe to streaming events
        subscriber = StreamSubscriber(self.session_id, request_id)
        await subscriber.connect()

        # Small delay to ensure SUB socket is fully connected before sending request
        # ZeroMQ PUB/SUB has a "slow joiner" problem where early messages can be lost
        await asyncio.sleep(0.1)

        # Collected content for saving
        collected_content: list[str] = []
        thinking_started = False
        thinking_finalized = False
        tool_was_called = False
        needs_blank_before_text = False

        try:
            # Send request to daemon (non-blocking, response comes via stream)
            # We need to send the request in a separate task since the daemon
            # will block until the agent completes
            send_task = asyncio.create_task(
                self._send_to_daemon(
                    request_id,
                    text,
                    conversation_id,
                    llm_history,
                    nvim_context,
                )
            )

            # Process streaming events
            while True:
                if self._request_cancelled:
                    raise asyncio.CancelledError()

                chunk = await subscriber.receive(timeout=1.0)
                if chunk is None:
                    # Check if send task completed
                    if send_task.done():
                        break
                    continue

                self._streaming_started = True

                # Handle different event types
                if chunk.event_type == StreamEventType.TEXT_DELTA:
                    delta = chunk.data.get("text", "")
                    if chunk.data.get("needs_blank_before") and needs_blank_before_text:
                        collected_content.append("\n")
                        self.nvim.async_call(
                            ui.stream_text_to_buffer, self.nvim, chat_bufnr, "\n"
                        )
                        needs_blank_before_text = False

                    collected_content.append(delta)
                    if not self._request_cancelled:
                        self.nvim.async_call(
                            ui.stream_text_to_buffer, self.nvim, chat_bufnr, delta
                        )

                elif chunk.event_type == StreamEventType.THINKING_START:
                    thinking_started = True
                    thinking_header = "**thinking**\n"
                    thinking_header += markers.make_marker("fold_start", "thinking")
                    thinking_header += "\n"
                    collected_content.append(thinking_header)
                    if not self._request_cancelled:
                        self.nvim.async_call(
                            ui.stream_text_to_buffer,
                            self.nvim,
                            chat_bufnr,
                            thinking_header,
                        )

                elif chunk.event_type == StreamEventType.THINKING_DELTA:
                    delta = chunk.data.get("text", "")
                    collected_content.append(delta)
                    if not self._request_cancelled:
                        self.nvim.async_call(
                            ui.stream_text_to_buffer, self.nvim, chat_bufnr, delta
                        )

                elif chunk.event_type == StreamEventType.THINKING_END:
                    thinking_finalized = True
                    thinking_footer = "\n" + markers.make_marker("fold_end") + "\n\n"
                    collected_content.append(thinking_footer)
                    if not self._request_cancelled:
                        self.nvim.async_call(
                            ui.stream_text_to_buffer,
                            self.nvim,
                            chat_bufnr,
                            thinking_footer,
                        )

                elif chunk.event_type == StreamEventType.TOOL_CALL_START:
                    tool_name = chunk.data.get("tool_name", "")
                    tool_args = chunk.data.get("tool_args", "")
                    status = chunk.data.get("status", "tool_pending")
                    parallel_tools = chunk.data.get("parallel_tools", [])
                    is_first = chunk.data.get("is_first", True)
                    skip_header = chunk.data.get("skip_header", False)

                    tool_was_called = True

                    # Emit tool execution event for fidget
                    fidget.emit_user_event(
                        self.nvim,
                        "AnyaToolExecution",
                        {
                            "request_id": request_id,
                            "tool_name": tool_name,
                        },
                    )

                    if not skip_header:
                        tool_headers = [
                            utils.format_tool_header(t["name"], t["args"])
                            for t in parallel_tools
                        ]
                        combined_header = " | ".join(tool_headers)

                        if is_first:
                            pending_header = (
                                "\n"
                                + combined_header
                                + "\n"
                                + markers.make_marker("fold_start", status)
                                + "\n"
                            )
                            collected_content.append(pending_header)
                            if not self._request_cancelled:
                                self.nvim.async_call(
                                    ui.stream_text_to_buffer,
                                    self.nvim,
                                    chat_bufnr,
                                    pending_header,
                                )
                                self.nvim.async_call(self._set_tool_fold_open, True)
                        else:
                            if not self._request_cancelled:
                                self.nvim.async_call(
                                    ui.update_tool_header_line,
                                    self.nvim,
                                    chat_bufnr,
                                    combined_header,
                                )

                elif chunk.event_type == StreamEventType.TOOL_CALL_END:
                    tools = chunk.data.get("tools", [])
                    output = chunk.data.get("output", "")
                    has_failure = chunk.data.get("has_failure", False)
                    is_edit_tool = chunk.data.get("is_edit_tool", False)
                    skip_output = chunk.data.get("skip_output", False)
                    unclosed = chunk.data.get("unclosed", False)

                    # Update markers
                    if (
                        not self._request_cancelled
                        and not is_edit_tool
                        and not skip_output
                    ):
                        if has_failure:
                            self.nvim.async_call(
                                ui.update_pending_markers_to_failure,
                                self.nvim,
                                chat_bufnr,
                            )
                        else:
                            self.nvim.async_call(
                                ui.update_pending_markers_to_success,
                                self.nvim,
                                chat_bufnr,
                            )

                    # Output tool result
                    if not is_edit_tool and not skip_output and output:
                        wrapped_output = f"``````\n{output}\n``````"
                        collected_content.append(wrapped_output)
                        if not self._request_cancelled:
                            self.nvim.async_call(
                                ui.stream_text_to_buffer_sync,
                                self.nvim,
                                chat_bufnr,
                                wrapped_output,
                            )

                    # Close fold
                    if not is_edit_tool and not skip_output:
                        fold_end_marker = "\n" + markers.make_marker("fold_end") + "\n"
                        collected_content.append(fold_end_marker)
                        if not self._request_cancelled:
                            self.nvim.async_call(
                                ui.stream_text_to_buffer,
                                self.nvim,
                                chat_bufnr,
                                fold_end_marker,
                            )
                            self.nvim.async_call(self._set_tool_fold_open, False)

                    # Emit completion events
                    for tool in tools:
                        fidget.emit_user_event(
                            self.nvim,
                            "AnyaToolExecutionComplete",
                            {
                                "request_id": request_id,
                                "tool_name": tool.get("name", ""),
                            },
                        )

                    if not unclosed:
                        tool_was_called = False
                    needs_blank_before_text = True

                elif chunk.event_type == StreamEventType.MESSAGE_END:
                    status = chunk.data.get("status", "success")
                    break

                elif chunk.event_type == StreamEventType.ERROR:
                    error = chunk.data.get("error", "Unknown error")
                    self.nvim.async_call(
                        ui.append_to_chat_buffer,
                        self.nvim,
                        chat_bufnr,
                        f"\n\n**Error:** {error}\n",
                    )
                    break

            # Wait for send task to complete
            await send_task

            # Ensure thinking is closed
            if thinking_started and not thinking_finalized:
                thinking_footer = "\n" + markers.make_marker("fold_end") + "\n"
                collected_content.append(thinking_footer)
                self.nvim.async_call(
                    ui.stream_text_to_buffer,
                    self.nvim,
                    chat_bufnr,
                    thinking_footer,
                )

            # Close any open tool folds
            if tool_was_called:
                fold_end_marker = "\n" + markers.make_marker("fold_end")
                collected_content.append(fold_end_marker)
                self.nvim.async_call(
                    ui.stream_text_to_buffer, self.nvim, chat_bufnr, fold_end_marker
                )
                self.nvim.async_call(self._set_tool_fold_open, False)

            # Save message to database
            now = datetime.now(timezone.utc)
            end_timestamp = (
                now.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{int(now.microsecond / 1000):03d}Z"
            )
            message_text = "".join(collected_content)

            def save_after_streaming():
                self._save_agent_message_to_db(
                    chat_bufnr,
                    msg_id,
                    "Code",
                    conversation_id,
                    timestamp,
                    end_timestamp,
                    message_text,
                )

            self.nvim.async_call(save_after_streaming)

            # Emit finish event
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
            now = datetime.now(timezone.utc)
            end_timestamp = (
                now.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{int(now.microsecond / 1000):03d}Z"
            )

            if tool_was_called:
                fold_end_marker = "\n" + markers.make_marker("fold_end")
                collected_content.append(fold_end_marker)
                self.nvim.async_call(
                    ui.append_to_chat_buffer, self.nvim, chat_bufnr, fold_end_marker
                )
                self.nvim.async_call(self._set_tool_fold_open, False)

            message_text = "".join(collected_content)

            def save_after_cancel():
                self._save_agent_message_to_db(
                    chat_bufnr,
                    msg_id,
                    "Code",
                    conversation_id,
                    timestamp,
                    end_timestamp,
                    message_text,
                )

            self.nvim.async_call(save_after_cancel)

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
                ui.append_to_chat_buffer, self.nvim, chat_bufnr, f"\n\n**Error:** {e}\n"
            )
            self.nvim.async_call(self.nvim.err_write, f"Agent error: {e}\n")

            fidget.emit_user_event(
                self.nvim,
                "AnyaRequestFinished",
                {
                    "id": request_id,
                    "status": "error",
                },
            )

        finally:
            await subscriber.disconnect()
            self._current_task = None
            self._current_request_id = None
            self._request_cancelled = False
            self.nvim.async_call(self._set_tool_fold_open, False)

    async def _send_to_daemon(
        self,
        request_id: str,
        text: str,
        conversation_id: str | None,
        llm_history: list[dict],
        nvim_context: NvimContext,
    ):
        """Send a message request to the daemon."""
        try:
            # Run the synchronous client call in a thread pool to avoid
            # blocking the asyncio event loop (which would prevent receiving streams)
            loop = asyncio.get_event_loop()

            def do_send():
                return self._client.send_message(
                    session_id=self.session_id,
                    request_id=request_id,
                    text=text,
                    conversation_id=conversation_id,
                    history=llm_history,
                    nvim_context=nvim_context,
                    timeout=300.0,
                )

            response = await loop.run_in_executor(None, do_send)
            if response is None:
                self.nvim.async_call(
                    self.nvim.err_write,
                    "Anya: No response from daemon (timeout or connection failed)\n",
                )
            return response
        except Exception as e:
            self.nvim.async_call(
                self.nvim.err_write,
                f"Anya: Failed to send to daemon: {e}\n",
            )
            return None

    def _save_agent_message_to_db(
        self,
        chat_bufnr,
        msg_id,
        agent_name,
        conversation_id,
        timestamp,
        end_timestamp,
        message_text,
    ):
        """Save agent message to database."""
        # Flush the streaming queue
        self.nvim.exec_lua("require('anya.text').flush_queue()")

        self._ensure_db()

        if not conversation_id:
            self.nvim.err_write(
                f"Warning: Missing conversation_id for message {msg_id}\n"
            )
            return

        # Read message content from buffer
        message_text_from_buffer = None
        if self.nvim.api.buf_is_valid(chat_bufnr):
            lines = self.nvim.api.buf_get_lines(chat_bufnr, 0, -1, False)
            message_markers_list: list[tuple[int, str]] = []

            def parse_message_id(line: str) -> str | None:
                prefix = markers.MESSAGE_PREFIX
                suffix = markers.MESSAGE_SUFFIX
                if not line.startswith(prefix) or not line.endswith(suffix):
                    return None
                return line[len(prefix) : -len(suffix)].strip()

            for idx, line in enumerate(lines):
                msg_marker_id = parse_message_id(line)
                if msg_marker_id:
                    message_markers_list.append((idx, msg_marker_id))

            # Find current message bounds
            start_idx = None
            end_idx = len(lines)
            for i, (idx, marker_id) in enumerate(message_markers_list):
                if marker_id == msg_id:
                    start_idx = idx + 1
                    if i + 1 < len(message_markers_list):
                        end_idx = message_markers_list[i + 1][0]
                    break

            if start_idx is not None and start_idx <= end_idx:
                message_slice = lines[start_idx:end_idx]
                while message_slice and message_slice[0] == "":
                    message_slice.pop(0)
                message_text_from_buffer = "\n".join(message_slice).rstrip("\n")

        if message_text_from_buffer:
            message_text = message_text_from_buffer
        elif not message_text:
            self.nvim.err_write(f"Warning: Empty message content for {msg_id}\n")
            return

        cleaned_content, markers_json = history.extract_markers_from_content(
            message_text
        )

        updated = db.update_message(
            msg_id,
            content=cleaned_content,
            ended_at=end_timestamp,
            markers=markers_json,
        )

        if not updated:
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

        if conversation_id:
            db.update_conversation_timestamp(conversation_id, end_timestamp)

        ui.process_markers(self.nvim, chat_bufnr)

    def cancel_agent(self):
        """Cancel the current agent response and flush the queue."""
        # Only allow cancellation if streaming has actually started
        if not self._streaming_started or self._current_task is None:
            return

        # Prevent cancel spam
        if self._cancel_in_progress:
            return

        chat_buf = ui.get_chat_buffer(self.nvim)
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

        # Send cancel request to daemon
        if self._current_request_id:
            self._client.cancel_request(self.session_id, self._current_request_id)

        # Flush the streaming queue to finish outputting pending text
        ui.flush_queue(self.nvim)

        # Force reset the request state in Lua to unlock the UI
        self.nvim.exec_lua("require('anya.conversation').force_reset_request_state()")

        # Only show cancellation message if streaming actually started
        if self._streaming_started:
            # Close any open code blocks in the buffer before adding cancellation message
            buffer_content = buffers.get_buffer_content(self.nvim, chat_buf.number)
            fixed_content = utils.close_open_code_blocks(buffer_content)

            # If blocks were closed, we need to append the closing fences
            if len(fixed_content) > len(buffer_content):
                original_lines = buffer_content.split("\n")
                fixed_lines = fixed_content.split("\n")
                if len(fixed_lines) > len(original_lines):
                    added_lines = fixed_lines[len(original_lines) :]
                    added_content = "\n".join(added_lines)
                    ui.append_to_chat_buffer(
                        self.nvim, chat_buf.number, added_content + "\n"
                    )

            # Write cancellation message to chat buffer
            cancel_msg = "\n> cancelled  "
            ui.append_to_chat_buffer(self.nvim, chat_buf.number, cancel_msg)

        # Always emit finish event to notify Lua that request is done
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
        self._current_request_id = None
        self._streaming_started = False
        self._cancel_in_progress = False

    @pynvim.function("AnyaSend", sync=True)
    def anya_send(self, args):
        """Send a prompt to the agent with streaming response.

        Returns dict with {conv_id, msg_id, timestamp} for Lua to render,
        or None on error/slash command.

        Args:
            args[0]: The prompt text
            args[1]: Optional existing conversation ID (None for new conversation)
        """
        if not args:
            self.nvim.err_write("AnyaSend requires a prompt argument.\n")
            return None
        text = args[0]
        existing_conv_id = args[1] if len(args) > 1 else None

        # Handle slash commands (no return value needed)
        if text and text.strip().startswith("/"):
            self._handle_slash_command(text.strip(), existing_conv_id)
            return None

        # Generate IDs and timestamp on server side
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        )

        # Use existing conversation ID or generate new one
        is_new_conversation = existing_conv_id is None
        conv_id = existing_conv_id if existing_conv_id else ids.new()
        msg_id = ids.new(conv_id)

        # Get user name
        user_name = self.nvim.eval("$USER") or "User"

        # Save to database
        self._ensure_db()
        if is_new_conversation:
            db.save_conversation(conv_id, timestamp)
        db.save_message_dict(
            msg_id=msg_id,
            conversation_id=conv_id,
            role="user",
            content=text,
            author=user_name,
            model=None,
            created_at=timestamp,
            ended_at=timestamp,
            markers=None,
        )

        # Save to prompt history via Lua
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

        # Schedule async agent task
        self.send(text, conv_id)

        # Return IDs for Lua to render the message
        return {
            "conv_id": conv_id,
            "msg_id": msg_id,
            "timestamp": timestamp,
            "is_new": is_new_conversation,
        }

    def _handle_slash_command(self, command, conversation_id=None):
        """Handle slash commands like /clear, /cancel, /help."""
        parts = command.split()
        cmd = parts[0].lower()

        if cmd == "/clear":
            self.nvim.async_call(self._clear_command)
        elif cmd == "/cancel":
            self.cancel_agent()
        elif cmd == "/help":
            self.nvim.async_call(self._help_command)
        elif cmd == "/file":
            self.nvim.async_call(self._file_command)
        elif cmd == "/compact":
            self.nvim.async_call(self._compact_command)
        else:
            # Unknown command - treat as regular prompt
            self.send(command, conversation_id)

    def _clear_command(self):
        """Handle /clear command."""
        self.nvim.exec_lua('require("anya.conversation").clear_conversation()', [])

    def _help_command(self):
        """Handle /help command by showing help in the chat buffer."""
        help_text = f"""Anya v{VERSION}

Available slash commands:
  /clear     Clear the current conversation
  /cancel    Cancel the current agent response
  /help      Show this help message
  /file      Open file picker to add files to prompt
  /compact   Compact conversation context

Usage:
  Type a message in the prompt buffer and press Enter to send.
  Use slash commands at the beginning of a line to execute them.

Examples:
  /clear
  /help
  How do I create a Python function?

For more help, see :h anya"""

        chat_buf = ui.get_chat_buffer(self.nvim)
        if not chat_buf or not self.nvim.api.buf_is_valid(chat_buf):
            return

        conv_id = None
        try:
            conv_id = self.nvim.api.buf_get_var(chat_buf, "anya_conversation_id")
        except Exception:
            pass

        msg_id = ids.new(conversation=conv_id)
        now = datetime.now(timezone.utc)
        timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        )

        ui.stream_text_to_buffer(
            self.nvim,
            chat_buf.number,
            "\n" + markers.make_message_marker(msg_id) + "\n",
        )
        ui.stream_text_to_buffer(self.nvim, chat_buf.number, help_text)
        ui.stream_text_to_buffer(self.nvim, chat_buf.number, "\n\n")

    def _file_command(self):
        """Handle /file command."""
        chat_buf = ui.get_chat_buffer(self.nvim)
        if chat_buf and self.nvim.api.buf_is_valid(chat_buf):
            ui.stream_text_to_buffer(
                self.nvim, chat_buf.number, "File picker not yet implemented.\n\n"
            )

    def _compact_command(self):
        """Handle /compact command."""
        chat_buf = ui.get_chat_buffer(self.nvim)
        if chat_buf and self.nvim.api.buf_is_valid(chat_buf):
            ui.stream_text_to_buffer(
                self.nvim,
                chat_buf.number,
                "Context compaction not yet implemented.\n\n",
            )

    @pynvim.function("AnyaCancel", sync=False)
    def anya_cancel(self, args):
        """Cancel the current agent response."""
        self.cancel_agent()

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
        """Get current UTC timestamp in ISO 8601 format with milliseconds."""
        now = datetime.now(timezone.utc)
        return (
            now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        )

    @pynvim.function("AnyaVersion", sync=True)
    def version(self, args):
        """Get the plugin version."""
        return VERSION

    def _help_text(self):
        return f"""anya v{VERSION}

Usage:
    :Anya                    Open the Anya interface (floating layout)
    :Anya help               Show this help message
    :Anya open               Open the Anya interface (floating layout)
    :Anya tab                Open the Anya interface in a new tab (floating layout)
    :Anya pane [right|left]  Toggle Anya in a pane (blocked if open in different layout)
    :Anya send <prompt>      Send a prompt to the agent
    :Anya history            Open the conversation history picker
    :Anya cancel             Cancel the current agent response (Ctrl+C)
    :Anya daemon [status|start|stop|restart]  Manage the daemon process
"""

    @pynvim.function("AnyaSaveConversation", sync=True)
    def save_conversation(self, args):
        """Save a new conversation to the database."""
        if len(args) < 2:
            self.nvim.err_write("AnyaSaveConversation requires (id, timestamp).\n")
            return False
        self._ensure_db()
        return db.save_conversation(args[0], args[1])

    @pynvim.function("AnyaSaveMessage", sync=True)
    def save_message(self, args):
        """Save a message to the database."""
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
        """List recent conversations."""
        self._ensure_db()
        limit = args[0] if args else 50
        offset = args[1] if len(args) > 1 else 0
        return db.list_conversations(limit, offset)

    @pynvim.function("AnyaLoadConversation", sync=True)
    def load_conversation(self, args):
        """Load a full conversation with messages."""
        if not args:
            self.nvim.err_write("AnyaLoadConversation requires a conversation ID.\n")
            return None
        self._ensure_db()
        return db.load_conversation(args[0])

    @pynvim.function("AnyaUpdateConversationTitle", sync=True)
    def update_conversation_title(self, args):
        """Update a conversation's title."""
        if len(args) < 2:
            self.nvim.err_write("AnyaUpdateConversationTitle requires (id, title).\n")
            return False
        self._ensure_db()
        return db.update_conversation_title(args[0], args[1])

    @pynvim.function("AnyaDeleteConversation", sync=True)
    def delete_conversation(self, args):
        """Delete a conversation and its messages."""
        if not args:
            self.nvim.err_write("AnyaDeleteConversation requires a conversation ID.\n")
            return False
        self._ensure_db()
        return db.delete_conversation(args[0])

    @pynvim.function("AnyaRebuildBufferContent", sync=True)
    def rebuild_buffer_content(self, args):
        """Rebuild buffer content from a conversation ID."""
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

    @pynvim.function("AnyaRepositionFloats", sync=True)
    def reposition_floats(self, _args):
        """Reposition floating windows when terminal is resized."""
        buffers.reposition_floats(self.nvim)

    @pynvim.function("AnyaCompleteAsync", sync=False)
    def anya_complete_async(self, args):
        """Provide async file path completions for @mentions."""
        if len(args) < 2:
            self.nvim.err_write("AnyaCompleteAsync requires base and callback_id.\n")
            return
        base, callback_id = args
        buffers.get_file_completions_async(self.nvim, base, callback_id)

    @pynvim.function("AnyaApplyEdit", sync=True)
    def apply_edit(self, args):
        """Apply a pending edit block from the chat buffer."""
        if len(args) < 2:
            return {"success": False, "message": "Requires bufnr and line_num"}

        bufnr = args[0]
        header_line = args[1]

        if not self.nvim.api.buf_is_valid(bufnr):
            return {"success": False, "message": "Invalid buffer"}

        if header_line is None:
            return {"success": False, "message": "No header line provided"}

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)

        fold_start_idx = None
        fold_end_idx = None

        for i in range(header_line - 1, len(lines)):
            line = lines[i]
            if "<!-- at:" in line and "fold_start" in line:
                fold_start_idx = i
            elif "<!-- at:" in line and "fold_end" in line:
                fold_end_idx = i
                break

        if fold_start_idx is None or fold_end_idx is None:
            return {"success": False, "message": "Could not find edit block boundaries"}

        edit_content = "\n".join(lines[fold_start_idx + 1 : fold_end_idx])

        from . import search_replace

        cwd = self.nvim.call("getcwd")
        results = search_replace.apply_edit_blocks(edit_content, cwd)

        if not results:
            return {"success": False, "message": "No edit blocks found"}

        all_success = all(r.success for r in results)
        messages = [r.message for r in results]

        if all_success:
            new_marker = markers.make_marker("fold_start", "edit_applied")
        else:
            new_marker = markers.make_marker("fold_start", "edit_failed")

        self.nvim.api.buf_set_lines(
            bufnr, fold_start_idx, fold_start_idx + 1, False, [new_marker]
        )

        ui.process_markers(self.nvim, bufnr)

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
        """Reject a pending edit block."""
        if len(args) < 2:
            return {"success": False, "message": "Requires bufnr and line_num"}

        bufnr = args[0]
        header_line = args[1]

        if not self.nvim.api.buf_is_valid(bufnr):
            return {"success": False, "message": "Invalid buffer"}

        if header_line is None:
            return {"success": False, "message": "No header line provided"}

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)

        fold_start_idx = None

        for i in range(header_line - 1, len(lines)):
            line = lines[i]
            if "<!-- at:" in line and "fold_start" in line:
                fold_start_idx = i
                break

        if fold_start_idx is None:
            return {"success": False, "message": "Could not find edit marker"}

        new_marker = markers.make_marker("fold_start", "edit_rejected")
        self.nvim.api.buf_set_lines(
            bufnr, fold_start_idx, fold_start_idx + 1, False, [new_marker]
        )

        ui.process_markers(self.nvim, bufnr)

        return {"success": True, "message": "Edit rejected"}

    @pynvim.function("AnyaFindEditAtLine", sync=True)
    def find_edit_at_line(self, args):
        """Find the edit header line for a given cursor position."""
        if len(args) < 2:
            return None

        bufnr = args[0]
        current_line = args[1]

        if not self.nvim.api.buf_is_valid(bufnr):
            return None

        lines = self.nvim.api.buf_get_lines(bufnr, 0, -1, False)

        for i in range(current_line - 1, -1, -1):
            line = lines[i]
            if "<!-- at:" in line and "fold_end" in line:
                return None
            if "<!-- at:" in line and "edit_pending" in line:
                return i

        return None

    def _refresh_modified_buffers(self, modified_paths):
        """Trigger checktime for any open buffers matching modified paths."""
        if not modified_paths:
            return

        abs_paths = set()
        for p in modified_paths:
            try:
                abs_p = os.path.abspath(os.path.expanduser(p))
                abs_paths.add(abs_p)
            except Exception:
                pass

        for buf in self.nvim.buffers:
            try:
                if not buf.valid or not buf.name:
                    continue

                buf_name = buf.name
                if os.path.abspath(buf_name) in abs_paths:
                    escaped_name = self.nvim.call("fnameescape", buf_name)
                    self.nvim.command(f"checktime {escaped_name}")
            except Exception:
                pass

    @pynvim.function("AnyaApplyEditContent", sync=True)
    def apply_edit_content(self, args):
        """Apply an edit block from its raw content string."""
        if not args or not args[0]:
            return {"success": False, "message": "No edit content provided"}

        raw_block = args[0]

        from . import search_replace

        cwd = self.nvim.call("getcwd")
        results = search_replace.apply_edit_blocks(raw_block, cwd)

        if not results:
            return {"success": False, "message": "No edit blocks found in content"}

        all_success = all(r.success for r in results)
        messages = [r.message for r in results]

        modified_paths = [r.path for r in results if r.success]
        self._refresh_modified_buffers(modified_paths)

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
        """Render SEARCH/REPLACE edit blocks using Lua edit_view."""
        if len(args) < 2:
            return False

        bufnr = args[0]
        edit_str = args[1]

        return ui.render_edit_blocks(self.nvim, bufnr, edit_str)

    @pynvim.function("AnyaUnapplyEdit", sync=True)
    def unapply_edit(self, args):
        """Unapply a previously applied edit by swapping SEARCH/REPLACE and reapplying."""
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

        modified_paths = [r.path for r in results if r.success]
        self._refresh_modified_buffers(modified_paths)

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
    def anya_update_edit_marker(self, args):
        """Update edit marker in database for a message."""
        message_id = args[0]
        old_marker = args[1]
        new_marker = args[2]

        try:
            db_instance = db.get_db()
            db_instance.update_message_marker(message_id, old_marker, new_marker)
            return {"success": True}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @pynvim.function("AnyaGetYoloMode", sync=True)
    def anya_get_yolo_mode(self, args):
        """Get current YOLO mode state."""
        return self._yolo_mode

    @pynvim.function("AnyaToggleYoloMode", sync=True)
    def anya_toggle_yolo_mode(self, args):
        """Toggle YOLO mode on/off."""
        self._yolo_mode = not self._yolo_mode
        return self._yolo_mode

    @pynvim.function("AnyaDaemonStatus", sync=True)
    def anya_daemon_status(self, args):
        """Get daemon status."""
        return daemon_mgmt.get_daemon_status()

    @pynvim.function("AnyaDaemonStart", sync=True)
    def anya_daemon_start(self, args):
        """Start the daemon."""
        return daemon_mgmt.start_daemon()

    @pynvim.function("AnyaDaemonStop", sync=True)
    def anya_daemon_stop(self, args):
        """Stop the daemon."""
        return daemon_mgmt.stop_daemon()

    @pynvim.function("AnyaEndSession", sync=False)
    def anya_end_session(self, args):
        """End the current session with the daemon."""
        self._client.end_session(self.session_id)
