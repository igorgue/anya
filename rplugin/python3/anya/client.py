"""ZeroMQ client for communicating with the Anya daemon.

Handles request/response and streaming subscription.
"""

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Any

import zmq
import zmq.asyncio
import cbor2

from .protocol import (
    Request,
    RequestType,
    Response,
    ResponseType,
    StreamChunk,
    StreamEventType,
    SendMessagePayload,
    CancelRequestPayload,
    NvimContext,
)


def get_data_dir() -> Path:
    """Get the data directory for daemon files."""
    data_dir = os.environ.get("XDG_DATA_HOME")
    if data_dir:
        return Path(data_dir) / "anya"
    return Path.home() / ".local" / "share" / "anya"


def get_socket_path() -> str:
    """Get the IPC socket path for REQ/REP communication."""
    return f"ipc://{get_data_dir() / 'daemon.sock'}"


def get_stream_socket_path() -> str:
    """Get the IPC socket path for PUB/SUB streaming."""
    return f"ipc://{get_data_dir() / 'daemon_stream.sock'}"


class AnyaClient:
    """Client for communicating with the Anya daemon."""

    def __init__(self):
        self.logger = logging.getLogger("anya.client")
        self._context: zmq.Context | None = None
        self._req_socket: zmq.Socket | None = None
        self._connected = False
        self._lock = threading.RLock()  # RLock allows re-entrant locking

    def _ensure_context(self):
        """Ensure ZeroMQ context is initialized."""
        if self._context is None:
            self._context = zmq.Context()

    def connect(self, timeout: float = 5.0) -> bool:
        """Connect to the daemon.

        Args:
            timeout: Connection timeout in seconds

        Returns:
            True if connected successfully
        """
        with self._lock:
            if self._connected:
                return True

            try:
                self._ensure_context()

                # Create REQ socket
                self._req_socket = self._context.socket(zmq.REQ)
                self._req_socket.setsockopt(zmq.RCVTIMEO, int(timeout * 1000))
                self._req_socket.setsockopt(zmq.SNDTIMEO, int(timeout * 1000))
                self._req_socket.setsockopt(zmq.LINGER, 0)

                socket_path = get_socket_path()
                self._req_socket.connect(socket_path)

                self._connected = True
                return True

            except Exception:
                self._cleanup_socket()
                return False

    def disconnect(self):
        """Disconnect from the daemon."""
        with self._lock:
            self._cleanup_socket()
            if self._context:
                self._context.term()
                self._context = None
            self._connected = False

    def _cleanup_socket(self):
        """Clean up the request socket."""
        if self._req_socket:
            try:
                self._req_socket.close()
            except Exception:
                pass
            self._req_socket = None

    def is_connected(self) -> bool:
        """Check if connected to the daemon."""
        return self._connected

    def ping(self, timeout: float = 2.0) -> bool:
        """Ping the daemon to check if it's alive.

        Args:
            timeout: Timeout in seconds

        Returns:
            True if daemon responds
        """
        try:
            response = self.send_request(
                RequestType.PING,
                session_id="ping",
                request_id="ping",
                payload={},
                timeout=timeout,
            )
            return response is not None and response.type == ResponseType.SUCCESS
        except Exception:
            return False

    def send_request(
        self,
        request_type: RequestType,
        session_id: str,
        request_id: str,
        payload: dict,
        timeout: float = 30.0,
    ) -> Response | None:
        """Send a request to the daemon.

        Args:
            request_type: Type of request
            session_id: Session ID
            request_id: Request ID
            payload: Request payload
            timeout: Timeout in seconds

        Returns:
            Response from daemon or None if failed
        """
        with self._lock:
            if not self._connected or not self._req_socket:
                if not self.connect(timeout=timeout):
                    return None

            try:
                # Set timeout for this request
                self._req_socket.setsockopt(zmq.RCVTIMEO, int(timeout * 1000))

                # Create and send request
                request = Request(
                    type=request_type,
                    session_id=session_id,
                    request_id=request_id,
                    payload=payload,
                )
                self._req_socket.send(request.serialize())

                # Wait for response
                response_data = self._req_socket.recv()
                return Response.deserialize(response_data)

            except zmq.Again:
                # Timeout - reset socket
                self._reset_socket()
                return None
            except Exception:
                self._reset_socket()
                return None

    def _reset_socket(self):
        """Reset the socket after an error."""
        self._cleanup_socket()
        self._connected = False

    def send_message(
        self,
        session_id: str,
        request_id: str,
        text: str,
        conversation_id: str | None,
        history: list[dict],
        nvim_context: NvimContext,
        timeout: float = 300.0,
    ) -> Response | None:
        """Send a message to the agent.

        Args:
            session_id: Session ID
            request_id: Request ID
            text: Message text
            conversation_id: Conversation ID
            history: LLM history
            nvim_context: Neovim context
            timeout: Timeout in seconds

        Returns:
            Response from daemon
        """
        payload = SendMessagePayload(
            text=text,
            conversation_id=conversation_id,
            history=history,
            nvim_context=nvim_context.to_dict(),
        )
        return self.send_request(
            RequestType.SEND_MESSAGE,
            session_id=session_id,
            request_id=request_id,
            payload=payload.to_dict(),
            timeout=timeout,
        )

    def cancel_request(
        self,
        session_id: str,
        target_request_id: str,
    ) -> Response | None:
        """Cancel an active request.

        Args:
            session_id: Session ID
            target_request_id: ID of request to cancel

        Returns:
            Response from daemon
        """
        payload = CancelRequestPayload(target_request_id=target_request_id)
        return self.send_request(
            RequestType.CANCEL_REQUEST,
            session_id=session_id,
            request_id=f"cancel_{target_request_id}",
            payload=payload.to_dict(),
            timeout=5.0,
        )

    def end_session(self, session_id: str) -> Response | None:
        """End a session and clean up resources.

        Args:
            session_id: Session ID to end

        Returns:
            Response from daemon
        """
        return self.send_request(
            RequestType.END_SESSION,
            session_id=session_id,
            request_id=f"end_{session_id}",
            payload={},
            timeout=5.0,
        )

    def get_status(self) -> dict | None:
        """Get daemon status.

        Returns:
            Status dict or None if failed
        """
        response = self.send_request(
            RequestType.GET_STATUS,
            session_id="status",
            request_id="status",
            payload={},
            timeout=5.0,
        )
        if response and response.type == ResponseType.SUCCESS:
            return response.payload
        return None

    def shutdown_daemon(self) -> bool:
        """Request daemon shutdown.

        Returns:
            True if shutdown request was acknowledged
        """
        response = self.send_request(
            RequestType.SHUTDOWN,
            session_id="shutdown",
            request_id="shutdown",
            payload={},
            timeout=5.0,
        )
        return response is not None and response.type == ResponseType.SUCCESS


