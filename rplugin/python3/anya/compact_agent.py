"""Conversation compaction using a simple LLM call.

Summarizes an entire conversation history into a single dense summary,
allowing continuation of long conversations that would otherwise exceed
the context window. Uses the same API settings as the main agent.
"""

import os
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import AgentSettings

logger = logging.getLogger("anya.compact_agent")

# Timeout in seconds for the compaction API call
_API_TIMEOUT = 60.0

# Maximum characters per message to include when building the compaction prompt
_MAX_MESSAGE_CHARS = 8000


def _truncate(text: str, max_chars: int = _MAX_MESSAGE_CHARS) -> str:
    """Truncate text to a maximum number of characters."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n...\n" + text[-half:]


async def _build_client(settings: "AgentSettings | None"):
    """Build a client from AgentSettings (or env vars if settings is None).

    Re-uses the same logic as title_agent._build_client.
    Returns a (client, model_name, api_type) tuple.
    """
    # Resolve settings vs environment
    if settings:
        model_name = settings.model or "gpt-4.1"
        api_key = settings.api_key
        api_base = settings.api_base
        api_type = settings.api_type or "responses"
    else:
        model_name = os.environ.get("ANYA_MODEL", "gpt-4.1")
        api_key = os.environ.get("ANYA_API_KEY") or os.environ.get("OPENAI_API_KEY")
        api_base = os.environ.get("ANYA_API_BASE") or os.environ.get("OPENAI_API_BASE")
        api_type = os.environ.get("ANYA_API_TYPE", "responses")

    if api_type == "copilot":
        from openai import AsyncOpenAI
        from .copilot_auth import get_auth

        auth = get_auth()
        copilot_token = await auth.get_copilot_token()
        copilot_base = auth.get_api_base()

        client = AsyncOpenAI(
            api_key=copilot_token,
            base_url=copilot_base,
            timeout=_API_TIMEOUT,
            default_headers={
                "Editor-Version": "Neovim/0.12",
                "Editor-Plugin-Version": "anya/0.0.1",
                "Copilot-Integration-Id": "copilot-chat",
                "Openai-Intent": "conversation-edits",
            },
        )
        return client, model_name, api_type

    if api_type == "anthropic":
        from anthropic import AsyncAnthropic

        client_kwargs: dict = {"timeout": _API_TIMEOUT}
        if api_key:
            client_kwargs["api_key"] = api_key
        if api_base:
            client_kwargs["base_url"] = api_base
        return AsyncAnthropic(**client_kwargs), model_name, api_type

    # Plain OpenAI / OpenRouter / any chat_completions-compatible endpoint
    from openai import AsyncOpenAI

    client_kwargs: dict = {"timeout": _API_TIMEOUT}
    if api_key:
        client_kwargs["api_key"] = api_key

    # Auto-detect OpenRouter if model contains '/' and no explicit base URL
    base_url = api_base
    if not base_url and "/" in model_name:
        base_url = "https://openrouter.ai/api/v1"
    if base_url:
        client_kwargs["base_url"] = base_url

    return AsyncOpenAI(**client_kwargs), model_name, api_type


def _build_compaction_prompt(history: list[dict]) -> str:
    """Build a compaction prompt from conversation history."""
    lines = [
        "You are summarizing a conversation between a user and an AI coding assistant.",
        "Produce a detailed, structured summary that preserves:",
        "- All technical decisions, file changes, and code written",
        "- The current state of work (what is done, what is in progress, what is next)",
        "- Any important context, constraints, or user preferences mentioned",
        "- Key errors encountered and their resolutions",
        "",
        "Write in the third person (e.g. \"The user asked..., The assistant wrote...\").",
        "Be as detailed as needed — this summary will replace the full history.",
        "Do NOT truncate or omit technical details. Include exact file paths, function names,",
        "and code snippets where they are important for continuing the work.",
        "",
        "CONVERSATION HISTORY:",
        "",
    ]

    for msg in history:
        role = msg.get("role", "unknown").upper()
        content = _truncate(msg.get("content", ""), _MAX_MESSAGE_CHARS)
        lines.append(f"[{role}]")
        lines.append(content)
        lines.append("")

    lines.append("SUMMARY:")
    return "\n".join(lines)


async def compact_conversation(
    history: list[dict],
    settings: "AgentSettings | None",
) -> str | None:
    """Summarize a conversation history into a single dense summary.

    Args:
        history: List of {role, content} dicts representing the full conversation
        settings: AgentSettings with API configuration, or None to use env vars

    Returns:
        Summary string or None on failure
    """
    if not history:
        return None

    try:
        client, model_name, api_type = await _build_client(settings)

        prompt = _build_compaction_prompt(history)

        if api_type == "anthropic":
            response = await client.messages.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.1,
            )
            summary = response.content[0].text if response.content else ""
        else:
            response = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=0.1,
            )
            summary = response.choices[0].message.content or ""

        summary = summary.strip()
        return summary if summary else None

    except Exception as e:
        import traceback

        log_path = os.path.expanduser("~/.local/share/anya/plugin_errors.log")
        with open(log_path, "a") as f:
            f.write("\n--- compact_agent error ---\n")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
            f.write("---\n")
        logger.warning(f"Compaction failed: {e}")
        return None
