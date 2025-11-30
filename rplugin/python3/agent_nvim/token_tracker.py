"""Token usage tracking and percentage calculation."""

import os

# Session token tracking
CONTEXT_WINDOW = 128000  # Default GPT-4o context window
USAGE_THRESHOLD = 0.95  # Force response at 95% usage
session_tokens_used = 0  # Track tokens across submits in same session


# Model context windows (tokens)
MODEL_CONTEXT_WINDOWS = {
    # GPT-5 family
    "gpt-5": 400000,
    "gpt-5-mini": 400000,
    "gpt-5-nano": 400000,
    "gpt-5.1": 400000,
    # GPT-4o family
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4o-2024-05-13": 128000,
    "gpt-4o-2024-08-06": 128000,
    "gpt-4o-2024-11-20": 128000,
    # Older models
    "gpt-4-turbo": 128000,
    "gpt-4-turbo-preview": 128000,
    "gpt-4": 8000,
    # GLM family (Zhipu)
    "glm-4.6": 200000,
    "glm-4.5": 131000,
    "glm-4.5-air": 131000,
    # Default fallback
    "default": 128000,
}


def get_context_window(model: str | None) -> int:
    """Get context window size for a model.

    Args:
        model: Model name (e.g., "gpt-4o" or "gpt-5.1")

    Returns:
        Context window size in tokens. Uses AGENT_CONTEXT_WINDOW env var if set,
        or looks up model in registry, or defaults to 128K.
    """
    # Check environment override first
    override = os.environ.get("AGENT_CONTEXT_WINDOW")
    if override:
        try:
            return int(override)
        except ValueError:
            pass

    if not model:
        return MODEL_CONTEXT_WINDOWS["default"]

    # Exact match
    if model in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model]

    # Prefix match (e.g., "gpt-4o-2024-11-20" -> "gpt-4o")
    for key in MODEL_CONTEXT_WINDOWS:
        if key != "default" and model.startswith(key):
            return MODEL_CONTEXT_WINDOWS[key]

    # Unknown model, use default
    return MODEL_CONTEXT_WINDOWS["default"]


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


def format_token_summary(
    total_tokens: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    model: str | None = None,
) -> str:
    """Format token usage as a human-readable string.

    Args:
        total_tokens: Total tokens used
        input_tokens: Input tokens (optional)
        output_tokens: Output tokens (optional)
        model: Model name (optional)

    Returns:
        Formatted string like "267/128K tokens (0.2%)" or "267 tokens"
    """
    percentage, context_window = calculate_usage_percentage(total_tokens, model)

    # Format context window size
    if context_window >= 1000000:
        ctx_str = f"{context_window / 1000000:.0f}M"
    elif context_window >= 1000:
        ctx_str = f"{context_window / 1000:.0f}K"
    else:
        ctx_str = str(context_window)

    # Build the summary
    if context_window > 0:
        return f"{total_tokens}/{ctx_str} tokens ({percentage:.1f}%)"
    else:
        return f"{total_tokens} tokens"


def format_placeholder_text(
    total_tokens: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Format token usage for placeholder display with highlight group.

    Concise format suitable for inline display. Highlight based on usage:
    - 0-40%: OkMsg (green/success)
    - 41-95%: WarningMsg (yellow/warning)
    - 96-100%: ErrorMsg (red/error)

    Args:
        total_tokens: Total tokens used
        input_tokens: Input tokens (optional)
        output_tokens: Output tokens (optional)
        model: Model name (optional)

    Returns:
        Tuple of (formatted_string, highlight_group)
        Formatted string like "25% of 128K" or "267 tokens"
        Highlight group: "OkMsg" (0-40%), "WarningMsg" (41-95%), or "ErrorMsg" (96-100%)
    """
    percentage, context_window = calculate_usage_percentage(total_tokens, model)

    # Determine highlight group based on usage percentage
    if percentage > 95:
        highlight = "ErrorMsg"
    elif percentage > 40:
        highlight = "WarningMsg"
    else:
        highlight = "OkMsg"

    if context_window > 0:
        if context_window >= 1000000:
            ctx_str = f"{context_window / 1000000:.0f}M"
        elif context_window >= 1000:
            ctx_str = f"{context_window / 1000:.0f}K"
        else:
            ctx_str = str(context_window)
        text = f"{percentage:.0f}% of {ctx_str}"
    else:
        text = f"{total_tokens} tokens"

    return text, highlight


def reset_session_tokens():
    """Reset session token counter (call on :AgentOpen)."""
    global session_tokens_used
    session_tokens_used = 0


def update_session_tokens(prompt_tokens: int, completion_tokens: int):
    """Update session token usage.

    Args:
        prompt_tokens: Input tokens used
        completion_tokens: Output tokens generated
    """
    global session_tokens_used
    session_tokens_used += prompt_tokens + completion_tokens


def calculate_max_tokens() -> int:
    """Calculate max_tokens for next request based on session usage.

    Returns:
        Max tokens to use for next request, capped at 95% of remaining context.
    """
    remaining = CONTEXT_WINDOW - session_tokens_used
    max_tokens = int(remaining * USAGE_THRESHOLD)
    return max(1000, max_tokens)  # Ensure at least 1000 tokens for response