class StreamSubscriber:
    """Subscribes to streaming events from the daemon."""

    def __init__(self, session_id: str, request_id: str):
        self.logger = logging.getLogger("anya.client.stream")
        self.session_id = session_id
        self.request_id = request_id
        self._context: zmq.asyncio.Context | None = None
        self._sub_socket: zmq.asyncio.Socket | None = None
        self._running = False

    async def connect(self):
        """Connect to the streaming socket."""
        self._context = zmq.asyncio.Context()
        self._sub_socket = self._context.socket(zmq.SUB)

        # Subscribe to events for this session:request
        topic = f"{self.session_id}:{self.request_id}".encode()
        self._sub_socket.setsockopt(zmq.SUBSCRIBE, topic)

        stream_path = get_stream_socket_path()
        self._sub_socket.connect(stream_path)
        self._running = True
        self.logger.debug(f"Subscribed to stream at {stream_path} with topic {topic}")

    async def disconnect(self):
        """Disconnect from the streaming socket."""
        self._running = False
        if self._sub_socket:
            self._sub_socket.close()
            self._sub_socket = None
        if self._context:
            self._context.term()
            self._context = None

    async def receive(self, timeout: float = 1.0) -> StreamChunk | None:
        """Receive a streaming chunk.

        Args:
            timeout: Timeout in seconds

        Returns:
            StreamChunk or None if timeout
        """
        if not self._sub_socket:
            return None

        try:
            data = await asyncio.wait_for(
                self._sub_socket.recv(),
                timeout=timeout,
            )
            return StreamChunk.deserialize(data)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            self.logger.error(f"Error receiving stream: {e}")
        return None

    async def stream(self) -> "AsyncIterator[StreamChunk]":
        """Async iterator over streaming chunks.

        Yields:
            StreamChunk events until MESSAGE_END is received
        """
        while self._running:
            chunk = await self.receive(timeout=1.0)
            if chunk:
                yield chunk
                if chunk.event_type == StreamEventType.MESSAGE_END:
                    break


