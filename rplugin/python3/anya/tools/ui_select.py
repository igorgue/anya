"""UI Select tool for user interaction."""

from agents import function_tool, RunContextWrapper
from ..agents.context import NvimPluginContext
from typing import List
import asyncio


async def _nvim_ui_select(nvim, options: list, prompt: str) -> str:
    """Ask user to select from options using vim.ui.select."""
    # Format options for Lua table
    lua_options = "{" + ", ".join(f'"{opt}"' for opt in options) + "}"
    lua_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")

    result = [None]

    def run_select():
        nvim.exec_lua(
            f"""
vim.g.anya_select_result = nil
vim.ui.select({lua_options},
    {{prompt = "{lua_prompt}"}},
    function(selection)
        vim.g.anya_select_result = selection or "Cancel"
    end)
"""
        )

    # Wrap the Neovim calls with async_call
    nvim.async_call(run_select)

    # Poll for the result with async sleep
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 300.0:

        def get_result():
            try:
                val = nvim.eval("get(g:, 'anya_select_result', v:null)")
                if val is not None and val != "v:null" and val != "null":
                    result[0] = str(val)
            except Exception:
                pass

        nvim.async_call(get_result)
        await asyncio.sleep(0.1)

        if result[0] is not None:
            return result[0]

    return "Cancel"


@function_tool
async def ui_select(
    ctx: RunContextWrapper[NvimPluginContext],
    prompt: str,
    options: List[str],
) -> str:
    """Present a selection UI to the user and return their choice.

    This tool is ideal for asking the user questions during code generation
    when you need clarification or to make a decision between multiple options.

    The selected option will be returned to you, allowing you to continue
    your reasoning or take action based on their choice.

    **When to use THIS tool:**
    - Ask the user to choose between multiple implementation options
    - Confirm an action before proceeding (yes/no confirmation)
    - Get clarification on requirements or preferences
    - Let the user select from a list of choices

    **How it works:**
    - The tool shows a vim.ui.select dialog to the user
    - The user picks one of the options
    - The selected option is returned as a string
    - You can then continue with your reasoning using their choice

    **Example:**
    If you ask the user "Which approach do you prefer?" with options:
    - "Use synchronous API"
    - "Use asynchronous API"
    - "Show me both options"

    The user selects "Use asynchronous API" and you receive that string,
    allowing you to proceed with the async implementation.

    Args:
        ctx: Agent context with access to Neovim
        prompt (str): Message/question to present to the user
        options (List[str]): List of options for user to select from (minimum 2)

    Returns:
        str: The selected option (the user's choice)

    Example usage:
        choice = await ui_select(
            ctx=ctx,
            prompt="Which approach would you like?",
            options=["Option A: Simple", "Option B: Advanced", "Cancel"]
        )
        # choice will be one of the provided options
    """
    plugin_context = ctx.context

    if not options or len(options) < 1:
        return "No options provided"

    # Direct Neovim mode - use UI directly
    if plugin_context.has_nvim:
        nvim = plugin_context.nvim
        return await _nvim_ui_select(nvim, options, prompt)

    # Daemon mode - use confirmation_callback
    if plugin_context.confirmation_callback:
        return await plugin_context.confirmation_callback(prompt, options)

    # No way to interact with user
    raise Exception(
        "ui_select requires either direct Neovim access or "
        "daemon mode with confirmation_callback. Neither is available."
    )
