"""Title generation for conversations using a simple LLM call.

Generates a short descriptive title for a conversation based on the
first user message and assistant response. Uses the same API settings
as the main agent.
"""

import logging
import os
import re
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
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[.*?\]\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text[:_MAX_CONTENT_CHARS]


def _parse_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None

    effort = str(value).strip().lower()
    if not effort:
        return None

    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    return effort if effort in allowed else None


def _get_reasoning_effort(settings: "AgentSettings | None") -> str | None:
    raw = None
    if settings is not None:
        raw = settings.thinking_budget
    if raw is None:
        raw = os.environ.get("ANYA_THINKING_BUDGET")
    if raw is None or not str(raw).strip():
        return None
    return _parse_reasoning_effort(raw) or "medium"


def _build_openai_request_kwargs(
    *,
    api_type: str,
    model_name: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    reasoning_effort: str | None,
) -> dict:
    if api_type == "responses":
        kwargs = {
            "model": model_name,
            "input": prompt,
            "max_output_tokens": max_tokens,
        }
        if reasoning_effort is not None:
            kwargs["reasoning"] = {
                "effort": reasoning_effort,
                "summary": "auto",
            }
        else:
            kwargs["temperature"] = temperature
        return kwargs

    kwargs = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": max_tokens,
    }
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    else:
        kwargs["temperature"] = temperature
    return kwargs


async def _build_client(settings: "AgentSettings | None"):
    """Build a client from AgentSettings (or env vars if settings is None).

    Handles all provider types: copilot, anthropic, openrouter, and plain OpenAI/compatible.
    Returns a (client, model_name, api_type) tuple.
    """
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

    from openai import AsyncOpenAI

    client_kwargs: dict = {"timeout": _API_TIMEOUT}
    if api_key:
        client_kwargs["api_key"] = api_key

    base_url = api_base
    if not base_url and "/" in model_name:
        base_url = "https://openrouter.ai/api/v1"
    if base_url:
        client_kwargs["base_url"] = base_url

    return AsyncOpenAI(**client_kwargs), model_name, api_type


async def generate_title(
    user_message: str,
    assistant_message: str,
    settings: "AgentSettings | None",
) -> str | None:
    """Generate a short title for a conversation."""
    try:
        client, model_name, api_type = await _build_client(settings)
        reasoning_effort = _get_reasoning_effort(settings)

        user_snippet = _clean_content(user_message)
        assistant_snippet = _clean_content(assistant_message)

        prompt = (
            "Generate a short, descriptive title (maximum 8 words) for this "
            "conversation. Output ONLY the title — no quotes, no trailing "
            "punctuation, no explanation.\n\n"
            f"User: {user_snippet}\n"
            f"Assistant: {assistant_snippet}\n"
            "Title:"
        )

        if api_type == "anthropic":
            response = await client.messages.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=30,
                temperature=0.3,
            )
            raw = response.content[0].text if response.content else ""
        elif api_type == "responses":
            response = await client.responses.create(
                **_build_openai_request_kwargs(
                    api_type=api_type,
                    model_name=model_name,
                    prompt=prompt,
                    max_tokens=30,
                    temperature=0.3,
                    reasoning_effort=reasoning_effort,
                )
            )
            raw = response.output_text or ""
        else:
            response = await client.chat.completions.create(
                **_build_openai_request_kwargs(
                    api_type=api_type,
                    model_name=model_name,
                    prompt=prompt,
                    max_tokens=30,
                    temperature=0.3,
                    reasoning_effort=reasoning_effort,
                )
            )
            raw = response.choices[0].message.content or ""

        title = raw.strip().strip("“”'").rstrip(".!?").strip()
        return title if title else None

    except Exception as e:
        import traceback

        log_path = os.path.expanduser("~/.local/share/anya/plugin_errors.log")
        with open(log_path, "a") as f:
            f.write("\n--- title_agent error ---\n")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
            f.write("---\n")
        logger.warning(f"Title generation failed: {e}")
        return None
