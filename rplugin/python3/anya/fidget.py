"""Fidget integration utilities for Anya plugin."""

import json
import logging

logger = logging.getLogger("anya.fidget")


def emit_user_event(nvim, event_name: str, data: dict) -> None:
    """Emit a User autocommand event with data for fidget integration.

    Args:
        nvim: Neovim instance
        event_name: Name of the event to emit
        data: Dictionary of data to pass with the event
    """
    try:
        # Serialize data to JSON
        data_json = json.dumps(data)
        # Use Lua bracket notation [[...]] to avoid quote escaping issues
        lua_code = f"""vim.api.nvim_exec_autocmds('User', {{pattern = '{event_name}', data = vim.fn.json_decode([[{data_json}]])}})"""

        def _emit_callback():
            try:
                nvim.exec_lua(lua_code)
            except Exception as e:
                logger.warning(f"Failed to emit event {event_name}: {e}")

        # Execute doautocmd with data
        nvim.async_call(_emit_callback)
    except Exception as e:
        logger.warning(f"Failed to schedule event {event_name}: {e}")
