"""Tool budget tracking to prevent context overflow."""

import functools
from .token_tracker import get_context_window


class ToolBudget:
    """Tracks approximate token usage from tool outputs during a request."""
    
    def __init__(self, model: str | None = None, budget_fraction: float = 0.5):
        """Initialize tool budget tracker.
        
        Args:
            model: Model name for context window lookup
            budget_fraction: Fraction of context window to allocate for tool outputs
        """
        context_window = get_context_window(model)
        self.budget = int(context_window * budget_fraction)
        self.tokens_used = 0
        self.budget_exceeded = False
    
    def estimate_tokens(self, text: str) -> int:
        """Estimate token count from text (rough: ~4 chars per token)."""
        return max(1, len(text) // 4)
    
    def can_use_budget(self, heavy_tool: bool = True) -> bool:
        """Check if we have budget remaining.
        
        Args:
            heavy_tool: If True, checks budget for heavy tools (read_file, search_repo)
                       If False, always allows (for lightweight tools like list_files)
        """
        if not heavy_tool:
            return True
        return self.tokens_used < self.budget
    
    def consume(self, text: str) -> None:
        """Track token usage from a tool result."""
        tokens = self.estimate_tokens(text)
        self.tokens_used += tokens
        if self.tokens_used >= self.budget:
            self.budget_exceeded = True
    
    def get_budget_exceeded_message(self) -> str:
        """Get message to return when budget is exceeded."""
        return (
            "Tool budget reached for this request. "
            "You have read as much file content as allowed. "
            "Please summarize your findings and provide a final answer "
            "based on the information you already have."
        )
    
    def get_status(self) -> str:
        """Get current budget status for logging."""
        percentage = (self.tokens_used / self.budget * 100) if self.budget > 0 else 0
        return f"{self.tokens_used}/{self.budget} tokens ({percentage:.1f}%)"


def wrap_tool_with_budget(fn, tool_name: str, budget: ToolBudget, is_heavy: bool = True):
    """Wrap a tool function with budget tracking.
    
    Args:
        fn: Original tool function
        tool_name: Name of the tool for logging
        budget: ToolBudget instance
        is_heavy: If True, this tool consumes significant tokens (read_file, search_repo)
    
    Returns:
        Wrapped function that tracks budget
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Check budget before executing heavy tools
        if is_heavy and not budget.can_use_budget(heavy_tool=True):
            return budget.get_budget_exceeded_message()
        
        # Execute the tool
        result = fn(*args, **kwargs)
        
        # Track token usage
        if isinstance(result, str):
            budget.consume(result)
        
        return result
    
    return wrapper
