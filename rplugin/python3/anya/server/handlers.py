"""Request handlers for the daemon server.

Handles agent execution and streams responses via ZeroMQ PUB socket.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import zmq.asyncio
from agents import Runner
from openai.types.responses import ResponseTextDeltaEvent

from ..model_provider import get_run_config
from ..protocol import (
    Request,
    RequestType,
    Response,
    ResponseType,
    StreamChunk,
    StreamEventType,
    SendMessagePayload,
    CancelRequestPayload,
    NvimContext,
    AgentSettings,
    make_error_response,
    make_success_response,
)
from ..agents import MAIN_AGENT_NAME
from ..token_tracker import (
    calculate_context_usage,
    format_context_window,
    parse_usage,
)
from ..agents.context import NvimPluginContext
from .. import markers
from .. import utils
from .. import tools as tools_module
from ..spacing import SpacingManager, ContentType
from .agents import AgentManager


DEFAULT_MODEL = os.environ.get("ANYA_MODEL", "gpt-4.1")


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
            else:
                return make_error_response(
                    request.request_id,
                    f"Unknown request type: {request.type.value}",
                )
        except Exception as e:
            self.logger.exception(f"Error handling request: {e}")
            return make_error_response(request.request_id, str(e))

    async def _handle_send_message(self, request: Request) -> Response:
        """Handle a SEND_MESSAGE request.

        Returns immediately with acknowledgment, then processes agent in background.
        This is required because ZeroMQ REP sockets need to send a response before
        they can receive another request (like TOOL_CONFIRMATION_RESPONSE).
        """
        self.logger.info(f"Handling SEND_MESSAGE for session {request.session_id}")
        payload = SendMessagePayload.from_dict(request.payload)

        # Set active request
        self.agent_manager.set_active_request(request.session_id, request.request_id)

        # Start agent processing in background task
        asyncio.create_task(
            self._process_agent_in_background(
                request.session_id,
                request.request_id,
                payload,
            )
        )

        # Return immediately so the REP socket can receive other requests
        # (like TOOL_CONFIRMATION_RESPONSE)
        return make_success_response(request.request_id, {"status": "started"})

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
            # Run agent streaming
            await self._run_agent_streaming(
                session_id,
                request_id,
                payload,
            )

            # Send stream end
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.MESSAGE_END,
                {"status": "success"},
            )

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
        the result is emitted as a TITLE_GENERATED system event.
        """
        asyncio.create_task(
            self._generate_title_background(
                conversation_id=request.payload.get("conversation_id", ""),
                user_message=request.payload.get("user_message", ""),
                assistant_message=request.payload.get("assistant_message", ""),
                settings_dict=request.payload.get("settings", {}),
            )
        )
        return make_success_response(request.request_id, {"status": "started"})

    async def _generate_title_background(
        self,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
        settings_dict: dict,
    ):
        """Generate a conversation title using the same API client as the coding agent."""
        import re

        title = None
        try:
            settings = AgentSettings.from_dict(settings_dict) if settings_dict else None

            # Resolve API config — same logic as model_provider.py
            if settings:
                model = settings.model or "gpt-4.1"
                base_url = settings.api_base
                api_key = settings.api_key
            else:
                model = os.environ.get("ANYA_MODEL", "gpt-4.1")
                base_url = os.environ.get("ANYA_API_BASE") or os.environ.get(
                    "OPENAI_API_BASE"
                )
                api_key = os.environ.get("ANYA_API_KEY") or os.environ.get(
                    "OPENAI_API_KEY"
                )

            if not base_url and model and "/" in model:
                base_url = "https://openrouter.ai/api/v1"

            from openai import AsyncOpenAI

            client_kwargs: dict = {}
            if api_key:
                client_kwargs["api_key"] = api_key
            if base_url:
                client_kwargs["base_url"] = base_url

            client = AsyncOpenAI(**client_kwargs)

            def _clean(text: str, max_chars: int = 400) -> str:
                text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
                text = re.sub(r"\[\[.*?\]\]", "", text)
                text = re.sub(r"\n{3,}", "\n\n", text)
                return text.strip()[:max_chars]

            prompt = (
                "Generate a short, descriptive title (maximum 8 words) for this "
                "conversation. Output ONLY the title — no quotes, no trailing "
                "punctuation, no explanation.\n\n"
                f"User: {_clean(user_message)}\n"
                f"Assistant: {_clean(assistant_message)}\n"
                "Title:"
            )

            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=30,
                    temperature=0.3,
                ),
                timeout=30.0,
            )

            raw = response.choices[0].message.content or ""
            cleaned = raw.strip().strip("\"'").rstrip(".!?").strip()
            title = cleaned if cleaned else None

        except Exception as e:
            self.logger.warning(f"Title generation failed: {e}")

        # Always emit result so the plugin can finish the fidget
        result_chunk = StreamChunk(
            request_id="system",
            session_id="system",
            event_type=StreamEventType.TITLE_GENERATED,
            data={
                "conversation_id": conversation_id,
                "title": title or "",
                "success": bool(title),
            },
        )
        try:
            await self.pub_socket.send(result_chunk.serialize())
        except Exception as e:
            self.logger.warning(f"Failed to emit TITLE_GENERATED event: {e}")

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
        self, confirmation_id: str, timeout: float = 300.0
    ) -> str:
        """Wait for user confirmation from the plugin.

        Args:
            confirmation_id: Unique ID for this confirmation request
            timeout: Timeout in seconds

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
    ):
        """Send a streaming chunk via PUB socket."""
        chunk = StreamChunk(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            data=data,
        )
        self.logger.debug(f"Sending stream chunk: {event_type.value}")
        await self.pub_socket.send(chunk.serialize())

    async def _run_agent_streaming(
        self,
        session_id: str,
        request_id: str,
        payload: SendMessagePayload,
    ):
        """Run the agent with streaming, sending events via PUB socket."""
        self.logger.info(f"Starting agent streaming for session {session_id}")

        # Build context from payload
        nvim_context = NvimContext.from_dict(payload.nvim_context)

        # Extract agent settings from context (client-side env vars override daemon's)
        agent_settings = nvim_context.get_agent_settings()
        self.logger.info(
            f"Using agent settings: model={agent_settings.model}, "
            f"api_base={agent_settings.api_base}, api_type={agent_settings.api_type}"
        )

        # Get or create agent for this session with the specified settings
        agent = await self.agent_manager.get_agent_for_session(
            session_id, settings=agent_settings
        )
        self.logger.info(f"Got agent for session {session_id}")

        # Update daemon's CWD to match client's CWD when a request comes in
        # This ensures tools that use os.getcwd() get the correct directory
        if nvim_context.cwd:
            try:
                os.chdir(nvim_context.cwd)
                self.logger.debug(f"Changed daemon CWD to: {nvim_context.cwd}")
            except Exception as e:
                self.logger.warning(f"Failed to change CWD to {nvim_context.cwd}: {e}")

        # Create confirmation callback that sends requests to the plugin
        import uuid

        async def confirmation_callback(prompt: str, options: list[str]) -> str:
            """Ask user for confirmation via the plugin."""
            confirmation_id = str(uuid.uuid4())

            # Send confirmation request to plugin
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

            # Wait for user response using the wait_for_confirmation method
            # This creates a future and waits for _handle_confirmation_response to set the result
            try:
                choice = await self.wait_for_confirmation(confirmation_id)
                return choice
            except Exception as e:
                self.logger.error(f"Error waiting for confirmation: {e}")
                return "Cancel"

        async def exec_callback(
            command: str, cwd: str, timeout: int, ui_dir: str | None = None
        ) -> dict:
            """Request command execution on the plugin (user's machine).

            Sends the command to the plugin which executes it locally and
            returns the result. This ensures commands run on the user's
            machine even if the daemon is running remotely.
            """
            confirmation_id = str(uuid.uuid4())

            # Send exec request to plugin
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

            # Wait for execution result (timeout + buffer for network latency)
            try:
                result = await self.wait_for_confirmation(
                    confirmation_id, timeout=timeout + 30.0
                )
                # Result should be a JSON string with stdout/stderr/returncode
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

        context = NvimPluginContext(
            nvim=None,  # No nvim in daemon
            session_id=session_id,
            allowed_commands=set(nvim_context.allowed_commands),
            confirmation_callback=confirmation_callback,
            exec_callback=exec_callback,
            cwd=nvim_context.cwd,
            current_buffer=nvim_context.current_buffer,
            open_buffers=nvim_context.open_buffers,
        )

        # Use provided history or empty
        llm_history = payload.history

        # Tracking state
        spacing_manager = SpacingManager()
        thinking_started = False
        thinking_finalized = False
        thinking_source = None
        parallel_tools = []
        parallel_skip_tools = []
        pending_tool_outputs = []
        expected_outputs = 0
        tool_was_called = False
        in_anya_marker = False

        # Get custom run config for OpenRouter models (models with '/' or ':' in name)
        # Pass agent_settings so it uses client's settings, not daemon's environment
        run_config = get_run_config(agent_settings)
        if run_config:
            self.logger.info(
                f"Using custom run config for model provider (model={agent_settings.model})"
            )

        # Run the agent
        result = Runner.run_streamed(
            starting_agent=agent,
            input=llm_history,
            context=context,
            max_turns=1000,
            run_config=run_config,
        )

        try:
            async for event in result.stream_events():
                # Check for cancellation
                if self.agent_manager.is_request_cancelled(session_id):
                    raise asyncio.CancelledError()

                # Detect reasoning/thinking content
                is_reasoning_event = False
                reasoning_text = None

                if event.type == "raw_response_event" and hasattr(event, "data"):
                    data = event.data
                    data_type = getattr(data, "type", "")

                    if isinstance(data_type, str) and data_type.startswith(
                        "response.reasoning"
                    ):
                        is_reasoning_event = True

                        if data_type == "response.reasoning_summary_text.delta":
                            if thinking_source is None or thinking_source == "summary":
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

                # Handle reasoning events
                if is_reasoning_event:
                    # Don't send reasoning events after thinking has been finalized
                    # (e.g., when a tool call has started)
                    if thinking_finalized:
                        continue

                    if not thinking_started:
                        thinking_started = True
                        await self._send_stream_chunk(
                            session_id,
                            request_id,
                            StreamEventType.THINKING_START,
                            {},
                        )

                    if reasoning_text:
                        await self._send_stream_chunk(
                            session_id,
                            request_id,
                            StreamEventType.THINKING_DELTA,
                            {"text": reasoning_text},
                        )

                    continue

                # Handle finalization transition
                if thinking_started and not thinking_finalized:
                    thinking_finalized = True
                    await self._send_stream_chunk(
                        session_id,
                        request_id,
                        StreamEventType.THINKING_END,
                        {},
                    )

                # Handle run item events
                if event.type == "run_item_stream_event":
                    item = event.item
                    item_type = getattr(item, "type", None)

                    # Handle reasoning items
                    if item_type == "reasoning_item" and not thinking_started:
                        raw_item = getattr(item, "raw_item", None)
                        summary_parts = (
                            getattr(raw_item, "summary", None) if raw_item else None
                        )
                        content_parts = (
                            getattr(raw_item, "content", None) if raw_item else None
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

                    # Handle tool calls
                    if item_type == "tool_call_item":
                        raw_item = getattr(item, "raw_item", None)
                        tool_name = getattr(item, "name", None) or (
                            getattr(raw_item, "name", "") if raw_item else ""
                        )
                        tool_args = getattr(item, "arguments", "") or (
                            getattr(raw_item, "arguments", "") if raw_item else ""
                        )

                        if tool_name:
                            tool_was_called = True

                            # Check if tool should skip output
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
                                    "tools": parallel_tools + parallel_skip_tools,
                                    "is_edit": False,
                                    "was_called": tool_was_called,
                                }

                    # Handle tool outputs
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
                                    "tools": parallel_tools + parallel_skip_tools,
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

                # Handle text deltas
                if hasattr(event, "data") and isinstance(
                    event.data, ResponseTextDeltaEvent
                ):
                    raw_delta = event.data.delta
                    if raw_delta:
                        # Filter anya markers for display
                        delta, in_anya_marker = utils.filter_anya_markers(
                            raw_delta, in_anya_marker
                        )
                        if not delta:
                            continue

                        # Suppress text deltas while a tool is executing
                        # This prevents tool output from being interleaved with LLM text
                        if tool_was_called:
                            continue

                        # Send text delta
                        await self._send_stream_chunk(
                            session_id,
                            request_id,
                            StreamEventType.TEXT_DELTA,
                            {
                                "text": delta,
                            },
                        )

            # Send token usage after streaming completes
            try:
                if hasattr(result, "context_wrapper") and result.context_wrapper:
                    raw_usage = result.context_wrapper.usage
                    if raw_usage:
                        # Get model from client settings (not env var, which may differ)
                        model = agent_settings.model or os.environ.get("ANYA_MODEL", DEFAULT_MODEL)

                        # Parse usage with detailed breakdown (opencode approach)
                        usage = parse_usage(raw_usage, provider=model)

                        # Calculate context usage with usable context consideration
                        percentage, context_window, usable_context, is_overflow = (
                            calculate_context_usage(usage, model)
                        )

                        # Use context_tokens for the "used" display
                        # This is input + cache.read + output (what actually fills context)
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
                            },
                        )
                        self.logger.info(
                            f"Token usage: {context_tokens}/{format_context_window(usable_context)} ({percentage:.1f}%) "
                            f"[in:{usage.input} out:{usage.output} cache:{usage.cache_read}]"
                        )
            except Exception as e:
                self.logger.debug(f"Error sending token usage: {e}")

        finally:
            # Ensure thinking is closed even on exception/cancellation
            if thinking_started and not thinking_finalized:
                await self._send_stream_chunk(
                    session_id,
                    request_id,
                    StreamEventType.THINKING_END,
                    {},
                )

            # Close any open tool folds even on exception/cancellation
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
