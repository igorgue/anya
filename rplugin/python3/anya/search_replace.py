"""Aider-style SEARCH/REPLACE block parser and applier.

This module provides fuzzy matching and indentation-aware code replacement,
inspired by Aider's editblock implementation.

Features:
- Exact match replacement
- Whitespace-tolerant matching (handles indentation differences)
- Anchor-based line-level matching (finds regions by first/last line)
- Fuzzy block matching with adaptive thresholds
- In-memory sequential application for atomic mode (later blocks see earlier changes)
- Rich diagnostics for near-miss failures
"""

import difflib
import math
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Optional, Tuple, Dict

# Configurable fuzzy matching thresholds
FUZZY_STRICT = 0.75  # "confident match, auto-apply"
FUZZY_LOOSE = 0.60  # "near miss, warn LLM but don't apply"
MIN_BLOCK_LINES_FOR_FUZZY = (
    2  # Only allow fuzzy on blocks with at least this many lines
)


def compute_adaptive_threshold(num_lines: int) -> float:
    """Compute an adaptive fuzzy threshold based on block size.

    Larger blocks naturally have more variance, so we lower the threshold.
    Smaller blocks need to be more precise to avoid wrong matches.
    """
    if num_lines >= 20:
        return max(FUZZY_LOOSE, FUZZY_STRICT - 0.12)
    if num_lines >= 10:
        return max(FUZZY_LOOSE, FUZZY_STRICT - 0.08)
    if num_lines >= 5:
        return max(FUZZY_LOOSE, FUZZY_STRICT - 0.05)
    return FUZZY_STRICT


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
    match_type: Optional[str] = (
        None  # "exact", "whitespace", "fuzzy", "anchor", "create"
    )
    similarity: Optional[float] = None  # For fuzzy matches, the similarity ratio
    near_match_snippet: Optional[str] = None  # For failures, show what we found


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


def find_anchor_match(
    whole_lines: List[str], part_lines: List[str]
) -> Optional[Tuple[int, int, float]]:
    """Find a region in whole_lines using anchor-based matching.

    This looks for the first and last non-empty lines of part_lines
    as anchors, then finds candidate regions and scores them.

    Returns:
        Tuple of (start_index, end_index, similarity) or None if no match.
    """
    # Get anchor lines (first and last non-empty stripped lines)
    stripped_part = [line.strip() for line in part_lines if line.strip()]
    if len(stripped_part) < 2:
        return None

    anchor_start = stripped_part[0]
    anchor_end = stripped_part[-1]

    # Find all positions where anchor_start matches
    candidates = []
    stripped_whole = [line.strip() for line in whole_lines]

    for i, line in enumerate(stripped_whole):
        if line == anchor_start:
            # Search forward for anchor_end
            max_search = min(
                len(whole_lines), i + len(part_lines) * 2
            )  # Cap search range
            for j in range(i + 1, max_search):
                if stripped_whole[j] == anchor_end:
                    candidates.append((i, j + 1))

    if not candidates:
        return None

    # Score each candidate region
    best_score = 0.0
    best_match = None

    for start, end in candidates:
        chunk = whole_lines[start:end]
        chunk_str = "".join(chunk)
        part_str = "".join(part_lines)
        score = SequenceMatcher(None, chunk_str, part_str).ratio()

        if score > best_score:
            best_score = score
            best_match = (start, end, score)

    return best_match


def replace_with_anchor_match(
    whole_lines: List[str], part_lines: List[str], replace_lines: List[str]
) -> Optional[Tuple[str, float]]:
    """Try anchor-based matching to find and replace a region.

    Returns:
        Tuple of (new_content, similarity) or None if no suitable match.
    """
    match = find_anchor_match(whole_lines, part_lines)
    if match is None:
        return None

    start, end, similarity = match

    # Use adaptive threshold based on block size
    threshold = compute_adaptive_threshold(len(part_lines))
    if similarity < threshold:
        return None

    # Detect indentation and adjust replacement
    matched_chunk = whole_lines[start:end]
    chunk_indent = detect_indent_prefix(matched_chunk)
    part_indent = detect_indent_prefix(part_lines)

    if chunk_indent != part_indent:
        replace_lines = reindent_lines(replace_lines, chunk_indent, part_indent)

    modified = whole_lines[:start] + replace_lines + whole_lines[end:]
    return "".join(modified), similarity


