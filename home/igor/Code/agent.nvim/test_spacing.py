#!/usr/bin/env python3

import os
import asyncio
import sys
sys.path.insert(0, '/home/igor/Code/agent.nvim/rplugin/python3')

# Mock nvim for testing
class MockNvim:
    def __init__(self):
        self.content_buffer = []
        self.calls = []
    
    def async_call(self, func, *args):
        if hasattr(func, '__call__'):
            # It's a lambda, execute it
            func(*args)
        else:
            # It's a method name with args
            self.calls.append((func.__name__ if hasattr(func, '__name__') else func, args))
    
    def api(self):
        return self

def _append_content(self, lines):
    """Mock append content method"""
    self.content_buffer.extend(lines)
    print(f"Appending: {lines}")

def _remove_last_line(self):
    """Mock remove last line method"""
    if len(self.content_buffer) > 0:
        removed = self.content_buffer.pop()
        print(f"Removed: '{removed}'")

async def test_glm_response():
    """Test what happens with a GLM-like response"""
    
    # Create mock nvim
    nvim = MockNvim()
    
    # Add methods to the mock
    type(nvim).async_call = nvim.async_call
    nvim.api = MockNvim()
    
    # Create a simple agent plugin instance for testing
    class TestAgent:
        def __init__(self, nvim):
            self.nvim = nvim
            self.content_buffer = []
            self.logger = type('obj', (object,), {'info': print})()
            self._append_content = _append_content.__get__(self, TestAgent)
            self._remove_last_line = _remove_last_line.__get__(self, TestAgent)
    
    agent = TestAgent(nvim)
    
    # Test the agent header addition
    print("=== Testing Agent Header Addition ===")
    header_lines = ["", "## Agent (glm-4.6)", ""]
    agent._append_content(header_lines)
    print(f"Buffer after header: {agent.content_buffer}")
    
    # Test different first chunk scenarios
    test_chunks = [
        ("Hello! I'm here to help.", "No leading newlines"),
        ("\nHello! I'm here to help.", "One leading newline"),
        ("\n\nHello! I'm here to help.", "Two leading newlines"),
        ("   \n\nHello! I'm here to help.", "Leading spaces + newlines"),
    ]
    
    for chunk, description in test_chunks:
        print(f"\n=== Testing: {description} ===")
        print(f"Chunk: {repr(chunk)}")
        
        # Reset for each test
        agent.content_buffer = []
        agent._append_content(header_lines)
        
        # Reset the response started flag
        if hasattr(agent, '_agent_response_started'):
            delattr(agent, '_agent_response_started')
        
        # Simulate the _append_stream_lua_direct logic
        if not hasattr(agent, '_agent_response_started'):
            agent._agent_response_started = True
            print(f"Chunk starts with newline: {chunk.startswith('\\n')}")
            
            if chunk.startswith('\n'):
                leading_newlines = len(chunk) - len(chunk.lstrip('\n'))
                print(f"Model wants {leading_newlines} leading newlines")
                if leading_newlines > 0:
                    agent._remove_last_line()
            else:
                agent._append_content([""])
        
        print(f"Final buffer: {agent.content_buffer}")

if __name__ == "__main__":
    asyncio.run(test_glm_response())