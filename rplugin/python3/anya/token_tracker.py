"""Token usage tracking and percentage calculation.

This module implements token counting similar to opencode's approach:
- Tracks input, output, reasoning, and cache (read/write) tokens separately
- Calculates usable context as: context_limit - max_output_tokens
- Detects overflow when: input + cache.read + output > usable_context
- Handles cached tokens differently for Anthropic (doesn't subtract from input)
"""

import os
from dataclasses import dataclass
from typing import Any

# Import comprehensive model list for partial matching
from .openrouter_models import OPENROUTER_CONTEXT_WINDOWS as ALL_OPENROUTER_MODELS

# Default context window fallback
DEFAULT_CONTEXT_WINDOW = 128000

# Default max output tokens (conservative estimate)
DEFAULT_MAX_OUTPUT = 32000

# Characters per token estimate (4:1 ratio)
CHARS_PER_TOKEN = 4


@dataclass
class TokenUsage:
    """Detailed token usage tracking."""

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def total(self) -> int:
        """Total tokens including all categories."""
        return self.input + self.output + self.reasoning + self.cache_read + self.cache_write

    @property
    def context_tokens(self) -> int:
        """Tokens that count toward context window (input + cache.read + output)."""
        return self.input + self.cache_read + self.output

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "input": self.input,
            "output": self.output,
            "reasoning": self.reasoning,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "total": self.total,
            "context_tokens": self.context_tokens,
        }


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using 4:1 char ratio.

    Args:
        text: Text to estimate tokens for

    Returns:
        Estimated token count
    """
    return max(0, round(len(text or "") / CHARS_PER_TOKEN))


def parse_usage(usage: Any, provider: str | None = None) -> TokenUsage:
    """Parse usage data from OpenAI Agents SDK into TokenUsage.

    Args:
        usage: Usage object from result.context_wrapper.usage
        provider: Provider name (e.g., "anthropic", "openai") for special handling

    Returns:
        TokenUsage with parsed values
    """
    if not usage:
        return TokenUsage()

    # Get base values
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    # Get detailed breakdown if available
    details = getattr(usage, "details", None)
    cached_tokens = 0
    reasoning_tokens = 0

    if details:
        # Check for cached tokens in input_tokens_details
        input_details = getattr(details, "input_tokens_details", None)
        if input_details:
            cached_tokens = getattr(input_details, "cached_tokens", 0) or 0

        # Check for reasoning tokens in output_tokens_details
        output_details = getattr(details, "output_tokens_details", None)
        if output_details:
            reasoning_tokens = getattr(output_details, "reasoning_tokens", 0) or 0

    # Anthropic doesn't subtract cached tokens from input (they're additional)
    # Other providers include cached in input, so we subtract
    is_anthropic = provider and "anthropic" in provider.lower()
    if cached_tokens > 0 and not is_anthropic:
        input_tokens = max(0, input_tokens - cached_tokens)

    return TokenUsage(
        input=input_tokens,
        output=output_tokens,
        reasoning=reasoning_tokens,
        cache_read=cached_tokens,
        cache_write=0,  # Not typically reported in usage
    )

# Model output limits (max tokens the model can generate)
# Most models default to 32K, but some have different limits
MODEL_OUTPUT_LIMITS: dict[str, int] = {
    # OpenAI models
    "gpt-4.1": 32768,
    "gpt-4.1-mini": 32768,
    "gpt-4.1-nano": 32768,
    "gpt-4o": 16384,
    "gpt-4o-mini": 16384,
    "o1": 100000,
    "o3": 100000,
    "o3-mini": 65536,
    "o4-mini": 100000,
    "gpt-5": 32768,
    "gpt-5-mini": 32768,
    "gpt-5.1": 32768,
    # Anthropic models
    "anthropic/claude-sonnet-4": 16384,
    "anthropic/claude-sonnet-4.5": 16384,
    "anthropic/claude-3.5-sonnet": 8192,
    "anthropic/claude-3.7-sonnet": 16384,
    "anthropic/claude-haiku-4.5": 8192,
    "anthropic/claude-opus-4": 32768,
    "anthropic/claude-opus-4.5": 32768,
    # Google models
    "google/gemini-2.0-flash-001": 8192,
    "google/gemini-2.5-flash": 8192,
    "google/gemini-2.5-pro": 8192,
    "google/gemini-3-pro-preview": 8192,
    # DeepSeek
    "deepseek/deepseek-chat": 8192,
    "deepseek/deepseek-r1": 8192,
}


def get_output_limit(model: str | None) -> int:
    """Get max output tokens for a model.

    Args:
        model: Model name

    Returns:
        Max output tokens the model can generate
    """
    if not model:
        return DEFAULT_MAX_OUTPUT

    # Exact match
    if model in MODEL_OUTPUT_LIMITS:
        return MODEL_OUTPUT_LIMITS[model]

    # Try with common provider prefixes
    prefixes = ["openai", "anthropic", "google", "deepseek"]
    for prefix in prefixes:
        prefixed = f"{prefix}/{model}"
        if prefixed in MODEL_OUTPUT_LIMITS:
            return MODEL_OUTPUT_LIMITS[prefixed]

    # Partial match: check if the base model name matches any entry
    base_model = model.split("/")[-1]
    for full_model_name, output_limit in MODEL_OUTPUT_LIMITS.items():
        if full_model_name.endswith(base_model):
            return output_limit

    return DEFAULT_MAX_OUTPUT


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

    # Exact match in the curated list
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
        "z-ai",
    ]
    for prefix in prefixes:
        prefixed = f"{prefix}/{model}"
        if prefixed in OPENROUTER_CONTEXT_WINDOWS:
            return OPENROUTER_CONTEXT_WINDOWS[prefixed]

    # Partial match: check if the base model name matches any entry
    # This handles cases like "glm-4.7" when the full name is "z-ai/glm-4.7"
    base_model = model.split("/")[-1]  # Get the part after last slash
    for full_model_name, context_size in ALL_OPENROUTER_MODELS.items():
        if full_model_name.endswith(base_model):
            return context_size

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


def calculate_context_usage(
    usage: TokenUsage, model: str | None
) -> tuple[float, int, int, bool]:
    """Calculate context usage percentage using opencode's approach.

    This calculates the usable context (context_limit - max_output_tokens)
    and checks if we're approaching overflow.

    Args:
        usage: TokenUsage with detailed breakdown
        model: Model name

    Returns:
        Tuple of (percentage, context_window, usable_context, is_overflow)
    """
    context_window = get_context_window(model)
    output_limit = get_output_limit(model)

    # Usable context = total context - reserved for output
    usable_context = context_window - output_limit
    if usable_context <= 0:
        usable_context = context_window

    # Context tokens = input + cache.read + output (what actually counts)
    context_tokens = usage.context_tokens

    # Calculate percentage of usable context
    percentage = (context_tokens / usable_context) * 100 if usable_context > 0 else 0

    # Check overflow (when context tokens exceed usable context)
    is_overflow = context_tokens > usable_context

    return percentage, context_window, usable_context, is_overflow


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
