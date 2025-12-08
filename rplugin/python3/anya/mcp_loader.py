"""Load MCP server configurations from file.

Note: MCP server support in Anya requires proper async context management.
Currently disabled pending resolution of initialization issues with the OpenAI Agents SDK.
See: https://github.com/igorgue/anya/issues/XXX
"""

import json
import os
from pathlib import Path
from typing import Any, TypedDict


class MCPServerConfig(TypedDict, total=False):
    """Configuration for an MCP server."""

    name: str
    type: str
    command: str
    args: list[str]
    env: dict[str, str]
    url: str
    headers: dict[str, str]
    timeout: int
    cache_tools_list: bool


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand environment variables in config values."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def load_mcp_server_configs() -> list[MCPServerConfig]:
    """Load MCP server configurations from ~/.config/anya/mcp/servers.json."""
    config_path = Path.home() / ".config" / "anya" / "mcp" / "servers.json"

    if not config_path.exists():
        return []

    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load MCP config from {config_path}: {e}")
        return []

    return config.get("servers", [])


def create_mcp_servers(configs: list[MCPServerConfig]) -> list:
    """Create MCP server instances from configurations.

    This function should be called in an async context before running the agent.
    """
    from agents.mcp import MCPServerStdio, MCPServerStreamableHttp

    servers = []

    for server_config in configs:
        server_type = server_config.get("type")
        name = server_config.get("name", "unknown")

        try:
            if server_type == "stdio":
                # Expand env vars in params
                command = server_config.get("command")
                args = server_config.get("args", [])
                env = server_config.get("env", {})

                # Expand environment variables
                env = _expand_env_vars(env)

                params = {"command": command, "args": args}
                if env:
                    params["env"] = env

                server = MCPServerStdio(
                    name=name,
                    params=params,
                    client_session_timeout_seconds=server_config.get("timeout", 30),
                    cache_tools_list=server_config.get("cache_tools_list", False),
                )
                servers.append(server)

            elif server_type == "streamable_http":
                url = server_config.get("url")
                # Expand environment variables in URL
                url = _expand_env_vars(url)

                params = {"url": url}

                # Add headers if present
                headers = server_config.get("headers", {})
                if headers:
                    params["headers"] = headers

                server = MCPServerStreamableHttp(
                    name=name,
                    params=params,
                    client_session_timeout_seconds=server_config.get("timeout", 30),
                    cache_tools_list=server_config.get("cache_tools_list", False),
                )
                servers.append(server)

            else:
                print(f"Warning: Unknown MCP server type '{server_type}' for '{name}'")

        except Exception as e:
            print(f"Warning: Failed to initialize MCP server '{name}': {e}")

    return servers
