import os

from agents import Agent
from agents.models.default_models import get_default_model_settings
from openai.types.shared import Reasoning

from .dynamic_instructions import (
    generate_dynamic_code_instructions,
    generate_dynamic_mcp_instructions,
    update_agent_instructions,
)

from .utils import get_instructions
from ..system_prompt import apply_system_prompt

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


async def CodeAgent(mcp_servers=None, thinking_budget=None, nvim=None) -> Agent:
    """Create a code agent with dynamically generated instructions based on MCP servers.

    This async version handles the generation of dynamic instructions that may
    require async calls to MCP servers.

    Args:
        mcp_servers: List of connected MCP server instances
        thinking_budget: Optional thinking budget for reasoning models.

            This should be a reasoning "effort" string supported by OpenAI (e.g.
            "minimal", "low", "medium", "high", "xhigh"). If not provided, reads
            from ANYA_THINKING_BUDGET.

    Returns:
        Configured Agent instance with dynamic instructions
    """
    from agents.run import RunConfig

    RunConfig.tracing_disabled = True

    # Get base instructions
    base_instructions = get_instructions("code.md")

    # Generate dynamic instructions based on available MCP servers
    dynamic_instructions = await generate_dynamic_code_instructions(mcp_servers or [])

    # Combine instructions
    instructions = update_agent_instructions(base_instructions, dynamic_instructions)

    # Expand placeholders and append environment context at the end.
    instructions = apply_system_prompt(instructions, nvim=nvim)

    # ------
    # Ergonomic Environment Variable Lookups
    # ------
    def _get_env(key, *fallback_keys, default=None):
        for k in (key, *fallback_keys):
            v = os.environ.get(k)
            if v is not None:
                return v
        return default

    # Model config
    model_name = _get_env("ANYA_MODEL", default="gpt-4.1").strip()
    model_settings = get_default_model_settings(model_name.lower())

    # API type (for completions/chat/responses/etc)
    api_type = _get_env("ANYA_API_TYPE", "ANYA_OPENAI_API_TYPE", default=None)
    if api_type:
        api_type = api_type.strip().lower()
        if api_type not in {"chat_completions", "responses"}:
            api_type = "responses"
    else:
        api_type = "responses"

    # API key and base url lookup (prefer ANYA_*, fallback to OPENAI_*)
    api_key = _get_env("ANYA_API_KEY", "OPENAI_API_KEY", default=None)
    api_base = _get_env("ANYA_API_BASE", "OPENAI_API_BASE", default=None)

    # Add: Set API type from environment (ANYA_OPENAI_API_TYPE)
    api_type = (os.environ.get("ANYA_OPENAI_API_TYPE") or "responses").strip().lower()
    if api_type not in ("responses", "chat_completions"):
        api_type = "responses"  # fallback default
    # If the model_settings supports passing api_type, set it here; we also pass
    # api_type through the Agent config below.
    if hasattr(model_settings, 'api_type'):
        model_settings.api_type = api_type

    # Get thinking budget (if not explicitly passed).
    if thinking_budget is None:
        thinking_budget = os.environ.get("ANYA_THINKING_BUDGET")

    # Configure reasoning if ANYA_THINKING_BUDGET is set
    if thinking_budget is not None:
        effort = _parse_reasoning_effort(thinking_budget) or "medium"

        # If model already has reasoning (like gpt-5), update the effort
        if model_settings.reasoning is not None:
            model_settings.reasoning.effort = effort
            model_settings.reasoning.summary = "auto"
        else:
            # For models without native reasoning, set it anyway
            # This may or may not produce reasoning events depending on the model
            model_settings.reasoning = Reasoning(
                effort=effort,
                summary="auto",
            )

    # Import tools here to avoid circular import
    from ..tools import (
        create_file,
        edit,
        exec,
        exec_lua,
        gh,
        list_files,
        read_file,
        read_many_files,
        write_file,
        search_code,
        parrot,
        buffer_name,
        store_memory,
        extract_memories,
        recall_memories,
    )

    config = {
        "name": MAIN_AGENT_NAME,
        "instructions": instructions,
        "model": model_name,
        "model_settings": model_settings,
        "tools": [
            create_file,
            edit,
            exec,
            exec_lua,
            gh,
            list_files,
            read_file,
            read_many_files,
            write_file,
            search_code,
            parrot,
            buffer_name,
            store_memory,
            extract_memories,
            recall_memories,
        ],
    }

    if mcp_servers:
        mcp_agent = await MCPAgent(mcp_servers)
        if mcp_agent:
            mcp_tool = mcp_agent.as_tool(
                tool_name="mcp",
                tool_description="Access external systems and data via MCP (Model Context Protocol) servers. "
                "Use this when you need to query databases, APIs, or other external services."
                "you can also use this as a last resort if you don't know how to ansewer a question.",
            )
            config["tools"] = config["tools"] + [mcp_tool]

    return Agent(**config)


async def MCPAgent(
    mcp_servers: list[object] | None = None,
    nvim=None,
) -> Agent | None:
    """Create an MCP agent with dynamically generated instructions.

    This async version handles the generation of dynamic instructions that may
    require async calls to MCP servers.

    Args:
        mcp_servers: List of connected MCP server instances

    Returns:
        Configured Agent instance with dynamic instructions, or None if no servers
    """
    if not mcp_servers:
        return None

    # Get base instructions
    base_instructions = get_instructions("mcp.md")

    # Generate dynamic instructions based on available servers
    dynamic_instructions = await generate_dynamic_mcp_instructions(mcp_servers)

    # Combine instructions
    instructions = update_agent_instructions(base_instructions, dynamic_instructions)

    # Expand placeholders and append environment context at the end.
    instructions = apply_system_prompt(instructions, nvim=nvim)

    # We don't need custom tools anymore since the logging will be done
    # in the agent's responses directly
    return Agent(
        name="MCP Tools",
        instructions=instructions,
        mcp_servers=mcp_servers,
    )


__all__ = [
    "NvimPluginContext",
    "CodeAgent",
    "MAIN_AGENT_NAME",
    "MAIN_ASSISTANT_NAME",
]
