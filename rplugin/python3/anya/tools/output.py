"""Tool output detection and formatting."""



def format_tool_header(tool_name: str, first_arg: str, max_len: int = 30) -> str:
    """Format tool header with trimmed argument.

    Args:
        tool_name: Tool function name
        first_arg: First argument to tool
        max_len: Max length before trimming

    Returns:
        **tool_name** or **tool_name: trimmed_arg**
    """
    # If no arguments, just show the tool name
    if not first_arg or first_arg == "(no args)":
        return f"**{tool_name}**"

    if len(first_arg) > max_len:
        trimmed = first_arg[: max_len - 3] + "..."
    else:
        trimmed = first_arg

    return f"**{tool_name}: {trimmed}**"
