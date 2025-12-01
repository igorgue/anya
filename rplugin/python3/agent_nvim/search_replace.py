"""Aider-style SEARCH/REPLACE block parser and applier.

This module provides fuzzy matching and indentation-aware code replacement,
inspired by Aider's editblock implementation.
"""

import difflib
import math
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class EditBlock:
    """Represents a single SEARCH/REPLACE edit operation."""

    path: str
    search: str
    replace: str
    raw_block: str = ""


@dataclass
class EditResult:
    """Result of applying an edit block."""

    success: bool
    path: str
    message: str
    original_content: Optional[str] = None
    new_content: Optional[str] = None
    match_type: Optional[str] = None  # "exact", "whitespace", "fuzzy"


# Regex patterns for SEARCH/REPLACE blocks (Aider-compatible)
HEAD = r"^<{5,9} SEARCH>?\s*$"
DIVIDER = r"^={5,9}\s*$"
UPDATED = r"^>{5,9} REPLACE\s*$"

HEAD_ERR = "<<<<<<< SEARCH"
DIVIDER_ERR = "======="
UPDATED_ERR = ">>>>>>> REPLACE"


def prep(content: str) -> Tuple[str, List[str]]:
    """Prepare content for matching - ensure trailing newline and split to lines."""
    if content and not content.endswith("\n"):
        content += "\n"
    lines = content.splitlines(keepends=True)
    return content, lines


def strip_filename(filename: str) -> Optional[str]:
    """Extract filename from a line, handling various formats."""
    filename = filename.strip()

    if filename == "...":
        return None

    # Handle ```python filename or similar
    if filename.startswith("```"):
        candidate = filename[3:].strip()
        # Check if it looks like a language identifier followed by filename
        parts = candidate.split()
        if len(parts) >= 2 and ("." in parts[-1] or "/" in parts[-1]):
            return parts[-1]
        if candidate and ("." in candidate or "/" in candidate):
            return candidate
        return None

    filename = filename.rstrip(":")
    filename = filename.lstrip("#")
    filename = filename.strip()
    filename = filename.strip("`")
    filename = filename.strip("*")

    return filename if filename else None


def find_filename(
    lines: List[str], valid_fnames: Optional[List[str]] = None
) -> Optional[str]:
    """Find filename from preceding lines before SEARCH block."""
    if valid_fnames is None:
        valid_fnames = []

    # Go back through the preceding lines (reversed)
    lines = list(reversed(lines[-3:]))

    filenames = []
    for line in lines:
        filename = strip_filename(line)
        if filename:
            filenames.append(filename)

        # Only continue as long as we keep seeing fences or potential filenames
        if not line.strip().startswith("```") and not line.strip():
            break

    if not filenames:
        return None

    # Check for exact match first
    for fname in filenames:
        if fname in valid_fnames:
            return fname

    # Check for partial match (basename match)
    for fname in filenames:
        for vfn in valid_fnames:
            if fname == Path(vfn).name:
                return vfn

    # Fuzzy matching with valid_fnames
    for fname in filenames:
        close_matches = difflib.get_close_matches(fname, valid_fnames, n=1, cutoff=0.8)
        if close_matches:
            return close_matches[0]

    # If no fuzzy match, look for a file with extension
    for fname in filenames:
        if "." in fname:
            return fname

    return filenames[0] if filenames else None


def parse_search_replace_blocks(
    content: str, valid_fnames: Optional[List[str]] = None
) -> List[EditBlock]:
    """Parse SEARCH/REPLACE blocks from LLM response content.

    Args:
        content: The LLM response containing SEARCH/REPLACE blocks
        valid_fnames: Optional list of valid filenames to help resolve ambiguous paths

    Returns:
        List of EditBlock objects
    """
    blocks = []
    lines = content.splitlines(keepends=True)
    i = 0
    current_filename = None

    head_pattern = re.compile(HEAD)
    divider_pattern = re.compile(DIVIDER)
    updated_pattern = re.compile(UPDATED)

    while i < len(lines):
        line = lines[i]

        # Check for SEARCH/REPLACE blocks
        if head_pattern.match(line.strip()):
            try:
                # Look for filename in preceding lines
                filename = find_filename(lines[max(0, i - 3) : i], valid_fnames)

                if not filename:
                    if current_filename:
                        filename = current_filename
                    else:
                        i += 1
                        continue  # Skip malformed blocks

                current_filename = filename

                # Collect SEARCH content
                search_text = []
                i += 1
                while i < len(lines) and not divider_pattern.match(lines[i].strip()):
                    search_text.append(lines[i])
                    i += 1

                if i >= len(lines) or not divider_pattern.match(lines[i].strip()):
                    continue  # Malformed block

                # Collect REPLACE content
                replace_text = []
                i += 1
                while i < len(lines) and not (
                    updated_pattern.match(lines[i].strip())
                    or divider_pattern.match(lines[i].strip())
                ):
                    replace_text.append(lines[i])
                    i += 1

                if i >= len(lines) or not (
                    updated_pattern.match(lines[i].strip())
                    or divider_pattern.match(lines[i].strip())
                ):
                    continue  # Malformed block

                # Build raw block for display
                raw_lines = [f"{filename}\n", f"{HEAD_ERR}\n"]
                raw_lines.extend(search_text)
                raw_lines.append(f"{DIVIDER_ERR}\n")
                raw_lines.extend(replace_text)
                raw_lines.append(f"{UPDATED_ERR}\n")

                blocks.append(
                    EditBlock(
                        path=filename,
                        search="".join(search_text),
                        replace="".join(replace_text),
                        raw_block="".join(raw_lines),
                    )
                )

            except Exception:
                pass  # Skip malformed blocks

        i += 1

    return blocks


