"""Load MCP server configurations from file and manage server lifecycle."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, TypedDict


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
                command = server_config.get("command")
                args = server_config.get("args", [])
                env = server_config.get("env", {})
                env = _expand_env_vars(env)

                params = {"command": command, "args": args}
                if env:
                    params["env"] = env

                server = MCPServerStdio(
                    name=name,
                    params=params,
                    client_session_timeout_seconds=server_config.get("timeout", 30),
                    cache_tools_list=server_config.get("cache_tools_list", True),
                )
                servers.append(server)

            elif server_type == "streamable_http":
                url = server_config.get("url")
                url = _expand_env_vars(url)

                params = {"url": url}

                headers = server_config.get("headers", {})
                if headers:
                    params["headers"] = headers

                server = MCPServerStreamableHttp(
                    name=name,
                    params=params,
                    client_session_timeout_seconds=server_config.get("timeout", 30),
                    cache_tools_list=server_config.get("cache_tools_list", True),
                )
                servers.append(server)

            else:
                print(f"Warning: Unknown MCP server type '{server_type}' for '{name}'")

        except Exception as e:
            print(f"Warning: Failed to initialize MCP server '{name}': {e}")

    return servers


class MCPManager:
    """Manages MCP server connections with caching for performance."""

    def __init__(self, nvim=None):
        self._nvim = nvim
        self._active_servers: list = []
        self._servers_loaded = False
        self._configs: list[MCPServerConfig] = []

    def _log(self, msg: str, is_error: bool = False):
        """Log a message to Neovim if available."""
        if self._nvim:
            if is_error:
                self._nvim.async_call(self._nvim.err_write, f"Anya MCP: {msg}\n")
            else:
                pass

    def load_configs(self) -> list[MCPServerConfig]:
        """Load MCP server configurations from file."""
        if not self._configs:
            self._configs = load_mcp_server_configs()
        return self._configs

    async def get_connected_servers(self) -> list:
        """Get connected MCP servers, connecting if needed.

        Returns cached servers if already connected, otherwise connects
        all servers in parallel and caches them.
        """
        if self._servers_loaded and self._active_servers:
            return self._active_servers

        configs = self.load_configs()
        if not configs:
            return []

        servers = create_mcp_servers(configs)
        if not servers:
            return []

        connected = await self._connect_servers_parallel(servers)
        if connected:
            self._active_servers = connected
            self._servers_loaded = True

        return self._active_servers

    async def _connect_servers_parallel(self, servers: list) -> list:
        """Connect to all servers in parallel for faster startup."""

        async def connect_single(server) -> tuple:
            """Connect a single server, returning (server, success)."""
            name = getattr(server, "name", "unknown")
            try:
                if hasattr(server, "connect"):
                    # Use server's configured timeout, fallback to 30s
                    timeout = getattr(server, "client_session_timeout_seconds", 30)
                    await asyncio.wait_for(server.connect(), timeout=timeout)
                return (server, True, None)
            except asyncio.TimeoutError:
                return (server, False, f"{name}: connection timeout")
            except Exception as e:
                return (server, False, f"{name}: {e}")

        results = await asyncio.gather(
            *[connect_single(s) for s in servers],
            return_exceptions=True,
        )

        connected = []
        for result in results:
            if isinstance(result, Exception):
                self._log(f"Connection exception: {result}", is_error=True)
                continue
            server, success, error = result
            if success:
                connected.append(server)
            elif error:
                self._log(error, is_error=True)

        return connected

    def reset(self):
        """Reset the server cache to force re-connection on next request."""
        self._active_servers = []
        self._servers_loaded = False

    def is_loaded(self) -> bool:
        """Check if servers are loaded and connected."""
        return self._servers_loaded and len(self._active_servers) > 0

    async def get_server_tools(self) -> List[Dict[str, Any]]:
        """Get tool information from all connected servers.

        Returns:
            List of dictionaries containing server name and tools
        """
        if not self._active_servers:
            return []

        server_tools = []
        for server in self._active_servers:
            try:
                name = getattr(server, "name", "unknown")
                if hasattr(server, "list_tools"):
                    # list_tools might be async
                    tools = server.list_tools
                    if callable(tools):
                        # Check if it's async
                        if asyncio.iscoroutinefunction(tools):
                            tools = await tools()
                        else:
                            tools = tools()
                    else:
                        # It might be a coroutine object already
                        if asyncio.iscoroutine(tools):
                            tools = await tools

                    if tools:
                        server_tools.append({"name": name, "tools": tools})
            except Exception as e:
                self._log(f"Failed to get tools from server: {e}", is_error=True)
                continue

        return server_tools
