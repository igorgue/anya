#!/usr/bin/env python3
"""Test script to verify handoff tools are properly attached."""

import asyncio
import sys
import os

# Add venv to path
venv_path = os.path.expanduser("~/.local/share/agent.nvim/venv/lib")
if os.path.exists(venv_path):
    for py_dir in os.listdir(venv_path):
        py_path = os.path.join(venv_path, py_dir, "site-packages")
        if os.path.isdir(py_path):
            sys.path.insert(0, py_path)

try:
    from agents import Agent, function_tool, Runner, handoff
    from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

# Simple test tool
@function_tool
def test_tool() -> str:
    """A test tool to verify tools are accessible."""
    return "Tool was called successfully!"

async def test_handoff_with_tools():
    """Test if tools are accessible in a handoff scenario."""
    
    # Create code agent with a tool
    code_agent = Agent(
        name="Code Agent",
        instructions="You are a coding assistant. You have access to tools.",
        tools=[test_tool],
    )
    
    # Create auto agent with handoff to code agent
    auto_agent = Agent(
        name="Auto Agent",
        instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
You are a router. Hand off to the code agent for coding tasks.""",
        handoffs=[
            handoff(
                agent=code_agent,
                tool_name_override="transfer_to_code",
                tool_description_override="Hand off to the code agent.",
            )
        ],
    )
    
    print("=" * 60)
    print("Agent configuration:")
    print(f"  Auto Agent has {len(auto_agent.tools) if auto_agent.tools else 0} tools")
    print(f"  Code Agent has {len(code_agent.tools) if code_agent.tools else 0} tools")
    
    if code_agent.tools:
        for tool in code_agent.tools:
            tool_name = getattr(tool, 'name', str(tool))
            print(f"    - {tool_name}")
    
    print("\nRunning test: ask agent to call tool after handoff...")
    print("=" * 60)
    
    result_stream = Runner.run_streamed(
        auto_agent,
        input=[
            {
                "role": "user",
                "content": "Hand off to code agent and call the test_tool to verify it works.",
            }
        ],
    )
    
    tool_called = False
    async for event in result_stream.stream_events():
        event_type = type(event).__name__
        
        if event_type == "RunItemStreamEvent" and hasattr(event, "item"):
            item_type = type(event.item).__name__
            if "ToolCall" in item_type:
                tool_called = True
                tool_name = getattr(event.item, 'name', 'unknown')
                print(f"\n✓ Tool called: {tool_name}")
            elif "AgentUpdated" in item_type or event_type == "AgentUpdatedStreamEvent":
                agent_name = getattr(event.item, 'name', 'unknown')
                print(f"✓ Agent updated: {agent_name}")
    
    print("\n" + "=" * 60)
    if tool_called:
        print("✓ SUCCESS: Tool was called after handoff!")
    else:
        print("✗ FAILURE: Tool was NOT called after handoff")
        print("  This indicates tools are not being carried through the handoff.")
    print("=" * 60)

if __name__ == "__main__":
    os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")
    asyncio.run(test_handoff_with_tools())
