"""Title generation for conversations using a simple LLM call.

Generates a short descriptive title for a conversation based on the
first user message and assistant response. Uses the same API settings
as the main agent.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from .reasoning import (
    build_anthropic_thinking_param,
    build_openai_reasoning_params,
    get_reasoning_effort,
)

if TYPE_CHECKING:
    from .protocol import AgentSettings

logger = logging.getLogger("anya.title_agent")

# Timeout in seconds for the title generation API call
_API_TIMEOUT = 30.0

# Maximum characters per message to include in title generation prompt
_MAX_CONTENT_CHARS = 200

# Prefixes that identify reasoning models (no temperature support, need more tokens)
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def _is_reasoning_model(model: str) -> bool:
    """Check if a model is a reasoning model based on its name."""
    return model.lower().startswith(_REASONING_PREFIXES)


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


class TitleAgent:
    """Class-based title agent for direct instantiation with a pre-built client."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        api_type: str = "responses",
        max_tokens: int = 24,
    ) -> None:
        self.client = client
        self.model = model
        self.api_type = api_type
        self.max_tokens = max_tokens

    def _is_reasoning_model(self, model: str | None = None) -> bool:
        return _is_reasoning_model(model or self.model or "")

    def _build_prompt(self, text: str) -> str:
        return (
            "Generate a very short, specific conversation title (2-6 words). "
            "Return only the title, with no quotes, punctuation suffix, or explanation.\n\n"
            f"Conversation:\n{text.strip()}"
        )

    def _request_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model}
        if self.api_type == "responses":
            kwargs["max_output_tokens"] = self.max_tokens
        elif self.api_type == "chat_completions":
            if self._is_reasoning_model():
                kwargs["max_completion_tokens"] = self.max_tokens
            else:
                kwargs["max_tokens"] = self.max_tokens
        return kwargs

    def _extract_responses_text(self, response: Any) -> str:
        text = getattr(response, "output_text", None)
        if text:
            return text.strip()

        output = getattr(response, "output", None) or []
        chunks: list[str] = []
        for item in output:
            content = getattr(item, "content", None) or []
            for part in content:
                part_text = getattr(part, "text", None)
                if part_text:
                    chunks.append(part_text)
        return " ".join(chunks).strip()

    def _extract_chat_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text") or item.get("content")
                    if txt:
                        parts.append(str(txt))
                else:
                    txt = getattr(item, "text", None)
                    if txt:
                        parts.append(txt)
            return " ".join(parts).strip()
        return ""

    def _normalize_title(self, text: str) -> str:
        title = (text or "").strip().strip("\"'")
        title = " ".join(title.split())
        if len(title) > 80:
            title = title[:80].rstrip()
        return title or "New chat"

    async def generate_title(self, text: str) -> str:
        prompt = self._build_prompt(text)
        kwargs = self._request_kwargs()

        logger.info(
            "Generating title with api_type=%s model=%s reasoning=%s",
            self.api_type,
            self.model,
            self._is_reasoning_model(),
        )

        if self.api_type == "responses":
            response = await self.client.responses.create(
                input=prompt,
                **kwargs,
            )
            raw_title = self._extract_responses_text(response)
        else:
            response = await self.client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You write concise conversation titles.",
                    },
                    {"role": "user", "content": prompt},
                ],
                **kwargs,
            )
            raw_title = self._extract_chat_text(response)

        title = self._normalize_title(raw_title)
        logger.info("Generated title: %s", title)
        return title


async def generate_title(
    user_message: str,
    assistant_message: str,
    settings: "AgentSettings | None",
) -> str | None:
    """Generate a short title for a conversation.

    This is the module-level function used by the handler.
    It builds the client from settings and calls the LLM to generate a title.

    Args:
        user_message: The user's message text
        assistant_message: The assistant's response text
        settings: AgentSettings with API configuration, or None to use env vars

    Returns:
        Generated title string or None on failure
    """
    try:
        client, model_name, api_type = await _build_client(settings)
        reasoning_effort = get_reasoning_effort(settings)

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

        is_reasoning = _is_reasoning_model(model_name)

        # Reasoning models need more output tokens (reasoning consumes from
        # the budget) and don't support the temperature parameter.
        # Also force low reasoning effort for title generation.
        if is_reasoning and reasoning_effort is None:
            reasoning_effort = "low"

        max_tokens = 256 if is_reasoning else 30

        if api_type == "anthropic":
            anthropic_kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            }
            if not is_reasoning:
                anthropic_kwargs["temperature"] = 0.3
            thinking = build_anthropic_thinking_param(reasoning_effort)
            if thinking is not None:
                anthropic_kwargs["thinking"] = thinking
            response = await client.messages.create(**anthropic_kwargs)
            raw = response.content[0].text if response.content else ""
        elif api_type == "responses":
            kwargs: dict[str, Any] = {
                "model": model_name,
                "input": prompt,
                "max_output_tokens": max_tokens,
            }
            reasoning_params = build_openai_reasoning_params(api_type, reasoning_effort)
            if reasoning_params:
                kwargs.update(reasoning_params)
            elif not is_reasoning:
                kwargs["temperature"] = 0.3
            response = await client.responses.create(**kwargs)
            raw = response.output_text or ""
        else:
            kwargs = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": max_tokens,
            }
            reasoning_params = build_openai_reasoning_params(api_type, reasoning_effort)
            if reasoning_params:
                kwargs.update(reasoning_params)
            elif not is_reasoning:
                kwargs["temperature"] = 0.3
            response = await client.chat.completions.create(**kwargs)
            raw = response.choices[0].message.content or ""

        title = raw.strip().strip('""\'').rstrip(".!?").strip()
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
