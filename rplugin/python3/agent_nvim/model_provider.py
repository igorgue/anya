"""Custom model provider for OpenRouter and other OpenAI-compatible APIs."""

import os


def get_custom_run_config():
    """Get RunConfig with custom model provider for models with '/' in their name.

    This is needed for providers like OpenRouter that use model names like
    'anthropic/claude-opus-4' which the SDK incorrectly parses as provider prefixes.

    Returns:
        RunConfig with custom model provider, or None if not needed.
    """
    model = os.environ.get("AGENT_MODEL", "gpt-5.1")

    # Only use custom provider if model name contains '/' (e.g., OpenRouter)
    if "/" not in model:
        return None

    base_url = os.environ.get("AGENT_BASE_URL")
    api_key = os.environ.get("AGENT_API_KEY") or os.environ.get("OPENAI_API_KEY")

    if not base_url and not api_key:
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

    return RunConfig(model_provider=CustomModelProvider())
