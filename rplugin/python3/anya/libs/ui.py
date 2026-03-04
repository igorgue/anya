"""Interactive prompts for asking the user questions from within execute.

Uses vim.ui.select and vim.ui.input, routed through a temp-file rendezvous
that the plugin monitors while the subprocess is running.  Works in both
daemon mode (exec_callback path) and direct Neovim mode.

Usage:
    from anya.libs import ui

    # Pick one option from a list
    choice = ui.ask("Which database should I use?", ["PostgreSQL", "SQLite", "MySQL"])

    # Free-form text input
    name = ui.input("What should I name the function?", default="my_func")

    # Simple yes/no
    if ui.confirm("Overwrite existing file?"):
        ...
"""

import json
import os
import time
import uuid


def _ui_dir() -> str | None:
    """Return the UI rendezvous directory injected by execute, or None."""
    return os.environ.get("ANYA_UI_DIR")


def _send_request(kind: str, payload: dict, timeout: float = 300.0) -> str:
    """Write a UI request file and block until the plugin writes a response.

    The request file is named  <id>.request.json  and the plugin writes
    the result to  <id>.response.json  in the same directory.

    Args:
        kind: "select" or "input"
        payload: Dict with fields appropriate for the kind.
        timeout: Seconds to wait before raising TimeoutError.

    Returns:
        The user's answer as a string.

    Raises:
        RuntimeError: If not running inside an execute call (no ANYA_UI_DIR).
        TimeoutError: If the user doesn't respond within *timeout* seconds.
    """
    ui_dir = _ui_dir()
    if not ui_dir:
        raise RuntimeError(
            "anya.libs.ui is only available inside execute() calls. "
            "ANYA_UI_DIR is not set."
        )

    os.makedirs(ui_dir, exist_ok=True)
    request_id = str(uuid.uuid4())
    request_file = os.path.join(ui_dir, f"{request_id}.request.json")
    response_file = os.path.join(ui_dir, f"{request_id}.response.json")

    request_data = {"id": request_id, "kind": kind, **payload}
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

    # Clean up stale request on timeout
    try:
        os.unlink(request_file)
    except OSError:
        pass
    raise TimeoutError(f"UI request timed out after {timeout}s")


def ask(prompt: str, options: list[str], timeout: float = 300.0) -> str:
    """Ask the user to pick one option from a list using vim.ui.select.

    Blocks until the user selects an option or dismisses the dialog.
    Returns "Cancel" if the dialog is dismissed without a selection.

    Args:
        prompt: Question or instruction shown to the user.
        options: List of choices to present (minimum 1).
        timeout: Seconds to wait for a response (default 300).

    Returns:
        The selected option string, or "Cancel" if dismissed.

    Example:
        db = ui.ask("Which database?", ["PostgreSQL", "SQLite", "MySQL"])
        if db == "Cancel":
            print("User cancelled")
        else:
            print(f"User picked: {db}")
    """
    if not options:
        raise ValueError("options must not be empty")
    return _send_request("select", {"prompt": prompt, "options": options}, timeout)


def input(prompt: str, default: str = "", timeout: float = 300.0) -> str:
    """Ask the user to type a value using vim.ui.input.

    Blocks until the user submits or cancels the input dialog.
    Returns an empty string if the user cancels.

    Args:
        prompt: Label shown next to the input field.
        default: Pre-filled value (default: empty string).
        timeout: Seconds to wait for a response (default 300).

    Returns:
        The text the user entered, or "" if cancelled.

    Example:
        name = ui.input("Function name:", default="my_func")
        if not name:
            print("User cancelled")
        else:
            print(f"User entered: {name}")
    """
    return _send_request("input", {"prompt": prompt, "default": default}, timeout)


def confirm(prompt: str, timeout: float = 300.0) -> bool:
    """Ask the user a yes/no question using vim.ui.select.

    Args:
        prompt: The yes/no question to show.
        timeout: Seconds to wait for a response (default 300).

    Returns:
        True if the user selected "Yes", False otherwise.

    Example:
        if ui.confirm("Overwrite the existing file?"):
            fs.write_file("out.py", new_content)
    """
    result = ask(prompt, ["Yes", "No"], timeout)
    return result == "Yes"
