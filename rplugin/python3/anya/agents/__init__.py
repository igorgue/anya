from agents import Agent

from .context import PluginContext
from .utils import get_instructions
from ..tools.buffer_name import buffer_name

code = Agent[PluginContext](
    name="Code",
    instructions=get_instructions("code.md"),
    tools=[buffer_name],
)

__all__ = ["code"]
