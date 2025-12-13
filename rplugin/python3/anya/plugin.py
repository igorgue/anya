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
from .mcp_loader import MCPManager
from .agents import CodeAgent, MCPAgent, MAIN_AGENT_NAME, MAIN_ASSISTANT_NAME
from . import ui
from . import engine
from . import utils

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
        self._cancel_in_progress = False  # Prevent cancel spam
        self._streaming_started = False  # Track if we've received any content
        self._request_cancelled = False  # Flag for async handler to check
        self.session_id = str(uuid.uuid4())  # Session ID for this Neovim instance
        self.allowed_commands = set()  # Persist allowed commands across agent runs
        self._tool_fold_open = False  # Track if a tool fold is currently open
        self._mcp_manager = MCPManager(nvim)  # MCP server manager with caching
        self._last_layout = "replace"  # Remember the last layout used

        # Agent instances (initialized later when MCP servers are ready)
        self._agent = None  # Main CodeAgent instance
        self._mcp_agent = None  # MCPAgent instance (if MCP servers are available)
        self._agent_initialization_lock = (
            asyncio.Lock()
        )  # Prevent concurrent agent initialization

        # Start MCP server connection in background on plugin load
        mcp_enabled = os.environ.get("ANYA_DISABLE_MCP", "0") != "1"
        if mcp_enabled and self._mcp_manager.load_configs():
            loop = self._ensure_loop()
            asyncio.run_coroutine_threadsafe(self._initialize_agents_with_mcp(), loop)
        else:
            # Initialize without MCP servers
            loop = self._ensure_loop()
            asyncio.run_coroutine_threadsafe(
                self._initialize_agents_without_mcp(), loop
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

    async def _initialize_agents_with_mcp(self):
        """Initialize agents with MCP servers support."""
        async with self._agent_initialization_lock:
            if self._agent is not None:
                return  # Already initialized

            try:
                # Get MCP servers
                mcp_servers = await self._mcp_manager.get_connected_servers()

                # Initialize the main CodeAgent with MCP servers
                self._agent = await CodeAgent(
                    mcp_servers=mcp_servers,
                    thinking_budget=DEFAULT_THINKING_BUDGET,
                )

                # Initialize MCP agent if servers are available
                if mcp_servers:
                    self._mcp_agent = await MCPAgent(mcp_servers)

                # TODO: handle this on fidget
                # self.nvim.async_call(
                #     self.nvim.out_write,
                #     f"Anya: Initialized agents with {len(mcp_servers or [])} MCP servers.\n",
                # )
            except Exception as e:
                self.nvim.async_call(
                    self.nvim.err_write,
                    f"Anya: Failed to initialize agents with MCP: {e}\n",
                )
                # Fallback to initialization without MCP
                await self._initialize_agents_without_mcp()

    async def _initialize_agents_without_mcp(self):
        """Initialize agents without MCP servers."""
        async with self._agent_initialization_lock:
            if self._agent is not None:
                return  # Already initialized

            try:
                # Initialize the main CodeAgent without MCP servers
                self._agent = await CodeAgent(
                    mcp_servers=None,
                    thinking_budget=DEFAULT_THINKING_BUDGET,
                )

                self.nvim.async_call(
                    self.nvim.out_write,
                    "Anya: Initialized agents without MCP servers.\n",
                )
            except Exception as e:
                self.nvim.async_call(
                    self.nvim.err_write, f"Anya: Failed to initialize agents: {e}\n"
                )

    async def _get_or_initialize_agent(self):
        """Get the initialized agent, initializing if necessary."""
        async with self._agent_initialization_lock:
            if self._agent is None:
                # Not initialized yet, do it now
                mcp_enabled = os.environ.get("ANYA_DISABLE_MCP", "0") != "1"
                if mcp_enabled and self._mcp_manager.load_configs():
                    try:
                        mcp_servers = await asyncio.wait_for(
                            self._mcp_manager.get_connected_servers(), timeout=5.0
                        )
                        self._agent = await CodeAgent(
                            mcp_servers=mcp_servers,
                            thinking_budget=DEFAULT_THINKING_BUDGET,
                        )
                        if mcp_servers:
                            self._mcp_agent = await MCPAgent(mcp_servers)
                    except asyncio.TimeoutError:
                        await self._initialize_agents_without_mcp()
                else:
                    await self._initialize_agents_without_mcp()

            return self._agent

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

    def _open_interface(self, layout="split", direction=None):
        """Open the Anya interface with floating chat and prompt windows.

        Args:
            layout: Layout hint (kept for compatibility; "pane" toggles, "tab" opens a new tab)
            direction: Layout hint (kept for compatibility)
        """
        # Remember the layout for reopening
        self._last_layout = layout

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

        chat_buf = ui.get_chat_buffer(self.nvim)
        if not chat_buf:
            self.nvim.err_write("Anya: Chat buffer not found.\n")
            return
        loop = self._ensure_loop()
        request_id = ids.new()
        self._streaming_started = False  # Reset streaming flag for new request
        self._request_cancelled = False  # Reset cancellation flag for new request
        self._current_task = asyncio.run_coroutine_threadsafe(
            engine.run_agent_streaming(
                self, text, conversation_id, chat_buf.number, request_id
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
            cancel_msg = "\n> cancelled 󱋟 "
            ui.append_to_chat_buffer(self.nvim, chat_buf.number, cancel_msg)

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

        # Handle slash commands
        if text and text.strip().startswith("/"):
            self._handle_slash_command(text.strip(), conversation_id)
            return

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
            # Don't save to history again since it was already saved before slash command handling
            self.send(command, conversation_id)

    def _clear_command(self):
        """Handle /clear command."""
        self.nvim.exec_lua('require("anya.conversation").clear_conversation()', [])

    def _help_command(self):
        """Handle /help command by showing help in the chat buffer."""
        from . import markers
        from . import ids

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

        # Get the chat buffer number and conversation ID
        chat_buf = ui.get_chat_buffer(self.nvim)
        if not chat_buf or not self.nvim.api.buf_is_valid(chat_buf):
            return

        # Get conversation ID from buffer
        conv_id = None
        try:
            conv_id = self.nvim.api.buf_get_var(chat_buf, "anya_conversation_id")
        except Exception:
            pass

        # Generate message ID and timestamp
        msg_id = ids.new(conversation=conv_id)
        now = datetime.now(timezone.utc)
        timestamp = (
            now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        )

        # Stream message with proper marker
        ui.stream_text_to_buffer(
            self.nvim,
            chat_buf.number,
            "\n" + markers.make_message_marker(msg_id) + "\n",
        )
        ui.stream_text_to_buffer(self.nvim, chat_buf.number, help_text)
        ui.stream_text_to_buffer(self.nvim, chat_buf.number, "\n\n")

    def _file_command(self):
        """Handle /file command."""
        # TODO: Implement file picker integration
        chat_buf = ui.get_chat_buffer(self.nvim)
        if chat_buf and self.nvim.api.buf_is_valid(chat_buf):
            ui.stream_text_to_buffer(
                self.nvim, chat_buf.number, "File picker not yet implemented.\n\n"
            )

    def _compact_command(self):
        """Handle /compact command."""
        # TODO: Implement context compaction
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
        # Produce e.g. 2024-06-06T03:21:19.348Z
        return (
            now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"
        )

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

    @pynvim.function("AnyaRepositionFloats", sync=True)
    def reposition_floats(self, _args):
        """Reposition floating windows when terminal is resized.

        Called by VimResized autocmd.
        """
        buffers.reposition_floats(self.nvim)

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
        ui.process_markers(self.nvim, bufnr)

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

        return ui.render_edit_blocks(self.nvim, bufnr, edit_str)

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
