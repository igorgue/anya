"""Anya Neovim Plugin"""

import pynvim
import asyncio
import concurrent.futures
import json
import threading
import os
import uuid
import time
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

        # State for :Anya do command (headless buffer modification)
        self._do_task = None
        self._do_request_id = None
        self._do_cancelled = False
        self._do_running = False
        self._do_buf_number = None

        # Daemon client
        self._client = AnyaClient()
        # Separate client for confirmations (to avoid blocking on main request socket)
        self._confirmation_client = AnyaClient()
        self._title_client = AnyaClient()
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
            with open(
                os.path.expanduser("~/.local/share/anya/plugin_errors.log"), "a"
            ) as f:
                f.write("\n--- Unhandled exception in event loop ---\n")
                f.write(f"Message: {msg}\n")
                if task:
                    f.write(f"Task: {task}\n")
                if exc:
                    f.write(f"Exception: {exc}\n")
                    f.write(
                        "".join(
                            traceback.format_exception(
                                type(exc), exc, exc.__traceback__
                            )
                        )
                    )
                f.write("---\n")

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
                        f"Anya: Failed to start daemon (Python: {daemon_mgmt._get_anya_python()}).\n"
                        "  A project virtualenv may be conflicting with Anya's venv.\n"
                        "  Fix: set vim.g.python3_host_prog to Anya's venv Python in your Neovim config.\n"
                        "  Or run manually: python -m anya.server.main -f\n",
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
                    elif chunk.event_type == StreamEventType.MCP_SERVER_READY:
                        self._handle_mcp_server_ready(chunk.data)
                    elif chunk.event_type == StreamEventType.TITLE_GENERATED:
                        self._handle_title_generated(chunk.data)
                    elif chunk.event_type == StreamEventType.CONVERSATION_COMPACTED:
                        self._handle_conversation_compacted(chunk.data)
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

    def _handle_mcp_server_ready(self, data: dict):
        """Handle a per-server probe event."""
        fidget.emit_user_event(
            self.nvim,
            "AnyaMcpServerUpdate",
            {
                "server": data.get("server", "unknown"),
                "status": data.get("status", "starting"),
                "tool_count": data.get("tool_count", 0),
                "error": data.get("error"),
            },
        )

    def _handle_title_generated(self, data: dict):
        """Handle TITLE_GENERATED system event from daemon."""
        # Only process if this event originated from our session
        originating_session = data.get("originating_session_id", "")
        if originating_session and originating_session != self.session_id:
            return

        conversation_id = data.get("conversation_id", "")
        title = data.get("title", "")
        success = data.get("success", False)

        if success and title and conversation_id:
            try:
                self._ensure_db()
                db.update_conversation_title(conversation_id, title)
                # Update the window title to show the conversation title
                self.nvim.async_call(
                    lambda: self.nvim.options.__setitem__(
                        "titlestring", f"Anya: {title}"
                    )
                )
            except Exception:
                pass

        fidget.emit_user_event(
            self.nvim,
            "AnyaTitleGenerationFinished",
            {
                "conversation_id": conversation_id,
                "title": title,
                "success": success,
            },
        )

    def _handle_conversation_compacted(self, data: dict):
        """Handle CONVERSATION_COMPACTED system event from daemon."""
        originating_session = data.get("originating_session_id", "")
        if originating_session and originating_session != self.session_id:
            return

        conversation_id = data.get("conversation_id", "")
        summary = data.get("summary", "")
        success = data.get("success", False)

        fidget.emit_user_event(
            self.nvim,
            "AnyaCompactionFinished",
            {
                "conversation_id": conversation_id,
                "success": success,
            },
        )

        if not success or not summary:
            self.nvim.async_call(
                self.nvim.err_write,
                "Anya: Compaction failed — could not generate summary.\n",
            )
            return

        self.nvim.async_call(self._apply_compaction, conversation_id, summary)

    def _apply_compaction(self, conversation_id: str, summary: str):
        """Apply compaction: replace buffer content and DB messages with the summary."""
        from datetime import datetime, timezone

        chat_buf = ui.get_chat_buffer(self.nvim)
        if not chat_buf or not self.nvim.api.buf_is_valid(chat_buf):
            return

        try:
            buf_conv_id = self.nvim.api.buf_get_var(chat_buf, "anya_conversation_id")
            if buf_conv_id != conversation_id:
                return
        except Exception:
            return

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        summary_msg_id = ids.new(conversation=conversation_id)

        note = "_[Conversation compacted — previous context summarized below]_\n\n"
        new_content = markers.make_message_marker(summary_msg_id) + "\n" + note + summary

        try:
            lines = new_content.split("\n")
            self.nvim.api.buf_set_option(chat_buf, "modifiable", True)
            self.nvim.api.buf_set_lines(chat_buf, 0, -1, False, lines)
        except Exception as e:
            self.nvim.err_write(f"Anya: Error updating buffer after compaction: {e}\n")
            return

        try:
            self._ensure_db()
            db.replace_messages_with_summary(
                conversation_id=conversation_id,
                summary_msg_id=summary_msg_id,
                summary_content=note + summary,
                timestamp=timestamp,
            )
        except Exception as e:
            self.nvim.err_write(f"Anya: Error updating DB after compaction: {e}\n")


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
        elif subcommand == "do":
            if len(args) < 2:
                self.nvim.err_write("'do' command requires an instruction argument.\n")
                return
            instruction = " ".join(args[1:])
            self.nvim.async_call(self._do_command, instruction)
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
            self._cancel_do_command()
        elif subcommand == "system-prompt":
            self.nvim.command("lua require('anya.system_prompt').show()")
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
        elif subcommand == "copilot":
            # GitHub Copilot management subcommands
            self._handle_copilot_command(args[1:] if len(args) > 1 else [])

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

    def _handle_copilot_command(self, args):
        """Handle :Anya copilot subcommands."""
        if not args:
            self.nvim.out_write("Usage: :Anya copilot [login|logout|status]\n")
            return

        copilot_cmd = args[0]

        if copilot_cmd == "login":
            self._copilot_login()
        elif copilot_cmd == "logout":
            self._copilot_logout()
        elif copilot_cmd == "status":
            self._copilot_status()
        elif copilot_cmd == "models":
            self._copilot_models()
        else:
            self.nvim.err_write(f"Unknown copilot command: {copilot_cmd}\n")
            self.nvim.out_write("Usage: :Anya copilot [login|logout|status|models]\n")

    def _copilot_login(self):
        """Run the Copilot device OAuth flow."""

        async def run_device_flow():
            from .copilot_auth import get_auth

            auth = get_auth()

            # Check if already logged in
            if auth.is_logged_in():
                self.nvim.async_call(
                    self.nvim.out_write,
                    "Anya Copilot: Already logged in. Use :Anya copilot logout first.\n",
                )
                return

            # Status callback to notify user
            async def status_callback(event, data):
                if event == "visit_url":
                    user_code = data.get("user_code", "")
                    verification_uri = data.get("verification_uri", "")
                    message = data.get("message", "")
                    self.nvim.async_call(
                        lambda: self.nvim.exec_lua(
                            f"vim.notify({repr(message)}, vim.log.levels.INFO, {{title = 'Anya Copilot'}})"
                        )
                    )
                    # Also write to messages
                    self.nvim.async_call(
                        self.nvim.out_write,
                        f"\nAnya Copilot: Visit {verification_uri} and enter code: {user_code}\n",
                    )

            try:
                await auth.device_flow(status_callback=status_callback)
                self.nvim.async_call(
                    lambda: self.nvim.exec_lua(
                        "vim.notify('Copilot login successful!', vim.log.levels.INFO, {title = 'Anya Copilot'})"
                    )
                )
                self.nvim.async_call(
                    self.nvim.out_write, "Anya Copilot: Login successful!\n"
                )
            except Exception as e:
                error_msg = str(e)
                self.nvim.async_call(
                    lambda: self.nvim.exec_lua(
                        f"vim.notify({repr(error_msg)}, vim.log.levels.ERROR, {{title = 'Anya Copilot'}})"
                    )
                )
                self.nvim.async_call(
                    self.nvim.err_write, f"Anya Copilot: Login failed: {error_msg}\n"
                )

        # Run in background
        loop = self._ensure_loop()
        asyncio.run_coroutine_threadsafe(run_device_flow(), loop)

    def _copilot_logout(self):
        """Log out from Copilot."""
        try:
            from .copilot_auth import get_auth

            auth = get_auth()
            auth.logout()
            self.nvim.out_write("Anya Copilot: Logged out successfully.\n")
            self.nvim.exec_lua(
                "vim.notify('Logged out from Copilot', vim.log.levels.INFO, {title = 'Anya Copilot'})"
            )
        except Exception as e:
            self.nvim.err_write(f"Anya Copilot: Logout failed: {e}\n")

    def _copilot_status(self):
        """Show Copilot authentication status."""
        try:
            from .copilot_auth import get_auth
            import time

            auth = get_auth()
            status = auth.get_status()

            lines = ["Anya Copilot Status:"]
            lines.append(f"  Logged in: {'yes' if status['logged_in'] else 'no'}")
            lines.append(f"  GitHub token: {'present' if status['has_github_token'] else 'missing'}")

            if status["copilot_token_expires_at"]:
                expires_at = status["copilot_token_expires_at"]
                expires_str = time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC", time.gmtime(expires_at)
                )
                valid = status["copilot_token_valid"]
                lines.append(f"  Copilot token: {'valid' if valid else 'expired'} (expires: {expires_str})")
            else:
                lines.append("  Copilot token: not cached")

            lines.append(f"  API endpoint: {status['api_base']}")

            self.nvim.out_write("\n".join(lines) + "\n")
        except Exception as e:
            self.nvim.err_write(f"Anya Copilot: Failed to get status: {e}\n")

    def _copilot_models(self):
        """List available Copilot models."""
        async def fetch_models():
            from .copilot_auth import get_auth

            auth = get_auth()
            
            if not auth.is_logged_in():
                self.nvim.async_call(
                    self.nvim.err_write,
                    "Anya Copilot: Not logged in. Run :Anya copilot login first.\n"
                )
                return

            try:
                models = await auth.get_models()
                
                lines = ["Anya Copilot Available Models:"]
                lines.append("-" * 50)
                for model in models:
                    model_id = model.get("id", "unknown")
                    owned_by = model.get("owned_by", "unknown")
                    lines.append(f"  {model_id}")
                    if owned_by != "unknown":
                        lines.append(f"    Provider: {owned_by}")
                lines.append("-" * 50)
                lines.append(f"Total: {len(models)} models")
                lines.append("")
                lines.append("Set model with: export ANYA_MODEL=<model_id>")
                
                self.nvim.async_call(
                    self.nvim.out_write, "\n".join(lines) + "\n"
                )
            except Exception as e:
                self.nvim.async_call(
                    self.nvim.err_write, f"Anya Copilot: Failed to fetch models: {e}\n"
                )

        # Run in background
        loop = self._ensure_loop()
        asyncio.run_coroutine_threadsafe(fetch_models(), loop)


    def send(self, text, conversation_id=None, is_new_conversation=False):
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
                text,
                conversation_id,
                chat_buf.number,
                request_id,
                is_new_conversation=is_new_conversation,
            ),
            loop,
        )

    async def _run_agent_via_daemon(
        self,
        text,
        conversation_id,
        chat_bufnr,
        request_id,
        is_new_conversation=False,
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
                open_buffers = self._collect_open_buffers()
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
            allowed_commands=list(self.allowed_commands),
            agent_settings=request_agent_settings.to_dict(),
        )

        # Subscribe to streaming events
        subscriber = StreamSubscriber(self.session_id, request_id)

        # Collected content for saving
        collected_content: list[str] = []
        last_save_time = time.monotonic()
        last_save_char_count = 0
        save_interval = 2.0  # Save every 2 seconds
        save_char_threshold = 1000  # Also save every 1000 chars
        thinking_started = False
        thinking_finalized = False
        tool_was_called = False
        early_title_triggered = False  # Track if we've started early title generation

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

                    # Incremental save: persist partial content periodically
                    current_char_count = sum(len(s) for s in collected_content)
                    current_time = time.monotonic()
                    if (
                        conversation_id
                        and msg_id
                        and (
                            current_time - last_save_time >= save_interval
                            or current_char_count - last_save_char_count
                            >= save_char_threshold
                        )
                    ):
                        last_save_time = current_time
                        last_save_char_count = current_char_count
                        # Save in background to not block streaming
                        partial_content = "".join(collected_content)
                        asyncio.create_task(
                            self._save_partial_message(
                                msg_id, conversation_id, partial_content
                            )
                        )

                    # Early title generation: trigger after ~300 chars of content
                    # This provides a title mid-stream instead of waiting for completion
                    if (
                        not early_title_triggered
                        and conversation_id
                        and sum(len(s) for s in collected_content) >= 300
                    ):
                        early_title_triggered = True
                        partial_content = "".join(collected_content)
                        asyncio.create_task(
                            self._generate_conversation_title(
                                conversation_id,
                                text,
                                partial_content,
                                request_agent_settings,
                            )
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

                    # Incremental save during thinking (same logic as TEXT_DELTA)
                    current_char_count = sum(len(s) for s in collected_content)
                    current_time = time.monotonic()
                    if (
                        conversation_id
                        and msg_id
                        and (
                            current_time - last_save_time >= save_interval
                            or current_char_count - last_save_char_count
                            >= save_char_threshold
                        )
                    ):
                        last_save_time = current_time
                        last_save_char_count = current_char_count
                        partial_content = "".join(collected_content)
                        asyncio.create_task(
                            self._save_partial_message(
                                msg_id, conversation_id, partial_content
                            )
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
                        # For execute, use the title argument as the display name
                        display_name = tool_name
                        if tool_args_raw:
                            try:
                                args_dict = (
                                    json.loads(tool_args_raw)
                                    if isinstance(tool_args_raw, str)
                                    else tool_args_raw
                                )
                                if tool_name == "execute":
                                    title = args_dict.get("title", "")
                                    if title:
                                        display_name = title
                            except (ValueError, AttributeError):
                                pass

                        tool_header = spacing_manager.format_delta(
                            f"[[{display_name}]]",
                            ContentType.TOOL_HEADER,
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

                    # Show confirmation dialog and send response
                    # Use default args to capture values by value, not reference
                    async def handle_confirmation(
                        _confirmation_id=confirmation_id,
                        _prompt=prompt,
                        _options=options,
                    ):
                        import functools

                        async def _send_choice(choice: str):
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
                                            "choice": choice,
                                        },
                                        5.0,
                                    ),
                                )
                            except Exception as e:
                                self.nvim.async_call(
                                    self.nvim.err_write,
                                    f"Anya: Exception sending confirmation: {e}\n",
                                )

                        async def _poll_result(
                            global_var: str, timeout: float = 300.0
                        ) -> str | None:
                            """Poll a Neovim global until it becomes non-null."""
                            start = asyncio.get_event_loop().time()
                            while asyncio.get_event_loop().time() - start < timeout:
                                slot = [None]

                                def _read(s=slot, gv=global_var):
                                    try:
                                        val = self.nvim.eval(f"get(g:, '{gv}', v:null)")
                                        if (
                                            val is not None
                                            and val != "v:null"
                                            and val != "null"
                                        ):
                                            s[0] = str(val)
                                    except Exception:
                                        pass

                                self.nvim.async_call(_read)
                                await asyncio.sleep(0.1)
                                if slot[0] is not None:
                                    return slot[0]
                            return None

                        # __input__ sentinel: use vim.ui.input
                        # Format: "__input__:<default>:<prompt>"
                        if _prompt.startswith("__input__:"):
                            rest = _prompt[len("__input__:") :]
                            colon_idx = rest.find(":")
                            inp_default = rest[:colon_idx] if colon_idx >= 0 else ""
                            inp_prompt = (
                                rest[colon_idx + 1 :] if colon_idx >= 0 else rest
                            )
                            lua_p = inp_prompt.replace('"', '\\"').replace("\n", "\\n")
                            lua_d = inp_default.replace('"', '\\"')

                            def run_input_ui():
                                self.nvim.exec_lua(
                                    f"""
vim.g.anya_confirmation_result = nil
vim.schedule(function()
    vim.ui.input(
        {{prompt = "{lua_p}", default = "{lua_d}"}},
        function(val)
            vim.g.anya_confirmation_result = val or ""
        end)
end)
"""
                                )

                            self.nvim.async_call(run_input_ui)
                            await asyncio.sleep(0.2)
                            choice = (
                                await _poll_result("anya_confirmation_result") or ""
                            )
                            await _send_choice(choice)
                            return

                        # Normal select path
                        lua_options = (
                            "{" + ", ".join(f'"{opt}"' for opt in _options) + "}"
                        )
                        lua_prompt = _prompt.replace('"', '\\"').replace("\n", "\\n")

                        def run_select():
                            self.nvim.exec_lua(
                                f"""
vim.g.anya_confirmation_result = nil
vim.schedule(function()
    vim.ui.select({lua_options},
        {{prompt = "{lua_prompt}"}},
        function(selection)
            vim.g.anya_confirmation_result = selection or "Cancel"
        end)
end)
"""
                            )

                        self.nvim.async_call(run_select)
                        await asyncio.sleep(0.2)
                        choice = await _poll_result("anya_confirmation_result")
                        if choice is None:
                            choice = "Cancel"
                        await _send_choice(choice)

                    # Handle confirmation in background task
                    asyncio.create_task(handle_confirmation())

                elif chunk.event_type == StreamEventType.EXEC_REQUEST:
                    # Handle exec request - execute command locally on user's machine
                    confirmation_id = chunk.data.get("confirmation_id")
                    exec_command = chunk.data.get("command", "")
                    exec_cwd = chunk.data.get("cwd", "")
                    exec_timeout = chunk.data.get("timeout", 30)
                    exec_ui_dir = chunk.data.get("ui_dir", "")

                    # Use default args to capture values by value, not reference
                    async def handle_exec_request(
                        _confirmation_id=confirmation_id,
                        _exec_command=exec_command,
                        _exec_cwd=exec_cwd,
                        _exec_timeout=exec_timeout,
                        _exec_ui_dir=exec_ui_dir,
                    ):
                        import functools
                        import glob
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

                            # Poll for process completion while serving UI requests
                            deadline = asyncio.get_event_loop().time() + _exec_timeout
                            result = None
                            while result is None:
                                poll = process.poll()
                                if poll is not None:
                                    stdout = (
                                        process.stdout.read() if process.stdout else ""
                                    )
                                    stderr = (
                                        process.stderr.read() if process.stderr else ""
                                    )
                                    result = {
                                        "stdout": stdout,
                                        "stderr": stderr,
                                        "returncode": process.returncode,
                                    }
                                    break
                                if asyncio.get_event_loop().time() > deadline:
                                    process.kill()
                                    result = {
                                        "stdout": "",
                                        "stderr": "",
                                        "returncode": -1,
                                        "error": f"Command timed out after {_exec_timeout} seconds",
                                    }
                                    break

                                # Serve any pending UI requests from the subprocess
                                if _exec_ui_dir and os.path.isdir(_exec_ui_dir):
                                    for req_file in glob.glob(
                                        os.path.join(_exec_ui_dir, "*.request.json")
                                    ):
                                        try:
                                            with open(req_file) as _f:
                                                req = json.load(_f)
                                            os.unlink(req_file)
                                        except Exception:
                                            continue

                                        req_id = req.get("id", "")
                                        kind = req.get("kind", "select")
                                        prompt = req.get("prompt", "")
                                        resp_file = os.path.join(
                                            _exec_ui_dir, f"{req_id}.response.json"
                                        )

                                        ui_result = ""
                                        try:
                                            if kind == "select":
                                                options = req.get("options", [])
                                                lua_options = (
                                                    "{"
                                                    + ", ".join(
                                                        f'"{o}"' for o in options
                                                    )
                                                    + "}"
                                                )
                                                lua_prompt = prompt.replace(
                                                    '"', '\\"'
                                                ).replace("\n", "\\n")

                                                # Use a temp file for the result to avoid
                                                # RPC polling that blocks the main loop and
                                                # freezes the picker UI
                                                import tempfile

                                                _ui_result_file = os.path.join(
                                                    tempfile.gettempdir(),
                                                    f"anya_ui_select_{req_id}",
                                                )

                                                def _run_select(
                                                    _lo=lua_options,
                                                    _lp=lua_prompt,
                                                    _rf=_ui_result_file,
                                                ):
                                                    self.nvim.exec_lua(
                                                        f"""
pcall(function() require('anya.text').pause_queue() end)
local _ok, _err = pcall(function()
  vim.ui.select({_lo},
      {{prompt = "{_lp}"}},
      vim.schedule_wrap(function(sel)
          local f = io.open("{_rf}", "w")
          if f then
              f:write(sel or "Cancel")
              f:close()
          end
          pcall(function() require('anya.text').resume_queue() end)
      end))
end)
if not _ok then
  pcall(function() require('anya.text').resume_queue() end)
  local f = io.open("{_rf}", "w")
  if f then f:write("Cancel") f:close() end
end
"""
                                                    )

                                                self.nvim.async_call(_run_select)
                                                _t0 = asyncio.get_event_loop().time()
                                                while (
                                                    asyncio.get_event_loop().time()
                                                    - _t0
                                                    < 300.0
                                                ):
                                                    await asyncio.sleep(0.15)
                                                    if os.path.exists(_ui_result_file):
                                                        try:
                                                            with open(
                                                                _ui_result_file
                                                            ) as _rf:
                                                                ui_result = (
                                                                    _rf.read().strip()
                                                                    or "Cancel"
                                                                )
                                                            os.unlink(_ui_result_file)
                                                        except Exception:
                                                            ui_result = "Cancel"
                                                        break

                                                if not ui_result:
                                                    self.nvim.async_call(
                                                        lambda: self.nvim.exec_lua(
                                                            "pcall(function() require('anya.text').resume_queue() end)"
                                                        )
                                                    )

                                            elif kind == "input":
                                                default = req.get("default", "")
                                                lua_prompt = prompt.replace(
                                                    '"', '\\"'
                                                ).replace("\n", "\\n")
                                                lua_default = default.replace(
                                                    '"', '\\"'
                                                )

                                                import tempfile

                                                _ui_result_file = os.path.join(
                                                    tempfile.gettempdir(),
                                                    f"anya_ui_input_{req_id}",
                                                )

                                                def _run_input(
                                                    _lp=lua_prompt,
                                                    _ld=lua_default,
                                                    _rf=_ui_result_file,
                                                ):
                                                    self.nvim.exec_lua(
                                                        f"""
pcall(function() require('anya.text').pause_queue() end)
local _ok, _err = pcall(function()
  vim.ui.input(
      {{prompt = "{_lp}", default = "{_ld}"}},
      vim.schedule_wrap(function(val)
          local f = io.open("{_rf}", "w")
          if f then
              f:write(val or "")
              f:close()
          end
          pcall(function() require('anya.text').resume_queue() end)
      end))
end)
if not _ok then
  pcall(function() require('anya.text').resume_queue() end)
  local f = io.open("{_rf}", "w")
  if f then f:write("") f:close() end
end
"""
                                                    )

                                                self.nvim.async_call(_run_input)
                                                _t0 = asyncio.get_event_loop().time()
                                                while (
                                                    asyncio.get_event_loop().time()
                                                    - _t0
                                                    < 300.0
                                                ):
                                                    await asyncio.sleep(0.15)
                                                    if os.path.exists(_ui_result_file):
                                                        try:
                                                            with open(
                                                                _ui_result_file
                                                            ) as _rf:
                                                                ui_result = _rf.read()
                                                            os.unlink(_ui_result_file)
                                                        except Exception:
                                                            ui_result = ""
                                                        break

                                                if not ui_result and ui_result != "":
                                                    self.nvim.async_call(
                                                        lambda: self.nvim.exec_lua(
                                                            "pcall(function() require('anya.text').resume_queue() end)"
                                                        )
                                                    )

                                        except Exception:
                                            ui_result = ""

                                        try:
                                            with open(resp_file, "w") as _f:
                                                json.dump({"result": ui_result}, _f)
                                        except Exception:
                                            pass

                                await asyncio.sleep(0.05)

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
                    # Detect context window exceeded errors and auto-compact
                    is_context_overflow = (
                        "context_length_exceeded" in error
                        or "context window" in error.lower()
                        or "maximum context length" in error.lower()
                        or "max_tokens_exceeded" in error
                        or ("400" in error and ("token" in error.lower() or "context" in error.lower()))
                    )
                    if is_context_overflow:
                        self.nvim.async_call(
                            ui.append_to_chat_buffer,
                            self.nvim,
                            chat_bufnr,
                            "\n\n_Context window full. Compacting conversation..._\n",
                        )
                        # Trigger auto-compaction using the history already built
                        if conversation_id:
                            self.nvim.async_call(
                                self._trigger_compaction,
                                conversation_id,
                                llm_history,
                            )
                    else:
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

            # Generate title if not already done during early streaming
            # (early_title_triggered handles the first-response case)
            if conversation_id and message_text and not early_title_triggered:
                asyncio.create_task(
                    self._generate_conversation_title(
                        conversation_id,
                        text,
                        message_text,
                        request_agent_settings,
                    )
                )

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
                        self.nvim.err_write(
                            f"Error saving cancelled message to DB: {e}\n"
                        )
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

        except Exception as err:
            # Capture error for use in closures
            error_msg = str(err)

            # Wrap callbacks with error handling
            def _safe_append_error(msg=error_msg):
                try:
                    ui.append_to_chat_buffer(
                        self.nvim, chat_bufnr, f"\n\n**Error:** {msg}\n"
                    )
                except Exception:
                    pass

            def _safe_write_error(msg=error_msg):
                try:
                    self.nvim.err_write(f"Agent error: {msg}\n")
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

    async def _generate_conversation_title(
        self,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
        settings: AgentSettings,
    ):
        """Delegate title generation to the daemon (uses same API client as coding agent)."""
        # Skip if the conversation already has a title
        try:
            self._ensure_db()
            conv = db.get_conversation(conversation_id)
            if conv and conv.get("title"):
                return
        except Exception:
            pass

        fidget.emit_user_event(
            self.nvim,
            "AnyaTitleGenerationStarted",
            {"conversation_id": conversation_id},
        )

        # Send to daemon — returns immediately; result arrives via TITLE_GENERATED system event
        try:
            loop = asyncio.get_event_loop()
            import functools

            await loop.run_in_executor(
                None,
                functools.partial(
                    self._title_client.send_request,
                    RequestType.GENERATE_TITLE,
                    self.session_id,
                    f"title_{conversation_id}",
                    {
                        "conversation_id": conversation_id,
                        "user_message": user_message,
                        "assistant_message": assistant_message,
                        "settings": settings.to_dict(),
                    },
                    5.0,  # Short — just waiting for the "started" ack
                ),
            )
        except Exception:
            # Daemon unreachable — close the fidget immediately
            fidget.emit_user_event(
                self.nvim,
                "AnyaTitleGenerationFinished",
                {"conversation_id": conversation_id, "title": "", "success": False},
            )

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

    async def _save_partial_message(
        self, msg_id: str, conversation_id: str, partial_content: str
    ):
        """Save partial message content to database during streaming.

        This ensures that if the daemon crashes or power is lost mid-stream,
        we don't lose all the accumulated content.
        """
        try:
            self._ensure_db()
            # Use update_message to save the partial content
            # We don't set ended_at since the message is still being streamed
            db.update_message(msg_id, content=partial_content, ended_at=None)
        except Exception as e:
            # Log but don't fail - this is a best-effort save
            try:
                self.logger.warning(f"Failed to save partial message: {e}")
            except Exception:
                pass

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
            # Strip cancellation marker if present (added by cancel_agent)
            # This ensures the visual indicator doesn't pollute the saved content
            lines = message_text_from_buffer.split("\n")
            while lines and lines[-1].strip().startswith("> cancelled"):
                lines.pop()
            message_text_from_buffer = "\n".join(lines).rstrip("\n")
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

    def _do_command(self, instruction: str):
        """Handle :Anya do <instruction> - headless buffer modification."""
        if self._do_running:
            self.nvim.err_write(
                "Anya: A 'do' operation is already in progress. Use :Anya cancel to stop it.\n"
            )
            return

        # Grab current buffer info synchronously (we're on the main thread here)
        try:
            buf = self.nvim.api.get_current_buf()
            buf_path = self.nvim.api.buf_get_name(buf)
            buf_lines = self.nvim.api.buf_get_lines(buf, 0, -1, False)
            buf_content = "\n".join(buf_lines)
            ft = self.nvim.api.buf_get_option(buf, "filetype")
            cwd = self.nvim.call("getcwd")
            open_buffers = self._collect_open_buffers()
        except Exception as e:
            self.nvim.err_write(f"Anya: Failed to read buffer: {e}\n")
            return

        if not buf_path:
            self.nvim.err_write("Anya: No current buffer to modify.\n")
            return

        request_agent_settings = self._get_agent_settings()
        request_id = ids.new()
        self._do_request_id = request_id
        self._do_cancelled = False
        self._do_running = True
        self._do_buf_number = buf.number

        # Install temporary <C-c> mapping that cancels the do operation
        try:
            self.nvim.exec_lua(
                """
local rid = select(1, ...)
vim.g.anya_do_active_request_id = rid
-- Map <C-c> in normal mode to cancel while do is running
vim.keymap.set("n", "<C-c>", function()
    vim.g.anya_do_active_request_id = nil
    vim.keymap.del("n", "<C-c>")
    vim.cmd("Anya cancel")
end, { noremap = true, silent = true, desc = "Cancel Anya do" })
""",
                request_id,
            )
        except Exception:
            pass

        fidget.emit_user_event(
            self.nvim,
            "AnyaDoStarted",
            {"id": request_id, "model": request_agent_settings.model},
        )

        loop = self._ensure_loop()
        self._do_task = asyncio.run_coroutine_threadsafe(
            self._run_do_via_daemon(
                instruction=instruction,
                buf_path=buf_path,
                buf_content=buf_content,
                ft=ft,
                cwd=cwd,
                open_buffers=open_buffers,
                request_id=request_id,
                buf_number=buf.number,
            ),
            loop,
        )

    def _cancel_do_command(self):
        """Cancel an in-progress :Anya do operation."""
        if not self._do_running:
            return
        self._do_cancelled = True

        if self._do_request_id:
            try:
                self._client.cancel_request(self.session_id, self._do_request_id)
            except Exception:
                pass

        if self._do_task:
            try:
                self._do_task.cancel()
            except Exception:
                pass

        # Remove temporary keymap
        try:
            self.nvim.exec_lua("""
vim.g.anya_do_active_request_id = nil
pcall(vim.keymap.del, "n", "<C-c>")
""")
        except Exception:
            pass

        fidget.emit_user_event(
            self.nvim,
            "AnyaDoFinished",
            {"id": self._do_request_id or "cancelled", "status": "cancelled"},
        )

        self._do_running = False
        self._do_task = None
        self._do_request_id = None

    def _normalize_buffer_path(self, path: str) -> str:
        """Normalize a buffer path for stable matching."""
        return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))

    def _collect_open_buffers(self) -> list[dict]:
        """Collect metadata for open file buffers."""
        cwd = self.nvim.call("getcwd")
        current_buf = self.nvim.api.get_current_buf()
        current_bufnr = current_buf.number if current_buf else -1

        visible_bufnrs = set()
        try:
            for win in self.nvim.api.list_wins():
                try:
                    visible_bufnrs.add(self.nvim.api.win_get_buf(win))
                except Exception:
                    pass
        except Exception:
            pass

        open_buffers = []
        for buf in self.nvim.buffers:
            try:
                if not buf.valid or not buf.name:
                    continue

                path = buf.name
                rel_path = path
                try:
                    rel_path = os.path.relpath(path, cwd) if cwd else path
                except Exception:
                    pass

                open_buffers.append(
                    {
                        "name": path,
                        "path": path,
                        "rel_path": rel_path,
                        "bufnr": buf.number,
                        "is_current": buf.number == current_bufnr,
                        "is_visible": buf.number in visible_bufnrs,
                        "modified": bool(
                            self.nvim.api.buf_get_option(buf.number, "modified")
                        ),
                        "filetype": self.nvim.api.buf_get_option(buf.number, "filetype"),
                    }
                )
            except Exception:
                continue

        return open_buffers

    def _find_open_buffer_number(
        self, target_path: str | None, fallback_bufnr: int | None = None
    ) -> int | None:
        """Find an open buffer number by file path."""
        if not target_path:
            return fallback_bufnr

        try:
            normalized_target = self._normalize_buffer_path(target_path)
        except Exception:
            normalized_target = target_path

        for buf in self.nvim.buffers:
            try:
                if not buf.valid or not buf.name:
                    continue
                if self._normalize_buffer_path(buf.name) == normalized_target:
                    return buf.number
            except Exception:
                continue

        return fallback_bufnr

    def _apply_buffer_modification(
        self, buf_number: int, content: str, mode: str = "replace"
    ) -> str:
        """Apply a text modification to a Neovim buffer."""
        if not self.nvim.api.buf_is_valid(buf_number):
            return "Error: Buffer is no longer valid"

        lines = content.split("\n")
        was_modifiable = self.nvim.api.buf_get_option(buf_number, "modifiable")
        self.nvim.api.buf_set_option(buf_number, "modifiable", True)

        try:
            if mode == "replace":
                self.nvim.api.buf_set_lines(buf_number, 0, -1, False, lines)
            elif mode == "append":
                lc = self.nvim.api.buf_line_count(buf_number)
                self.nvim.api.buf_set_lines(buf_number, lc, lc, False, lines)
            elif mode == "prepend":
                self.nvim.api.buf_set_lines(buf_number, 0, 0, False, lines)
            else:
                return f"Error: Invalid modify mode: {mode}"
        finally:
            self.nvim.api.buf_set_option(buf_number, "modifiable", was_modifiable)

        self.nvim.api.buf_set_option(buf_number, "modified", True)
        self.nvim.exec_lua(
            """
local bufnr = select(1, ...)
vim.schedule(function()
    vim.cmd("checktime")
    vim.api.nvim_exec_autocmds("User", {
        pattern = "AnyaDoBufferModified",
        data = { bufnr = bufnr },
    })
    vim.cmd("redraw!")
end)
""",
            buf_number,
        )
        return "ok"

    async def _run_do_via_daemon(
        self,
        instruction: str,
        buf_path: str,
        buf_content: str,
        ft: str,
        cwd: str,
        open_buffers: list,
        request_id: str,
        buf_number: int,
    ):
        """Run :Anya do headlessly via the daemon, then write result back to buffer."""

        loop = asyncio.get_event_loop()
        is_running = await loop.run_in_executor(None, daemon_mgmt.is_daemon_running)

        if not is_running:
            started = await loop.run_in_executor(None, daemon_mgmt.start_daemon)
            if not started:
                self.nvim.async_call(
                    self.nvim.err_write,
                    "Anya: Failed to start daemon.\n",
                )
                self.nvim.async_call(self._finish_do, request_id, "error")
                return

        request_agent_settings = self._get_agent_settings()

        # Build a focused instruction that includes the buffer content
        rel_path = buf_path
        try:
            import os

            rel_path = os.path.relpath(buf_path, cwd) if cwd else buf_path
        except Exception:
            pass

        user_message = (
            f"{instruction}\n\n"
            f"Current file: `{rel_path}` (filetype: {ft})\n\n"
            f"File content:\n```{ft}\n{buf_content}\n```\n\n"
            f"Use `from anya.libs import buffer; buffer.modify(content)` to write the result.\n"
            f"The `content` argument must be the COMPLETE new file content.\n"
            f"Do not add any explanation — just use buffer.modify() with the full result."
        )

        nvim_context = NvimContext(
            session_id=self.session_id,
            cwd=cwd,
            current_buffer=buf_path,
            current_buffer_content=buf_content,
            open_buffers=open_buffers,
            allowed_commands=[],
            agent_settings=request_agent_settings.to_dict(),
        )

        subscriber = StreamSubscriber(self.session_id, request_id)

        try:
            await subscriber.connect()
            await asyncio.sleep(0.1)

            send_task = asyncio.create_task(
                self._send_to_daemon(
                    request_id,
                    user_message,
                    None,  # no conversation
                    [{"role": "user", "content": user_message}],
                    nvim_context,
                )
            )

            while True:
                if self._do_cancelled:
                    raise asyncio.CancelledError()

                chunk = await subscriber.receive(timeout=0.2)
                if chunk is None:
                    continue

                if chunk.event_type == StreamEventType.MODIFY_BUFFER_REQUEST:
                    await self._handle_do_modify_buffer(chunk, request_id, buf_number)

                elif chunk.event_type == StreamEventType.EXEC_REQUEST:
                    # Allow exec during do (for any shell tools the agent might use)
                    asyncio.create_task(self._handle_do_exec_request(chunk))

                elif chunk.event_type == StreamEventType.MESSAGE_END:
                    break

                elif chunk.event_type == StreamEventType.ERROR:
                    error = chunk.data.get("error", "Unknown error")
                    self.nvim.async_call(
                        self.nvim.err_write,
                        f"Anya do: Error: {error}\n",
                    )
                    break

            try:
                await send_task
            except Exception:
                pass

            self.nvim.async_call(self._finish_do, request_id, "success")

        except asyncio.CancelledError:
            self.nvim.async_call(self._finish_do, request_id, "cancelled")

        except Exception as err:
            self.nvim.async_call(
                self.nvim.err_write,
                f"Anya do: {err}\n",
            )
            self.nvim.async_call(self._finish_do, request_id, "error")

        finally:
            try:
                await subscriber.disconnect()
            except Exception:
                pass

    async def _handle_do_modify_buffer(self, chunk, request_id: str, buf_number: int):
        """Handle MODIFY_BUFFER_REQUEST during an :Anya do operation."""
        import functools

        confirmation_id = chunk.data.get("confirmation_id")
        content = chunk.data.get("content", "")
        mode = chunk.data.get("mode", "replace")
        target_path = chunk.data.get("target_path") or chunk.data.get("buf_path")

        result_container = [None]

        def apply_modification():
            try:
                target_buf_number = self._find_open_buffer_number(target_path, buf_number)
                if target_buf_number is None:
                    if target_path:
                        result_container[0] = (
                            f"Error: Open buffer not found for path: {target_path}"
                        )
                    else:
                        result_container[0] = "Error: Buffer is no longer valid"
                    return

                result_container[0] = self._apply_buffer_modification(
                    target_buf_number, content, mode
                )
            except Exception as e:
                result_container[0] = f"Error: {e}"

        self.nvim.async_call(apply_modification)

        # Wait for the async call to complete
        for _ in range(100):
            await asyncio.sleep(0.05)
            if result_container[0] is not None:
                break

        result_str = result_container[0] or "Error: timeout"

        # Send result back to daemon
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                functools.partial(
                    self._confirmation_client.send_request,
                    RequestType.TOOL_CONFIRMATION_RESPONSE,
                    self.session_id,
                    confirmation_id,
                    {
                        "confirmation_id": confirmation_id,
                        "choice": result_str,
                    },
                    5.0,
                ),
            )
        except Exception as e:
            self.nvim.async_call(
                self.nvim.err_write,
                f"Anya: Error sending modify-buffer result: {e}\n",
            )

    async def _handle_do_exec_request(self, chunk):
        """Handle EXEC_REQUEST during :Anya do (reuses existing exec logic)."""
        import functools
        import glob
        import json
        import subprocess

        confirmation_id = chunk.data.get("confirmation_id")
        exec_command = chunk.data.get("command", "")
        exec_cwd = chunk.data.get("cwd", "")
        exec_timeout = chunk.data.get("timeout", 30)
        exec_ui_dir = chunk.data.get("ui_dir", "")

        try:
            cwd_val = [None]

            def get_cwd():
                cwd_val[0] = self.nvim.call("getcwd")

            self.nvim.async_call(get_cwd)
            for _ in range(20):
                await asyncio.sleep(0.05)
                if cwd_val[0] is not None:
                    break

            cwd = exec_cwd or cwd_val[0] or ""
            process = subprocess.Popen(
                exec_command,
                shell=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = asyncio.get_event_loop().time() + exec_timeout
            result = None
            while result is None:
                poll = process.poll()
                if poll is not None:
                    stdout = process.stdout.read() if process.stdout else ""
                    stderr = process.stderr.read() if process.stderr else ""
                    result = {
                        "stdout": stdout,
                        "stderr": stderr,
                        "returncode": process.returncode,
                    }
                    break
                if asyncio.get_event_loop().time() > deadline:
                    process.kill()
                    result = {
                        "stdout": "",
                        "stderr": "",
                        "returncode": -1,
                        "error": "timeout",
                    }
                    break

                # Serve any pending ui_dir requests from the subprocess
                # (e.g. buffer.modify() writes a modify_buffer request here)
                if exec_ui_dir and os.path.isdir(exec_ui_dir):
                    for req_file in glob.glob(
                        os.path.join(exec_ui_dir, "*.request.json")
                    ):
                        try:
                            with open(req_file) as _f:
                                req = json.load(_f)
                            os.unlink(req_file)
                        except Exception:
                            continue

                        req_id = req.get("id", "")
                        kind = req.get("kind", "select")
                        resp_file = os.path.join(exec_ui_dir, f"{req_id}.response.json")

                        ui_result = ""
                        try:
                            if kind == "modify_buffer":
                                buf_content = req.get("content", "")
                                buf_mode = req.get("mode", "replace")
                                target_path = req.get("target_path") or req.get("buf_path")
                                result_container = [None]

                                def _apply_modification(
                                    _content=buf_content,
                                    _mode=buf_mode,
                                    _target_path=target_path,
                                    _buf=self._do_buf_number,
                                ):
                                    try:
                                        target_buf_number = self._find_open_buffer_number(
                                            _target_path, _buf
                                        )
                                        if target_buf_number is None:
                                            if _target_path:
                                                result_container[0] = (
                                                    f"Error: Open buffer not found for path: {_target_path}"
                                                )
                                            else:
                                                result_container[0] = (
                                                    "Error: Buffer is no longer valid"
                                                )
                                            return
                                        result_container[0] = self._apply_buffer_modification(
                                            target_buf_number, _content, _mode
                                        )
                                    except Exception as e:
                                        result_container[0] = f"Error: {e}"

                                self.nvim.async_call(_apply_modification)
                                for _ in range(100):
                                    await asyncio.sleep(0.05)
                                    if result_container[0] is not None:
                                        break
                                ui_result = result_container[0] or "Error: timeout"
                        except Exception as e:
                            ui_result = f"Error: {e}"

                        try:
                            with open(resp_file, "w") as _f:
                                json.dump({"result": ui_result}, _f)
                        except Exception:
                            pass

                await asyncio.sleep(0.05)
        except Exception as e:
            result = {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                functools.partial(
                    self._confirmation_client.send_request,
                    RequestType.TOOL_CONFIRMATION_RESPONSE,
                    self.session_id,
                    confirmation_id,
                    {
                        "confirmation_id": confirmation_id,
                        "choice": json.dumps(result),
                    },
                    5.0,
                ),
            )
        except Exception:
            pass

    def _finish_do(self, request_id: str, status: str):
        """Clean up after :Anya do completes."""
        # Remove temporary <C-c> mapping
        try:
            self.nvim.exec_lua("""
vim.g.anya_do_active_request_id = nil
pcall(vim.keymap.del, "n", "<C-c>")
""")
        except Exception:
            pass

        fidget.emit_user_event(
            self.nvim,
            "AnyaDoFinished",
            {"id": request_id, "status": status},
        )

        self._do_running = False
        self._do_task = None
        self._do_request_id = None
        self._do_cancelled = False
        self._do_buf_number = None

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

            # Handle plugin-level slash commands.
            # /init and /plan are agent instructions and must go through normal flow.
            if text and text.strip().startswith("/"):
                handled = self._handle_slash_command(text.strip(), existing_conv_id)
                if handled:
                    return None

            # Generate IDs and timestamp on server side
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            timestamp = (
                now.strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{int(now.microsecond / 1000):03d}Z"
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
                # Store the current working directory with the conversation
                cwd = self.nvim.call("getcwd")
                db.save_conversation(conv_id, timestamp, cwd)
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
            self.send(text, conv_id, is_new_conversation=is_new_conversation)

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
                with open(
                    os.path.expanduser("~/.local/share/anya/plugin_errors.log"), "a"
                ) as f:
                    f.write("\n--- AnyaSend exception ---\n")
                    f.write(
                        "".join(traceback.format_exception(type(e), e, e.__traceback__))
                    )
                    f.write("---\n")
            except Exception:
                pass
            return None

    def _handle_slash_command(self, command, conversation_id=None):
        """Handle plugin-level slash commands.

        Returns True when handled by the plugin, False when it should be sent
        to the agent as a normal prompt (e.g. /init, /plan, or unknown commands).
        """
        parts = command.split()
        cmd = parts[0].lower() if parts else ""

        if cmd == "/clear" or cmd == "/new":
            self.nvim.async_call(self._clear_command)
            return True
        elif cmd == "/cancel":
            self.cancel_agent()
            return True
        elif cmd == "/help":
            self.nvim.async_call(self._help_command)
            return True
        elif cmd == "/file":
            self.nvim.async_call(self._file_command)
            return True
        elif cmd == "/compact":
            self.nvim.async_call(self._compact_command)
            return True

        # Not a plugin command: let AnyaSend persist and send as normal prompt.
        return False

    def _clear_command(self):
        """Handle /clear command."""
        self.nvim.exec_lua('require("anya.conversation").clear_conversation()', [])

    def _help_command(self):
        """Handle /help command by showing help in the chat buffer."""
        help_text = f"""Anya v{VERSION}

Available slash commands:
  /clear     Clear the current conversation
  /new       Clear the current conversation (alias for /clear)
  /cancel    Cancel the current agent response
  /help      Show this help message
  /file      Open file picker to add files to prompt
  /compact   Compact conversation context
  /init      Create or update AGENTS.md with project instructions
  /plan      Draft a plan first, then choose save/execute/save+execute

Usage:
  Type a message in the prompt buffer and press Enter to send.
  Use slash commands at the beginning of a line to execute them.

Headless buffer modification:
  :Anya do <instruction>   Apply an instruction to the current buffer.
  While running, press <C-c> or :Anya cancel to stop it.
  Example: :Anya do "write a good commit message for this diff"

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

    def _trigger_compaction(self, conversation_id: str, history: list):
        """Send COMPACT_CONVERSATION request to the daemon.

        Args:
            conversation_id: The conversation to compact
            history: Current LLM history (list of {role, content} dicts)
        """
        import functools

        settings = self._get_agent_settings()

        fidget.emit_user_event(
            self.nvim,
            "AnyaCompactionStarted",
            {"conversation_id": conversation_id},
        )

        chat_buf = ui.get_chat_buffer(self.nvim)
        if chat_buf and self.nvim.api.buf_is_valid(chat_buf):
            ui.stream_text_to_buffer(
                self.nvim,
                chat_buf.number,
                "\n_Compacting conversation..._\n",
            )

        async def _send():
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(
                    None,
                    functools.partial(
                        self._title_client.send_request,
                        RequestType.COMPACT_CONVERSATION,
                        self.session_id,
                        f"compact_{conversation_id}",
                        {
                            "conversation_id": conversation_id,
                            "history": history,
                            "settings": settings.to_dict(),
                        },
                        5.0,
                    ),
                )
            except Exception as e:
                self.nvim.async_call(
                    self.nvim.err_write,
                    f"Anya: Failed to send compaction request: {e}\n",
                )
                fidget.emit_user_event(
                    self.nvim,
                    "AnyaCompactionFinished",
                    {"conversation_id": conversation_id, "success": False},
                )

        loop = self._ensure_loop()
        asyncio.run_coroutine_threadsafe(_send(), loop)


    def _compact_command(self):
        """Handle /compact command — compact the current conversation."""
        chat_buf = ui.get_chat_buffer(self.nvim)
        if not chat_buf or not self.nvim.api.buf_is_valid(chat_buf):
            return

        conversation_id = None
        try:
            conversation_id = self.nvim.api.buf_get_var(chat_buf, "anya_conversation_id")
        except Exception:
            pass

        if not conversation_id:
            self.nvim.err_write("Anya: No active conversation to compact.\n")
            return

        # Build history from buffer content
        try:
            import concurrent.futures
            buf_future: concurrent.futures.Future = concurrent.futures.Future()

            def _get_content():
                try:
                    lines = self.nvim.api.buf_get_lines(chat_buf, 0, -1, False)
                    buf_future.set_result("\n".join(lines))
                except Exception as exc:
                    buf_future.set_exception(exc)

            self.nvim.async_call(_get_content)

            # Wait briefly for async_call to complete (we're on main thread here)
            import time as _time
            _t0 = _time.monotonic()
            while not buf_future.done() and _time.monotonic() - _t0 < 2.0:
                _time.sleep(0.01)

            buf_content = buf_future.result() if buf_future.done() else ""
            records = history.parse_buffer_content(buf_content)
            llm_history = history.build_llm_history(records)
        except Exception as e:
            self.nvim.err_write(f"Anya: Error reading buffer for compaction: {e}\n")
            return

        if not llm_history:
            self.nvim.err_write("Anya: No conversation history to compact.\n")
            return

        self._trigger_compaction(conversation_id, llm_history)

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

    @pynvim.function("AnyaDo", sync=False)
    def anya_do(self, args):
        """Run :Anya do programmatically from Lua."""
        if not args:
            self.nvim.err_write("AnyaDo requires an instruction argument.\n")
            return
        instruction = args[0]
        self.nvim.async_call(self._do_command, instruction)

    @pynvim.function("AnyaDoCancel", sync=False)
    def anya_do_cancel(self, args):
        """Cancel a running :Anya do operation."""
        self._cancel_do_command()

    def _help_text(self):
        return f"""anya v{VERSION}

Usage:
    :Anya                    Open the Anya interface (floating layout)
    :Anya help               Show this help message
    :Anya open               Open the Anya interface (floating layout)
    :Anya tab                Open the Anya interface in a new tab (floating layout)
    :Anya pane [right|left]  Toggle Anya in a pane (blocked if open in different layout)
    :Anya send <prompt>      Send a prompt to the agent
    :Anya do <instruction>   Apply an instruction to the current buffer (headless)
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

    @pynvim.function("AnyaGetSystemPrompt", sync=False)
    def anya_get_system_prompt(self, args):
        """Get the current system prompt from the daemon."""

        def fetch_and_display():
            try:
                # Get cwd from args or use current directory
                cwd = args[0] if args else self.nvim.call("getcwd")

                # Get agent settings from environment
                settings = self._get_agent_settings()

                # Request system prompt from daemon
                request_id = str(uuid.uuid4())
                response = self._client.send_request(
                    RequestType.GET_SYSTEM_PROMPT,
                    session_id=self.session_id,
                    request_id=request_id,
                    payload={
                        "cwd": cwd,
                        "settings": settings.to_dict(),
                    },
                )

                if response is None:
                    self.nvim.async_call(
                        lambda: self.nvim.err_write(
                            "Anya: Failed to get system prompt: no response from daemon.\n"
                        )
                    )
                    return

                if response.error:
                    error_message = f"Anya: Failed to get system prompt: {response.error}\n"
                    self.nvim.async_call(
                        lambda message=error_message: self.nvim.err_write(message)
                    )
                    return

                prompt = response.payload.get("system_prompt", "")
                if not prompt:
                    self.nvim.async_call(
                        lambda: self.nvim.err_write(
                            "Anya: No system prompt available.\n"
                        )
                    )
                    return

                # Display in snacks scratch buffer via Lua
                self.nvim.async_call(
                    lambda: self.nvim.exec_lua(
                        f"require('anya.system_prompt').display({repr(prompt)})"
                    )
                )
            except Exception as e:
                error_message = f"Anya: Error getting system prompt: {e}\n"
                self.nvim.async_call(
                    lambda message=error_message: self.nvim.err_write(message)
                )

        # Run in background thread
        import threading

        thread = threading.Thread(target=fetch_and_display, daemon=True)
        thread.start()

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
