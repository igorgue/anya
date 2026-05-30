"""Memory retrieval helpers backed by Anya's SQLite memory store."""

from __future__ import annotations

from typing import Any

from .. import db

_VALID_CATEGORIES = {"personal", "preference", "project", "task", "skill"}


def search_memories(
    query: str | None = None,
    category: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search stored memories by optional query/category, ordered by newest first."""
    normalized_category = (category or "").strip().lower() or None
    if normalized_category and normalized_category not in _VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category: {category!r}. Valid categories: {sorted(_VALID_CATEGORIES)}"
        )
    safe_limit = max(1, min(int(limit), 50))
    return db.search_memories(
        query=query, category=normalized_category, limit=safe_limit
    )


def format_memories(memories: list[dict[str, Any]]) -> str:
    """Format memory records into plain text for invisible context injection."""
    if not memories:
        return ""

    lines: list[str] = []
    for memory in memories:
        text = str(memory.get("text", "")).strip()
        if not text:
            continue
        lines.append(text)

    return "\n".join(lines) if lines else ""
