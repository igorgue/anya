import os
import asyncio
import json
import tempfile


async def exec_lua(code: str, nvim=None, logger=None) -> str:
    """Execute Lua code inside Neovim.

    Args:
        code: Lua code to execute
        nvim: Neovim instance for executing commands
        logger: Logger instance for error logging

    Returns:
        Result of Lua execution or error message
    """
    if nvim is None:
        return "Error: Neovim instance not available"

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
        except Exception as e:
            if logger:
                logger.error(f"exec_lua error: {e}")
        finally:
            # Set the event from the main thread
            asyncio.get_event_loop().call_soon_threadsafe(lua_done.set)

    nvim.async_call(run_lua)

    # Wait for completion with async timeout
    try:
        await asyncio.wait_for(lua_done.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        try:
            os.unlink(temp_lua)
        except:
            pass
        return "Error: Lua execution timed out"

    # Read result from temp file
    try:
        os.unlink(temp_lua)

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
                    try:
                        parts.append(f"=> {json.dumps(val, indent=2, default=str)}")
                    except (TypeError, ValueError):
                        parts.append(f"=> {repr(val)}")
            else:
                parts.append(f"Error: {result.get('error', 'unknown error')}")

            return "\n".join(parts) if parts else "nil"
        else:
            return "Error: No result file created"
    except Exception as e:
        return f"Error reading result: {type(e).__name__}: {e}"
