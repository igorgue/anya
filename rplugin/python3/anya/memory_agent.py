from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from textwrap import dedent
from typing import Any

from agents import Agent, Runner
from agents.models.default_models import get_default_model_settings

from .protocol import AgentSettings
from .model_provider import get_run_config
from .reasoning import apply_reasoning_settings

logger = logging.getLogger("anya.memory")

MEMORY_AGENT_NAME = "Memory"
VALID_CATEGORIES = {"personal", "preference", "project", "task", "skill"}

MEMORY_PROMPT = dedent(
    """
    You extract durable user memories from a single user message for later recall.

    Your job:
    - Analyze the user's latest message.
    - Extract only information likely to remain useful in future conversations.
    - Ignore temporary chit-chat, generic requests, information already obvious from the current prompt alone, and statements that a fact is unknown/not yet stated.
    - Return strict JSON only.

    Memory categories:
    - personal: stable facts about the user
    - preference: likes, dislikes, communication preferences, tooling preferences
    - project: durable facts about the user's project, environment, architecture, or workflow
    - task: ongoing or explicitly remembered tasks/todos the user wants tracked
    - skill: stated expertise or lack of expertise worth remembering

    Output format:
    {
      "memories": [
        {
          "text": "short normalized memory statement",
          "category": "personal|preference|project|task|skill",
          "confidence": 0.0,
          "deduplication_key": "stable-key-or-empty"
        }
      ]
    }

    Rules:
    - Return at most 5 memories.
    - If nothing should be remembered, return {"memories": []}.
    - ALWAYS extract explicit positive user identity/preferences/profile facts, even if phrased casually.
      Examples that must be remembered:
      - "My favorite is Python" -> "User's favorite programming language is Python." category "preference" key "favorite-programming-language"
      - "I prefer tabs" -> "User prefers tabs." category "preference"
      - "Call me Igor" -> "User prefers to be called Igor." category "personal"
    - Resolve short answers using immediate conversational context when obvious. For example, if
      the assistant asked for a favorite programming language and the user says "My favorite is Python",
      store the full normalized preference, not the ambiguous phrase.
    - Never store negative/absence facts like "the user has not stated X", "unknown", or "not saved".
    - `text` must be concise, standalone, and written in third-person-neutral style.
    - `confidence` must be between 0 and 1.
    - `deduplication_key` should be a stable normalized key when possible. Prefer semantic keys
      like "favorite-programming-language" for replaceable user preferences.
    - Do not include explanations, markdown, or prose outside the JSON.
    """
).strip()


def _get_setting(
    settings: AgentSettings | None,
    attr: str,
    env_key: str,
    *fallback_keys,
    default=None,
):
    if settings:
        val = getattr(settings, attr, None)
        if val is not None:
            return val
    import os

    for k in (env_key, *fallback_keys):
        v = os.environ.get(k)
        if v is not None:
            return v
    return default


async def build_memory_agent(settings: AgentSettings | None = None) -> Agent:
    model_name = (
        _get_setting(settings, "model", "ANYA_MODEL", default="gpt-4.1") or "gpt-4.1"
    ).strip()
    model_settings_obj = get_default_model_settings(model_name.lower())

    api_type = _get_setting(
        settings,
        "api_type",
        "ANYA_API_TYPE",
        "ANYA_OPENAI_API_TYPE",
        default="responses",
    )
    if api_type:
        api_type = api_type.strip().lower()
        if api_type not in {"chat_completions", "responses", "anthropic", "copilot"}:
            api_type = "responses"
    else:
        api_type = "responses"

    if hasattr(model_settings_obj, "api_type"):
        model_settings_obj.api_type = api_type

    thinking_budget = _get_setting(settings, "thinking_budget", "ANYA_THINKING_BUDGET")
    apply_reasoning_settings(model_settings_obj, api_type, thinking_budget)
    model_settings_obj.parallel_tool_calls = False

    return Agent(
        name=MEMORY_AGENT_NAME,
        instructions=MEMORY_PROMPT,
        model=model_name,
        model_settings=model_settings_obj,
        tools=[],
    )


