"""Preview modal for CompactAgent using Snacks.win or fallback."""

import time

# Try to import typing, with fallback for older Python versions
try:
    from typing import Optional
except ImportError:
    # Fallback for older Python versions
    Optional = type


class CompactPreviewModal:
    """
    Manages the Snacks.win-based preview interface for context compaction.
    """
    
    def __init__(self, nvim, logger):
        self.nvim = nvim
        self.logger = logger
        self.snacks_available = self._check_snacks()
        self.decision = None
        self.edited_summary = None
        
    def _check_snacks(self) -> bool:
        """Check if Snacks.nvim is available."""
        try:
            # Try to require snacks module
            result = self.nvim.exec_lua("return pcall(require, 'snacks')")
            return result[0] if isinstance(result, tuple) else result
        except Exception:
            return False
    
    def show_preview(self, original_context: str, compacted_summary: str) -> bool:
        """Show preview modal and return user approval status.
        
        Args:
            original_context: Original conversation context
            compacted_summary: Generated compacted summary
            
        Returns:
            True if user accepts compaction, False otherwise
        """
        try:
            # Temporarily auto-apply to avoid async switching issues
            self.logger.info("Auto-applying compaction (preview temporarily disabled)")
            
            # Reset decision state
            self.decision = "accept"
            self.edited_summary = None
            
            return True  # Auto-approve for now
                
        except Exception as e:
            self.logger.error(f"Error showing preview: {e}")
            self.nvim.err_write(f"Error showing compaction preview: {e}\n")
            return False
    
    def _show_snacks_preview(self, original_context: str, compacted_summary: str) -> bool:
        """Show preview using Snacks.win."""
        # Create comparison content
        original_lines = len(original_context.split('\n'))
        summary_lines = len(compacted_summary.split('\n'))
        original_tokens = self._estimate_tokens(original_context)
        summary_tokens = self._estimate_tokens(compacted_summary)
        reduction_pct = (1 - summary_tokens / original_tokens) * 100 if original_tokens > 0 else 0
        
        header = f"## Context Compaction Preview\n\n"
        header += f"**Original:** {original_lines:,} lines, ~{original_tokens:,} tokens\n"
        header += f"**Compacted:** {summary_lines:,} lines, ~{summary_tokens:,} tokens\n"
        header += f"**Reduction:** {reduction_pct:.1f}% smaller\n\n"
        
        # Create content with sections
        content = [
            header,
            "## Original Context (excerpt)",
            "```",
            original_context[:1000] + ("..." if len(original_context) > 1000 else ""),
            "```",
            "",
            "## Compacted Summary",
            "```",
            compacted_summary,
            "```",
            "",
            "## Actions",
            "<Enter> or y - Accept compaction",
            "<Esc> or n - Cancel", 
            "e - Edit summary",
            "r - Regenerate with different settings",
            "q - Quit"
        ]
        
        # Setup global variables for decision tracking
        self.nvim.vars['agent_compact_decision'] = None
        self.nvim.vars['agent_compact_edited_summary'] = None
        
        # Show using Snacks.win
        lua_code = f"""
        local snacks = require('snacks')
        
        local content = {repr('\n'.join(content))}
        
        local win = snacks.win({{
            title = " Context Compaction Preview ",
            width = math.floor(vim.o.columns * 0.8),
            height = math.floor(vim.o.lines * 0.8),
            border = "rounded",
            style = "split",
            position = "float",
            enter = true,
            buf = {{
                filetype = "markdown",
                modifiable = false,
            }},
            keys = {{
                ["<Enter>"] = function()
                    vim.g.agent_compact_decision = "accept"
                    win:close()
                end,
                ["y"] = function()
                    vim.g.agent_compact_decision = "accept"
                    win:close()
                end,
                ["<Esc>"] = function()
                    vim.g.agent_compact_decision = "cancel"
                    win:close()
                end,
                ["n"] = function()
                    vim.g.agent_compact_decision = "cancel"
                    win:close()
                end,
                ["e"] = function()
                    vim.g.agent_compact_decision = "edit"
                    win:close()
                end,
                ["r"] = function()
                    vim.g.agent_compact_decision = "regenerate"
                    win:close()
                end,
                ["q"] = function()
                    vim.g.agent_compact_decision = "cancel"
                    win:close()
                end,
            }}
        }})
        
        -- Set buffer content
        local buf = win.buf
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(content, "\\n"))
        
        -- Wait for window to close
        local function wait_for_decision()
            while vim.g.agent_compact_decision == nil do
                vim.cmd("redraw")
                vim.defer_fn(function() end, 100)
            end
        end
        
        wait_for_decision()
        """
        
        self.nvim.exec_lua(lua_code)
        
        # Wait for decision
        return self._wait_for_decision(compacted_summary)
    
    def _show_fallback_preview(self, original_context: str, compacted_summary: str) -> bool:
        """Show preview using native Neovim floating windows."""
        # Create buffer for preview
        buf = self.nvim.api.create_buf(False, True)
        
        # Create content
        original_lines = len(original_context.split('\n'))
        summary_lines = len(compacted_summary.split('\n'))
        original_tokens = self._estimate_tokens(original_context)
        summary_tokens = self._estimate_tokens(compacted_summary)
        reduction_pct = (1 - summary_tokens / original_tokens) * 100 if original_tokens > 0 else 0
        
        content = [
            "## Context Compaction Preview",
            "",
            f"Original: {original_lines:,} lines, ~{original_tokens:,} tokens",
            f"Compacted: {summary_lines:,} lines, ~{summary_tokens:,} tokens", 
            f"Reduction: {reduction_pct:.1f}% smaller",
            "",
            "## Compacted Summary",
            "```",
            compacted_summary,
            "```",
            "",
            "Actions:",
            "y - Accept compaction",
            "n - Cancel",
            "e - Edit summary",
        ]
        
        self.nvim.api.buf_set_lines(buf, 0, -1, False, content)
        self.nvim.api.buf_set_option(buf, 'filetype', 'markdown')
        self.nvim.api.buf_set_option(buf, 'modifiable', False)
        
        # Create window
        width = min(80, self.nvim.api.get_option('columns') - 4)
        height = min(30, self.nvim.api.get_option('lines') - 4)
        
        win_config = {
            'relative': 'editor',
            'width': width,
            'height': height,
            'col': (self.nvim.api.get_option('columns') - width) // 2,
            'row': (self.nvim.api.get_option('lines') - height) // 2,
            'border': 'rounded',
            'style': 'minimal',
            'title': ' Context Compaction Preview ',
        }
        
        win = self.nvim.api.open_win(buf, True, win_config)
        
        # Setup keybindings
        self.nvim.api.buf_set_keymap(buf, 'n', 'y', 
            ':lua vim.g.agent_compact_decision = "accept"<CR>:close<CR>', 
            {'noremap': True, 'silent': True})
        self.nvim.api.buf_set_keymap(buf, 'n', 'n', 
            ':lua vim.g.agent_compact_decision = "cancel"<CR>:close<CR>', 
            {'noremap': True, 'silent': True})
        self.nvim.api.buf_set_keymap(buf, 'n', '<Esc>', 
            ':lua vim.g.agent_compact_decision = "cancel"<CR>:close<CR>', 
            {'noremap': True, 'silent': True})
        self.nvim.api.buf_set_keymap(buf, 'n', 'e', 
            ':lua vim.g.agent_compact_decision = "edit"<CR>:close<CR>', 
            {'noremap': True, 'silent': True})
        self.nvim.api.buf_set_keymap(buf, 'n', 'q', 
            ':lua vim.g.agent_compact_decision = "cancel"<CR>:close<CR>', 
            {'noremap': True, 'silent': True})
        
        # Setup global variables for decision tracking
        self.nvim.vars['agent_compact_decision'] = None
        self.nvim.vars['agent_compact_edited_summary'] = None
        
        # Wait for decision
        return self._wait_for_decision(compacted_summary)
    
    def _wait_for_decision(self, default_summary: str) -> bool:
        """Wait for user decision and return result."""
        try:
            # Use a simple non-blocking approach
            # The popup will handle user interaction and set the decision
            # We'll check for the decision without blocking
            max_attempts = 6000  # 60 seconds * 100 attempts per second
            attempts = 0
            
            while attempts < max_attempts:
                # Check if decision was made
                decision = self.nvim.vars.get('agent_compact_decision', None)
                if decision:
                    break
                    
                # Very short non-blocking delay
                attempts += 1
                time.sleep(0.01)  # 10ms
                
            if not decision:
                self.logger.warning("Preview decision timeout after 60 seconds")
                return False
            
            self.decision = decision
            
            if decision == "accept":
                return True
            elif decision == "edit":
                # Get edited summary
                edited = self.nvim.vars.get('agent_compact_edited_summary', None)
                if edited:
                    self.edited_summary = edited
                    return True
                else:
                    # If no edited summary, use default
                    self.edited_summary = default_summary
                    return True
            elif decision == "regenerate":
                # Signal that regeneration is needed
                return "regenerate"
            else:
                return False
                
        except Exception as e:
            self.logger.error(f"Error waiting for decision: {e}")
            return False
    
    def get_decision(self) -> tuple[str, Optional[str]]:
        """Get the user's decision and any edited summary.
        
        Returns:
            Tuple of (decision_type, edited_summary_or_none)
            decision_type can be: "accept", "cancel", "regenerate"
        """
        return self.decision, self.edited_summary
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        # Rough estimation: ~4 characters per token
        return len(text) // 4
    
    def show_edit_interface(self, summary: str) -> Optional[str]:
        """Show interface for editing the summary.
        
        Args:
            summary: Current summary to edit
            
        Returns:
            Edited summary or None if cancelled
        """
        try:
            # Create a new buffer for editing
            buf = self.nvim.api.create_buf(True, False)
            
            # Set buffer content
            lines = summary.split('\n')
            self.nvim.api.buf_set_lines(buf, 0, -1, False, lines)
            
            # Set buffer options
            self.nvim.api.buf_set_option(buf, 'filetype', 'markdown')
            self.nvim.api.buf_set_option(buf, 'buftype', 'acwrite')
            self.nvim.api.buf_set_name(buf, 'AgentCompactEdit')
            
            # Create window
            win_config = {
                'relative': 'editor',
                'width': min(80, self.nvim.api.get_option('columns') - 4),
                'height': min(20, self.nvim.api.get_option('lines') - 4),
                'col': (self.nvim.api.get_option('columns') - win_config['width']) // 2,
                'row': (self.nvim.api.get_option('lines') - win_config['height']) // 2,
                'border': 'rounded',
                'style': 'minimal',
                'title': ' Edit Compacted Summary ',
            }
            
            win = self.nvim.api.open_win(buf, True, win_config)
            
            # Setup keybindings
            self.nvim.api.buf_set_keymap(buf, 'n', '<Enter>', 
                ':lua vim.g.agent_compact_edited_summary = table.concat(vim.api.nvim_buf_get_lines(0, 0, -1, false), "\\n")<CR>:close<CR>', 
                {'noremap': True, 'silent': True})
            
            self.nvim.api.buf_set_keymap(buf, 'n', '<Esc>', 
                ':lua vim.g.agent_compact_decision = "cancel"<CR>:close<CR>', 
                {'noremap': True, 'silent': True})
            
            # Setup commands
            self.nvim.api.buf_create_user_command(buf, 'Write', 
                'lua vim.g.agent_compact_edited_summary = table.concat(vim.api.nvim_buf_get_lines(0, 0, -1, false), "\\n")', 
                {})
            
            # Instructions
            instructions = [
                "# Edit Summary",
                "",
                "Make your changes to the compacted summary above.",
                "Press <Enter> to save and accept, <Esc> to cancel.",
                "",
                "=== Summary ==="
            ]
            
            self.nvim.api.buf_set_lines(buf, 0, 0, False, instructions)
            self.nvim.api.buf_set_lines(buf, len(instructions), -1, False, lines)
            
            # Wait for user to finish editing
            self.nvim.command('normal G')
            
            return None  # The actual edited content will be retrieved by _wait_for_decision
            
        except Exception as e:
            self.logger.error(f"Error showing edit interface: {e}")
            self.nvim.err_write(f"Error showing edit interface: {e}\n")
            return None


def repr(obj) -> str:
    """Helper function to convert object to Lua string representation."""
    if isinstance(obj, str):
        # Escape special characters for Lua
        return '"' + obj.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    elif isinstance(obj, (list, tuple)):
        return '{' + ', '.join(repr(item) for item in obj) + '}'
    elif isinstance(obj, dict):
        return '{' + ', '.join(f'[{repr(k)}] = {repr(v)}' for k, v in obj.items()) + '}'
    else:
        return str(obj)