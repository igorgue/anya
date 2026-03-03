from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class NvimPluginContext:
    """Context for tool execution.

    When running in daemon mode, nvim will be None and tools must operate
    without direct Neovim access. The cwd and other context is passed via
    the daemon protocol.
    """

    nvim: Any | None  # pynvim.Nvim or None in daemon mode
    session_id: str
    allowed_commands: set[str] = field(default_factory=set)
    # Additional context for daemon mode
    cwd: str = ""
    current_buffer: str = ""
    current_buffer_content: str = ""  # Content of the current buffer
    open_buffers: list[dict] = field(default_factory=list)
    # Confirmation callback for requesting user confirmation in daemon mode (exec tool)
    confirmation_callback: Callable[[str, list[str]], Awaitable[str]] | None = None
    # Exec callback for running commands on the client machine in daemon mode
    # Takes (command: str, cwd: str, timeout: int) and returns dict with stdout/stderr/returncode
    exec_callback: Callable[[str, str, int], Awaitable[dict[str, Any]]] | None = (
        None  # (command, cwd, timeout, ui_dir=None)
    )
    # Background exec callback for running long commands without blocking
    background_exec_callback: Callable[[str, str, str, str], Awaitable[str]] | None = None
    # Modify buffer callback for modifying Neovim buffers in daemon mode
    # Takes (path: str, content: str, mode: str) and returns success message or error
    modify_buffer_callback: Callable[[str, str, str], Awaitable[str]] | None = None

    @property
    def has_nvim(self) -> bool:
        """Check if nvim is available for direct API calls."""
        return self.nvim is not None
