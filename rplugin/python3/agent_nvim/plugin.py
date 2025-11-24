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
    
    def _get_tool_wrappers(self):
        """Get tool wrappers, creating them if needed."""
        if self._tool_wrappers is None:
            try:
                from agents import function_tool
                
                # Create closures that capture self and cached_cwd
                def tool_read_file_wrapper(path: str) -> str:
                    cwd = getattr(self, "_cached_cwd", None)
                    return tools.read_file(path, cwd)
                
                def tool_list_files_wrapper(path: str = ".") -> str:
                    cwd = getattr(self, "_cached_cwd", None)
                    return tools.list_files(path, cwd)
                
                def tool_search_repo_wrapper(query: str) -> str:
                    cwd = getattr(self, "_cached_cwd", None)
                    return tools.search_repo(query, cwd)
                
                def tool_apply_patch_wrapper(patch_str: str) -> str:
                    return tools.apply_patch_proposal(
                        patch_str,
                        lambda p: self.nvim.async_call(self.buffer_manager.create_diff_buffer, p)
                    )
                
                self._tool_wrappers = {
                    'read_file': function_tool(tool_read_file_wrapper),
                    'list_files': function_tool(tool_list_files_wrapper),
                    'search_repo': function_tool(tool_search_repo_wrapper),
                    'apply_patch': function_tool(tool_apply_patch_wrapper),
                }
            except ImportError:
                self.logger.warning("Could not import function_tool from agents")
                self._tool_wrappers = {}
        
        return self._tool_wrappers
    
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
        self.nvim.async_call(self.buffer_manager.create_layout)
    
    @pynvim.command("AgentSubmit", sync=False)
    def agent_submit(self):
        """Submit the current prompt to the agent."""
        # Get content from prompt buffer
        prompt_buf = self.nvim.current.buffer
        lines = prompt_buf[:]
        text = "\\n".join(lines).strip()

        if not text:
            return

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
    
    @pynvim.function("AgentComplete", sync=True)
    def agent_complete(self, args):
        """Provide file path completions for @mentions."""
        findstart, base = args
        return self.buffer_manager.get_completions(findstart, base)
    
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
        elif cmd == "/help":
            self.buffer_manager.append_content(
                [
                    "",
                    "### Help",
                    "- `/clear`: Clear chat history",
                    "- `/cancel`: Cancel current request",
                    "- `/help`: Show this message",
                    "",
                ]
            )
        else:
            self.buffer_manager.append_content([f"Unknown command: {cmd}"])
    
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

        # Append user message (show original text to user)
        self.buffer_manager.append_content(["", f"## {username}", "", text])

        # Add user message to conversation history
        self._conversation_history.append({"role": "user", "content": resolved_text})

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Reset cancellation flag and set current request ID
        self._cancel_requested = False
        self._current_request_id = request_id

        # Run agent in background
        asyncio.create_task(self._run_agent_wrapper(resolved_text, request_id))
    
    async def _run_agent_wrapper(self, prompt, request_id):
        """Wrapper to call run_agent with all necessary parameters."""
        # Get tool wrappers
        tool_wrappers = self._get_tool_wrappers()
        
        # Create a reference object for current_request_id
        current_request_id_ref = {'value': self._current_request_id}
        
        await run_agent(
            prompt=prompt,
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
        
        # Update the actual current_request_id after run_agent completes
        self._current_request_id = current_request_id_ref['value']
