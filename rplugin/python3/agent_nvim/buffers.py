"""Buffer management for agent.nvim plugin."""

import os
import subprocess
import tempfile


class BufferManager:
    """Manages Neovim buffers for agent.nvim plugin."""
    
    def __init__(self, nvim, logger):
        """Initialize buffer manager.
        
        Args:
            nvim: Neovim instance
            logger: Logger instance
        """
        self.nvim = nvim
        self.logger = logger
        self.content_buf = None
        self.prompt_buf = None
        self._agent_response_started = False
        # Create namespaces for highlights
        self._user_prompt_ns = nvim.api.create_namespace("agent_user_prompt")
        self._prompt_highlight_ns = nvim.api.create_namespace("agent_prompt_highlight")
    
    def create_layout(self):
        """Create the agent UI layout with content and prompt buffers."""
        # Check if buffers already exist
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
            self.nvim.api.buf_set_option(content_buf, "filetype", "agent-content")
            self.nvim.api.buf_set_option(content_buf, "buftype", "nofile")
            self.nvim.api.buf_set_option(content_buf, "swapfile", False)
            # Set buffer variable to identify this as agent content
            self.nvim.api.buf_set_var(content_buf, "agent_buffer", "content")

        # Create prompt buffer if needed
        if not prompt_buf or not prompt_buf.valid:
            prompt_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(prompt_buf, "AgentPrompt")
            self.nvim.api.buf_set_option(prompt_buf, "filetype", "agent-prompt")
            self.nvim.api.buf_set_option(prompt_buf, "buftype", "nofile")
            self.nvim.api.buf_set_option(prompt_buf, "swapfile", False)

        # Create split layout
        # Use current buffer instead of new tab
        self.nvim.command("enew")

        # Set content buffer to current window
        content_win = self.nvim.api.get_current_win()
        self.nvim.api.win_set_buf(content_win, content_buf)
        # Set wrap for content window
        self.nvim.api.win_set_option(content_win, "wrap", True)
        self.nvim.api.win_set_option(content_win, "linebreak", True)

        # Create split for prompt
        self.nvim.command("botright split")
        self.nvim.command("resize 5")
        self.nvim.api.win_set_buf(0, prompt_buf)

        # Store buffer handles
        self.content_buf = content_buf
        self.prompt_buf = prompt_buf

        # Add welcome message if empty
        if len(content_buf) <= 1:
            welcome = [
                "```",
                "         ▗       ▘    ",
                " ▀▌▛▌█▌▛▌▜▘  ▛▌▌▌▌▛▛▌ ",
                " █▌▙▌▙▖▌▌▐▖▗ ▌▌▚▘▌▌▌▌ ",
                "   ▄▌                 ",
                "```",
                "",
                "> Type your request in the prompt below.",
            ]
            self.nvim.api.buf_set_lines(content_buf, 0, -1, False, welcome)

        # Enable render-markdown for the content buffer
        self.enable_render_markdown()
        

    
    def create_diff_buffer(self, patch_str):
        """Create or update the diff buffer with patch content.
        
        Args:
            patch_str: Patch content as string
        """
        # Create or reuse AgentDiff buffer
        diff_buf = None
        for buf in self.nvim.buffers:
            if buf.name.endswith("AgentDiff"):
                diff_buf = buf
                break

        if not diff_buf or not diff_buf.valid:
            diff_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(diff_buf, "AgentDiff")
            self.nvim.api.buf_set_option(diff_buf, "filetype", "diff")
            self.nvim.api.buf_set_option(diff_buf, "buftype", "nofile")
            self.nvim.api.buf_set_option(diff_buf, "swapfile", False)

        # Set content
        lines = patch_str.split("\n")
        self.nvim.api.buf_set_lines(diff_buf, 0, -1, False, lines)

        # Open in a split if not visible
        win_found = False
        for win in self.nvim.windows:
            if win.buffer == diff_buf:
                win_found = True
                break

        if not win_found:
            self.nvim.command("vsplit")
            self.nvim.api.win_set_buf(0, diff_buf)
            self.nvim.out_write("Patch proposed in AgentDiff buffer.\n")
    
    def apply_patch(self):
        """Apply the patch from the AgentDiff buffer."""
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

        # Apply patch using git apply
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
                tmp.write(patch_content)
                tmp_path = tmp.name

            cwd = self.nvim.call("getcwd")
            cmd = [
                "git",
                "apply",
                "--ignore-space-change",
                "--ignore-whitespace",
                tmp_path,
            ]

            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

            if proc.returncode == 0:
                self.nvim.out_write("Patch applied successfully!\n")
                # Close diff buffer/window? Maybe keep it for reference.
                self.nvim.command("checktime")  # Reload buffers
            else:
                self.nvim.err_write(f"Failed to apply patch: {proc.stderr}\n")

            os.remove(tmp_path)
        except Exception as e:
            self.nvim.err_write(f"Exception applying patch: {e}\n")
    
    def get_completions(self, findstart, base):
        """Provide file path completions for @mentions.
        
        Args:
            findstart: 1 to find start position, 0 to return matches
            base: Partial string to complete
            
        Returns:
            Start position (when findstart=1) or list of matches (when findstart=0)
        """
        if findstart == 1:
            # Find start of the word to complete
            # We want to complete after '@'
            line = self.nvim.current.line
            col = self.nvim.current.window.cursor[1]

            # Search backwards for '@'
            start = -1
            for i in range(col - 1, -1, -1):
                if line[i] == "@":
                    start = i
                    break
                if line[i] == " ":  # Stop at space
                    break

            if start != -1:
                return start + 1  # Return index after '@'
            return -1
        else:
            # Return list of matches
            # base is the string after '@'
            try:
                cwd = self.nvim.call("getcwd")
                matches = []

                # Simple recursive search
                for root, _, filenames in os.walk(cwd):
                    if ".git" in root:
                        continue
                    for filename in filenames:
                        rel_path = os.path.relpath(os.path.join(root, filename), cwd)
                        if rel_path.startswith(base):
                            matches.append(rel_path)
                            if len(matches) > 50:  # Limit results
                                break
                    if len(matches) > 50:
                        break

                return matches
            except Exception:
                return []

    def get_file_completions_async(self, base, callback_id):
        """Provide async file path completions for @mentions.

        Args:
            base: Partial string to complete (after '@')
            callback_id: ID for the callback to call with results
        """
        try:
            cwd = self.nvim.call("getcwd")
            matches = []

            # Simple recursive search
            for root, _, filenames in os.walk(cwd):
                if ".git" in root:
                    continue
                for filename in filenames:
                    rel_path = os.path.relpath(os.path.join(root, filename), cwd)
                    if rel_path.startswith(base):
                        matches.append(rel_path)
                        if len(matches) > 50:  # Limit results
                            break
                if len(matches) > 50:
                    break

            # Call the Lua callback with results
            self.nvim.exec_lua("agent_nvim_blink_file_completion_callback(...)", [matches, callback_id])
        except Exception as e:
            self.nvim.exec_lua("agent_nvim_blink_file_completion_callback(...)", [[], callback_id])
    
    def append_content(self, lines, fold=False):
        """Append one or more lines to the content buffer.
        
        Args:
            lines: List of strings to append
            fold: If True, fold the appended content immediately
        """
        if hasattr(self, "content_buf") and self.content_buf and self.content_buf.valid:
            # Ensure every item is a single line
            processed = []
            for item in lines:
                if isinstance(item, str) and "\n" in item:
                    # Split on newlines, keep empty parts (blank lines)
                    processed.extend([ln for ln in item.split("\n")])
                else:
                    processed.append(item)
            
            def wrapped_append():
                """Append and scroll."""
                self._append_and_scroll(processed, fold=fold)
            
            # Write the processed list to the buffer
            self.nvim.async_call(wrapped_append)
            # Enable render-markdown after content is added
            self.nvim.async_call(self.enable_render_markdown)
    
    def _append_and_scroll(self, processed, fold=False):
        """Helper to append lines and autoscroll content buffer.
        
        Args:
            processed: List of lines to append
            fold: If True, create a fold for the appended lines
        """
        if not hasattr(self, "content_buf") or not self.content_buf or not self.content_buf.valid:
            return

        # Get the line count before appending (this is where new content starts)
        start_line = len(self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False))
        
        # Save the current window to restore focus later
        try:
            current_win = self.nvim.current.window
        except Exception:
            current_win = None
        
        # Append lines
        self.nvim.api.buf_set_lines(self.content_buf, -1, -1, False, processed)

        # Get the new line count (end of appended content)
        end_line = len(self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False))
        
        # Highlight file references in appended lines (higher priority overrides Special)
        self._highlight_file_refs(start_line, end_line)
        
        # Highlight user prompts (lower priority so file refs can override)
        self._highlight_user_prompt(start_line, end_line)

        # Autoscroll to bottom only if autoscroll is enabled
        for win in self.nvim.windows:
            if win.buffer == self.content_buf:
                try:
                    # Check if autoscroll is enabled for this buffer
                    autoscroll_enabled = self.nvim.api.buf_get_var(self.content_buf, "agent_autoscroll_enabled")
                except Exception:
                    # If variable doesn't exist, default to enabled
                    autoscroll_enabled = 1
                
                # Only scroll if autoscroll is enabled
                if autoscroll_enabled:
                    win.cursor = (end_line, 0)
        
        # Restore focus to the previously active window
        if current_win and current_win.valid:
            try:
                self.nvim.current.window = current_win
            except Exception:
                pass
        
        # Create fold if requested
        if fold and len(processed) > 1:
            bufnr = self.content_buf.number
            # start_line is 0-indexed count, so +1 for 1-indexed vim line
            fold_start = start_line + 1
            fold_end = end_line
            self._create_fold(bufnr, fold_start, fold_end)
    
    def _create_fold(self, bufnr, start_line, end_line):
         """Create a fold in the buffer.
         
         Args:
             bufnr: Buffer number
             start_line: Start line (1-indexed)
             end_line: End line (1-indexed)
         """
         try:
             # Add highlight to the first line (tool output title) using OkMsg
             # start_line is 1-indexed, nvim_buf_add_highlight uses 0-indexed
             self.nvim.api.buf_add_highlight(bufnr, -1, "OkMsg", start_line - 1, 0, -1)
             
             self.nvim.exec_lua(
                 "require('agent_nvim.folds').create_fold(...)",
                 bufnr, start_line, end_line, None
             )
         except Exception as e:
             self.logger.error(f"Error creating fold: {e}")
    
    def _highlight_file_refs(self, start_line, end_line):
        """Highlight file references in the specified range.
        
        Args:
            start_line: Start line (0-indexed)
            end_line: End line (0-indexed, exclusive)
        """
        try:
            if not self.content_buf or not self.content_buf.valid:
                return
            
            import re
            bufnr = self.content_buf.number
            lines = self.nvim.api.buf_get_lines(self.content_buf, start_line, end_line, False)
            
            # Pattern for file references like @path/to/file
            pattern = r'@[a-zA-Z0-9_./-]+/[a-zA-Z0-9_./-]*'
            
            for idx, line in enumerate(lines):
                line_num = start_line + idx
                for match in re.finditer(pattern, line):
                    start_col = match.start()
                    end_col = match.end()
                    # Use priority 10 so Directory highlights override Special
                    self.nvim.api.buf_add_highlight(bufnr, 10, "Directory", line_num, start_col, end_col)
        except Exception as e:
            self.logger.debug(f"Error highlighting file refs: {e}")
        finally:
            # Clear the user prompt namespace to prepare for redraw
            try:
                self.nvim.api.buf_clear_namespace(self.content_buf.number, self._user_prompt_ns, start_line, end_line)
            except Exception:
                pass
    
    def _highlight_user_prompt(self, start_line, end_line):
        """Highlight user prompt text with Comment highlight group and username with CursorLineNr.
        
        Also adds virtual text prefix (┃ ) to each line of the user prompt.
        
        Args:
            start_line: Start line (0-indexed)
            end_line: End line (0-indexed, exclusive)
        """
        try:
            if not self.content_buf or not self.content_buf.valid:
                return
            
            import re
            bufnr = self.content_buf.number
            lines = self.nvim.api.buf_get_lines(self.content_buf, start_line, end_line, False)
            
            # Patterns for special highlighting
            file_pattern = r'@[a-zA-Z0-9_./-]+'
            slash_pattern = r'/[a-z]+'
            
            # Helper to check if a position is inside any file reference
            def is_inside_file_ref(pos, file_ranges):
                for start, end in file_ranges:
                    if start <= pos < end:
                        return True
                return False
            
            # Look for user prompt section: empty line, # Username header, empty line, then prompt text
            # We mark the username with CursorLineNr and prompt text with Comment highlight
            in_user_section = False
            user_section_start = None
            skip_next_empty = False
            
            for idx, line in enumerate(lines):
                 line_num = start_line + idx
                 
                 # Check if this is an Agent header line (# Agent)
                 if line.startswith("# Agent"):
                     # Highlight the agent title with CursorLineNr
                     self.nvim.api.buf_add_highlight(bufnr, 5, "CursorLineNr", line_num, 0, -1)
                     continue
                 
                 # Check if this is a user header line (# Username, not # Agent)
                 if line.startswith("# ") and not line.startswith("# Agent"):
                     in_user_section = True
                     user_section_start = line_num
                     skip_next_empty = True  # Skip the empty line after the header
                     # Highlight the username line with CursorLineNr
                     self.nvim.api.buf_add_highlight(bufnr, 5, "CursorLineNr", line_num, 0, -1)
                     continue
                 
                 # If we're in a user section and we've moved past header setup
                 if in_user_section and user_section_start is not None:
                     # Skip the first empty line after header
                     if skip_next_empty and not line.strip():
                         skip_next_empty = False
                         continue
                     skip_next_empty = False
                     
                     # Stop highlighting when we hit another section marker or agent response
                     if line.startswith("#") or line.startswith("**["):
                         in_user_section = False
                         continue
                     
                     # Mark the prompt text with Comment highlight (priority 0, lowest priority so Directory overrides)
                     if line.strip():  # Only highlight non-empty lines
                         self.nvim.api.buf_add_highlight(bufnr, 0, "Comment", line_num, 0, -1)
                     
                     # Find all file reference ranges first (these take priority)
                     file_ranges = []
                     for match in re.finditer(file_pattern, line):
                         file_ranges.append((match.start(), match.end()))
                     
                     # Highlight slash commands (but not if inside a file reference)
                     for match in re.finditer(slash_pattern, line):
                         start_col = match.start()
                         if not is_inside_file_ref(start_col, file_ranges):
                             end_col = match.end()
                             self.nvim.api.buf_add_highlight(bufnr, 10, "Special", line_num, start_col, end_col)
                     
                     # Highlight file references with higher priority
                     for start_col, end_col in file_ranges:
                         self.nvim.api.buf_add_highlight(bufnr, 10, "Directory", line_num, start_col, end_col)
                     
                     # Add virtual text prefix for all lines (including blank lines)
                     self.nvim.api.buf_set_extmark(bufnr, self._user_prompt_ns, line_num, 0, {
                         "virt_text": [["┃ ", "Comment"]],
                         "virt_text_pos": "inline"
                     })
        except Exception as e:
            self.logger.debug(f"Error highlighting user prompt: {e}")
    
    def append_stream_lua_direct(self, text, bufnr):
        """Append text using Lua animation for smooth typing effect.
        
        Args:
            text: Text to append
            bufnr: Buffer number (must be passed in, can't access from async context)
        """
        # Handle initial spacing for agent responses
        # Check if this is the first chunk and if we need to remove the extra blank line
        should_remove_last_line = False
        if not self._agent_response_started:
            self._agent_response_started = True
            # Ensure the text starts with a non-empty character to maintain spacing
            # This works for both chat_completions and responses APIs
            if text and text.startswith("\n"):
                # If text starts with newlines, remove the extra blank line
                # since the model is providing its own spacing
                should_remove_last_line = True

        # Escape text for Lua string
        escaped_text = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("'", "\\'")
        )

        # Use a timer to animate character-by-character
        # The should_remove_last_line flag is handled atomically on first write
        lua_code = f"""
        local bufnr = {bufnr}
        local text = "{escaped_text}"
        local should_remove_last_line = {str(should_remove_last_line).lower()}

        -- Initialize animation queue if it doesn't exist
        if not _G.agent_stream_queue then
            _G.agent_stream_queue = {{}}
            _G.agent_stream_timer = nil
            _G.agent_stream_paused = false
        end

        -- Add text to queue
        table.insert(_G.agent_stream_queue, {{bufnr = bufnr, text = text, remove_last_line = should_remove_last_line}})

        -- Start timer if not already running
        if not _G.agent_stream_timer then
            local function timer_callback()
                -- Check if streaming is paused (tool output being written)
                if _G.agent_stream_paused then
                    return  -- Skip this tick, will try again on next interval
                end

                if #_G.agent_stream_queue == 0 then
                    if _G.agent_stream_timer then
                        _G.agent_stream_timer:stop()
                        _G.agent_stream_timer = nil
                    end
                    return
                end

                local item = _G.agent_stream_queue[1]
                if not vim.api.nvim_buf_is_valid(item.bufnr) then
                    table.remove(_G.agent_stream_queue, 1)
                    return
                end

                -- Handle blank line removal on first write atomically
                if item.remove_last_line then
                    local line_count = vim.api.nvim_buf_line_count(item.bufnr)
                    local last_line_idx = line_count - 1
                    local last_line = vim.api.nvim_buf_get_lines(item.bufnr, last_line_idx, last_line_idx + 1, false)
                    if (last_line[1] or "") == "" then
                        vim.api.nvim_buf_set_lines(item.bufnr, last_line_idx, last_line_idx + 1, false, {{}})
                    end
                    item.remove_last_line = false
                end

                -- Vary characters written: mostly 3, sometimes 2 or 4 for irregularity
                local rand = math.random()
                local chars_to_write = 3
                if rand < 0.15 then
                    chars_to_write = 2  -- 15% slower
                elseif rand > 0.85 then
                    chars_to_write = 4  -- 15% faster
                end

                local chunk = item.text:sub(1, chars_to_write)
                item.text = item.text:sub(chars_to_write + 1)

                if chunk ~= "" then
                    local line_count = vim.api.nvim_buf_line_count(item.bufnr)
                    local last_line_idx = line_count - 1
                    local last_line = vim.api.nvim_buf_get_lines(item.bufnr, last_line_idx, last_line_idx + 1, false)
                    local last_column = #(last_line[1] or "")

                    local lines = vim.split(chunk, "\\n", {{plain = true}})
                    vim.api.nvim_buf_set_text(item.bufnr, last_line_idx, last_column, last_line_idx, last_column, lines)

                    -- Autoscroll only if autoscroll is enabled for this buffer
                    local autoscroll_enabled = 1
                    local ok, result = pcall(function()
                        return vim.api.nvim_buf_get_var(item.bufnr, "agent_autoscroll_enabled")
                    end)
                    if ok and result == 0 then
                        autoscroll_enabled = 0
                    end
                    
                    if autoscroll_enabled == 1 then
                        for _, win in ipairs(vim.api.nvim_list_wins()) do
                            if vim.api.nvim_win_get_buf(win) == item.bufnr then
                                local new_line_count = vim.api.nvim_buf_line_count(item.bufnr)
                                pcall(vim.api.nvim_win_set_cursor, win, {{new_line_count, 0}})
                            end
                        end
                    end
                end

                -- Remove item if all text written
                if item.text == "" then
                    table.remove(_G.agent_stream_queue, 1)
                end
            end

            _G.agent_stream_timer = vim.loop.new_timer()
            -- Start with random delay and keep repeating with slight variation
            local base_interval = 15
            _G.agent_stream_timer:start(math.random(10, 20), base_interval, vim.schedule_wrap(timer_callback))
        end
        """
        try:
            self.nvim.exec_lua(lua_code)
        except Exception as e:
            self.logger.error(f"Error in _append_stream_lua: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
    

    
    def append_cancel_message(self):
        """Append cancellation message with smart spacing."""
        if hasattr(self, "content_buf") and self.content_buf and self.content_buf.valid:
            lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)

            # Check if there's any response content after the agent header
            response_started = False
            if len(lines) > 4:
                # Check the lines after the header structure
                for i in range(4, len(lines)):
                    if lines[i].strip():  # Non-empty line
                        response_started = True
                        break

            if response_started:
                # There's already content, so add the cancellation message with spacing
                self.append_content(["", "**[Request cancelled by user]**"])
            else:
                # No content yet, just add the cancellation message
                self.append_content(["**[Request cancelled by user]**"])
    
    def enable_render_markdown(self):
        """Enable render-markdown for the content buffer."""
        try:
            # Try to enable render-markdown if it's available
            self.nvim.command("silent! RenderMarkdown enable")
        except Exception:
            pass
    
    def reset_agent_response_flag(self):
        """Reset the agent response started flag."""
        self._agent_response_started = False
    
    def highlight_prompt_buffer(self):
        """Highlight file references and slash commands in the prompt buffer as user types."""
        try:
            if not hasattr(self, "prompt_buf") or not self.prompt_buf or not self.prompt_buf.valid:
                return
            
            import re
            bufnr = self.prompt_buf.number
            lines = self.nvim.api.buf_get_lines(self.prompt_buf, 0, -1, False)
            
            # Clear existing highlights (only in highlight namespace, preserving placeholder)
            self.nvim.api.buf_clear_namespace(bufnr, self._prompt_highlight_ns, 0, -1)
            
            # Pattern for slash commands like /help, /clear, /cancel
            slash_pattern = r'/[a-z]+'
            
            # Pattern for file references like @filename or @path/to/file
            file_pattern = r'@[a-zA-Z0-9_./-]+'
            
            for line_num, line in enumerate(lines):
                # Find all file reference ranges first (these take priority)
                file_ranges = []
                for match in re.finditer(file_pattern, line):
                    file_ranges.append((match.start(), match.end()))
                
                # Helper to check if a position is inside any file reference
                def is_inside_file_ref(pos):
                    for start, end in file_ranges:
                        if start <= pos < end:
                            return True
                    return False
                
                # Highlight slash commands (but not if inside a file reference)
                for match in re.finditer(slash_pattern, line):
                    start_col = match.start()
                    if not is_inside_file_ref(start_col):
                        end_col = match.end()
                        self.nvim.api.buf_add_highlight(bufnr, self._prompt_highlight_ns, "Special", line_num, start_col, end_col)
                
                # Highlight file references
                for start_col, end_col in file_ranges:
                    self.nvim.api.buf_add_highlight(bufnr, self._prompt_highlight_ns, "Directory", line_num, start_col, end_col)
        except Exception as e:
            self.logger.debug(f"Error highlighting prompt buffer: {e}")
