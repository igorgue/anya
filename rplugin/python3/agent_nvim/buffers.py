"""Buffer management for agent.nvim plugin."""

import os
import re
import subprocess
import tempfile

# Try to import typing, with fallback for older Python versions
try:
    from typing import List, Dict, Any
except ImportError:
    # Fallback for older Python versions
    List = list
    Dict = dict
    Any = object


def _get_plugin_root():
    """Get the plugin root directory."""
    # __file__ = .../rplugin/python3/agent_nvim/buffers.py
    # Go up 4 levels to get plugin root
    path = os.path.abspath(__file__)
    for _ in range(4):
        path = os.path.dirname(path)
    return path


def _load_logo():
    """Load logo from res/logo.txt file."""
    import logging

    logger = logging.getLogger("agent.nvim")

    plugin_dir = _get_plugin_root()
    logo_path = os.path.join(plugin_dir, "res", "logo.txt")
    lines = []
    try:
        with open(logo_path, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f]
        logger.info(f"Loaded logo from file: {logo_path}")
    except (FileNotFoundError, IOError) as e:
        logger.warning(f"Could not load logo from {logo_path}: {e}, using fallback")
        # Fallback to hardcoded logo if file not found
        lines = [
            "agent.nvim",
        ]
    return lines


def _build_welcome_message():
    """Build welcome message with logo loaded from file."""
    logo_lines = _load_logo()
    msg = ["```"]
    msg.extend(logo_lines)
    msg.extend(["```", "", "> Type your request in the prompt below."])
    return msg


