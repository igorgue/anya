"""Async agent creation functions to handle dynamic instruction generation."""

from typing import Any, List

from agents import Agent

from .context import NvimPluginContext
from .dynamic_instructions import (
    generate_dynamic_code_instructions,
    generate_dynamic_mcp_instructions,
    update_agent_instructions,
)


async def CodeAgentAsync(mcp_servers=None):
    """Create a code agent with dynamically generated instructions based on MCP servers.

    This async version handles the generation of dynamic instructions that may
    require async calls to MCP servers.

    Args:
        mcp_servers: List of connected MCP server instances

    Returns:
        Configured Agent instance with dynamic instructions
    """
    # Import here to avoid circular imports
    from .utils import get_instructions
    from ..tools import (
        buffer_name,
        create,
        edit,
        exec,
        exec_lua,
        gh,
        list_files,
        parrot,
        read_file,
        read_many_files,
        search,
    )

    # Get base instructions
    base_instructions = get_instructions("code.md")

    # Generate dynamic instructions based on available MCP servers
    dynamic_instructions = await generate_dynamic_code_instructions(mcp_servers or [])

    # Combine instructions
    instructions = update_agent_instructions(base_instructions, dynamic_instructions)

    config = {
        "name": "Code",
        "instructions": instructions,
        "tools": [
            buffer_name,
            create,
            edit,
            exec,
            exec_lua,
            gh,
            list_files,
            parrot,
            read_file,
            read_many_files,
            search,
        ],
    }

    if mcp_servers:
        mcp_agent = await MCPAgentAsync(mcp_servers)
        if mcp_agent:
            mcp_tool = mcp_agent.as_tool(
                tool_name="mcp",
                tool_description="Access external systems and data via MCP (Model Context Protocol) servers. "
                "Use this when you need to query databases, APIs, or other external services."
                "you can also use this as a last resort if you don't know how to ansewer a question.",
            )
            config["tools"] = config["tools"] + [mcp_tool]

    return Agent(**config)


async def MCPAgentAsync(
    mcp_servers: List[Any] | None = None,
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

    # Import here to avoid circular imports
    from .utils import get_instructions

    # Get base instructions
    base_instructions = get_instructions("mcp.md")

    # Generate dynamic instructions based on available servers
    dynamic_instructions = await generate_dynamic_mcp_instructions(mcp_servers)

    # Combine instructions
    instructions = update_agent_instructions(base_instructions, dynamic_instructions)

    # We don't need custom tools anymore since the logging will be done
    # in the agent's responses directly
    return Agent(
        name="MCP Tools",
        instructions=instructions,
        mcp_servers=mcp_servers,
    )