def perfect_replace(
    whole_lines: List[str], part_lines: List[str], replace_lines: List[str]
) -> Optional[str]:
    """Try exact match replacement."""
    part_tup = tuple(part_lines)
    part_len = len(part_lines)

    for i in range(len(whole_lines) - part_len + 1):
        whole_tup = tuple(whole_lines[i : i + part_len])
        if part_tup == whole_tup:
            res = whole_lines[:i] + replace_lines + whole_lines[i + part_len :]
            return "".join(res)

    return None


def match_but_for_leading_whitespace(
    whole_lines: List[str], part_lines: List[str]
) -> Optional[str]:
    """Check if lines match ignoring leading whitespace differences."""
    num = len(whole_lines)

    # Does the non-whitespace all agree?
    if not all(whole_lines[i].lstrip() == part_lines[i].lstrip() for i in range(num)):
        return None

    # Are they all offset the same?
    add = set(
        whole_lines[i][: len(whole_lines[i]) - len(whole_lines[i].lstrip())]
        for i in range(num)
        if whole_lines[i].strip()
    )

    if len(add) != 1:
        return None

    return add.pop()


def replace_part_with_missing_leading_whitespace(
    whole_lines: List[str], part_lines: List[str], replace_lines: List[str]
) -> Optional[str]:
    """Handle cases where LLM omits or modifies leading whitespace."""
    # Outdent everything in part_lines and replace_lines by the max fixed amount possible
    leading = [len(p) - len(p.lstrip()) for p in part_lines if p.strip()] + [
        len(p) - len(p.lstrip()) for p in replace_lines if p.strip()
    ]

    if leading and min(leading):
        num_leading = min(leading)
        part_lines = [p[num_leading:] if p.strip() else p for p in part_lines]
        replace_lines = [p[num_leading:] if p.strip() else p for p in replace_lines]

    # Can we find an exact match not including the leading whitespace?
    num_part_lines = len(part_lines)

    for i in range(len(whole_lines) - num_part_lines + 1):
        add_leading = match_but_for_leading_whitespace(
            whole_lines[i : i + num_part_lines], part_lines
        )

        if add_leading is None:
            continue

        replace_lines = [
            add_leading + rline if rline.strip() else rline for rline in replace_lines
        ]
        whole_lines = (
            whole_lines[:i] + replace_lines + whole_lines[i + num_part_lines :]
        )
        return "".join(whole_lines)

    return None


def detect_indent_prefix(lines: List[str]) -> str:
    """Detect the common leading whitespace from non-empty lines."""
    for line in lines:
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return ""


def reindent_lines(
    lines: List[str], target_indent: str, source_indent: str
) -> List[str]:
    """Reindent lines from source_indent to target_indent."""
    result = []
    for line in lines:
        if not line.strip():
            result.append(line)
        elif line.startswith(source_indent):
            result.append(target_indent + line[len(source_indent) :])
        else:
            result.append(target_indent + line.lstrip())
    return result


def replace_closest_edit_distance(
    whole_lines: List[str], part: str, part_lines: List[str], replace_lines: List[str]
) -> Optional[str]:
    """Fuzzy matching using edit distance with indentation preservation."""
    similarity_thresh = 0.8

    max_similarity = 0
    most_similar_chunk_start = -1
    most_similar_chunk_end = -1

    scale = 0.1
    min_len = math.floor(len(part_lines) * (1 - scale))
    max_len = math.ceil(len(part_lines) * (1 + scale))

    for length in range(min_len, max_len + 1):
        for i in range(len(whole_lines) - length + 1):
            chunk = whole_lines[i : i + length]
            chunk_str = "".join(chunk)

            similarity = SequenceMatcher(None, chunk_str, part).ratio()

            if similarity > max_similarity:
                max_similarity = similarity
                most_similar_chunk_start = i
                most_similar_chunk_end = i + length

    if max_similarity < similarity_thresh:
        return None

    # Detect indentation from the matched chunk and adjust replacement
    matched_chunk = whole_lines[most_similar_chunk_start:most_similar_chunk_end]
    chunk_indent = detect_indent_prefix(matched_chunk)
    part_indent = detect_indent_prefix(part_lines)

    # Reindent replacement lines to match the file's indentation
    if chunk_indent != part_indent:
        replace_lines = reindent_lines(replace_lines, chunk_indent, part_indent)

    modified_whole = (
        whole_lines[:most_similar_chunk_start]
        + replace_lines
        + whole_lines[most_similar_chunk_end:]
    )
    return "".join(modified_whole)


