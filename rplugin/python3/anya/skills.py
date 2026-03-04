"""Agent Skills discovery and validation.

Implements filesystem-based Skills compatible with Claude Code and the open
agent skills ecosystem (skills.sh / ~/.agents/skills/):
- Scans ~/.claude/skills/ (global, Claude Code)
- Scans ~/.agents/skills/ (global, universal agent skills)
- Scans <cwd>/.claude/skills/ (project-local, Claude Code)
- Scans <cwd>/.agents/skills/ (project-local, universal agent skills)
- Parses YAML frontmatter from each SKILL.md, including multi-line block scalars
- Validates name and description fields per the Claude Code spec
- Later scan locations take precedence over earlier ones for the same name
  (project-local > global; .agents > .claude within the same scope)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger("anya.skills")

# Validation constants (per the Claude Code Skills spec)
_NAME_MAX_LEN = 64
_DESC_MAX_LEN = 1024
_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_RESERVED_WORDS = {"anthropic", "claude"}
_XML_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: str  # absolute path to the skill directory
    skill_md_path: str  # absolute path to SKILL.md


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML-style frontmatter from a markdown file.

    Returns (frontmatter_dict, body) where body is everything after the closing ---.
    Handles both simple ``key: value`` pairs and block scalar values used by the
    universal agent skills ecosystem (e.g. the ``uv`` / ``ty`` skills from
    astral-sh/claude-code-plugins):

        description:
          First line of the description.
          Second line continues here.

    The block scalar value is collected by joining all indented continuation
    lines with a single space and stripping the result.
    """
    content = content.strip()
    if not content.startswith("---"):
        return {}, content

    # Find the closing ---
    rest = content[3:]
    end = rest.find("\n---")
    if end == -1:
        return {}, content

    frontmatter_text = rest[:end].strip()
    body = rest[end + 4:].strip()  # skip \n---

    result: dict[str, str] = {}
    current_key: str | None = None
    current_value_lines: list[str] = []

    def _flush():
        if current_key is not None:
            result[current_key] = " ".join(current_value_lines).strip()

    for line in frontmatter_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # An indented line whose first token contains no colon is a continuation
        # of the previous block-scalar value.
        first_token = stripped.split()[0] if stripped.split() else ""
        if current_key is not None and line.startswith((" ", "\t")) and ":" not in first_token:
            current_value_lines.append(stripped)
            continue

        # New key: value pair — flush the previous one first
        _flush()
        current_key = None
        current_value_lines = []

        if ":" in stripped:
            key, _, value = stripped.partition(":")
            current_key = key.strip()
            value = value.strip()
            if value:
                # Inline value — single-line
                current_value_lines = [value]
            # else: block scalar, continuation lines follow

    _flush()
    return result, body


def _validate_name(name: str) -> str | None:
    """Validate a skill name. Returns an error message or None if valid."""
    if not name:
        return "name is required"
    if len(name) > _NAME_MAX_LEN:
        return f"name exceeds {_NAME_MAX_LEN} characters"
    if not _NAME_PATTERN.match(name):
        return "name must contain only lowercase letters, numbers, and hyphens"
    if _XML_TAG_PATTERN.search(name):
        return "name cannot contain XML tags"
    for word in _RESERVED_WORDS:
        if word in name:
            return f'name cannot contain reserved word "{word}"'
    return None


def _validate_description(description: str) -> str | None:
    """Validate a skill description. Returns an error message or None if valid."""
    if not description:
        return "description is required and must be non-empty"
    if len(description) > _DESC_MAX_LEN:
        return f"description exceeds {_DESC_MAX_LEN} characters"
    if _XML_TAG_PATTERN.search(description):
        return "description cannot contain XML tags"
    return None


