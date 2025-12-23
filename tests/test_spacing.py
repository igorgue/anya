import sys
import os
import unittest

# Add rplugin/python3 to sys.path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../rplugin/python3"))
)

from anya.spacing import SpacingManager, ContentType
from anya import markers


class TestSpacingManager(unittest.TestCase):
    def setUp(self):
        self.sm = SpacingManager()

    def test_marker_isolation(self):
        text = "some text<!-- at: fold_start -->more text"
        isolated = self.sm.ensure_marker_isolation(text)
        self.assertEqual(isolated, "some text\n<!-- at: fold_start -->\nmore text")

    def test_marker_isolation_redundant(self):
        text = "some text\n\n<!-- at: fold_start -->\n\nmore text"
        isolated = self.sm.ensure_marker_isolation(text)
        self.assertEqual(isolated, "some text\n<!-- at: fold_start -->\nmore text")

    def test_transitions(self):
        # Text -> Tool Header
        self.sm.format_delta("hello", ContentType.TEXT)
        header = self.sm.format_content("ls", ContentType.TOOL_HEADER, ["fold_start"])
        # Header then marker. Markers always end with \n for isolation.
        self.assertEqual(header, "\n\nls\n<!-- at: fold_start -->\n")

        # Marker -> Text (after tool marker, should have blank line)
        text = self.sm.format_delta("output", ContentType.TEXT)
        # Should have leading newlines from get_spacing_for_transition (blank line after marker)
        self.assertEqual(text, "\n\noutput")

    def test_message_boundary(self):
        header = self.sm.format_content(
            "", ContentType.MESSAGE_BOUNDARY, msg_id="123", is_first_in_buffer=True
        )
        # First in buffer should have NO leading newline, but HAS trailing for next block
        self.assertEqual(header, "<!-- am: 123 -->\n")

        # Next message
        header2 = self.sm.format_content("", ContentType.MESSAGE_BOUNDARY, msg_id="456")
        # Should NOT have leading newline (removed by ensure_marker_isolation fix), but has trailing for next
        self.assertEqual(header2, "<!-- am: 456 -->\n")

    def test_format_delta(self):
        delta1 = self.sm.format_delta("hello", ContentType.TEXT)
        # First delta in a new manager will NOT have a newline because _last_content_type is None
        self.assertEqual(delta1, "hello")

        # Test delta starting with newline after tool marker (fold_start)
        self.sm.format_content("", ContentType.MARKER, ["fold_start"])
        delta2 = self.sm.format_delta("\nworld", ContentType.TEXT)
        # fold_start is a tool marker, so text after it gets a blank line
        self.assertEqual(delta2, "\n\nworld")


if __name__ == "__main__":
    unittest.main()
