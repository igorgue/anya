"""Custom model provider for OpenRouter and other OpenAI-compatible APIs."""

import os
import logging

logger = logging.getLogger("agent_nvim")


def get_custom_run_config():
    """Get RunConfig with custom model provider for custom API endpoints.

    This is needed for:
    - Providers like OpenRouter that use model names like 'anthropic/claude-opus-4'
    - Any custom API endpoint specified via AGENT_BASE_URL

    Returns:
        RunConfig with custom model provider, or None if not needed.
    """
    model = os.environ.get("AGENT_MODEL", "gpt-5.1")
    base_url = os.environ.get("AGENT_BASE_URL")
    api_key = os.environ.get("AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")

    # Use custom provider if:
    # 1. Model name contains '/' (e.g., OpenRouter models like 'anthropic/claude-opus-4')
    # 2. A custom base URL is specified (e.g., custom API endpoints)
    needs_custom_provider = "/" in model or base_url is not None

    logger.info(
        f"get_custom_run_config: model={model}, base_url={base_url}, needs_custom={needs_custom_provider}"
    )

    if not needs_custom_provider:
        return None

    if not api_key:
        logger.warning("Custom provider needed but no API key found")
        return None

    try:
        from agents import (
            Model,
            ModelProvider,
            OpenAIChatCompletionsModel,
            RunConfig,
            set_tracing_disabled,
        )
        from openai import AsyncOpenAI
    except ImportError:
        return None

    client_kwargs = {}
    if base_url:
        client_kwargs["base_url"] = base_url
    if api_key:
        client_kwargs["api_key"] = api_key

    custom_client = AsyncOpenAI(**client_kwargs)

    class CustomModelProvider(ModelProvider):
        def get_model(self, model_name: str | None) -> Model:
            return OpenAIChatCompletionsModel(
                model=model_name or model,
                openai_client=custom_client,
            )

    # Disable tracing for custom providers by default
    if os.environ.get("AGENT_DISABLE_TRACING", "1") == "1":
        set_tracing_disabled(True)

    # For chat_completions API, disable nested handoff history as non-OpenAI
    # providers don't support the nested message format
    api_type = os.environ.get("AGENT_API_TYPE", "responses")
    nest_handoff = api_type != "chat_completions"

    return RunConfig(
        model_provider=CustomModelProvider(), nest_handoff_history=nest_handoff
    )
