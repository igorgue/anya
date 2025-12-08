from agents import Agent

from .context import NvimPluginContext
from .utils import get_instructions
from ..tools import buffer_name, create, parrot

code = Agent[NvimPluginContext](
    name="Code",
    instructions=get_instructions("code.md"),
    tools=[buffer_name, create, parrot],
)

__all__ = ["code", "NvimPluginContext"]
