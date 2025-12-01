def edit(edit_blocks: str) -> str:
    """Propose code edits using SEARCH/REPLACE blocks.

    Use this tool to make precise code modifications. Each edit block specifies:
    - The file path
    - A SEARCH section with the exact code to find
    - A REPLACE section with the new code

    CRITICAL - edit tool behavior:
    - When you call the edit tool, the agent STOPS and waits for user to apply (1) or reject (2) the patch
    - After the user decides, you will receive one of these messages:
    - PATCH_APPLIED: The patch was successfully applied - continue with next steps
    - PATCH_REJECTED: The user rejected - ask what they want changed
    - PATCH_FAILED: The patch could not be applied - re-read the file and regenerate with correct context lines
    - Do NOT use alternative approaches (like exec with sed) - always use the edit tool for code changes
    - EXCEPTION: In YOLO mode (AGENT_YOLO=1), patches are auto-applied without stopping. You will NOT receive PATCH_APPLIED/REJECTED messages - just continue with your work.

    Example:
    ```
    src/utils.py
    <<<<<<< SEARCH
    def calculate_total(items):
        total = 0
        for item in items:
            total += item['price']
        return total
    =======
    def calculate_total(items):
        total = 0
        for item in items:
            total += item['price'] * item['quantity']
        return total
    >>>>>>> REPLACE
    ```

    Rules:
    - The SEARCH section must EXACTLY match existing code (including whitespace and indentation)
    - Include enough context lines to uniquely identify the location
    - Keep blocks small and focused on specific changes
    - Use multiple blocks for multiple changes
    - For new files: use empty SEARCH section
    - The user will review and approve each edit before it's applied

    Args:
        edit_blocks: String containing one or more SEARCH/REPLACE blocks

    Returns:
        The edit blocks content (to be rendered and applied by the UI)
    """
    # Clean up - remove outer markdown code fences if present
    edit_blocks = edit_blocks.strip()
    if edit_blocks.startswith("```") and not edit_blocks.startswith("<<<"):
        lines = edit_blocks.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        edit_blocks = "\n".join(lines)

    # Ensure trailing newline
    if not edit_blocks.endswith("\n"):
        edit_blocks += "\n"

    return edit_blocks
