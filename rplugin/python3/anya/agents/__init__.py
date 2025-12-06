from .code_agent import CodeAgent
from .utils import get_instructions

code = CodeAgent(name="Code", instructions=get_instructions("code.md"))

__all__ = ["code"]
