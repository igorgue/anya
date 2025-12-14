"""Daemon lifecycle management.

Functions to start, stop, and check the status of the Anya daemon.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .client import AnyaClient, get_data_dir


logger = logging.getLogger("anya.daemon")


def get_pid_file() -> Path:
    """Get the PID file path."""
    return get_data_dir() / "daemon.pid"


def get_socket_path() -> Path:
    """Get the IPC socket file path."""
    return get_data_dir() / "daemon.sock"


def get_stream_socket_path() -> Path:
    """Get the streaming socket file path."""
    return get_data_dir() / "daemon_stream.sock"


def get_log_file() -> Path:
    """Get the log file path."""
    return get_data_dir() / "daemon.log"


def read_pid_file() -> int | None:
    """Read the PID from the PID file.

    Returns:
        PID if file exists and is valid, None otherwise
    """
    pid_file = get_pid_file()
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text().strip())
        return pid
    except (ValueError, IOError):
        return None


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running.

    Args:
        pid: Process ID to check

    Returns:
        True if process is running
    """
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_daemon_running() -> bool:
    """Check if the daemon is running.

    First checks PID file, then tries to ping the daemon.

    Returns:
        True if daemon is running and responsive
    """
    pid = read_pid_file()
    if pid is None:
        return False

    if not is_process_running(pid):
        # PID file exists but process is not running - clean up
        cleanup_stale_files()
        return False

    # Try to ping the daemon using a quick ZeroMQ check
    # Don't use the full client to avoid potential hangs
    import zmq

    context = zmq.Context()
    socket = None
    try:
        socket = context.socket(zmq.REQ)
        socket.setsockopt(zmq.RCVTIMEO, 1000)  # 1 second timeout
        socket.setsockopt(zmq.SNDTIMEO, 1000)
        socket.setsockopt(zmq.LINGER, 0)

        socket_path = f"ipc://{get_socket_path()}"
        socket.connect(socket_path)

        # Send a simple ping
        from .protocol import Request, RequestType, ResponseType

        request = Request(
            type=RequestType.PING,
            session_id="ping",
            request_id="ping",
            payload={},
        )
        socket.send(request.serialize())
        response_data = socket.recv()

        from .protocol import Response

        response = Response.deserialize(response_data)
        return response.type == ResponseType.SUCCESS
    except Exception:
        return False
    finally:
        if socket:
            socket.close(linger=0)
        context.term()


def cleanup_stale_files():
    """Clean up stale PID and socket files."""
    files_to_remove = [
        get_pid_file(),
        get_socket_path(),
        get_stream_socket_path(),
    ]

    for f in files_to_remove:
        try:
            if f.exists():
                f.unlink()
        except Exception as e:
            logger.warning(f"Failed to remove {f}: {e}")


def start_daemon(foreground: bool = False, debug: bool = False) -> bool:
    """Start the Anya daemon.

    Args:
        foreground: If True, run in foreground (for debugging)
        debug: Enable debug logging

    Returns:
        True if daemon started successfully
    """
    if is_daemon_running():
        logger.info("Daemon is already running")
        return True

    # Clean up any stale files
    cleanup_stale_files()

    # Ensure data directory exists
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Build command to start daemon
    # Use the server module directly
    python_executable = sys.executable
    server_module = "anya.server.main"

    cmd = [python_executable, "-m", server_module]
    if foreground:
        cmd.append("--foreground")
    if debug:
        cmd.append("--debug")

    # Set up environment with PYTHONPATH including the rplugin directory
    # This is needed because the daemon runs as a subprocess and needs
    # to find the anya package
    env = os.environ.copy()
    rplugin_path = Path(__file__).parent.parent.resolve()
    existing_pythonpath = env.get("PYTHONPATH", "")
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{rplugin_path}:{existing_pythonpath}"
    else:
        env["PYTHONPATH"] = str(rplugin_path)

    logger.info(f"Starting daemon with command: {' '.join(cmd)}")
    logger.info(f"PYTHONPATH: {env['PYTHONPATH']}")

    try:
        if foreground:
            # Run in foreground (blocking)
            subprocess.run(cmd, check=True, env=env)
            return True
        else:
            # Start daemon in background
            # Redirect output to log file
            log_file = get_log_file()

            with open(log_file, "a") as log:
                process = subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=log,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                )

            # Wait a moment for daemon to start
            time.sleep(1.0)

            # Check if daemon started successfully
            for i in range(10):  # Try for 5 seconds
                # First check if process died
                if process.poll() is not None:
                    # Read any error output from log
                    try:
                        with open(log_file, "r") as f:
                            last_lines = f.readlines()[-20:]
                            logger.error(
                                f"Daemon exited with code {process.returncode}. "
                                f"Last log lines: {''.join(last_lines)}"
                            )
                    except Exception:
                        logger.error(f"Daemon exited with code {process.returncode}")
                    return False

                # Try to ping
                if is_daemon_running():
                    logger.info("Daemon started successfully")
                    return True
                time.sleep(0.5)

            logger.warning("Daemon process started but not responding to ping")
            return False

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to start daemon: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to start daemon: {e}")
        return False


