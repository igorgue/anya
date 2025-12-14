from dataclasses import dataclass, field
from typing import Any


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
    yolo_mode: bool = False
    # Additional context for daemon mode
    cwd: str = ""
    current_buffer: str = ""
    open_buffers: list[dict] = field(default_factory=list)

    @property
    def has_nvim(self) -> bool:
        """Check if nvim is available for direct API calls."""
        return self.nvim is not None
