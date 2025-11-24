import pynvim
import sys
import os
import subprocess
import asyncio
import logging
import json
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
            self.nvim.api.buf_set_option(content_buf, 'wrap', True)
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
        # Clear current tabpage
        self.nvim.command('tabnew')
        
        # Set content buffer to current window
        self.nvim.api.win_set_buf(0, content_buf)
        
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

    def _handle_slash_command(self, text):
        cmd = text.split()[0]
        if cmd == "/clear":
            if hasattr(self, 'content_buf') and self.content_buf.valid:
                self.nvim.async_call(self.nvim.api.buf_set_lines, self.content_buf, 0, -1, False, [])
        elif cmd == "/help":
            self._append_content(["", "### Help", "- `/clear`: Clear chat history", "- `/help`: Show this message", ""])
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
        
        # Run agent in background
        asyncio.create_task(self._run_agent(resolved_text))

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

    async def _run_agent(self, prompt):
        try:
            # Import from current Python environment
            try:
                from agents import Agent, Runner, function_tool
                import openai
            except ImportError as e:
                # Debug: show sys.path and error
                debug_msg = f"ImportError: {e}\nPython: {sys.executable}\nsys.path: {sys.path}"
                self.nvim.async_call(self._append_content, ["Error: agents not installed. Run :AgentInstall.", debug_msg])
                return

            # Configure OpenAI with custom base URL and API key if provided
            client_kwargs = {}
            base_url = os.environ.get('AGENT_BASE_URL')
            if base_url:
                client_kwargs['base_url'] = base_url
            
            api_key = os.environ.get('AGENT_API_KEY') or os.environ.get('OPENAI_API_KEY')
            if api_key:
                client_kwargs['api_key'] = api_key
            
            # Create client if we have custom configuration
            client = openai.OpenAI(**client_kwargs) if client_kwargs else openai.OpenAI()
            
            # Get model from environment or use default
            model = os.environ.get('AGENT_MODEL')

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

            # Initialize Agent with optional model and client
            agent_kwargs = {
                "name": "Neovim Agent",
                "instructions": full_instructions,
                "tools": [read_file_tool, list_files_tool, search_repo_tool, apply_patch_tool],
                "client": client
            }
            if model:
                agent_kwargs['model'] = model
            
            agent = Agent(**agent_kwargs)
            
            # Display agent header with model name
            display_model = model if model else "gpt-4o"
            self.nvim.async_call(self._append_content, ["", f"## Agent ({display_model})", "", ""])

            # Run the agent with streaming
            result_stream = Runner.run_streamed(agent, input=prompt)
            
            # Accumulate streaming text and update periodically to avoid race conditions
            accumulated_text = ""
            update_counter = 0
            
            async for event in result_stream.stream_events():
                event_type = type(event).__name__
                
                # Accumulate text deltas
                if event_type == 'RawResponsesStreamEvent':
                    data = event.data
                    data_type = type(data).__name__
                    
                    if data_type == 'ResponseTextDeltaEvent':
                        accumulated_text += data.delta
                        update_counter += 1
                        
                        # Update every 5 deltas to reduce race conditions while still showing progress
                        if update_counter >= 5:
                            self.nvim.async_call(self._append_stream, accumulated_text)
                            accumulated_text = ""
                            update_counter = 0
                
                if result_stream.is_complete:
                    break
            
            # Flush any remaining text after loop completes
            if accumulated_text:
                self.nvim.async_call(self._append_stream, accumulated_text)

        except Exception as e:
            import traceback
            self.logger.error(f"Agent run failed: {e}\n{traceback.format_exc()}")
            self.nvim.async_call(self._append_content, [f"\nError: {str(e)}"])

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

    def _append_stream(self, text):
        """Append text (possibly with newlines) to the content buffer.
        
        This method is called through nvim.async_call, so all nvim API calls here are safe.
        """
        if not hasattr(self, 'content_buf') or not self.content_buf.valid:
            return
            
        try:
            # Get current buffer content
            all_lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
            
            if not all_lines:
                all_lines = ['']
            
            # Check if we should autoscroll (user is at the end of buffer)
            should_scroll = False
            for win in self.nvim.windows:
                if win.buffer == self.content_buf:
                    cursor = win.cursor
                    # If cursor is on last line or close to it, autoscroll
                    if cursor[0] >= len(all_lines) - 2:
                        should_scroll = True
                        break
            
            # Append text to the last line
            last_line = all_lines[-1]
            combined = last_line + text
            
            # Split on newlines
            if '\n' in combined:
                parts = combined.split('\n')
                # Replace last line with all parts
                new_lines = all_lines[:-1] + parts
            else:
                # Just update the last line
                new_lines = all_lines[:-1] + [combined]
            
            # Write back the entire buffer
            self.nvim.api.buf_set_lines(self.content_buf, 0, -1, False, new_lines)
            
            # Autoscroll if user was at the end
            if should_scroll:
                for win in self.nvim.windows:
                    if win.buffer == self.content_buf:
                        # Move cursor to last line
                        new_line_count = len(new_lines)
                        win.cursor = (new_line_count, 0)
        except Exception as e:
            self.logger.error(f"Error in _append_stream: {e}")

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

