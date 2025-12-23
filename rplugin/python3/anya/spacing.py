"""Spacing manager for Anya chat content.

Ensures consistent spacing between different content types and enforces
marker isolation rules.
"""

import re
from enum import Enum
from . import markers


class ContentType(Enum):
    TEXT = "text"
    MARKER = "marker"
    TOOL_HEADER = "tool_header"
    TOOL_OUTPUT = "tool_output"
    THINKING = "thinking"
    MESSAGE_BOUNDARY = "message_boundary"
    EDIT_BLOCK = "edit_block"


class SpacingManager:
    """Manages spacing between content blocks in the chat buffer."""

    def __init__(self):
        self._last_content_type = None

    def ensure_marker_isolation(self, text: str) -> str:
        """Ensure all markers in the text are on their own lines.

        Args:
            text: The text to process.

        Returns:
            Text with markers isolated on separate lines.
        """
        if not text:
            return text

        # If no markers are present, just return text as-is
        if "<!--" not in text:
            return text

        # Match any marker pattern
        marker_pattern = r"(<!-- (?:at|am): .*? -->)"

        # 1. Process each line: if marker is on a line with content, split it up
        lines = text.split("\n")
        normalized_lines = []

        for line in lines:
            stripped = line.strip()
            match = re.search(marker_pattern, line)
            if match:
                # Marker detected - check if it's the only thing on this line
                # (ignoring whitespace)
                if line.strip() == match.group(1):
                    # Marker is already on its own line (just whitespace around it)
                    normalized_lines.append(match.group(1))
                else:
                    # Marker is on a line with content - need to split
                    marker = match.group(1)
                    before = line[: match.start()]
                    after = line[match.end() :]

                    # Add content before marker (if any)
                    if before.strip():
                        normalized_lines.append(before.rstrip())
                    # Add marker on its own line
                    normalized_lines.append(marker)
                    # Add content after marker (if any)
                    if after.strip():
                        normalized_lines.append(after.lstrip())
            else:
                # Non-marker line - preserve as-is
                normalized_lines.append(line)

        # 2. Rejoin
        result = "\n".join(normalized_lines)

        # 3. Add blank line before first marker if needed (only if there's content before it)
        if len(normalized_lines) > 0 and (normalized_lines[0].startswith("<!--")):
            # Check if there's non-whitespace content before this marker
            has_content_before = False
            for line in normalized_lines:
                if line.startswith("<!--"):
                    # Found marker line, stop
                    break
                if line.strip():
                    has_content_before = True
            if has_content_before:
                result = "\n" + result

        # 4. Collapse multiple newlines (reduce 3+ to 2)
        result = re.sub(r"\n{3,}", "\n\n", result)
        # This ensures markers are on their own line but have NO blanks before or after.
        result = re.sub(r"\n\s*\n+(<!-- .*? -->)", r"\n\1", result)
        result = re.sub(r"(<!-- .*? -->)\s*\n\s*\n+", r"\1\n", result)
        result = re.sub(r"(<!-- .*? -->)\s*\n\s*(<!-- .*? -->)", r"\1\n\2", result)

        # 5. Final normalization of non-marker spacing (max 1 blank line)
        result = re.sub(r"\n{3,}", "\n\n", result)

        return result

    def get_spacing_for_transition(
        self, next_type: ContentType, content: str = ""
    ) -> str:
        """Get the required spacing before a new content block."""
        if self._last_content_type is None:
            self._last_content_type = next_type
            return ""

        # If the last block was a marker type, we don't want to add extra spacing
        # because the marker block already ends with a newline.
        if self._last_content_type in [
            ContentType.MARKER,
            ContentType.MESSAGE_BOUNDARY,
        ]:
            self._last_content_type = next_type
            return ""

        # Default: one newline to separate blocks
        spacing = "\n"

        # Rules for transitions between non-marker blocks
        if next_type == ContentType.TOOL_HEADER:
            spacing = "\n\n"
        elif next_type == ContentType.TEXT:
            if self._last_content_type in [
                ContentType.TOOL_OUTPUT,
                ContentType.THINKING,
            ]:
                spacing = "\n\n"

        self._last_content_type = next_type
        return spacing

    def format_delta(self, delta: str, content_type: ContentType) -> str:
        """Format a delta of content, adding spacing only if it's the start of a block."""
        if self._last_content_type != content_type:
            # If we are following a marker, ensure the delta doesn't start with its own newlines
            if self._last_content_type in [
                ContentType.MARKER,
                ContentType.MESSAGE_BOUNDARY,
            ]:
                delta = delta.lstrip("\n")

            prefix = self.get_spacing_for_transition(content_type, delta)
            isolated = self.ensure_marker_isolation(delta)

            if not isolated:
                return prefix

            result = prefix + isolated
            # If it ends with a marker, ensure it has a trailing newline
            if isolated.endswith("-->"):
                result += "\n"

            # Restore leading newline if it was intended by prefix or isolation
            if not result.startswith("\n") and prefix.startswith("\n"):
                result = "\n" + result

            return result

        if "<!--" in delta:
            isolated = self.ensure_marker_isolation(delta)
            if isolated.endswith("-->"):
                isolated += "\n"
            return isolated

        return delta

    def format_content(
        self,
        content: str,
        content_type: ContentType,
        marker_list: list[str] | None = None,
        msg_id: str | None = None,
        is_first_in_buffer: bool = False,
    ) -> str:
        """Format content with proper spacing and markers."""
        # 1. Get transition spacing
        prefix = self.get_spacing_for_transition(content_type, content)
        if is_first_in_buffer:
            prefix = ""

        # 2. Handle markers
        marker_lines = []
        if msg_id:
            marker_lines.append(markers.make_message_marker(msg_id))
        if marker_list is not None:
            marker_lines.append(markers.make_marker(*marker_list))

        if marker_lines:
            marker_str = "\n".join(marker_lines)
            if content:
                # For tools and thinking, the marker follows the header
                if content_type in [ContentType.TOOL_HEADER, ContentType.THINKING]:
                    content = f"{content}\n{marker_str}"
                else:
                    # Marker then content (default for messages)
                    content = f"{marker_str}\n{content}"
            else:
                content = marker_str

            # If this block is purely markers, update state
            if not content or content == marker_str:
                self._last_content_type = ContentType.MARKER

        # 3. Ensure marker isolation and NO blank lines around them
        isolated = self.ensure_marker_isolation(content)

        # 4. Re-apply boundary spacing
        result = prefix + isolated

        # If it ends with a marker, ensure it has a trailing newline for the NEXT block
        if result.endswith("-->"):
            result += "\n"

        if is_first_in_buffer:
            # Aggressively ensure NO leading whitespace/newlines at the start of the conversation
            return result.lstrip()

        return result


def normalize_spacing(text: str) -> str:
    """Normalize spacing in text to prevent excessive blank lines.

    Reduces 3+ consecutive newlines to 2 (one blank line).
    Preserves single blank lines.
    """
    return re.sub(r"\n{3,}", "\n\n", text)
