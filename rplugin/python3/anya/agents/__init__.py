from agents import Agent

from .context import NvimPluginContext
from .utils import get_instructions
from ..mcp_loader import load_mcp_server_configs
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

# Load MCP server configurations (not connected yet)
_mcp_server_configs = load_mcp_server_configs()

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


def create_code_agent(mcp_servers=None):
    """Create a code agent with optional MCP servers."""
    config = _BASE_AGENT_CONFIG.copy()
    if mcp_servers:
        config["mcp_servers"] = mcp_servers
    return Agent[NvimPluginContext](**config)


__all__ = ["code", "NvimPluginContext", "_mcp_server_configs", "create_code_agent"]