def _load_skill_from_dir(skill_dir: str) -> Skill | None:
    """Attempt to load a Skill from a directory. Returns None on failure."""
    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md_path):
        return None

    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Failed to read {skill_md_path}: {e}")
        return None

    frontmatter, _ = _parse_frontmatter(content)

    name = frontmatter.get("name", "").strip()
    description = frontmatter.get("description", "").strip()

    name_err = _validate_name(name)
    if name_err:
        logger.warning(f"Skill in {skill_dir} has invalid name: {name_err}")
        return None

    desc_err = _validate_description(description)
    if desc_err:
        logger.warning(f"Skill in {skill_dir} has invalid description: {desc_err}")
        return None

    return Skill(
        name=name,
        description=description,
        path=os.path.abspath(skill_dir),
        skill_md_path=os.path.abspath(skill_md_path),
    )


def discover_skills(cwd: str | None = None) -> list[Skill]:
    """Discover all available Skills.

    Scans four locations in priority order (later overrides earlier):

    1. ``~/.claude/skills/``       — global Claude Code skills
    2. ``~/.agents/skills/``       — global universal agent skills (skills.sh)
    3. ``<cwd>/.claude/skills/``   — project-local Claude Code skills
    4. ``<cwd>/.agents/skills/``   — project-local universal agent skills

    Skills with the same name in a later location override earlier ones, so
    project-local skills always win over global ones.

    Args:
        cwd: Current working directory to scan for project-local skills.
             Falls back to os.getcwd() if not provided.

    Returns:
        List of valid Skill objects, sorted by name.
    """
    if cwd is None:
        try:
            cwd = os.getcwd()
        except Exception:
            cwd = ""

    home = os.path.expanduser("~")
    skills_by_name: dict[str, Skill] = {}

    # 1. Global Claude Code skills
    _scan_skills_dir(os.path.join(home, ".claude", "skills"), skills_by_name)

    # 2. Global universal agent skills
    _scan_skills_dir(os.path.join(home, ".agents", "skills"), skills_by_name)

    if cwd:
        # 3. Project-local Claude Code skills
        _scan_skills_dir(os.path.join(cwd, ".claude", "skills"), skills_by_name)

        # 4. Project-local universal agent skills
        _scan_skills_dir(os.path.join(cwd, ".agents", "skills"), skills_by_name)

    return sorted(skills_by_name.values(), key=lambda s: s.name)


def _scan_skills_dir(skills_dir: str, result: dict[str, Skill]) -> None:
    """Scan a skills directory and populate the result dict."""
    if not os.path.isdir(skills_dir):
        return

    try:
        entries = os.listdir(skills_dir)
    except Exception as e:
        logger.warning(f"Failed to list skills directory {skills_dir}: {e}")
        return

    for entry in entries:
        skill_dir = os.path.join(skills_dir, entry)
        if not os.path.isdir(skill_dir):
            continue
        skill = _load_skill_from_dir(skill_dir)
        if skill is not None:
            result[skill.name] = skill
            logger.debug(f"Loaded skill: {skill.name} from {skill.path}")


def skills_fingerprint(skills: list[Skill]) -> str:
    """Compute a short hash fingerprint of a skills list for cache invalidation.

    Incorporates skill names, paths, and mtimes so the cache is invalidated
    whenever skills are added, removed, or their SKILL.md is modified.
    """
    parts = []
    for skill in skills:
        try:
            mtime = str(os.path.getmtime(skill.skill_md_path))
        except Exception:
            mtime = "0"
        parts.append(f"{skill.name}:{skill.skill_md_path}:{mtime}")

    digest = hashlib.md5("\n".join(parts).encode()).hexdigest()[:8]
    return digest


def format_skills_for_prompt(skills: list[Skill]) -> str:
    """Format discovered skills as a system prompt block (Level 1 metadata).

    This lightweight block (~100 tokens per skill) is always injected into
    the system prompt so the agent knows which skills are available.
    """
    if not skills:
        return ""

    lines = [
        "",
        "---",
        "# Agent Skills",
        "",
        "The following Skills are available. When a user request matches a Skill's",
        "description, read the SKILL.md file using `execute` to load the full",
        "instructions before proceeding. Then follow those instructions.",
        "",
    ]

    for skill in skills:
        lines.append(f"## {skill.name}")
        lines.append(f"**Description**: {skill.description}")
        lines.append(f"**Instructions**: `{skill.skill_md_path}`")
        lines.append("")

    return "\n".join(lines)
