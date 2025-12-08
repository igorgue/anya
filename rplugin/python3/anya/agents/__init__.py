from agents import Agent

from .context import NvimPluginContext
from .utils import get_instructions
from ..tools import (
    buffer_name,
    create,
    exec_lua,
    gh,
    list_files,
    parrot,
    read_file,
    read_many_files,
    search,
)

code = Agent[NvimPluginContext](
    name="Code",
    instructions=get_instructions("code.md"),
    tools=[
        buffer_name,
        create,
        exec_lua,
        gh,
        list_files,
        parrot,
        read_file,
        read_many_files,
        search,
    ],
)

__all__ = ["code", "NvimPluginContext"]
