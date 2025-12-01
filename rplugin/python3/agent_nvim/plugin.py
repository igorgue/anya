"""Main plugin class for agent.nvim."""

import pynvim
import os
import logging
import asyncio
import uuid

# Try to import typing, with fallback for older Python versions
try:
    from typing import List, Dict, Any, Optional, Tuple
except ImportError:
    # Fallback for older Python versions
    List = list
    Dict = dict
    Any = object
    Optional = type
    Tuple = tuple

from . import installation, tools, utils
from .buffers import BufferManager
from .mcp import MCPManager
from .agent_runner import run_agent
from .tool_budget import ToolBudget
from .compact_agent import CompactAgent, ContextAnalyzer
from .compact_preview import CompactPreviewModal
from . import tool_tracker
from . import exec_permissions
from .config import ConfigManager

# Constants
PLUGIN_NAME = "agent.nvim"


@pynvim.plugin
class AgentPlugin(object):
    """Main plugin class for agent.nvim."""

    def __init__(self, nvim):
        self.nvim = nvim
        self._setup_logging()

        # Initialize configuration manager
        self.config_manager = ConfigManager(self.logger)

        # Initialize managers
        self.buffer_manager = BufferManager(nvim, self.logger)
        self.mcp_manager = MCPManager(self.logger)

        # Initialize compact agent components
        self._setup_compact_agent()

        # Conversation state
        self._conversation_history = []
        self._cancel_requested = False
        self._current_request_id = None
        self._cached_cwd = None
        self._agent_busy = False

        # Tool wrappers will be created lazily
        self._tool_wrappers = None

        # Initialize Lua module (history will be initialized by ftplugin when buffer opens)
        try:
            nvim.exec_lua("require('agent_nvim')")
            self._sync_config_to_lua()
        except Exception as e:
            self.logger.debug(f"Failed to initialize Lua module: {e}")

    def _setup_compact_agent(self):
        """Initialize the specialized CompactAgent with model configuration."""
        try:
            compact_model = self._get_compact_model()
            self.compact_agent = CompactAgent(model=compact_model, logger=self.logger)
            self.context_analyzer = ContextAnalyzer(self.logger)
            self.preview_modal = CompactPreviewModal(self.nvim, self.logger)

            # Check if the agent was created successfully
            if self.compact_agent.agent is None:
                self.logger.warning(
                    "CompactAgent created but agent is None - using fallback mode"
                )
                self.nvim.out_write(
                    "  CompactAgent initialized in fallback mode (limited functionality). Install OpenAI agents SDK for full features.\n"
                )
            else:
                self.logger.info(
                    f"Compact agent initialized with model: {compact_model}"
                )

        except Exception as e:
            self.logger.error(f"Failed to setup compact agent: {e}")
            self.compact_agent = None
            self.context_analyzer = None
            self.preview_modal = None
            self.nvim.out_write(f"  Failed to initialize CompactAgent: {e}\n")

    def _get_compact_model(self) -> str:
        """Get model for compact agent, with environment variable override."""
        custom_model = os.environ.get("AGENT_COMPACT_MODEL")
        if custom_model:
            self.logger.info(
                f"Using custom compact model from AGENT_COMPACT_MODEL: {custom_model}"
            )
            return custom_model

        # Use a fast, cheap model for compaction by default
        model = "gpt-4.1-mini"
        self.logger.info(f"Using default compact model: {model}")
        return model

    def _get_conversation_context(self) -> List[Dict]:
        """Get current conversation context from stored conversation history."""
        try:
            # Return the maintained conversation history directly
            # This is the source of truth, not the buffer representation
            if self._conversation_history:
                return self._conversation_history
            else:
                return []

        except Exception as e:
            self.logger.error(f"Error getting conversation context: {e}")
            return []

    def _redraw_buffer_with_history(self, compacted_history: List[Dict]):
        """Redraw the buffer with a new conversation history.

        This clears the content buffer and reconstructs it from the compacted history.
        """
        try:
            if (
                not hasattr(self.buffer_manager, "content_buf")
                or not self.buffer_manager.content_buf
            ):
                return

            # Build markdown lines from conversation history
            lines = []
            for msg in compacted_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")

                # Format as markdown message with role header
                role_display = role.title() if role != "system" else "System"
                lines.append(f"## {role_display}")
                lines.append(content)
                lines.append("")

            # Set buffer content
            self.nvim.api.buf_set_lines(
                self.buffer_manager.content_buf, 0, -1, False, lines
            )

            # Update internal conversation history
            self._conversation_history = []

            self.logger.info(
                f"Redrew buffer with {len(compacted_history)} compacted messages"
            )
        except Exception as e:
            self.logger.error(f"Error redrawing buffer: {e}")

    def _apply_compacted_context(self, summary: str):
        """Apply compacted context to the conversation buffer."""
        try:
            if (
                not hasattr(self.buffer_manager, "content_buf")
                or not self.buffer_manager.content_buf
            ):
                return

            # Clean up summary: remove "USER: " and "ASSISTANT: " prefixes
            cleaned_lines = []
            for line in summary.split("\n"):
                # Remove USER: or ASSISTANT: prefixes if present
                if line.startswith("USER: "):
                    cleaned_lines.append(line[6:])
                elif line.startswith("ASSISTANT: "):
                    cleaned_lines.append(line[11:])
                else:
                    cleaned_lines.append(line)

            new_content = cleaned_lines

            self.nvim.api.buf_set_lines(
                self.buffer_manager.content_buf, 0, -1, False, new_content
            )

            # Update conversation history
            self._conversation_history = [
                {
                    "role": "system",
                    "content": f"Context was compacted. Summary: {summary}",
                }
            ]

            # Show success message
            self.buffer_manager.append_content(
                [
                    "> **Context compacted successfully**",
                    "Conversation reduced to essential context.",
                    "",
                ]
            )

            self.logger.info("Applied compacted context to conversation")

        except Exception as e:
            self.logger.error(f"Error applying compacted context: {e}")
            self.nvim.err_write(f"Error applying compacted context: {e}\n")

    def _setup_logging(self):
        """Set up logging configuration."""
        self.logger = logging.getLogger("agent_nvim")
        handler = logging.FileHandler(
            os.path.expanduser(f"~/.local/state/nvim/{PLUGIN_NAME}.log")
        )
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def _sync_config_to_lua(self):
        """Synchronize configuration state from Python to Lua."""
        try:
            agent = self.config_manager.get("agent", "AUTO")
            mode = self.config_manager.get("mode", "ASK")

            # Sync YOLO mode to environment variable so is_yolo_mode() works
            import os

            os.environ["AGENT_YOLO"] = "1" if mode == "YOLO" else ""

            # Set global Lua state for toolbar
            lua_code = f"""
            _G.agent_state = {{
                agent = "{agent}",
                mode = "{mode}"
            }}
            
            -- Set up callback for config changes from Lua
            function _G.agent_config_callback(key, value)
                -- This will be called by toolbar when user toggles settings
                -- We need to inform the Python plugin via a command
                vim.api.nvim_command(string.format("AgentConfigUpdate %s %s", key, value))
            end
            """
            self.nvim.exec_lua(lua_code)
            self.logger.debug(f"Synced config to Lua: agent={agent}, mode={mode}")
        except Exception as e:
            self.logger.error(f"Failed to sync config to Lua: {e}")

    def _update_config_from_lua(self, key: str, value: str):
        """Update configuration when changed from Lua toolbar.

        Args:
            key: Configuration key (agent or mode)
            value: New value
        """
        try:
            self.config_manager.set(key, value)

            # Sync YOLO mode to environment variable so is_yolo_mode() works
            if key == "mode":
                import os

                os.environ["AGENT_YOLO"] = "1" if value == "YOLO" else ""

            self.logger.debug(f"Updated config: {key} = {value}")
        except Exception as e:
            self.logger.error(f"Failed to update config: {e}")

    def _get_tool_wrappers(self, tool_budget: ToolBudget | None = None):
        """Get tool wrappers, creating them with optional budget tracking.

        Args:
            tool_budget: Optional ToolBudget instance for tracking token usage
        """
        try:
            from agents import function_tool

            # Create closures that capture self, cached_cwd, and tool_budget
            # Budget tracking is done inline to preserve function signatures for the SDK
            async def read_file(path: str, timeout: int = 30) -> str:
                """Read file content with automatic truncation for large files.

                SYNTAX: Use @ notation for line ranges:
                - path/file.py: Read first 100 lines (auto-truncated if larger)
                - path/file.py@start-end: Read entire file
                - path/file.py@10-50: Read lines 10-50
                - path/file.py@start-100: Read lines 1-100
                - path/file.py@100-end: Read from line 100 to end

                IMPORTANT: When you see "[FILE TOO LARGE]" message with current range info,
                IMMEDIATELY use the @start-end syntax to read the full file. Do NOT keep
                reading the same truncated file without specifying a range.

                Args:
                    path: File path with optional @range specification
                    timeout: Timeout in seconds (default 30)"""
                # Check budget before reading (heavy tool)
                if tool_budget and not tool_budget.can_use_budget(heavy_tool=True):
                    return tool_budget.get_budget_exceeded_message()

                cwd = getattr(self, "_cached_cwd", None)

                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(tools.read_file, path, cwd), timeout=timeout
                    )

                    # Track token usage
                    if tool_budget and isinstance(result, str):
                        tool_budget.consume(result)
                except asyncio.TimeoutError:
                    result = f"Error: Tool timed out after {timeout} seconds"

                # Record file read for conversation history preservation
                truncated = (
                    "FILE TRUNCATED" in result if isinstance(result, str) else False
                )
                tool_tracker.record_file_read(path, result, truncated)

                return result

            async def read_many_files(files: List[str], timeout: int = 30) -> str:
                """Read multiple files in a single call, with optional line ranges (file@start-end).

                Args:
                    files: List of file paths with optional @range specifications
                    timeout: Timeout in seconds (default 30)"""
                # Check budget before reading (heavy tool)
                if tool_budget and not tool_budget.can_use_budget(heavy_tool=True):
                    return tool_budget.get_budget_exceeded_message()

                cwd = getattr(self, "_cached_cwd", None)

                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(tools.read_many_files, files, cwd),
                        timeout=timeout,
                    )

                    # Track token usage
                    if tool_budget and isinstance(result, str):
                        tool_budget.consume(result)
                except asyncio.TimeoutError:
                    result = f"Error: Tool timed out after {timeout} seconds"

                # Record multiple file reads for conversation history preservation
                for file_spec in files:
                    truncated = (
                        "FILE TOO LARGE" in result if isinstance(result, str) else False
                    )
                    tool_tracker.record_file_read(file_spec, result, truncated)

                return result

            async def list_files(path: str = ".", timeout: int = 30) -> str:
                """List files in directory.

                Args:
                    path: Directory path to list (default current directory)
                    timeout: Timeout in seconds (default 30)"""
                cwd = getattr(self, "_cached_cwd", None)

                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(tools.list_files, path, cwd), timeout=timeout
                    )

                    # Track token usage (light tool, no budget check)
                    if tool_budget and isinstance(result, str):
                        tool_budget.consume(result)

                    # Record file list for conversation history preservation
                    tool_tracker.record_file_list(path, result)
                except asyncio.TimeoutError:
                    result = f"Error: Tool timed out after {timeout} seconds"

                return result

            async def search_repo(query: str, timeout: int = 30) -> str:
                """Search repository.

                Args:
                    query: Search query string
                    timeout: Timeout in seconds (default 30)"""
                # Check budget before searching (heavy tool)
                if tool_budget and not tool_budget.can_use_budget(heavy_tool=True):
                    return tool_budget.get_budget_exceeded_message()

                cwd = getattr(self, "_cached_cwd", None)

                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(tools.search_repo, query, cwd),
                        timeout=timeout,
                    )

                    # Track token usage
                    if tool_budget and isinstance(result, str):
                        tool_budget.consume(result)
                except asyncio.TimeoutError:
                    result = f"Error: Tool timed out after {timeout} seconds"

                # Record search for conversation history preservation
                tool_tracker.record_search(query, result)

                return result

            def patch(patch_str: str) -> str:
                """Propose a patch. Agent stops and waits for user to apply (1) or reject (2).
                DEPRECATED: Use the `edit` tool with SEARCH/REPLACE blocks instead."""
                return tools.patch(patch_str)

            def edit(edit_blocks: str) -> str:
                """Make code edits using SEARCH/REPLACE blocks.

                Format for each edit:
                ```
                path/to/file.py
                <<<<<<< SEARCH
                exact code to find (must match exactly including whitespace)
                =======
                replacement code
                >>>>>>> REPLACE
                ```

                Rules:
                - SEARCH must EXACTLY match existing code
                - Use multiple blocks for multiple changes
                - For new files: use empty SEARCH section
                """
                return tools.edit(edit_blocks)

            async def exec_lua(code: str) -> str:
                """Execute Lua code inside Neovim."""
                return await tools.exec_lua(code, nvim=self.nvim, logger=self.logger)

            async def exec(command: str, timeout: int = 30) -> str:
                """Execute a shell command with user permission."""
                cwd = getattr(self, "_cached_cwd", None)

                self.logger.info(f"exec called with command: {command}")

                # YOLO mode: run without asking
                if exec_permissions.is_yolo_mode():
                    self.logger.info("YOLO mode: executing command without prompt")
                    return tools.exec(command, cwd=cwd, timeout=timeout)

                # Check if command is in allow list
                allow_list = exec_permissions.load_allow_list()
                self.logger.info(f"Allow list: {allow_list}")
                self.logger.info(f"Allow list path: {exec_permissions.ALLOW_LIST_PATH}")

                if exec_permissions.is_command_allowed(command):
                    self.logger.info("Command in allow list, executing")
                    return tools.exec(command, cwd=cwd, timeout=timeout)

                self.logger.info("Command NOT in allow list, prompting user")

                # Prompt user for permission
                choice = await exec_permissions.prompt_exec_permission(
                    self.nvim, command, self.logger
                )

                self.logger.info(f"User choice: {choice}")

                if choice == "run":
                    return tools.exec(command, cwd=cwd, timeout=timeout)
                elif choice == "always":
                    self.logger.info(f"Adding command to allow list: {command}")
                    exec_permissions.add_to_allow_list(command)
                    self.logger.info(
                        f"Allow list after add: {exec_permissions.load_allow_list()}"
                    )
                    return tools.exec(command, cwd=cwd, timeout=timeout)
                else:
                    return "Command execution was declined by user."

            async def gh(command: str, timeout: int = 30) -> str:
                """Execute GitHub CLI (gh) commands to interact with GitHub.

                Args:
                    command: GitHub CLI command to execute (can start with or without 'gh')
                    timeout: Timeout in seconds (default 30)"""
                cwd = getattr(self, "_cached_cwd", None)

                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(tools.gh, command, cwd, timeout),
                        timeout=timeout,
                    )

                    # Track token usage (light tool, no budget check)
                    if tool_budget and isinstance(result, str):
                        tool_budget.consume(result)
                except asyncio.TimeoutError:
                    result = (
                        f"Error: GitHub CLI command timed out after {timeout} seconds"
                    )

                return result

            return {
                "read_file": function_tool(read_file),
                "read_many_files": function_tool(read_many_files),
                "list_files": function_tool(list_files),
                "search_repo": function_tool(search_repo),
                "patch": function_tool(patch),
                "edit": function_tool(edit),
                "exec_lua": function_tool(exec_lua),
                "exec": function_tool(exec),
                "gh": function_tool(gh),
            }
        except ImportError:
            self.logger.warning("Could not import function_tool from agents")
            return {}

    @pynvim.command("AgentInstall", sync=False)
    def agent_install(self):
        """Install agent.nvim dependencies."""
        self.nvim.out_write("Installing agent.nvim dependencies...\\n")
        # Get plugin root directory
        plugin_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../..")
        )
        # Run installation in background
        asyncio.create_task(installation.install_deps(self.nvim, plugin_root))

    @pynvim.command("AgentTestImport", sync=False)
    def agent_test_import(self):
        """Test that required imports are available."""
        installation.test_imports(self.nvim)

    @pynvim.command("AgentOpen", sync=False)
    def agent_open(self):
        """Open the agent interface."""
        from .token_tracker import reset_session_tokens, get_context_window

        reset_session_tokens()

        # Get model and context window for placeholder
        model = os.environ.get("AGENT_MODEL", "gpt-5.1")
        context_window = get_context_window(model)

        # Format context window size
        if context_window >= 1000000:
            ctx_str = f"{context_window / 1000000:.0f}M"
        elif context_window >= 1000:
            ctx_str = f"{context_window / 1000:.0f}K"
        else:
            ctx_str = str(context_window)

        placeholder_text = f"0% of {ctx_str}"

        # Create layout and set placeholder
        def _open_with_placeholder():
            self.buffer_manager.create_layout()
            # Set placeholder after layout is created
            self.nvim.exec_lua(
                f'_G.AgentSetPlaceholder("{placeholder_text}", "Comment")'
            )

        self.nvim.async_call(_open_with_placeholder)

    @pynvim.command("AgentSubmit", sync=False)
    def agent_submit(self):
        """Submit the current prompt to the agent."""
        # Don't submit if agent is busy
        if self._agent_busy:
            return

        # Apply any pending patches first
        self.nvim.async_call(self.buffer_manager.apply_pending_patches)

        # Get content from prompt buffer
        prompt_buf = self.nvim.current.buffer
        lines = prompt_buf[:]
        text = "\n".join(lines).strip()

        if not text:
            return

        # Check for /file command - handle specially (don't submit, modify prompt)
        if "/file" in text:
            self._handle_file_command_inline(text, prompt_buf)
            return

        # Save to history via Lua - use existing instance, don't create new one
        try:
            result = self.nvim.exec_lua(
                """
                local args = {...}
                local prompt_text = args[1]
                if not prompt_text or type(prompt_text) ~= "string" or prompt_text == "" then
                    return false
                end

                -- Use existing global history instance
                if not _G.agent_prompt_history then
                    -- Initialize if missing (shouldn't happen if ftplugin loaded)
                    local history = require('agent_nvim.history')
                    _G.agent_prompt_history = history.new()
                end

                -- Record directly to the history instance
                local success = _G.agent_prompt_history:record(prompt_text)
                _G.agent_prompt_history:reset()
                return success
            """,
                text,
            )

            if not result:
                self.logger.warning("Failed to save prompt to history")

        except Exception as e:
            self.logger.error(f"Failed to save prompt to history: {e}")

        # Clear prompt buffer
        self.nvim.api.buf_set_lines(prompt_buf, 0, -1, False, [""])

        # Handle slash commands
        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            self._handle_user_prompt(text)

    @pynvim.command("AgentCancel", sync=False)
    def agent_cancel(self):
        """Cancel the currently running agent request or compaction."""
        if self._cancel_requested:
            # Already cancelled
            return

        if self._current_request_id:
            self._cancel_requested = True
            # Emit cancelling event for fidget
            utils.emit_user_event(
                self.nvim, "AgentCancelling", {"id": self._current_request_id}
            )
            # Only add spacing if there's already content in the response
            self.nvim.async_call(self.buffer_manager.append_cancel_message)
        elif hasattr(self, "_compact_cancelled") and not self._compact_cancelled:
            self._compact_cancelled = True
            self.nvim.out_write("Cancelling compaction...\\n")
        else:
            self.nvim.out_write("No active agent request or compaction to cancel.\\n")

    @pynvim.command("AgentApply", sync=False)
    def agent_apply(self):
        """Apply the patch in the AgentDiff buffer."""
        self.buffer_manager.apply_patch()

    @pynvim.function("AgentPatchAction", sync=False)
    def agent_patch_action(self, args):
        """Handle patch actions from Lua (apply/reject) and continue conversation."""
        try:
            # Args: action, patch_content, previous_state
            # previous_state: 0=PENDING (first decision), 1=ACCEPT, 2=REJECT
            action = args[0]
            patch_content = args[1]
            previous_state = args[2] if len(args) > 2 else 0

            success = False
            message = ""
            is_first_decision = previous_state == 0  # PENDING state

            if action == "apply":
                success = self.buffer_manager._apply_single_patch(patch_content)
                if success:
                    self.logger.info("Patch applied!")
                    if is_first_decision:
                        message = "PATCH_APPLIED: The patch was successfully applied."
                else:
                    # Get the error from the last apply attempt
                    if is_first_decision:
                        message = "PATCH_FAILED: The patch could not be applied. Please read the target file again and regenerate the patch with correct context lines."
            elif action == "reject":
                # Reverse the patch (undo) - this is called when user rejects an already-applied patch
                # In normal mode: user pressed 1 (apply) then 2 (reject)
                # In YOLO mode: patch was auto-applied, user pressed 2 to undo
                success = self.buffer_manager._apply_single_patch(
                    patch_content, reverse=True
                )
                if success:
                    self.logger.info("Patch reversed (rejected)!")
                else:
                    self.logger.warning(
                        "Failed to reverse patch - may not have been applied"
                    )
                # Don't continue agent - this is an undo action
                return
            elif action == "reject_pending":
                # First decision to reject (from PENDING state) - no patch to reverse
                success = True
                message = "PATCH_REJECTED: The user rejected the patch. Ask if they want changes or a different approach."

            # Only continue agent on first decision (from PENDING state)
            if message and is_first_decision:
                self._conversation_history.append({"role": "user", "content": message})

                # Show feedback in content buffer
                feedback_text = (
                    "> Patch applied successfully."
                    if action == "apply" and success
                    else (
                        ">   Patch rejected by user."
                        if action == "reject_pending"
                        else ">   Patch failed to apply."
                    )
                )
                self.nvim.async_call(
                    self.buffer_manager.append_content, ["", feedback_text]
                )

                self.nvim.async_call(self.buffer_manager.append_content, ["", ""])

                # Continue the agent automatically (without header)
                self._continue_agent_after_patch(skip_header=True)

        except Exception as e:
            self.logger.error(f"Error in AgentPatchAction: {e}")
            self.nvim.err_write(f"Error applying/reverting patch: {e}\n")

    @pynvim.function("AgentEditAction", sync=False)
    def agent_edit_action(self, args):
        """Handle edit actions from Lua (apply/reject) and continue conversation."""
        try:
            # Args: action, edit_content, previous_state
            # previous_state: 0=PENDING (first decision), 1=ACCEPT, 2=REJECT
            action = args[0]
            edit_content = args[1]
            previous_state = args[2] if len(args) > 2 else 0

            success = False
            message = ""
            is_first_decision = previous_state == 0  # PENDING state

            if action == "apply":
                success = self.buffer_manager.apply_edit_blocks(edit_content)
                if success:
                    self.logger.info("Edit blocks applied!")
                    if is_first_decision:
                        message = "EDIT_APPLIED: The SEARCH/REPLACE edits were successfully applied."
                else:
                    if is_first_decision:
                        message = "EDIT_FAILED: The edits could not be applied. The SEARCH content did not match the file. Please read the target file again and regenerate the edit with correct content."
            elif action == "reject":
                # Undo - restore from backups
                success = self.buffer_manager._restore_backups()
                if success:
                    self.logger.info("Edit blocks reversed (rejected)!")
                else:
                    self.logger.warning(
                        "Failed to reverse edits - may not have been applied"
                    )
                return
            elif action == "reject_pending":
                # First decision to reject - no edits to reverse
                success = True
                message = "EDIT_REJECTED: The user rejected the edits. Ask if they want changes or a different approach."

            # Only continue agent on first decision
            if message and is_first_decision:
                self._conversation_history.append({"role": "user", "content": message})

                # Show feedback in content buffer
                feedback_text = (
                    "> Edits applied successfully."
                    if action == "apply" and success
                    else (
                        ">   Edits rejected by user."
                        if action == "reject_pending"
                        else ">   Edits failed to apply."
                    )
                )
                self.nvim.async_call(
                    self.buffer_manager.append_content, ["", feedback_text]
                )

                self.nvim.async_call(self.buffer_manager.append_content, ["", ""])

                # Continue the agent automatically
                self._continue_agent_after_patch(skip_header=True)

        except Exception as e:
            self.logger.error(f"Error in AgentEditAction: {e}")
            self.nvim.err_write(f"Error applying/reverting edits: {e}\n")

    def _continue_agent_after_patch(self, skip_header=False):
        """Continue the agent after a patch action."""
        import uuid

        # Don't run if already busy
        if self._agent_busy:
            self.logger.info("Agent already busy, skipping continuation")
            return

        # Cache cwd before async operations
        self._cached_cwd = self.nvim.call("getcwd")

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Reset cancellation flag and set current request ID
        self._cancel_requested = False
        self._current_request_id = request_id
        self._agent_busy = True

        # Run agent in background
        asyncio.create_task(
            self._run_agent_wrapper(request_id, skip_header=skip_header)
        )

    @pynvim.command("AgentConfigUpdate", nargs="*", sync=True)
    def agent_config_update(self, args):
        """Update agent configuration from Lua toolbar.

        Args:
            args: [key, value] pairs
        """
        try:
            if len(args) < 2:
                return

            key = args[0]
            value = args[1]

            # Update configuration
            self._update_config_from_lua(key, value)
        except Exception as e:
            self.logger.error(f"Error updating config: {e}")
            self.nvim.err_write(f"Config update error: {e}\n")

    @pynvim.command("AgentSyncConfig", sync=False)
    def agent_sync_config(self):
        """Reload and sync configuration from disk to Lua toolbar."""
        try:
            # Reload config from disk
            self.config_manager._config = self.config_manager._load_config()
            # Sync to Lua
            self._sync_config_to_lua()
            self.nvim.out_write("Configuration reloaded and synced\n")
        except Exception as e:
            self.logger.error(f"Error syncing config: {e}")
            self.nvim.err_write(f"Sync config error: {e}\n")

    @pynvim.command("AgentHistoryTest", sync=True)
    def agent_history_test(self):
        """Test and diagnose the history system."""
        result = self.nvim.exec_lua(
            """
            if not _G.agent_prompt_history then
                return {status = "ERROR", message = "History instance not found"}
            end

            -- Test saving
            local test_msg = "History test at " .. os.date()
            local save_result = _G.AgentHistorySavePrompt(test_msg)

            -- Get diagnostic info
            local diagnostic = _G.agent_prompt_history:diagnostic()

            return {
                status = save_result and "OK" or "SAVE_FAILED",
                test_message = test_msg,
                save_result = save_result,
                diagnostic = diagnostic
            }
        """,
            [],
        )

        self.nvim.out_write("History Test Results:\n")
        self.nvim.out_write(f"Status: {result['status']}\n")
        self.nvim.out_write(f"Test Message: {result['test_message']}\n")
        self.nvim.out_write(f"Save Result: {result['save_result']}\n")
        self.nvim.out_write(f"History File: {result['diagnostic']['path']}\n")
        self.nvim.out_write(f"Entry Count: {result['diagnostic']['entry_count']}\n")

        if result["diagnostic"]["issues"] and len(result["diagnostic"]["issues"]) > 0:
            self.nvim.out_write("Issues:\n")
            for issue in result["diagnostic"]["issues"]:
                self.nvim.out_write(f"  - {issue}\n")
        else:
            self.nvim.out_write("No issues found\n")

    @pynvim.function("AgentComplete", sync=True)
    def agent_complete(self, args):
        """Provide file path completions for @mentions."""
        findstart, base = args
        return self.buffer_manager.get_completions(findstart, base)

    @pynvim.function("AgentCompleteAsync", sync=False)
    def agent_complete_async(self, args):
        """Provide async file path completions for @mentions."""
        base, callback_id = args
        self.buffer_manager.get_file_completions_async(base, callback_id)

    @pynvim.function("AgentHighlightPrompt", sync=False)
    def agent_highlight_prompt(self, args):
        """Highlight file references in the prompt buffer."""
        self.buffer_manager.highlight_prompt_buffer()

    def _parse_compact_command(self, args: List[str]) -> tuple:
        """Parse command into parameters and natural language instructions."""
        params = {}
        instructions_parts = []

        i = 0
        while i < len(args):
            arg = args[i]

            if arg.startswith("--"):
                flag = arg

                # Check if this is a flag with value (either --flag=value or --flag value)
                if "=" in flag:
                    flag, value = flag.split("=", 1)
                    params[flag] = value
                elif i + 1 < len(args) and not args[i + 1].startswith("--"):
                    # Next argument is the value for this flag
                    params[flag] = args[i + 1]
                    i += 1  # Skip the value in next iteration
                else:
                    # Boolean flag
                    params[flag] = True
            else:
                # This is part of natural language instructions
                instructions_parts.append(arg)

            i += 1

        instructions = " ".join(instructions_parts).strip()
        return params, instructions

    def _fallback_compaction(
        self,
        conversation_history: List,
        instructions: str = None,
        target_tokens: int = None,
    ):
        """Simple fallback compaction when agent is not available."""
        try:
            # Simple compaction logic
            if not conversation_history:
                return "No conversation history available for compaction."

            # Format conversation
            formatted_lines = []
            for msg in conversation_history:
                if isinstance(msg, dict):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    formatted_lines.append(f"## {role.title()}")
                    formatted_lines.append(content)
                    formatted_lines.append("")

            full_text = "\n".join(formatted_lines)

            # Apply simple size reduction
            if target_tokens:
                target_chars = target_tokens * 4
                if len(full_text) > target_chars:
                    # Simple truncation
                    ratio = target_chars / len(full_text)
                    target_lines = int(len(formatted_lines) * ratio)
                    compacted_lines = formatted_lines[:target_lines]

                    if compacted_lines:
                        compacted_lines.append("")
                        compacted_lines.append("... [content compacted] ...")
                        compacted_lines.append("")
                        compacted_lines.append(
                            "*Fallback compaction applied - install OpenAI agents SDK for AI-powered compaction*"
                        )

                    return "\n".join(compacted_lines)

            # Default: keep first 60%
            if len(formatted_lines) > 10:
                cutoff = int(len(formatted_lines) * 0.6)
                compacted_lines = formatted_lines[:cutoff]
                compacted_lines.extend(
                    [
                        "",
                        "... [content compacted] ...",
                        "",
                        f"Original conversation: {len(conversation_history)} messages",
                        "Compacted with fallback mode",
                        "*Install OpenAI agents SDK for advanced AI-powered compaction*",
                    ]
                )
                return "\n".join(compacted_lines)

            return full_text

        except Exception as e:
            self.logger.error(f"Error in fallback compaction: {e}")
            return f"Error during fallback compaction: {str(e)}\n\nOriginal messages: {len(conversation_history) if conversation_history else 0}"

    def _handle_compact_command(self, args):
        """Handle /compact slash command using the specialized agent."""
        try:
            if not self.compact_agent:
                self.buffer_manager.append_content(
                    [
                        "",
                        "  **Compact agent not available**",
                        "The CompactAgent failed to initialize. This could be due to:",
                        "- Missing OpenAI agents SDK installation",
                        "- Invalid API key configuration",
                        "- Network connectivity issues",
                        "",
                        "Basic fallback compaction is available with limited functionality.",
                        "For full features, run `:AgentInstall` and check your OpenAI API key.",
                        "",
                    ]
                )
                return

            # Parse command for parameters and natural language instructions
            params, instructions = self._parse_compact_command(args)

            # Get target tokens from parameters or infer from instructions
            target_tokens = None
            if "--tokens" in params:
                try:
                    target_tokens = int(params["--tokens"])
                except (ValueError, TypeError):
                    self.buffer_manager.append_content(
                        [
                            "",
                            "  **Invalid token count**",
                            "Please provide a valid number for --tokens parameter.",
                            "",
                        ]
                    )
                    return
            elif instructions and self.compact_agent:
                # Get current context to estimate tokens
                current_context = self._get_conversation_context()
                current_text = "\n".join(
                    msg.get("content", "") for msg in current_context
                )
                current_tokens = len(current_text) // 4  # Rough estimation
                target_tokens = self.compact_agent.infer_token_target(
                    instructions, current_tokens
                )

            # Get current conversation context
            current_context = self._get_conversation_context()

            if not current_context:
                self.buffer_manager.append_content(
                    [
                        "",
                        "** 󰋽  No conversation to compact **",
                        "There's no conversation history available to compact.",
                        "",
                    ]
                )
                return

            # Show initial message
            self.buffer_manager.append_content(
                [
                    "",
                    "> **Compacting...**",
                    f"Target tokens: {target_tokens or 'auto-detect'}",
                    "",
                ]
            )

            # Initialize compact cancellation flag
            self._compact_cancelled = False

            # Create fidget progress handle
            try:
                self.nvim.async_call(
                    self.nvim.exec_lua,
                    """
                    local fidget = require('agent.fidget')
                    _G._compact_progress_handle = fidget:create_progress_handle({
                        data = { model = os.getenv('AGENT_MODEL') or 'gpt-4o' }
                    })
                    """,
                )
            except Exception as e:
                self.logger.debug(f"Could not create fidget progress: {e}")

            # Run compaction in background thread to avoid blocking event loop
            import threading

            def run_compaction_thread():
                try:
                    self._perform_compaction(
                        current_context, instructions, target_tokens
                    )
                except Exception as e:
                    self.logger.error(f"Error in compaction thread: {e}")
                    # Use async_call to update UI from thread
                    self.nvim.async_call(
                        self.buffer_manager.append_content,
                        [f"  **Compaction error**: {str(e)}", ""],
                    )
                finally:
                    # Clean up fidget progress
                    try:
                        self.nvim.async_call(
                            self.nvim.exec_lua,
                            """
                            if _G._compact_progress_handle then
                                _G._compact_progress_handle:finish()
                                _G._compact_progress_handle = nil
                            end
                            """,
                        )
                    except Exception as e:
                        self.logger.debug(f"Could not finish fidget progress: {e}")
                    finally:
                        # Clear the compact cancelled flag when done
                        self._compact_cancelled = False

            thread = threading.Thread(target=run_compaction_thread, daemon=True)
            thread.start()

        except Exception as e:
            self.logger.error(f"Error handling compact command: {e}")
            self.buffer_manager.append_content(
                ["", f"**   Error during compaction **: {str(e)}", ""]
            )

    def _update_fidget_progress(self, message: str):
        """Update fidget progress handle with a message."""
        try:
            self.nvim.async_call(
                self.nvim.exec_lua,
                f"""
                if _G._compact_progress_handle then
                    _G._compact_progress_handle.message = '{message}'
                end
            """,
            )
        except Exception as e:
            self.logger.debug(f"Could not update fidget progress: {e}")

    def _perform_compaction(self, conversation_history, instructions, target_tokens):
        """Perform the actual compaction."""
        try:
            # Check for cancellation before starting
            if self._compact_cancelled:
                self.nvim.async_call(
                    self.buffer_manager.append_content,
                    ["> **Compaction cancelled**", ""],
                )
                return

            # Show progress
            self._update_fidget_progress("Analyzing context...")
            self.nvim.async_call(
                self.buffer_manager.append_content,
                ["", "> **Analyzing conversation context...**"],
            )

            # Check for cancellation
            if self._compact_cancelled:
                self.nvim.async_call(
                    self.buffer_manager.append_content,
                    ["> **Compaction cancelled**", ""],
                )
                return

            # Use context analyzer if available
            if self.context_analyzer:
                key_elements = self.context_analyzer.extract_key_elements(
                    conversation_history
                )
                self.nvim.async_call(
                    self.buffer_manager.append_content,
                    [
                        f"Found {len(key_elements.get('active_tasks', []))} active tasks",
                        f"Found {len(key_elements.get('file_references', []))} file references",
                    ],
                )

            # Generate summary
            self._update_fidget_progress("Generating summary...")
            self.nvim.async_call(
                self.buffer_manager.append_content,
                ["", "> **Generating compacted summary...**"],
            )

            # Check for cancellation before expensive operations
            if self._compact_cancelled:
                self.nvim.async_call(
                    self.buffer_manager.append_content,
                    ["> **Compaction cancelled**", ""],
                )
                return

            if instructions and self.compact_agent:
                # Use instruction-aware compaction
                summary, compacted_history = (
                    self.compact_agent.compact_with_instructions(
                        conversation_history, instructions, target_tokens
                    )
                )
            elif self.compact_agent:
                # Use standard compaction
                summary, compacted_history = self.compact_agent.compact_conversation(
                    conversation_history, target_tokens
                )
            else:
                # Show informative message about fallback mode
                self.nvim.async_call(
                    self.buffer_manager.append_content,
                    [
                        "⚠️ Using fallback compaction mode (limited features)",
                        "*For AI-powered compaction, install OpenAI agents SDK*",
                    ],
                )

                # Use simple fallback compaction
                summary, compacted_history = self._fallback_compaction(
                    conversation_history, instructions, target_tokens
                )

            # Check for cancellation after summary generation
            if self._compact_cancelled:
                self.nvim.async_call(
                    self.buffer_manager.append_content,
                    ["> **Compaction cancelled**", ""],
                )
                return

            # Get original text for comparison
            original_text = "\n".join(
                msg.get("content", "") for msg in conversation_history
            )

            # Show preview and apply
            try:
                self._update_fidget_progress("Showing preview...")
                if self.preview_modal:
                    approved = self.preview_modal.show_preview(original_text, summary)
                else:
                    # Auto-apply if no preview modal available
                    approved = True

                if approved:
                    # Check for cancellation before applying
                    if self._compact_cancelled:
                        self.nvim.async_call(
                            self.buffer_manager.append_content,
                            ["> **Compaction cancelled**", ""],
                        )
                        return

                    # User approved compaction
                    if self.preview_modal:
                        decision, edited_summary = self.preview_modal.get_decision()
                        final_summary = edited_summary if edited_summary else summary
                    else:
                        final_summary = summary

                    self._update_fidget_progress("Applying context...")
                    self.nvim.async_call(
                        self.buffer_manager.append_content,
                        ["", "> **Applying compacted context...**"],
                    )
                    # Clear buffer and redraw with compacted history
                    self.nvim.async_call(
                        self._redraw_buffer_with_history, compacted_history
                    )

                    # Show statistics
                    original_tokens = len(original_text) // 4
                    summary_tokens = len(final_summary) // 4
                    reduction = (
                        (1 - summary_tokens / original_tokens) * 100
                        if original_tokens > 0
                        else 0
                    )

                    self.nvim.async_call(
                        self.buffer_manager.append_content,
                        [
                            "> **Compaction complete**",
                            f"Tokens reduced from ~{original_tokens:,} to ~{summary_tokens:,} ({reduction:.1f}% reduction)",
                            "Conversation context preserved",
                            "",
                        ],
                    )
                else:
                    self.nvim.async_call(
                        self.buffer_manager.append_content,
                        ["> **Compaction cancelled**", ""],
                    )
            except Exception as e:
                self.logger.error(f"Error in compaction preview: {e}")
                self.nvim.async_call(
                    self.buffer_manager.append_content,
                    [f"> Error during compaction: {str(e)}", ""],
                )

        except Exception as e:
            self.logger.error(f"Error performing compaction: {e}")
            self.nvim.async_call(
                self.buffer_manager.append_content,
                [f"> **Compaction failed**: {str(e)}", ""],
            )

    def _handle_slash_command(self, text):
        """Handle slash commands like /clear, /cancel, /help, /compact."""
        parts = text.strip().split()
        cmd = parts[0] if parts else text

        if cmd == "/clear":
            if (
                hasattr(self.buffer_manager, "content_buf")
                and self.buffer_manager.content_buf
                and self.buffer_manager.content_buf.valid
            ):
                self.nvim.async_call(
                    self.nvim.api.buf_set_lines,
                    self.buffer_manager.content_buf,
                    0,
                    -1,
                    False,
                    [],
                )
                # Clear all extmarks from the buffer
                try:
                    self.nvim.async_call(
                        self.nvim.api.buf_clear_namespace,
                        self.buffer_manager.content_buf,
                        -1,
                        0,
                        -1,
                    )
                except Exception:
                    pass
            # Clear conversation history
            self._conversation_history = []
        elif cmd == "/cancel":
            self.agent_cancel()
        elif cmd == "/file":
            self._handle_file_command()
        elif cmd == "/compact":
            self._handle_compact_command(parts[1:] if len(parts) > 1 else [])
        elif cmd == "/help":
            self.buffer_manager.append_content(
                [
                    "",
                    "## Help",
                    "",
                    "- `/clear`: Clear chat history",
                    "- `/cancel`: Cancel current request",
                    "- `/file`: Open file picker and add files to prompt",
                    "- `/compact [instructions]`: Compact conversation context",
                    "  Examples:",
                    "  - `/compact` - Compact with automatic settings",
                    "  - `/compact aggressively` - Heavy compaction",
                    "  - `/compact focus on authentication flow` - Focus on specific topic",
                    "  - `/compact --tokens=2000` - Target specific token count",
                    "- `/help`: Show this message",
                    "",
                ]
            )
        else:
            self.buffer_manager.append_content([f"Unknown command: {cmd}"])

    def _handle_file_command_inline(self, text, prompt_buf):
        """Handle /file command inline - replace /file with selected files in prompt."""
        try:
            # Remove /file from the text to get the rest of the prompt
            remaining_text = text.replace("/file", "").strip()

            # Get the buffer number while we have it
            prompt_buf_num = self.nvim.current.buffer.number

            self.nvim.exec_lua(
                """
                local args = {...}
                local remaining_prompt = args[1]
                local prompt_buf_num = args[2]

                -- Store for callback (capture in globals for async access)
                _G._agent_remaining_prompt = remaining_prompt
                _G._agent_prompt_buf_num = prompt_buf_num

                local function apply_files_to_prompt(files)
                    if not files or #files == 0 then
                        -- No files selected, restore prompt without /file
                        local prompt_buf = _G._agent_prompt_buf_num
                        if prompt_buf and vim.api.nvim_buf_is_valid(prompt_buf) then
                            local remaining = _G._agent_remaining_prompt or ''
                            if remaining ~= '' then
                                local lines = vim.split(remaining, '\\n', {plain = true})
                                vim.api.nvim_buf_set_lines(prompt_buf, 0, -1, false, lines)
                            else
                                vim.api.nvim_buf_set_lines(prompt_buf, 0, -1, false, {''})
                            end
                            local win = vim.fn.bufwinid(prompt_buf)
                            if win > 0 then
                                vim.api.nvim_set_current_win(win)
                            end
                        end
                        _G._agent_remaining_prompt = nil
                        _G._agent_prompt_buf_num = nil
                        return
                    end

                    local prompt_buf = _G._agent_prompt_buf_num
                    local remaining = _G._agent_remaining_prompt or ''

                    if not prompt_buf or not vim.api.nvim_buf_is_valid(prompt_buf) then
                        vim.notify('Prompt buffer is not valid', vim.log.levels.ERROR)
                        _G._agent_remaining_prompt = nil
                        _G._agent_prompt_buf_num = nil
                        return
                    end

                    -- Build file references
                    local file_refs = {}
                    for _, file in ipairs(files) do
                        table.insert(file_refs, '@' .. file)
                    end
                    local file_text = table.concat(file_refs, ' ')

                    -- Create new text: files first, then remaining prompt
                    local new_text
                    if remaining == '' then
                        new_text = file_text
                    else
                        new_text = file_text .. '\\n\\n' .. remaining
                    end

                    -- Set buffer content
                    local new_lines = vim.split(new_text, '\\n', {plain = true})
                    vim.api.nvim_buf_set_lines(prompt_buf, 0, -1, false, new_lines)

                    -- Set cursor to end and focus the window
                    local win = vim.fn.bufwinid(prompt_buf)
                    if win > 0 then
                        vim.api.nvim_set_current_win(win)
                        vim.api.nvim_win_set_cursor(win, {#new_lines, 0})
                    end

                    -- Clean up globals
                    _G._agent_remaining_prompt = nil
                    _G._agent_prompt_buf_num = nil
                end

                -- Open file picker from project root to search all files recursively
                local root = vim.fn.getcwd()
                -- Try to find git root for the project
                local git_root = vim.fn.systemlist('git rev-parse --show-toplevel 2>/dev/null')[1]
                if git_root and git_root ~= '' and vim.fn.isdirectory(git_root) == 1 then
                    root = git_root
                end

                Snacks.picker.files({
                    cwd = root,
                    hidden = false,
                    confirm = function(picker, item)
                        -- Get selected items, fallback to current item if none multi-selected
                        local items = picker:selected({fallback = true})
                        local files = {}
                        for _, selected_item in ipairs(items) do
                            local file_path = selected_item.file or selected_item.text
                            if file_path then
                                table.insert(files, file_path)
                            end
                        end
                        picker:close()
                        vim.schedule(function()
                            apply_files_to_prompt(files)
                        end)
                    end,
                })
            """,
                remaining_text,
                prompt_buf_num,
            )
        except Exception as e:
            self.logger.error(f"Error in /file command: {e}")
            self.nvim.out_write(f"Error opening file picker: {str(e)}\n")

    def _handle_user_prompt(self, text):
        """Handle user prompt submission."""
        # Reset tool tracker for new request
        tool_tracker.reset_tool_reads()

        # Cache cwd before async operations
        self._cached_cwd = self.nvim.call("getcwd")

        # Resolve mentions
        resolved_text = utils.resolve_mentions(
            text, lambda path: tools.read_file(path, self._cached_cwd)
        )

        # Get username from environment and titlecase it
        username = os.environ.get("USER", "User").title()

        # Reset autoscroll to enabled when new prompt is submitted
        if (
            hasattr(self.buffer_manager, "content_buf")
            and self.buffer_manager.content_buf
            and self.buffer_manager.content_buf.valid
        ):
            try:
                self.nvim.api.buf_set_var(
                    self.buffer_manager.content_buf, "agent_autoscroll_enabled", 1
                )
            except Exception:
                pass

        # Clear welcome message if this is the first message
        is_first_message = self.buffer_manager.clear_welcome_message()

        # Append user message (show original text to user)
        # No blank line on top for first message, add blank line for subsequent messages
        if is_first_message:
            self.buffer_manager.append_content([f"# {username}", "", text])
        else:
            self.buffer_manager.append_content(["", f"# {username}", "", text])

        # Add user message to conversation history
        self._conversation_history.append({"role": "user", "content": resolved_text})

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Reset cancellation flag and set current request ID
        self._cancel_requested = False
        self._current_request_id = request_id
        self._agent_busy = True

        # Run agent in background
        asyncio.create_task(self._run_agent_wrapper(request_id))

    async def _run_agent_wrapper(self, request_id, skip_header=False):
        """Wrapper to call run_agent with all necessary parameters."""
        import os

        # Get model for budget calculation
        model = os.environ.get("AGENT_MODEL", "gpt-5.1")

        # Create tool budget for this request
        tool_budget = ToolBudget(model=model)
        self.logger.info(
            f"Created tool budget: {tool_budget.budget} tokens for model {model}"
        )

        # Get tool wrappers with budget tracking
        tool_wrappers = self._get_tool_wrappers(tool_budget=tool_budget)

        # Create a reference object for current_request_id
        current_request_id_ref = {"value": self._current_request_id}

        try:
            await run_agent(
                request_id=request_id,
                nvim=self.nvim,
                buffer_manager=self.buffer_manager,
                logger=self.logger,
                conversation_history=self._conversation_history,
                cancel_flag_getter=lambda: self._cancel_requested,
                current_request_id_ref=current_request_id_ref,
                mcp_manager=self.mcp_manager,
                tool_wrappers=tool_wrappers,
                cached_cwd=self._cached_cwd,
                emit_event_fn=lambda name, data: utils.emit_user_event(
                    self.nvim, name, data
                ),
                skip_header=skip_header,
            )
        finally:
            pass

        # Log budget usage after request
        self.logger.info(f"Tool budget usage: {tool_budget.get_status()}")

        # Add any tool reads to conversation history for preservation
        if tool_tracker.add_tool_reads_to_history(self._conversation_history):
            self.logger.info("Added tool reads to conversation history")

        # Update the actual current_request_id after run_agent completes
        self._current_request_id = current_request_id_ref["value"]

        # Clear the busy flag when agent finishes
        self._agent_busy = False
