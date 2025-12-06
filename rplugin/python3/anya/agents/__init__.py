from agents import Agent

from .context import NvimPluginContext
from .utils import get_instructions
from ..tools.buffer_name import buffer_name

code = Agent[NvimPluginContext](
    name="Code",
    instructions=get_instructions("code.md"),
    tools=[buffer_name],
)

__all__ = ["code", "NvimPluginContext"]
