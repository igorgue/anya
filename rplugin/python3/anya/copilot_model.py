"""GitHub Copilot model provider for OpenAI Agents SDK.

Provides a ModelProvider that uses the Copilot API with chat completions.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import AgentSettings

logger = logging.getLogger("anya.copilot_model")


async def get_copilot_model_provider(settings: "AgentSettings | None" = None):
    """Get a ModelProvider configured for GitHub Copilot.

    Args:
        settings: Optional AgentSettings from client.

    Returns:
        A ModelProvider instance configured for Copilot.

    Note:
        Copilot only supports chat completions, not the responses API.
    """
    from agents import Model, ModelProvider, OpenAIChatCompletionsModel
    from openai import AsyncOpenAI

    from .copilot_auth import get_auth

    auth = get_auth()

    # Get a fresh Copilot token
    copilot_token = await auth.get_copilot_token()
    api_base = auth.get_api_base()

    # Get model name from settings
    if settings and settings.model:
        model_name = settings.model
    else:
        import os

        model_name = os.environ.get("ANYA_MODEL", "gpt-4o")

    # Create OpenAI client with Copilot configuration
    client = AsyncOpenAI(
        api_key=copilot_token,
        base_url=api_base,
        default_headers={
            "Editor-Version": "Neovim/0.12",
            "Editor-Plugin-Version": "anya/0.0.1",
            "Copilot-Integration-Id": "copilot-chat",
            "Openai-Intent": "conversation-edits",
        },
    )

    class CopilotModelProvider(ModelProvider):
        """ModelProvider that wraps Copilot chat completions."""

        def get_model(self, model_name_arg: str | None) -> Model:
            # The token might have expired, get a fresh one
            # Note: We can't easily refresh the client here, but the token
            # has a ~30 min lifetime which should be enough for most requests
            return OpenAIChatCompletionsModel(
                model=model_name_arg or model_name,
                openai_client=client,
            )

    logger.info(f"Created Copilot model provider for model={model_name}, base_url={api_base}")
    return CopilotModelProvider()
