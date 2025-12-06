"""Anya Neovim Plugin"""

from pynvim import Nvim, plugin, command, function, autocmd
import textwrap

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
    def output_text_fn(self, args: list[str]) -> None:
        if not args:
            self.nvim.err_write("output_text requires a text argument.\n")
            return

        text = args[0]
        # Optional: pass buffer number and fold flag
        bufnr = int(args[1]) if len(args) > 1 else None
        fold = bool(args[2]) if len(args) > 2 else False

        self.output_text(text, bufnr, fold)

    @autocmd("BufEnter", pattern="anya_chat", eval='expand("<afile>")', sync=True)
    def on_bufenter(self, filename: str):
        self.nvim.out_write("anya is in " + filename + "\n")

    def _help_text(self) -> str:
        return textwrap.dedent(
            f"""
            {NAME} v{VERSION}

            Usage:
                :Anya help        Show this help message
                :Anya <command>   Execute a specific command
            """
        ).lstrip()

    def send(self, text: str):
        self.nvim.out_write(f"Sending text: {text}\n")

    def output_text(self, text: str, bufnr: int | None = None, fold: bool = False):
        """Output text with streaming animation effect using Lua module.

        Text may contain marker lines (from anya.markers) which will be
        processed to create folds. If fold=True, markers are injected automatically.

        Args:
            text: Text to output (may contain marker lines)
            bufnr: Buffer number (defaults to current buffer)
            fold: If True, wrap text with fold markers
        """
        if bufnr is None:
            bufnr = self.nvim.current.buffer.number

        self.nvim.exec_lua(
            'require("anya").streaming.output_text(...)', bufnr, text, fold
        )
