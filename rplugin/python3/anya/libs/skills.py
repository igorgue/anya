"""Skill loading helpers for persistent hidden conversation context."""

from __future__ import annotations

import hashlib
import json
import os
import uuid

from .. import db, ids
from ..skills import Skill, discover_skills


def _resolve_skill(skill_name_or_path: str, cwd: str | None = None) -> Skill:
    skills = discover_skills(cwd=cwd)
    for skill in skills:
        if skill.name == skill_name_or_path or skill.skill_md_path == skill_name_or_path:
            return skill
    raise ValueError(f"Skill not found: {skill_name_or_path}")


def _fingerprint(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _format_skill_message(skill: Skill, content: str, fingerprint: str) -> str:
    parts = [
        '<loaded_skill name="' + skill.name + '" path="' + skill.skill_md_path + '" fingerprint="' + fingerprint + '">',
        content.strip(),
        '</loaded_skill>',
    ]
    return '\n'.join(parts)


def _resolve_conversation_id(conversation_id: str | None) -> str:
    if conversation_id:
        return conversation_id

    current = db.get_current_conversation_id()
    if current:
        return current

    raise ValueError("conversation_id is required when no active conversation exists")


def _notify(message: str, level: str = "info"):
    """Fire a vim.notify event (fire-and-forget)."""
    ui_dir = os.environ.get("ANYA_UI_DIR")
    if not ui_dir:
        return
    try:
        os.makedirs(ui_dir, exist_ok=True)
        event_file = os.path.join(ui_dir, uuid.uuid4().hex + ".event.json")
        with open(event_file, "w") as f:
            json.dump({"kind": "notify", "message": message, "level": level, "title": "Anya"}, f)
    except Exception:
        pass


def load(
    skill_name_or_path: str,
    conversation_id: str | None = None,
    cwd: str | None = None,
) -> str:
    """Load a skill once per conversation and persist it as a hidden message."""
    conversation_id = _resolve_conversation_id(conversation_id)

    skill = _resolve_skill(skill_name_or_path, cwd=cwd)
    with open(skill.skill_md_path, "r", encoding="utf-8") as f:
        content = f.read()
    fingerprint = _fingerprint(skill.skill_md_path)
    meta = json.dumps(
        {
            "skill_name": skill.name,
            "skill_path": skill.skill_md_path,
            "fingerprint": fingerprint,
        },
        sort_keys=True,
    )

    existing = db.find_hidden_message(
        conversation_id,
        "skill",
        meta_substring='"skill_name": "' + skill.name + '"',
    )
    if existing:
        try:
            existing_meta = json.loads(existing.get("meta") or "{}")
        except Exception:
            existing_meta = {}
        if existing_meta.get("fingerprint") == fingerprint:
            return "Skill '" + skill.name + "' already loaded for this conversation."

    conversation = db.get_conversation(conversation_id)
    if not conversation:
        raise ValueError("Conversation not found: " + conversation_id)

    timestamp = conversation.get("updated_at") or conversation.get("created_at")
    if not timestamp:
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat()

    msg_id = ids.new(conversation=conversation_id)
    message = _format_skill_message(skill, content, fingerprint)
    db.save_message_dict(
        msg_id=msg_id,
        conversation_id=conversation_id,
        role="system",
        content=message,
        author="skill-loader",
        created_at=timestamp,
        ended_at=timestamp,
        hidden=True,
        message_type="skill",
        meta=meta,
    )
    _notify("Loaded skill: " + skill.name)
    return "Loaded skill '" + skill.name + "' into hidden conversation context."
