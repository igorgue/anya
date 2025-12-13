import os
from agents import Agent

from .dynamic_instructions import (
    generate_dynamic_code_instructions,
    generate_dynamic_mcp_instructions,
    update_agent_instructions,
)

from .utils import get_instructions
from ..system_prompt import apply_system_prompt
from ..tools import (
    create,
    edit,
    exec,
    exec_lua,
    gh,
    list_files,
    read_file,
    read_many_files,
    search,
    parrot,
    buffer_name,
)

MAIN_AGENT_NAME = "Code"
MAIN_ASSISTANT_NAME = "Anya"


async def CodeAgent(mcp_servers=None, thinking_budget=None, nvim=None) -> Agent:
    """Create a code agent with dynamically generated instructions based on MCP servers.

    This async version handles the generation of dynamic instructions that may
    require async calls to MCP servers.

    Args:
        mcp_servers: List of connected MCP server instances
        thinking_budget: Optional thinking budget for reasoning models (e.g., o3)
                        If not provided, reads from ANYA_THINKING_BUDGET env var

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

    # Get thinking budget (model is handled via OPENAI_MODEL_ID env var)
    if thinking_budget is None:
        thinking_budget = os.environ.get("ANYA_THINKING_BUDGET")

    config = {
        "name": MAIN_AGENT_NAME,
        "instructions": instructions,
        "tools": [
            create,
            edit,
            exec,
            exec_lua,
            gh,
            list_files,
            read_file,
            read_many_files,
            search,
            parrot,
            buffer_name,
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
