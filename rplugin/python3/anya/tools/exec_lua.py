import asyncio
import json
import os
import tempfile
from agents import function_tool, RunContextWrapper

from ..agents.context import NvimPluginContext
from .utils import create_error_handler


@function_tool(failure_error_function=create_error_handler)
async def exec_lua(ctx: RunContextWrapper[NvimPluginContext], code: str) -> str:
    """Execute Lua code inside Neovim.

    Note: This tool requires direct Neovim access and will not work in daemon mode.

    Args:
        code: Lua code to execute

    Returns:
        Result of Lua execution or error message
    """
    # exec_lua requires direct nvim access
    if not ctx.context.has_nvim:
        raise Exception(
            "exec_lua requires direct Neovim access. "
            "This tool is not available in daemon mode. "
            "Consider using exec() for shell commands instead, which can work in daemon mode "
            "with YOLO mode enabled (set g:anya_yolo_mode=1)."
        )

    nvim = ctx.context.nvim
    code = code.strip()

    # Use a temp file to communicate results since we can't do sync RPC
    # from the async context (greenlet context issue)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".lua", delete=False) as f:
        temp_lua = f.name
        temp_result = temp_lua + ".result"

        # Write wrapper that captures output and writes to file
        wrapper_code = f"""
local _output = {{}}
local _old_print = print
print = function(...)
    local args = {{...}}
    local parts = {{}}
    for i, v in ipairs(args) do
        parts[i] = tostring(v)
    end
    table.insert(_output, table.concat(parts, "\\t"))
end

local _ok, _result = pcall(function()
    {code}
end)

print = _old_print

local result = {{
    output = table.concat(_output, "\\n"),
    ok = _ok,
}}
if _ok then
    if _result ~= nil then
        result.value = _result
    end
else
    result.error = tostring(_result)
end

-- Write result to file as JSON
local json_result = vim.fn.json_encode(result)
local file = io.open("{temp_result}", "w")
if file then
    file:write(json_result)
    file:close()
end
"""
        f.write(wrapper_code)

    lua_done = asyncio.Event()

    def run_lua():
        try:
            nvim.command(f"luafile {temp_lua}")
        except Exception as _e:
            pass
        finally:
            # Set the event from the main thread
            asyncio.get_event_loop().call_soon_threadsafe(lua_done.set)

    nvim.async_call(run_lua)

    # Wait for result file to be created with timeout
    start_time = asyncio.get_event_loop().time()
    timeout = 5.0
    while not os.path.exists(temp_result):
        if asyncio.get_event_loop().time() - start_time > timeout:
            try:
                os.unlink(temp_lua)
            except Exception:
                pass
            raise Exception("Lua execution timed out")
        await asyncio.sleep(0.01)

    # Read result from temp file
    try:
        try:
            os.unlink(temp_lua)
        except Exception:
            pass

        if os.path.exists(temp_result):
            with open(temp_result, "r") as f:
                result = json.load(f)
            os.unlink(temp_result)

            parts = []
            if result.get("output"):
                parts.append(result["output"])

            if result.get("ok"):
                if "value" in result:
                    val = result["value"]
                    if val is not None:
                        try:
                            parts.append(f"=> {json.dumps(val, indent=2, default=str)}")
                        except (TypeError, ValueError):
                            parts.append(f"=> {repr(val)}")
            else:
                parts.append(f"Error: {result.get('error', 'unknown error')}")

            result = "\n".join(parts) if parts else "(no output)"
            return f"\n{result}\n"
        else:
            raise Exception("No result file created")
    except Exception as e:
        raise Exception(f"Reading result: {type(e).__name__}: {e}")
