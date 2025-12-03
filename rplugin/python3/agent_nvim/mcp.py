"""MCP server management for agent.nvim plugin."""

import asyncio
import os
import re
import json

# Global MCP tool call timeout in seconds (configurable)
MCP_TOOL_TIMEOUT = 60


class MCPManager:
    """Manages MCP server configuration and lifecycle."""

    def __init__(self, logger):
        """Initialize MCP manager.

        Args:
            logger: Logger instance
        """
        self.logger = logger
        self._mcp_hosted_tools = []
        self._active_servers = []
        self._servers_loaded = False

    def _expand_env_vars(self, value):
        """Expand environment variables in a string value.

        Supports $VAR and ${VAR} syntax.

        Args:
            value: The value to expand (can be string, dict, list, or other)

        Returns:
            The value with environment variables expanded
        """
        if isinstance(value, str):
            # Match $VAR or ${VAR} patterns
            def replace_var(match):
                var_name = match.group(1) or match.group(2)
                return os.environ.get(var_name, match.group(0))

            # Pattern matches ${VAR} or $VAR (word characters only)
            pattern = r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
            return re.sub(pattern, replace_var, value)
        elif isinstance(value, dict):
            return {k: self._expand_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._expand_env_vars(item) for item in value]
        else:
            # For non-string/dict/list types (int, bool, None, etc.), return as-is
            return value

    def load_servers(self, config_path=None):
        """Load MCP servers from configuration file.

            config_path: Path to MCP servers.json config file
                        (defaults to ~/.config/agent.nvim/mcp/servers.json)

        Returns:
            Tuple of (mcp_server_instances, hosted_tools)
        """
        try:
            # Try to import MCP classes - handle gracefully if not available
            from agents.mcp import MCPServerStdio, MCPServerStreamableHttp, MCPServerSse
            from agents import HostedMCPTool

            mcp_available = True
        except ImportError:
            self.logger.info("MCP classes not available in agents SDK")
            return [], []

        # Return cached servers if already loaded to avoid CPU spike from re-instantiation
        if self._servers_loaded and self._active_servers:
            self.logger.debug(
                f"Returning {len(self._active_servers)} cached MCP servers"
            )
            return self._active_servers, self._mcp_hosted_tools

        # Path to MCP servers configuration
        if config_path is None:
            config_path = os.path.expanduser("~/.config/agent.nvim/mcp/servers.json")

        if not os.path.exists(config_path):
            self.logger.info(f"MCP config not found at {config_path}")
            return [], []

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            servers = config.get("servers", [])
            mcp_server_instances = []
            hosted_tools = []

            for server_config in servers:
                # Expand environment variables in all config values
                server_config = self._expand_env_vars(server_config)
                try:
                    server_type = server_config.get("type")
                    server_name = server_config.get("name", f"server_{len(servers)}")

                    if server_type == "stdio":
                        # stdio-based MCP server
                        if "command" not in server_config:
                            self.logger.error(
                                f"MCP stdio server {server_name} missing 'command' field"
                            )
                            continue

                        server = MCPServerStdio(
                            name=server_name,
                            params={
                                "command": server_config["command"],
                                "args": server_config.get("args", []),
                            },
                            cache_tools_list=server_config.get(
                                "cache_tools_list", True
                            ),
                        )
                        mcp_server_instances.append(server)

                    elif server_type == "streamable_http":
                        # HTTP-based MCP server
                        if "url" not in server_config:
                            self.logger.error(
                                f"MCP HTTP server {server_name} missing 'url' field"
                            )
                            continue

                        server = MCPServerStreamableHttp(
                            name=server_name,
                            params={
                                "url": server_config["url"],
                                "headers": server_config.get("headers", {}),
                                "timeout": server_config.get(
                                    "timeout", MCP_TOOL_TIMEOUT
                                ),
                            },
                            cache_tools_list=server_config.get(
                                "cache_tools_list", True
                            ),
                        )
                        mcp_server_instances.append(server)

                    elif server_type == "sse":
                        # Server-Sent Events MCP server
                        if "url" not in server_config:
                            self.logger.error(
                                f"MCP SSE server {server_name} missing 'url' field"
                            )
                            continue

                        server = MCPServerSse(
                            name=server_name,
                            params={
                                "url": server_config["url"],
                                "headers": server_config.get("headers", {}),
                                "timeout": server_config.get(
                                    "timeout", MCP_TOOL_TIMEOUT
                                ),
                            },
                            cache_tools_list=server_config.get(
                                "cache_tools_list", True
                            ),
                        )
                        mcp_server_instances.append(server)

                    elif server_type == "hosted":
                        # Hosted MCP tool (managed by OpenAI)
                        if "server_label" not in server_config:
                            self.logger.error(
                                f"MCP hosted server {server_name} missing 'server_label' field"
                            )
                            continue

                        tool_config = {
                            "type": "mcp",
                            "server_label": server_config["server_label"],
                            "require_approval": server_config.get(
                                "require_approval", "never"
                            ),
                        }

                        if "server_url" in server_config:
                            tool_config["server_url"] = server_config["server_url"]

                        if "connector_id" in server_config:
                            tool_config["connector_id"] = server_config["connector_id"]
                            if "authorization" in server_config:
                                tool_config["authorization"] = server_config[
                                    "authorization"
                                ]

                        hosted_tool = HostedMCPTool(tool_config=tool_config)
                        hosted_tools.append(hosted_tool)

                    else:
                        self.logger.error(f"Unknown MCP server type: {server_type}")

                except Exception as e:
                    import traceback

                    self.logger.error(
                        f"Failed to create MCP server {server_config.get('name', 'unknown')}: {e}"
                    )
                    self.logger.error(f"Traceback: {traceback.format_exc()}")
                    continue

            if mcp_server_instances:
                self.logger.info(f"Loaded {len(mcp_server_instances)} MCP servers")

            if hosted_tools:
                self._mcp_hosted_tools = hosted_tools

            # Mark as loaded (will be set to True after connect_servers succeeds)
            return mcp_server_instances, hosted_tools

        except Exception as e:
            self.logger.error(f"Failed to load MCP config: {e}")
            return [], []

    async def connect_servers(self, mcp_servers):
        """Connect to MCP servers in parallel for faster startup.

        Args:
            mcp_servers: List of MCP server instances to connect

        Returns:
            List of successfully connected servers
        """
        import traceback

        # If servers are already loaded and active, return them directly
        if self._servers_loaded and self._active_servers:
            self.logger.debug(
                f"Returning {len(self._active_servers)} already connected MCP servers"
            )
            return self._active_servers

        async def connect_single_server(server):
            """Connect a single server, returning (server, success) tuple."""
            try:
                # Check if already connected (has session)
                if hasattr(server, "session") and server.session:
                    self.logger.debug(f"MCP server {server.name} already connected")
                    return (server, True)

                self.logger.info(f"Connecting to MCP server: {server.name}")

                # Check if server has connect method
                if hasattr(server, "connect"):
                    await server.connect()
                    self.logger.info(
                        f"Successfully connected to MCP server: {server.name}"
                    )
                    return (server, True)
                else:
                    self.logger.warning(
                        f"MCP server {server.name} does not have connect method, skipping connection"
                    )
                    # Still return server if it might auto-connect
                    return (server, True)

            except Exception as server_error:
                self.logger.error(
                    f"Failed to connect MCP server {server.name}: {server_error}"
                )
                self.logger.error(
                    f"Traceback for {server.name}: {traceback.format_exc()}"
                )
                return (server, False)

        connected_servers = []
        try:
            # Connect all MCP servers in parallel using asyncio.gather
            if mcp_servers:
                self.logger.info(
                    f"Connecting to {len(mcp_servers)} MCP servers in parallel..."
                )
                results = await asyncio.gather(
                    *[connect_single_server(server) for server in mcp_servers],
                    return_exceptions=True,
                )

                for result in results:
                    if isinstance(result, Exception):
                        self.logger.error(f"MCP server connection exception: {result}")
                        continue
                    server, success = result
                    if success:
                        connected_servers.append(server)

            if connected_servers:
                self.logger.info(
                    f"Successfully connected to {len(connected_servers)} MCP servers"
                )
                # Update active servers list and mark as loaded for caching
                self._active_servers = connected_servers
                self._servers_loaded = True
            else:
                self.logger.warning("No MCP servers could be connected")

            return connected_servers

        except Exception as e:
            self.logger.error(f"Failed to initialize MCP servers: {e}")
            return []

    async def shutdown(self):
        """Shutdown all active MCP servers."""
        # Clear active servers list and reset loaded flag
        self._active_servers = []
        self._servers_loaded = False

    def reset_cache(self):
        """Reset the server cache to force re-connection on next request."""
        self._active_servers = []
        self._servers_loaded = False
        self.logger.debug("MCP server cache reset")

    def get_hosted_tools(self):
        """Get list of hosted MCP tools.

        Returns:
            List of hosted MCP tool instances
        """
        return self._mcp_hosted_tools