def stop_daemon(timeout: float = 5.0) -> bool:
    """Stop the Anya daemon.

    First tries graceful shutdown via IPC, then falls back to SIGTERM.

    Args:
        timeout: Timeout for graceful shutdown

    Returns:
        True if daemon stopped successfully
    """
    pid = read_pid_file()

    if pid is None:
        logger.info("No daemon PID file found")
        cleanup_stale_files()
        return True

    if not is_process_running(pid):
        logger.info("Daemon process not running, cleaning up files")
        cleanup_stale_files()
        return True

    # Try graceful shutdown via IPC
    client = AnyaClient()
    try:
        if client.ping(timeout=1.0):
            logger.info("Sending shutdown request to daemon")
            client.shutdown_daemon()

            # Wait for daemon to stop
            start_time = time.time()
            while time.time() - start_time < timeout:
                if not is_process_running(pid):
                    logger.info("Daemon stopped gracefully")
                    cleanup_stale_files()
                    return True
                time.sleep(0.2)
    except Exception as e:
        logger.warning(f"Failed to send shutdown request: {e}")
    finally:
        client.disconnect()

    # Fall back to SIGTERM
    logger.info("Sending SIGTERM to daemon")
    try:
        os.kill(pid, signal.SIGTERM)

        # Wait for process to exit
        start_time = time.time()
        while time.time() - start_time < timeout:
            if not is_process_running(pid):
                logger.info("Daemon stopped via SIGTERM")
                cleanup_stale_files()
                return True
            time.sleep(0.2)

        # Last resort: SIGKILL
        logger.warning("Daemon did not stop, sending SIGKILL")
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)

        if not is_process_running(pid):
            logger.info("Daemon killed")
            cleanup_stale_files()
            return True

    except ProcessLookupError:
        logger.info("Daemon process already stopped")
        cleanup_stale_files()
        return True
    except Exception as e:
        logger.error(f"Failed to stop daemon: {e}")

    return False


def restart_daemon(foreground: bool = False, debug: bool = False) -> bool:
    """Restart the Anya daemon.

    Args:
        foreground: If True, run in foreground
        debug: Enable debug logging

    Returns:
        True if daemon restarted successfully
    """
    stop_daemon()
    return start_daemon(foreground=foreground, debug=debug)


def get_daemon_status() -> dict:
    """Get detailed daemon status.

    Returns:
        Status dict with pid, running, and daemon info
    """
    pid = read_pid_file()
    process_running = pid is not None and is_process_running(pid)

    status = {
        "pid": pid,
        "pid_file": str(get_pid_file()),
        "process_running": process_running,
        "socket_exists": get_socket_path().exists(),
        "stream_socket_exists": get_stream_socket_path().exists(),
        "daemon_responsive": False,
        "daemon_info": None,
    }

    if process_running:
        client = AnyaClient()
        try:
            if client.ping(timeout=2.0):
                status["daemon_responsive"] = True
                status["daemon_info"] = client.get_status()
        except Exception:
            pass
        finally:
            client.disconnect()

    return status


def ensure_daemon_running() -> bool:
    """Ensure the daemon is running, starting it if necessary.

    Returns:
        True if daemon is running
    """
    if is_daemon_running():
        return True
    return start_daemon()
