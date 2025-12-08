from dataclasses import dataclass
from pynvim import Nvim


@dataclass
class NvimPluginContext:
    nvim: Nvim
    session_id: str
