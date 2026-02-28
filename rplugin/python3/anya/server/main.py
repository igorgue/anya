"""Main daemon server implementation.

Runs as a standalone process, handling agent operations via ZeroMQ IPC.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import zmq
import zmq.asyncio

# Disable OpenAI tracing globally - prevents hitting their tracing API servers
# This must be done before any agent operations
from agents import set_tracing_disabled
set_tracing_disabled(True)

from ..protocol import (
    Request,
    RequestType,
    Response,
    ResponseType,
    StreamChunk,
    StreamEventType,
    make_error_response,
    make_success_response,
)
from .agents import AgentManager
from .handlers import RequestHandler

# Configure logging
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


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


def get_pid_file() -> Path:
    """Get the PID file path."""
    return get_data_dir() / "daemon.pid"


def get_log_file() -> Path:
    """Get the log file path."""
    return get_data_dir() / "daemon.log"


class AnyaDaemon:
    """Main daemon server class."""

    def __init__(self):
        self.logger = logging.getLogger("anya.daemon")
        self.context: zmq.asyncio.Context | None = None
        self.rep_socket: zmq.asyncio.Socket | None = None
        self.pub_socket: zmq.asyncio.Socket | None = None
        self.agent_manager: AgentManager | None = None
        self.handler: RequestHandler | None = None
        self.running = False
        self._shutdown_event = asyncio.Event()

    async def start(self):
        """Start the daemon server."""
        self.logger.info("Starting Anya daemon...")

        # Ensure data directory exists
        data_dir = get_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        # Write PID file
        pid_file = get_pid_file()
        pid_file.write_text(str(os.getpid()))
        self.logger.info(f"PID file written: {pid_file}")

        # Initialize ZeroMQ context
        self.context = zmq.asyncio.Context()

        # Create REP socket for request/response
        self.rep_socket = self.context.socket(zmq.REP)
        socket_path = get_socket_path()
        self.rep_socket.bind(socket_path)
        self.logger.info(f"REP socket bound to: {socket_path}")

        # Create PUB socket for streaming
        self.pub_socket = self.context.socket(zmq.PUB)
        stream_path = get_stream_socket_path()
        self.pub_socket.bind(stream_path)
        self.logger.info(f"PUB socket bound to: {stream_path}")

        # Initialize agent manager with PUB socket for status events
        self.agent_manager = AgentManager()
        await self.agent_manager.initialize(pub_socket=self.pub_socket)

        # Initialize request handler
        self.handler = RequestHandler(
            agent_manager=self.agent_manager,
            pub_socket=self.pub_socket,
        )

        self.running = True
        self.logger.info("Anya daemon started successfully")

        # Run main loop
        await self._main_loop()

    async def _main_loop(self):
        """Main request handling loop.

        ZeroMQ REP sockets require strict recv-send alternation.
        We must send a response before we can receive the next request.
        Handlers that need async processing (like SEND_MESSAGE) return
        immediately and continue processing in background tasks.
        """
        self.logger.info("Entering main loop...")

        while self.running:
            try:
                # Receive with timeout to allow checking shutdown
                try:
                    message = await asyncio.wait_for(
                        self.rep_socket.recv(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # No message, check shutdown flag and continue
                    continue

                # Handle request and send response immediately
                # This is required by ZeroMQ REP socket semantics
                try:
                    response = await self._handle_request(message)
                    await self.rep_socket.send(response.serialize())
                except Exception as e:
                    self.logger.exception(f"Error handling request: {e}")
                    # Send error response to maintain REP socket state
                    error_response = make_error_response("unknown", str(e))
                    await self.rep_socket.send(error_response.serialize())

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.exception(f"Error in main loop: {e}")
                await asyncio.sleep(0.1)

        self.logger.info("Main loop exited")

    async def _handle_request(self, message: bytes) -> Response:
        """Handle an incoming request."""
        try:
            request = Request.deserialize(message)
            self.logger.info(
                f"Received request: {request.type.value} from session {request.session_id}"
            )

            # Handle built-in requests
            if request.type == RequestType.PING:
                return make_success_response(request.request_id, {"pong": True})

            if request.type == RequestType.SHUTDOWN:
                self.logger.info("Shutdown request received")
                self.running = False
                self._shutdown_event.set()
                return make_success_response(request.request_id)

            if request.type == RequestType.GET_STATUS:
                status = await self.agent_manager.get_status()
                return make_success_response(request.request_id, status)

            if request.type == RequestType.END_SESSION:
                await self.agent_manager.end_session(request.session_id)
                return make_success_response(request.request_id)

            # Delegate to handler for agent operations
            return await self.handler.handle(request)

        except Exception as e:
            self.logger.exception(f"Error handling request: {e}")
            return make_error_response("unknown", str(e))

    async def stop(self):
        """Stop the daemon server."""
        self.logger.info("Stopping Anya daemon...")
        self.running = False

        # Clean up agent manager
        if self.agent_manager:
            await self.agent_manager.shutdown()

        # Close sockets
        if self.rep_socket:
            self.rep_socket.close()
        if self.pub_socket:
            self.pub_socket.close()

        # Terminate context
        if self.context:
            self.context.term()

        # Remove socket files
        try:
            socket_file = get_data_dir() / "daemon.sock"
            if socket_file.exists():
                socket_file.unlink()
            stream_file = get_data_dir() / "daemon_stream.sock"
            if stream_file.exists():
                stream_file.unlink()
        except Exception as e:
            self.logger.warning(f"Error removing socket files: {e}")

        # Remove PID file
        try:
            pid_file = get_pid_file()
            if pid_file.exists():
                pid_file.unlink()
        except Exception as e:
            self.logger.warning(f"Error removing PID file: {e}")

        self.logger.info("Anya daemon stopped")


def setup_logging(debug: bool = False, foreground: bool = True):
    """Set up logging configuration.

    Args:
        debug: Enable debug level logging
        foreground: If True, also log to stderr. If False (daemon mode),
                    only log to file since stderr is redirected to the log file.
    """
    level = logging.DEBUG if debug else logging.INFO

    # Ensure log directory exists
    log_file = get_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Get root logger and clear any existing handlers to avoid duplicates
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.setLevel(level)

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT)

    # Add file handler - always
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root_logger.addHandler(file_handler)

    # Add stderr handler only in foreground mode
    # In daemon mode, stderr is already redirected to the log file by the parent
    # process, so adding a stderr handler would cause duplicate logs
    if foreground:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        stderr_handler.setLevel(level)
        root_logger.addHandler(stderr_handler)


def daemonize():
    """Fork and detach from terminal to become a daemon."""
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent exits
        sys.exit(0)

    # Create new session
    os.setsid()

    # Second fork
    pid = os.fork()
    if pid > 0:
        # First child exits
        sys.exit(0)

    # Redirect standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()

    # Redirect stdin to /dev/null
    with open("/dev/null", "r") as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())

    # Note: We keep stdout/stderr for logging


async def run_daemon(daemon: AnyaDaemon):
    """Run the daemon with signal handling."""
    loop = asyncio.get_event_loop()

    # Set up signal handlers
    def signal_handler(signum):
        daemon.logger.info(f"Received signal {signum}")
        asyncio.create_task(daemon.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    try:
        await daemon.start()
    except Exception as e:
        daemon.logger.exception(f"Daemon error: {e}")
    finally:
        await daemon.stop()


def main(foreground: bool = False, debug: bool = False):
    """Main entry point for the daemon."""
    setup_logging(debug=debug, foreground=foreground)

    if not foreground:
        daemonize()

    daemon = AnyaDaemon()
    asyncio.run(run_daemon(daemon))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Anya Daemon Server")
    parser.add_argument(
        "-f", "--foreground", action="store_true", help="Run in foreground"
    )
    parser.add_argument(
        "-d", "--debug", action="store_true", help="Enable debug logging"
    )
    args = parser.parse_args()

    main(foreground=args.foreground, debug=args.debug)
