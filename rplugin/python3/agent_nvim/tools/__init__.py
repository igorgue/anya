"""Builtin tools for agent.nvim plugin."""

import os

from .edit import edit
from .exec import exec
from .exec_lua import exec_lua
from .read_file import read_file
from .read_many_files import read_many_files
from .search import search
from .gh import gh

# File reading limits (configurable via environment)
MAX_READ_BYTES = int(os.environ.get("AGENT_MAX_READ_BYTES", 64000))  # ~16k tokens

__all__ = [
    edit,
    exec,
    exec_lua,
    read_file,
    read_many_files,
    search,
    gh,
    MAX_READ_BYTES,
]
