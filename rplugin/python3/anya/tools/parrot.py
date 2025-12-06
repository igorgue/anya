from agents import function_tool


@function_tool
async def parrot(message: str) -> str:
    """Get the name of the current buffer."""
    return message.upper()
