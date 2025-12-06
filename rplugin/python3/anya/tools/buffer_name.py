from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from .utils import nvim_call_sync


@function_tool
async def buffer_name(ctx: RunContextWrapper[NvimPluginContext]) -> str:
    """Get the name of the current buffer.

    Returns:
        str: The name of the current buffer.
    """
    return nvim_call_sync(
        ctx.context.nvim, lambda: ctx.context.nvim.current.buffer.name
    )
