"""Dynamic instruction generation for agents based on available MCP servers."""

from typing import Any, List
import asyncio


def _extract_tool_info(tool: Any) -> tuple[str, str, dict]:
    """Extract tool information from various tool object types.

    Args:
        tool: Tool object (could be dict, dataclass, or other types)

    Returns:
        Tuple of (tool_name, tool_description, input_schema)
    """
    tool_name = "unknown"
    tool_desc = "No description"
    input_schema = {}

    # Handle dictionary-like tools
    if hasattr(tool, "get"):
        tool_name = tool.get("name", "unknown")
        tool_desc = tool.get("description", "No description")
        input_schema = tool.get("inputSchema", {})
    else:
        # Handle object attributes (dataclasses, etc.)
        if hasattr(tool, "name"):
            tool_name = getattr(tool, "name")
        if hasattr(tool, "description"):
            tool_desc = getattr(tool, "description")

        # Look for schema in various possible attributes
        for schema_attr in ["inputSchema", "params_json_schema", "params", "schema"]:
            if hasattr(tool, schema_attr):
                schema = getattr(tool, schema_attr)
                # If it's a dict, use it directly
                if isinstance(schema, dict):
                    input_schema = schema
                # If it has a dict() method, call that
                elif hasattr(schema, "dict") and callable(schema.dict):
                    input_schema = schema.dict()
                # If it's a string, try to parse it
                elif isinstance(schema, str):
                    try:
                        import json

                        input_schema = json.loads(schema)
                    except (json.JSONDecodeError, TypeError):
                        pass
                break

    return tool_name, tool_desc, input_schema


async def generate_dynamic_code_instructions(mcp_servers: List[Any]) -> str:
    """Generate dynamic instructions for the code agent based on available MCP servers.

    Args:
        mcp_servers: List of connected MCP server instances

    Returns:
        String containing additional instructions to append to the base prompt
    """
    if not mcp_servers:
        return ""

    # Collect tool information from all servers
    server_tools = []
    for server in mcp_servers:
        try:
            name = getattr(server, "name", "unknown")
            # Get tools from the server if available
            if hasattr(server, "list_tools"):
                # list_tools might be a method or property
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
            # Skip servers that don't expose tool information
            print(
                f"Warning: Failed to get tools from server {getattr(server, 'name', 'unknown')}: {e}"
            )
            continue

    if not server_tools:
        return ""

    # Generate dynamic instructions
    instructions = "\n## Available MCP Services\n\n"
    instructions += "You have access to the following external services via MCP (Model Context Protocol) servers:\n\n"

    for server_info in server_tools:
        server_name = server_info["name"]
        tools = server_info["tools"]

        instructions += f"### {server_name}\n"
        instructions += f"Available tools from {server_name}:\n"

        for tool in tools:
            tool_name, tool_desc, input_schema = _extract_tool_info(tool)

            # List input schema if available
            if input_schema and "properties" in input_schema:
                params = []
                for param_name, param_info in input_schema["properties"].items():
                    param_type = param_info.get("type", "any")
                    param_desc = param_info.get("description", "")
                    required = param_name in input_schema.get("required", [])

                    param_str = f"`{param_name}`"
                    if param_type != "any":
                        param_str += f" ({param_type})"
                    if required:
                        param_str += " (required)"
                    if param_desc:
                        param_str += f": {param_desc}"
                    params.append(param_str)

                if params:
                    instructions += f"- **{tool_name}**: {tool_desc}\n  Parameters: {', '.join(params)}\n"
                else:
                    instructions += f"- **{tool_name}**: {tool_desc}\n"
            else:
                instructions += f"- **{tool_name}**: {tool_desc}\n"

        instructions += "\n"

    instructions += (
        "To use these services, call the `mcp` tool with the appropriate parameters. "
    )
    instructions += "The MCP agent will handle the interaction with the external service and return results.\n"

    return instructions


async def generate_dynamic_mcp_instructions(mcp_servers: List[Any]) -> str:
    """Generate dynamic instructions for the MCP agent based on available servers.

    Args:
        mcp_servers: List of connected MCP server instances

    Returns:
        String containing additional instructions to append to the base prompt
    """
    if not mcp_servers:
        return ""

    # Collect server information
    server_info = []
    for server in mcp_servers:
        try:
            name = getattr(server, "name", "unknown")
            server_info.append({"name": name})
        except Exception:
            continue

    if not server_info:
        return ""

    # Generate dynamic instructions
    instructions = "\n## Available Servers\n\n"
    instructions += "You are connected to the following MCP servers:\n\n"

    for info in server_info:
        instructions += f"- **{info['name']}**: Use tools from this server as needed\n"

    instructions += "\nRemember to always start your response with the tool information as specified in the format above.\n"

    return instructions


def update_agent_instructions(base_instructions: str, dynamic_instructions: str) -> str:
    """Update agent instructions by appending dynamic content.

    Args:
        base_instructions: The original static instructions
        dynamic_instructions: Additional instructions to append

    Returns:
        Combined instructions
    """
    if not dynamic_instructions:
        return base_instructions

    # Ensure there's a newline between base and dynamic instructions
    if base_instructions and not base_instructions.endswith("\n"):
        base_instructions += "\n"

    return base_instructions + dynamic_instructions


# Synchronous wrappers for compatibility
def generate_dynamic_code_instructions_sync(mcp_servers: List[Any]) -> str:
    """Synchronous version of generate_dynamic_code_instructions."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're already in an event loop, we can't use run()
            # This shouldn't happen in normal operation, but let's handle it gracefully
            print(
                "Warning: generate_dynamic_code_instructions_sync called in running event loop"
            )
            return ""
        else:
            return loop.run_until_complete(
                generate_dynamic_code_instructions(mcp_servers)
            )
    except Exception:
        # Fallback if async fails
        return ""


def generate_dynamic_mcp_instructions_sync(mcp_servers: List[Any]) -> str:
    """Synchronous version of generate_dynamic_mcp_instructions."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're already in an event loop, we can't use run()
            print(
                "Warning: generate_dynamic_mcp_instructions_sync called in running event loop"
            )
            return ""
        else:
            return loop.run_until_complete(
                generate_dynamic_mcp_instructions(mcp_servers)
            )
    except Exception:
        # Fallback if async fails
        return ""
