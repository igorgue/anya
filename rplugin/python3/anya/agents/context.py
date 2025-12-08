from dataclasses import dataclass, field
from pynvim import Nvim


@dataclass
class NvimPluginContext:
    nvim: Nvim
    session_id: str
    allowed_commands: set[str] = field(default_factory=set)
