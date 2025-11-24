import pynvim
import sys
import os
import subprocess
import asyncio
import logging
import json
import uuid
from typing import List, Dict, Any, Optional

# Constants
PLUGIN_NAME = "agent.nvim"
VENV_DIR = os.path.expanduser(f"~/.local/share/{PLUGIN_NAME}/venv")

# Mock Agent/Runner if import fails (for development/fallback)
class MockAgent:
    def __init__(self, name, instructions, tools=None):
        self.name = name
        self.instructions = instructions
        self.tools = tools or []

class MockRunner:
    @staticmethod
    def run(agent, messages):
        # Mock response
        return MockResult(messages)

class MockResult:
    def __init__(self, messages):
        self.messages = messages
        self.final_response = "This is a mock response from the agent."


@pynvim.plugin
class AgentPlugin(object):
    def __init__(self, nvim):
        self.nvim = nvim
        self._setup_path()
        self.logger = logging.getLogger("agent_nvim")
        # Basic logging setup
        handler = logging.FileHandler(os.path.expanduser(f"~/.local/state/nvim/{PLUGIN_NAME}.log"))
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        # Stream buffer for accumulating text
        self._stream_buffer = ""
        # Conversation history
        self._conversation_history = []
        # Cancellation flag for stopping agent execution
        self._cancel_requested = False
        self._current_request_id = None

    def _setup_path(self):
        # Dependencies should already be available in the current Python environment
        # No need to manipulate sys.path
        pass

    @pynvim.command('AgentInstall', sync=False)
    def agent_install(self):
        self.nvim.out_write("Installing agent.nvim dependencies...\n")
        # Run installation in background
        asyncio.create_task(self._install_deps())

    async def _install_deps(self):
        try:
            # Install requirements to the current Python environment (user site-packages)
            plugin_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
            req_file = os.path.join(plugin_root, "requirements.txt")
            
            if not os.path.exists(req_file):
                 self.nvim.async_call(self.nvim.err_write, f"requirements.txt not found at {req_file}\n")
                 return

            self.nvim.async_call(self.nvim.out_write, "Installing requirements to current Python environment...\n")
            # Install to current Python environment (works with both venv and system Python)
            cmd = [sys.executable, "-m", "pip", "install", "-r", req_file]
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                self.nvim.async_call(self.nvim.out_write, "Agent dependencies installed successfully! Please restart Neovim.\n")
            else:
                self.nvim.async_call(self.nvim.err_write, f"Failed to install dependencies: {stderr.decode()}\n")
        except Exception as e:
            self.nvim.async_call(self.nvim.err_write, f"Exception during install: {str(e)}\n")

    @pynvim.command('AgentTestImport', sync=False)
    def agent_test_import(self):
        try:
            import openai
            import agents
            self.nvim.out_write("Success: 'openai' and 'agents' modules imported.\n")
            self.nvim.out_write(f"agents contents: {dir(agents)}\n")
        except ImportError as e:
            self.nvim.err_write(f"Error: Could not import modules. {e}\n")

    @pynvim.command('AgentOpen', sync=False)
    def agent_open(self):
        self.nvim.async_call(self._create_layout)

    def _create_layout(self):
        # Check if buffers already exist
        # We'll use buffer variables to track them
        content_buf = None
        prompt_buf = None

        # Try to find existing buffers
        for buf in self.nvim.buffers:
            if buf.name.endswith("AgentContent"):
                content_buf = buf
            elif buf.name.endswith("AgentPrompt"):
                prompt_buf = buf

        # Create content buffer if needed
        if not content_buf or not content_buf.valid:
            content_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(content_buf, "AgentContent")
            self.nvim.api.buf_set_option(content_buf, 'filetype', 'agent-content')
            self.nvim.api.buf_set_option(content_buf, 'buftype', 'nofile')
            self.nvim.api.buf_set_option(content_buf, 'swapfile', False)
            # Set buffer variable to identify this as agent content
            self.nvim.api.buf_set_var(content_buf, 'agent_buffer', 'content')

        # Create prompt buffer if needed
        if not prompt_buf or not prompt_buf.valid:
            prompt_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(prompt_buf, "AgentPrompt")
            self.nvim.api.buf_set_option(prompt_buf, 'filetype', 'agent-prompt')
            self.nvim.api.buf_set_option(prompt_buf, 'buftype', 'nofile')
            self.nvim.api.buf_set_option(prompt_buf, 'swapfile', False)

        # Create split layout
        # Use current buffer instead of new tab
        self.nvim.command('enew')
        
        # Set content buffer to current window
        content_win = self.nvim.api.get_current_win()
        self.nvim.api.win_set_buf(content_win, content_buf)
        # Set wrap for content window
        self.nvim.api.win_set_option(content_win, 'wrap', True)
        self.nvim.api.win_set_option(content_win, 'linebreak', True)
        
        # Create split for prompt
        self.nvim.command('botright split')
        self.nvim.command('resize 5')
        self.nvim.api.win_set_buf(0, prompt_buf)
        
        # Store buffer handles
        self.content_buf = content_buf
        self.prompt_buf = prompt_buf

        # Add welcome message if empty
        if len(content_buf) <= 1:
            welcome = [
                "```",
                "    _    ____ _____ _   _ _____   _   ___     _____ __  __",
                "   / \\  / ___| ____| \\ | |_   _| | \ | \\ \\   / /_ _|  \\/  |",
                "  / _ \\| |  _|  _| |  \\| | | |   |  \\| |\\ \\ / / | || |\\/| |",
                " / ___ \\ |_| | |___| |\\  | | | _ | |\\  | \\ V /  | || |  | |",
                "/_/   \\_\\____|_____|_| \\_| |_|(_)|_| \\_|  \\_/  |___|_|  |_|",
                "```",
                "",
                "Type your request in the prompt below.",
                ""
            ]
            self.nvim.api.buf_set_lines(content_buf, 0, -1, False, welcome)
            
        # Enable render-markdown for the content buffer
        self._enable_render_markdown()

    @pynvim.command('AgentSubmit', sync=False)
    def agent_submit(self):
        # Get content from prompt buffer
        prompt_buf = self.nvim.current.buffer
        lines = prompt_buf[:]
        text = "\n".join(lines).strip()
        
        if not text:
            return

        # Clear prompt buffer
        self.nvim.api.buf_set_lines(prompt_buf, 0, -1, False, [""])

        # Handle slash commands
        if text.startswith("/"):
            self._handle_slash_command(text)
        else:
            self._handle_user_prompt(text)

    @pynvim.command('AgentCancel', sync=False)
    def agent_cancel(self):
        """Cancels the currently running agent request."""
        if self._current_request_id and not self._cancel_requested:
            self._cancel_requested = True
            self.nvim.async_call(self._append_content, ["\n**[Request cancelled by user]**\n"])
        elif not self._current_request_id:
            self.nvim.out_write("No active agent request to cancel.\n")

    def _handle_slash_command(self, text):
        cmd = text.split()[0]
        if cmd == "/clear":
            if hasattr(self, 'content_buf') and self.content_buf.valid:
                self.nvim.async_call(self.nvim.api.buf_set_lines, self.content_buf, 0, -1, False, [])
            # Clear conversation history
            self._conversation_history = []
        elif cmd == "/cancel":
            self.agent_cancel()
        elif cmd == "/help":
            self._append_content(["", "### Help", "- `/clear`: Clear chat history", "- `/cancel`: Cancel current request", "- `/help`: Show this message", ""])
        else:
            self._append_content([f"Unknown command: {cmd}"])

    def _handle_user_prompt(self, text):
        # Cache cwd before async operations (nvim.call doesn't work in async)
        self._cached_cwd = self.nvim.call('getcwd')
        
        # Resolve mentions
        resolved_text = self._resolve_mentions(text)
        
        # Get username from environment and titlecase it
        username = os.environ.get('USER', 'User').title()
        
        # Append user message (show original text to user, but send resolved to agent?)
        # For transparency, let's show what we are sending if it's different, or just the user text.
        # Let's show the user text.
        self._append_content(["", f"## {username}", "", text])
        
        # Add user message to conversation history
        self._conversation_history.append({"role": "user", "content": resolved_text})
        
        # Generate unique request ID for fidget tracking
        request_id = str(uuid.uuid4())
        
        # Reset cancellation flag and set current request ID
        self._cancel_requested = False
        self._current_request_id = request_id
        
        # Run agent in background
        asyncio.create_task(self._run_agent(resolved_text, request_id))

    def _resolve_mentions(self, text: str) -> str:
        """Replaces @file mentions with file content."""
        import re
        
        def replace_match(match):
            path = match.group(1)
            content = self._tool_read_file(path)
            if content.startswith("Error"):
                return f"[Error reading {path}: {content}]"
            return f"\n--- Start of {path} ---\n{content}\n--- End of {path} ---\n"

        # Match @path/to/file or @filename
        # Simple regex: @ followed by non-whitespace characters
        # We might want to be more specific, e.g., @[filepath] or just @filepath
        # Let's stick to @filepath for now, stopping at whitespace.
        return re.sub(r'@([\w./-]+)', replace_match, text)

    def _load_project_instructions(self) -> str:
        """Loads project-specific instructions from AGENTS.md or .agent/instructions.md."""
        candidates = ["AGENTS.md", ".agent/instructions.md"]
        cwd = getattr(self, '_cached_cwd', os.getcwd())
        
        for cand in candidates:
            path = os.path.join(cwd, cand)
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        return f.read()
                except Exception:
                    pass
        return ""

    async def _run_agent(self, prompt, request_id=None):
        request_id = request_id or str(uuid.uuid4())
        model = os.environ.get('AGENT_MODEL', 'gpt-4o')
        
        # Emit fidget start event
        self._emit_user_event('AgentRequestStarted', {
            'id': request_id,
            'model': model
        })
        
        status = 'error'  # Default to error, will be set to success if completion succeeds
        
        try:
            # Import from current Python environment
            try:
                from agents import Agent, Runner, function_tool, set_default_openai_client, set_default_openai_api, set_tracing_disabled
                from openai import AsyncOpenAI
            except ImportError as e:
                # Debug: show sys.path and error
                debug_msg = f"ImportError: {e}\nPython: {sys.executable}\nsys.path: {sys.path}"
                self.nvim.async_call(self._append_content, ["Error: agents not installed. Run :AgentInstall.", debug_msg])
                self._emit_user_event('AgentRequestFinished', {'id': request_id, 'status': 'error'})
                return

            # Configure custom OpenAI client if base URL or API key provided
            base_url = os.environ.get('AGENT_BASE_URL')
            api_key = os.environ.get('AGENT_API_KEY') or os.environ.get('OPENAI_API_KEY')
            
            if base_url or api_key:
                client_kwargs = {}
                if base_url:
                    client_kwargs['base_url'] = base_url
                if api_key:
                    client_kwargs['api_key'] = api_key
                
                # Create custom client and set it as default
                custom_client = AsyncOpenAI(**client_kwargs)
                set_default_openai_client(custom_client, use_for_tracing=False)
                
                # Allow choosing API type via environment variable
                # Options: 'responses' (default) or 'chat_completions'
                api_type = os.environ.get('AGENT_API_TYPE', 'responses')
                set_default_openai_api(api_type)
                
                # Disable tracing for custom providers by default
                if os.environ.get('AGENT_DISABLE_TRACING', '1') == '1':
                    set_tracing_disabled(True)
            
            # Model already set at the start of this function
            # model = os.environ.get('AGENT_MODEL')

            # Load instructions
            base_instructions = "You are a helpful AI assistant embedded in Neovim. You can read files, list files, search the repository, and propose patches."
            project_instructions = self._load_project_instructions()
            full_instructions = base_instructions
            if project_instructions:
                full_instructions += "\n\nProject Instructions:\n" + project_instructions

            # Create tools - wrap instance methods with function_tool
            read_file_tool = function_tool(self._tool_read_file)
            list_files_tool = function_tool(self._tool_list_files)
            search_repo_tool = function_tool(self._tool_search_repo)
            apply_patch_tool = function_tool(self._tool_apply_patch)

            # Initialize Agent with optional model
            agent_kwargs = {
                "name": "Neovim Agent",
                "instructions": full_instructions,
                "tools": [read_file_tool, list_files_tool, search_repo_tool, apply_patch_tool]
            }
            if model:
                agent_kwargs['model'] = model
            
            agent = Agent(**agent_kwargs)
            
            # Display agent header with model name
            display_model = model if model else "gpt-4o"
            self.nvim.async_call(self._append_content, ["", f"## Agent ({display_model})", ""])

            # Build input from conversation history
            # The history already includes the current prompt (added in _handle_user_prompt)
            # Convert to input list format expected by the SDK
            input_messages = self._conversation_history.copy()

            # Run the agent with streaming and conversation history
            # Pass the message list as 'input' parameter
            result_stream = Runner.run_streamed(agent, input=input_messages)
            
            # Cache buffer number before async loop (can't access nvim API from async context)
            content_bufnr = self.content_buf.handle if hasattr(self.content_buf, 'handle') else self.content_buf.number
            
            async for event in result_stream.stream_events():
                # Check for cancellation
                if self._cancel_requested:
                    self.logger.info(f"Agent request {request_id} cancelled by user")
                    status = 'cancelled'
                    break
                
                event_type = type(event).__name__
                
                # Handle different event types for different APIs
                if event_type == 'RawResponsesStreamEvent':
                    data = event.data
                    data_type = type(data).__name__
                    
                    # Only process actual output text, not reasoning/thinking
                    if data_type == 'ResponseTextDeltaEvent':
                        delta = data.delta
                        if delta:
                            # Just send the delta directly - let vim.schedule handle it
                            self._append_stream_lua_direct(delta, content_bufnr)
                    # Skip ResponseReasoningSummaryTextDeltaEvent - that's internal thinking
                    
                elif event_type == 'RawChatCompletionsStreamEvent':
                    # Handle chat completions API events
                    data = event.data
                    data_type = type(data).__name__
                    
                    if data_type == 'ChatCompletionsTextDeltaEvent':
                        delta = data.delta
                        if delta:
                            self._append_stream_lua_direct(delta, content_bufnr)
                
                if result_stream.is_complete:
                    break
            
            # Get the final output and add it to conversation history
            # After the stream completes, RunResultStreaming has final_output attribute
            if hasattr(result_stream, 'final_output') and result_stream.final_output:
                self._conversation_history.append({
                    "role": "assistant",
                    "content": str(result_stream.final_output)
                })
            
            # Agent completed successfully (unless cancelled)
            if not self._cancel_requested:
                status = 'success'

        except Exception as e:
            import traceback
            self.logger.error(f"Agent run failed: {e}\n{traceback.format_exc()}")
            self.nvim.async_call(self._append_content, [f"\nError: {str(e)}"])
            status = 'error'
        finally:
            # Clear current request ID
            if self._current_request_id == request_id:
                self._current_request_id = None
                self._cancel_requested = False
            
            # Emit fidget finish event
            self._emit_user_event('AgentRequestFinished', {
                'id': request_id,
                'status': status
            })

    # --- Tools ---

    def _tool_read_file(self, path: str) -> str:
        """Reads the content of a file."""
        try:
            if not os.path.isabs(path):
                # Try to resolve relative to cached cwd
                cwd = getattr(self, '_cached_cwd', os.getcwd())
                path = os.path.join(cwd, path)
            
            if not os.path.exists(path):
                return f"Error: File {path} does not exist."
                
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {e}"

    def _tool_list_files(self, path: str = ".") -> str:
        """Lists files in a directory (recursive, respects gitignore if possible)."""
        try:
            cwd = getattr(self, '_cached_cwd', os.getcwd())
            target_dir = os.path.join(cwd, path)
            
            # Use fd or find if available, else os.walk
            # For simplicity, let's use os.walk but limit depth/count
            files = []
            for root, _, filenames in os.walk(target_dir):
                if '.git' in root:
                    continue
                for filename in filenames:
                    rel_path = os.path.relpath(os.path.join(root, filename), cwd)
                    files.append(rel_path)
                    if len(files) > 100:
                        return "\n".join(files) + "\n... (truncated)"
            return "\n".join(files)
        except Exception as e:
            return f"Error listing files: {e}"

    def _tool_search_repo(self, query: str) -> str:
        """Searches the repository for a string using grep/ripgrep."""
        try:
            cwd = getattr(self, '_cached_cwd', os.getcwd())
            # Try ripgrep first
            cmd = ["rg", "--line-number", "--no-heading", "--smart-case", query, cwd]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    return result.stdout[:2000] # Limit output
            except FileNotFoundError:
                # Fallback to grep
                cmd = ["grep", "-rn", query, cwd]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    return result.stdout[:2000]
            
            return "No matches found."
        except Exception as e:
            return f"Error searching repo: {e}"

    def _tool_apply_patch(self, patch_str: str) -> str:
        """Proposes a patch to be applied. Creates a diff buffer for review."""
        try:
            self.nvim.async_call(self._create_diff_buffer, patch_str)
            return "Patch proposed. Please review the 'AgentDiff' buffer and run :AgentApply to apply it."
        except Exception as e:
            return f"Error proposing patch: {e}"

    def _create_diff_buffer(self, patch_str):
        # Create or reuse AgentDiff buffer
        diff_buf = None
        for buf in self.nvim.buffers:
            if buf.name.endswith("AgentDiff"):
                diff_buf = buf
                break
        
        if not diff_buf or not diff_buf.valid:
            diff_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(diff_buf, "AgentDiff")
            self.nvim.api.buf_set_option(diff_buf, 'filetype', 'diff')
            self.nvim.api.buf_set_option(diff_buf, 'buftype', 'nofile')
            self.nvim.api.buf_set_option(diff_buf, 'swapfile', False)

        # Set content
        lines = patch_str.split('\n')
        self.nvim.api.buf_set_lines(diff_buf, 0, -1, False, lines)
        
        # Open in a split if not visible
        win_found = False
        for win in self.nvim.windows:
            if win.buffer == diff_buf:
                win_found = True
                break
        
        if not win_found:
            self.nvim.command('vsplit')
            self.nvim.api.win_set_buf(0, diff_buf)
            self.nvim.out_write("Patch proposed in AgentDiff buffer.\n")

    @pynvim.command('AgentApply', sync=False)
    def agent_apply(self):
        """Applies the patch in the AgentDiff buffer."""
        # Find AgentDiff buffer
        diff_buf = None
        for buf in self.nvim.buffers:
            if buf.name.endswith("AgentDiff"):
                diff_buf = buf
                break
        
        if not diff_buf or not diff_buf.valid:
            self.nvim.err_write("No AgentDiff buffer found.\n")
            return

        # Get content
        lines = diff_buf[:]
        patch_content = "\n".join(lines)
        
        if not patch_content.strip():
            self.nvim.err_write("AgentDiff buffer is empty.\n")
            return

        # Apply patch
        # We'll use 'git apply' or 'patch' command
        # First write to a temp file
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
                tmp.write(patch_content)
                tmp_path = tmp.name
            
            cwd = self.nvim.call('getcwd')
            cmd = ["git", "apply", "--ignore-space-change", "--ignore-whitespace", tmp_path]
            
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            
            if proc.returncode == 0:
                self.nvim.out_write("Patch applied successfully!\n")
                # Close diff buffer/window? Maybe keep it for reference.
                self.nvim.command('checktime') # Reload buffers
            else:
                self.nvim.err_write(f"Failed to apply patch: {proc.stderr}\n")
                
            os.remove(tmp_path)
        except Exception as e:
            self.nvim.err_write(f"Exception applying patch: {e}\n")

    @pynvim.function('AgentComplete', sync=True)
    def agent_complete(self, args):
        findstart, base = args
        
        if findstart == 1:
            # Find start of the word to complete
            # We want to complete after '@'
            line = self.nvim.current.line
            col = self.nvim.current.window.cursor[1]
            
            # Search backwards for '@'
            start = -1
            for i in range(col - 1, -1, -1):
                if line[i] == '@':
                    start = i
                    break
                if line[i] == ' ': # Stop at space
                    break
            
            if start != -1:
                return start + 1 # Return index after '@'
            return -1
        else:
            # Return list of matches
            # base is the string after '@'
            try:
                cwd = self.nvim.call('getcwd')
                matches = []
                
                # Simple recursive search
                for root, _, filenames in os.walk(cwd):
                    if '.git' in root:
                        continue
                    for filename in filenames:
                        rel_path = os.path.relpath(os.path.join(root, filename), cwd)
                        if rel_path.startswith(base):
                            matches.append(rel_path)
                            if len(matches) > 50: # Limit results
                                break
                    if len(matches) > 50:
                        break
                
                return matches
            except Exception:
                return []

    
    def _append_stream_lua_direct(self, text, bufnr):
        """Append text using Lua animation for smooth typing effect.
        
        Args:
            text: Text to append
            bufnr: Buffer number (must be passed in, can't access from async context)
        """
        # Escape text for Lua string
        escaped_text = text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace("'", "\\'") 
        
        # Use a timer to animate character-by-character
        lua_code = f'''
        local bufnr = {bufnr}
        local text = "{escaped_text}"
        
        -- Initialize animation queue if it doesn't exist
        if not _G.agent_stream_queue then
            _G.agent_stream_queue = {{}}
            _G.agent_stream_timer = nil
        end
        
        -- Add text to queue
        table.insert(_G.agent_stream_queue, {{bufnr = bufnr, text = text}})
        
        -- Start timer if not already running
        if not _G.agent_stream_timer then
            _G.agent_stream_timer = vim.loop.new_timer()
            _G.agent_stream_timer:start(0, 15, vim.schedule_wrap(function()
                if #_G.agent_stream_queue == 0 then
                    _G.agent_stream_timer:stop()
                    _G.agent_stream_timer = nil
                    return
                end
                
                local item = _G.agent_stream_queue[1]
                if not vim.api.nvim_buf_is_valid(item.bufnr) then
                    table.remove(_G.agent_stream_queue, 1)
                    return
                end
                
                -- Take 3 characters at a time for smooth but not too slow animation
                local chars_to_write = 3
                local chunk = item.text:sub(1, chars_to_write)
                item.text = item.text:sub(chars_to_write + 1)
                
                if chunk ~= "" then
                    local line_count = vim.api.nvim_buf_line_count(item.bufnr)
                    local last_line_idx = line_count - 1
                    local last_line = vim.api.nvim_buf_get_lines(item.bufnr, last_line_idx, last_line_idx + 1, false)
                    local last_column = #(last_line[1] or "")
                    
                    local lines = vim.split(chunk, "\\n", {{plain = true}})
                    vim.api.nvim_buf_set_text(item.bufnr, last_line_idx, last_column, last_line_idx, last_column, lines)
                    
                    -- Autoscroll
                    for _, win in ipairs(vim.api.nvim_list_wins()) do
                        if vim.api.nvim_win_get_buf(win) == item.bufnr then
                            local new_line_count = vim.api.nvim_buf_line_count(item.bufnr)
                            pcall(vim.api.nvim_win_set_cursor, win, {{new_line_count, 0}})
                        end
                    end
                end
                
                -- Remove item if all text written
                if item.text == "" then
                    table.remove(_G.agent_stream_queue, 1)
                end
            end))
        end
        '''
        
        try:
            self.nvim.exec_lua(lua_code)
        except Exception as e:
            self.logger.error(f"Error in _append_stream_lua: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
    
    def _append_stream(self, text):
        """Append text to the content buffer using nvim_buf_set_text.
        
        This method is called through nvim.async_call, so all nvim API calls here are safe.
        """
        if not hasattr(self, 'content_buf') or not self.content_buf.valid:
            return
            
        try:
            # Get the last line and column
            line_count = self.nvim.api.buf_line_count(self.content_buf)
            last_line_idx = line_count - 1  # 0-indexed
            
            # Get the last line content to find the column
            last_line_content = self.nvim.api.buf_get_lines(self.content_buf, last_line_idx, last_line_idx + 1, False)
            if not last_line_content:
                last_column = 0
            else:
                last_column = len(last_line_content[0])
            
            # Split text into lines
            lines = text.split('\n')
            
            # Use buf_set_text to insert at the current position
            # This API inserts text at (line, col) without replacing the whole buffer
            self.nvim.api.buf_set_text(self.content_buf, last_line_idx, last_column, last_line_idx, last_column, lines)
            
            # Autoscroll to bottom
            for win in self.nvim.windows:
                if win.buffer == self.content_buf:
                    try:
                        new_line_count = self.nvim.api.buf_line_count(self.content_buf)
                        win.cursor = (new_line_count, 0)
                    except Exception:
                        pass
        except Exception as e:
            self.logger.error(f"Error in _append_stream: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
    

    def _remove_last_line(self):
        """Remove the last line from the content buffer."""
        if hasattr(self, 'content_buf') and self.content_buf.valid:
            line_count = len(self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False))
            if line_count > 0:
                self.nvim.api.buf_set_lines(self.content_buf, -2, -1, False, [])

    def _enable_render_markdown(self):
        """Enable render-markdown for the content buffer."""
        try:
            # Try to enable render-markdown if it's available
            self.nvim.command('silent! RenderMarkdown enable')
        except Exception:
            pass
    
    def _append_content(self, lines):
        """Append one or more lines to the content buffer.

        Neovim requires each list element to be a single line, so any
        string that contains newline characters is split into separate
        entries before writing.
        """
        if hasattr(self, 'content_buf') and self.content_buf.valid:
            # Ensure every item is a single line
            processed = []
            for item in lines:
                if isinstance(item, str) and '\n' in item:
                    # Split on newlines, keep non‑empty parts
                    processed.extend([ln for ln in item.split('\n') if ln != ''])
                else:
                    processed.append(item)
            # Write the processed list to the buffer
            self.nvim.async_call(
                lambda: self._append_and_scroll(processed)
            )
            # Enable render-markdown after content is added
            self.nvim.async_call(self._enable_render_markdown)
    
    def _append_and_scroll(self, processed):
        """Helper to append lines and autoscroll content buffer."""
        if not hasattr(self, 'content_buf') or not self.content_buf.valid:
            return
        
        # Append lines
        self.nvim.api.buf_set_lines(
            self.content_buf, -1, -1, False, processed
        )
        
        # Autoscroll to bottom
        for win in self.nvim.windows:
            if win.buffer == self.content_buf:
                line_count = len(self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False))
                win.cursor = (line_count, 0)
    
    def _emit_user_event(self, event_name, data):
        """Emit a User autocommand event with data for fidget integration."""
        try:
            # Serialize data to JSON
            data_json = json.dumps(data)
            # Escape single quotes for Vim command
            data_json_escaped = data_json.replace("'", "''")
            # Execute doautocmd with data
            self.nvim.async_call(
                lambda: self.nvim.exec_lua(
                    f"vim.api.nvim_exec_autocmds('User', {{pattern = '{event_name}', data = vim.fn.json_decode('{data_json_escaped}')}})"
                )
            )
        except Exception as e:
            self.logger.error(f"Error emitting user event {event_name}: {e}")

