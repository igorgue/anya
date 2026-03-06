import os
import sys
import unittest
from unittest.mock import patch

# Add rplugin/python3 to sys.path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../rplugin/python3"))
)

from anya.plugin import AnyaPlugin


class FakeBuffer:
    def __init__(self, number: int):
        self.number = number


class FakeApi:
    def __init__(self, lines: list[str], conversation_id: str = "conv-1"):
        self._lines = lines
        self._conversation_id = conversation_id
        self.buf_get_lines_calls = 0
        self.last_get_lines_args: tuple[int, int, bool] | None = None

    def buf_is_valid(self, _buf) -> bool:
        return True

    def buf_get_var(self, _buf, name: str) -> str:
        if name != "anya_conversation_id":
            raise AssertionError(f"unexpected var lookup: {name}")
        return self._conversation_id

    def buf_get_lines(
        self,
        _buf,
        start: int,
        end: int,
        strict_indexing: bool,
    ) -> list[str]:
        self.buf_get_lines_calls += 1
        self.last_get_lines_args = (start, end, strict_indexing)
        return list(self._lines)


class FakeNvim:
    def __init__(self, lines: list[str], conversation_id: str = "conv-1"):
        self.api = FakeApi(lines, conversation_id=conversation_id)
        self.errors: list[str] = []

    def err_write(self, message: str):
        self.errors.append(message)

    def async_call(self, _fn, *_args):
        raise AssertionError("_compact_command should not schedule nested async_call")


class TestCompactCommand(unittest.TestCase):
    def _make_plugin(self, nvim: FakeNvim) -> AnyaPlugin:
        plugin = object.__new__(AnyaPlugin)
        plugin.nvim = nvim
        plugin.triggered: list[tuple[str, list[dict[str, str]]]] = []
        plugin._trigger_compaction = lambda conversation_id, llm_history: (
            plugin.triggered.append((conversation_id, llm_history))
        )
        return plugin

    @patch("anya.plugin.ui.flush_queue")
    @patch("anya.plugin.ui.get_chat_buffer")
    @patch("anya.plugin.history.build_llm_history")
    @patch("anya.plugin.history.parse_buffer_content")
    def test_compact_command_reads_buffer_synchronously_and_triggers_compaction(
        self,
        parse_buffer_content,
        build_llm_history,
        get_chat_buffer,
        flush_queue,
    ):
        nvim = FakeNvim(["<!-- am: msg-1 -->", "> user", "assistant"])
        plugin = self._make_plugin(nvim)
        get_chat_buffer.return_value = FakeBuffer(7)
        parse_buffer_content.return_value = ["record"]
        llm_history = [{"role": "user", "content": "hello"}]
        build_llm_history.return_value = llm_history

        plugin._compact_command()

        flush_queue.assert_called_once_with(nvim)
        parse_buffer_content.assert_called_once_with(
            "<!-- am: msg-1 -->\n> user\nassistant"
        )
        build_llm_history.assert_called_once_with(["record"])
        self.assertEqual(plugin.triggered, [("conv-1", llm_history)])
        self.assertEqual(nvim.api.buf_get_lines_calls, 1)
        self.assertEqual(nvim.api.last_get_lines_args, (0, -1, False))
        self.assertEqual(nvim.errors, [])

    @patch("anya.plugin.ui.flush_queue")
    @patch("anya.plugin.ui.get_chat_buffer")
    @patch("anya.plugin.history.build_llm_history")
    @patch("anya.plugin.history.parse_buffer_content")
    def test_compact_command_reports_empty_history(
        self,
        parse_buffer_content,
        build_llm_history,
        get_chat_buffer,
        flush_queue,
    ):
        nvim = FakeNvim(["just text"])
        plugin = self._make_plugin(nvim)
        get_chat_buffer.return_value = FakeBuffer(9)
        parse_buffer_content.return_value = []
        build_llm_history.return_value = []

        plugin._compact_command()

        flush_queue.assert_called_once_with(nvim)
        parse_buffer_content.assert_called_once_with("just text")
        build_llm_history.assert_called_once_with([])
        self.assertEqual(plugin.triggered, [])
        self.assertEqual(
            nvim.errors,
            ["Anya: No conversation history to compact.\n"],
        )


if __name__ == "__main__":
    unittest.main()
