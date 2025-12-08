from agents import Agent, function_tool

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


def MCPAgent(
    mcp_servers: list[dict] | None = None,
) -> Agent[NvimPluginContext] | None:
    """Create an MCP agent with MCP server tools.

    This agent is designed to be called as a tool by the Code Agent.
    It focuses on using MCP server tools to query external systems.
    """
    if not mcp_servers:
        return None

    # We don't need custom tools anymore since the logging will be done
    # in the agent's responses directly
    return Agent[NvimPluginContext](
        name="MCP Tools",
        instructions=get_instructions("mcp.md"),
        mcp_servers=mcp_servers,
    )


def CodeAgent(mcp_servers=None):
    """Create a code agent with optional MCP servers as a delegated tool.

    When MCP servers are available, they're added as an 'mcp_tools' tool
    that the agent can call on-demand. This avoids blocking on MCP startup.
    """
    config = {
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

    if mcp_servers:
        mcp_agent = MCPAgent(mcp_servers)
        if mcp_agent:
            mcp_tool = mcp_agent.as_tool(
                tool_name="mcp",
                tool_description="Access external systems and data via MCP (Model Context Protocol) servers. "
                "Use this when you need to query databases, APIs, or other external services."
                "you can also use this as a last resort if you don't know how to ansewer a question.",
            )
            config["tools"] = config["tools"] + [mcp_tool]

    return Agent[NvimPluginContext](**config)


__all__ = [
    "NvimPluginContext",
    "CodeAgent",
]
