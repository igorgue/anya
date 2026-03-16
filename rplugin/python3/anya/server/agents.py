"""Agent management for the daemon server.

Manages agent lifecycle:
- Code agent: Cached by (session_id, settings_hash) to allow different clients
              to use different settings
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field

import anyio
import zmq.asyncio
from agents import Agent

from ..agents import CodeAgent, DoAgent
from ..skills import discover_skills, skills_fingerprint
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

        # MCP probing disabled at startup to avoid 100% CPU issue with
        # anyio task group cleanup. Servers are initialized lazily on first use.
        # The MCP tools cache is still used if it exists from a previous session.
        self._mcp_probe_task = None

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
        cwd: str | None = None,
        request_kind: str = "chat",
        memory_context: str | None = None,
    ) -> Agent:
        """Get or create the Code agent for a session with specific settings.

        Agents are cached by (session_id, settings_hash, cwd). If a session uses
        different settings (e.g., different model) or a different working directory,
        a new agent is created and cached.

        Args:
            session_id: The session ID
            settings: Optional AgentSettings from client. If not provided,
                      uses daemon's default environment settings.
            cwd: Optional working directory for this session's project.

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

        # Include CWD and skills fingerprint in cache key so each project
        # gets its own agent, and the agent is recreated if skills change on disk.
        # The lightweight `:Anya do` agent intentionally skips project docs/skills,
        # so avoid that discovery work on its hot path.
        if request_kind == "do":
            skill_fp = "do"
        else:
            skills = discover_skills(cwd=cwd)
            skill_fp = skills_fingerprint(skills)
        memory_fp = (memory_context or "").strip()
        cache_key = (
            f"{request_kind}:{settings_hash}:{cwd or ''}:{skill_fp}:{hash(memory_fp)}"
        )

        # Check if we have a cached agent for these settings
        if cache_key in session.agents:
            self.logger.debug(
                f"Reusing cached agent for session {session_id}, cache_key={cache_key}"
            )
            session.current_settings_hash = settings_hash
            return session.agents[cache_key]

        # Create new agent for these settings
        agent_label = "Do" if request_kind == "do" else "Code"
        self.logger.info(
            f"Creating {agent_label} agent for session {session_id}, "
            f"model={settings.model}, cwd={cwd}, cache_key={cache_key}"
        )

        agent_factory = DoAgent if request_kind == "do" else CodeAgent
        factory_kwargs = {
            "thinking_budget": settings.thinking_budget or self._thinking_budget,
            "nvim": None,  # No nvim in daemon context
            "settings": settings,
            "cwd": cwd,
        }
        if request_kind != "do":
            factory_kwargs["memory_context"] = memory_context
        agent = await agent_factory(**factory_kwargs)

        # Cache the agent
        session.agents[cache_key] = agent
        session.current_settings_hash = settings_hash
        self.logger.info(
            f"{agent_label} agent created for session {session_id}, model={settings.model}"
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

    async def _emit_system_event(self, event_type, data: dict):
        """Emit a daemon-wide system event via the PUB socket."""
        if not self._pub_socket:
            return
        from ..protocol import StreamChunk

        chunk = StreamChunk(
            request_id="system",
            session_id="system",
            event_type=event_type,
            data=data,
        )
        try:
            await self._pub_socket.send(chunk.serialize())
        except Exception as e:
            self.logger.warning(f"Failed to emit system event: {e}")

    async def _safe_probe_mcp_servers(self):
        """Wrapper around _probe_mcp_servers that catches all exceptions."""
        try:
            await self._probe_mcp_servers()
        except asyncio.CancelledError:
            self.logger.info("MCP probe task cancelled")
            raise
        except Exception as e:
            self.logger.error(f"MCP probe task failed with exception: {e}")
            # Don't re-raise - this is a background task

    async def _probe_mcp_servers(self):
        """Background task: probe all configured MCP servers and emit fidget events."""
        from ..mcp_loader import load_mcp_server_configs, create_mcp_servers
        from ..protocol import StreamEventType

        configs = load_mcp_server_configs()
        if not configs:
            return

        servers = create_mcp_servers(configs)
        if not servers:
            return

        await self._emit_system_event(
            StreamEventType.MCP_INIT_START,
            {"message": f"starting {len(servers)} server(s)..."},
        )

        tool_results: dict[str, list] = {}

        async def _probe_one(server):
            """Probe a single MCP server with timeout.

            IMPORTANT: We use anyio.fail_after for timeouts because the MCP library
            uses anyio task groups internally. Using asyncio.timeout causes
            "Attempted to exit cancel scope in a different task" errors.
            """
            name = getattr(server, "name", "unknown")
            await self._emit_system_event(
                StreamEventType.MCP_SERVER_READY,
                {"server": name, "status": "starting"},
            )

            try:
                # Use anyio.fail_after for proper anyio task group compatibility
                with anyio.fail_after(15.0):
                    async with server:
                        tools = await server.list_tools()
                        tool_results[name] = [
                            {
                                "name": getattr(
                                    t,
                                    "name",
                                    t.get("name", "") if isinstance(t, dict) else "",
                                ),
                                "description": getattr(
                                    t,
                                    "description",
                                    t.get("description", "")
                                    if isinstance(t, dict)
                                    else "",
                                ),
                            }
                            for t in (tools or [])
                        ]
                        await self._emit_system_event(
                            StreamEventType.MCP_SERVER_READY,
                            {
                                "server": name,
                                "status": "ready",
                                "tool_count": len(tools),
                            },
                        )
                        return name
            except TimeoutError:
                self.logger.warning(f"MCP probe timeout for '{name}'")
                await self._emit_system_event(
                    StreamEventType.MCP_SERVER_READY,
                    {"server": name, "status": "timeout"},
                )
                return None
            except asyncio.CancelledError:
                self.logger.info(f"MCP probe cancelled for '{name}'")
                raise
            except Exception as e:
                self.logger.warning(f"MCP probe failed for '{name}': {e}")
                await self._emit_system_event(
                    StreamEventType.MCP_SERVER_READY,
                    {"server": name, "status": "failed", "error": str(e)},
                )
                return None

        # Use return_exceptions=True to prevent one failure from affecting others
        # and add an overall timeout for the entire probe operation using anyio
        try:
            with anyio.fail_after(60):
                results = await asyncio.gather(
                    *[_probe_one(s) for s in servers],
                    return_exceptions=True,
                )
        except TimeoutError:
            self.logger.warning("MCP probe overall timeout exceeded")
            results = []

        # Filter out None and exceptions
        ready = []
        for r in results:
            if isinstance(r, Exception):
                self.logger.warning(f"MCP probe exception: {r}")
            elif r is not None:
                ready.append(r)

        # Persist tool listings so the agent prompt can include them without discovery calls
        if tool_results:
            import json
            from pathlib import Path

            cache_path = (
                Path.home() / ".local" / "share" / "anya" / "mcp_tools_cache.json"
            )
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "w") as f:
                    json.dump(tool_results, f)
            except Exception as e:
                self.logger.warning(f"Failed to write MCP tools cache: {e}")

        await self._emit_system_event(
            StreamEventType.MCP_INIT_COMPLETE,
            {"success": True, "servers": ready},
        )
        self.logger.info(
            f"MCP probe complete: {len(ready)}/{len(servers)} servers ready"
        )

    async def get_status(self) -> dict:
        """Get the status of the agent manager."""
        return {
            "sessions_count": len(self._sessions),
            "sessions": list(self._sessions.keys()),
        }

    async def shutdown(self):
        """Shutdown the agent manager and clean up resources."""
        self.logger.info("Shutting down agent manager...")

        # Cancel the MCP probe task if it's still running
        if hasattr(self, "_mcp_probe_task") and self._mcp_probe_task:
            if not self._mcp_probe_task.done():
                self._mcp_probe_task.cancel()
                try:
                    await self._mcp_probe_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    self.logger.warning(f"Error cancelling MCP probe task: {e}")

        # Clean up all sessions
        for session_id in list(self._sessions.keys()):
            await self.end_session(session_id)

        self.logger.info("Agent manager shutdown complete")