def try_dotdotdots(whole: str, part: str, replace: str) -> Optional[str]:
    """Handle ... ellipsis in SEARCH/REPLACE blocks."""
    dots_re = re.compile(r"(^\s*\.\.\.\n)", re.MULTILINE | re.DOTALL)

    part_pieces = re.split(dots_re, part)
    replace_pieces = re.split(dots_re, replace)

    if len(part_pieces) != len(replace_pieces):
        return None

    if len(part_pieces) == 1:
        # No dots in this edit block
        return None

    # Compare odd strings (the ... markers)
    all_dots_match = all(
        part_pieces[i] == replace_pieces[i] for i in range(1, len(part_pieces), 2)
    )

    if not all_dots_match:
        return None

    part_pieces = [part_pieces[i] for i in range(0, len(part_pieces), 2)]
    replace_pieces = [replace_pieces[i] for i in range(0, len(replace_pieces), 2)]

    pairs = zip(part_pieces, replace_pieces)
    for part_chunk, replace_chunk in pairs:
        if not part_chunk and not replace_chunk:
            continue

        if not part_chunk and replace_chunk:
            if not whole.endswith("\n"):
                whole += "\n"
            whole += replace_chunk
            continue

        if whole.count(part_chunk) == 0:
            return None
        if whole.count(part_chunk) > 1:
            return None

        whole = whole.replace(part_chunk, replace_chunk, 1)

    return whole


def replace_most_similar_chunk(
    whole: str, part: str, replace: str
) -> Tuple[Optional[str], str]:
    """Best efforts to find the `part` lines in `whole` and replace them with `replace`.

    Returns:
        Tuple of (new_content or None, match_type)
        match_type is one of: "exact", "whitespace", "dotdotdot", "fuzzy", "none"
    """
    whole, whole_lines = prep(whole)
    part, part_lines = prep(part)
    replace, replace_lines = prep(replace)

    # Try exact match first
    res = perfect_replace(whole_lines, part_lines, replace_lines)
    if res:
        return res, "exact"

    # Try being flexible about leading whitespace
    res = replace_part_with_missing_leading_whitespace(
        whole_lines, part_lines, replace_lines
    )
    if res:
        return res, "whitespace"

    # Drop leading empty line (LLMs sometimes add them spuriously)
    if len(part_lines) > 2 and not part_lines[0].strip():
        skip_blank_line_part_lines = part_lines[1:]
        res = perfect_replace(whole_lines, skip_blank_line_part_lines, replace_lines)
        if res:
            return res, "exact"
        res = replace_part_with_missing_leading_whitespace(
            whole_lines, skip_blank_line_part_lines, replace_lines
        )
        if res:
            return res, "whitespace"

    # Try to handle ... ellipsis
    try:
        res = try_dotdotdots(whole, part, replace)
        if res:
            return res, "dotdotdot"
    except Exception:
        pass

    # Try fuzzy matching as last resort
    res = replace_closest_edit_distance(whole_lines, part, part_lines, replace_lines)
    if res:
        return res, "fuzzy"

    return None, "none"


def find_similar_lines(
    search_lines: str, content_lines: str, threshold: float = 0.6
) -> str:
    """Find lines in content that are similar to search_lines."""
    search_list = search_lines.splitlines()
    content_list = content_lines.splitlines()

    best_ratio = 0
    best_match = None
    best_match_i = 0

    for i in range(len(content_list) - len(search_list) + 1):
        chunk = content_list[i : i + len(search_list)]
        ratio = SequenceMatcher(None, search_list, chunk).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = chunk
            best_match_i = i

    if best_ratio < threshold:
        return ""

    if (
        best_match
        and best_match[0] == search_list[0]
        and best_match[-1] == search_list[-1]
    ):
        return "\n".join(best_match)

    # Extend context around match
    N = 5
    best_match_end = min(len(content_list), best_match_i + len(search_list) + N)
    best_match_i = max(0, best_match_i - N)

    best = content_list[best_match_i:best_match_end]
    return "\n".join(best)


