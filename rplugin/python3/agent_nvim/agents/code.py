"""CodeAgent implementation for the main coding assistant."""

import logging

try:
    from typing import List, Optional
except ImportError:
    List = list
    Optional = type


class CodeAgent:
    """
    Main coding agent for agent.nvim.

    This agent handles general coding tasks including:
    - Reading and writing files
    - Debugging and fixing errors
    - Refactoring and optimizing code
    - Writing new features
    - Explaining code logic
    - Searching the repository
    """

    def __init__(
        self,
        model: str,
        logger: logging.Logger,
        tools: Optional[List] = None,
        mcp_servers: Optional[List] = None,
        project_instructions: Optional[str] = None,
        yolo_mode: bool = False,
    ):
        self.model = model
        self.logger = logger
        self.tools = tools or []
        self.mcp_servers = mcp_servers or []
        self.project_instructions = project_instructions or ""
        self.yolo_mode = yolo_mode
        self.agent = None

    def create_agent(self):
        """Create the OpenAI Agent with configured tools and instructions."""
        try:
            from agents import Agent
            from agents.agent import StopAtTools
        except ImportError as e:
            self.logger.error(f"Could not import Agent from agents package: {e}")
            return None

        instructions = self._build_instructions()

        agent_kwargs = {
            "name": "Code Agent",
            "instructions": instructions,
            "tools": self.tools,
        }

        # Only use StopAtTools if not in YOLO mode
        if not self.yolo_mode:
            agent_kwargs["tool_use_behavior"] = StopAtTools(
                stop_at_tool_names=["patch", "edit"]
            )

        # Add MCP servers if available (but tools take priority)
        if self.mcp_servers:
            agent_kwargs["mcp_servers"] = self.mcp_servers

        if self.model:
            agent_kwargs["model"] = self.model

        self.agent = Agent(**agent_kwargs)

        # Verify agent has both tool lists after creation
        agent_tools = (
            len(self.agent.tools)
            if hasattr(self.agent, "tools") and self.agent.tools
            else 0
        )
        agent_mcp = (
            len(self.agent.mcp_servers)
            if hasattr(self.agent, "mcp_servers") and self.agent.mcp_servers
            else 0
        )
        self.logger.info(
            f"Agent '{self.agent.name}' created with {agent_tools} tools and {agent_mcp} MCP servers"
        )

        # Log tools for debugging handoff issues
        self.logger.info(f"CodeAgent created with {len(self.tools)} tools")
        for tool in self.tools:
            tool_name = getattr(tool, "name", str(tool))
            self.logger.info(f"  - {tool_name}")

        return self.agent

    def _build_instructions(self) -> str:
        """Build the full instructions for the agent."""
        from ..utils import load_agent_prompt

        base_instructions = load_agent_prompt("code")
        if not base_instructions:
            base_instructions = """You are a helpful AI assistant embedded in Neovim. You can read files, list files, search the repository, propose patches, and execute Lua code directly inside Neovim."""

        full_instructions = base_instructions

        # Add project-specific instructions
        if self.project_instructions:
            full_instructions += (
                "\n\nProject Instructions:\n" + self.project_instructions
            )

        # Add MCP servers info
        if self.mcp_servers:
            full_instructions += (
                f"\n\nAdditional MCP tools are available for enhanced capabilities "
                f"(loaded {len(self.mcp_servers)} MCP servers: {[s.name for s in self.mcp_servers]})."
            )

        return full_instructions

    def get_display_model(self) -> str:
        """Get the model name for display purposes."""
        return self.model if self.model else "gpt-4o"
