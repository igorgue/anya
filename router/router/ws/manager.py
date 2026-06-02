"""WebSocket connection manager for the router.

Maintains persistent connections from daemon clients and routes
messages to/from the correct client.
"""

import asyncio
import json
import logging
import time as _time
from typing import Callable, Awaitable

from fastapi import WebSocket

from ..crypto.identity import verify_signature
from ..db.database import RouterDB

logger = logging.getLogger("anya.router.ws")


class WSError(Exception):
    """WebSocket protocol error."""
    pass


class WSAuthenticatedClient:
    """A connected daemon client authenticated via WebSocket."""

    def __init__(
        self,
        client_id: str,
        websocket: WebSocket,
        db: RouterDB,
        on_message: Callable[[str, dict], Awaitable[None]] | None = None,
    ):
        self.client_id = client_id
        self.websocket = websocket
        self.db = db
        self.on_message = on_message
        self.connected_at = _time.time()
        self._logger = logger.getChild(client_id[:8])

    async def send(self, data: dict) -> None:
        """Send a JSON message to the client."""
        await self.websocket.send_json(data)

    async def recv(self) -> dict:
        """Receive a JSON message from the client."""
        data = await self.websocket.receive_json()
        return data

    @property
    def is_connected(self) -> bool:
        """Check if the WebSocket is still connected."""
        try:
            # FastAPI doesn't have a great way to check, but we catch errors on send/recv
            return True
        except Exception:
            return False


class WSManager:
    """Manages all daemon WebSocket connections."""

    def __init__(self, db: RouterDB):
        self.db = db
        self._clients: dict[str, WSAuthenticatedClient] = {}
        self._lock = asyncio.Lock()
        self._logger = logging.getLogger("anya.router.ws_manager")

    async def handle_connection(self, websocket: WebSocket) -> WSAuthenticatedClient | None:
        """Handle a new WebSocket connection with timestamp-based auth."""
        from ..crypto.identity import verify_signature

        # Receive the client's hello message
        raw = await websocket.receive_text()
        try:
            hello = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.close(code=4000, reason="invalid_hello")
            return None

        if hello.get("type") != "hello":
            await websocket.close(code=4000, reason="expected_hello")
            return None

        try:
            client_id = hello["client_id"]
            signature_hex = hello.get("signature", "")
            timestamp = hello.get("timestamp", 0)
        except KeyError:
            await websocket.close(code=4000, reason="missing_fields")
            return None

        # Check timestamp is within 30 seconds (prevents replay)
        now = int(_time.time())
        if abs(now - timestamp) > 30:
            await websocket.close(code=4001, reason="stale_timestamp")
            return None

        # Verify the signature: client signed "ws_auth:{client_id}:{timestamp}"
        client = self.db.get_client(client_id)
        if not client:
            await websocket.close(code=4001, reason="unknown_client")
            return None

        message = f"ws_auth:{client_id}:{timestamp}".encode()
        signature = bytes.fromhex(signature_hex)
        if not verify_signature(client.public_key, message, signature):
            await websocket.close(code=4001, reason="auth_failed")
            return None

        # Send auth success
        await websocket.send_json({"type": "auth_ok", "client_id": client_id})

        # Create client connection
        ws_client = WSAuthenticatedClient(
            client_id=client_id,
            websocket=websocket,
            db=self.db,
        )

        async with self._lock:
            # Close any existing connection for this client
            existing = self._clients.get(client_id)
            if existing:
                try:
                    await existing.websocket.close(code=4002, reason="replaced")
                except Exception:
                    pass
            self._clients[client_id] = ws_client

        self.db.touch_client(client_id)
        logger.info(f"Client {client_id[:12]} authenticated via WebSocket")

        # Deliver any queued messages
        await self._deliver_queued(ws_client)

        return ws_client

    async def _deliver_queued(self, client: WSAuthenticatedClient):
        """Deliver queued messages to a newly connected client."""
        pending = self.db.get_pending_messages(client.client_id)
        if not pending:
            return

        for msg in pending:
            await client.send({
                "type": "incoming_message",
                "chat_id": msg.chat_id,
                "text": msg.text,
                "message_id": msg.id,
            })
            # Small delay to avoid flooding
            await asyncio.sleep(0.05)

        self.db.mark_delivered([m.id for m in pending])
        logger.info(
            f"Delivered {len(pending)} queued messages to {client.client_id[:12]}"
        )

    async def remove_client(self, client_id: str):
        """Remove a disconnected client."""
        async with self._lock:
            self._clients.pop(client_id, None)
        logger.info(f"Client {client_id[:12]} disconnected")

    async def get_client(self, client_id: str) -> WSAuthenticatedClient | None:
        """Get a connected client by ID."""
        async with self._lock:
            return self._clients.get(client_id)

    async def get_connected_client_ids(self) -> list[str]:
        """Get list of currently connected client IDs."""
        async with self._lock:
            return list(self._clients.keys())

    async def send_to_client(self, client_id: str, data: dict) -> bool:
        """Send a message to a specific client.

        Returns:
            True if sent successfully, False if client not connected.
        """
        client = await self.get_client(client_id)
        if not client:
            return False
        try:
            await client.send(data)
            return True
        except Exception as e:
            logger.warning(f"Failed to send to {client_id[:12]}: {e}")
            await self.remove_client(client_id)
            return False

    async def broadcast(self, data: dict):
        """Broadcast a message to all connected clients."""
        async with self._lock:
            clients = list(self._clients.values())

        for client in clients:
            try:
                await client.send(data)
            except Exception as e:
                logger.warning(f"Broadcast failed for {client.client_id[:12]}: {e}")
                await self.remove_client(client.client_id)
