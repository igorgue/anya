"""Custom model provider for OpenRouter and other OpenAI-compatible APIs.

This is needed for providers like OpenRouter that use model names like 'anthropic/claude-opus-4'
which contain '/' or ':' characters that the OpenAI Agents SDK doesn't handle directly.

Also supports Anthropic API via the anthropic package.
"""

import os
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import AgentSettings

logger = logging.getLogger("anya.model_provider")


def needs_custom_provider(
    model: str, base_url: str | None = None, api_type: str = "responses"
) -> bool:
    """Check if a custom model provider is needed.

    Returns True if:
    1. Model name contains '/' (e.g., OpenRouter models like 'anthropic/claude-opus-4')
    2. Model name contains ':' (e.g., OpenRouter variants like 'model:free')
    3. A custom base URL is specified (e.g., custom API endpoints)
    4. API type is 'anthropic' (native Anthropic API)
    5. API type is 'copilot' (GitHub Copilot API)
    6. Model name starts with 'github-copilot/'
    """
    return (
        "/" in model
        or ":" in model
        or base_url is not None
        or api_type in ("anthropic", "copilot")
        or model.startswith("github-copilot/")
    )


def get_custom_model_provider(
    settings: "AgentSettings | None" = None,
):
    """Get a custom ModelProvider for OpenRouter, Anthropic, or other custom API endpoints.

    Args:
        settings: Optional AgentSettings from client. If provided, these override
                  environment variables.

    Returns:
        A ModelProvider instance, or None if not needed.
    """
    # Use settings if provided, otherwise fall back to environment
    if settings:
        model = settings.model or "gpt-4.1"
        base_url = settings.api_base
        api_key = settings.api_key
        api_type = settings.api_type or "responses"
    else:
        model = os.environ.get("ANYA_MODEL", "gpt-4.1")
        base_url = os.environ.get("ANYA_API_BASE") or os.environ.get("OPENAI_API_BASE")
        api_key = os.environ.get("ANYA_API_KEY") or os.environ.get("OPENAI_API_KEY")
        api_type = os.environ.get("ANYA_API_TYPE", "responses")

    if not needs_custom_provider(model, base_url, api_type):
        return None

    # Handle Anthropic API type (first, as it has its own auth)
    if api_type == "anthropic":
        from .anthropic_model import get_anthropic_model_provider

        return get_anthropic_model_provider(settings)

    # Handle Copilot API type (has its own auth, no api_key needed)
    if api_type == "copilot":
        from .copilot_model import get_copilot_model_provider
        import asyncio

        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, create a new loop in a thread
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run, get_copilot_model_provider(settings)
                    )
                    return future.result()
            else:
                return loop.run_until_complete(get_copilot_model_provider(settings))
        except RuntimeError:
            return asyncio.run(get_copilot_model_provider(settings))

    # For other custom providers, we need an API key
    if not api_key:
        logger.warning("Custom provider needed but no API key found")
        return None

    # Handle OpenAI-compatible APIs (chat_completions)
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


def get_run_config(settings: "AgentSettings | None" = None):
    """Get RunConfig with custom model provider if needed.

    Args:
        settings: Optional AgentSettings from client. If provided, these override
                  environment variables.

    Returns:
        RunConfig with custom model provider, or None if not needed.
    """
    # Use settings if provided, otherwise fall back to environment
    if settings:
        model = settings.model or "gpt-4.1"
        base_url = settings.api_base
        api_type = settings.api_type or "responses"
    else:
        model = os.environ.get("ANYA_MODEL", "gpt-4.1")
        base_url = os.environ.get("ANYA_API_BASE") or os.environ.get("OPENAI_API_BASE")
        api_type = os.environ.get("ANYA_API_TYPE", "responses")

    if not needs_custom_provider(model, base_url, api_type):
        return None

    provider = get_custom_model_provider(settings)
    if not provider:
        return None

    try:
        from agents import RunConfig
    except ImportError:
        return None

    # For chat_completions API, anthropic API, or copilot API, disable nested handoff history
    # as non-OpenAI providers don't support the nested message format
    nest_handoff = api_type not in ("chat_completions", "anthropic", "copilot")

    return RunConfig(model_provider=provider, nest_handoff_history=nest_handoff)
