"""AutoAgent implementation for routing/handoff to specialized agents."""

import logging

try:
    from typing import List, Optional
except ImportError:
    List = list
    Optional = type


class AutoAgent:
    """
    Handoff agent that routes conversations to specialized agents.

    This agent analyzes user requests and determines which specialized
    agent is best suited to handle each task. It does not answer
    questions itself - it only routes to other agents.

    Handoffs to:
    - CodeAgent: Programming questions and coding tasks
    - PlanAgent: Complex multi-step tasks requiring planning
    - ChatAgent: General conversation and knowledge questions
    - ReviewAgent: Code review requests
    - VerifyAgent: Verification and validation tasks
    """

    def __init__(
        self,
        model: str,
        logger: logging.Logger,
        mcp_servers: Optional[List] = None,
        project_instructions: Optional[str] = None,
    ):
        self.model = model
        self.logger = logger
        self.mcp_servers = mcp_servers or []
        self.project_instructions = project_instructions or ""
        self.agent = None
        self._handoff_agents = {}

    def register_handoff_agent(self, name: str, agent):
        """Register an agent that can be handed off to."""
        self._handoff_agents[name] = agent

    def create_agent(self):
        """Create the AutoAgent with handoffs to registered agents."""
        try:
            from agents import Agent, handoff
            from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX
        except ImportError as e:
            self.logger.error(f"Could not import from agents package: {e}")
            return None

        instructions = self._build_instructions(RECOMMENDED_PROMPT_PREFIX)

        # Build handoffs list from registered agents
        handoffs = []
        for name, agent in self._handoff_agents.items():
            if agent is not None:
                handoffs.append(
                    handoff(
                        agent=agent,
                        tool_name_override=f"transfer_to_{name}",
                        tool_description_override=f"Hand off the conversation to the {name} agent.",
                    )
                )
                self.logger.info(f"Registered handoff to {name} agent")

        agent_kwargs = {
            "name": "Auto Agent",
            "instructions": instructions,
            "handoffs": handoffs,
            "tools": [],  # Auto agent has no tools, only handoffs
        }

        if self.mcp_servers:
            agent_kwargs["mcp_servers"] = self.mcp_servers

        if self.model:
            agent_kwargs["model"] = self.model

        self.agent = Agent(**agent_kwargs)
        return self.agent

    def _build_instructions(self, handoff_prefix: str = "") -> str:
        """Build the full instructions for the agent."""
        from ..utils import load_agent_prompt

        base_instructions = load_agent_prompt("auto")
        if not base_instructions:
            base_instructions = """You are a routing agent that delegates tasks to specialized agents. 
Analyze the user's request and hand off to the appropriate agent. Do not answer questions yourself."""

        full_instructions = ""
        if handoff_prefix:
            full_instructions = handoff_prefix + "\n\n"
        full_instructions += base_instructions

        # Add available agents info
        if self._handoff_agents:
            full_instructions += "\n\n## Available Agents\n\n"
            for name in self._handoff_agents.keys():
                full_instructions += f"- `{name}`: Use transfer_to_{name} to hand off\n"

        # Add project-specific instructions
        if self.project_instructions:
            full_instructions += "\n\nProject Instructions:\n" + self.project_instructions

        return full_instructions

    def get_display_model(self) -> str:
        """Get the model name for display purposes."""
        return self.model if self.model else "gpt-4o"