class AsyncAnyaClient:
    """Async client for communicating with the Anya daemon."""

    def __init__(self):
        self.logger = logging.getLogger("anya.client.async")
        self._context: zmq.asyncio.Context | None = None
        self._req_socket: zmq.asyncio.Socket | None = None
        self._connected = False
        self._lock = asyncio.Lock()

    async def connect(self, timeout: float = 5.0) -> bool:
        """Connect to the daemon."""
        async with self._lock:
            if self._connected:
                return True

            try:
                self._context = zmq.asyncio.Context()
                self._req_socket = self._context.socket(zmq.REQ)
                self._req_socket.setsockopt(zmq.RCVTIMEO, int(timeout * 1000))
                self._req_socket.setsockopt(zmq.SNDTIMEO, int(timeout * 1000))
                self._req_socket.setsockopt(zmq.LINGER, 0)

                socket_path = get_socket_path()
                self._req_socket.connect(socket_path)

                self._connected = True
                self.logger.debug(f"Connected to daemon at {socket_path}")
                return True

            except Exception as e:
                self.logger.error(f"Failed to connect to daemon: {e}")
                await self._cleanup()
                return False

    async def disconnect(self):
        """Disconnect from the daemon."""
        async with self._lock:
            await self._cleanup()
            self._connected = False

    async def _cleanup(self):
        """Clean up resources."""
        if self._req_socket:
            self._req_socket.close()
            self._req_socket = None
        if self._context:
            self._context.term()
            self._context = None

    async def send_request(
        self,
        request_type: RequestType,
        session_id: str,
        request_id: str,
        payload: dict,
        timeout: float = 30.0,
    ) -> Response | None:
        """Send a request to the daemon."""
        async with self._lock:
            if not self._connected or not self._req_socket:
                if not await self.connect(timeout=timeout):
                    return None

            try:
                self._req_socket.setsockopt(zmq.RCVTIMEO, int(timeout * 1000))

                request = Request(
                    type=request_type,
                    session_id=session_id,
                    request_id=request_id,
                    payload=payload,
                )
                await self._req_socket.send(request.serialize())
                response_data = await self._req_socket.recv()
                return Response.deserialize(response_data)

            except zmq.Again:
                self.logger.error("Request timed out")
                await self._reset_socket()
                return None
            except Exception as e:
                self.logger.error(f"Request failed: {e}")
                await self._reset_socket()
                return None

    async def _reset_socket(self):
        """Reset socket after error."""
        if self._req_socket:
            self._req_socket.close()
            self._req_socket = None
        self._connected = False

    async def ping(self, timeout: float = 2.0) -> bool:
        """Ping the daemon."""
        try:
            response = await self.send_request(
                RequestType.PING,
                session_id="ping",
                request_id="ping",
                payload={},
                timeout=timeout,
            )
            return response is not None and response.type == ResponseType.SUCCESS
        except Exception:
            return False

    def create_stream_subscriber(
        self,
        session_id: str,
        request_id: str,
    ) -> StreamSubscriber:
        """Create a stream subscriber for a request."""
        return StreamSubscriber(session_id, request_id)
