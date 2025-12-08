from agents import Agent

from .context import NvimPluginContext
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

# Base agent configuration (will be reused to create agents with/without MCP servers)
_BASE_AGENT_CONFIG = {
    "name": "Code",
    "instructions": get_instructions("code.md"),
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

# Create default agent without MCP servers for backward compatibility
code = Agent[NvimPluginContext](**_BASE_AGENT_CONFIG)


def create_mcp_agent(
    mcp_servers: list[dict] | None = None,
) -> Agent[NvimPluginContext] | None:
    """Create an MCP agent with MCP server tools.

    This agent is designed to be called as a tool by the Code Agent.
    It focuses on using MCP server tools to query external systems.
    """
    if not mcp_servers:
        return None

    return Agent[NvimPluginContext](
        name="MCP Tools",
        instructions=get_instructions("mcp.md"),
        mcp_servers=mcp_servers,
    )


def create_code_agent_with_mcp_tool(
    mcp_servers: list[dict] | None = None,
) -> Agent[NvimPluginContext] | None:
    """Create a code agent with MCP servers as a delegated tool.

    Instead of directly including MCP servers, this creates an MCP Agent
    as a tool that can be called on-demand. This avoids blocking the
    main agent while waiting for MCP servers to connect.
    """
    config = _BASE_AGENT_CONFIG.copy()

    # If MCP servers are available, create an MCP Agent and convert to tool
    if mcp_servers:
        mcp_agent = create_mcp_agent(mcp_servers)
        if mcp_agent:
            mcp_tool = mcp_agent.as_tool(
                tool_name="mcp_tools",
                tool_description="Access external systems and data via MCP (Model Context Protocol) servers. "
                "Use this when you need to query databases, APIs, or other external services.",
            )
            config["tools"] = config["tools"] + [mcp_tool]

    return Agent[NvimPluginContext](**config)


def create_code_agent(mcp_servers=None):
    """Create a code agent with optional MCP servers as a delegated tool.

    When MCP servers are available, they're added as an 'mcp_tools' tool
    that the agent can call on-demand. This avoids blocking on MCP startup.
    """
    return create_code_agent_with_mcp_tool(mcp_servers)


__all__ = [
    "code",
    "NvimPluginContext",
    "create_code_agent",
    "create_mcp_agent",
]