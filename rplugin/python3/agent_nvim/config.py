"""Configuration management for agent.nvim."""

import os
import json
import logging

try:
    from typing import Dict, Any, Optional
except ImportError:
    Dict = dict
    Any = object
    Optional = type


class ConfigManager:
    """Manages agent.nvim configuration persisted to disk."""

    def __init__(self, logger: logging.Logger = None):
        """Initialize config manager.

        Args:
            logger: Logger instance
        """
        self.logger = logger or logging.getLogger("agent.nvim")
        self.config_dir = os.path.expanduser("~/.config/agent.nvim")
        self.config_file = os.path.join(self.config_dir, "config.json")
        self._ensure_config_dir()
        self._config = self._load_config()

    def _ensure_config_dir(self):
        """Ensure config directory exists."""
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to create config directory: {e}")

    def _load_config(self) -> Dict[str, Any]:
        """Load config from file or return defaults."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.logger.debug(f"Loaded config from {self.config_file}")
                    # Merge with defaults
                    return self._merge_with_defaults(config)
            except Exception as e:
                self.logger.warning(f"Failed to load config: {e}, using defaults")
                return self._get_default_config()
        return self._get_default_config()

    @staticmethod
    def _get_default_config() -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "agent": "CODER",
            "mode": "ASK",
            "toolbar_enabled": True,
        }

    def _merge_with_defaults(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Merge loaded config with defaults.

        Args:
            config: Loaded configuration

        Returns:
            Merged configuration
        """
        defaults = self._get_default_config()
        return {**defaults, **config}

    def _save_config(self):
        """Save config to file."""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
                self.logger.debug(f"Saved config to {self.config_file}")
        except Exception as e:
            self.logger.error(f"Failed to save config: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value.

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Config value
        """
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """Set config value and save.

        Args:
            key: Config key
            value: Config value
        """
        self._config[key] = value
        self._save_config()

    def update(self, updates: Dict[str, Any]):
        """Update multiple config values.

        Args:
            updates: Dictionary of updates
        """
        self._config.update(updates)
        self._save_config()
