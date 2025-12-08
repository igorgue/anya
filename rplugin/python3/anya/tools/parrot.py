import time
from agents import function_tool


@function_tool
async def parrot(message: str) -> str:
    """Respond back the message in with all uppercase letters.
    **only** respond with the message in uppercase.

    Args:
        message (str): The message to be parroted.

    Returns:
        str: The input message converted to uppercase.
    """
    time.sleep(3)  # Simulate processing delay
    return message.upper()
