"""Built-in Python libraries available to agent-generated code.

All modules under this package are automatically available via:
    from anya.libs import <module>

Each module provides utilities the agent can use directly in execute() calls
without needing to install anything or write boilerplate.
"""

import importlib
import inspect
import os
import pkgutil


def get_libs_prompt() -> str:
    """Scan all modules in this package and generate a prompt section.

    Reads each module's docstring and public function signatures/docstrings
    to produce a markdown block describing what the agent can import.

    Returns:
        Markdown string listing available libs, or empty string if none found.
    """
    libs_dir = os.path.dirname(os.path.abspath(__file__))
    modules = []

    for _finder, name, _ispkg in pkgutil.iter_modules([libs_dir]):
        try:
            module = importlib.import_module(f".{name}", package="anya.libs")
            module_doc = inspect.getdoc(module) or ""

            # Collect public functions defined in this module
            functions = []
            for func_name, func in inspect.getmembers(module, inspect.isfunction):
                if func_name.startswith("_"):
                    continue
                if func.__module__ != module.__name__:
                    continue
                func_doc = inspect.getdoc(func) or ""
                first_line = func_doc.splitlines()[0] if func_doc else ""
                functions.append((func_name, first_line))

            modules.append((name, module_doc, functions))
        except Exception:
            continue

    if not modules:
        return ""

    lines = ["\n## Built-in Agent Libraries\n"]
    lines.append(
        "These modules are pre-installed and importable in any `execute` call "
        "via `from anya.libs import <module>`:\n"
    )

    for mod_name, mod_doc, funcs in modules:
        # For MCP module, include full docstring since tools are documented there
        if mod_name == "mcp" and mod_doc and "## Available Servers" in mod_doc:
            lines.append(f"### `from anya.libs import {mod_name}`")
            lines.append(mod_doc)
            lines.append("")
            continue

        # Use first line of module docstring as summary
        summary = mod_doc.splitlines()[0] if mod_doc else "No description."
        lines.append(f"### `from anya.libs import {mod_name}`")
        lines.append(summary)
        if funcs:
            for func_name, func_doc in funcs:
                entry = f"- `{func_name}(...)`"
                if func_doc:
                    entry += f": {func_doc}"
                lines.append(entry)
        lines.append("")

    return "\n".join(lines)
