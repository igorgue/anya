"""MCP server management for agent.nvim plugin."""

import os
import json


class MCPManager:
    """Manages MCP server configuration and lifecycle."""
    
    def __init__(self, logger):
        """Initialize MCP manager.
        
        Args:
            logger: Logger instance
        """
        self.logger = logger
        self._mcp_hosted_tools = []
    
    def load_servers(self, config_path=None):
        """Load MCP servers from configuration file.
        
        Args:
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
                                "timeout": server_config.get("timeout", 30),
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
                                "timeout": server_config.get("timeout", 45),
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
                    self.logger.error(
                        f"Failed to create MCP server {server_config.get('name', 'unknown')}: {e}"
                    )
                    continue

            if mcp_server_instances:
                self.logger.info(f"Loaded {len(mcp_server_instances)} MCP servers")
            
            if hosted_tools:
                self._mcp_hosted_tools = hosted_tools

            return mcp_server_instances, hosted_tools

        except Exception as e:
            self.logger.error(f"Failed to load MCP config: {e}")
            return [], []
    
    async def connect_servers(self, mcp_servers):
        """Connect to MCP servers.
        
        Args:
            mcp_servers: List of MCP server instances to connect
            
        Returns:
            List of successfully connected servers
        """
        connected_servers = []
        try:
            # Connect all MCP servers
            for server in mcp_servers:
                try:
                    self.logger.info(f"Connecting to MCP server: {server.name}")

                    # Check if server has connect method
                    if hasattr(server, "connect"):
                        await server.connect()
                        self.logger.info(
                            f"Successfully connected to MCP server: {server.name}"
                        )
                        connected_servers.append(server)
                    else:
                        self.logger.warning(
                            f"MCP server {server.name} does not have connect method, skipping connection"
                        )
                        # Still add to connected_servers if it might auto-connect
                        connected_servers.append(server)

                except Exception as server_error:
                    self.logger.error(
                        f"Failed to connect MCP server {server.name}: {server_error}"
                    )
                    # Continue with other servers even if one fails
                    continue

            if connected_servers:
                self.logger.info(
                    f"Successfully connected to {len(connected_servers)} MCP servers"
                )
            else:
                self.logger.warning("No MCP servers could be connected")

            return connected_servers

        except Exception as e:
            self.logger.error(f"Failed to initialize MCP servers: {e}")
            return []
    
    async def disconnect_servers(self, mcp_servers):
        """Disconnect from MCP servers.
        
        Args:
            mcp_servers: List of MCP server instances to disconnect
        """
        try:
            for server in mcp_servers:
                try:
                    if hasattr(server, "disconnect"):
                        self.logger.info(
                            f"Disconnecting MCP server: {server.name}"
                        )
                        await server.disconnect()
                        self.logger.info(
                            f"Successfully disconnected MCP server: {server.name}"
                        )
                    else:
                        self.logger.debug(
                            f"MCP server {server.name} does not have disconnect method"
                        )
                except Exception as server_error:
                    self.logger.error(
                        f"Failed to disconnect MCP server {server.name}: {server_error}"
                    )
                    # Continue with other cleanup even if one fails
                    continue
        except Exception as e:
            self.logger.error(f"Failed to cleanup MCP servers: {e}")
    
    def get_hosted_tools(self):
        """Get list of hosted MCP tools.
        
        Returns:
            List of hosted MCP tool instances
        """
        return self._mcp_hosted_tools
