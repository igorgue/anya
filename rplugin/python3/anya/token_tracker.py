"""Token usage tracking and percentage calculation."""

import os

# Default context window fallback
DEFAULT_CONTEXT_WINDOW = 128000

# OpenRouter model context windows (subset of most common models)
# To update: curl -s https://openrouter.ai/api/v1/models | jq
OPENROUTER_CONTEXT_WINDOWS = {
    # 2M context
    "x-ai/grok-4-fast": 2000000,
    # 1M context
    "google/gemini-2.0-flash-001": 1048576,
    "google/gemini-2.5-flash": 1048576,
    "google/gemini-2.5-pro": 1048576,
    "google/gemini-3-pro-preview": 1048576,
    "openai/gpt-4.1": 1047576,
    "openai/gpt-4.1-mini": 1047576,
    "openai/gpt-4.1-nano": 1047576,
    "anthropic/claude-sonnet-4": 1000000,
    "anthropic/claude-sonnet-4.5": 1000000,
    # 400K context
    "openai/gpt-5": 400000,
    "openai/gpt-5-mini": 400000,
    "openai/gpt-5.1": 400000,
    # 256K context
    "qwen/qwen3-coder": 262144,
    "x-ai/grok-4": 256000,
    # 200K context
    "anthropic/claude-3.5-sonnet": 200000,
    "anthropic/claude-3.7-sonnet": 200000,
    "anthropic/claude-3.7-sonnet:thinking": 200000,
    "anthropic/claude-haiku-4.5": 200000,
    "anthropic/claude-opus-4": 200000,
    "anthropic/claude-opus-4.5": 200000,
    "openai/o1": 200000,
    "openai/o3": 200000,
    "openai/o3-mini": 200000,
    "openai/o4-mini": 200000,
    # 160K context
    "deepseek/deepseek-chat": 163840,
    "deepseek/deepseek-r1": 163840,
    # 128K context
    "meta-llama/llama-3.3-70b-instruct": 131072,
    "x-ai/grok-3": 131072,
    "openai/gpt-4o": 128000,
    "openai/gpt-4o-mini": 128000,
    "openai/chatgpt-4o-latest": 128000,
    # Non-prefixed versions (for direct OpenAI API)
    "gpt-4.1": 1047576,
    "gpt-4.1-mini": 1047576,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "o1": 200000,
    "o3": 200000,
    "o3-mini": 200000,
}


def get_context_window(model: str | None) -> int:
    """Get context window size for a model.

    Args:
        model: Model name (e.g., "gpt-4o", "gpt-4.1", or "anthropic/claude-sonnet-4")

    Returns:
        Context window size in tokens. Uses ANYA_CONTEXT_WINDOW env var if set,
        or looks up model in registry, or defaults to 128K.
    """
    # Check environment override first
    override = os.environ.get("ANYA_CONTEXT_WINDOW")
    if override:
        try:
            return int(override)
        except ValueError:
            pass

    if not model:
        return DEFAULT_CONTEXT_WINDOW

    # Exact match
    if model in OPENROUTER_CONTEXT_WINDOWS:
        return OPENROUTER_CONTEXT_WINDOWS[model]

    # Try with common provider prefixes (e.g., "gpt-4.1" -> "openai/gpt-4.1")
    prefixes = [
        "openai",
        "anthropic",
        "google",
        "meta-llama",
        "mistralai",
        "x-ai",
        "deepseek",
        "qwen",
    ]
    for prefix in prefixes:
        prefixed = f"{prefix}/{model}"
        if prefixed in OPENROUTER_CONTEXT_WINDOWS:
            return OPENROUTER_CONTEXT_WINDOWS[prefixed]

    # Unknown model, use default
    return DEFAULT_CONTEXT_WINDOW


def calculate_usage_percentage(
    total_tokens: int, model: str | None
) -> tuple[float, int]:
    """Calculate token usage as percentage of context window.

    Args:
        total_tokens: Total tokens used in this request
        model: Model name

    Returns:
        Tuple of (percentage: float, context_window: int)
    """
    context_window = get_context_window(model)
    percentage = (total_tokens / context_window) * 100 if context_window > 0 else 0
    return percentage, context_window


def format_context_window(context_window: int) -> str:
    """Format context window size for display.

    Args:
        context_window: Context window size in tokens

    Returns:
        Formatted string like "128K" or "1M"
    """
    if context_window >= 1000000:
        return f"{context_window / 1000000:.0f}M"
    elif context_window >= 1000:
        return f"{context_window / 1000:.0f}K"
    else:
        return str(context_window)
