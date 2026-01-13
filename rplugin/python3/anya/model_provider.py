"""Custom model provider for OpenRouter and other OpenAI-compatible APIs.

This is needed for providers like OpenRouter that use model names like 'anthropic/claude-opus-4'
which contain '/' or ':' characters that the OpenAI Agents SDK doesn't handle directly.
"""

import os
import logging

logger = logging.getLogger("anya.model_provider")


def needs_custom_provider(model: str, base_url: str | None = None) -> bool:
    """Check if a custom model provider is needed.

    Returns True if:
    1. Model name contains '/' (e.g., OpenRouter models like 'anthropic/claude-opus-4')
    2. Model name contains ':' (e.g., OpenRouter variants like 'model:free')
    3. A custom base URL is specified (e.g., custom API endpoints)
    """
    return "/" in model or ":" in model or base_url is not None


def get_custom_model_provider():
    """Get a custom ModelProvider for OpenRouter or other custom API endpoints.

    Returns:
        A ModelProvider instance, or None if not needed.
    """
    model = os.environ.get("ANYA_MODEL", "gpt-4.1")
    base_url = os.environ.get("ANYA_API_BASE") or os.environ.get("OPENAI_API_BASE")
    api_key = os.environ.get("ANYA_API_KEY") or os.environ.get("OPENAI_API_KEY")

    if not needs_custom_provider(model, base_url):
        return None

    if not api_key:
        logger.warning("Custom provider needed but no API key found")
        return None

    try:
        from agents import Model, ModelProvider, OpenAIChatCompletionsModel
        from openai import AsyncOpenAI
    except ImportError as e:
        logger.error(f"Failed to import agents SDK: {e}")
        return None

    # Default to OpenRouter base URL if model contains '/' but no base URL is set
    if "/" in model and not base_url:
        base_url = "https://openrouter.ai/api/v1"
        logger.info(f"Using OpenRouter base URL for model {model}")

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    custom_client = AsyncOpenAI(**client_kwargs)

    class CustomModelProvider(ModelProvider):
        def get_model(self, model_name: str | None) -> Model:
            return OpenAIChatCompletionsModel(
                model=model_name or model,
                openai_client=custom_client,
            )

    logger.info(f"Created custom model provider for model={model}, base_url={base_url}")
    return CustomModelProvider()


def get_run_config():
    """Get RunConfig with custom model provider if needed.

    Returns:
        RunConfig with custom model provider, or None if not needed.
    """
    model = os.environ.get("ANYA_MODEL", "gpt-4.1")
    base_url = os.environ.get("ANYA_API_BASE") or os.environ.get("OPENAI_API_BASE")

    if not needs_custom_provider(model, base_url):
        return None

    provider = get_custom_model_provider()
    if not provider:
        return None

    try:
        from agents import RunConfig, set_tracing_disabled
    except ImportError:
        return None

    # Disable tracing for custom providers by default
    if os.environ.get("ANYA_DISABLE_TRACING", "1") == "1":
        set_tracing_disabled(True)

    # For chat_completions API, disable nested handoff history as non-OpenAI
    # providers don't support the nested message format
    api_type = os.environ.get("ANYA_API_TYPE", "responses")
    nest_handoff = api_type != "chat_completions"

    return RunConfig(model_provider=provider, nest_handoff_history=nest_handoff)
