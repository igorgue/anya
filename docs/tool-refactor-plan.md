# Tool Refactor Plan: Using @function_tool Decorator

## Goal

Allow end users to create custom tools using the OpenAI Agents SDK `@function_tool` decorator directly, instead of the current closure-based wrapper pattern in `plugin.py`.

## Current Architecture

Tools are defined as closures inside `_get_tool_wrappers()` in `plugin.py`:

```python
def _get_tool_wrappers(self, tool_budget):
    async def read_file(path: str, timeout: int = 30) -> str:
        # Captures self, tool_budget, cached_cwd via closure
        cwd = getattr(self, "_cached_cwd", None)
        result = await asyncio.to_thread(tools.read_file, path, cwd)
        tool_budget.consume(result)
        return result
    
    return {"read_file": function_tool(read_file), ...}
```

This works but is confusing for users who want to add custom tools.

## Desired Architecture

Tools use `@function_tool` decorator with `RunContextWrapper` for dependency injection:

```python
# tools/read_file.py
from agents import function_tool, RunContextWrapper
from agent_nvim.context import AgentContext

@function_tool
async def read_file(ctx: RunContextWrapper[AgentContext], path: str) -> str:
    """Tool description for LLM."""
    cwd = ctx.context.cwd
    result = await asyncio.to_thread(_read_file_impl, path, cwd)
    ctx.context.tool_budget.consume(result)
    return result
```

## What Was Attempted

1. Created `context.py` with `AgentContext` dataclass holding: `cwd`, `tool_budget`, `nvim`, `buffer_manager`, `logger`

2. Refactored `tools/read_file.py` to use `@function_tool` with `RunContextWrapper[AgentContext]`

3. Updated `plugin.py` to create `AgentContext` and pass to `run_agent()`

4. Updated `agent_runner.py` to pass `context=agent_context` to `Runner.run_streamed()`

5. Updated agent classes to use `Agent[AgentContext]` typing

## Why It Failed

The LLM stopped recognizing the built-in tools entirely. Possible causes:

1. **Context type mismatch**: When using `RunContextWrapper[T]`, the Agent must be typed as `Agent[T]` and the context must match exactly. Mixed tools (some with context, some without) may cause issues.

2. **Import timing**: The `@function_tool` decorator runs at import time, resolving type hints. Circular imports or missing types could cause silent failures.

3. **Tool filtering**: The SDK may filter out tools that don't match the agent's context type.

4. **Schema issues**: The decorated tool's schema might have been malformed.

## Correct Approach (Untested)

1. **All or nothing**: Either ALL tools use `RunContextWrapper` or NONE do. Don't mix patterns.

2. **Test incrementally**: Convert ONE tool, verify it works, then continue.

3. **Check tool registration**: Add logging to verify tools appear in `agent.tools` before running.

4. **Verify schema**: Print `tool.params_json_schema` to ensure `ctx` is excluded.

5. **Keep closure tools for internals**: Maybe only expose `@function_tool` pattern for USER-defined tools, not built-in ones.

## Files That Need Changes

- `rplugin/python3/agent_nvim/context.py` (create)
- `rplugin/python3/agent_nvim/tools/*.py` (refactor each tool)
- `rplugin/python3/agent_nvim/tools/__init__.py` (update exports)
- `rplugin/python3/agent_nvim/plugin.py` (create context, simplify `_get_tool_wrappers`)
- `rplugin/python3/agent_nvim/agent_runner.py` (pass context to Runner)
- `rplugin/python3/agent_nvim/agents/*.py` (type agents with context)

## OpenAI Agents SDK Reference

```python
from agents import Agent, Runner, function_tool, RunContextWrapper
from dataclasses import dataclass

@dataclass
class MyContext:
    user_id: str

@function_tool
async def my_tool(ctx: RunContextWrapper[MyContext], arg: str) -> str:
    """Description for LLM."""
    return f"User {ctx.context.user_id} said {arg}"

agent = Agent[MyContext](name="Assistant", tools=[my_tool])
result = await Runner.run(agent, "prompt", context=MyContext(user_id="123"))
```

## Resources

- OpenAI Agents SDK docs: https://openai.github.io/openai-agents-python/
- Context docs: https://github.com/openai/openai-agents-python/blob/main/docs/context.md
