"""Agent management for the daemon server.

Manages agent lifecycle:
- Code agent: Cached by (session_id, settings_hash) to allow different clients
              to use different settings
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field

import zmq.asyncio
from agents import Agent

from ..agents import CodeAgent, MAIN_AGENT_NAME
from ..protocol import AgentSettings


@dataclass
class SessionState:
    """State for a single client session."""

    session_id: str
    # Agents are cached by settings_hash within a session
    # This allows a session to use different models if settings change
    agents: dict[str, Agent] = field(default_factory=dict)  # settings_hash -> Agent
    current_settings_hash: str | None = None
    active_request_id: str | None = None
    cancelled: bool = False
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    last_activity: float = field(
        default_factory=lambda: asyncio.get_event_loop().time()
    )


class AgentManager:
    """Manages agent lifecycle for all sessions."""

    def __init__(self):
        self.logger = logging.getLogger("anya.daemon.agents")

        # Session state (per-session Code agents)
        self._sessions: dict[str, SessionState] = {}

        # Configuration
        self._thinking_budget = os.environ.get("ANYA_THINKING_BUDGET")

        # Lock for agent initialization
        self._init_lock = asyncio.Lock()

        # PUB socket for emitting status events (set by daemon)
        self._pub_socket: zmq.asyncio.Socket | None = None

    async def initialize(self, pub_socket: zmq.asyncio.Socket | None = None):
        """Initialize the agent manager.

        Args:
            pub_socket: PUB socket for emitting status events to clients
        """
        self._pub_socket = pub_socket
        self.logger.info("Agent manager initialized")

    async def get_or_create_session(self, session_id: str) -> SessionState:
        """Get or create a session state."""
        if session_id not in self._sessions:
            self.logger.info(f"Creating new session: {session_id}")
            self._sessions[session_id] = SessionState(session_id=session_id)

        session = self._sessions[session_id]
        session.last_activity = asyncio.get_event_loop().time()
        return session

    async def get_agent_for_session(
        self,
        session_id: str,
        settings: AgentSettings | None = None,
    ) -> Agent:
        """Get or create the Code agent for a session with specific settings.

        Agents are cached by (session_id, settings_hash). If a session uses
        different settings (e.g., different model), a new agent is created
        and cached for those settings.

        Args:
            session_id: The session ID
            settings: Optional AgentSettings from client. If not provided,
                      uses daemon's default environment settings.

        Returns:
            The Code agent for this session and settings combination.
        """
        session = await self.get_or_create_session(session_id)

        # Calculate settings hash for cache key
        if settings is None:
            # Use defaults - create a settings object from daemon's env
            settings = AgentSettings(
                model=os.environ.get("ANYA_MODEL", "gpt-4.1"),
                api_key=os.environ.get("ANYA_API_KEY")
                or os.environ.get("OPENAI_API_KEY"),
                api_base=os.environ.get("ANYA_API_BASE")
                or os.environ.get("OPENAI_API_BASE"),
                api_type=os.environ.get("ANYA_API_TYPE", "responses"),
                thinking_budget=self._thinking_budget,
            )

        settings_hash = settings.settings_hash()

        # Check if we have a cached agent for these settings
        if settings_hash in session.agents:
            self.logger.debug(
                f"Reusing cached agent for session {session_id}, settings_hash={settings_hash}"
            )
            session.current_settings_hash = settings_hash
            return session.agents[settings_hash]

        # Create new agent for these settings
        self.logger.info(
            f"Creating Code agent for session {session_id}, "
            f"model={settings.model}, settings_hash={settings_hash}"
        )

        agent = await CodeAgent(
            thinking_budget=settings.thinking_budget or self._thinking_budget,
            nvim=None,  # No nvim in daemon context
            settings=settings,
        )

        # Cache the agent
        session.agents[settings_hash] = agent
        session.current_settings_hash = settings_hash
        self.logger.info(
            f"Code agent created for session {session_id}, model={settings.model}"
        )

        return agent

    async def end_session(self, session_id: str):
        """End a session and clean up its agents."""
        if session_id in self._sessions:
            self.logger.info(f"Ending session: {session_id}")
            session = self._sessions.pop(session_id)
            # Clear all cached agents
            session.agents.clear()

    async def cancel_request(self, session_id: str, request_id: str) -> bool:
        """Cancel an active request for a session."""
        if session_id not in self._sessions:
            return False

        session = self._sessions[session_id]
        if session.active_request_id == request_id:
            session.cancelled = True
            self.logger.info(f"Cancelled request {request_id} for session {session_id}")
            return True

        return False

    def set_active_request(self, session_id: str, request_id: str):
        """Set the active request for a session."""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.active_request_id = request_id
            session.cancelled = False

    def clear_active_request(self, session_id: str):
        """Clear the active request for a session."""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.active_request_id = None
            session.cancelled = False

    def is_request_cancelled(self, session_id: str) -> bool:
        """Check if the current request for a session is cancelled."""
        if session_id in self._sessions:
            return self._sessions[session_id].cancelled
        return False

    async def get_status(self) -> dict:
        """Get the status of the agent manager."""
        return {
            "sessions_count": len(self._sessions),
            "sessions": list(self._sessions.keys()),
        }

    async def shutdown(self):
        """Shutdown the agent manager and clean up resources."""
        self.logger.info("Shutting down agent manager...")

        # Clean up all sessions
        for session_id in list(self._sessions.keys()):
            await self.end_session(session_id)

        self.logger.info("Agent manager shutdown complete")
