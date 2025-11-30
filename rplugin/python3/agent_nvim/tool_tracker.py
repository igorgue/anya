"""Track tool usage in agent.nvim for conversation history preservation."""

# Global tool tracker to record files read during agent execution
_tool_reads = []


def reset_tool_reads():
    """Reset the list of tool reads for a new request."""
    global _tool_reads
    _tool_reads = []


def record_file_read(path: str, content: str, truncated: bool = False):
    """Record a file read for later addition to conversation history.

    Args:
        path: Path to the file read
        content: Content that was read
        truncated: Whether the content was truncated
    """
    global _tool_reads
    _tool_reads.append(
        {
            "type": "file_read",
            "path": path,
            "content": content,
            "truncated": truncated,
        }
    )


def record_search(query: str, results: str):
    """Record a repository search for later addition to conversation history.

    Args:
        query: Search query
        results: Search results
    """
    global _tool_reads
    _tool_reads.append(
        {
            "type": "search",
            "query": query,
            "results": results,
        }
    )


def record_file_list(path: str, files: str):
    """Record a file listing for later addition to conversation history.

    Args:
        path: Directory path
        files: File listing results
    """
    global _tool_reads
    _tool_reads.append(
        {
            "type": "file_list",
            "path": path,
            "files": files,
        }
    )


def get_tool_reads() -> list:
    """Get list of recorded tool reads."""
    global _tool_reads
    return _tool_reads.copy()


def add_tool_reads_to_history(conversation_history: list) -> bool:
    """Add any recorded tool reads to the conversation history.

    Args:
        conversation_history: The conversation history list to append to

    Returns:
        True if any tool reads were added, False otherwise
    """
    global _tool_reads

    if not _tool_reads:
        return False

    # Create a system message documenting the tool reads
    tool_records = []

    for record in _tool_reads:
        if record["type"] == "file_read":
            tool_records.append(
                f"**File Read**: {record['path']}"
                f"{'(truncated)' if record['truncated'] else ''}\n"
                f"```\n{record['content']}\n```"
            )
        elif record["type"] == "search":
            tool_records.append(
                f"**Repository Search**: {record['query']}\n"
                f"```\n{record['results']}\n```"
            )
        elif record["type"] == "file_list":
            tool_records.append(
                f"**File Listing**: {record['path']}\n```\n{record['files']}\n```"
            )

    if tool_records:
        # Add as a system message to preserve context
        conversation_history.append(
            {
                "role": "system",
                "content": f"[Tool Context]\n\n" + "\n\n".join(tool_records),
            }
        )
        # Clear the tool reads after adding them to history
        _tool_reads = []
        return True

    return False
