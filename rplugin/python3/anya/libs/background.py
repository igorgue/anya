"""Background job utilities: list, inspect, and tail background processes.

Usage:
    from anya.libs import background

    # List all jobs in current project
    jobs = background.list_jobs()

    # Get specific job info
    job = background.get_job("abc12345")

    # Tail last N lines of output
    output = background.tail_logs("abc12345", lines=50)

    # Check if job is still running
    running = background.is_running("abc12345")
"""

import json
import os
import tempfile
from datetime import datetime


def _get_background_dir(cwd: str | None = None) -> str:
    """Get the background process directory for a project."""
    base = cwd or os.getcwd()
    return os.path.join(base, ".anya", "background")


def _read_meta_file(meta_path: str) -> dict | None:
    """Read and parse a metadata file, returning None if invalid."""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta_file(meta_path: str, data: dict) -> None:
    """Atomically write a metadata file."""
    data["updated_at"] = datetime.now().isoformat()
    # Write to temp file then rename for atomicity
    fd, temp_path = tempfile.mkstemp(
        dir=os.path.dirname(meta_path), prefix=".tmp_meta_", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, meta_path)
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def list_jobs(cwd: str | None = None) -> list[dict]:
    """List all background jobs in the project.

    Returns a list of job metadata dicts, each containing:
        - process_id: Short unique identifier
        - title: Human-readable job title
        - command: The command being executed
        - cwd: Working directory
        - start_time: ISO timestamp when job started
        - end_time: ISO timestamp when job ended (None if running)
        - status: "running", "completed", "failed", or "error"
        - returncode: Exit code (None if running)

    Args:
        cwd: Project directory (default: os.getcwd()).

    Returns:
        List of job metadata dictionaries.
    """
    bg_dir = _get_background_dir(cwd)
    if not os.path.isdir(bg_dir):
        return []

    jobs = []
    for filename in os.listdir(bg_dir):
        if not filename.endswith(".meta.json"):
            continue
        meta_path = os.path.join(bg_dir, filename)
        meta = _read_meta_file(meta_path)
        if meta:
            jobs.append(meta)

    # Sort by start_time, newest first
    jobs.sort(key=lambda j: j.get("start_time", ""), reverse=True)
    return jobs


def get_job(process_id: str, cwd: str | None = None) -> dict | None:
    """Get metadata for a specific background job.

    Args:
        process_id: The short process identifier.
        cwd: Project directory (default: os.getcwd()).

    Returns:
        Job metadata dict or None if not found.
    """
    bg_dir = _get_background_dir(cwd)
    meta_path = os.path.join(bg_dir, f"{process_id}.meta.json")
    return _read_meta_file(meta_path)


def tail_logs(process_id: str, lines: int = 50, cwd: str | None = None) -> str:
    """Get the last N lines of a background job's output log.

    Args:
        process_id: The short process identifier.
        lines: Number of lines to return (default 50).
        cwd: Project directory (default: os.getcwd()).

    Returns:
        The last N lines of the log file, with a header showing job status.
    """
    bg_dir = _get_background_dir(cwd)
    log_path = os.path.join(bg_dir, f"{process_id}.log")
    meta = get_job(process_id, cwd)

    if not os.path.exists(log_path):
        return f"No log file found for process {process_id}"

    # Read the log file
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    # Get last N lines
    tail_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
    content = "".join(tail_lines).rstrip()

    # Build header with status info
    header_parts = [f"Process: {process_id}"]
    if meta:
        status = meta.get("status", "unknown")
        title = meta.get("title", "untitled")
        header_parts.append(f"Title: {title}")
        header_parts.append(f"Status: {status}")
        if meta.get("end_time"):
            header_parts.append(f"Ended: {meta['end_time']}")
    header_parts.append(f"Showing last {len(tail_lines)} of {len(all_lines)} lines")
    header_parts.append("")

    return "\n".join(header_parts) + content


def read_logs(
    process_id: str, start: int = 0, end: int | None = None, cwd: str | None = None
) -> str:
    """Read a range of lines from a background job's output log.

    Args:
        process_id: The short process identifier.
        start: Starting line number (0-indexed, default 0).
        end: Ending line number (exclusive, None for end of file).
        cwd: Project directory (default: os.getcwd()).

    Returns:
        The requested lines from the log file, with a header.
    """
    bg_dir = _get_background_dir(cwd)
    log_path = os.path.join(bg_dir, f"{process_id}.log")
    meta = get_job(process_id, cwd)

    if not os.path.exists(log_path):
        return f"No log file found for process {process_id}"

    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    total_lines = len(all_lines)
    end = end or total_lines
    selected = all_lines[start:end]
    content = "".join(selected).rstrip()

    header_parts = [f"Process: {process_id}"]
    if meta:
        header_parts.append(f"Title: {meta.get('title', 'untitled')}")
        header_parts.append(f"Status: {meta.get('status', 'unknown')}")
    header_parts.append(f"Lines {start}-{min(end, total_lines)} of {total_lines}")
    header_parts.append("")

    return "\n".join(header_parts) + content


def is_running(process_id: str, cwd: str | None = None) -> bool:
    """Check if a background job is still running.

    Args:
        process_id: The short process identifier.
        cwd: Project directory (default: os.getcwd()).

    Returns:
        True if the job is running, False otherwise.
    """
    meta = get_job(process_id, cwd)
    if not meta:
        return False
    return meta.get("status") == "running"


def stop_job(process_id: str, cwd: str | None = None) -> dict:
    """Stop a running background job by sending SIGTERM.

    Args:
        process_id: The short process identifier.
        cwd: Project directory (default: os.getcwd()).

    Returns:
        Dict with 'success' bool and 'message' string.
    """
    import signal

    meta = get_job(process_id, cwd)
    if not meta:
        return {
            "success": False,
            "message": f"No job found with process_id: {process_id}",
        }

    if meta.get("status") != "running":
        return {
            "success": False,
            "message": f"Job {process_id} is not running (status: {meta.get('status')})",
        }

    pid = meta.get("pid")
    if not pid:
        return {"success": False, "message": f"No PID found for job {process_id}"}

    try:
        os.kill(pid, signal.SIGTERM)
        # Update metadata to reflect we requested stop
        bg_dir = _get_background_dir(cwd)
        meta_path = os.path.join(bg_dir, f"{process_id}.meta.json")
        meta["status"] = "stopping"
        meta["stop_requested_at"] = datetime.now().isoformat()
        _write_meta_file(meta_path, meta)
        return {
            "success": True,
            "message": f"Sent SIGTERM to process {pid} (job {process_id})",
        }
    except ProcessLookupError:
        return {"success": False, "message": f"Process {pid} no longer exists"}
    except PermissionError:
        return {"success": False, "message": f"Permission denied to kill process {pid}"}
    except Exception as e:
        return {"success": False, "message": f"Error stopping job: {e}"}


def wait_for_job(
    process_id: str,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.5,
    cwd: str | None = None,
) -> dict:
    """Wait for a background job to complete, returning its final status.

    Args:
        process_id: The short process identifier.
        timeout_seconds: Maximum time to wait (default 30).
        poll_interval: How often to check status (default 0.5s).
        cwd: Project directory (default: os.getcwd()).

    Returns:
        Final job metadata dict.

    Raises:
        TimeoutError: If the job doesn't complete within timeout.
    """
    import time

    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout_seconds:
        meta = get_job(process_id, cwd)
        if meta and meta.get("status") != "running":
            return meta
        time.sleep(poll_interval)

    raise TimeoutError(
        f"Job {process_id} did not complete within {timeout_seconds} seconds"
    )
