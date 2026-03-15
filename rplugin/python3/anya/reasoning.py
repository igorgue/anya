from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import AgentSettings

_ALLOWED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def parse_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    effort = str(value).strip().lower()
    if not effort:
        return None
    return effort if effort in _ALLOWED_REASONING_EFFORTS else None


def get_reasoning_effort(settings: "AgentSettings | None") -> str | None:
    raw = None
    if settings is not None:
        raw = settings.thinking_budget
    if raw is None:
        import os

        raw = os.environ.get("ANYA_THINKING_BUDGET")
    if raw is None or not str(raw).strip():
        return None
    return parse_reasoning_effort(raw) or "medium"


def _merge_extra_body(existing: Any, updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def apply_reasoning_settings(model_settings: Any, api_type: str, thinking_budget: str | None) -> None:
    effort = parse_reasoning_effort(thinking_budget)
    if thinking_budget is None:
        return

    if effort is None:
        effort = "medium"

    if effort == "none":
        if api_type == "responses":
            from agents.model_settings import Reasoning

            model_settings.reasoning = Reasoning(effort="none")
        elif api_type == "chat_completions":
            model_settings.extra_body = _merge_extra_body(
                getattr(model_settings, "extra_body", None),
                {"reasoning_effort": "none"},
            )
        elif api_type == "anthropic":
            model_settings.extra_body = _merge_extra_body(
                getattr(model_settings, "extra_body", None),
                {"thinking": {"type": "disabled"}},
            )
        return

    if getattr(model_settings, "reasoning", None) is not None:
        model_settings.reasoning.effort = effort
        model_settings.reasoning.summary = "auto"


def build_openai_reasoning_params(api_type: str, reasoning_effort: str | None) -> dict[str, Any]:
    if reasoning_effort == "none" and api_type == "responses":
        return {"reasoning": {"effort": "none"}}

    if reasoning_effort == "none" and api_type == "chat_completions":
        return {"extra_body": {"reasoning_effort": "none"}}

    if reasoning_effort is None:
        return {}

    if api_type == "responses":
        return {"reasoning": {"effort": reasoning_effort, "summary": "auto"}}

    if api_type == "chat_completions":
        return {"reasoning_effort": reasoning_effort}

    return {}


def build_anthropic_thinking_param(reasoning_effort: str | None) -> dict[str, Any] | None:
    if reasoning_effort == "none":
        return {"type": "disabled"}
    return None
