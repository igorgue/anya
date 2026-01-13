from agents import function_tool
import datetime
import hashlib
import json
import uuid
from typing import Optional, List
from pydantic import BaseModel


class MemoryFragment(BaseModel):
    """Memory fragment structure for storage."""
    text: str
    category: str = "other"
    source: str = "user"


@function_tool
async def store_memory(memory: MemoryFragment) -> str:
    """Store a memory for future recall.
    
    Use this to remember important facts, preferences, or details about the user or project.
    
    Args:
        memory: Memory with text, category (personal/skill/project/task), and source (user/assistant)
    
    Returns:
        Confirmation message
    """
    now_utc = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    
    text = memory.text.strip()
    cat = memory.category.lower().strip()
    source = memory.source.strip() if memory.source in ['user', 'assistant'] else 'user'
    
    # Generate deduplication key
    dedup = hashlib.sha256((text + '|' + cat + '|' + source).encode()).hexdigest()[:24]
    
    memory_dict = {
        'id': str(uuid.uuid4()),
        'text': text,
        'category': cat,
        'source': source,
        'timestamp': now_utc,
        'deduplication_key': dedup,
    }
    
    try:
        from ..db import save_memory as db_save_memory
        db_save_memory(memory_dict)
        return f"Stored: {text[:30]}..."
    except Exception as e:
        return f"Failed to store memory: {e}"

store_memory.skip_output = True


@function_tool
async def recall_memories(query: str = "", category: str = "") -> str:
    """Search and recall stored memories.
    
    Use this to look up previously stored information about the user or project.
    
    Args:
        query: Optional text to search for in memories
        category: Optional category filter (personal, skill, project, task)
    
    Returns:
        List of matching memories or message if none found
    """
    from ..db import search_memories
    
    memories = search_memories(
        query=query if query else None,
        category=category if category else None,
        limit=10
    )
    
    if not memories:
        return "No memories found"
    
    results = []
    for mem in memories:
        results.append(f"- [{mem['category']}] {mem['text']}")
    
    return "\n".join(results)

recall_memories.skip_output = True


@function_tool
async def extract_memories(user_message: str, assistant_response: str) -> str:
    """Extract potential memories from a conversation exchange.
    
    Analyzes the conversation to identify important information worth remembering.
    Returns a JSON list of memory candidates - use store_memory to save them.
    
    Args:
        user_message: The user's message/prompt
        assistant_response: Your response to the user
    
    Returns:
        JSON list of memory candidates with text, category, and source fields
    """
    from agents import Agent, Runner
    import os
    
    MEMORY_EXTRACTION_PROMPT = """
You are a Memory Extraction Agent. Extract concise, useful, and non-trivial memories from the conversation. For each memory, provide:
- text: The actual memory (fact, preference, project detail, etc)
- category: One of: personal, skill, project, task
- source: 'user' or 'assistant' (who provided this memory)
Avoid trivial, duplicate, or irrelevant information. Return ONLY a valid JSON list of memory objects.
"""
    
    extraction_prompt = (
        f"User message: {user_message}\n"
        f"Assistant response: {assistant_response}\n"
        "\nExtract memories from BOTH inputs as a JSON list; each with text, category, source ('user' or 'assistant'). "
        "Do not include trivial or duplicate info. Output ONLY the JSON list."
    )
    
    model_name = os.environ.get("ANYA_MODEL", "gpt-4.1-mini").strip()
    agent = Agent(
        name="MemoryExtractor",
        instructions=MEMORY_EXTRACTION_PROMPT,
        model=model_name,
    )
    
    try:
        result = await Runner.run(agent, extraction_prompt)
        return result.final_output.strip()
    except Exception as e:
        return f"Failed to extract memories: {e}"

extract_memories.skip_output = True
