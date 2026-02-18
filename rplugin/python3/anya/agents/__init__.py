import os
from typing import TYPE_CHECKING

from agents import Agent
from agents.models.default_models import get_default_model_settings
from openai.types.shared import Reasoning

from .dynamic_instructions import (
    generate_dynamic_code_instructions,
    update_agent_instructions,
)

from .utils import get_instructions
from ..system_prompt import apply_system_prompt
from ..libs import get_libs_prompt

if TYPE_CHECKING:
    from ..protocol import AgentSettings

MAIN_AGENT_NAME = "Code"
MAIN_ASSISTANT_NAME = "Anya"


def _parse_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if not v:
        return None

    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    return v if v in allowed else None


async def CodeAgent(
    thinking_budget=None,
    nvim=None,
    settings: "AgentSettings | None" = None,
) -> Agent:
    """Create a code agent with only the run_code tool.

    Args:
        thinking_budget: Optional thinking budget for reasoning models.
        nvim: Optional nvim instance (not used in daemon context)
        settings: Optional AgentSettings from client.

    Returns:
        Configured Agent instance
    """
    from agents.run import RunConfig

    RunConfig.tracing_disabled = True

    # Get base instructions
    base_instructions = get_instructions("code.md")

    # Generate dynamic instructions
    dynamic_instructions = await generate_dynamic_code_instructions([])

    # Append built-in libs section (auto-discovered from anya.libs)
    libs_instructions = get_libs_prompt()

    # Combine instructions
    instructions = update_agent_instructions(base_instructions, dynamic_instructions)
    instructions = update_agent_instructions(instructions, libs_instructions)

    # Expand placeholders and append environment context at the end.
    instructions = apply_system_prompt(instructions, nvim=nvim)

    # ------
    # Configuration from settings or environment
    # ------
    def _get_setting(attr: str, env_key: str, *fallback_keys, default=None):
        """Get setting from AgentSettings or environment variables."""
        if settings:
            val = getattr(settings, attr, None)
            if val is not None:
                return val
        # Fall back to environment
        for k in (env_key, *fallback_keys):
            v = os.environ.get(k)
            if v is not None:
                return v
        return default

    # Model config
    model_name = (
        _get_setting("model", "ANYA_MODEL", default="gpt-4.1") or "gpt-4.1"
    ).strip()
    model_settings_obj = get_default_model_settings(model_name.lower())

    # API type
    api_type = _get_setting(
        "api_type", "ANYA_API_TYPE", "ANYA_OPENAI_API_TYPE", default="responses"
    )
    if api_type:
        api_type = api_type.strip().lower()
        if api_type not in {"chat_completions", "responses"}:
            api_type = "responses"
    else:
        api_type = "responses"

    if hasattr(model_settings_obj, "api_type"):
        model_settings_obj.api_type = api_type

    # Get thinking budget
    if thinking_budget is None:
        thinking_budget = _get_setting("thinking_budget", "ANYA_THINKING_BUDGET")

    if thinking_budget is not None and model_settings_obj.reasoning is not None:
        effort = _parse_reasoning_effort(thinking_budget) or "medium"
        model_settings_obj.reasoning.effort = effort
        model_settings_obj.reasoning.summary = "auto"

    from ..tools import run_code

    config = {
        "name": MAIN_AGENT_NAME,
        "instructions": instructions,
        "model": model_name,
        "model_settings": model_settings_obj,
        "tools": [run_code],
    }

    return Agent(**config)


__all__ = [
    "CodeAgent",
    "MAIN_AGENT_NAME",
    "MAIN_ASSISTANT_NAME",
]
