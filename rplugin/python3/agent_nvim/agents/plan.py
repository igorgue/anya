"""PlanAgent implementation for breaking down complex tasks."""

import logging

try:
    from typing import List, Optional
except ImportError:
    List = list
    Optional = type


class PlanAgent:
    """
    Planning agent specialized in breaking down complex tasks.

    This agent analyzes complex requests, breaks them into
    manageable sub-tasks, and creates actionable plans.
    """

    def __init__(
        self,
        model: str,
        logger: logging.Logger,
        tools: Optional[List] = None,
        mcp_servers: Optional[List] = None,
        project_instructions: Optional[str] = None,
    ):
        self.model = model
        self.logger = logger
        self.tools = tools or []
        self.mcp_servers = mcp_servers or []
        self.project_instructions = project_instructions or ""
        self.agent = None

    def create_agent(self):
        """Create the PlanAgent."""
        try:
            from agents import Agent
        except ImportError as e:
            self.logger.error(f"Could not import from agents package: {e}")
            return None

        instructions = self._build_instructions()

        agent_kwargs = {
            "name": "Plan Agent",
            "instructions": instructions,
            "tools": self.tools,
        }

        if self.mcp_servers:
            agent_kwargs["mcp_servers"] = self.mcp_servers

        if self.model:
            agent_kwargs["model"] = self.model

        self.agent = Agent(**agent_kwargs)
        return self.agent

    def _build_instructions(self) -> str:
        """Build the full instructions for the agent."""
        from ..utils import load_agent_prompt

        base_instructions = load_agent_prompt("plan")
        if not base_instructions:
            base_instructions = """You are a planning agent that breaks down complex tasks into manageable steps.
Analyze requests, create actionable plans, and present them clearly."""

        full_instructions = base_instructions

        # Add project-specific instructions
        if self.project_instructions:
            full_instructions += "\n\nProject Instructions:\n" + self.project_instructions

        return full_instructions

    def get_display_model(self) -> str:
        """Get the model name for display purposes."""
        return self.model if self.model else "gpt-4o"
