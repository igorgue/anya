import os
import sys
import unittest
from unittest.mock import patch

# Add rplugin/python3 to sys.path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../rplugin/python3"))
)

from anya.protocol import AgentSettings
from anya.title_agent import _build_openai_request_kwargs, _get_reasoning_effort


class TestTitleAgent(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_get_reasoning_effort_returns_none_without_budget(self):
        settings = AgentSettings(thinking_budget=None)
        self.assertIsNone(_get_reasoning_effort(settings))

    @patch.dict(os.environ, {}, clear=True)
    def test_get_reasoning_effort_defaults_invalid_budget_to_medium(self):
        settings = AgentSettings(thinking_budget="definitely-thinking")
        self.assertEqual(_get_reasoning_effort(settings), "medium")

    def test_build_responses_kwargs_omits_temperature_for_reasoning(self):
        kwargs = _build_openai_request_kwargs(
            api_type="responses",
            model_name="gpt-5",
            prompt="hello",
            max_tokens=30,
            temperature=0.3,
            reasoning_effort="medium",
        )

        self.assertEqual(kwargs["reasoning"], {"effort": "medium", "summary": "auto"})
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["max_output_tokens"], 30)

    def test_build_chat_kwargs_omits_temperature_for_reasoning(self):
        kwargs = _build_openai_request_kwargs(
            api_type="chat_completions",
            model_name="gpt-5",
            prompt="hello",
            max_tokens=30,
            temperature=0.3,
            reasoning_effort="low",
        )

        self.assertEqual(kwargs["reasoning_effort"], "low")
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["max_completion_tokens"], 30)

    def test_build_kwargs_keep_temperature_without_reasoning(self):
        kwargs = _build_openai_request_kwargs(
            api_type="responses",
            model_name="gpt-4.1",
            prompt="hello",
            max_tokens=30,
            temperature=0.3,
            reasoning_effort=None,
        )

        self.assertEqual(kwargs["temperature"], 0.3)
        self.assertNotIn("reasoning", kwargs)


if __name__ == "__main__":
    unittest.main()
