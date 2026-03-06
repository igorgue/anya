"""GitHub Copilot authentication module.

Implements the two-stage OAuth device flow:
1. Device OAuth to get a long-lived GitHub token
2. Token exchange to get a short-lived Copilot API token
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("anya.copilot_auth")

# GitHub Copilot VS Code extension client ID
CLIENT_ID = "Iv1.b507a08c87ecfe98"

# Token storage paths
ANYA_DATA_DIR = Path.home() / ".local" / "share" / "anya"
TOKEN_PATH = ANYA_DATA_DIR / "copilot_token"  # Long-lived GitHub token
API_KEY_CACHE_PATH = ANYA_DATA_DIR / "copilot_api_key.json"  # Short-lived Copilot token

# Required headers for Copilot API
COPILOT_HEADERS = {
    "Editor-Version": "Neovim/0.12",
    "Editor-Plugin-Version": "anya/0.0.1",
    "Copilot-Integration-Id": "copilot-chat",
    "Openai-Intent": "conversation-edits",
}


@dataclass
class CopilotToken:
    """Cached Copilot API token with expiry."""

    token: str
    expires_at: int  # Unix timestamp
    api_base: str

    def is_expired(self, buffer_seconds: int = 300) -> bool:
        """Check if token is expired or will expire within buffer_seconds."""
        return time.time() >= (self.expires_at - buffer_seconds)


class CopilotAuth:
    """Manages GitHub Copilot authentication."""

    def __init__(self):
        self._cached_token: CopilotToken | None = None
        self._ensure_data_dir()

    def _ensure_data_dir(self):
        """Ensure the data directory exists."""
        ANYA_DATA_DIR.mkdir(parents=True, exist_ok=True)

    def is_logged_in(self) -> bool:
        """Check if user has a stored GitHub token."""
        return TOKEN_PATH.exists() and TOKEN_PATH.stat().st_size > 0

    async def get_github_token(self) -> str | None:
        """Get the stored GitHub token, if any."""
        try:
            if TOKEN_PATH.exists():
                return TOKEN_PATH.read_text().strip()
        except Exception as e:
            logger.error(f"Error reading GitHub token: {e}")
        return None

    async def device_flow(self, status_callback=None) -> str:
        """Run the device OAuth flow.

        Args:
            status_callback: Optional async callback for status updates.
                           Called with (message, data) where data may contain
                           user_code, verification_uri, etc.

        Returns:
            The GitHub access token.

        Raises:
            Exception if the flow fails.
        """
        async with httpx.AsyncClient() as client:
            # Step 1: Get device code
            logger.info("Starting device flow...")
            response = await client.post(
                "https://github.com/login/device/code",
                data={
                    "client_id": CLIENT_ID,
                    "scope": "user:email",
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            device_data = response.json()

            device_code = device_data["device_code"]
            user_code = device_data["user_code"]
            verification_uri = device_data["verification_uri"]
            interval = device_data.get("interval", 5)

            logger.info(f"Device code obtained: {user_code}")

            # Notify user with code
            if status_callback:
                await status_callback(
                    "visit_url",
                    {
                        "user_code": user_code,
                        "verification_uri": verification_uri,
                        "message": f"Visit {verification_uri} and enter code: {user_code}",
                    },
                )

            # Step 2: Poll for authorization
            while True:
                await asyncio.sleep(interval)

                response = await client.post(
                    "https://github.com/login/oauth/access_token",
                    data={
                        "client_id": CLIENT_ID,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                token_data = response.json()

                if "access_token" in token_data:
                    github_token = token_data["access_token"]
                    # Save the token
                    TOKEN_PATH.write_text(github_token)
                    logger.info("GitHub token obtained and saved")
                    return github_token

                error = token_data.get("error", "")
                if error == "authorization_pending":
                    # Keep polling
                    continue
                elif error == "slow_down":
                    interval = max(interval + 5, token_data.get("interval", interval))
                    continue
                elif error == "expired_token":
                    raise Exception("Device code expired. Please try again.")
                elif error == "access_denied":
                    raise Exception("Authorization denied by user.")
                else:
                    raise Exception(f"Device flow error: {error}")

    async def exchange_token(self, github_token: str) -> CopilotToken:
        """Exchange GitHub token for a short-lived Copilot API token.

        Args:
            github_token: The long-lived GitHub OAuth token.

        Returns:
            CopilotToken with the short-lived API token.

        Raises:
            Exception if the exchange fails.
        """
        async with httpx.AsyncClient() as client:
            logger.info("Exchanging GitHub token for Copilot token...")
            response = await client.get(
                "https://api.github.com/copilot_internal/v2/token",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    **COPILOT_HEADERS,
                },
            )
            response.raise_for_status()
            data = response.json()

            token = CopilotToken(
                token=data["token"],
                expires_at=data["expires_at"],
                api_base=data.get("endpoints", {}).get(
                    "api", "https://api.githubcopilot.com"
                ),
            )

            # Cache to disk
            try:
                API_KEY_CACHE_PATH.write_text(
                    json.dumps(
                        {
                            "token": token.token,
                            "expires_at": token.expires_at,
                            "api_base": token.api_base,
                        }
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to cache Copilot token: {e}")

            logger.info(f"Copilot token obtained, expires at {token.expires_at}")
            return token

    async def get_copilot_token(self) -> str:
        """Get a valid Copilot API token.

        This will exchange the GitHub token for a Copilot token if needed,
        or refresh an expired Copilot token.

        Returns:
            A valid Copilot API token.

        Raises:
            Exception if not logged in or exchange fails.
        """
        # Check in-memory cache first
        if self._cached_token and not self._cached_token.is_expired():
            return self._cached_token.token

        # Try to load from disk cache
        if not self._cached_token:
            try:
                if API_KEY_CACHE_PATH.exists():
                    data = json.loads(API_KEY_CACHE_PATH.read_text())
                    self._cached_token = CopilotToken(
                        token=data["token"],
                        expires_at=data["expires_at"],
                        api_base=data.get("api_base", "https://api.githubcopilot.com"),
                    )
                    if not self._cached_token.is_expired():
                        return self._cached_token.token
            except Exception as e:
                logger.warning(f"Failed to load cached Copilot token: {e}")

        # Need to exchange for new token
        github_token = await self.get_github_token()
        if not github_token:
            raise Exception("Not logged in to Copilot. Run :Anya copilot login first.")

        self._cached_token = await self.exchange_token(github_token)
        return self._cached_token.token

    def get_api_base(self) -> str:
        """Get the Copilot API base URL.

        Returns the cached endpoint or the default.
        """
        # Check in-memory cache
        if self._cached_token:
            return self._cached_token.api_base

        # Try disk cache
        try:
            if API_KEY_CACHE_PATH.exists():
                data = json.loads(API_KEY_CACHE_PATH.read_text())
                return data.get("api_base", "https://api.githubcopilot.com")
        except Exception:
            pass

        return "https://api.githubcopilot.com"

    def get_status(self) -> dict[str, Any]:
        """Get the current authentication status.

        Returns a dict with:
        - logged_in: bool
        - has_github_token: bool
        - copilot_token_valid: bool (if logged in)
        - copilot_token_expires_at: int | None
        - api_base: str
        """
        status = {
            "logged_in": False,
            "has_github_token": False,
            "copilot_token_valid": False,
            "copilot_token_expires_at": None,
            "api_base": self.get_api_base(),
        }

        # Check GitHub token
        status["has_github_token"] = self.is_logged_in()
        status["logged_in"] = status["has_github_token"]

        # Check Copilot token cache
        try:
            if API_KEY_CACHE_PATH.exists():
                data = json.loads(API_KEY_CACHE_PATH.read_text())
                expires_at = data.get("expires_at")
                status["copilot_token_expires_at"] = expires_at
                if expires_at:
                    status["copilot_token_valid"] = time.time() < expires_at
        except Exception:
            pass

        return status

    async def get_models(self) -> list[dict[str, Any]]:
        """Get available Copilot models.

        Returns a list of model info dicts with:
        - id: model identifier
        - name: display name
        - provider: model provider (openai, anthropic, etc.)

        Raises:
            Exception if not logged in or API call fails.
        """
        token = await self.get_copilot_token()
        api_base = self.get_api_base()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{api_base}/models",
                headers={
                    "Authorization": f"Bearer {token}",
                    **COPILOT_HEADERS,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            # Parse OpenAI-style models response
            models = []
            for model in data.get("data", []):
                model_id = model.get("id", "")
                models.append(
                    {
                        "id": model_id,
                        "name": model.get("name", model_id),
                        "owned_by": model.get("owned_by", "unknown"),
                    }
                )

            # Sort by name
            models.sort(key=lambda m: m["id"])
            return models

    def logout(self):
        """Delete all stored tokens."""
        try:
            if TOKEN_PATH.exists():
                TOKEN_PATH.unlink()
            if API_KEY_CACHE_PATH.exists():
                API_KEY_CACHE_PATH.unlink()
            self._cached_token = None
            logger.info("Logged out from Copilot")
        except Exception as e:
            logger.error(f"Error during logout: {e}")
            raise


# Singleton instance
_auth_instance: CopilotAuth | None = None


def get_auth() -> CopilotAuth:
    """Get the singleton CopilotAuth instance."""
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = CopilotAuth()
    return _auth_instance