def apply_edit_block(block: EditBlock, cwd: str) -> EditResult:
    """Apply a single edit block to a file.

    Args:
        block: The EditBlock to apply
        cwd: Current working directory for resolving relative paths

    Returns:
        EditResult with success status and details
    """
    # Resolve path
    if not os.path.isabs(block.path):
        full_path = os.path.join(cwd, block.path)
    else:
        full_path = block.path

    # Handle new file creation (empty SEARCH section)
    if not block.search.strip():
        try:
            # Create parent directories if needed
            parent_dir = os.path.dirname(full_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir)

            # Check if file exists
            original_content = None
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    original_content = f.read()
                new_content = original_content + block.replace
            else:
                new_content = block.replace

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return EditResult(
                success=True,
                path=block.path,
                message=f"Created/appended to {block.path}",
                original_content=original_content,
                new_content=new_content,
                match_type="create",
            )
        except Exception as e:
            return EditResult(
                success=False,
                path=block.path,
                message=f"Failed to create {block.path}: {e}",
            )

    # Handle existing file modification
    if not os.path.exists(full_path):
        return EditResult(
            success=False,
            path=block.path,
            message=f"File not found: {block.path}",
        )

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            original_content = f.read()

        new_content, match_type = replace_most_similar_chunk(
            original_content, block.search, block.replace
        )

        if new_content is None:
            # Find similar lines to help with error message
            similar = find_similar_lines(block.search, original_content)
            hint = ""
            if similar:
                hint = f"\n\nDid you mean:\n```\n{similar}\n```"

            return EditResult(
                success=False,
                path=block.path,
                message=f"SEARCH block did not match any content in {block.path}{hint}",
                original_content=original_content,
            )

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return EditResult(
            success=True,
            path=block.path,
            message=f"Applied edit to {block.path} ({match_type} match)",
            original_content=original_content,
            new_content=new_content,
            match_type=match_type,
        )

    except Exception as e:
        return EditResult(
            success=False,
            path=block.path,
            message=f"Error applying edit to {block.path}: {e}",
        )


def apply_edit_blocks(
    blocks: List[EditBlock], cwd: str, atomic: bool = True
) -> List[EditResult]:
    """Apply multiple edit blocks.

    Args:
        blocks: List of EditBlock objects to apply
        cwd: Current working directory
        atomic: If True, all edits must succeed or none are applied

    Returns:
        List of EditResult objects
    """
    if not atomic:
        return [apply_edit_block(block, cwd) for block in blocks]

    # Atomic mode: validate all first, then apply
    # First pass: read all files and compute results in memory
    pending_writes = []
    results = []

    for block in blocks:
        if not os.path.isabs(block.path):
            full_path = os.path.join(cwd, block.path)
        else:
            full_path = block.path

        # Handle new file creation
        if not block.search.strip():
            original = None
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    original = f.read()
                new_content = original + block.replace
            else:
                new_content = block.replace

            pending_writes.append((full_path, new_content, original))
            results.append(
                EditResult(
                    success=True,
                    path=block.path,
                    message=f"Will create/append to {block.path}",
                    original_content=original,
                    new_content=new_content,
                    match_type="create",
                )
            )
            continue

        # Handle modification
        if not os.path.exists(full_path):
            return [
                EditResult(
                    success=False,
                    path=block.path,
                    message=f"File not found: {block.path}",
                )
            ]

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            original_content = f.read()

        new_content, match_type = replace_most_similar_chunk(
            original_content, block.search, block.replace
        )

        if new_content is None:
            similar = find_similar_lines(block.search, original_content)
            hint = ""
            if similar:
                hint = f"\n\nDid you mean:\n```\n{similar}\n```"
            return [
                EditResult(
                    success=False,
                    path=block.path,
                    message=f"SEARCH block did not match: {block.path}{hint}",
                    original_content=original_content,
                )
            ]

        pending_writes.append((full_path, new_content, original_content))
        results.append(
            EditResult(
                success=True,
                path=block.path,
                message=f"Applied edit to {block.path} ({match_type} match)",
                original_content=original_content,
                new_content=new_content,
                match_type=match_type,
            )
        )

    # All validations passed, write files
    for full_path, new_content, _ in pending_writes:
        parent_dir = os.path.dirname(full_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    return results


def format_edit_block_for_display(block: EditBlock) -> str:
    """Format an edit block for display in the UI."""
    lines = []
    lines.append(f"**{block.path}**")
    lines.append("```")
    lines.append(HEAD_ERR)
    lines.append(block.search.rstrip("\n"))
    lines.append(DIVIDER_ERR)
    lines.append(block.replace.rstrip("\n"))
    lines.append(UPDATED_ERR)
    lines.append("```")
    return "\n".join(lines)
