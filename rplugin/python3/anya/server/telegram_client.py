"""Telegram client for the Anya daemon.

Connects to the Telegram Router via WebSocket, authenticates with
Ed25519 keypair, and handles incoming messages from Telegram users.

This runs inside the Anya daemon process (rplugin/python3/anya/server/).
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import hashes, serialization

logger = logging.getLogger("anya.telegram_client")

CLIENT_ID_PREFIX = "ac"


def _generate_keypair() -> tuple[bytes, bytes]:
    """Generate a new Ed25519 keypair.

    Returns:
        (public_key_bytes, private_key_bytes)
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return public_bytes, private_bytes


def _public_key_to_client_id(public_key: bytes) -> str:
    """Derive a human-readable client ID from a public key."""
    import base64
    import hashlib

    raw_hash = hashlib.blake2b(public_key, digest_size=16).digest()
    b32 = base64.b32hexencode(raw_hash).decode().lower().rstrip("=")
    return f"{CLIENT_ID_PREFIX}{b32[:28]}"


def _sign_message(private_key_bytes: bytes, message: bytes) -> bytes:
    """Sign a message with the private key (detached signature)."""
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    return private_key.sign(message)


def get_identity_path() -> Path:
    """Get the path to the stored identity keypair."""
    data_dir = os.environ.get("XDG_DATA_HOME")
    if data_dir:
        base = Path(data_dir)
    else:
        base = Path.home() / ".local" / "share"
    return base / "anya" / "telegram_identity.json"


def load_or_create_identity() -> tuple[str, bytes, bytes]:
    """Load existing identity or create a new one.

    Returns:
        (client_id, public_key, private_key)
    """
    identity_path = get_identity_path()

    if identity_path.exists():
        data = json.loads(identity_path.read_text())
        public_key = bytes.fromhex(data["public_key"])
        private_key = bytes.fromhex(data["private_key"])
        client_id = data["client_id"]
        logger.info(f"Loaded identity: {client_id[:12]}")
        return client_id, public_key, private_key

    # Generate new keypair
    public_key, private_key = _generate_keypair()
    client_id = _public_key_to_client_id(public_key)

    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(
        json.dumps(
            {
                "client_id": client_id,
                "public_key": public_key.hex(),
                "private_key": private_key.hex(),
            },
            indent=2,
        )
    )

    logger.info(f"Created new identity: {client_id[:12]}")
    return client_id, public_key, private_key


class TelegramClient:
    """WebSocket client connecting to the Telegram Router.

    Runs as a background task inside the daemon.
    """

    def __init__(
        self,
        router_url: str,
        on_message: callable = None,
        on_new_conversation: callable = None,
    ):
        self.router_url = router_url
        self.on_message = on_message
        self.on_new_conversation = on_new_conversation or (lambda chat_id, text=None: None)
        self._running = False
        self._ws = None
        self._task: asyncio.Task | None = None
        self._logger = logger.getChild("client")

        # Load identity
        self.client_id, self.public_key, self.private_key = load_or_create_identity()

    async def start(self):
        """Start the telegram client (connects to router)."""
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        """Stop the telegram client."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def send_response(self, chat_id: int, text: str):
        """Send a response back to a Telegram user via the router."""
        if not self._ws:
            self._logger.warning("Cannot send response: not connected")
            return
        try:
            await self._ws.send(json.dumps({
                "type": "response",
                "chat_id": chat_id,
                "text": text,
            }))
        except Exception as e:
            self._logger.error(f"Failed to send response: {e}")

    async def get_pairing_code(self) -> dict | None:
        """Request a pairing code from the router.

        Returns:
            The router pairing payload, or None if failed.
        """
        import httpx

        router_base = self.router_url.replace("/ws", "").rstrip("/")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{router_base}/pairing",
                    json={"client_id": self.client_id},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    return data
                return {"code": str(data)}
        except Exception as e:
            self._logger.error(f"Failed to get pairing code: {e}")
            return None

    async def _connect_with_retry(self):
        """Connect to the router with exponential backoff."""
        import websockets

        retry_delay = 3
        max_delay = 300  # 5 minutes

        while self._running:
            try:
                ws_url = self.router_url
                self._logger.info(f"Connecting to router: {ws_url}")

                async with websockets.connect(ws_url) as ws:
                    self._ws = ws
                    retry_delay = 3  # Reset on success

                    # Authenticate: sign the client_id + current timestamp
                    import time as _time
                    timestamp = int(_time.time())
                    msg = f"ws_auth:{self.client_id}:{timestamp}".encode()
                    signature = _sign_message(self.private_key, msg)

                    await ws.send(json.dumps({
                        "type": "hello",
                        "client_id": self.client_id,
                        "signature": signature.hex(),
                        "timestamp": timestamp,
                    }))

                    # Wait for auth response
                    raw = await ws.recv()
                    auth_resp = json.loads(raw)
                    if auth_resp.get("type") != "auth_ok":
                        self._logger.error(f"Auth failed: {auth_resp}")
                        await asyncio.sleep(10)
                        continue

                    self._logger.info(f"Authenticated as {self.client_id[:12]}")


                    # Main message loop
                    await self._message_loop(ws)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.warning(
                    f"Connection failed: {e}. Retrying in {retry_delay}s..."
                )
                self._ws = None

            if self._running:
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    async def _register_with_router(self):
        """Register this client's public key with the router via HTTP."""
        import httpx

        router_base = self.router_url.replace("/ws", "").rstrip("/")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{router_base}/register",
                    json={
                        "client_id": self.client_id,
                        "public_key": self.public_key.hex(),
                        "display_name": os.environ.get("USER", "unknown"),
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    self._logger.info(f"Registered with router: {self.client_id[:12]}")
                else:
                    self._logger.warning(f"Registration returned {resp.status_code}: {resp.text}")
        except httpx.ConnectError:
            self._logger.warning(f"Cannot reach router at {router_base}")
        except Exception as e:
            self._logger.warning(f"Registration error: {e}")

    async def _message_loop(self, ws):
        """Receive messages from the router."""
        async for raw in ws:
            try:
                data = json.loads(raw)
                msg_type = data.get("type")

                if msg_type == "incoming_message":
                    chat_id = data["chat_id"]
                    text = data["text"]
                    self._logger.info(
                        f"Received message from chat {chat_id}: {text[:50]}..."
                    )
                    await self.on_message(chat_id, text)

                elif msg_type == "new_conversation":
                    chat_id = data["chat_id"]
                    self._logger.info(f"New conversation requested for chat {chat_id}")
                    if self.on_new_conversation:
                        await self.on_new_conversation(chat_id)

                elif msg_type == "pairing_added":
                    chat_id = data["chat_id"]
                    self._logger.info(f"New pairing added: chat {chat_id}")

                elif msg_type == "pairing_removed":
                    chat_id = data["chat_id"]
                    self._logger.info(f"Pairing removed: chat {chat_id}")

                elif msg_type == "pong":
                    pass

                elif msg_type == "error":
                    self._logger.warning(f"Router error: {data.get('detail')}")

            except json.JSONDecodeError:
                self._logger.warning(f"Invalid JSON from router: {raw[:100]}")
            except Exception as e:
                self._logger.error(f"Error handling message: {e}")

    async def _run(self):
        """Main run loop with reconnection."""
        # Register with router first (HTTP), then connect WebSocket
        await self._register_with_router()
        try:
            await self._connect_with_retry()
        except asyncio.CancelledError:
            pass
        finally:
            self._ws = None
            self._logger.info("Telegram client stopped")