class BufferManager:
    """Manages Neovim buffers for agent.nvim plugin."""

    _welcome_message_cache = None

    @property
    def WELCOME_MESSAGE(self):
        """Lazy-load welcome message on first access."""
        if BufferManager._welcome_message_cache is None:
            BufferManager._welcome_message_cache = _build_welcome_message()
            self.logger.info(
                f"Welcome message loaded, logo has {len(BufferManager._welcome_message_cache) - 4} lines"
            )
        return BufferManager._welcome_message_cache

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
        self._file_backups = {}
        # Create namespaces for highlights
        self._user_prompt_ns = nvim.api.create_namespace("agent_user_prompt")
        self._prompt_highlight_ns = nvim.api.create_namespace("agent_prompt_highlight")
        self._username_highlight_ns = nvim.api.create_namespace(
            "agent_username_highlight"
        )
        self._tool_fold_ns = nvim.api.create_namespace("agent_tool_fold")
        # Create transparent highlight groups for tool fold icons
        self._setup_tool_fold_highlights()
        # Track last output type for consistent spacing
        # Values: None, 'header', 'tool', 'thinking', 'llm'
        # Note: 'thinking' is ONLY for built-in reasoning (o1/glm-4), NOT MCP tools
        self._last_output_type = None

    def _setup_tool_fold_highlights(self):
        """Create transparent highlight groups for tool fold icons."""
        try:
            # Use exec_lua to call nvim_set_hl directly (matches edit_view.lua pattern)
            # Get foreground colors from existing highlight groups and create transparent versions
            # Explicitly set bg to nil to ensure transparency
            self.nvim.exec_lua(
                """
                local ok_hl = vim.api.nvim_get_hl(0, { name = "OkMsg", link = false })
                local err_hl = vim.api.nvim_get_hl(0, { name = "ErrorMsg", link = false })
                
                -- Create transparent versions - explicitly set bg to nil for transparency
                vim.api.nvim_set_hl(0, "AgentToolFoldOk", {
                    fg = ok_hl.fg,
                    bg = nil,  -- Explicitly nil for transparency
                })
                vim.api.nvim_set_hl(0, "AgentToolFoldError", {
                    fg = err_hl.fg,
                    bg = nil,  -- Explicitly nil for transparency
                })
                """
            )
        except Exception as e:
            self.logger.warning(f"Could not create transparent tool fold highlights: {e}")
            # Fallback to regular highlights if transparent setup fails
            pass

    def create_layout(self):
        """Create the agent UI layout with content and prompt buffers."""
        # Check if buffers already exist
        content_buf = None
        prompt_buf = None

        # Try to find existing buffers
        for buf in self.nvim.buffers:
            if buf.name.endswith("chat"):
                content_buf = buf
            elif buf.name.endswith("prompt"):
                prompt_buf = buf

        # Create content buffer if needed
        if not content_buf or not content_buf.valid:
            content_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(content_buf, "chat")
            # Use markdown.agent-content to inherit markdown behavior
            self.nvim.api.buf_set_option(
                content_buf, "filetype", "markdown.agent-content"
            )
            self.nvim.api.buf_set_option(content_buf, "buftype", "nofile")
            self.nvim.api.buf_set_option(content_buf, "swapfile", False)
            # Set buffer variable to identify this as agent content
            self.nvim.api.buf_set_var(content_buf, "agent_buffer", "content")

        # Create prompt buffer if needed
        if not prompt_buf or not prompt_buf.valid:
            prompt_buf = self.nvim.api.create_buf(False, True)
            self.nvim.api.buf_set_name(prompt_buf, "prompt")
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

        # Create split for prompt (6 lines: 5 usable + 1 for toolbar)
        self.nvim.command("botright split")
        self.nvim.command("resize 6")
        self.nvim.api.win_set_buf(0, prompt_buf)

        # Store buffer handles
        self.content_buf = content_buf
        self.prompt_buf = prompt_buf

        # Set up window size preservation for prompt
        self.nvim.exec_lua(f"""
        -- Initialize preferred height with current actual height (6 = 5 usable + 1 for toolbar)
        _G.agent_prompt_preferred_height = 6
        """)
        self._setup_prompt_size_preservation()

        # Add welcome message if empty (with animation)
        if len(content_buf) <= 1:
            self.animate_welcome_message(content_buf)

        # Enable render-markdown for the content buffer
        self.enable_render_markdown()

    def _setup_prompt_size_preservation(self):
        """Set up autocmd to preserve user's preferred prompt window height."""
        self.nvim.exec_lua(f"""
        local group = vim.api.nvim_create_augroup("AgentPromptSizePreservation", {{ clear = true }})
        
        -- Initialize global variables (6 = 5 usable + 1 for toolbar)
        _G.agent_prompt_preferred_height = 6
        _G.agent_prompt_last_known_height = 6
        
        local function get_prompt_window()
            for _, win in ipairs(vim.api.nvim_list_wins()) do
                if vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == {self.prompt_buf.number} then
                    return win
                end
            end
            return nil
        end
        
        -- Function to restore preferred height when Neovim resizes
        local function restore_on_neovim_resize()
            local prompt_win = get_prompt_window()
            if prompt_win then
                local current_height = vim.api.nvim_win_get_height(prompt_win)
                if current_height ~= _G.agent_prompt_preferred_height then
                    vim.api.nvim_win_set_height(prompt_win, _G.agent_prompt_preferred_height)
                end
            end
        end
        
        -- Function to detect manual resizes by the user
        local function detect_manual_resize()
            local prompt_win = get_prompt_window()
            if prompt_win then
                local current_height = vim.api.nvim_win_get_height(prompt_win)
                if current_height ~= _G.agent_prompt_last_known_height then
                    _G.agent_prompt_preferred_height = current_height
                    _G.agent_prompt_last_known_height = current_height
                end
            end
        end
        
        -- Track manual resizes on WinResized (fires for ALL resizes)
        vim.api.nvim_create_autocmd("WinResized", {{
            group = group,
            callback = function()
                -- Small delay to let Neovim settle
                vim.defer_fn(function()
                    detect_manual_resize()
                end, 50)
            end,
        }})
        
        -- Restore preferred height when Neovim window itself is resized
        vim.api.nvim_create_autocmd("VimResized", {{
            group = group,
            callback = function()
                vim.defer_fn(restore_on_neovim_resize, 100)
            end,
        }})
        
        -- Update last known height when entering prompt window
        vim.api.nvim_create_autocmd("WinEnter", {{
            group = group,
            buffer = {self.prompt_buf.number},
            callback = function()
                local prompt_win = get_prompt_window()
                if prompt_win then
                    _G.agent_prompt_last_known_height = vim.api.nvim_win_get_height(prompt_win)
                end
            end,
        }})
        """)

    def render_edit_blocks(self, edit_str):
        """Render SEARCH/REPLACE edit blocks in the content buffer using Lua.

        Args:
            edit_str: String containing one or more SEARCH/REPLACE blocks
        """
        if (
            not hasattr(self, "content_buf")
            or not self.content_buf
            or not self.content_buf.valid
        ):
            return

        try:
            from . import search_replace

            # Parse the edit blocks
            blocks = search_replace.parse_search_replace_blocks(edit_str)

            if not blocks:
                self.logger.warning("No valid SEARCH/REPLACE blocks found")
                self.append_content(["", "> No valid SEARCH/REPLACE blocks found", ""])
                return

            # Render each block
            for block in blocks:
                try:
                    self.nvim.exec_lua(
                        """
                        local args = {...}
                        require('agent_nvim.edit_view').render_edit(
                            args[1], args[2], args[3], args[4], args[5]
                        )
                        """,
                        self.content_buf.number,
                        block.path,
                        block.search,
                        block.replace,
                        block.raw_block,
                    )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to render edit block for {block.path}: {e}"
                    )
                    # Continue with next block instead of failing entirely
                    continue

            # Add instruction message immediately after rendering edit blocks
            # This ensures proper ordering (instruction appears after the edit blocks)
            self._append_and_scroll(
                [
                    "",
                    "> Press **1** to accept, **2** to reject and **za** to open the changeset on top of the fold",
                ],
                fold=False,
            )

            # Scroll to bottom to show new content
            self._scroll_to_bottom()

        except Exception as e:
            self.logger.error(f"Error rendering edit blocks: {e}")
            self.nvim.out_write(f"Edit block render error: {e}\n")
            # Fallback to simple append
            self.append_content(["```", edit_str, "```"])

    def apply_edit_blocks(self, edit_str, return_details=False):
        """Apply SEARCH/REPLACE edit blocks to files.

        Args:
            edit_str: String containing one or more SEARCH/REPLACE blocks
            return_details: If True, return (success, message) tuple for LLM feedback

        Returns:
            If return_details is False: True if all edits applied successfully, False otherwise
            If return_details is True: (success, message) tuple with detailed feedback
        """
        try:
            from . import search_replace

            cwd = self.nvim.call("getcwd")

            # Parse the edit blocks
            blocks = search_replace.parse_search_replace_blocks(edit_str)

            if not blocks:
                self.logger.warning("No valid SEARCH/REPLACE blocks to apply")
                msg = "No valid SEARCH/REPLACE blocks found in the edit content."
                if return_details:
                    return False, msg
                return False

            # Apply all blocks atomically
            results = search_replace.apply_edit_blocks(blocks, cwd, atomic=True)

            # Check if all succeeded
            all_success = all(r.success for r in results)

            if all_success:
                # Store backups for undo
                for result in results:
                    if result.original_content is not None:
                        full_path = os.path.join(cwd, result.path)
                        self._file_backups[full_path] = result.original_content

                # Reload modified buffers
                self.nvim.command("checktime")

                self.logger.info(f"Applied {len(results)} edit(s) successfully")
                msg = f"Successfully applied {len(results)} edit(s)."
                if return_details:
                    return True, msg
                return True
            else:
                # Build detailed failure message for LLM
                failure_messages = []
                for result in results:
                    if not result.success:
                        self.logger.error(f"Edit failed: {result.message}")
                        self.nvim.err_write(f"Edit failed: {result.message}\n")
                        failure_messages.append(result.message)

                msg = "EDIT_FAILED: " + " | ".join(failure_messages)
                if return_details:
                    return False, msg
                return False

        except Exception as e:
            self.logger.error(f"Error applying edit blocks: {e}")
            self.nvim.err_write(f"Error applying edits: {e}\n")
            msg = f"EDIT_FAILED: Error applying edits: {e}"
            if return_details:
                return False, msg
            return False

    def _scroll_to_bottom(self):
        """Scroll content buffer to bottom."""
        for win in self.nvim.windows:
            if win.buffer == self.content_buf:
                try:
                    line_count = len(self.content_buf)
                    win.cursor = (line_count, 0)
                except Exception:
                    pass

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
            self.nvim.exec_lua(
                "agent_nvim_blink_file_completion_callback(...)", [matches, callback_id]
            )
        except Exception:
            self.nvim.exec_lua(
                "agent_nvim_blink_file_completion_callback(...)", [[], callback_id]
            )

    def _calculate_spacing(self, content_type):
        """Calculate blank lines needed before content based on output state.
        
        Args:
            content_type: Type of content being added ('header', 'tool', 'thinking', 'llm', 'user')
                         'thinking' is ONLY for built-in reasoning (o1/glm-4), NOT MCP tools
            
        Returns:
            Number of blank lines to prepend (0, 1, or 2)
        """
        # Spacing rules (based on user requirements):
        # - LLM text and tool outputs both end with blank lines
        # - First content (None): no spacing
        # - Header/user after content: 1 blank line before
        # - After header/user: no spacing (they end with blank line)
        # - Tool after LLM: 0 blank lines (LLM already ends with blank, tool gets spacing from that)
        # - LLM after tool: 0 blank lines (tool already ends with blank, LLM gets spacing from that)
        # - Consecutive tools: no spacing between them (both end with blank)
        # - Consecutive LLM (after interruption): 0 blank lines (previous LLM already ended with blank)
        
        if self._last_output_type is None:
            # Very first content - no spacing
            return 0
        
        # Headers and user prompts get 1 blank line before them (unless first)
        if content_type in ('header', 'user'):
            return 1
        
        # Thinking and tool are both "tool-like" for spacing purposes
        current_is_tool = content_type in ('tool', 'thinking')
        last_was_tool = self._last_output_type in ('tool', 'thinking')
        last_was_thinking = self._last_output_type == 'thinking'
        last_was_llm = self._last_output_type == 'llm'
        # Headers and user prompts both end with a blank line in their content
        last_was_header_like = self._last_output_type in ('header', 'user')
        
        # LLM after header/user: 1 blank line (header ends with blank, but we want another before LLM)
        if content_type == 'llm' and last_was_header_like:
            return 1
        
        # After thinking: 1 blank line before LLM (thinking ends with blank, but we want another before LLM)
        # Tool after thinking: 0 blank lines (thinking already ends with blank, tool doesn't need another)
        if last_was_thinking:
            if content_type == 'llm':
                return 1
            elif content_type == 'tool':
                return 0
        
        # Tool/thinking after header/user: no blank line (they already end with blank)
        if last_was_header_like:
            return 0
        
        # Thinking after tool: 1 blank line (user wants spacing between tool and thinking)
        if content_type == 'thinking' and self._last_output_type == 'tool':
            return 1
        
        # Between consecutive tool-like items: no blank line (both end with blank)
        # But exclude thinking from this - thinking always needs spacing after it
        if current_is_tool and last_was_tool and not last_was_thinking:
            return 0
        
        # Tool after LLM: 0 blank lines (LLM already ends with blank)
        if current_is_tool and last_was_llm:
            return 0
        
        # LLM after tool: 1 blank line (after tool group, LLM needs a blank line before it)
        # But if last was thinking, we already handled it above
        if content_type == 'llm' and last_was_tool and not last_was_thinking:
            return 1
        
        # LLM after LLM (after interruption): 0 blank lines (previous LLM already ended with blank)
        if content_type == 'llm' and last_was_llm:
            return 0
        
        # Default: 1 blank line for other transitions
        return 1

    def append_content(self, lines, fold=False, fold_error=False, content_type='llm', tool_title_index=None):
        """Append one or more lines to the content buffer.

        Args:
            lines: List of strings to append
            fold: If True, fold the appended content immediately
            fold_error: If True, highlight the fold header as an error (red)
            content_type: Type of content ('header', 'tool', 'llm') for spacing
            tool_title_index: Index in lines list where tool title is (for icon placement)
        """
        try:
            if (
                hasattr(self, "content_buf")
                and self.content_buf
                and self.content_buf.valid
            ):
                # Calculate spacing based on previous output type
                spacing = self._calculate_spacing(content_type)
                self.logger.info(f"Spacing: last={self._last_output_type}, current={content_type}, spacing={spacing}")
                
                # Ensure every item is a single line
                processed = []
                
                # Add spacing blank lines if needed
                for _ in range(spacing):
                    processed.append("")
                
                for item in lines:
                    if isinstance(item, str) and "\n" in item:
                        # Split on newlines, keep empty parts (blank lines)
                        processed.extend([ln for ln in item.split("\n")])
                    else:
                        processed.append(item)

                # Update state tracker
                self._last_output_type = content_type

                def wrapped_append():
                    """Append and scroll."""
                    try:
                        # Calculate tool_title_index in processed list
                        # If tool_title_index was provided, add spacing to account for prepended blank lines
                        processed_tool_title_index = None
                        if tool_title_index is not None:
                            processed_tool_title_index = tool_title_index + spacing
                        self._append_and_scroll(
                            processed, fold=fold, fold_error=fold_error, spacing_lines=spacing, tool_title_index=processed_tool_title_index, content_type=content_type
                        )
                    except Exception as e:
                        self.logger.error(f"Error appending content to buffer: {e}")
                        # Don't re-raise to prevent cascading errors

                # Write the processed list to the buffer
                self.nvim.async_call(wrapped_append)
                # Enable render-markdown after content is added
                self.nvim.async_call(self.enable_render_markdown)
        except Exception as e:
            self.logger.error(f"Error in append_content: {e}")
            # Don't re-raise to prevent cascading errors during error handling

    def _append_and_scroll(self, processed, fold=False, fold_error=False, spacing_lines=0, tool_title_index=None, content_type='llm'):
        """Helper to append lines and autoscroll content buffer.

        Args:
            processed: List of lines to append
            fold: If True, create a fold for the appended lines
            fold_error: If True, highlight the fold header as an error (red)
            spacing_lines: Number of blank lines prepended for spacing (fold should skip these)
            tool_title_index: Index in processed list where tool title is (for icon placement)
        """
        try:
            if (
                not hasattr(self, "content_buf")
                or not self.content_buf
                or not self.content_buf.valid
            ):
                return

            # Get current lines to determine if buffer is empty
            current_lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
            start_line = len(current_lines)
        except Exception as e:
            self.logger.error(f"Error accessing buffer in _append_and_scroll: {e}")
            return

        try:
            # Save the current window to restore focus later
            try:
                current_win = self.nvim.current.window
            except Exception:
                current_win = None

            # Check if buffer is empty (single empty line)
            buffer_is_empty = current_lines == [""]

            if buffer_is_empty:
                # Replace the empty line instead of appending after it
                self.nvim.api.buf_set_lines(self.content_buf, 0, -1, False, processed)
                start_line = 0
            else:
                # Append lines
                self.nvim.api.buf_set_lines(self.content_buf, -1, -1, False, processed)

            # Get the new line count (end of appended content)
            end_line = len(self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False))
            self.logger.info(f"_append_and_scroll: start_line={start_line} (0-indexed), end_line={end_line} (1-indexed), spacing_lines={spacing_lines}, processed_len={len(processed)}")

            # Highlight file references in appended lines (higher priority overrides Special)
            self._highlight_file_refs(start_line, end_line)

            # Highlight user prompts (lower priority so file refs can override)
            self._highlight_user_prompt(start_line, end_line)

            # Create fold if requested BEFORE autoscroll
            # This ensures the window is positioned correctly after folding
            if fold and len(processed) > 1:
                bufnr = self.content_buf.number
                # Calculate tool title row (0-indexed)
                # If tool_title_index is provided, use it directly
                # Otherwise, find first non-empty line (fallback)
                if tool_title_index is not None and tool_title_index < len(processed):
                    # tool_title_index is the index in processed list (already accounts for spacing)
                    # Lines are appended starting at start_line (0-indexed)
                    tool_title_row = start_line + tool_title_index  # 0-indexed
                    # Verify this is actually the tool title line
                    try:
                        verify_line = self.nvim.api.buf_get_lines(self.content_buf, tool_title_row, tool_title_row + 1, False)
                        if verify_line and verify_line[0]:
                            self.logger.info(f"Tool title at row {tool_title_row}: '{verify_line[0][:50]}'")
                    except Exception:
                        pass
                else:
                    # Fallback: find first non-empty line
                    tool_title_row = start_line + spacing_lines  # 0-indexed
                    for i in range(start_line, end_line):
                        try:
                            line = self.nvim.api.buf_get_lines(self.content_buf, i, i + 1, False)
                            if line and line[0] and line[0].strip():
                                tool_title_row = i
                                self.logger.info(f"Found tool title at row {i}: '{line[0][:50]}'")
                                break
                        except Exception:
                            pass
                
                # Fold start (1-indexed): tool_title_row + 1
                fold_start = tool_title_row + 1  # 1-indexed for fold
                fold_end = end_line
                self.logger.info(f"Creating fold: start={fold_start}, end={fold_end}, tool_title_row={tool_title_row} (0-indexed), start_line={start_line}, end_line={end_line}")
                self._create_fold(bufnr, fold_start, fold_end, fold_error=fold_error, tool_title_row=tool_title_row, content_type=content_type)

        except Exception as e:
            self.logger.error(f"Error writing to buffer in _append_and_scroll: {e}")
            return

        # Autoscroll to bottom only if autoscroll is enabled
        # Do this AFTER folding to ensure cursor is positioned correctly
        try:
            for win in self.nvim.windows:
                if win.buffer == self.content_buf:
                    try:
                        # Check if autoscroll is enabled for this buffer
                        autoscroll_enabled = self.nvim.api.buf_get_var(
                            self.content_buf, "agent_autoscroll_enabled"
                        )
                    except Exception:
                        # If variable doesn't exist, default to enabled
                        autoscroll_enabled = 1

                    # Only scroll if autoscroll is enabled and position is valid
                    if autoscroll_enabled:
                        try:
                            # If we created a fold, scroll to the line containing the fold (first line of folded region)
                            # Otherwise scroll to the end of the appended content
                            if fold and len(processed) > 1:
                                # Scroll to the first line of the fold (where the summary is)
                                win.cursor = (start_line + 1, 0)
                            else:
                                # Scroll to the end of appended content
                                if end_line > 0:
                                    win.cursor = (end_line, 0)
                        except Exception:
                            # Cursor position invalid, skip scrolling
                            pass
        except Exception:
            # Windows collection changed during iteration (e.g., window closed)
            # This is safe to ignore as the content was already appended
            pass

        # Restore focus to the previously active window
        if current_win and current_win.valid:
            try:
                self.nvim.current.window = current_win
            except Exception:
                pass

    def _create_fold(self, bufnr, start_line, end_line, fold_error=False, tool_title_row=None, content_type='llm'):
        """Create a fold in the buffer.

        Args:
            bufnr: Buffer number
            start_line: Start line (1-indexed) for fold
            end_line: End line (1-indexed) for fold
            fold_error: If True, highlight the fold header as an error (red)
            tool_title_row: Row number (0-indexed) where tool title is (for icon placement)
            content_type: Type of content ('tool', 'thinking', 'llm', etc.) - thinking folds don't get icons
        """
        try:
            # Use tool_title_row if provided (0-indexed), otherwise calculate from start_line
            # start_line is 1-indexed, convert to 0-indexed
            title_row = tool_title_row if tool_title_row is not None else (start_line - 1)
            self.logger.info(f"_create_fold called: bufnr={bufnr}, start_line={start_line} (1-indexed), end_line={end_line} (1-indexed), tool_title_row param={tool_title_row}, calculated title_row={title_row} (0-indexed)")
            # Verify end_line is correct
            if end_line < start_line:
                self.logger.error(f"ERROR: end_line ({end_line}) < start_line ({start_line})!")
                return
            
            # Add highlight to the first line (tool output title)
            # Use ErrorMsg for errors, OkMsg for success
            # title_row is 0-indexed, nvim_buf_add_highlight uses 0-indexed
            highlight_group = "ErrorMsg" if fold_error else "OkMsg"
            self.nvim.api.buf_add_highlight(
                bufnr, -1, highlight_group, title_row, 0, -1
            )

            # Create fold first
            self.nvim.exec_lua(
                "require('agent_nvim.folds').create_fold(...)",
                bufnr,
                start_line,
                end_line,
                None,
            )
            
            # Only add icons for tool folds, not thinking folds
            if content_type != 'thinking':
                # Add virtual text with icon at the right edge (like edit view toolbar)
                # Use same Nerd Font icons as edit_view.lua for consistency
                if fold_error:
                    icon = ""  # x mark
                else:
                    icon = ""  # ok mark
                # Use transparent highlight groups for icons
                icon_hl = "AgentToolFoldError" if fold_error else "AgentToolFoldOk"
                
                # Add debugging to see what's happening
                end_row_0_indexed = end_line - 1
                # Log exact values
                self.logger.info(f"Creating tool fold icon: bufnr={bufnr}, title_row={title_row}, end_row_0_indexed={end_row_0_indexed}, icon={icon!r}, icon_hl={icon_hl}")
                
                # Use Python API directly instead of Lua to avoid argument passing issues
                # Create extmark with virtual text at right edge
                # Format: virt_text is list of [text, highlight] pairs
                try:
                    extmark_id = self.nvim.api.buf_set_extmark(
                        bufnr,
                        self._tool_fold_ns,
                        title_row,  # 0-indexed row
                        0,  # Column 0
                        {
                            "virt_text": [[f" {icon} ", icon_hl]],
                            "virt_text_pos": "right_align",
                            "end_row": end_row_0_indexed,  # 0-indexed end row
                            "hl_mode": "combine",  # Combine with existing highlights for transparency
                        },
                    )
                    self.logger.info(f"Created tool fold icon extmark id={extmark_id} on row {title_row} using Python API")
                except Exception as e:
                    self.logger.error(f"Error creating tool fold icon extmark: {e}")
        except Exception as e:
            self.logger.error(f"Error creating fold: {e}")

    def _highlight_file_refs(self, start_line, end_line):
        """Highlight file references in the specified range.

        Args:
            start_line: Start line (0-indexed)
            end_line: End line (0-indexed, exclusive)
        """
        try:
            # Clear the user prompt namespace at the start to prepare for redraw
            try:
                self.nvim.api.buf_clear_namespace(
                    self.content_buf.number, self._user_prompt_ns, start_line, end_line
                )
            except Exception:
                pass

            if not self.content_buf or not self.content_buf.valid:
                return

            import re

            bufnr = self.content_buf.number
            lines = self.nvim.api.buf_get_lines(
                self.content_buf, start_line, end_line, False
            )

            # Pattern for file references like @path/to/file
            pattern = r"@[a-zA-Z0-9_./-]+/[a-zA-Z0-9_./-]*"

            # Clear any existing highlights in the range we're about to update
            self.nvim.api.buf_clear_namespace(
                bufnr, self._prompt_highlight_ns, start_line, end_line
            )

            for idx, line in enumerate(lines):
                line_num = start_line + idx
                for match in re.finditer(pattern, line):
                    start_col = match.start()
                    end_col = match.end()
                    # Use priority 10 so Directory highlights override Special
                    self.nvim.api.buf_add_highlight(
                        bufnr, 10, "Directory", line_num, start_col, end_col
                    )
        except Exception as e:
            self.logger.debug(f"Error highlighting file refs: {e}")

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
            lines = self.nvim.api.buf_get_lines(
                self.content_buf, start_line, end_line, False
            )

            # Patterns for special highlighting
            file_pattern = r"@[a-zA-Z0-9_./-]+"
            slash_pattern = r"/[a-z]+"

            # Helper to check if a position is inside any file reference
            def is_inside_file_ref(pos, file_ranges):
                for start, end in file_ranges:
                    if start <= pos < end:
                        return True
                return False

            # Look for user prompt section
            in_user_section = False
            user_section_start = None
            skip_next_empty = False

            for idx, line in enumerate(lines):
                line_num = start_line + idx

                # Check if this is an Agent header line (# Agent)
                if line.startswith("# Agent"):
                    # Use extmark with line_hl_group to highlight full line including EOL
                    self.nvim.api.buf_set_extmark(
                        bufnr,
                        self._user_prompt_ns,
                        line_num,
                        0,
                        {"line_hl_group": "CursorLineNr", "priority": 5},
                    )
                    continue

                # Check if this is a user header line (# Username, not # Agent)
                if line.startswith("# ") and not line.startswith("# Agent"):
                    in_user_section = True
                    user_section_start = line_num
                    skip_next_empty = True  # Skip the empty line after the header
                    # Use extmark with line_hl_group to highlight full line including EOL
                    self.nvim.api.buf_set_extmark(
                        bufnr,
                        self._user_prompt_ns,
                        line_num,
                        0,
                        {"line_hl_group": "CursorLineNr", "priority": 5},
                    )
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

                    # Mark the prompt text with Comment highlight
                    if line.strip():
                        self.nvim.api.buf_add_highlight(
                            bufnr, 0, "Comment", line_num, 0, -1
                        )

                    # Find all file reference ranges first (these take priority)
                    file_ranges = []
                    for match in re.finditer(file_pattern, line):
                        file_ranges.append((match.start(), match.end()))

                    # Highlight slash commands (but not if inside a file reference)
                    for match in re.finditer(slash_pattern, line):
                        start_col = match.start()
                        # Only highlight if preceded by start of line or space, and not inside file ref
                        if (
                            start_col == 0 or line[start_col - 1] == " "
                        ) and not is_inside_file_ref(start_col, file_ranges):
                            end_col = match.end()
                            self.nvim.api.buf_add_highlight(
                                bufnr, 10, "Special", line_num, start_col, end_col
                            )

                    # Highlight file references with higher priority
                    for start_col, end_col in file_ranges:
                        self.nvim.api.buf_add_highlight(
                            bufnr, 10, "Directory", line_num, start_col, end_col
                        )

                    # Add virtual text prefix for all lines (including blank lines)
                    self.nvim.api.buf_set_extmark(
                        bufnr,
                        self._user_prompt_ns,
                        line_num,
                        0,
                        {"virt_text": [["┃ ", "Comment"]], "virt_text_pos": "inline"},
                    )
        except Exception as e:
            self.logger.debug(f"Error highlighting user prompt: {e}")

    def init_thinking_section(self, bufnr):
        """Initialize thinking section with header and opening code fence.
        
        Args:
            bufnr: Buffer number
            
        Returns:
            Start line number (1-indexed) where thinking section begins
        """
        # Calculate spacing based on what came before
        spacing = self._calculate_spacing('thinking')
        
        # Build header lines
        header_lines = []
        for _ in range(spacing):
            header_lines.append("")
        header_lines.extend(["**Thinking**", "``````"])
        
        # Append header and get start line
        # Note: This is called from async context, use async_call for thread safety
        try:
            def init_sync():
                current_lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
                start_line = len(current_lines)
                
                # Append header
                self.nvim.api.buf_set_lines(self.content_buf, -1, -1, False, header_lines)
                
                # Update state
                self._last_output_type = 'thinking'
                
                # Store thinking start line for fold creation
                self._thinking_start_line = start_line + 1  # 1-indexed
            
            # Execute in main thread
            self.nvim.async_call(init_sync)
            
            # Calculate start line now (before async append happens)
            # We'll get the current line count and add spacing + header lines
            current_lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
            start_line = len(current_lines) + spacing + 1  # +1 for 1-indexed, spacing already accounted
            self._thinking_start_line = start_line
            
            return start_line
        except Exception as e:
            self.logger.error(f"Error initializing thinking section: {e}")
            return None
    
    def stream_thinking_content(self, text, bufnr):
        """Stream thinking content incrementally using Lua animation.
        
        Args:
            text: Text delta to append to thinking section
            bufnr: Buffer number
        """
        if not text:
            return
            
        # Escape text for Lua string
        escaped_text = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("'", "\\'")
        )
        
        # Use same streaming mechanism as LLM text
        lua_code = f"""
        local bufnr = {bufnr}
        local text = "{escaped_text}"

        -- Initialize animation queue if it doesn't exist
        if not _G.agent_stream_queue then
            _G.agent_stream_queue = {{}}
            _G.agent_stream_timer = nil
            _G.agent_stream_paused = false
        end

        -- Add text to queue
        table.insert(_G.agent_stream_queue, {{bufnr = bufnr, text = text}})

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

                -- Vary characters written: more natural variation
                local rand = math.random()
                local chars_to_write = 3
                if rand < 0.1 then
                    chars_to_write = 1  -- 10% very slow
                elseif rand < 0.25 then
                    chars_to_write = 2  -- 15% slow
                elseif rand < 0.6 then
                    chars_to_write = 3  -- 35% normal
                elseif rand < 0.8 then
                    chars_to_write = 4  -- 20% fast
                else
                    chars_to_write = 5  -- 20% very fast
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
            local base_interval = 8
            _G.agent_stream_timer:start(math.random(5, 10), base_interval, vim.schedule_wrap(timer_callback))
        end
        """
        try:
            self.nvim.async_call(self.nvim.exec_lua, lua_code)
        except Exception as e:
            self.logger.error(f"Error streaming thinking content: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
    
    def capture_thinking_end_line(self, bufnr):
        """Capture the current end line of thinking content.
        
        This should be called when LLM content is first detected, BEFORE any LLM
        content is written to the buffer. This ensures the fold ends at the correct
        location.
        
        Args:
            bufnr: Buffer number
        """
        try:
            # Capture the current end of thinking content (0-indexed)
            current_lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
            self._thinking_end_line = len(current_lines)  # 0-indexed
            self.logger.debug(f"Captured thinking end line: {self._thinking_end_line} (0-indexed)")
        except Exception as e:
            self.logger.error(f"Error capturing thinking end line: {e}")
    
    def finalize_thinking_section(self, bufnr):
        """Finalize thinking section by adding closing fence, blank line, and creating fold.
        
        Args:
            bufnr: Buffer number
            
        Note: This method should be called from async_call context (it's called from
        finalize_thinking() which is already wrapped in async_call).
        """
        try:
            # Use the captured thinking end line if available, otherwise calculate it
            # This ensures we use the line captured BEFORE any LLM content was written
            if hasattr(self, '_thinking_end_line') and self._thinking_end_line is not None:
                thinking_end_line = self._thinking_end_line  # 0-indexed
                self.logger.debug(f"Using captured thinking end line: {thinking_end_line} (0-indexed)")
            else:
                # Fallback: calculate from current buffer state
                # This should only happen if capture_thinking_end_line wasn't called
                import time
                time.sleep(0.05)  # Small delay to catch any last-minute writes
                current_lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
                thinking_end_line = len(current_lines)  # 0-indexed
                self.logger.warning(f"Using calculated thinking end line (capture not called): {thinking_end_line} (0-indexed)")
            
            # Add closing fence at the exact end of thinking content
            closing_fence = ["``````"]
            # Insert at the exact line where thinking content ends (thinking_end_line is 0-indexed)
            self.nvim.api.buf_set_lines(self.content_buf, thinking_end_line, thinking_end_line, False, closing_fence)
            
            # Get the line number of the closing fence (this is where the fold should end)
            # The closing fence is at thinking_end_line (0-indexed), so fold_end is thinking_end_line + 1 (1-indexed)
            closing_fence_line = thinking_end_line + 1  # 1-indexed for fold
            
            # Create fold if we have a start line (fold includes closing fence but not blank line)
            if hasattr(self, '_thinking_start_line') and self._thinking_start_line:
                fold_start = self._thinking_start_line  # Already 1-indexed
                # Fold end should be exactly the line with the closing fence
                fold_end = closing_fence_line  # This is the line with the closing fence (1-indexed)
                tool_title_row = self._thinking_start_line - 1  # Convert to 0-indexed
                self._create_fold(bufnr, fold_start, fold_end, fold_error=False, tool_title_row=tool_title_row, content_type='thinking')
            
            # Add blank line AFTER the fold (at the line after the closing fence)
            # Use -1 to append at the very end to ensure it's after everything
            current_end = len(self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False))
            self.nvim.api.buf_set_lines(self.content_buf, current_end, current_end, False, [""])
            
            # Update state - thinking is done, next content will be LLM
            # This ensures proper spacing for the next content
            self._last_output_type = 'thinking'
            
            # Clear thinking tracking lines
            if hasattr(self, '_thinking_start_line'):
                delattr(self, '_thinking_start_line')
            if hasattr(self, '_thinking_end_line'):
                delattr(self, '_thinking_end_line')
        except Exception as e:
            self.logger.error(f"Error finalizing thinking section: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def append_stream_lua_direct(self, text, bufnr):
        """Append text using Lua animation for smooth typing effect.

        Args:
            text: Text to append
            bufnr: Buffer number (must be passed in, can't access from async context)
        """
        # Handle initial spacing for agent responses based on spacing state
        if not self._agent_response_started and text:
            # Calculate spacing based on what came before
            spacing = self._calculate_spacing('llm')
            
            # Strip leading newlines from the text
            text = text.lstrip("\n")
            # If stripping leaves empty text, skip this chunk (don't set flag yet)
            if not text:
                return
            
            # Add spacing newlines based on state
            text = ("\n" * spacing) + text
            
            self._agent_response_started = True
            # Update state to reflect LLM output
            self._last_output_type = 'llm'

        # Escape text for Lua string
        escaped_text = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("'", "\\'")
        )

        # Use a timer to animate character-by-character
        lua_code = f"""
        local bufnr = {bufnr}
        local text = "{escaped_text}"

        -- Initialize animation queue if it doesn't exist
        if not _G.agent_stream_queue then
            _G.agent_stream_queue = {{}}
            _G.agent_stream_timer = nil
            _G.agent_stream_paused = false
        end

        -- Add text to queue
        table.insert(_G.agent_stream_queue, {{bufnr = bufnr, text = text}})

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

                -- Vary characters written: more natural variation
                local rand = math.random()
                local chars_to_write = 3
                if rand < 0.1 then
                    chars_to_write = 1  -- 10% very slow
                elseif rand < 0.25 then
                    chars_to_write = 2  -- 15% slow
                elseif rand < 0.6 then
                    chars_to_write = 3  -- 35% normal
                elseif rand < 0.8 then
                    chars_to_write = 4  -- 20% fast
                else
                    chars_to_write = 5  -- 20% very fast
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
            local base_interval = 8
            _G.agent_stream_timer:start(math.random(5, 10), base_interval, vim.schedule_wrap(timer_callback))
        end
        """
        try:
            self.nvim.async_call(self.nvim.exec_lua, lua_code)
        except Exception as e:
            self.logger.error(f"Error in _append_stream_lua: {e}")
            import traceback

            self.logger.error(traceback.format_exc())

    def append_cancel_message(self):
        """Queue cancellation message to be appended after streaming completes."""
        if hasattr(self, "content_buf") and self.content_buf and self.content_buf.valid:
            bufnr = self.content_buf.number

            # Add cancel message to the streaming queue so it appears after the response finishes
            # We need to escape the text for Lua
            lua_code = f"""
            -- Initialize stream queue if it doesn't exist
            if not _G.agent_stream_queue then
                _G.agent_stream_queue = {{}}
            end

            -- Queue the cancel message at the end
            table.insert(_G.agent_stream_queue, {{
                bufnr = {bufnr},
                text = "\\n\\n> **[Request cancelled by user]**",
                remove_last_line = false
            }})
            """
            try:
                self.nvim.async_call(self.nvim.exec_lua, lua_code)
            except Exception as e:
                self.logger.error(f"Error queueing cancel message: {e}")
                # Fallback: append directly if queue is not available
                lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
                response_started = False
                if len(lines) > 4:
                    for i in range(4, len(lines)):
                        if lines[i].strip():
                            response_started = True
                            break

                if response_started:
                    self.append_content(["", "> **[Request cancelled by user]**"])
                else:
                    self.append_content(["> **[Request cancelled by user]**"])

    def clear_welcome_message(self):
        """Remove the welcome message if it's the only content in the buffer.

        Returns:
            True if welcome message was cleared (first message), False otherwise
        """
        # Always stop the logo animation when submitting
        self.stop_logo_animation()

        if not self.content_buf or not self.content_buf.valid:
            return False

        lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)

        # Check if buffer contains only the welcome message
        if lines == self.WELCOME_MESSAGE:
            # Remove all the welcome message lines
            self.nvim.api.buf_set_lines(
                self.content_buf, 0, len(self.WELCOME_MESSAGE), False, []
            )
            return True
        return False

    def enable_render_markdown(self):
        """Enable render-markdown for the content buffer."""
        try:
            # Try to enable render-markdown if it's available
            self.nvim.command("silent! RenderMarkdown enable")
        except Exception:
            pass

    def animate_welcome_message(self, content_buf):
        """Animate the welcome message logo with a scanning effect.

        Args:
            content_buf: Buffer to animate in
        """
        try:
            bufnr = content_buf.number
            # Use the continuous scan animation (runs until user sends message)
            self.nvim.exec_lua(
                "require('agent_nvim.logo_animation').animate_logo_scan(...)",
                bufnr,
            )
        except Exception as e:
            self.logger.debug(f"Animation failed, falling back to static: {e}")
            # Fallback to static welcome message
            self.nvim.api.buf_set_lines(content_buf, 0, -1, False, self.WELCOME_MESSAGE)

    def stop_logo_animation(self):
        """Stop the logo animation (called when user submits a message)."""
        try:
            self.nvim.exec_lua("require('agent_nvim.logo_animation').stop_animation()")
        except Exception:
            pass

    def reset_agent_response_flag(self):
        """Reset the agent response started flag and Lua spacing check.
        
        Note: Does NOT reset _last_output_type - that is managed by append_content.
        The header's append_content call will set it to 'header' after appending.
        """
        self._agent_response_started = False
        # Don't reset _last_output_type here - let append_content manage it
        # This allows correct spacing calculation for the header based on
        # what came before (e.g., 'llm' from previous response)
        # Also reset the Lua spacing check for the new response
        try:
            self.nvim.exec_lua("_G.agent_stream_spacing_checked = false")
        except Exception:
            pass
    
    def set_output_type(self, output_type):
        """Manually set the last output type for spacing calculations.
        
        Args:
            output_type: Type to set ('header', 'tool', 'thinking', 'llm', 'user', or None)
                         'thinking' is ONLY for built-in reasoning (o1/glm-4), NOT MCP tools
        """
        self._last_output_type = output_type
    
    def reset_spacing_state(self):
        """Reset spacing state to initial values.
        
        Call this when clearing the buffer to ensure correct spacing for the next content.
        """
        self._last_output_type = None
        self._agent_response_started = False
        try:
            self.nvim.exec_lua("_G.agent_stream_spacing_checked = false")
        except Exception:
            pass

    def highlight_prompt_buffer(self):
        """Highlight file references and slash commands in the prompt buffer as user types."""
        try:
            if (
                not hasattr(self, "prompt_buf")
                or not self.prompt_buf
                or not self.prompt_buf.valid
            ):
                return

            import re

            bufnr = self.prompt_buf.number
            lines = self.nvim.api.buf_get_lines(self.prompt_buf, 0, -1, False)

            # Clear existing highlights (only in highlight namespace, preserving placeholder)
            self.nvim.api.buf_clear_namespace(bufnr, self._prompt_highlight_ns, 0, -1)

            # Pattern for slash commands like /help, /clear, /cancel
            slash_pattern = r"/[a-z]+"

            # Pattern for file references like @filename or @path/to/file
            file_pattern = r"@[a-zA-Z0-9_./-]+"

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
                    # Only highlight if preceded by start of line or space, and not inside file ref
                    if (
                        start_col == 0 or line[start_col - 1] == " "
                    ) and not is_inside_file_ref(start_col):
                        end_col = match.end()
                        self.nvim.api.buf_add_highlight(
                            bufnr,
                            self._prompt_highlight_ns,
                            "Special",
                            line_num,
                            start_col,
                            end_col,
                        )

                # Highlight file references
                for start_col, end_col in file_ranges:
                    self.nvim.api.buf_add_highlight(
                        bufnr,
                        self._prompt_highlight_ns,
                        "Directory",
                        line_num,
                        start_col,
                        end_col,
                    )
        except Exception as e:
            self.logger.debug(f"Error highlighting prompt buffer: {e}")

    def get_conversation_context(self) -> List[Dict]:
        """Extract current conversation context from content buffer.

        Returns:
            List of message dictionaries with 'role' and 'content' keys
        """
        try:
            if not self.content_buf or not self.content_buf.valid:
                return []

            # Get buffer content
            lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
            content = "\n".join(lines)

            # Split by message markers and create conversation history
            conversation_history = []
            current_message = ""
            current_role = "user"

            for line in lines:
                if line.startswith("## "):
                    # Save previous message if any
                    if current_message.strip():
                        conversation_history.append(
                            {"role": current_role, "content": current_message.strip()}
                        )
                    # Start new message
                    current_role = "assistant" if "Assistant" in line else "user"
                    current_message = line
                else:
                    current_message += "\n" + line if current_message else line

            # Add final message
            if current_message.strip():
                conversation_history.append(
                    {"role": current_role, "content": current_message.strip()}
                )

            return conversation_history

        except Exception as e:
            self.logger.error(f"Error getting conversation context: {e}")
            return []

    def apply_compacted_context(self, summary: str, nvim):
        """Replace buffer content with compacted summary.

        Args:
            summary: Compacted summary to apply
            nvim: Neovim instance
        """
        try:
            if not self.content_buf or not self.content_buf.valid:
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

            nvim.api.buf_set_lines(self.content_buf, 0, -1, False, new_content)

            self.logger.info("Applied compacted context to conversation")

        except Exception as e:
            self.logger.error(f"Error applying compacted context: {e}")
            nvim.err_write(f"Error applying compacted context: {e}\n")

    def add_compaction_metadata(self, metadata: Dict, nvim):
        """Add metadata about compaction to buffer.

        Args:
            metadata: Dictionary with compaction metadata
            nvim: Neovim instance
        """
        try:
            if not self.content_buf or not self.content_buf.valid:
                return

            # Create metadata block
            metadata_lines = [
                "",
                "<!-- Compaction Metadata -->",
                f"<!-- Timestamp: {metadata.get('timestamp', 'Unknown')} -->",
                f"<!-- Original tokens: {metadata.get('original_tokens', 'Unknown')} -->",
                f"<!-- Compacted tokens: {metadata.get('compacted_tokens', 'Unknown')} -->",
                f"<!-- Reduction: {metadata.get('reduction_percent', 'Unknown')}% -->",
                "<!-- End Compaction Metadata -->",
                "",
            ]

            # Append to buffer
            nvim.api.buf_set_lines(self.content_buf, -1, -1, False, metadata_lines)

        except Exception as e:
            self.logger.error(f"Error adding compaction metadata: {e}")
