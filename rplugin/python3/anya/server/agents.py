"""Agent management for the daemon server.

Manages agent lifecycle:
- MCP agent: Single instance, always running, shared across all sessions
- Code agent: One instance per session, created on first request
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import zmq.asyncio
from agents import Agent

from ..mcp_loader import MCPManager, load_mcp_server_configs, create_mcp_servers
from ..agents import CodeAgent, MCPAgent, MAIN_AGENT_NAME
from ..protocol import StreamChunk, StreamEventType


@dataclass
class SessionState:
    """State for a single client session."""

    session_id: str
    agent: Agent | None = None
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

        # MCP state (shared across all sessions)
        self._mcp_servers: list = []
        self._mcp_agent: Agent | None = None
        self._mcp_initialized = False
        self._mcp_ready = False  # True when MCP servers are connected

        # Session state (per-session Code agents)
        self._sessions: dict[str, SessionState] = {}

        # Configuration
        self._thinking_budget = os.environ.get("ANYA_THINKING_BUDGET")
        self._mcp_enabled = os.environ.get("ANYA_DISABLE_MCP", "0") != "1"

        # Lock for agent initialization
        self._init_lock = asyncio.Lock()

        # Background task for MCP initialization
        self._mcp_init_task: asyncio.Task | None = None

        # PUB socket for emitting status events (set by daemon)
        self._pub_socket: zmq.asyncio.Socket | None = None

    async def initialize(self, pub_socket: zmq.asyncio.Socket | None = None):
        """Initialize the agent manager. MCP initialization runs in background.

        Args:
            pub_socket: PUB socket for emitting status events to clients
        """
        async with self._init_lock:
            if self._mcp_initialized:
                return

            self.logger.info("Initializing agent manager...")
            self._pub_socket = pub_socket

            if self._mcp_enabled:
                # Start MCP initialization in background - don't block daemon startup
                self._mcp_init_task = asyncio.create_task(
                    self._initialize_mcp_background()
                )

            self._mcp_initialized = True
            self.logger.info(
                "Agent manager initialized (MCP initializing in background)"
            )

    async def _initialize_mcp_background(self):
        """Background task for MCP initialization."""
        # Emit start event
        await self._emit_mcp_status(
            StreamEventType.MCP_INIT_START,
            {"message": "Initializing MCP servers..."},
        )

        try:
            await self._initialize_mcp()
            self._mcp_ready = True
            server_names = [getattr(s, "name", "unknown") for s in self._mcp_servers]
            self.logger.info(f"MCP initialization complete: {server_names}")
            await self._emit_mcp_status(
                StreamEventType.MCP_INIT_COMPLETE,
                {
                    "success": True,
                    "servers": server_names,
                    "message": f"Connected to {len(server_names)} MCP server(s)",
                },
            )
        except Exception as e:
            self.logger.error(f"MCP initialization failed: {e}")
            await self._emit_mcp_status(
                StreamEventType.MCP_INIT_COMPLETE,
                {
                    "success": False,
                    "error": str(e),
                    "message": f"MCP initialization failed: {e}",
                },
            )

    async def _emit_mcp_status(self, event_type: StreamEventType, data: dict):
        """Emit MCP status event via PUB socket.

        Uses 'system' topic for daemon-wide events not tied to a specific request.
        """
        if self._pub_socket is None:
            return

        try:
            chunk = StreamChunk(
                request_id="system",
                session_id="system",
                event_type=event_type,
                data=data,
            )
            await self._pub_socket.send(chunk.serialize())
            self.logger.debug(f"Emitted MCP status: {event_type.value}")
        except Exception as e:
            self.logger.warning(f"Failed to emit MCP status: {e}")

    async def _initialize_mcp(self):
        """Initialize MCP servers and MCP agent."""
        self.logger.info("Loading MCP server configurations...")

        configs = load_mcp_server_configs()
        if not configs:
            self.logger.info("No MCP server configurations found")
            return

        self.logger.info(f"Found {len(configs)} MCP server configurations")

        servers = create_mcp_servers(configs)
        if not servers:
            self.logger.warning("No MCP servers could be created")
            return

        # Connect all servers in parallel
        connected = await self._connect_servers(servers)

        if connected:
            self._mcp_servers = connected
            self.logger.info(f"Connected to {len(connected)} MCP servers")

            # Create the shared MCP agent
            self._mcp_agent = await MCPAgent(connected)
            if self._mcp_agent:
                self.logger.info("MCP agent created successfully")
        else:
            self.logger.warning("No MCP servers connected")

    async def _connect_servers(self, servers: list) -> list:
        """Connect to MCP servers in parallel."""

        async def connect_single(server) -> tuple:
            name = getattr(server, "name", "unknown")
            try:
                if hasattr(server, "connect"):
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
                self.logger.error(f"Connection exception: {result}")
                continue
            server, success, error = result
            if success:
                connected.append(server)
            elif error:
                self.logger.warning(error)

        return connected

    async def get_or_create_session(self, session_id: str) -> SessionState:
        """Get or create a session state."""
        if session_id not in self._sessions:
            self.logger.info(f"Creating new session: {session_id}")
            self._sessions[session_id] = SessionState(session_id=session_id)

        session = self._sessions[session_id]
        session.last_activity = asyncio.get_event_loop().time()
        return session

    async def get_agent_for_session(self, session_id: str) -> Agent:
        """Get or create the Code agent for a session.

        Each session gets its own Code agent instance that persists
        for the lifetime of the session.
        """
        session = await self.get_or_create_session(session_id)

        if session.agent is None:
            self.logger.info(f"Creating Code agent for session: {session_id}")
            session.agent = await CodeAgent(
                mcp_servers=self._mcp_servers if self._mcp_servers else None,
                thinking_budget=self._thinking_budget,
                nvim=None,  # No nvim in daemon context
            )
            self.logger.info(f"Code agent created for session: {session_id}")

        return session.agent

    async def end_session(self, session_id: str):
        """End a session and clean up its agent."""
        if session_id in self._sessions:
            self.logger.info(f"Ending session: {session_id}")
            session = self._sessions.pop(session_id)
            # Agent cleanup if needed
            session.agent = None

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
            "mcp_enabled": self._mcp_enabled,
            "mcp_initialized": self._mcp_initialized,
            "mcp_ready": self._mcp_ready,
            "mcp_servers_count": len(self._mcp_servers),
            "mcp_servers": [getattr(s, "name", "unknown") for s in self._mcp_servers],
            "sessions_count": len(self._sessions),
            "sessions": list(self._sessions.keys()),
        }

    def is_mcp_ready(self) -> bool:
        """Check if MCP servers are connected and ready."""
        return self._mcp_ready

    async def shutdown(self):
        """Shutdown the agent manager and clean up resources."""
        self.logger.info("Shutting down agent manager...")

        # Clean up all sessions
        for session_id in list(self._sessions.keys()):
            await self.end_session(session_id)

        # Disconnect MCP servers
        for server in self._mcp_servers:
            try:
                if hasattr(server, "disconnect"):
                    await server.disconnect()
            except Exception as e:
                self.logger.warning(f"Error disconnecting MCP server: {e}")

        self._mcp_servers = []
        self._mcp_agent = None
        self._mcp_initialized = False

        self.logger.info("Agent manager shutdown complete")
