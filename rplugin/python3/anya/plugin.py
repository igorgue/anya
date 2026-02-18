"""Anya Neovim Plugin"""

import pynvim
import asyncio
import concurrent.futures
import json
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
from .spacing import SpacingManager, ContentType
from .client import AnyaClient, StreamSubscriber, SystemSubscriber
from .protocol import (
    AgentSettings,
    NvimContext,
    RequestType,
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
        # Separate client for confirmations (to avoid blocking on main request socket)
        self._confirmation_client = AnyaClient()
        self._daemon_check_done = False

        # System event subscriber for MCP status updates
        self._system_subscriber: SystemSubscriber | None = None
        self._system_listener_task = None
        self._system_listener_running = False

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

        def exception_handler(loop, context):
            import traceback
            msg = context.get("message", "")
            exc = context.get("exception")
            task = context.get("future")
            with open(os.path.expanduser("~/.local/share/anya/plugin_errors.log"), "a") as f:
                f.write(f"\n--- Unhandled exception in event loop ---\n")
                f.write(f"Message: {msg}\n")
                if task:
                    f.write(f"Task: {task}\n")
                if exc:
                    f.write(f"Exception: {exc}\n")
                    f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
                f.write(f"---\n")

        self._loop.set_exception_handler(exception_handler)
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
                    return

            self._daemon_check_done = True

            # Start system event listener for MCP status updates
            await self._start_system_event_listener()
        except Exception as e:
            self.nvim.async_call(
                self.nvim.err_write,
                f"Anya: Error checking daemon: {e}\n",
            )

    async def _start_system_event_listener(self):
        """Start listening for daemon system events (MCP status, etc.)."""
        if self._system_listener_running:
            return

        try:
            self._system_subscriber = SystemSubscriber()
            await self._system_subscriber.connect()
            self._system_listener_running = True

            # Run the listener loop
            while self._system_listener_running and self._system_subscriber:
                try:
                    chunk = await self._system_subscriber.receive(timeout=1.0)
                    if chunk is None:
                        continue

                    # Handle MCP status events
                    if chunk.event_type == StreamEventType.MCP_INIT_START:
                        self._handle_mcp_init_start(chunk.data)
                    elif chunk.event_type == StreamEventType.MCP_INIT_COMPLETE:
                        self._handle_mcp_init_complete(chunk.data)
                except Exception:
                    # Don't spam errors - just log once and continue
                    pass

        except Exception as e:
            self.nvim.async_call(
                self.nvim.err_write,
                f"Anya: Error in system event listener: {e}\n",
            )
        finally:
            self._system_listener_running = False
            if self._system_subscriber:
                await self._system_subscriber.disconnect()
                self._system_subscriber = None

    def _handle_mcp_init_start(self, data: dict):
        """Handle MCP initialization start event."""
        fidget.emit_user_event(
            self.nvim,
            "AnyaMcpInitStarted",
            {
                "message": data.get("message", "Initializing MCP servers..."),
            },
        )

    def _handle_mcp_init_complete(self, data: dict):
        """Handle MCP initialization complete event."""
        fidget.emit_user_event(
            self.nvim,
            "AnyaMcpInitFinished",
            {
                "success": data.get("success", False),
                "servers": data.get("servers", []),
                "error": data.get("error"),
                "message": data.get("message", ""),
            },
        )

    def _ensure_db(self):
        """Ensure the database is initialized (lazy initialization)."""
        if not self._db_initialized:
            db.init_db()
            self._db_initialized = True

    def _get_agent_settings(self) -> AgentSettings:
        """Get agent settings from environment variables.

        These settings are passed to the daemon so it uses the client's
        configuration rather than its own environment.
        """
        return AgentSettings(
            model=os.environ.get("ANYA_MODEL", "gpt-4.1"),
            api_key=os.environ.get("ANYA_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            api_base=os.environ.get("ANYA_API_BASE")
            or os.environ.get("OPENAI_API_BASE"),
            api_type=os.environ.get("ANYA_API_TYPE", "responses"),
            thinking_budget=os.environ.get("ANYA_THINKING_BUDGET"),
            disable_mcp=os.environ.get("ANYA_DISABLE_MCP", "0") == "1",
        )

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
        elif subcommand == "close" or subcommand == "toggle":
            # Explicitly close or toggle the Anya pane
            self.nvim.async_call(buffers.close_pane, self.nvim)
        elif subcommand == "history":
            self.nvim.command("lua require('anya.picker').open()")
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
                # Emit finish event so Lua state is properly reset
                fidget.emit_user_event(
                    self.nvim,
                    "AnyaRequestFinished",
                    {"id": request_id, "status": "error"},
                )
                return
            self.nvim.async_call(
                self.nvim.out_write,
                "Anya: Daemon started.\n",
            )

        # Get agent settings from client-side environment (used for fidget and DB)
        request_agent_settings = self._get_agent_settings()

        # Emit fidget start event
        fidget.emit_user_event(
            self.nvim,
            "AnyaRequestStarted",
            {
                "id": request_id,
                "model": request_agent_settings.model,
            },
        )

        # Get buffer content and build history
        buffer_content = await ui.get_buffer_content_async(self.nvim, chat_bufnr)
        is_chat_buf_empty = not buffer_content or not buffer_content.strip()
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

        # Tracking state
        spacing_manager = SpacingManager()

        # Output message header
        header = spacing_manager.format_content(
            "",
            ContentType.MESSAGE_BOUNDARY,
            msg_id=msg_id,
            is_first_in_buffer=is_chat_buf_empty,
        )
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
                    model=request_agent_settings.model,
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

        # Build nvim context for daemon using Future for proper synchronization
        context_future: concurrent.futures.Future = concurrent.futures.Future()

        def get_nvim_context():
            try:
                cwd = self.nvim.call("getcwd")
                current_buffer = self.nvim.api.buf_get_name(0)
                open_buffers = []
                # Get open buffers info
                for buf in self.nvim.buffers:
                    if buf.valid and buf.name:
                        open_buffers.append(
                            {
                                "name": buf.name,
                                "bufnr": buf.number,
                            }
                        )
                context_future.set_result(
                    {
                        "cwd": cwd,
                        "current_buffer": current_buffer,
                        "open_buffers": open_buffers,
                    }
                )
            except Exception:
                context_future.set_result(
                    {
                        "cwd": "",
                        "current_buffer": "",
                        "open_buffers": [],
                    }
                )

        self.nvim.async_call(get_nvim_context)

        # Wait for the context to be populated (with timeout)
        timeout_count = 0
        while not context_future.done() and timeout_count < 100:
            await asyncio.sleep(0.01)
            timeout_count += 1

        # Get result or use defaults if timed out
        if context_future.done():
            ctx_data = context_future.result()
        else:
            ctx_data = {"cwd": "", "current_buffer": "", "open_buffers": []}

        nvim_context = NvimContext(
            session_id=self.session_id,
            cwd=ctx_data["cwd"],
            current_buffer=ctx_data["current_buffer"],
            current_buffer_content="",
            open_buffers=ctx_data["open_buffers"],
            yolo_mode=self._yolo_mode,
            allowed_commands=list(self.allowed_commands),
            agent_settings=request_agent_settings.to_dict(),
        )

        # Subscribe to streaming events
        subscriber = StreamSubscriber(self.session_id, request_id)

        # Collected content for saving
        collected_content: list[str] = []
        thinking_started = False
        thinking_finalized = False
        tool_was_called = False

        try:
            await subscriber.connect()

            # Small delay to ensure SUB socket is fully connected before sending request
            # ZeroMQ PUB/SUB has a "slow joiner" problem where early messages can be lost
            await asyncio.sleep(0.1)

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
            # Note: We don't check send_task.done() anymore because the daemon
            # returns immediately after starting the background task. We rely on
            # MESSAGE_END event to know when streaming is complete.
            while True:
                # Check cancellation flag before waiting for events
                if self._request_cancelled:
                    raise asyncio.CancelledError()

                # Use shorter timeout (0.2s) for responsive cancellation
                chunk = await subscriber.receive(timeout=0.2)
                if chunk is None:
                    # Timeout - check cancellation and continue waiting
                    continue

                self._streaming_started = True

                # Handle different event types
                if chunk.event_type == StreamEventType.TEXT_DELTA:
                    delta = chunk.data.get("text", "")
                    formatted = spacing_manager.format_delta(delta, ContentType.TEXT)

                    collected_content.append(formatted)
                    if not self._request_cancelled:
                        self.nvim.async_call(
                            ui.stream_text_to_buffer, self.nvim, chat_bufnr, formatted
                        )

                elif chunk.event_type == StreamEventType.THINKING_START:
                    thinking_started = True
                    thinking_header = spacing_manager.format_content(
                        "**thinking**", ContentType.THINKING, ["fold_start", "thinking"]
                    )
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
                    formatted = spacing_manager.format_delta(
                        delta, ContentType.THINKING
                    )
                    collected_content.append(formatted)
                    if not self._request_cancelled:
                        self.nvim.async_call(
                            ui.stream_text_to_buffer, self.nvim, chat_bufnr, formatted
                        )

                elif chunk.event_type == StreamEventType.THINKING_END:
                    thinking_finalized = True
                    thinking_footer = spacing_manager.format_content(
                        "", ContentType.MARKER, ["fold_end"]
                    )
                    collected_content.append(thinking_footer)
                    if not self._request_cancelled:
                        # Use sync to ensure fold_end is written before any tool fold_start
                        self.nvim.async_call(
                            ui.stream_text_to_buffer_sync,
                            self.nvim,
                            chat_bufnr,
                            thinking_footer,
                        )

                elif chunk.event_type == StreamEventType.TOOL_CALL_START:
                    tool_name = chunk.data.get("tool_name", "")
                    tool_args_raw = chunk.data.get("tool_args", "")

                    tool_was_called = True

                    fidget.emit_user_event(
                        self.nvim,
                        "AnyaToolExecution",
                        {
                            "request_id": request_id,
                            "tool_name": tool_name,
                        },
                    )

                    # Display tool action header in buffer
                    if tool_name and not self._request_cancelled:
                        # For run_code, use the title argument as the display name
                        display_name = tool_name
                        if tool_name == "run_code" and tool_args_raw:
                            try:
                                args_dict = (
                                    json.loads(tool_args_raw)
                                    if isinstance(tool_args_raw, str)
                                    else tool_args_raw
                                )
                                title = args_dict.get("title", "")
                                if title:
                                    display_name = title
                            except (ValueError, AttributeError):
                                pass

                        # Use format_delta (TEXT) to avoid \n wrapping around [[...]]
                        tool_header = spacing_manager.format_delta(
                            f" [[{display_name}]] ",
                            ContentType.TEXT,
                        )
                        collected_content.append(tool_header)
                        self.nvim.async_call(
                            ui.stream_text_to_buffer,
                            self.nvim,
                            chat_bufnr,
                            tool_header,
                        )

                elif chunk.event_type == StreamEventType.TOOL_CALL_END:
                    tools = chunk.data.get("tools", [])

                    for tool in tools:
                        fidget.emit_user_event(
                            self.nvim,
                            "AnyaToolExecutionComplete",
                            {
                                "request_id": request_id,
                                "tool_name": tool.get("name", ""),
                            },
                        )

                    tool_was_called = False

                elif chunk.event_type == StreamEventType.MEMORY_STORED:
                    # Emit memory stored event for fidget notification
                    memory_text = chunk.data.get("text", "")
                    memory_category = chunk.data.get("category", "")
                    memory_count = chunk.data.get("count", 1)
                    fidget.emit_user_event(
                        self.nvim,
                        "AnyaMemoryStored",
                        {
                            "request_id": request_id,
                            "text": memory_text,
                            "category": memory_category,
                            "count": memory_count,
                        },
                    )

                elif chunk.event_type == StreamEventType.TOKEN_USAGE:
                    # Update token usage display in winbar
                    # Use usable_context (context - max_output) for accurate percentage
                    total_tokens = chunk.data.get("total_tokens", 0)
                    percentage = chunk.data.get("percentage", 0)
                    usable_context = chunk.data.get(
                        "usable_context", chunk.data.get("context_window", 128000)
                    )
                    if not self._request_cancelled:
                        self.nvim.async_call(
                            self.nvim.exec_lua,
                            f"require('anya.ui_utils').set_token_stats({total_tokens}, {usable_context}, {percentage})",
                        )

                elif chunk.event_type == StreamEventType.MESSAGE_END:
                    # Flush queue and clean up trailing blank lines in the buffer
                    # Wrap callbacks with error handling to prevent silent crashes
                    def _safe_flush_queue():
                        try:
                            ui.flush_queue(self.nvim)
                        except Exception:
                            pass

                    def _safe_cleanup_trailing():
                        try:
                            ui.cleanup_trailing_blanks(self.nvim, chat_bufnr)
                        except Exception:
                            pass

                    self.nvim.async_call(_safe_flush_queue)
                    self.nvim.async_call(_safe_cleanup_trailing)
                    break

                elif chunk.event_type == StreamEventType.TOOL_CONFIRMATION_REQUEST:
                    confirmation_id = chunk.data.get("confirmation_id")
                    prompt = chunk.data.get("prompt", "")
                    options = chunk.data.get("options", ["Yes", "No"])

                    # Log that we received the confirmation request
                    self.nvim.async_call(
                        self.nvim.out_write,
                        f"Anya: Received confirmation request: {prompt[:50]}...\n",
                    )

                    # Show confirmation dialog and send response
                    # Use default args to capture values by value, not reference
                    async def handle_confirmation(
                        _confirmation_id=confirmation_id,
                        _prompt=prompt,
                        _options=options,
                    ):
                        # Format options for Lua table
                        lua_options = (
                            "{" + ", ".join(f'"{opt}"' for opt in _options) + "}"
                        )
                        lua_prompt = _prompt.replace('"', '\\"').replace("\n", "\\n")

                        def run_select():
                            self.nvim.exec_lua(
                                f"""
vim.g.anya_confirmation_result = nil
vim.ui.select({lua_options},
    {{prompt = "{lua_prompt}"}},
    function(selection)
        vim.g.anya_confirmation_result = selection or "Cancel"
    end)
"""
                            )

                        # Schedule the select UI to run on Neovim thread
                        self.nvim.async_call(run_select)

                        # Wait a bit for UI to appear
                        await asyncio.sleep(0.2)

                        # Poll for result (same pattern as exec.py)
                        start_time = asyncio.get_event_loop().time()
                        while asyncio.get_event_loop().time() - start_time < 300.0:
                            result = [None]

                            def get_result():
                                try:
                                    val = self.nvim.eval(
                                        "get(g:, 'anya_confirmation_result', v:null)"
                                    )
                                    # Handle v:null - it might be returned as None or as a string
                                    if (
                                        val is not None
                                        and val != "v:null"
                                        and val != "null"
                                    ):
                                        result[0] = val
                                except Exception:
                                    pass

                            self.nvim.async_call(get_result)
                            await asyncio.sleep(0.1)  # Give async_call time to execute

                            if result[0] is not None:
                                choice = str(result[0])
                                # Send confirmation response to daemon
                                self.nvim.async_call(
                                    self.nvim.out_write,
                                    f"Anya: Sending confirmation response: {choice}\n",
                                )
                                try:
                                    # Send confirmation response to daemon
                                    import functools

                                    loop = asyncio.get_event_loop()
                                    response = await loop.run_in_executor(
                                        None,
                                        functools.partial(
                                            self._confirmation_client.send_request,
                                            RequestType.TOOL_CONFIRMATION_RESPONSE,
                                            self.session_id,
                                            _confirmation_id,
                                            {
                                                "confirmation_id": _confirmation_id,
                                                "choice": choice,
                                            },
                                            5.0,
                                        ),
                                    )
                                    self.nvim.async_call(
                                        self.nvim.out_write,
                                        f"Anya: Confirmation choice sent: {choice}\n",
                                    )
                                except Exception as e:
                                    self.nvim.async_call(
                                        self.nvim.err_write,
                                        f"Anya: Exception sending confirmation: {e}\n",
                                    )
                                return

                        # Timeout - send Cancel
                        self.nvim.async_call(
                            self.nvim.out_write,
                            "Anya: Confirmation timed out, sending Cancel\n",
                        )
                        try:
                            import functools

                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None,
                                functools.partial(
                                    self._confirmation_client.send_request,
                                    RequestType.TOOL_CONFIRMATION_RESPONSE,
                                    self.session_id,
                                    _confirmation_id,
                                    {
                                        "confirmation_id": _confirmation_id,
                                        "choice": "Cancel",
                                    },
                                    5.0,
                                ),
                            )
                        except Exception:
                            pass  # Silent fail on timeout

                    # Handle confirmation in background task
                    asyncio.create_task(handle_confirmation())

                elif chunk.event_type == StreamEventType.EDIT_CONFIRMATION_REQUEST:
                    confirmation_id = chunk.data.get("confirmation_id")
                    edit_blocks = chunk.data.get("edit_blocks", "")
                    edit_yolo_mode = chunk.data.get("yolo_mode", False)

                    # Handle edit confirmation in background task
                    # Use default args to capture values by value, not reference
                    async def handle_edit_confirmation(
                        _confirmation_id=confirmation_id,
                        _edit_blocks=edit_blocks,
                        _edit_yolo_mode=edit_yolo_mode,
                        _chat_bufnr=chat_bufnr,
                    ):
                        import functools
                        import json

                        edit_id = _confirmation_id[:8]  # Use confirmation_id as edit_id

                        # Wait for streaming queue to empty before rendering edit UI
                        # This ensures edit blocks appear after tool output is complete
                        async def wait_for_queue_empty(max_wait: float = 5.0):
                            start = asyncio.get_event_loop().time()
                            while asyncio.get_event_loop().time() - start < max_wait:
                                queue_future: concurrent.futures.Future = (
                                    concurrent.futures.Future()
                                )

                                def check_queue():
                                    try:
                                        status = self.nvim.exec_lua(
                                            "return require('anya.text').get_queue_status()"
                                        )
                                        queue_future.set_result(status)
                                    except Exception:
                                        queue_future.set_result(
                                            {"queue_length": 0, "timer_running": False}
                                        )

                                self.nvim.async_call(check_queue)
                                # Wait for result
                                wait_count = 0
                                while not queue_future.done() and wait_count < 10:
                                    await asyncio.sleep(0.01)
                                    wait_count += 1
                                if queue_future.done():
                                    status = queue_future.result()
                                    queue_length = status.get("queue_length", 0)
                                    timer_running = status.get("timer_running", False)
                                    # Queue is empty and not running
                                    if queue_length == 0:
                                        return
                                    # Queue is stalled (has items but timer not running)
                                    # This can happen if timer was paused or stopped
                                    # Force flush and continue instead of waiting for timeout
                                    if not timer_running and queue_length > 0:

                                        def flush_queue():
                                            self.nvim.exec_lua(
                                                "require('anya.text').flush_queue(false)"
                                            )

                                        self.nvim.async_call(flush_queue)
                                        return
                                await asyncio.sleep(0.02)

                        await wait_for_queue_empty()

                        # Render edit blocks and setup callback
                        render_future: concurrent.futures.Future = (
                            concurrent.futures.Future()
                        )

                        def render_and_setup():
                            try:
                                # Add spacing before edit blocks
                                spacing = spacing_manager.get_spacing_for_transition(
                                    ContentType.EDIT_BLOCK
                                )
                                if spacing:
                                    ui.append_to_chat_buffer(
                                        self.nvim, _chat_bufnr, spacing
                                    )

                                # Render edit blocks
                                self.nvim.call(
                                    "AnyaRenderEditBlocks", _chat_bufnr, _edit_blocks
                                )

                                # Setup callback for when user makes decision
                                self.nvim.exec_lua(
                                    f"""
                                    local edit_view = require('anya.edit_view')
                                    edit_view.set_decision_callback(function(action, success, message)
                                        vim.g.anya_edit_result_{edit_id} = {{
                                            action = action,
                                            success = success,
                                            message = message
                                        }}
                                    end)
                                    """
                                )
                                render_future.set_result(True)
                            except Exception as e:
                                self.nvim.err_write(
                                    f"Anya: Error rendering edit blocks: {e}\n"
                                )
                                render_future.set_result(False)

                        self.nvim.async_call(render_and_setup)

                        # Wait for render to complete (up to 3 seconds)
                        wait_count = 0
                        while not render_future.done() and wait_count < 300:
                            await asyncio.sleep(0.01)
                            wait_count += 1

                        # Check if render succeeded
                        if render_future.done() and not render_future.result():
                            # Render failed - send failure response
                            try:
                                loop = asyncio.get_event_loop()
                                await loop.run_in_executor(
                                    None,
                                    functools.partial(
                                        self._confirmation_client.send_request,
                                        RequestType.TOOL_CONFIRMATION_RESPONSE,
                                        self.session_id,
                                        _confirmation_id,
                                        {
                                            "confirmation_id": _confirmation_id,
                                            "choice": json.dumps(
                                                {
                                                    "action": "failed",
                                                    "success": False,
                                                    "message": "Failed to render edit UI",
                                                }
                                            ),
                                        },
                                        5.0,
                                    ),
                                )
                            except Exception:
                                pass
                            return

                        # If YOLO mode, auto-apply
                        if _edit_yolo_mode:
                            await asyncio.sleep(0.1)  # Small delay for UI to settle

                            def auto_apply():
                                try:
                                    self.nvim.exec_lua(
                                        """
                                        local edit_view = require('anya.edit_view')
                                        edit_view.handle_keypress_any_edit('1')
                                        """
                                    )
                                except Exception as e:
                                    # Escape the error message for Lua string
                                    escaped_msg = (
                                        str(e)
                                        .replace("\\", "\\\\")
                                        .replace('"', '\\"')
                                        .replace("\n", "\\n")
                                    )
                                    try:
                                        self.nvim.exec_lua(
                                            f"""
                                            vim.g.anya_edit_result_{edit_id} = {{
                                                action = "failed",
                                                success = false,
                                                message = "Error: {escaped_msg}"
                                            }}
                                            """
                                        )
                                    except Exception:
                                        pass  # Silently fail if we can't set the result

                            self.nvim.async_call(auto_apply)
                            await asyncio.sleep(0.1)

                        # Poll for result using Future for proper synchronization
                        var_name = f"anya_edit_result_{edit_id}"
                        start_time = asyncio.get_event_loop().time()
                        while asyncio.get_event_loop().time() - start_time < 300.0:
                            result_future: concurrent.futures.Future = (
                                concurrent.futures.Future()
                            )

                            def get_result():
                                try:
                                    val = self.nvim.eval(
                                        f"get(g:, '{var_name}', v:null)"
                                    )
                                    if val is not None and val != "v:null":
                                        result_future.set_result(val)
                                    else:
                                        result_future.set_result(None)
                                except Exception:
                                    result_future.set_result(None)

                            self.nvim.async_call(get_result)

                            # Wait for async_call to complete
                            wait_count = 0
                            while not result_future.done() and wait_count < 20:
                                await asyncio.sleep(0.01)
                                wait_count += 1

                            if result_future.done():
                                result = result_future.result()
                                if result is not None:
                                    # Clean up
                                    def cleanup():
                                        try:
                                            self.nvim.command(f"unlet g:{var_name}")
                                        except Exception:
                                            pass

                                    self.nvim.async_call(cleanup)

                                    # Send result back to daemon
                                    try:
                                        loop = asyncio.get_event_loop()
                                        await loop.run_in_executor(
                                            None,
                                            functools.partial(
                                                self._confirmation_client.send_request,
                                                RequestType.TOOL_CONFIRMATION_RESPONSE,
                                                self.session_id,
                                                _confirmation_id,
                                                {
                                                    "confirmation_id": _confirmation_id,
                                                    "choice": json.dumps(result),
                                                },
                                                5.0,
                                            ),
                                        )
                                    except Exception as e:
                                        self.nvim.async_call(
                                            self.nvim.err_write,
                                            f"Anya: Error sending edit response: {e}\n",
                                        )
                                    return

                            # Small delay before next poll
                            await asyncio.sleep(0.05)

                        # Timeout
                        try:
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None,
                                functools.partial(
                                    self._confirmation_client.send_request,
                                    RequestType.TOOL_CONFIRMATION_RESPONSE,
                                    self.session_id,
                                    _confirmation_id,
                                    {
                                        "confirmation_id": _confirmation_id,
                                        "choice": json.dumps(
                                            {
                                                "action": "timeout",
                                                "success": False,
                                                "message": "Edit timed out",
                                            }
                                        ),
                                    },
                                    5.0,
                                ),
                            )
                        except Exception:
                            pass

                    asyncio.create_task(handle_edit_confirmation())

                elif chunk.event_type == StreamEventType.EXEC_REQUEST:
                    # Handle exec request - execute command locally on user's machine
                    confirmation_id = chunk.data.get("confirmation_id")
                    exec_command = chunk.data.get("command", "")
                    exec_cwd = chunk.data.get("cwd", "")
                    exec_timeout = chunk.data.get("timeout", 30)

                    # Use default args to capture values by value, not reference
                    async def handle_exec_request(
                        _confirmation_id=confirmation_id,
                        _exec_command=exec_command,
                        _exec_cwd=exec_cwd,
                        _exec_timeout=exec_timeout,
                    ):
                        import functools
                        import json
                        import subprocess

                        try:
                            # Use the cwd from the request, or fall back to current
                            cwd = _exec_cwd or self.nvim.call("getcwd")

                            # Execute command locally
                            process = subprocess.Popen(
                                _exec_command,
                                shell=True,
                                cwd=cwd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                            )

                            try:
                                stdout, stderr = process.communicate(
                                    timeout=_exec_timeout
                                )
                                result = {
                                    "stdout": stdout,
                                    "stderr": stderr,
                                    "returncode": process.returncode,
                                }
                            except subprocess.TimeoutExpired:
                                process.kill()
                                result = {
                                    "stdout": "",
                                    "stderr": "",
                                    "returncode": -1,
                                    "error": f"Command timed out after {_exec_timeout} seconds",
                                }
                        except Exception as e:
                            result = {
                                "stdout": "",
                                "stderr": "",
                                "returncode": -1,
                                "error": str(e),
                            }

                        # Send result back to daemon
                        try:
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None,
                                functools.partial(
                                    self._confirmation_client.send_request,
                                    RequestType.TOOL_CONFIRMATION_RESPONSE,
                                    self.session_id,
                                    _confirmation_id,
                                    {
                                        "confirmation_id": _confirmation_id,
                                        "choice": json.dumps(result),
                                    },
                                    5.0,
                                ),
                            )
                        except Exception as e:
                            self.nvim.async_call(
                                self.nvim.err_write,
                                f"Anya: Error sending exec response: {e}\n",
                            )

                    asyncio.create_task(handle_exec_request())

                elif chunk.event_type == StreamEventType.ERROR:
                    error = chunk.data.get("error", "Unknown error")
                    self.nvim.async_call(
                        ui.append_to_chat_buffer,
                        self.nvim,
                        chat_bufnr,
                        f"\n\n**Error:** {error}\n",
                    )
                    break

            # Wait for send task to complete (should be immediate now)
            try:
                await send_task
            except Exception as e:
                self.nvim.err_write(f"Anya: Error sending request: {e}\n")

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

            # Save message to database
            now = datetime.now(timezone.utc)
            end_timestamp = (
                now.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{int(now.microsecond / 1000):03d}Z"
            )
            message_text = "".join(collected_content)

            def save_after_streaming():
                try:
                    self._save_agent_message_to_db(
                        chat_bufnr,
                        msg_id,
                        "Code",
                        conversation_id,
                        timestamp,
                        end_timestamp,
                        message_text,
                    )
                except Exception as e:
                    try:
                        self.nvim.err_write(f"Error saving message to DB: {e}\n")
                    except Exception:
                        pass

            self.nvim.async_call(save_after_streaming)

            # Emit finish event immediately (UI responsiveness)
            # Duration will be displayed when process_markers runs after DB save
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

            message_text = "".join(collected_content)

            def save_after_cancel():
                try:
                    self._save_agent_message_to_db(
                        chat_bufnr,
                        msg_id,
                        "Code",
                        conversation_id,
                        timestamp,
                        end_timestamp,
                        message_text,
                    )
                except Exception as e:
                    try:
                        self.nvim.err_write(f"Error saving cancelled message to DB: {e}\n")
                    except Exception:
                        pass

            self.nvim.async_call(save_after_cancel)

            # Emit finish event immediately
            fidget.emit_user_event(
                self.nvim,
                "AnyaRequestFinished",
                {
                    "id": request_id,
                    "status": "cancelled",
                },
            )

        except Exception as e:
            # Wrap callbacks with error handling
            def _safe_append_error():
                try:
                    ui.append_to_chat_buffer(self.nvim, chat_bufnr, f"\n\n**Error:** {e}\n")
                except Exception:
                    pass

            def _safe_write_error():
                try:
                    self.nvim.err_write(f"Agent error: {e}\n")
                except Exception:
                    pass

            self.nvim.async_call(_safe_append_error)
            self.nvim.async_call(_safe_write_error)

            # For errors, emit finish event immediately (no DB save needed for error case)
            fidget.emit_user_event(
                self.nvim,
                "AnyaRequestFinished",
                {
                    "id": request_id,
                    "status": "error",
                },
            )

        finally:
            try:
                await subscriber.disconnect()
            except Exception:
                pass  # Ignore disconnect errors

            self._current_task = None
            self._current_request_id = None
            self._request_cancelled = False

            def _safe_set_tool_fold_open():
                try:
                    self._set_tool_fold_open(False)
                except Exception:
                    pass

            self.nvim.async_call(_safe_set_tool_fold_open)

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
        # Flush the streaming queue without processing markers (we do it at the end
        # with pre-loaded messages to avoid RPC re-entrancy deadlock)
        self.nvim.exec_lua("require('anya.text').flush_queue(false)")

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

        # Load messages from DB and pass to process_markers to avoid
        # RPC re-entrancy deadlock (Python exec_lua -> Lua vim.fn -> Python blocked)
        messages = []
        if conversation_id:
            conv_data = db.load_conversation(conversation_id)
            if conv_data and conv_data.get("messages"):
                messages = conv_data["messages"]

        ui.process_markers(self.nvim, chat_bufnr, messages)

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

        # Send cancel request to daemon first (so daemon stops the agent)
        if self._current_request_id:
            try:
                self._client.cancel_request(self.session_id, self._current_request_id)
            except Exception as e:
                self.nvim.err_write(f"Anya: Failed to send cancel to daemon: {e}\n")

        # Cancel the concurrent.futures.Future (this doesn't cancel the coroutine,
        # but we've already set _request_cancelled which the coroutine checks)
        try:
            self._current_task.cancel()
        except Exception:
            pass  # Ignore - the flag is what matters

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
        try:
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
                try:
                    self.nvim.exec_lua(
                        """
                        local prompt_text = select(1, ...)
                        if prompt_text and prompt_text ~= "" then
                            require("anya.history").add(prompt_text)
                        end
                        """,
                        text,
                    )
                except Exception:
                    pass  # Non-critical, ignore errors

            # Schedule async agent task
            self.send(text, conv_id)

            # Return IDs for Lua to render the message
            return {
                "conv_id": conv_id,
                "msg_id": msg_id,
                "timestamp": timestamp,
                "is_new": is_new_conversation,
            }
        except Exception as e:
            import traceback
            self.nvim.err_write(f"AnyaSend error: {e}\n")
            # Log to file for debugging
            try:
                with open(os.path.expanduser("~/.local/share/anya/plugin_errors.log"), "a") as f:
                    f.write(f"\n--- AnyaSend exception ---\n")
                    f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
                    f.write(f"---\n")
            except Exception:
                pass
            return None

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

    @pynvim.function("AnyaGetToolOutput", sync=True)
    def get_tool_output(self, args):
        """Fetch tool output content by ID."""
        if not args:
            return None
        output_id = args[0]
        if not output_id:
            return None
        self._ensure_db()
        return db.get_tool_output(output_id)

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

    @pynvim.function("AnyaResizePromptHeight", sync=True)
    def resize_prompt_height(self, args):
        """Resize the prompt float height by delta lines."""
        if len(args) < 1:
            self.nvim.err_write("AnyaResizePromptHeight requires a delta argument.\n")
            return

        try:
            delta = int(args[0])
            # Get current manual override height (or current prompt height as base)
            current_height = buffers._anya_state.get(
                "manual_prompt_height"
            ) or buffers._anya_state.get("prompt_height", buffers.PROMPT_HEIGHT)
            new_height = max(1, min(current_height + delta, buffers.PROMPT_MAX_HEIGHT))

            # Update the manual override height
            buffers._anya_state["manual_prompt_height"] = new_height

            # Reposition the floats to apply the new height
            buffers.reposition_floats(self.nvim)
        except (ValueError, IndexError):
            self.nvim.err_write(
                "AnyaResizePromptHeight requires a valid integer delta.\n"
            )

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