def replace_closest_edit_distance(
    whole_lines: List[str], part: str, part_lines: List[str], replace_lines: List[str]
) -> Optional[Tuple[str, float]]:
    """Fuzzy matching using edit distance with indentation preservation.

    Returns:
        Tuple of (new_content, similarity) or None if no match above threshold.
    """
    # Use adaptive threshold based on block size
    num_lines = len(part_lines)
    similarity_thresh = compute_adaptive_threshold(num_lines)

    max_similarity = 0.0
    most_similar_chunk_start = -1
    most_similar_chunk_end = -1

    # Allow more variance in chunk size for better matching
    scale = 0.15
    min_len = max(1, math.floor(num_lines * (1 - scale)))
    max_len = math.ceil(num_lines * (1 + scale))

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
    return "".join(modified_whole), max_similarity


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
) -> Tuple[Optional[str], str, Optional[float], Optional[str]]:
    """Best efforts to find the `part` lines in `whole` and replace them with `replace`.

    Returns:
        Tuple of (new_content or None, match_type, similarity, near_match_snippet)
        match_type is one of: "exact", "whitespace", "dotdotdot", "anchor", "fuzzy", "near_miss", "none"
        similarity is the match ratio for fuzzy/anchor matches
        near_match_snippet shows the closest match for failures
    """
    whole, whole_lines = prep(whole)
    part, part_lines = prep(part)
    replace, replace_lines = prep(replace)

    # Try exact match first
    res = perfect_replace(whole_lines, part_lines, replace_lines)
    if res:
        return res, "exact", 1.0, None

    # Try being flexible about leading whitespace
    res = replace_part_with_missing_leading_whitespace(
        whole_lines, part_lines, replace_lines
    )
    if res:
        return res, "whitespace", 1.0, None

    # Drop leading empty line (LLMs sometimes add them spuriously)
    if len(part_lines) > 2 and not part_lines[0].strip():
        skip_blank_line_part_lines = part_lines[1:]
        res = perfect_replace(whole_lines, skip_blank_line_part_lines, replace_lines)
        if res:
            return res, "exact", 1.0, None
        res = replace_part_with_missing_leading_whitespace(
            whole_lines, skip_blank_line_part_lines, replace_lines
        )
        if res:
            return res, "whitespace", 1.0, None

    # Try to handle ... ellipsis
    try:
        res = try_dotdotdots(whole, part, replace)
        if res:
            return res, "dotdotdot", 1.0, None
    except Exception:
        pass

    # Try anchor-based matching (good for when code has shifted)
    if len(part_lines) >= MIN_BLOCK_LINES_FOR_FUZZY:
        result = replace_with_anchor_match(whole_lines, part_lines, replace_lines)
        if result:
            new_content, similarity = result
            return new_content, "anchor", similarity, None

    # Try fuzzy matching as last resort
    if len(part_lines) >= MIN_BLOCK_LINES_FOR_FUZZY:
        result = replace_closest_edit_distance(
            whole_lines, part, part_lines, replace_lines
        )
        if result:
            new_content, similarity = result
            return new_content, "fuzzy", similarity, None

    # No match found - try to find a near-miss for diagnostics
    near_match = find_similar_lines(part, whole, threshold=FUZZY_LOOSE)
    if near_match:
        # Check if this is a "near miss" (between loose and strict thresholds)
        snippet, start, end, ratio = near_match
        if ratio >= FUZZY_LOOSE:
            return None, "near_miss", ratio, snippet

    return None, "none", 0.0, None


def find_similar_lines(
    search_lines: str, content_lines: str, threshold: float = 0.6
) -> Optional[Tuple[str, int, int, float]]:
    """Find lines in content that are similar to search_lines.

    Returns:
        Tuple of (snippet, start_line, end_line, similarity_ratio) or None if below threshold.
    """
    search_list = search_lines.splitlines()
    content_list = content_lines.splitlines()

    if not search_list or not content_list:
        return None

    if len(search_list) > len(content_list):
        return None

    best_ratio = 0.0
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
        return None

    # If we have an exact first/last line match, return just the matched chunk
    if (
        best_match
        and best_match[0] == search_list[0]
        and best_match[-1] == search_list[-1]
    ):
        end_line = best_match_i + len(search_list)
        return "\n".join(best_match), best_match_i, end_line, best_ratio

    # Extend context around match for better diagnostics
    N = 3
    start = max(0, best_match_i - N)
    end = min(len(content_list), best_match_i + len(search_list) + N)

    snippet = "\n".join(content_list[start:end])
    return snippet, best_match_i, best_match_i + len(search_list), best_ratio


