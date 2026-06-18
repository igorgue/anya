"""Request handlers for the daemon server.

Handles agent execution and streams responses via ZeroMQ PUB socket.
"""

import asyncio
import logging
import os
import errno
from datetime import datetime, timezone
from typing import Any

import zmq.asyncio
from agents import Runner
from openai.types.responses import ResponseTextDeltaEvent


from ..model_provider import get_run_config
from ..protocol import (
    Request,
    RequestType,
    Response,
    StreamChunk,
    StreamEventType,
    SendMessagePayload,
    CancelRequestPayload,
    NvimContext,
    AgentSettings,
    make_error_response,
    make_success_response,
)
from ..token_tracker import (
    calculate_context_usage,
    format_context_window,
    choose_context_usage,
)
from ..agents.context import NvimPluginContext
from .. import db
from .. import utils, history
from .. import tools as tools_module
from ..memory_agent import (
    extract_memories_from_message,
    make_memory_record,
)
from ..libs.memory import format_memories, search_memories as retrieve_memories
from .agents import AgentManager


DEFAULT_MODEL = os.environ.get("ANYA_MODEL", "gpt-4.1")


def _is_disconnect_send_error(exc: Exception) -> bool:
    err_no = getattr(exc, "errno", None)
    if err_no in {errno.EAGAIN, errno.ENOENT, errno.ENOTSOCK, errno.EPIPE, 107, 88, 2}:
        return True
    text = str(exc).lower()
    markers = (
        "resource temporarily unavailable",
        "socket operation on non-socket",
        "no such file or directory",
        "broken pipe",
        "transport endpoint is not connected",
    )
    return any(marker in text for marker in markers)


def _utc_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(now.microsecond / 1000):03d}Z"


def _extract_history_timestamp(message: dict, default: str) -> str:
    return (
        message.get("created_at")
        or message.get("timestamp")
        or message.get("ended_at")
        or default
    )


def _background_mode_instruction() -> str:
    return (
        "SYSTEM: The user client disconnected and this run is continuing in background mode. "
        "Do not ask the user for confirmation, input, or follow-up before finishing. "
        "Interactive tools and buffer edits may be unavailable. If a tool cannot run without the client, "
        "adapt and finish with the best final answer you can using the available context."
    )


def _detect_filetype(tool_name: str, content: str) -> str | None:
    """Detect filetype for tool output based on tool name and content."""
    if tool_name in ("read_file", "read_many_files"):
        # Try to extract filename from the output header
        # Format: "File: /path/to/file.ext"
        lines = content.split("\n", 5)
        for line in lines[:3]:
            if line.startswith("File:"):
                filename = line.split(":", 1)[1].strip()
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                # Map common extensions to filetypes
                ext_map = {
                    "py": "python",
                    "js": "javascript",
                    "ts": "typescript",
                    "tsx": "typescriptreact",
                    "jsx": "javascriptreact",
                    "rs": "rust",
                    "go": "go",
                    "lua": "lua",
                    "sh": "bash",
                    "bash": "bash",
                    "zsh": "zsh",
                    "json": "json",
                    "yaml": "yaml",
                    "yml": "yaml",
                    "toml": "toml",
                    "md": "markdown",
                    "html": "html",
                    "css": "css",
                    "sql": "sql",
                    "c": "c",
                    "cpp": "cpp",
                    "h": "c",
                    "hpp": "cpp",
                    "java": "java",
                    "rb": "ruby",
                    "php": "php",
                    "swift": "swift",
                    "kt": "kotlin",
                    "vim": "vim",
                }
                return ext_map.get(ext, "text")
    elif tool_name == "exec":
        return "bash"
    elif tool_name == "search_code":
        return "text"
    elif tool_name == "list_files":
        return "text"
    elif tool_name == "gh":
        return "text"
    return "text"


def _is_retryable_error(exception: Exception) -> bool:
    """Check if an exception represents a retryable transient API/network error."""
    error_str = str(exception)
    error_lower = error_str.lower()

    def _dict_is_retryable(error_payload: dict) -> bool:
        error = (
            error_payload.get("error", {}) if isinstance(error_payload, dict) else {}
        )
        code = str(error.get("code", "") or error_payload.get("code", ""))
        message = str(
            error.get("message", "") or error_payload.get("message", "")
        ).lower()
        err_type = str(error.get("type", "") or error_payload.get("type", "")).lower()

        if code.startswith("5"):
            return True
        if err_type in {"server_error", "api_connection_error", "timeout_error"}:
            return True
        transient_markers = (
            "internal network failure",
            "network failure",
            "connection reset",
            "connection aborted",
            "temporarily unavailable",
            "service unavailable",
            "gateway timeout",
            "bad gateway",
            "timed out",
            "timeout",
        )
        return any(marker in message for marker in transient_markers)

    if isinstance(exception, dict):
        return _dict_is_retryable(exception)

    if (
        "'code': '5" in error_str
        or '"code": "5' in error_str
        or "'code': 5" in error_str
        or '"code": 5' in error_str
    ):
        return True

    transient_markers = (
        "internal network failure",
        "network failure",
        "connection reset",
        "connection aborted",
        "connection error",
        "connection interrupted",
        "temporarily unavailable",
        "service unavailable",
        "gateway timeout",
        "bad gateway",
        "timed out",
        "timeout",
    )
    if any(marker in error_lower for marker in transient_markers):
        return True

    try:
        from openai import APIError, APIConnectionError, APITimeoutError, RateLimitError

        if isinstance(exception, (APIConnectionError, APITimeoutError, RateLimitError)):
            return True
        if isinstance(exception, APIError):
            status_code = getattr(exception, "status_code", None)
            body = getattr(exception, "body", None)
            if status_code is not None and status_code >= 500:
                return True
            if isinstance(body, dict) and _dict_is_retryable(body):
                return True
    except ImportError:
        pass

    import asyncio
    import httpx

    if isinstance(
        exception,
        (
            asyncio.TimeoutError,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
        ),
    ):
        return True

    return False



def _is_tool_call_order_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "tool_calls" in text
        and ("tool_call_id" in text or "tool messages" in text)
        and ("must be followed" in text or "insufficient tool messages" in text)
    )