def normalize_memory_payload(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        text = raw.strip()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                raw = json.loads(text[start : end + 1])
            else:
                return []

    if not isinstance(raw, dict):
        return []

    memories = raw.get("memories", [])
    if not isinstance(memories, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in memories:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        category = str(item.get("category", "")).strip().lower()
        if not text or category not in VALID_CATEGORIES:
            continue
        confidence = item.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        dedup = str(item.get("deduplication_key", "")).strip()
        if not dedup:
            dedup = hashlib.sha1(f"{category}:{text.lower()}".encode()).hexdigest()[:24]
        normalized.append(
            {
                "text": text,
                "category": category,
                "confidence": confidence,
                "deduplication_key": dedup,
            }
        )
    return normalized[:5]


def _extract_simple_memories(
    message: str, conversation_context: str | None = None
) -> list[dict[str, Any]]:
    """Deterministic extraction for obvious durable facts.

    This avoids relying on a second LLM call for common statements like
    "My favorite is Python".
    """
    text = " ".join((message or "").strip().split())
    if not text:
        return []

    lower = text.lower()
    context = (conversation_context or "").lower()
    memories: list[dict[str, Any]] = []

    name_match = re.search(r"\bmy name is\s+[\"']?([^\"'.!,?]+)", text, re.I)
    if name_match:
        name = name_match.group(1).strip()
        if name:
            memories.append(
                {
                    "text": f"User's name is {name}.",
                    "category": "personal",
                    "confidence": 1.0,
                    "deduplication_key": "user-name",
                }
            )

    fav_match = re.search(
        r"\b(?:my favorite (?:programming )?language is|favorite (?:programming )?language[:=]?|my favorite is)\s+[\"']?([A-Za-z][A-Za-z0-9+#._ -]{0,40})",
        text,
        re.I,
    )
    if fav_match:
        value = fav_match.group(1).strip().strip(".!,?;:\"'")
        if value and (
            "programming language" in lower
            or "programming language" in context
            or "my favorite is" in lower
        ):
            memories.append(
                {
                    "text": f"User's favorite programming language is {value}.",
                    "category": "preference",
                    "confidence": 1.0,
                    "deduplication_key": "favorite-programming-language",
                }
            )

    return memories


async def extract_memories_from_message(
    message: str,
    settings: AgentSettings | None = None,
    conversation_context: str | None = None,
) -> list[dict[str, Any]]:
    if not message or not message.strip():
        return []

    context = (conversation_context or "").strip()
    deterministic = _extract_simple_memories(message, context)

    try:
        agent = await build_memory_agent(settings)
        run_config = get_run_config(settings)
        content = f"User message:\n{message.strip()}"
        if context:
            content = f"Recent conversation context:\n{context}\n\n{content}"
        prompt = [{"role": "user", "content": content}]
        result = await Runner.run(
            agent, input=prompt, max_turns=1, run_config=run_config
        )
        final_output = getattr(result, "final_output", "")
        model_memories = normalize_memory_payload(final_output)
    except Exception:
        logger.exception(
            "Memory model extraction failed; using deterministic memories only"
        )
        model_memories = []

    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*deterministic, *model_memories]:
        key = (
            str(item.get("deduplication_key") or item.get("text") or "").strip().lower()
        )
        if not key or key in seen:
            continue
        seen.add(key)
        combined.append(item)
    return combined[:5]


def make_memory_record(
    memory: dict[str, Any],
    *,
    conversation_id: str | None,
    message_id: str | None,
    source: str = "user_message",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    dedup = (
        memory.get("deduplication_key")
        or hashlib.sha1(
            f"{memory.get('category', '')}:{memory.get('text', '').lower()}".encode()
        ).hexdigest()[:24]
    )
    record_id = hashlib.sha1(
        f"{dedup}:{conversation_id or ''}:{message_id or ''}".encode()
    ).hexdigest()[:32]
    return {
        "id": record_id,
        "text": memory.get("text", "").strip(),
        "category": memory.get("category", "").strip().lower(),
        "source": source,
        "timestamp": now,
        "deduplication_key": dedup,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }
