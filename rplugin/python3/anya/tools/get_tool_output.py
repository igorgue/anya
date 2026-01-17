"""Tool for LLM to retrieve stored tool outputs."""

from agents import function_tool

from .. import db


@function_tool
def get_tool_output(output_id: str) -> str:
    """Retrieve the full content of a previously stored tool output.

    Use this when you need to re-read a tool output that was referenced
    earlier in the conversation (shown as [Tool output #id: N lines from tool_name]).

    Args:
        output_id: The tool output ID (e.g., "fa234uf")

    Returns:
        The full tool output content, or an error message if not found.
    """
    output = db.get_tool_output(output_id)
    if not output:
        return f"Error: Tool output '{output_id}' not found"

    return output["content"]


# Don't display output in chat (LLM reads it directly)
get_tool_output.skip_output = True