def _drop_tool_tail(items: list[dict]) -> list[dict]:
    """Drop an incomplete tool-call continuation from SDK input items.

    Some OpenAI-compatible Chat Completions providers, including DeepSeek, reject
    any assistant tool_calls message unless it is immediately followed by every
    matching tool message. If a provider raises that error after a streamed SDK
    run has already produced tool-call items, retrying with the same SDK result
    input can replay an incomplete assistant tool-call tail. Keep the stable
    caller history and remove only the trailing generated tool-call block.
    """
    last_tool_call_idx: int | None = None
    for idx, item in enumerate(items):
        if isinstance(item, dict) and item.get("type") in {
            "function_call",
            "function_tool_call",
            "tool_call",
        }:
            last_tool_call_idx = idx

    if last_tool_call_idx is None:
        return items

    return items[:last_tool_call_idx]

def _should_retry(exception: Exception) -> bool:
    """Determine if we should retry based on the exception type."""
    return _is_retryable_error(exception)


def _retry_delay_seconds(attempt: int) -> float:
    """Backoff schedule for transient LLM/API failures."""
    delays = [1.0, 2.0, 5.0]
    index = max(0, min(attempt - 1, len(delays) - 1))
    return delays[index]


class RequestHandler:
    """Handles incoming requests and executes agent operations."""

    def __init__(
        self,
        agent_manager: AgentManager,
        pub_socket: zmq.asyncio.Socket,
    ):
        self.logger = logging.getLogger("anya.daemon.handler")
        self.agent_manager = agent_manager
        self.pub_socket = pub_socket
        # Store confirmation responses indexed by confirmation_id
        # Maps: confirmation_id -> asyncio.Future with the user's choice
        self.confirmation_responses: dict[str, asyncio.Future] = {}
        # Store responses that arrive before the future is created
        self.pending_confirmation_responses: dict[str, str] = {}

        # Track active tool state per session for proper cleanup on cancellation
        # Maps: session_id -> {"tools": list, "is_edit": bool, "was_called": bool}
        self._active_tools: dict[str, dict] = {}
        self._telegram_response_callbacks: dict[str, Any] = {}

    async def handle(self, request: Request) -> Response:
        """Handle an incoming request."""
        try:
            if request.type == RequestType.SEND_MESSAGE:
                return await self._handle_send_message(request)
            elif request.type == RequestType.CANCEL_REQUEST:
                return await self._handle_cancel_request(request)
            elif request.type == RequestType.TOOL_CONFIRMATION_RESPONSE:
                return await self._handle_confirmation_response(request)
            elif request.type == RequestType.GENERATE_TITLE:
                return await self._handle_generate_title(request)
            elif request.type == RequestType.COMPACT_CONVERSATION:
                return await self._handle_compact_conversation(request)
            elif request.type == RequestType.GET_SYSTEM_PROMPT:
                return await self._handle_get_system_prompt(request)
            elif request.type == RequestType.SEARCH_MENTIONS:
                return await self._handle_search_mentions(request)
            elif request.type == RequestType.GET_MENTION_CONTENT:
                return await self._handle_get_mention_content(request)
            elif request.type == RequestType.TELEGRAM_PAIR:
                return await self._handle_telegram_pair(request)
            else:
                return make_error_response(
                    request.request_id,
                    f"Unknown request type: {request.type.value}",
                )
        except Exception as e:
            self.logger.exception(f"Error handling request: {e}")
            return make_error_response(request.request_id, str(e))


    async def _handle_telegram_pair(self, request: Request) -> Response:
        """Create a Telegram pairing code for the connected router client."""
        telegram_client = getattr(self, "telegram_client", None)
        if telegram_client is None:
            return make_error_response(
                request.request_id,
                "Telegram router is not configured (client not started). Set ANYA_ROUTER_URL to disable auto-connect.",
            )

        try:
            result = await telegram_client.get_pairing_code()
        except Exception as e:
            self.logger.exception("Failed to create Telegram pairing code")
            return make_error_response(request.request_id, str(e))

        return make_success_response(request.request_id, result)

    async def _handle_send_message(self, request: Request) -> Response:
        """Handle a SEND_MESSAGE request.

        Returns immediately with acknowledgment, then processes agent in background.
        This is required because ZeroMQ REP sockets need to send a response before
        they can receive another request (like TOOL_CONFIRMATION_RESPONSE).
        """
        self.logger.info(f"Handling SEND_MESSAGE for session {request.session_id}")
        payload = SendMessagePayload.from_dict(request.payload)

        session_state = await self.agent_manager.get_or_create_session(request.session_id)

        # Start agent processing in background task
        task = asyncio.create_task(
            self._process_agent_in_background(
                request.session_id,
                request.request_id,
                payload,
            )
        )
        self.agent_manager.set_active_request(request.session_id, request.request_id, task)
        session_state.last_activity = asyncio.get_event_loop().time()

        # Return immediately so the REP socket can receive other requests
        # (like TOOL_CONFIRMATION_RESPONSE)
        return make_success_response(request.request_id, {"status": "started"})

    def _persist_assistant_placeholder(
        self,
        conversation_id: str | None,
        request_id: str,
        created_at: str,
        model: str | None,
    ) -> None:
        if not conversation_id:
            return
        try:
            inserted = db.save_message_dict(
                msg_id=request_id,
                conversation_id=conversation_id,
                role="assistant",
                content="",
                author="Anya",
                model=model,
                created_at=created_at,
                ended_at=None,
                markers=None,
            )
            if not inserted:
                db.update_message(request_id, content="", ended_at=None, markers=None)
        except Exception as e:
            self.logger.warning("Failed to persist assistant placeholder for %s: %s", request_id, e)

    def _persist_assistant_message(
        self,
        conversation_id: str | None,
        request_id: str,
        created_at: str,
        ended_at: str | None,
        content: str,
    ) -> None:
        if not conversation_id:
            return
        try:
            cleaned_content, markers_json = history.extract_markers_from_content(content)
        except Exception:
            cleaned_content, markers_json = history.extract_markers_from_content(content)

        if not cleaned_content:
            # Even with no content, persist ended_at if provided so the
            # duration virtual text always shows in the UI.
            if ended_at:
                db.update_message(request_id, ended_at=ended_at)
                db.update_conversation_timestamp(conversation_id, ended_at)
            return

        updated = db.update_message(
            request_id,
            content=cleaned_content,
            ended_at=ended_at,
            markers=markers_json,
        )
        if not updated:
            db.save_message_dict(
                msg_id=request_id,
                conversation_id=conversation_id,
                role="assistant",
                content=cleaned_content,
                author="Anya",
                model=DEFAULT_MODEL,
                created_at=created_at,
                ended_at=ended_at,
                markers=markers_json,
            )
        if ended_at:
            db.update_conversation_timestamp(conversation_id, ended_at)

    async def _process_agent_in_background(
        self,
        session_id: str,
        request_id: str,
        payload: SendMessagePayload,
    ):
        """Process agent request in background after returning response to client."""
        # Send stream start
        self.logger.info(f"Sending MESSAGE_START for request {request_id}")
        await self._send_stream_chunk(
            session_id,
            request_id,
            StreamEventType.MESSAGE_START,
            {"message_id": request_id},
        )

        try:
            nvim_context = NvimContext.from_dict(payload.nvim_context)
            agent_settings = nvim_context.get_agent_settings()
            created_at = _utc_timestamp()
            self._persist_assistant_placeholder(
                payload.conversation_id,
                request_id,
                created_at,
                agent_settings.model,
            )

            memory_task = asyncio.create_task(
                self._store_memories_background(
                    session_id,
                    request_id,
                    payload,
                    agent_settings,
                )
            )

            # Run agent streaming
            final_message = await self._run_agent_streaming(
                session_id,
                request_id,
                payload,
            )

            try:
                await memory_task
            except Exception as e:
                self.logger.warning(f"Memory background task failed: {e}")

            end_timestamp = _utc_timestamp()
            self._persist_assistant_message(
                payload.conversation_id,
                request_id,
                created_at,
                end_timestamp,
                final_message,
            )

            if payload.conversation_id and final_message.strip():
                asyncio.create_task(
                    self._generate_title_background(
                        session_id=session_id,
                        request_id=f"title_{payload.conversation_id}",
                        conversation_id=payload.conversation_id,
                        user_message=payload.text,
                        assistant_message=final_message,
                        settings_dict=agent_settings.to_dict(),
                    )
                )

            # Send stream end
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.MESSAGE_END,
                {"status": "success"},
            )

            # Call Telegram response callback for this request if set.
            telegram_callback = self._telegram_response_callbacks.pop(request_id, None)
            if telegram_callback:
                try:
                    await telegram_callback(final_message)
                except Exception as e:
                    self.logger.warning(f"Telegram response callback failed: {e}")

        except asyncio.CancelledError:
            # Send TOOL_CALL_END if there's an active tool (e.g., pending edit)
            if session_id in self._active_tools:
                active_tools = self._active_tools.pop(session_id)
                await self._send_stream_chunk(
                    session_id,
                    request_id,
                    StreamEventType.TOOL_CALL_END,
                    {
                        "tools": active_tools.get("tools", []),
                        "output": "",
                        "has_failure": False,
                        "is_edit_tool": active_tools.get("is_edit", False),
                        "skip_output": False,
                        "unclosed": True,
                    },
                )
            end_timestamp = _utc_timestamp()
            self._persist_assistant_message(
                payload.conversation_id,
                request_id,
                created_at if "created_at" in locals() else end_timestamp,
                end_timestamp,
                locals().get("final_message", ""),
            )
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.MESSAGE_END,
                {"status": "cancelled"},
            )

        except Exception as e:
            self.logger.exception(f"Error in agent execution: {e}")
            # Send TOOL_CALL_END if there's an active tool (e.g., pending edit)
            if session_id in self._active_tools:
                active_tools = self._active_tools.pop(session_id)
                await self._send_stream_chunk(
                    session_id,
                    request_id,
                    StreamEventType.TOOL_CALL_END,
                    {
                        "tools": active_tools.get("tools", []),
                        "output": "",
                        "has_failure": False,
                        "is_edit_tool": active_tools.get("is_edit", False),
                        "skip_output": False,
                        "unclosed": True,
                    },
                )
            end_timestamp = _utc_timestamp()
            self._persist_assistant_message(
                payload.conversation_id,
                request_id,
                created_at if "created_at" in locals() else end_timestamp,
                end_timestamp,
                locals().get("final_message", ""),
            )
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.ERROR,
                {"error": str(e)},
            )
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.MESSAGE_END,
                {"status": "error"},
            )

        finally:
            self.agent_manager.clear_active_request(session_id)
            self._active_tools.pop(session_id, None)

    async def _handle_generate_title(self, request: Request) -> Response:
        """Handle a GENERATE_TITLE request.

        Returns immediately; title is generated in a background task and
        the result is emitted as a TITLE_GENERATED event to the requesting session.
        """
        asyncio.create_task(
            self._generate_title_background(
                session_id=request.session_id,
                request_id=request.request_id,
                conversation_id=request.payload.get("conversation_id", ""),
                user_message=request.payload.get("user_message", ""),
                assistant_message=request.payload.get("assistant_message", ""),
                settings_dict=request.payload.get("settings", {}),
            )
        )
        return make_success_response(request.request_id, {"status": "started"})

    async def _generate_title_background(
        self,
        session_id: str,
        request_id: str,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
        settings_dict: dict,
    ):
        """Generate a conversation title using the same API client as the coding agent."""
        title = None
        success = False

        # Skip if the conversation already has a title
        try:
            conv = db.get_conversation(conversation_id)
            if conv and conv.get("title"):
                self.logger.info(
                    f"Conversation {conversation_id} already has title, skipping generation"
                )
                # Still emit event to close the fidget notification
                result_chunk = StreamChunk(
                    request_id="system",
                    session_id="system",
                    event_type=StreamEventType.TITLE_GENERATED,
                    data={
                        "conversation_id": conversation_id,
                        "title": conv.get("title"),
                        "success": True,
                        "originating_session_id": session_id,
                    },
                )
                try:
                    await self.pub_socket.send(result_chunk.serialize())
                except Exception as e:
                    self.logger.warning(f"Failed to emit TITLE_GENERATED event: {e}")
                return
        except Exception as e:
            self.logger.warning(f"Failed to check existing title: {e}")

        try:
            from ..title_agent import generate_title

            settings = AgentSettings.from_dict(settings_dict) if settings_dict else None
            self.logger.info(
                "Starting title generation for conversation %s (session %s)",
                conversation_id,
                session_id,
            )
            title = await asyncio.wait_for(
                generate_title(user_message, assistant_message, settings),
                timeout=35.0,
            )
            success = bool(title)
            self.logger.info(
                "Finished title generation for conversation %s: success=%s",
                conversation_id,
                success,
            )
        except asyncio.TimeoutError:
            self.logger.warning(
                "Title generation timed out for conversation %s",
                conversation_id,
            )
        except Exception as e:
            self.logger.warning(f"Title generation failed: {e}")

        if success and title and conversation_id:
            try:
                db.update_conversation_title(conversation_id, title)
            except Exception as e:
                self.logger.warning("Failed to persist title for conversation %s: %s", conversation_id, e)

        # Always emit result so the plugin can finish the fidget
        # Use system topic so all instances receive it, but include session_id
        # so each instance can filter appropriately
        result_chunk = StreamChunk(
            request_id="system",
            session_id="system",
            event_type=StreamEventType.TITLE_GENERATED,
            data={
                "conversation_id": conversation_id,
                "title": title or "",
                "success": success,
                "originating_session_id": session_id,
            },
        )
        try:
            await self.pub_socket.send(result_chunk.serialize())
            self.logger.info(
                "Emitted TITLE_GENERATED for conversation %s (session %s, success=%s)",
                conversation_id,
                session_id,
                success,
            )
        except Exception as e:
            self.logger.warning(f"Failed to emit TITLE_GENERATED event: {e}")

    async def _handle_compact_conversation(self, request: Request) -> Response:
        """Handle a COMPACT_CONVERSATION request.

        Returns immediately; compaction runs in a background task and the result
        is emitted as a CONVERSATION_COMPACTED system event.
        """
        asyncio.create_task(
            self._compact_conversation_background(
                session_id=request.session_id,
                request_id=request.request_id,
                conversation_id=request.payload.get("conversation_id", ""),
                history=request.payload.get("history", []),
                settings_dict=request.payload.get("settings", {}),
            )
        )
        return make_success_response(request.request_id, {"status": "started"})

    async def _compact_conversation_background(
        self,
        session_id: str,
        request_id: str,
        conversation_id: str,
        history: list[dict],
        settings_dict: dict,
    ):
        """Generate a conversation summary and emit CONVERSATION_COMPACTED."""
        summary = None
        try:
            from ..compact_agent import compact_conversation

            settings = AgentSettings.from_dict(settings_dict) if settings_dict else None
            summary = await compact_conversation(history, settings)
        except Exception as e:
            self.logger.warning(f"Compaction failed: {e}")

        result_chunk = StreamChunk(
            request_id="system",
            session_id="system",
            event_type=StreamEventType.CONVERSATION_COMPACTED,
            data={
                "conversation_id": conversation_id,
                "summary": summary or "",
                "success": bool(summary),
                "originating_session_id": session_id,
            },
        )
        try:
            await self.pub_socket.send(result_chunk.serialize())
        except Exception as e:
            self.logger.warning(f"Failed to emit CONVERSATION_COMPACTED event: {e}")

    async def _store_memories_background(
        self,
        session_id: str,
        request_id: str,
        payload: SendMessagePayload,
        agent_settings: AgentSettings | None = None,
    ) -> None:
        """Extract and persist durable memories from the latest user message."""
        conversation_id = payload.conversation_id
        message_id = request_id

        try:
            extracted = await extract_memories_from_message(
                payload.text,
                agent_settings,
                conversation_context=self._build_recent_memory_extraction_context(payload),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.warning(f"Memory extraction failed: {e}")
            return

        stored_count = 0
        for memory in extracted:
            try:
                record = make_memory_record(
                    memory,
                    conversation_id=conversation_id,
                    message_id=message_id,
                )
                saved = db.save_memory(record)
                if not saved:
                    continue
                stored_count += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.warning(f"Failed to persist memory: {e}")


    def _build_recent_memory_extraction_context(self, payload: SendMessagePayload) -> str:
        """Build a compact recent context for resolving short memory statements."""
        messages = payload.history or []
        if not messages:
            return ""

        lines: list[str] = []
        for message in messages[-8:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip()
            content = str(message.get("content", "")).strip()
            if not role or not content:
                continue
            # Keep context compact and avoid huge tool outputs.
            content = " ".join(content.split())[:600]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    async def _handle_cancel_request(self, request: Request) -> Response:
        """Handle a CANCEL_REQUEST request."""
        payload = CancelRequestPayload.from_dict(request.payload)
        success = await self.agent_manager.cancel_request(
            request.session_id,
            payload.target_request_id,
        )
        return make_success_response(
            request.request_id,
            {"cancelled": success},
        )

    async def _handle_confirmation_response(self, request: Request) -> Response:
        """Handle a TOOL_CONFIRMATION_RESPONSE from the plugin."""
        confirmation_id = request.payload.get("confirmation_id")
        choice = request.payload.get("choice")

        if not confirmation_id:
            return make_error_response(request.request_id, "Missing confirmation_id")

        # Store the response and signal waiting coroutine
        if confirmation_id in self.confirmation_responses:
            future = self.confirmation_responses[confirmation_id]
            future.set_result(choice or "Cancel")
        else:
            # Response arrived before wait_for_confirmation was called
            # Store it for when wait_for_confirmation is called
            self.pending_confirmation_responses[confirmation_id] = choice or "Cancel"

        return make_success_response(request.request_id)

    async def wait_for_confirmation(
        self, confirmation_id: str, timeout: float | None = None
    ) -> str:
        """Wait for user confirmation from the plugin.

        Args:
            confirmation_id: Unique ID for this confirmation request
            timeout: Timeout in seconds, or None to wait indefinitely

        Returns:
            User's choice ("Execute", "Allow for this session", "Cancel", etc.)
        """
        # Check if response already arrived before we started waiting
        if confirmation_id in self.pending_confirmation_responses:
            choice = self.pending_confirmation_responses.pop(confirmation_id)
            return choice

        future: asyncio.Future[str] = asyncio.Future()
        self.confirmation_responses[confirmation_id] = future

        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return "Cancel"
        finally:
            self.confirmation_responses.pop(confirmation_id, None)
            self.pending_confirmation_responses.pop(confirmation_id, None)

    async def _send_stream_chunk(
        self,
        session_id: str,
        request_id: str,
        event_type: StreamEventType,
        data: dict,
    ) -> bool:
        """Send a streaming chunk via PUB socket.

        Returns False when the session appears disconnected and UI delivery should
        be treated as best-effort only. Detached/background runs must continue
        even when no client is listening anymore.
        """
        chunk = StreamChunk(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            data=data,
        )
        self.logger.debug(f"Sending stream chunk: {event_type.value}")
        try:
            await self.pub_socket.send(chunk.serialize())
            return True
        except Exception as e:
            if session_id != "system" and _is_disconnect_send_error(e):
                self.logger.info(
                    "Stream delivery failed for session %s request %s (%s); marking session detached and continuing in background",
                    session_id,
                    request_id,
                    e,
                )
                await self.agent_manager.end_session(session_id)
                return False
            raise

    def _build_memory_context(self, payload: SendMessagePayload) -> str | None:
        """Build hidden memory context before the agent run.

        Retrieval should be forgiving: user prompts like "What's my name?" may not
        share literal tokens with a stored fact like "User's full name is ...".
        Start with recent personal memories, then add query matches for the latest
        prompt, preserving newest-first order and de-duplicating by id/text.
        """
        query = (payload.text or "").strip()
        memories: list[dict] = []
        seen: set[str] = set()

        def add(results: list[dict]) -> None:
            for memory in results:
                key = str(memory.get("id") or memory.get("text") or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                memories.append(memory)

        # Query-specific matches must come first. Otherwise older but exact
        # personal facts (for example full name / birth date) can be crowded out
        # by the recent category snapshots before the final 40-memory cap.
        if query:
            add(retrieve_memories(query=query, category="personal", limit=10))
            add(retrieve_memories(query=query, category="preference", limit=10))
            add(retrieve_memories(query=query, limit=10))

        # Durable user facts/preferences should still be broadly available for
        # non-memory-specific prompts.
        add(retrieve_memories(category="preference", limit=20))
        add(retrieve_memories(category="personal", limit=20))

        if not memories:
            return None

        return format_memories(memories[:40])

    async def _run_agent_streaming(
        self,
        session_id: str,
        request_id: str,
        payload: SendMessagePayload,
    ) -> str:
        """Run the agent with streaming, sending events via PUB socket."""
        self.logger.info(f"Starting agent streaming for session {session_id}")

        nvim_context = NvimContext.from_dict(payload.nvim_context)
        agent_settings = nvim_context.get_agent_settings()
        self.logger.info(
            f"Using agent settings: model={agent_settings.model}, "
            f"api_base={agent_settings.api_base}, api_type={agent_settings.api_type}"
        )

        memory_context = (
            None
            if nvim_context.request_kind == "do"
            else self._build_memory_context(payload)
        )

        # Keep memory request-scoped. Agent instances are cached and some model
        # providers cache system prompts, so relying only on instructions makes
        # memory updates flaky across chats.
        agent = await self.agent_manager.get_agent_for_session(
            session_id,
            settings=agent_settings,
            cwd=nvim_context.cwd,
            request_kind=nvim_context.request_kind,
            memory_context=None,
        )
        self.logger.info(f"Got agent for session {session_id}")

        import uuid

        async def confirmation_callback(prompt: str, options: list[str]) -> str:
            # Telegram sessions auto-confirm tool prompts (daemon-side execution)
            if session_id.startswith("telegram:"):
                return options[0] if options else "Confirm"
            confirmation_id = str(uuid.uuid4())
            if self.agent_manager.is_session_detached(session_id):
                return "Cancel"
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.TOOL_CONFIRMATION_REQUEST,
                {
                    "confirmation_id": confirmation_id,
                    "prompt": prompt,
                    "options": options,
                },
            )
            try:
                choice = await self.wait_for_confirmation(confirmation_id)
                return choice
            except Exception as e:
                self.logger.error(f"Error waiting for confirmation: {e}")
                return "Cancel"

        async def exec_callback(
            command: str, cwd: str, timeout: int, ui_dir: str | None = None
        ) -> dict:
            # Telegram sessions execute commands directly on the daemon
            if session_id.startswith("telegram:"):
                import asyncio
                self.logger.info(f"Telegram exec: {command[:120]}...")
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                try:
                    effective_timeout = (timeout + 30.0) if timeout is not None else None
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=effective_timeout
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    stdout, stderr = await proc.communicate()
                    return {
                        "stdout": "",
                        "stderr": stderr.decode(),
                        "returncode": -1,
                        "error": "Command timed out",
                    }
                return {
                    "stdout": stdout.decode(),
                    "stderr": stderr.decode(),
                    "returncode": proc.returncode,
                }
            if self.agent_manager.is_session_detached(session_id):
                return {
                    "stdout": "",
                    "stderr": "",
                    "returncode": 1,
                    "error": "Client disconnected; interactive exec is unavailable in background mode.",
                }
            confirmation_id = str(uuid.uuid4())
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.EXEC_REQUEST,
                {
                    "confirmation_id": confirmation_id,
                    "command": command,
                    "cwd": cwd,
                    "timeout": timeout,
                    "ui_dir": ui_dir or "",
                },
            )
            try:
                effective_timeout = (timeout + 30.0) if timeout is not None else None
                result = await self.wait_for_confirmation(
                    confirmation_id, timeout=effective_timeout
                )
                if isinstance(result, dict):
                    return result
                elif isinstance(result, str):
                    import json

                    try:
                        return json.loads(result)
                    except json.JSONDecodeError:
                        return {
                            "stdout": "",
                            "stderr": "",
                            "returncode": 1,
                            "error": f"Invalid response: {result}",
                        }
                return {
                    "stdout": "",
                    "stderr": "",
                    "returncode": 1,
                    "error": "Invalid response type",
                }
            except Exception as e:
                self.logger.error(f"Error waiting for exec result: {e}")
                return {
                    "stdout": "",
                    "stderr": "",
                    "returncode": 1,
                    "error": str(e),
                }

        async def modify_buffer_callback(
            buf_path: str,
            content: str,
            mode: str,
            set_modified: bool,
        ) -> str:
            # Telegram sessions have no Neovim buffers to modify
            if session_id.startswith("telegram:"):
                return "Done"
            if self.agent_manager.is_session_detached(session_id):
                return "Error: Client disconnected; buffer modification is unavailable in background mode."
            confirmation_id = str(uuid.uuid4())
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.MODIFY_BUFFER_REQUEST,
                {
                    "confirmation_id": confirmation_id,
                    "buf_path": buf_path,
                    "content": content,
                    "mode": mode,
                    "set_modified": set_modified,
                },
            )
            try:
                result = await self.wait_for_confirmation(confirmation_id, timeout=30.0)
                return result
            except Exception as e:
                self.logger.error(f"Error waiting for modify buffer result: {e}")
                return f"Error: {e}"

        async def task_list_callback(payload: dict[str, Any]) -> None:
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.TASK_LIST_UPDATE,
                payload,
            )

        context = NvimPluginContext(
            nvim=None,
            session_id=session_id,
            allowed_commands=set(nvim_context.allowed_commands),
            confirmation_callback=confirmation_callback,
            exec_callback=exec_callback,
            modify_buffer_callback=modify_buffer_callback,
            task_list_callback=task_list_callback,
            cwd=nvim_context.cwd,
            current_buffer=nvim_context.current_buffer,
            open_buffers=nvim_context.open_buffers,
            detached=self.agent_manager.is_session_detached(session_id),
        )

        llm_history = list(payload.history)
        if memory_context:
            llm_history.append({
                "role": "system",
                "content": (
                    "Use these durable user facts naturally when relevant. "
                    "Do not mention memories, memory search, or memory storage. "
                    "If asked whether a fact was saved or will be remembered, answer briefly and naturally; "
                    "do not claim persistent memory is unavailable.\n"
                    f"{memory_context.strip()}"
                ),
            })
        assistant_parts: list[str] = []
        last_partial_save = 0
        detached_instruction_added = False
        thinking_started = False
        thinking_finalized = False
        thinking_source = None
        assistant_text_started = False
        parallel_tools = []
        parallel_skip_tools = []
        pending_tool_outputs = []
        expected_outputs = 0
        tool_was_called = False
        in_anya_marker = False

        run_config = get_run_config(agent_settings)
        if run_config:
            self.logger.info(
                f"Using custom run config for model provider (model={agent_settings.model})"
            )

        max_retries = 3
        attempt = 0

        try:
            while True:
                attempt += 1
                result = Runner.run_streamed(
                    starting_agent=agent,
                    input=llm_history,
                    context=context,
                    max_turns=8 if nvim_context.request_kind == "do" else 1000,
                    run_config=run_config,
                )

                try:
                    async for event in result.stream_events():
                        if self.agent_manager.is_request_cancelled(session_id):
                            raise asyncio.CancelledError()

                        if (
                            self.agent_manager.is_session_detached(session_id)
                            and not detached_instruction_added
                        ):
                            llm_history.append({"role": "system", "content": _background_mode_instruction()})
                            detached_instruction_added = True
                            context.detached = True
                            self.logger.info(
                                "Session %s detached; continuing request %s in background mode",
                                session_id,
                                request_id,
                            )

                        is_reasoning_event = False
                        reasoning_text = None

                        if event.type == "raw_response_event" and hasattr(
                            event, "data"
                        ):
                            data = event.data
                            data_type = getattr(data, "type", "")

                            if isinstance(data_type, str) and data_type.startswith(
                                "response.reasoning"
                            ):
                                is_reasoning_event = True
                                if data_type == "response.reasoning_summary_text.delta":
                                    if (
                                        thinking_source is None
                                        or thinking_source == "summary"
                                    ):
                                        thinking_source = "summary"
                                        reasoning_text = getattr(data, "delta", "")
                                elif data_type in (
                                    "response.reasoning_text.delta",
                                    "response.reasoning_content.delta",
                                ):
                                    if thinking_source is None:
                                        thinking_source = "content"
                                        reasoning_text = getattr(data, "delta", "")
                                    elif thinking_source == "content":
                                        reasoning_text = getattr(data, "delta", "")

                            if (
                                not reasoning_text
                                and hasattr(data, "delta")
                                and thinking_source != "summary"
                            ):
                                delta = data.delta
                                if (
                                    hasattr(delta, "reasoning_content")
                                    and delta.reasoning_content
                                ):
                                    is_reasoning_event = True
                                    if thinking_source is None:
                                        thinking_source = "content"
                                    reasoning_text = delta.reasoning_content
                                elif hasattr(delta, "reasoning") and delta.reasoning:
                                    is_reasoning_event = True
                                    if thinking_source is None:
                                        thinking_source = "content"
                                    reasoning_text = delta.reasoning

                        if is_reasoning_event:
                            if assistant_text_started or not reasoning_text:
                                continue
                            if thinking_finalized:
                                thinking_started = False
                                thinking_finalized = False
                                thinking_source = None
                            if not thinking_started:
                                thinking_started = True
                                await self._send_stream_chunk(
                                    session_id,
                                    request_id,
                                    StreamEventType.THINKING_START,
                                    {},
                                )
                            await self._send_stream_chunk(
                                session_id,
                                request_id,
                                StreamEventType.THINKING_DELTA,
                                {"text": reasoning_text},
                            )
                            continue

                        should_finalize_thinking = (
                            thinking_started
                            and not thinking_finalized
                            and hasattr(event, "data")
                            and isinstance(event.data, ResponseTextDeltaEvent)
                            and bool(getattr(event.data, "delta", ""))
                        )
                        if should_finalize_thinking:
                            thinking_finalized = True
                            await self._send_stream_chunk(
                                session_id,
                                request_id,
                                StreamEventType.THINKING_END,
                                {},
                            )

                        if event.type == "run_item_stream_event":
                            item = event.item
                            item_type = getattr(item, "type", None)

                            if item_type == "reasoning_item":
                                if assistant_text_started:
                                    continue
                                if thinking_started and thinking_finalized:
                                    thinking_started = False
                                    thinking_finalized = False
                                    thinking_source = None
                                if not thinking_started:
                                    raw_item = getattr(item, "raw_item", None)
                                    summary_parts = (
                                        getattr(raw_item, "summary", None)
                                        if raw_item
                                        else None
                                    )
                                    content_parts = (
                                        getattr(raw_item, "content", None)
                                        if raw_item
                                        else None
                                    )
                                    summary_text = "\n".join(
                                        getattr(p, "text", "")
                                        for p in (summary_parts or [])
                                        if getattr(p, "text", "")
                                    )
                                    content_text = "\n".join(
                                        getattr(p, "text", "")
                                        for p in (content_parts or [])
                                        if getattr(p, "text", "")
                                    )
                                    display_text = summary_text or content_text

                                    if display_text:
                                        thinking_started = True
                                        thinking_finalized = True
                                        await self._send_stream_chunk(
                                            session_id,
                                            request_id,
                                            StreamEventType.THINKING_START,
                                            {},
                                        )
                                        await self._send_stream_chunk(
                                            session_id,
                                            request_id,
                                            StreamEventType.THINKING_DELTA,
                                            {"text": display_text},
                                        )
                                        await self._send_stream_chunk(
                                            session_id,
                                            request_id,
                                            StreamEventType.THINKING_END,
                                            {},
                                        )
                                    continue

                            if (
                                item_type != "reasoning_item"
                                and thinking_started
                                and not thinking_finalized
                            ):
                                thinking_finalized = True
                                await self._send_stream_chunk(
                                    session_id,
                                    request_id,
                                    StreamEventType.THINKING_END,
                                    {},
                                )

                            if item_type == "tool_call_item":
                                raw_item = getattr(item, "raw_item", None)
                                tool_name = getattr(item, "name", None) or (
                                    getattr(raw_item, "name", "") if raw_item else ""
                                )
                                tool_args = getattr(item, "arguments", "") or (
                                    getattr(raw_item, "arguments", "")
                                    if raw_item
                                    else ""
                                )

                                if tool_name:
                                    tool_was_called = True
                                    tool_func = getattr(tools_module, tool_name, None)
                                    skip_output = (
                                        getattr(tool_func, "skip_output", False)
                                        if tool_func
                                        else False
                                    )

                                    if skip_output:
                                        expected_outputs += 1
                                        parallel_skip_tools.append(
                                            {"name": tool_name, "args": tool_args}
                                        )
                                    else:
                                        status = "tool_pending"
                                        parallel_tools.append(
                                            {
                                                "name": tool_name,
                                                "args": tool_args,
                                                "status": status,
                                            }
                                        )
                                        expected_outputs += 1
                                        await self._send_stream_chunk(
                                            session_id,
                                            request_id,
                                            StreamEventType.TOOL_CALL_START,
                                            {
                                                "tool_name": tool_name,
                                                "tool_args": tool_args,
                                                "status": status,
                                                "parallel_tools": parallel_tools,
                                                "is_first": len(parallel_tools) == 1,
                                            },
                                        )
                                        self._active_tools[session_id] = {
                                            "tools": parallel_tools
                                            + parallel_skip_tools,
                                            "is_edit": False,
                                            "was_called": tool_was_called,
                                        }

                            elif item_type == "tool_call_output_item":
                                tool_output = getattr(item, "output", "")
                                pending_tool_outputs.append(tool_output)

                                if (
                                    len(pending_tool_outputs) >= expected_outputs
                                    and expected_outputs > 0
                                ):
                                    has_failure = any(
                                        o.strip().startswith("Error:")
                                        for o in pending_tool_outputs
                                        if isinstance(o, str)
                                    )
                                    await self._send_stream_chunk(
                                        session_id,
                                        request_id,
                                        StreamEventType.TOOL_CALL_END,
                                        {
                                            "tools": parallel_tools
                                            + parallel_skip_tools,
                                            "has_failure": has_failure,
                                            "skip_output": bool(parallel_skip_tools)
                                            and not parallel_tools,
                                        },
                                    )
                                    self._active_tools.pop(session_id, None)
                                    pending_tool_outputs = []
                                    expected_outputs = 0
                                    tool_was_called = False
                                    parallel_tools = []
                                    parallel_skip_tools = []

                        if hasattr(event, "data") and isinstance(
                            event.data, ResponseTextDeltaEvent
                        ):
                            raw_delta = event.data.delta
                            if raw_delta:
                                assistant_text_started = True
                                delta, in_anya_marker = utils.filter_anya_markers(
                                    raw_delta, in_anya_marker
                                )
                                if not delta or tool_was_called:
                                    continue
                                assistant_parts.append(delta)
                                current_message = "".join(assistant_parts)
                                if payload.conversation_id and (len(current_message) - last_partial_save >= 200):
                                    self._persist_assistant_message(
                                        payload.conversation_id,
                                        request_id,
                                        _extract_history_timestamp(llm_history[-1], _utc_timestamp()) if llm_history else _utc_timestamp(),
                                        None,
                                        current_message,
                                    )
                                    last_partial_save = len(current_message)
                                await self._send_stream_chunk(
                                    session_id,
                                    request_id,
                                    StreamEventType.TEXT_DELTA,
                                    {"text": delta},
                                )

                    try:
                        if (
                            hasattr(result, "context_wrapper")
                            and result.context_wrapper
                        ):
                            raw_usage = result.context_wrapper.usage
                            self.logger.debug(
                                f"Raw usage from context_wrapper: {raw_usage}"
                            )
                            if raw_usage:
                                model = agent_settings.model or os.environ.get(
                                    "ANYA_MODEL", DEFAULT_MODEL
                                )
                                usage, aggregate_usage, request_count = (
                                    choose_context_usage(raw_usage, provider=model)
                                )
                                (
                                    percentage,
                                    context_window,
                                    usable_context,
                                    is_overflow,
                                ) = calculate_context_usage(usage, model)
                                context_tokens = usage.context_tokens
                                await self._send_stream_chunk(
                                    session_id,
                                    request_id,
                                    StreamEventType.TOKEN_USAGE,
                                    {
                                        "total_tokens": context_tokens,
                                        "prompt_tokens": usage.input,
                                        "completion_tokens": usage.output,
                                        "reasoning_tokens": usage.reasoning,
                                        "cache_read": usage.cache_read,
                                        "cache_write": usage.cache_write,
                                        "percentage": percentage,
                                        "context_window": context_window,
                                        "usable_context": usable_context,
                                        "is_overflow": is_overflow,
                                        "aggregate_context_tokens": aggregate_usage.context_tokens,
                                        "request_usage_entries": request_count,
                                    },
                                )
                                self.logger.info(
                                    f"Token usage: {context_tokens}/{format_context_window(usable_context)} ({percentage:.1f}%) "
                                    f"[in:{usage.input} out:{usage.output} cache:{usage.cache_read}] "
                                    f"aggregate_ctx={aggregate_usage.context_tokens} entries={request_count}"
                                )
                    except Exception as e:
                        self.logger.warning(
                            f"Error sending token usage: {e}", exc_info=True
                        )

                    break

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if _is_tool_call_order_error(e) and attempt <= max_retries:
                        try:
                            retry_history = result.to_input_list(mode="normalized")
                        except Exception:
                            retry_history = llm_history
                        pruned_history = _drop_tool_tail(list(retry_history))
                        if len(pruned_history) < len(retry_history):
                            llm_history = pruned_history
                            self.logger.warning(
                                "Provider rejected incomplete tool-call history on attempt %s/%s for session %s; pruning trailing tool-call block and retrying: %s",
                                attempt,
                                max_retries + 1,
                                session_id,
                                e,
                            )
                            await self._send_stream_chunk(
                                session_id,
                                request_id,
                                StreamEventType.ERROR,
                                {
                                    "error": f"Provider rejected incomplete tool-call history, retrying ({attempt}/{max_retries})...",
                                },
                            )
                            await asyncio.sleep(_retry_delay_seconds(attempt))
                            continue

                    if _should_retry(e) and attempt <= max_retries:
                        delay = _retry_delay_seconds(attempt)
                        self.logger.warning(
                            "Transient agent/API error on attempt %s/%s for session %s: %s. Retrying in %.1fs",
                            attempt,
                            max_retries + 1,
                            session_id,
                            e,
                            delay,
                        )
                        await self._send_stream_chunk(
                            session_id,
                            request_id,
                            StreamEventType.ERROR,
                            {
                                "error": f"Transient network/API error, retrying ({attempt}/{max_retries})...",
                            },
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise
        finally:
            if thinking_started and not thinking_finalized:
                thinking_finalized = True
                await self._send_stream_chunk(
                    session_id,
                    request_id,
                    StreamEventType.THINKING_END,
                    {},
                )

            if tool_was_called:
                await self._send_stream_chunk(
                    session_id,
                    request_id,
                    StreamEventType.TOOL_CALL_END,
                    {
                        "tools": parallel_tools,
                        "output": "",
                        "has_failure": False,
                        "is_edit_tool": False,
                        "skip_output": False,
                        "unclosed": True,
                    },
                )

        return "".join(assistant_parts)

    async def _handle_get_system_prompt(self, request: Request) -> Response:
        """Handle a GET_SYSTEM_PROMPT request.

        Returns the full system prompt for the Code agent, including all
        dynamic instructions, context, AGENTS.md, and skills metadata.
        """
        from ..protocol import AgentSettings

        # Get settings from payload or use defaults
        settings_dict = request.payload.get("settings", {})
        cwd = request.payload.get("cwd")

        settings = AgentSettings.from_dict(settings_dict) if settings_dict else None

        try:
            # Build the system prompt the same way as when creating an agent
            from ..agents.utils import get_instructions
            from ..agents.dynamic_instructions import update_agent_instructions
            from ..system_prompt import apply_system_prompt
            from ..libs import get_libs_prompt

            base_instructions = get_instructions("agent.md")
            libs_instructions = get_libs_prompt()
            instructions = update_agent_instructions(
                base_instructions, libs_instructions
            )
            instructions = apply_system_prompt(instructions, nvim=None, cwd=cwd)

            return make_success_response(
                request.request_id,
                {
                    "system_prompt": instructions,
                    "model": settings.model
                    if settings
                    else os.environ.get("ANYA_MODEL", "gpt-4.1"),
                },
            )
        except Exception as e:
            self.logger.exception(f"Error getting system prompt: {e}")
            return make_error_response(request.request_id, str(e))

    async def _handle_search_mentions(self, request: Request) -> Response:
        """Handle a SEARCH_MENTIONS request.

        Search conversations for @mention completion.
        """
        query = request.payload.get("query", "")
        limit = request.payload.get("limit", 20)

        try:
            results = db.search_conversation_mentions(query, limit)
            return make_success_response(request.request_id, {"results": results})
        except Exception as e:
            self.logger.exception(f"Error searching mentions: {e}")
            return make_error_response(request.request_id, str(e))

    async def _handle_get_mention_content(self, request: Request) -> Response:
        """Handle a GET_MENTION_CONTENT request.

        Get the content of a conversation for mention context injection.
        """
        conversation_id = request.payload.get("conversation_id")
        max_chars = request.payload.get("max_chars", 8000)

        if not conversation_id:
            return make_error_response(request.request_id, "conversation_id required")

        try:
            content = db.get_conversation_content_for_mention(
                conversation_id, max_chars
            )
            if content is None:
                return make_error_response(
                    request.request_id, f"Conversation {conversation_id} not found"
                )
            return make_success_response(request.request_id, {"content": content})
        except Exception as e:
            self.logger.exception(f"Error getting mention content: {e}")
            return make_error_response(request.request_id, str(e))
