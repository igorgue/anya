from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext


@function_tool
async def buffer_name(ctx: RunContextWrapper[NvimPluginContext]) -> str:
    """Get the name of the current buffer."""
    return ctx.context.nvim.current.buffer.name
