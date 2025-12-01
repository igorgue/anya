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
        # Create namespaces for highlights
        self._user_prompt_ns = nvim.api.create_namespace("agent_user_prompt")
        self._prompt_highlight_ns = nvim.api.create_namespace("agent_prompt_highlight")
        self._username_highlight_ns = nvim.api.create_namespace(
            "agent_username_highlight"
        )
        # File backups for undo support when git apply can't be used
        # Maps absolute filepath -> original content (or None if file didn't exist)
        self._file_backups = {}

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

        # Create split for prompt
        self.nvim.command("botright split")
        self.nvim.command("resize 5")
        self.nvim.api.win_set_buf(0, prompt_buf)

        # Store buffer handles
        self.content_buf = content_buf
        self.prompt_buf = prompt_buf

        # Add welcome message if empty (with animation)
        if len(content_buf) <= 1:
            self.animate_welcome_message(content_buf)

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

    def render_diff_block(self, patch_str):
        """Render a diff block in the content buffer using Lua.

        Args:
            patch_str: Patch content as string
        """
        if (
            not hasattr(self, "content_buf")
            or not self.content_buf
            or not self.content_buf.valid
        ):
            return

        try:
            self.nvim.exec_lua(
                "local args = {...}; require('agent_nvim.diff_view').render_diff(args[1], args[2])",
                self.content_buf.number,
                patch_str,
            )
            # Scroll to bottom to show new content
            self._scroll_to_bottom()
        except Exception as e:
            self.logger.error(f"Error rendering diff block: {e}")
            self.nvim.out_write(f"Diff render error: {e}\n")
            # Fallback to simple append
            self.append_content(["```diff", patch_str, "```"])

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

    def apply_pending_patches(self):
        """Apply all pending patches in the content buffer that are marked as ACCEPT."""
        if (
            not hasattr(self, "content_buf")
            or not self.content_buf
            or not self.content_buf.valid
        ):
            return

        try:
            # Get patches from Lua
            patches = self.nvim.exec_lua(
                "return require('agent_nvim.diff_view').get_patches(...)",
                self.content_buf.number,
            )

            if not patches:
                return

            applied_count = 0
            for patch in patches:
                state = patch.get("state", 1)  # Default to ACCEPT (1)
                content = patch.get("content", "")

                # STATE_ALWAYS_ACCEPT = 0, STATE_ACCEPT = 1
                if state == 0 or state == 1:
                    if self._apply_single_patch(content):
                        applied_count += 1

            if applied_count > 0:
                self.append_content(["", f"> **Applied {applied_count} patch(es)**"])

        except Exception as e:
            self.logger.error(f"Error applying pending patches: {e}")
            self.nvim.err_write(f"Error applying patches: {e}\n")

    def _can_use_git_apply(self, filepath: str, cwd: str) -> bool:
        """Check if a file can be patched using git apply.

        Git apply works for:
        - Files that are tracked and unmodified (clean)
        - Files that are staged (changes in index)

        Git apply fails for:
        - Untracked files (not in git)
        - Files with unstaged modifications

        Args:
            filepath: Relative path to the file
            cwd: Current working directory (git repo root)

        Returns:
            True if git apply can be used, False if direct file manipulation needed
        """
        try:
            # Check git status for this specific file
            result = subprocess.run(
                ["git", "status", "--porcelain", "--", filepath],
                cwd=cwd,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                # Not a git repo or git error - use direct method
                return False

            status = result.stdout.strip()

            if not status:
                # Empty status means file is tracked and clean - git apply works
                return True

            # Parse status codes (first two characters)
            # XY format: X=index status, Y=worktree status
            if len(status) >= 2:
                worktree_status = status[1]

                # Untracked file (??) - git apply won't work
                if status.startswith("??"):
                    return False

                # File has unstaged modifications ( M, MM, AM, etc.)
                # The worktree has changes not in index - git apply may fail
                if worktree_status == "M":
                    return False

                # Staged changes only (M , A , etc.) - git apply works
                if worktree_status == " ":
                    return True

            # Default to trying git apply
            return True

        except Exception as e:
            self.logger.warning(f"Error checking git status for {filepath}: {e}")
            return False

    def _parse_patch_files(self, patch_content: str, cwd: str) -> list:
        """Parse patch content to extract target file paths.

        Args:
            patch_content: The unified diff patch content
            cwd: Current working directory for resolving paths

        Returns:
            List of tuples: (relative_path, absolute_path, is_new_file)
        """
        files = []
        lines = patch_content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            # Look for +++ b/path or +++ path (target file)
            if line.startswith("+++ "):
                target = line[4:].strip()

                # Remove b/ prefix if present
                if target.startswith("b/"):
                    target = target[2:]

                # Check if this is a new file by looking at the --- line
                is_new_file = False
                if i > 0:
                    prev_line = lines[i - 1]
                    if prev_line.startswith("--- /dev/null") or prev_line.startswith(
                        "--- a/dev/null"
                    ):
                        is_new_file = True

                # Skip /dev/null targets (file deletions)
                if target != "/dev/null" and not target.endswith("/dev/null"):
                    abs_path = os.path.join(cwd, target)
                    files.append((target, abs_path, is_new_file))

            i += 1

        return files

    def _parse_hunks(self, patch_content: str, target_file: str) -> list:
        """Parse patch hunks for a specific file.

        Args:
            patch_content: The unified diff patch content
            target_file: The target file path (relative, without b/ prefix)

        Returns:
            List of hunk dicts with keys: old_start, old_count, new_start, new_count, lines
        """
        hunks = []
        lines = patch_content.split("\n")
        in_target_file = False
        current_hunk = None

        for line in lines:
            # Check if we're entering a new file section
            if line.startswith("+++ "):
                target = line[4:].strip()
                if target.startswith("b/"):
                    target = target[2:]
                in_target_file = target == target_file

            # Parse hunk header
            elif line.startswith("@@ ") and in_target_file:
                # Save previous hunk if exists
                if current_hunk:
                    hunks.append(current_hunk)

                # Parse @@ -old_start,old_count +new_start,new_count @@
                match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if match:
                    current_hunk = {
                        "old_start": int(match.group(1)),
                        "old_count": int(match.group(2) or 1),
                        "new_start": int(match.group(3)),
                        "new_count": int(match.group(4) or 1),
                        "lines": [],
                    }

            # Collect hunk lines
            elif current_hunk and in_target_file:
                if line.startswith(("+", "-", " ")):
                    current_hunk["lines"].append(line)
                elif line.startswith("\\"):
                    # "\ No newline at end of file" - note but don't add
                    current_hunk["lines"].append(line)

        # Don't forget the last hunk
        if current_hunk:
            hunks.append(current_hunk)

        return hunks

    def _apply_hunks_to_content(self, content: str, hunks: list) -> str:
        """Apply parsed hunks to file content.

        Args:
            content: Original file content
            hunks: List of parsed hunks from _parse_hunks

        Returns:
            Modified file content
        """
        lines = content.splitlines(keepends=True)

        # Track offset as we apply hunks (line numbers shift)
        offset = 0

        for hunk in hunks:
            old_start = hunk["old_start"] - 1 + offset  # Convert to 0-indexed
            old_count = hunk["old_count"]

            # Build new lines from hunk
            new_lines = []
            for hunk_line in hunk["lines"]:
                if hunk_line.startswith("+") and not hunk_line.startswith("+++"):
                    # Added line - include it (remove the + prefix)
                    new_lines.append(hunk_line[1:] + "\n")
                elif hunk_line.startswith(" "):
                    # Context line - include it
                    new_lines.append(hunk_line[1:] + "\n")
                elif hunk_line.startswith("\\"):
                    # "\ No newline at end of file" - remove newline from last line
                    if new_lines:
                        new_lines[-1] = new_lines[-1].rstrip("\n")
                # Lines starting with - are removed (not added to new_lines)

            # Replace old lines with new lines
            lines[old_start : old_start + old_count] = new_lines

            # Update offset for next hunk
            offset += len(new_lines) - old_count

        return "".join(lines)

    def _apply_patch_directly(self, patch_content: str, cwd: str) -> bool:
        """Apply patch using direct file manipulation.

        Args:
            patch_content: The unified diff patch content
            cwd: Current working directory

        Returns:
            True if successful, False otherwise
        """
        try:
            files = self._parse_patch_files(patch_content, cwd)

            for rel_path, abs_path, is_new_file in files:
                # Backup original content before modifying
                if os.path.exists(abs_path):
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        self._file_backups[abs_path] = f.read()
                else:
                    # File doesn't exist - mark as None for undo (will delete)
                    self._file_backups[abs_path] = None

                if is_new_file:
                    # New file - extract content from + lines
                    hunks = self._parse_hunks(patch_content, rel_path)
                    new_content = []
                    for hunk in hunks:
                        for line in hunk["lines"]:
                            if line.startswith("+") and not line.startswith("+++"):
                                new_content.append(line[1:])

                    # Create parent directories if needed
                    parent_dir = os.path.dirname(abs_path)
                    if parent_dir and not os.path.exists(parent_dir):
                        os.makedirs(parent_dir)

                    with open(abs_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(new_content))
                        if new_content:
                            f.write("\n")
                else:
                    # Existing file - apply hunks
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()

                    hunks = self._parse_hunks(patch_content, rel_path)
                    new_content = self._apply_hunks_to_content(content, hunks)

                    with open(abs_path, "w", encoding="utf-8") as f:
                        f.write(new_content)

                self.logger.info(f"Applied patch directly to {abs_path}")

            return True

        except Exception as e:
            self.logger.error(f"Error applying patch directly: {e}")
            self.nvim.err_write(f"Error applying patch directly: {e}\n")
            return False

    def _restore_backups(self) -> bool:
        """Restore files from backups (undo operation).

        Returns:
            True if all files restored successfully, False otherwise
        """
        try:
            for abs_path, original_content in self._file_backups.items():
                if original_content is None:
                    # File was newly created - delete it
                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                        self.logger.info(f"Deleted newly created file: {abs_path}")
                else:
                    # Restore original content
                    with open(abs_path, "w", encoding="utf-8") as f:
                        f.write(original_content)
                    self.logger.info(f"Restored original content: {abs_path}")

            self._file_backups.clear()
            return True

        except Exception as e:
            self.logger.error(f"Error restoring backups: {e}")
            self.nvim.err_write(f"Error restoring backups: {e}\n")
            return False

    def _apply_single_patch(self, patch_content, reverse=False):
        """Apply a single patch content string.

        Uses git apply for clean tracked files, falls back to direct file
        manipulation for untracked or modified files.

        Args:
            patch_content: The patch content
            reverse: If True, apply in reverse (undo/reject)

        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info(
                f"Applying patch content (len={len(patch_content)}, reverse={reverse}):\n{patch_content}"
            )

            cwd = self.nvim.call("getcwd")

            # Handle reverse/undo operation
            if reverse:
                # If we have backups, use them (from direct apply)
                if self._file_backups:
                    return self._restore_backups()

                # Otherwise try git apply --reverse
                with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
                    tmp.write(patch_content)
                    tmp_path = tmp.name

                cmd = [
                    "git",
                    "apply",
                    "--ignore-space-change",
                    "--ignore-whitespace",
                    "--reverse",
                    tmp_path,
                ]

                proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
                os.remove(tmp_path)

                if proc.returncode == 0:
                    return True
                else:
                    self.logger.error(f"Failed to reverse patch: {proc.stderr}")
                    self.nvim.err_write(
                        f"Failed to reverse patch: {proc.stderr.strip()}\n"
                    )
                    return False

            # Forward apply - determine which method to use
            files = self._parse_patch_files(patch_content, cwd)

            # Check if all files can use git apply
            use_git_apply = True
            for rel_path, abs_path, is_new_file in files:
                if is_new_file:
                    # New files - check if path already exists as untracked
                    if os.path.exists(abs_path):
                        use_git_apply = False
                        break
                else:
                    if not self._can_use_git_apply(rel_path, cwd):
                        use_git_apply = False
                        break

            if use_git_apply:
                # Try git apply
                with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
                    tmp.write(patch_content)
                    tmp_path = tmp.name

                cmd = [
                    "git",
                    "apply",
                    "--ignore-space-change",
                    "--ignore-whitespace",
                    tmp_path,
                ]

                proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
                os.remove(tmp_path)

                if proc.returncode == 0:
                    self.logger.info("Applied patch using git apply")
                    return True
                else:
                    # Git apply failed - fall back to direct method
                    self.logger.warning(
                        f"git apply failed ({proc.stderr.strip()}), trying direct method"
                    )
                    return self._apply_patch_directly(patch_content, cwd)
            else:
                # Use direct file manipulation
                self.logger.info(
                    "Using direct file manipulation (untracked or modified files)"
                )
                return self._apply_patch_directly(patch_content, cwd)

        except Exception as e:
            self.logger.error(f"Exception applying patch: {e}")
            self.nvim.err_write(f"Exception applying patch: {e}\n")
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

    def append_content(self, lines, fold=False, fold_error=False):
        """Append one or more lines to the content buffer.

        Args:
            lines: List of strings to append
            fold: If True, fold the appended content immediately
            fold_error: If True, highlight the fold header as an error (red)
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
                self._append_and_scroll(processed, fold=fold, fold_error=fold_error)

            # Write the processed list to the buffer
            self.nvim.async_call(wrapped_append)
            # Enable render-markdown after content is added
            self.nvim.async_call(self.enable_render_markdown)

    def _append_and_scroll(self, processed, fold=False, fold_error=False):
        """Helper to append lines and autoscroll content buffer.

        Args:
            processed: List of lines to append
            fold: If True, create a fold for the appended lines
            fold_error: If True, highlight the fold header as an error (red)
        """
        if (
            not hasattr(self, "content_buf")
            or not self.content_buf
            or not self.content_buf.valid
        ):
            return

        # Get current lines to determine if buffer is empty
        current_lines = self.nvim.api.buf_get_lines(self.content_buf, 0, -1, False)
        start_line = len(current_lines)

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

        # Highlight file references in appended lines (higher priority overrides Special)
        self._highlight_file_refs(start_line, end_line)

        # Highlight user prompts (lower priority so file refs can override)
        self._highlight_user_prompt(start_line, end_line)

        # Create fold if requested BEFORE autoscroll
        # This ensures the window is positioned correctly after folding
        if fold and len(processed) > 1:
            bufnr = self.content_buf.number
            # start_line is 0-indexed count, so +1 for 1-indexed vim line
            fold_start = start_line + 1
            fold_end = end_line
            self._create_fold(bufnr, fold_start, fold_end, fold_error=fold_error)

        # Autoscroll to bottom only if autoscroll is enabled
        # Do this AFTER folding to ensure cursor is positioned correctly
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

        # Restore focus to the previously active window
        if current_win and current_win.valid:
            try:
                self.nvim.current.window = current_win
            except Exception:
                pass

    def _create_fold(self, bufnr, start_line, end_line, fold_error=False):
        """Create a fold in the buffer.

        Args:
            bufnr: Buffer number
            start_line: Start line (1-indexed)
            end_line: End line (1-indexed)
            fold_error: If True, highlight the fold header as an error (red)
        """
        try:
            # Add highlight to the first line (tool output title)
            # Use ErrorMsg for errors, OkMsg for success
            # start_line is 1-indexed, nvim_buf_add_highlight uses 0-indexed
            highlight_group = "ErrorMsg" if fold_error else "OkMsg"
            self.nvim.api.buf_add_highlight(
                bufnr, -1, highlight_group, start_line - 1, 0, -1
            )

            self.nvim.exec_lua(
                "require('agent_nvim.folds').create_fold(...)",
                bufnr,
                start_line,
                end_line,
                None,
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

    def append_stream_lua_direct(self, text, bufnr):
        """Append text using Lua animation for smooth typing effect.

        Args:
            text: Text to append
            bufnr: Buffer number (must be passed in, can't access from async context)
        """
        # Handle initial spacing for agent responses
        # The header ends with a blank line. We need to preserve it by ensuring
        # content starts on a NEW line (after the blank line).
        if not self._agent_response_started and text:
            # Strip leading newlines - we'll add exactly one to ensure proper spacing
            text = text.lstrip("\n")
            # If stripping leaves empty text, skip this chunk (don't set flag yet)
            if not text:
                return
            # Prepend newline so content starts AFTER the blank line, not ON it
            text = "\n" + text
            self._agent_response_started = True

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
        """Reset the agent response started flag and Lua spacing check."""
        self._agent_response_started = False
        # Also reset the Lua spacing check for the new response
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
