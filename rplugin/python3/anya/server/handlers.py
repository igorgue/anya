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
    make_error_response,
    make_success_response,
)
from ..agents import MAIN_AGENT_NAME
from ..agents.context import NvimPluginContext
from .. import markers
from .. import utils
from .. import tools as tools_module
from .agents import AgentManager


DEFAULT_MODEL = os.environ.get("ANYA_MODEL", "gpt-4.1")


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
        # Lock to serialize edit confirmations - only one edit at a time
        self._edit_lock = asyncio.Lock()

    async def handle(self, request: Request) -> Response:
        """Handle an incoming request."""
        try:
            if request.type == RequestType.SEND_MESSAGE:
                return await self._handle_send_message(request)
            elif request.type == RequestType.CANCEL_REQUEST:
                return await self._handle_cancel_request(request)
            elif request.type == RequestType.TOOL_CONFIRMATION_RESPONSE:
                return await self._handle_confirmation_response(request)
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
            await self._send_stream_chunk(
                session_id,
                request_id,
                StreamEventType.MESSAGE_END,
                {"status": "cancelled"},
            )

        except Exception as e:
            self.logger.exception(f"Error in agent execution: {e}")
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

        # Get or create agent for this session
        agent = await self.agent_manager.get_agent_for_session(session_id)
        self.logger.info(f"Got agent for session {session_id}")

        # Build context from payload
        nvim_context = NvimContext.from_dict(payload.nvim_context)

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

        async def edit_confirmation_callback(edit_blocks: str, yolo_mode: bool) -> dict:
            """Request edit confirmation via the plugin.

            Sends edit blocks to the plugin which renders them in the UI,
            waits for user to press 1 (apply) or 2 (reject), applies the
            edit if approved, and returns the result.

            Uses a lock to ensure only one edit is shown to the user at a time.
            This prevents multiple edit UIs from appearing simultaneously.
            """
            # Acquire lock BEFORE sending anything to client
            # This ensures only one edit confirmation is active at a time
            async with self._edit_lock:
                confirmation_id = str(uuid.uuid4())

                # Send edit confirmation request to plugin
                await self._send_stream_chunk(
                    session_id,
                    request_id,
                    StreamEventType.EDIT_CONFIRMATION_REQUEST,
                    {
                        "confirmation_id": confirmation_id,
                        "edit_blocks": edit_blocks,
                        "yolo_mode": yolo_mode,
                    },
                )

                # Wait for user response
                try:
                    result = await self.wait_for_confirmation(
                        confirmation_id, timeout=300.0
                    )
                    # Result should be a JSON string with action/success/message
                    # The plugin will have already applied/rejected the edit
                    if isinstance(result, dict):
                        return result
                    elif isinstance(result, str):
                        # Parse JSON if it's a string
                        import json

                        try:
                            return json.loads(result)
                        except json.JSONDecodeError:
                            # Treat as action name
                            return {
                                "action": result,
                                "success": result == "apply",
                                "message": "",
                            }
                    return {
                        "action": "timeout",
                        "success": False,
                        "message": "Invalid response",
                    }
                except Exception as e:
                    self.logger.error(f"Error waiting for edit confirmation: {e}")
                    return {"action": "failed", "success": False, "message": str(e)}

        async def exec_callback(command: str, cwd: str, timeout: int) -> dict:
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
            yolo_mode=nvim_context.yolo_mode,
            confirmation_callback=confirmation_callback,
            edit_confirmation_callback=edit_confirmation_callback,
            exec_callback=exec_callback,
            cwd=nvim_context.cwd,
            current_buffer=nvim_context.current_buffer,
            open_buffers=nvim_context.open_buffers,
        )

        # Use provided history or empty
        llm_history = payload.history

        # Tracking state
        thinking_started = False
        thinking_finalized = False
        thinking_source = None
        parallel_tools = []
        parallel_skip_tools = []
        pending_tool_outputs = []
        expected_outputs = 0
        tool_was_called = False
        in_anya_marker = False
        needs_blank_before_text = False
        last_output_was_marker = True
        last_output_was_tool = False

        # Run the agent
        result = Runner.run_streamed(
            starting_agent=agent,
            input=llm_history,
            context=context,
            max_turns=1000,
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
                    if not thinking_started:
                        thinking_started = True
                        await self._send_stream_chunk(
                            session_id,
                            request_id,
                            StreamEventType.THINKING_START,
                            {},
                        )
                        last_output_was_marker = True
                        last_output_was_tool = False

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
                    last_output_was_marker = True
                    needs_blank_before_text = False

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
                            last_output_was_marker = True
                            last_output_was_tool = False
                            needs_blank_before_text = False
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
                                status = (
                                    "edit_pending"
                                    if tool_name == "edit"
                                    else "tool_pending"
                                )
                                parallel_tools.append(
                                    {
                                        "name": tool_name,
                                        "args": tool_args,
                                        "status": status,
                                    }
                                )
                                expected_outputs += 1

                                # Send tool call start event
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
                                        "skip_header": tool_name == "edit",
                                    },
                                )
                                last_output_was_marker = True
                                last_output_was_tool = True

                    # Handle tool outputs
                    elif item_type == "tool_call_output_item":
                        tool_output = getattr(item, "output", "")
                        pending_tool_outputs.append(tool_output)

                        if (
                            len(pending_tool_outputs) >= expected_outputs
                            and expected_outputs > 0
                        ):
                            is_edit_tool = any(
                                t["name"] == "edit" for t in parallel_tools
                            )
                            has_failure = any(
                                o.strip().startswith("Error:")
                                for o in pending_tool_outputs
                                if isinstance(o, str)
                            )

                            all_outputs = "\n".join(
                                o for o in pending_tool_outputs if o
                            )

                            # Send tool call end event
                            await self._send_stream_chunk(
                                session_id,
                                request_id,
                                StreamEventType.TOOL_CALL_END,
                                {
                                    "tools": parallel_tools + parallel_skip_tools,
                                    "output": all_outputs,
                                    "has_failure": has_failure,
                                    "is_edit_tool": is_edit_tool,
                                    "skip_output": bool(parallel_skip_tools)
                                    and not parallel_tools,
                                },
                            )

                            pending_tool_outputs = []
                            expected_outputs = 0
                            tool_was_called = False
                            parallel_tools = []
                            parallel_skip_tools = []
                            needs_blank_before_text = True
                            last_output_was_marker = True

                # Handle text deltas
                if hasattr(event, "data") and isinstance(
                    event.data, ResponseTextDeltaEvent
                ):
                    delta = event.data.delta
                    if delta:
                        # Filter anya markers
                        delta, in_anya_marker = utils.filter_anya_markers(
                            delta, in_anya_marker
                        )
                        if not delta:
                            continue

                        # Send text delta
                        await self._send_stream_chunk(
                            session_id,
                            request_id,
                            StreamEventType.TEXT_DELTA,
                            {
                                "text": delta,
                                "needs_blank_before": needs_blank_before_text,
                            },
                        )
                        needs_blank_before_text = False
                        last_output_was_marker = False
                        last_output_was_tool = False

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
