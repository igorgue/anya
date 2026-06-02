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
    make_error_response,
    make_success_response,
)
from .agents import AgentManager
from .handlers import RequestHandler
from .telegram_client import TelegramClient

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
        self.telegram_client: TelegramClient | None = None
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

        # Start Telegram client if configured
        self.telegram_client = await self._start_telegram_client()
        if self.handler is not None:
            self.handler.telegram_client = self.telegram_client

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
                return make_success_response(request.request_id, {"detached": True})

            # Delegate to handler for agent operations
            return await self.handler.handle(request)

        except Exception as e:
            self.logger.exception(f"Error handling request: {e}")
            return make_error_response("unknown", str(e))

    async def _start_telegram_client(self) -> TelegramClient | None:
        """Start the Telegram Router client if configured."""
        router_url = os.environ.get("ANYA_ROUTER_URL")
        if not router_url:
            return None

        self.logger.info(f"Starting Telegram client, connecting to {router_url}")

        telegram_conversations: dict[int, str] = {}

        def current_conversation_id(chat_id: int) -> str:
            conversation_id = telegram_conversations.get(chat_id)
            if conversation_id is None:
                conversation_id = f"telegram:{chat_id}"
                telegram_conversations[chat_id] = conversation_id
            return conversation_id

        async def on_new_conversation(chat_id: int):
            """Start a fresh persisted Telegram conversation for this chat."""
            import time
            from .. import db

            timestamp_ms = int(time.time() * 1000)
            conversation_id = f"telegram:{chat_id}:{timestamp_ms}"
            created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            db.save_conversation(conversation_id, created_at, cwd=os.getcwd())
            try:
                db.update_conversation_title(conversation_id, f"Telegram {chat_id} #{timestamp_ms}")
            except Exception:
                pass
            telegram_conversations[chat_id] = conversation_id
            self.logger.info(
                "Started new Telegram conversation for chat %s: %s",
                chat_id,
                conversation_id,
            )

        async def on_message(chat_id: int, text: str):
            """Handle an incoming Telegram message as its own persisted conversation."""
            if not self.handler:
                return

            import time
            from .. import db
            from ..protocol import Request, RequestType

            conversation_id = current_conversation_id(chat_id)
            session_id = conversation_id
            timestamp_ms = int(time.time() * 1000)
            request_id = f"tg-assistant-{chat_id}-{timestamp_ms}"
            user_message_id = f"tg-user-{chat_id}-{timestamp_ms}"

            nvim_ctx = {
                "session_id": session_id,
                "cwd": os.getcwd(),
                "current_buffer": "",
                "current_buffer_content": "",
                "open_buffers": [],
                "allowed_commands": [],
                "agent_settings": {},
                "request_kind": "telegram",
            }

            created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if not db.get_conversation(conversation_id):
                db.save_conversation(conversation_id, created_at, cwd=os.getcwd())
                try:
                    db.update_conversation_title(conversation_id, f"Telegram {chat_id}")
                except Exception:
                    pass

            db.save_message_dict(
                msg_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=text,
                author="Telegram",
                created_at=created_at,
            )
            db.update_conversation_timestamp(conversation_id, created_at)

            history_rows = db.get_messages(conversation_id)[-40:]
            history = [
                {"role": row["role"], "content": row["content"]}
                for row in history_rows
                if row.get("role") in {"user", "assistant"} and row.get("content")
            ]

            telegram_client = self.telegram_client

            async def telegram_callback(response_text: str):
                if telegram_client:
                    await telegram_client.send_response(chat_id, response_text)

            self.handler._telegram_response_callbacks[request_id] = telegram_callback

            request = Request(
                type=RequestType.SEND_MESSAGE,
                session_id=session_id,
                request_id=request_id,
                payload={
                    "text": text,
                    "conversation_id": conversation_id,
                    "history": history,
                    "nvim_context": nvim_ctx,
                },
            )

            await self.handler.handle(request)
            self.logger.info(
                "Telegram message queued for chat %s in conversation %s (req %s)",
                chat_id,
                conversation_id,
                request_id[:12],
            )

        client = TelegramClient(
            router_url=router_url,
            on_message=on_message,
            on_new_conversation=on_new_conversation,
        )
        await client.start()
        return client

    async def stop(self):
        """Stop the daemon server."""
        self.logger.info("Stopping Anya daemon...")
        self.running = False

        # Stop Telegram client
        if self.telegram_client:
            await self.telegram_client.stop()

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
