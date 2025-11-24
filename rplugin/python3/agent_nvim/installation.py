"""Installation and dependency management for agent.nvim plugin."""

import sys
import os
import subprocess
import asyncio


async def install_deps(nvim, plugin_root: str):
    """Install dependencies to the current Python environment.
    
    Args:
        nvim: Neovim instance
        plugin_root: Root directory of the plugin
    """
    try:
        req_file = os.path.join(plugin_root, "requirements.txt")

        if not os.path.exists(req_file):
            nvim.async_call(
                nvim.err_write, f"requirements.txt not found at {req_file}\n"
            )
            return

        nvim.async_call(
            nvim.out_write,
            "Installing requirements to current Python environment...\n",
        )
        # Install to current Python environment (works with both venv and system Python)
        cmd = [sys.executable, "-m", "pip", "install", "-r", req_file]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            nvim.async_call(
                nvim.out_write,
                "Agent dependencies installed successfully! Please restart Neovim.\n",
            )
        else:
            nvim.async_call(
                nvim.err_write,
                f"Failed to install dependencies: {stderr.decode()}\n",
            )
    except Exception as e:
        nvim.async_call(
            nvim.err_write, f"Exception during install: {str(e)}\n"
        )


def test_imports(nvim):
    """Test that required imports are available.
    
    Args:
        nvim: Neovim instance
    """
    try:
        import openai
        import agents

        nvim.out_write("Success: 'openai' and 'agents' modules imported.\n")
        nvim.out_write(f"agents contents: {dir(agents)}\n")
    except ImportError as e:
        nvim.err_write(f"Error: Could not import modules. {e}\n")
