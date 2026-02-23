"""Title generation for conversations using a simple LLM call.

Generates a short descriptive title for a conversation based on the
first user message and assistant response. Uses the same API settings
as the main agent but always goes through chat_completions for simplicity.
"""

import re
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import AgentSettings

logger = logging.getLogger("anya.title_agent")

# Timeout in seconds for the title generation API call
_API_TIMEOUT = 30.0

# Maximum characters per message to include in title generation prompt
_MAX_CONTENT_CHARS = 200


def _clean_content(text: str) -> str:
    """Strip Anya buffer markers and noise from content before passing to LLM.

    Removes:
    - HTML-style markers: <!-- at: ... --> and <!-- am: ... -->
    - Tool call headers: [[tool_name]]
    - Collapses excess whitespace
    """
    # Strip HTML marker comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Strip [[tool_name]] tool headers
    text = re.sub(r"\[\[.*?\]\]", "", text)
    # Collapse runs of whitespace / blank lines
    text = re.sub(r"
{3,}", "

", text)
    text = text.strip()
    return text[:_MAX_CONTENT_CHARS]


async def generate_title(
    user_message: str,
    assistant_message: str,
    settings: "AgentSettings",
) -> str | None:
    """Generate a short title for a conversation.

    Args:
        user_message: The user's first message
        assistant_message: The assistant's first response (raw buffer text)
        settings: AgentSettings with API configuration

    Returns:
        Generated title string or None on failure
    """
    try:
        from openai import AsyncOpenAI

        # Build client with same settings as main agent.
        # Use a short timeout so a hung request doesn't block the fidget forever.
        client_kwargs: dict = {"timeout": _API_TIMEOUT}
        if settings.api_key:
            client_kwargs["api_key"] = settings.api_key

        # Resolve base URL: use explicit setting, or auto-detect OpenRouter
        base_url = settings.api_base
        if not base_url and settings.model and "/" in settings.model:
            base_url = "https://openrouter.ai/api/v1"
        if base_url:
            client_kwargs["base_url"] = base_url

        client = AsyncOpenAI(**client_kwargs)

        # Clean and truncate both messages
        user_snippet = _clean_content(user_message)
        assistant_snippet = _clean_content(assistant_message)

        prompt = (
            "Generate a short, descriptive title (maximum 8 words) for this "
            "conversation. Output ONLY the title — no quotes, no trailing "
            "punctuation, no explanation.

"
            f"User: {user_snippet}
"
            f"Assistant: {assistant_snippet}
"
            "Title:"
        )

        response = await client.chat.completions.create(
            model=settings.model or "gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=30,
            temperature=0.3,
        )

        raw = response.choices[0].message.content or ""
        title = raw.strip().strip(""'").rstrip(".!?").strip()
        return title if title else None

    except Exception as e:
        import traceback, os
        log_path = os.path.expanduser("~/.local/share/anya/plugin_errors.log")
        with open(log_path, "a") as f:
            f.write("
--- title_agent error ---
")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
            f.write("---
")
        logger.warning(f"Title generation failed: {e}")
        return None
