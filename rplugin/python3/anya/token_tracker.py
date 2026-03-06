"""Token usage tracking and percentage calculation.

This module implements token counting similar to opencode's approach:
- Tracks input, output, reasoning, and cache (read/write) tokens separately
- Calculates usable context as: context_limit - max_output_tokens
- Detects overflow when: input + cache.read > usable_context
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
        return (
            self.input
            + self.output
            + self.reasoning
            + self.cache_read
            + self.cache_write
        )

    @property
    def context_tokens(self) -> int:
        """Tokens that count toward context window (input + cache.read).

        Note: Output tokens don't consume context - they're generated from it.
        """
        return self.input + self.cache_read

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

    def _get(obj: Any, key: str, default: Any = 0) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    # Get base values
    input_tokens = _get(usage, "input_tokens", 0) or 0
    output_tokens = _get(usage, "output_tokens", 0) or 0

    cached_tokens = 0
    reasoning_tokens = 0

    # The SDK Usage class has input_tokens_details and output_tokens_details
    # as direct attributes (not nested under 'details')
    input_details = _get(usage, "input_tokens_details", None)
    if input_details:
        cached_tokens = _get(input_details, "cached_tokens", 0) or 0

    output_details = _get(usage, "output_tokens_details", None)
    if output_details:
        reasoning_tokens = _get(output_details, "reasoning_tokens", 0) or 0

    # Also check for 'details' wrapper (some API responses use this structure)
    if not cached_tokens and not reasoning_tokens:
        details = _get(usage, "details", None)
        if details:
            input_details = _get(details, "input_tokens_details", None)
            if input_details:
                cached_tokens = _get(input_details, "cached_tokens", 0) or 0

            output_details = _get(details, "output_tokens_details", None)
            if output_details:
                reasoning_tokens = _get(output_details, "reasoning_tokens", 0) or 0

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


def extract_request_usages(usage: Any, provider: str | None = None) -> list[TokenUsage]:
    """Extract per-request usage entries from aggregate usage when available."""
    if not usage:
        return []

    entries = None

    if isinstance(usage, dict):
        entries = usage.get("request_usage_entries")
    else:
        entries = getattr(usage, "request_usage_entries", None)

    if not entries:
        return []

    parsed: list[TokenUsage] = []
    for entry in entries:
        if not entry:
            continue

        entry_usage = entry
        if isinstance(entry, dict):
            entry_usage = entry.get("usage", entry)
        else:
            nested_usage = getattr(entry, "usage", None)
            if nested_usage is not None:
                entry_usage = nested_usage

        parsed_usage = parse_usage(entry_usage, provider=provider)
        if parsed_usage.total > 0 or parsed_usage.context_tokens > 0:
            parsed.append(parsed_usage)

    return parsed


def choose_context_usage(
    usage: Any, provider: str | None = None
) -> tuple[TokenUsage, TokenUsage, int]:
    """Return (effective, aggregate, request_count) token usage.

    - effective: best single-request context usage (max context_tokens)
    - aggregate: run-aggregated usage from context_wrapper.usage
    """
    aggregate = parse_usage(usage, provider=provider)
    request_usages = extract_request_usages(usage, provider=provider)

    if not request_usages:
        return aggregate, aggregate, 0

    effective = max(request_usages, key=lambda u: u.context_tokens)
    return effective, aggregate, len(request_usages)


PROVIDER_PREFIXES = [
    "openai",
    "anthropic",
    "google",
    "meta-llama",
    "mistralai",
    "x-ai",
    "z-ai",
    "deepseek",
    "qwen",
    "moonshotai",
    "cohere",
    "nvidia",
]


def _match_model(model: str) -> int | None:
    """Match a model name against the OpenRouter models table.

    Args:
        model: Model name (e.g., "gpt-4o", "glm-4.7", "z-ai/glm-4.7")

    Returns:
        Context window size if found, None otherwise.
    """
    model_lower = model.lower()

    # 1. Exact match (case-insensitive)
    for full_model_name, context_size in ALL_OPENROUTER_MODELS.items():
        if full_model_name.lower() == model_lower:
            return context_size

    # 2. Try with common provider prefixes (e.g., "gpt-4.1" -> "openai/gpt-4.1")
    for prefix in PROVIDER_PREFIXES:
        prefixed = f"{prefix}/{model_lower}"
        for full_model_name, context_size in ALL_OPENROUTER_MODELS.items():
            if full_model_name.lower() == prefixed:
                return context_size

    # 3. Partial match: match only the model name part after the slash
    # This handles cases like "glm-4.7" matching "z-ai/glm-4.7"
    base_model = model_lower.split("/")[-1]
    for full_model_name, context_size in ALL_OPENROUTER_MODELS.items():
        if full_model_name.lower().split("/")[-1] == base_model:
            return context_size

    return None


def get_context_window(model: str | None) -> int:
    """Get context window size for a model.

    Args:
        model: Model name (e.g., "gpt-4o", "gpt-4.1", "glm-4.7", or "z-ai/glm-4.7")

    Returns:
        Context window size in tokens. Uses ANYA_CONTEXT_WINDOW env var if set,
        or looks up model in openrouter_models.py, or defaults to 128K.
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

    result = _match_model(model)
    return result if result is not None else DEFAULT_CONTEXT_WINDOW


def calculate_context_usage(
    usage: TokenUsage, model: str | None
) -> tuple[float, int, int, bool]:
    """Calculate context usage percentage.

    Args:
        usage: TokenUsage with detailed breakdown
        model: Model name

    Returns:
        Tuple of (percentage, context_window, usable_context, is_overflow)

        - percentage: context_tokens / usable_context * 100
        - context_window: full context window size
        - usable_context: context_window - max_output_tokens
        - is_overflow: whether context_tokens exceeds usable_context
    """
    context_window = get_context_window(model)

    # Usable context is the portion available for input
    # (reserve space for model output)
    usable_context = context_window - DEFAULT_MAX_OUTPUT

    # Context tokens = input + cache.read (what fills the context window)
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
