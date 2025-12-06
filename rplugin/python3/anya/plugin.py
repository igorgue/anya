"""Anya Neovim Plugin"""

from pynvim import Nvim, plugin, command, function, autocmd
import textwrap
import asyncio
import threading

from agents import Runner

from .agents import code
from .agents.context import NvimPluginContext


NAME = "anya"
VERSION = "0.0.1"


@plugin
class AnyaPlugin(object):
    nvim: Nvim

    def __init__(self, nvim: Nvim) -> None:
        self.nvim = nvim

    @command("Anya", nargs="*", range="")
    def main_cmd(self, args: list[str], _range: list[int]) -> None:
        subcommand = args[0] if args else "help"

        if subcommand == "help":
            self.nvim.out_write(self._help_text())
            return

        if subcommand == "open":
            self.nvim.out_write("Anya open executed!\n")
            return

        if subcommand == "send":
            if len(args) < 2:
                self.nvim.err_write("'send' command requires text argument.\n")
                return

            # Join all arguments after 'send' with spaces
            text = " ".join(args[1:])

            self.send(text)

            return

        self.nvim.out_write("Anya main executed!\n")

    @function("AnyaOutputText", sync=True)
    def output_text_fn(self, args: list) -> None:
        if not args:
            self.nvim.err_write("output_text requires a text argument.\n")
            return

        text = args[0]
        # Optional: pass buffer number and markers list
        bufnr = int(args[1]) if len(args) > 1 else None
        markers = args[2] if len(args) > 2 else None

        self.output_text(text, bufnr, markers)

    @autocmd("BufEnter", pattern="anya_chat", eval='expand("<afile>")', sync=True)
    def on_bufenter(self, filename: str):
        self.nvim.out_write("anya is in " + filename + "\n")

    def _help_text(self) -> str:
        return textwrap.dedent(
            f"""
            {NAME} v{VERSION}

            Usage:
                :Anya help              Show this help message
                :Anya send <prompt>     Send a prompt to the code agent
                :Anya open              Open the Anya interface
            """
        ).lstrip()

    def send(self, text: str):
        """Send a prompt to the code agent and display the response."""
        self.nvim.out_write(f"Sending: {text}\n")

        # Create context with nvim instance
        context = NvimPluginContext(nvim=self.nvim)

        def run_agent():
            """Run agent in a separate thread with its own event loop."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    Runner.run(
                        starting_agent=code,
                        input=text,
                        context=context,
                    )
                )
                response = result.final_output or "[No response from agent]"
                # Callback to main thread with result
                self.nvim.async_call(self.nvim.out_write, f"\n{response}\n")
            except Exception as e:
                self.nvim.async_call(self.nvim.err_write, f"Agent error: {e}\n")
            finally:
                loop.close()

        # Start agent in background thread (non-blocking)
        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()

    def output_text(
        self, text: str, bufnr: int | None = None, markers: list[str] | None = None
    ):
        """Output text with streaming animation effect using Lua module.

        Text may contain marker lines (from anya.markers) which will be
        processed to create folds and other UI elements.

        Args:
            text: Text to output (may contain marker lines)
            bufnr: Buffer number (defaults to current buffer)
            markers: List of markers to inject (e.g., ["fold", "tool_success"])
        """
        if bufnr is None:
            bufnr = self.nvim.current.buffer.number

        self.nvim.exec_lua(
            'require("anya").text.output_text(...)', bufnr, text, markers
        )
