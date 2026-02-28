"""System prompt placeholder expansion and appended environment context.

This mirrors the placeholder substitution used in Neovim Lua configs, but runs
inside Anya's Python remote plugin.

Supported placeholders:
- {OS}
- {KERNEL}
- {NEOVIM}
- {DE}
- {NVIDIA_VERSION_INFO}
- {CWD}
- {CURRENT_DATE}

In addition to substituting placeholders when present, we also append:
1. A small "System context" block to the end of every agent's instructions
2. The contents of AGENTS.md from the current working directory (if it exists)
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from .utils import nvim_call_sync

T = TypeVar("T")


@dataclass(frozen=True)
class SystemPromptContext:
    os: str
    kernel: str
    neovim: str
    de: str
    nvidia_version_info: str
    cwd: str
    current_date: str


def _read_os_pretty_name() -> str | None:
    # Prefer /etc/os-release (common on Linux)
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("PRETTY_NAME="):
                    value = line.split("=", 1)[1].strip().strip('"')
                    return value
    except Exception:
        return None
    return None


def _run_command(argv: list[str], timeout_s: float = 1.0) -> str | None:
    """Run a command and return stdout, or None on failure."""
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except Exception:
        return None

    out = (proc.stdout or "").strip()
    if not out:
        return None
    return out


def get_os_info() -> str:
    pretty = _read_os_pretty_name()
    if pretty:
        return pretty

    # Fallback: platform string
    try:
        return platform.platform()
    except Exception:
        return "Unknown"


def get_kernel_info() -> str:
    try:
        return platform.release() or "Unknown"
    except Exception:
        return "Unknown"


def _nvim_call_sync_safe(nvim: Any, func: Callable[[], T]) -> T:
    """Call into Neovim safely from background threads.

    pynvim rejects RPC requests from non-main threads. For background threads we
    schedule onto the main thread with nvim.async_call and wait for the result.

    If we're already on the main Python thread, call directly to avoid deadlocks.
    """
    if threading.current_thread() is threading.main_thread():
        return func()

    # Delegate to the shared helper used by tools.
    return nvim_call_sync(nvim, func)


def _get_neovim_version_from_nvim(nvim: Any) -> str | None:
    """Try to obtain Neovim version via embedded Lua.

    Returns a string like: "0.10.1" (or similar), or None if unavailable.
    """

    def _get() -> dict[str, Any] | None:
        return nvim.exec_lua(
            """
            local v = vim.version()
            -- v has fields: major, minor, patch
            return {major = v.major, minor = v.minor, patch = v.patch}
            """
        )

    try:
        ver = _nvim_call_sync_safe(nvim, _get)
        if isinstance(ver, dict):
            major = ver.get("major")
            minor = ver.get("minor")
            patch = ver.get("patch")
            if major is not None and minor is not None and patch is not None:
                return f"{major}.{minor}.{patch}"
    except Exception:
        return None

    return None


def get_neovim_info(nvim: Any | None = None) -> str:
    if nvim is not None:
        v = _get_neovim_version_from_nvim(nvim)
        if v:
            return v

    # Fallback: call `nvim --version` (first line is usually enough)
    out = _run_command(["nvim", "--version"], timeout_s=1.0)
    if out:
        first = out.splitlines()[0].strip()
        return first

    return "Unknown"


def get_de_info() -> str:
    # Desktop environment/session hints (best-effort)
    for key in ("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "GDMSESSION"):
        val = os.environ.get(key)
        if val:
            return val
    return "Unknown"


def get_nvidia_info() -> str:
    # Best-effort: if nvidia-smi exists, ask for driver version.
    out = _run_command(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        timeout_s=1.0,
    )
    if out:
        # If multiple GPUs, pick first line.
        return out.splitlines()[0].strip()

    return "N/A"


def get_cwd(nvim: Any | None = None, cwd: str | None = None) -> str:
    if cwd:
        return cwd
    if nvim is not None:
        try:
            return str(_nvim_call_sync_safe(nvim, lambda: nvim.call("getcwd")))
        except Exception:
            pass
    try:
        return os.getcwd()
    except Exception:
        return "Unknown"


def get_current_date_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def read_agent_md_from_cwd(
    nvim: Any | None = None, cwd: str | None = None
) -> str | None:
    """Read AGENTS.md from the current working directory if it exists.

    Returns the file contents as a string, or None if the file doesn't exist.
    Uses explicit cwd if provided, then Neovim's working directory (vim.cwd),
    otherwise falls back to os.getcwd().
    """
    # Use explicit cwd first, then Neovim, then os.getcwd()
    if not cwd and nvim is not None:
        try:
            cwd = str(_nvim_call_sync_safe(nvim, lambda: nvim.call("getcwd")))
        except Exception:
            pass

    if not cwd:
        try:
            cwd = os.getcwd()
        except Exception:
            return None

    agent_md_path = os.path.join(cwd, "AGENTS.md")

    if not os.path.isfile(agent_md_path):
        return None

    try:
        with open(agent_md_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return content if content else None
    except Exception:
        return None


def build_system_prompt_context(
    nvim: Any | None = None, cwd: str | None = None
) -> SystemPromptContext:
    return SystemPromptContext(
        os=get_os_info(),
        kernel=get_kernel_info(),
        neovim=get_neovim_info(nvim),
        de=get_de_info(),
        nvidia_version_info=get_nvidia_info(),
        cwd=get_cwd(nvim, cwd=cwd),
        current_date=get_current_date_utc(),
    )


def expand_placeholders(
    template: str, nvim: Any | None = None, cwd: str | None = None
) -> str:
    ctx = build_system_prompt_context(nvim, cwd=cwd)

    # Mirror the Lua :gsub chain with simple replacements.
    # Use exact placeholder spellings to avoid unintended substitutions.
    return (
        template.replace("{OS}", ctx.os)
        .replace("{KERNEL}", ctx.kernel)
        .replace("{NEOVIM}", ctx.neovim)
        .replace("{DE}", ctx.de)
        .replace("{NVIDIA_VERSION_INFO}", ctx.nvidia_version_info)
        .replace("{CWD}", ctx.cwd)
        .replace("{CURRENT_DATE}", ctx.current_date)
    )


def system_context_block(nvim: Any | None = None, cwd: str | None = None) -> str:
    ctx = build_system_prompt_context(nvim, cwd=cwd)
    # Keep this compact and purely informational.
    return "\n".join(
        [
            "",  # leading newline
            "---",
            "System context (auto-appended):",
            f"Current date (UTC): {ctx.current_date}",
            f"OS: {ctx.os}",
            f"Kernel: {ctx.kernel}",
            f"Neovim: {ctx.neovim}",
            f"DE: {ctx.de}",
            f"NVIDIA_VERSION_INFO: {ctx.nvidia_version_info}",
            f"CWD: {ctx.cwd}",
        ]
    )


def apply_system_prompt(
    template: str, nvim: Any | None = None, cwd: str | None = None
) -> str:
    """Expand known placeholders, then append the system context block and AGENTS.md if present."""
    expanded = expand_placeholders(template, nvim, cwd=cwd)

    # Ensure we append at the end of the instructions.
    # Avoid adding excessive blank lines.
    if not expanded.endswith("\n"):
        expanded += "\n"

    result = expanded.rstrip() + system_context_block(nvim, cwd=cwd) + "\n"

    # Append AGENTS.md from current working directory if it exists
    agent_md_content = read_agent_md_from_cwd(nvim, cwd=cwd)
    if agent_md_content:
        result += "\n---\n"
        result += agent_md_content
        result += "\n"

    return result