def apply_edit_block(block: EditBlock, cwd: str) -> EditResult:
    """Apply a single edit block to a file.

    Args:
        block: The EditBlock to apply
        cwd: Current working directory for resolving relative paths

    Returns:
        EditResult with success status and details
    """
    # Resolve path with tilde expansion
    path = os.path.expanduser(block.path)
    if not os.path.isabs(path):
        full_path = os.path.join(cwd, path)
    else:
        full_path = path

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

        new_content, match_type, similarity, near_match = replace_most_similar_chunk(
            original_content, block.search, block.replace
        )

        if new_content is None:
            # Build a helpful error message
            if match_type == "near_miss" and near_match:
                hint = (
                    f"\n\nClosest match (similarity: {similarity:.0%}):\n"
                    f"```\n{near_match}\n```\n\n"
                    "The file likely changed since your SEARCH block was generated. "
                    "Please re-read the file and regenerate your edit."
                )
            else:
                # Try to find similar lines for diagnostics
                similar_result = find_similar_lines(block.search, original_content)
                if similar_result:
                    snippet, _, _, ratio = similar_result
                    hint = f"\n\nDid you mean (similarity: {ratio:.0%}):\n```\n{snippet}\n```"
                else:
                    hint = ""

            return EditResult(
                success=False,
                path=block.path,
                message=f"SEARCH block did not match any content in {block.path}{hint}",
                original_content=original_content,
                match_type=match_type,
                similarity=similarity,
                near_match_snippet=near_match,
            )

        # Write the modified content back to the file
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return EditResult(
            success=True,
            path=block.path,
            message=f"Successfully applied edits to {block.path}",
            original_content=original_content,
            new_content=new_content,
            match_type=match_type,
            similarity=similarity,
        )

    except Exception as e:
        return EditResult(
            success=False,
            path=block.path,
            message=f"Error applying edit to {block.path}: {e}",
        )


def apply_edit_blocks(edit_blocks: str, cwd: str = None) -> List[EditResult]:
    """Apply multiple SEARCH/REPLACE blocks to files sequentially.

    Args:
        edit_blocks: String containing one or more SEARCH/REPLACE blocks
        cwd: Current working directory for resolving relative paths

    Returns:
        List of EditResult objects for each block
    """
    if cwd is None:
        cwd = os.getcwd()

    blocks = parse_search_replace_blocks(edit_blocks)
    results = []
    content_cache: Dict[str, str] = {}

    for block in blocks:
        # Resolve full path
        path = os.path.expanduser(block.path)
        if not os.path.isabs(path):
            full_path = os.path.join(cwd, path)
        else:
            full_path = path

        # Get file content, using cache if available (to see updates from previous blocks)
        if full_path in content_cache:
            original_content = content_cache[full_path]
            # Create a temporary block with the cached content for application
            temp_block = EditBlock(
                path=block.path, search=block.search, replace=block.replace
            )
            # Manually apply to cached content
            new_content, match_type, similarity, near_match = (
                replace_most_similar_chunk(
                    original_content, block.search, block.replace
                )
            )

            if new_content is None:
                if match_type == "near_miss" and near_match:
                    hint = (
                        f"\n\nClosest match (similarity: {similarity:.0%}):\n"
                        f"```\n{near_match}\n```"
                    )
                else:
                    similar_result = find_similar_lines(block.search, original_content)
                    hint = (
                        f"\n\nDid you mean (similarity: {similar_result[3]:.0%}):\n```\n{similar_result[0]}\n```"
                        if similar_result
                        else ""
                    )

                results.append(
                    EditResult(
                        success=False,
                        path=block.path,
                        message=f"SEARCH block did not match any content in {block.path}{hint}",
                        original_content=original_content,
                        match_type=match_type,
                        similarity=similarity,
                        near_match_snippet=near_match,
                    )
                )
            else:
                # Write the modified content back to the file
                try:
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(new_content)

                    content_cache[full_path] = new_content
                    results.append(
                        EditResult(
                            success=True,
                            path=block.path,
                            message=f"Successfully applied edits to {block.path}",
                            original_content=original_content,
                            new_content=new_content,
                            match_type=match_type,
                            similarity=similarity,
                        )
                    )
                except Exception as e:
                    results.append(
                        EditResult(
                            success=False,
                            path=block.path,
                            message=f"Error writing edit to {block.path}: {e}",
                            original_content=original_content,
                            new_content=new_content,
                            match_type=match_type,
                            similarity=similarity,
                        )
                    )
        else:
            # Apply normally
            result = apply_edit_block(block, cwd)
            results.append(result)
            if result.success and result.new_content:
                content_cache[full_path] = result.new_content

    return results
