"""Modify the current Neovim buffer or another open file buffer.

Uses a temp-file rendezvous pattern (similar to ui.py) that the plugin
monitors while the subprocess is running. Works in both daemon mode
(during normal chat execute() calls and :Anya do) and direct Neovim mode.

Usage:
    from anya.libs import buffer

    # Replace current buffer contents
    buffer.modify("def hello():\n    print('hello')")

    # Replace another open file buffer by path
    buffer.modify_file("lib/app.py", "print('updated')")

    # Inspect what file buffers are currently open
    print(buffer.list_open_buffers())

Note: This lib only works inside execute() calls when buffer context is
available (for example while the agent is running from Neovim).
"""

import json
import os
import time
import uuid


def _ui_dir() -> str | None:
    """Return the UI rendezvous directory injected by execute, or None."""
    return os.environ.get("ANYA_UI_DIR")


def _normalize_path(path: str) -> str:
    """Normalize a file path for matching against open buffer paths."""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def _load_open_buffers() -> list[dict]:
    """Load serialized open buffer metadata from execute() environment."""
    raw = os.environ.get("ANYA_OPEN_BUFFERS", "")
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    return [item for item in data if isinstance(item, dict)]


def _request_modify(
    content: str,
    mode: str = "replace",
    timeout: float = 30.0,
    target_path: str | None = None,
) -> str:
    """Send a modify-buffer request to the plugin and wait for the response."""
    ui_dir = _ui_dir()
    if not ui_dir:
        raise RuntimeError(
            "anya.libs.buffer is only available inside execute() calls. "
            "ANYA_UI_DIR is not set."
        )

    if mode not in ("replace", "append", "prepend"):
        raise ValueError(
            f"Invalid mode: {mode!r}. Use 'replace', 'append', or 'prepend'."
        )

    request_id = str(uuid.uuid4())
    request_file = os.path.join(ui_dir, f"{request_id}.request.json")
    response_file = os.path.join(ui_dir, f"{request_id}.response.json")

    request_data = {
        "id": request_id,
        "kind": "modify_buffer",
        "content": content,
        "mode": mode,
    }
    if target_path:
        request_data["target_path"] = _normalize_path(target_path)

    with open(request_file, "w") as f:
        json.dump(request_data, f)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(response_file):
            with open(response_file) as f:
                response = json.load(f)
            try:
                os.unlink(response_file)
            except OSError:
                pass
            return response.get("result", "")
        time.sleep(0.05)

    try:
        os.unlink(request_file)
    except OSError:
        pass
    raise TimeoutError(f"Buffer modify request timed out after {timeout}s")


def modify(content: str, mode: str = "replace", timeout: float = 30.0) -> str:
    """Modify the current Neovim buffer content."""
    return _request_modify(content=content, mode=mode, timeout=timeout)


def modify_file(
    path: str,
    content: str,
    mode: str = "replace",
    timeout: float = 30.0,
) -> str:
    """Modify an already-open file buffer by path.

    Args:
        path: File path for an open Neovim buffer. Relative paths are resolved
            against the execute() process cwd before matching.
        content: Content to write to the buffer.
        mode: "replace", "append", or "prepend".
        timeout: Seconds to wait before raising TimeoutError.

    Returns:
        Success message or error description.
    """
    return _request_modify(
        content=content,
        mode=mode,
        timeout=timeout,
        target_path=path,
    )


def list_open_buffers() -> list[dict]:
    """Return metadata for file buffers currently open in Neovim."""
    return _load_open_buffers()


def is_open(path: str) -> bool:
    """Return True if the given file path matches an open Neovim buffer."""
    target = _normalize_path(path)
    for buf in _load_open_buffers():
        candidate = buf.get("path") or buf.get("name")
        if not candidate:
            continue
        try:
            if _normalize_path(candidate) == target:
                return True
        except Exception:
            continue
    return False
