"""Protocol definitions for daemon IPC communication.

Uses CBOR2 for serialization over ZeroMQ IPC sockets.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import cbor2


class RequestType(Enum):
    """Types of requests that can be sent to the daemon."""

    SEND_MESSAGE = "send_message"
    CANCEL_REQUEST = "cancel_request"
    GET_STATUS = "get_status"
    END_SESSION = "end_session"
    SHUTDOWN = "shutdown"
    PING = "ping"
    TOOL_CONFIRMATION_RESPONSE = "tool_confirmation_response"
    GENERATE_TITLE = "generate_title"


class ResponseType(Enum):
    """Types of responses from the daemon."""

    SUCCESS = "success"
    ERROR = "error"
    STREAM_START = "stream_start"
    STREAM_CHUNK = "stream_chunk"
    STREAM_END = "stream_end"
    PONG = "pong"
    STATUS = "status"


class StreamEventType(Enum):
    """Types of streaming events."""

    TEXT_DELTA = "text_delta"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_END = "thinking_end"
    MESSAGE_START = "message_start"
    MESSAGE_END = "message_end"
    ERROR = "error"
    TOOL_CONFIRMATION_REQUEST = "tool_confirmation_request"
    # Exec request - daemon asks plugin to execute command on user's machine
    EXEC_REQUEST = "exec_request"
    # System events (daemon-wide, not tied to a specific request)
    MCP_INIT_START = "mcp_init_start"
    MCP_INIT_COMPLETE = "mcp_init_complete"
    # Per-server probe event: emitted as each server starts/succeeds/fails
    MCP_SERVER_READY = "mcp_server_ready"
    # Memory events
    MEMORY_STORED = "memory_stored"
    # Token usage update
    TOKEN_USAGE = "token_usage"
    # Title generation result (system event)
    TITLE_GENERATED = "title_generated"
    # Buffer modification request
    MODIFY_BUFFER_REQUEST = "modify_buffer_request"


@dataclass
class Request:
    """A request message sent to the daemon."""

    type: RequestType
    session_id: str
    request_id: str
    payload: dict = field(default_factory=dict)

    def serialize(self) -> bytes:
        """Serialize to CBOR bytes."""
        data = {
            "type": self.type.value,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "payload": self.payload,
        }
        return cbor2.dumps(data)

    @classmethod
    def deserialize(cls, data: bytes) -> "Request":
        """Deserialize from CBOR bytes."""
        obj = cbor2.loads(data)
        return cls(
            type=RequestType(obj["type"]),
            session_id=obj["session_id"],
            request_id=obj["request_id"],
            payload=obj.get("payload", {}),
        )


@dataclass
class Response:
    """A response message from the daemon."""

    type: ResponseType
    request_id: str
    payload: dict = field(default_factory=dict)
    error: str | None = None

    def serialize(self) -> bytes:
        """Serialize to CBOR bytes."""
        data = {
            "type": self.type.value,
            "request_id": self.request_id,
            "payload": self.payload,
        }
        if self.error:
            data["error"] = self.error
        return cbor2.dumps(data)

    @classmethod
    def deserialize(cls, data: bytes) -> "Response":
        """Deserialize from CBOR bytes."""
        obj = cbor2.loads(data)
        return cls(
            type=ResponseType(obj["type"]),
            request_id=obj["request_id"],
            payload=obj.get("payload", {}),
            error=obj.get("error"),
        )


@dataclass
class StreamChunk:
    """A streaming chunk sent via PUB socket."""

    request_id: str
    session_id: str
    event_type: StreamEventType
    data: dict = field(default_factory=dict)

    def serialize(self) -> bytes:
        """Serialize to CBOR bytes with topic prefix."""
        # Topic is session_id:request_id for filtering
        topic = f"{self.session_id}:{self.request_id}".encode()
        payload = {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "event_type": self.event_type.value,
            "data": self.data,
        }
        return topic + b" " + cbor2.dumps(payload)

    @classmethod
    def deserialize(cls, data: bytes) -> "StreamChunk":
        """Deserialize from CBOR bytes (after topic stripped)."""
        # Find the space separator between topic and payload
        space_idx = data.find(b" ")
        if space_idx == -1:
            payload_bytes = data
        else:
            payload_bytes = data[space_idx + 1 :]

        obj = cbor2.loads(payload_bytes)
        return cls(
            request_id=obj["request_id"],
            session_id=obj["session_id"],
            event_type=StreamEventType(obj["event_type"]),
            data=obj.get("data", {}),
        )


@dataclass
class SendMessagePayload:
    """Payload for SEND_MESSAGE request."""

    text: str
    conversation_id: str | None = None
    history: list[dict] = field(default_factory=list)
    # Neovim context for tools
    nvim_context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SendMessagePayload":
        return cls(
            text=data.get("text", ""),
            conversation_id=data.get("conversation_id"),
            history=data.get("history", []),
            nvim_context=data.get("nvim_context", {}),
        )


@dataclass
class CancelRequestPayload:
    """Payload for CANCEL_REQUEST."""

    target_request_id: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CancelRequestPayload":
        return cls(target_request_id=data.get("target_request_id", ""))


@dataclass
class AgentSettings:
    """Agent configuration settings passed from client to daemon.

    These settings override daemon-side environment variables, allowing
    each Neovim client to use different models/providers.
    """

    model: str = "gpt-4.1"
    api_key: str | None = None
    api_base: str | None = None
    api_type: str = "responses"  # "responses" or "chat_completions"
    thinking_budget: str | None = None
    disable_mcp: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentSettings":
        return cls(
            model=data.get("model", "gpt-4.1"),
            api_key=data.get("api_key"),
            api_base=data.get("api_base"),
            api_type=data.get("api_type", "responses"),
            thinking_budget=data.get("thinking_budget"),
            disable_mcp=data.get("disable_mcp", False),
        )

    def settings_hash(self) -> str:
        """Generate a hash for caching agents by settings.

        Only includes settings that affect agent creation (not api_key for security).
        """
        import hashlib

        key_parts = [
            self.model or "",
            self.api_base or "",
            self.api_type or "",
            self.thinking_budget or "",
            str(self.disable_mcp),
        ]
        return hashlib.md5("|".join(key_parts).encode()).hexdigest()[:12]


@dataclass
class NvimContext:
    """Neovim context passed to tools via protocol.

    This replaces direct nvim access for daemon-executed tools.
    """

    session_id: str
    cwd: str = ""
    current_buffer: str = ""
    current_buffer_content: str = ""
    open_buffers: list[dict] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    agent_settings: dict = field(default_factory=dict)  # AgentSettings as dict

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "NvimContext":
        return cls(
            session_id=data.get("session_id", ""),
            cwd=data.get("cwd", ""),
            current_buffer=data.get("current_buffer", ""),
            current_buffer_content=data.get("current_buffer_content", ""),
            open_buffers=data.get("open_buffers", []),
            allowed_commands=data.get("allowed_commands", []),
            agent_settings=data.get("agent_settings", {}),
        )

    def get_agent_settings(self) -> AgentSettings:
        """Get AgentSettings from the embedded dict."""
        return AgentSettings.from_dict(self.agent_settings)


def make_error_response(request_id: str, error: str) -> Response:
    """Create an error response."""
    return Response(
        type=ResponseType.ERROR,
        request_id=request_id,
        error=error,
    )


def make_success_response(request_id: str, payload: dict | None = None) -> Response:
    """Create a success response."""
    return Response(
        type=ResponseType.SUCCESS,
        request_id=request_id,
        payload=payload or {},
    )
