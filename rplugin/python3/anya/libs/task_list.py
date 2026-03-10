"""Live task-list updates for long or multi-step work inside execute().

Usage:
    from anya.libs import task_list

    task_list.update(
        title="Implement feature",
        items=[
            {"text": "Inspect code", "status": "done"},
            {"text": "Make changes", "status": "in_progress"},
            {"text": "Verify", "status": "pending"},
        ],
    )
"""

import json
import os
import time
import uuid

_ALLOWED_STATUSES = {"pending", "in_progress", "done"}


def _ui_dir() -> str | None:
    return os.environ.get("ANYA_UI_DIR")


def _normalize_item(item: dict) -> dict:
    if not isinstance(item, dict):
        raise TypeError("task list items must be dicts")
    text = str(item.get("text", "")).strip()
    if not text:
        raise ValueError("task list item text must not be empty")
    status = str(item.get("status", "pending")).strip()
    if status not in _ALLOWED_STATUSES:
        raise ValueError(
            f"invalid task list status: {status!r}. "
            "Use 'pending', 'in_progress', or 'done'."
        )
    return {"text": text, "status": status}


def update(title: str, items: list[dict], timeout: float = 30.0) -> None:
    """Publish a full task-list snapshot to the parent execute process."""
    ui_dir = _ui_dir()
    if not ui_dir:
        raise RuntimeError(
            "anya.libs.task_list is only available inside execute() calls. "
            "ANYA_UI_DIR is not set."
        )

    normalized_title = str(title or "").strip()
    if not normalized_title:
        raise ValueError("task list title must not be empty")
    if not isinstance(items, list) or not items:
        raise ValueError("task list items must be a non-empty list")

    normalized_items = [_normalize_item(item) for item in items]
    os.makedirs(ui_dir, exist_ok=True)

    event_id = str(uuid.uuid4())
    event_file = os.path.join(ui_dir, f"{event_id}.event.json")
    event_data = {
        "id": event_id,
        "kind": "task_list_update",
        "title": normalized_title,
        "items": normalized_items,
    }

    with open(event_file, "w") as f:
        json.dump(event_data, f)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not os.path.exists(event_file):
            return
        time.sleep(0.05)

    raise TimeoutError(f"Task-list update timed out after {timeout}s")
