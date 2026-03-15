import os
from textwrap import dedent
from typing import TYPE_CHECKING

from agents import Agent
from agents.models.default_models import get_default_model_settings

from .dynamic_instructions import (
    generate_dynamic_code_instructions,
    update_agent_instructions,
)

from .utils import get_instructions
from ..system_prompt import apply_system_prompt
from ..libs import get_libs_prompt
from ..reasoning import apply_reasoning_settings

if TYPE_CHECKING:
    from ..protocol import AgentSettings

MAIN_AGENT_NAME = "Code"
DO_AGENT_NAME = "Do"
MAIN_ASSISTANT_NAME = "Anya"





def _build_do_instructions(cwd: str | None = None) -> str:
    instructions = dedent(
        """
        You are Anya's fast headless buffer-editing agent for `:Anya do`.

        Your job is to modify the current Neovim buffer as quickly as possible.

        Rules:
        - Prefer a single `execute` tool call.
        - Inside `execute`, usually transform the provided buffer content directly with Python string operations.
        - Use `from anya.libs import buffer` and call `buffer.modify(content)` with the COMPLETE new buffer text.
        - Do not explain your work.
        - Do not ask the user questions.
        - Do not read unrelated project files unless the instruction explicitly requires extra context.
        - Do not inspect docs, skills, AGENTS.md, or web resources unless absolutely necessary.
        - Finish as soon as the buffer has been updated.
        """
    ).strip()
    return apply_system_prompt(
        instructions,
        nvim=None,
        cwd=cwd,
        include_project_docs=False,
        include_skills=False,
    )


async def CodeAgent(
    thinking_budget=None,
    nvim=None,
    settings: "AgentSettings | None" = None,
    cwd: str | None = None,
) -> Agent:
    """Create a code agent with the execute tool.

    Args:
        thinking_budget: Optional thinking budget for reasoning models.
        nvim: Optional nvim instance (not used in daemon context)
        settings: Optional AgentSettings from client.
        cwd: Optional explicit working directory (used in daemon mode).

    Returns:
        Configured Agent instance
    """
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
    instructions = apply_system_prompt(instructions, nvim=nvim, cwd=cwd)

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

    # API type - now supports "responses", "chat_completions", "anthropic", and "copilot"
    api_type = _get_setting(
        "api_type", "ANYA_API_TYPE", "ANYA_OPENAI_API_TYPE", default="responses"
    )
    if api_type:
        api_type = api_type.strip().lower()
        # Allow "responses", "chat_completions", "anthropic", and "copilot"
        if api_type not in {"chat_completions", "responses", "anthropic", "copilot"}:
            api_type = "responses"
    else:
        api_type = "responses"

    if hasattr(model_settings_obj, "api_type"):
        model_settings_obj.api_type = api_type

    # Get thinking budget
    if thinking_budget is None:
        thinking_budget = _get_setting("thinking_budget", "ANYA_THINKING_BUDGET")

    apply_reasoning_settings(model_settings_obj, api_type, thinking_budget)

    # Enable parallel tool calls by default for explicit control
    model_settings_obj.parallel_tool_calls = True

    from ..tools import execute

    config = {
        "name": MAIN_AGENT_NAME,
        "instructions": instructions,
        "model": model_name,
        "model_settings": model_settings_obj,
        "tools": [execute],
    }

    return Agent(**config)


async def DoAgent(
    thinking_budget=None,
    nvim=None,
    settings: "AgentSettings | None" = None,
    cwd: str | None = None,
) -> Agent:
    """Create a lightweight agent optimized for `:Anya do`."""
    instructions = _build_do_instructions(cwd=cwd)

    def _get_setting(attr: str, env_key: str, *fallback_keys, default=None):
        if settings:
            val = getattr(settings, attr, None)
            if val is not None:
                return val
        for k in (env_key, *fallback_keys):
            v = os.environ.get(k)
            if v is not None:
                return v
        return default

    model_name = (
        _get_setting("model", "ANYA_MODEL", default="gpt-4.1") or "gpt-4.1"
    ).strip()
    model_settings_obj = get_default_model_settings(model_name.lower())

    api_type = _get_setting(
        "api_type", "ANYA_API_TYPE", "ANYA_OPENAI_API_TYPE", default="responses"
    )
    if api_type:
        api_type = api_type.strip().lower()
        if api_type not in {"chat_completions", "responses", "anthropic", "copilot"}:
            api_type = "responses"
    else:
        api_type = "responses"

    if hasattr(model_settings_obj, "api_type"):
        model_settings_obj.api_type = api_type

    if thinking_budget is None:
        thinking_budget = _get_setting("thinking_budget", "ANYA_THINKING_BUDGET")

    apply_reasoning_settings(model_settings_obj, api_type, thinking_budget)

    model_settings_obj.parallel_tool_calls = False

    from ..tools import execute

    return Agent(
        name=DO_AGENT_NAME,
        instructions=instructions,
        model=model_name,
        model_settings=model_settings_obj,
        tools=[execute],
    )


__all__ = [
    "CodeAgent",
    "DoAgent",
    "MAIN_AGENT_NAME",
    "DO_AGENT_NAME",
    "MAIN_ASSISTANT_NAME",
]
