"""Main plugin class for agent.nvim."""

import pynvim
import os
import logging
import asyncio
import uuid

from . import installation, tools, utils
from .buffers import BufferManager
from .mcp import MCPManager
from .agent_runner import run_agent
from .tool_budget import ToolBudget

# Constants
PLUGIN_NAME = "agent.nvim"


@pynvim.plugin
class AgentPlugin(object):
    """Main plugin class for agent.nvim."""
    
    def __init__(self, nvim):
        self.nvim = nvim
        self._setup_logging()
        
        # Initialize managers
        self.buffer_manager = BufferManager(nvim, self.logger)
        self.mcp_manager = MCPManager(self.logger)
        
        # Conversation state
        self._conversation_history = []
        self._cancel_requested = False
        self._current_request_id = None
        self._cached_cwd = None
        
        # Tool wrappers will be created lazily
        self._tool_wrappers = None
        
        # Initialize Lua module (history will be initialized by ftplugin when buffer opens)
        try:
            nvim.exec_lua("require('agent_nvim')")
        except Exception as e:
            self.logger.debug(f"Failed to initialize Lua module: {e}")
    
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
    
    def _get_tool_wrappers(self, tool_budget: ToolBudget | None = None):
        """Get tool wrappers, creating them with optional budget tracking.
        
        Args:
            tool_budget: Optional ToolBudget instance for tracking token usage
        """
        try:
            from agents import function_tool
            
            # Create closures that capture self, cached_cwd, and tool_budget
            # Budget tracking is done inline to preserve function signatures for the SDK
            def read_file(path: str) -> str:
                """Read file content."""
                # Check budget before reading (heavy tool)
                if tool_budget and not tool_budget.can_use_budget(heavy_tool=True):
                    return tool_budget.get_budget_exceeded_message()
                
                cwd = getattr(self, "_cached_cwd", None)
                result = tools.read_file(path, cwd)
                
                # Track token usage
                if tool_budget and isinstance(result, str):
                    tool_budget.consume(result)
                return result
            
            def list_files(path: str = ".") -> str:
                """List files in directory."""
                cwd = getattr(self, "_cached_cwd", None)
                result = tools.list_files(path, cwd)
                
                # Track token usage (light tool, no budget check)
                if tool_budget and isinstance(result, str):
                    tool_budget.consume(result)
                return result
            
            def search_repo(query: str) -> str:
                """Search repository."""
                # Check budget before searching (heavy tool)
                if tool_budget and not tool_budget.can_use_budget(heavy_tool=True):
                    return tool_budget.get_budget_exceeded_message()
                
                cwd = getattr(self, "_cached_cwd", None)
                result = tools.search_repo(query, cwd)
                
                # Track token usage
                if tool_budget and isinstance(result, str):
                    tool_budget.consume(result)
                return result
            
            def apply_patch(patch_str: str) -> str:
                """Apply patch proposal."""
                return tools.apply_patch_proposal(
                    patch_str,
                    lambda p: self.nvim.async_call(self.buffer_manager.create_diff_buffer, p)
                )
            
            async def execute_lua(code: str) -> str:
                """Execute Lua code inside Neovim."""
                return await tools.execute_lua(code, nvim=self.nvim, logger=self.logger)
            
            return {
                'read_file': function_tool(read_file),
                'list_files': function_tool(list_files),
                'search_repo': function_tool(search_repo),
                'apply_patch': function_tool(apply_patch),
                'execute_lua': function_tool(execute_lua),
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
        from .token_tracker import reset_session_tokens
        reset_session_tokens()
        self.nvim.async_call(self.buffer_manager.create_layout)
    
    @pynvim.command("AgentSubmit", sync=False)
    def agent_submit(self):
        """Submit the current prompt to the agent."""
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
            result = self.nvim.exec_lua("""
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
            """, text)
            
            if not result:
                self.logger.warning(f"Failed to save prompt to history")
                
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
        """Cancel the currently running agent request."""
        if self._current_request_id and not self._cancel_requested:
            self._cancel_requested = True
            # Only add spacing if there's already content in the response
            self.nvim.async_call(self.buffer_manager.append_cancel_message)
        elif not self._current_request_id:
            self.nvim.out_write("No active agent request to cancel.\\n")
    
    @pynvim.command("AgentApply", sync=False)
    def agent_apply(self):
        """Apply the patch in the AgentDiff buffer."""
        self.buffer_manager.apply_patch()
    
    @pynvim.command("AgentHistoryTest", sync=True)
    def agent_history_test(self):
        """Test and diagnose the history system."""
        result = self.nvim.exec_lua("""
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
        """, [])
        
        self.nvim.out_write(f"History Test Results:\n")
        self.nvim.out_write(f"Status: {result['status']}\n")
        self.nvim.out_write(f"Test Message: {result['test_message']}\n")
        self.nvim.out_write(f"Save Result: {result['save_result']}\n")
        self.nvim.out_write(f"History File: {result['diagnostic']['path']}\n")
        self.nvim.out_write(f"Entry Count: {result['diagnostic']['entry_count']}\n")
        
        if result['diagnostic']['issues'] and len(result['diagnostic']['issues']) > 0:
            self.nvim.out_write(f"Issues:\n")
            for issue in result['diagnostic']['issues']:
                self.nvim.out_write(f"  - {issue}\n")
        else:
            self.nvim.out_write(f"No issues found\n")
    
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
    
    def _handle_slash_command(self, text):
        """Handle slash commands like /clear, /cancel, /help."""
        cmd = text.split()[0]
        if cmd == "/clear":
            if hasattr(self.buffer_manager, "content_buf") and self.buffer_manager.content_buf and self.buffer_manager.content_buf.valid:
                self.nvim.async_call(
                    self.nvim.api.buf_set_lines, self.buffer_manager.content_buf, 0, -1, False, []
                )
            # Clear conversation history
            self._conversation_history = []
        elif cmd == "/cancel":
            self.agent_cancel()
        elif cmd == "/file":
            self._handle_file_command()
        elif cmd == "/help":
            self.buffer_manager.append_content(
                [
                    "",
                    "## Help",
                    "- `/clear`: Clear chat history",
                    "- `/cancel`: Cancel current request",
                    "- `/file`: Open file picker and add files to prompt",
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
            
            self.nvim.exec_lua("""
                local args = {...}
                local remaining_prompt = args[1]
                local prompt_buf_num = args[2]
                
                -- Store for callback
                _G._agent_remaining_prompt = remaining_prompt
                _G._agent_prompt_buf_num = prompt_buf_num
                
                local function apply_files_to_prompt(files)
                    if not files or #files == 0 then
                        return
                    end
                    
                    local prompt_buf = _G._agent_prompt_buf_num
                    if not vim.api.nvim_buf_is_valid(prompt_buf) then
                        vim.notify('Prompt buffer is not valid', vim.log.levels.ERROR)
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
                    if remaining_prompt == '' or remaining_prompt == nil then
                        new_text = file_text
                    else
                        new_text = file_text .. '\\n\\n' .. remaining_prompt
                    end
                    
                    -- Set buffer content
                    local new_lines = vim.split(new_text, '\\n', {plain = true})
                    vim.api.nvim_buf_set_lines(prompt_buf, 0, -1, false, new_lines)
                    
                    -- Set cursor to end
                    local win = vim.fn.bufwinid(prompt_buf)
                    if win > 0 then
                        vim.api.nvim_win_set_cursor(win, {#new_lines, 0})
                    end
                    
                    -- Clean up globals
                    _G._agent_remaining_prompt = nil
                    _G._agent_prompt_buf_num = nil
                end
                
                -- Open file picker
                Snacks.picker.files({
                    actions = {
                        confirm = function(picker, item)
                            local items = picker:selected({fallback = false})
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
                        end
                    }
                })
            """, remaining_text, prompt_buf_num)
        except Exception as e:
            self.logger.error(f"Error in /file command: {e}")
            self.nvim.out_write(f"Error opening file picker: {str(e)}\n")
    
    def _handle_user_prompt(self, text):
        """Handle user prompt submission."""
        # Cache cwd before async operations
        self._cached_cwd = self.nvim.call("getcwd")

        # Resolve mentions
        resolved_text = utils.resolve_mentions(
            text,
            lambda path: tools.read_file(path, self._cached_cwd)
        )

        # Get username from environment and titlecase it
        username = os.environ.get("USER", "User").title()

        # Reset autoscroll to enabled when new prompt is submitted
        if hasattr(self.buffer_manager, "content_buf") and self.buffer_manager.content_buf and self.buffer_manager.content_buf.valid:
            try:
                self.nvim.api.buf_set_var(self.buffer_manager.content_buf, "agent_autoscroll_enabled", 1)
            except Exception:
                pass

        # Append user message (show original text to user)
        self.buffer_manager.append_content(["", f"# {username}", "", text])

        # Add user message to conversation history
        self._conversation_history.append({"role": "user", "content": resolved_text})

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Reset cancellation flag and set current request ID
        self._cancel_requested = False
        self._current_request_id = request_id

        # Run agent in background
        asyncio.create_task(self._run_agent_wrapper(request_id))
    
    async def _run_agent_wrapper(self, request_id):
        """Wrapper to call run_agent with all necessary parameters."""
        import os
        
        # Get model for budget calculation
        model = os.environ.get("AGENT_MODEL", "gpt-5.1")
        
        # Create tool budget for this request
        tool_budget = ToolBudget(model=model)
        self.logger.info(f"Created tool budget: {tool_budget.budget} tokens for model {model}")
        
        # Get tool wrappers with budget tracking
        tool_wrappers = self._get_tool_wrappers(tool_budget=tool_budget)
        
        # Create a reference object for current_request_id
        current_request_id_ref = {'value': self._current_request_id}
        
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
            emit_event_fn=lambda name, data: utils.emit_user_event(self.nvim, name, data)
        )
        
        # Log budget usage after request
        self.logger.info(f"Tool budget usage: {tool_budget.get_status()}")
        
        # Update the actual current_request_id after run_agent completes
        self._current_request_id = current_request_id_ref['value']
