"""FastAPI application for the Anya Telegram Router.

Endpoints:
- GET /health - Health check
- WS /ws - Daemon WebSocket connection (with challenge-response auth)
- POST /pairing - Daemon requests a new pairing code
- POST /respond - Daemon sends a response back to a Telegram user

The Telegram bot runs as a background task alongside the HTTP server.
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request

from .db.database import RouterDB
from .ws.manager import WSManager
from .bot.handler import TelegramBot, PairingCodeStore

logger = logging.getLogger("anya.router")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: start/stop the Telegram bot."""
    # Startup
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set - bot will not start")

    app.state.db = RouterDB()
    app.state.db.connect()
    app.state.pairing_store = PairingCodeStore()
    app.state.ws_manager = WSManager(app.state.db)

    bot_task = None
    if bot_token:
        app.state.bot = TelegramBot(
            token=bot_token,
            db=app.state.db,
            ws_manager=app.state.ws_manager,
            pairing_store=app.state.pairing_store,
        )
        bot_task = asyncio.create_task(app.state.bot.start())

    logger.info("Router started")
    yield

    # Shutdown
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass

    if bot_token and hasattr(app.state, "bot"):
        await app.state.bot.stop()

    app.state.db.close()
    logger.info("Router stopped")


app = FastAPI(
    title="Anya Telegram Router",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "connected_clients": len(await app.state.ws_manager.get_connected_client_ids()),
    }


@app.post("/register")
async def register_client(request: Request):
    """Register a new daemon client.

    Called by daemon on first run to register its public key.
    Body: {
        "client_id": "ac...",
        "public_key": "hex_encoded_ed25519_pubkey",
        "display_name": "optional_name"
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    client_id = body.get("client_id")
    public_key_hex = body.get("public_key")

    if not client_id or not public_key_hex:
        raise HTTPException(status_code=400, detail="client_id_and_public_key_required")

    try:
        public_key = bytes.fromhex(public_key_hex)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_public_key_format")

    display_name = body.get("display_name", client_id[:12])

    app.state.db.register_client(
        client_id=client_id,
        public_key=public_key,
        display_name=display_name,
    )

    return {"status": "registered", "client_id": client_id}


@app.get("/clients")
async def list_clients():
    """List all connected daemon clients."""
    client_ids = await app.state.ws_manager.get_connected_client_ids()
    return {"clients": client_ids}


@app.post("/pairing")
async def create_pairing(request: Request):
    """Daemon requests a new pairing code.

    The daemon sends its client_id, and the router returns a short
    pairing code that can be used via /connect <code> in Telegram.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    client_id = body.get("client_id")
    chat_id = body.get("chat_id")  # Optional: pre-specify chat

    if not client_id:
        raise HTTPException(status_code=400, detail="client_id_required")

    client = app.state.db.get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="client_not_found")

    if chat_id:
        # Create pairing directly if chat_id is provided
        app.state.db.create_pairing(chat_id, client_id)
        return {"code": None, "status": "paired", "chat_id": chat_id}
    else:
        # Generate a pairing code for the user to enter in Telegram
        code = app.state.pairing_store.create(client_id, chat_id=0)
        return {"code": code, "status": "waiting"}


@app.post("/respond")
async def handle_response(request: Request):
    """Daemon sends a response back to a Telegram user.

    Body: {
        "chat_id": 123456,
        "text": "response text"
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    chat_id = body.get("chat_id")
    text = body.get("text")

    if not chat_id or not text:
        raise HTTPException(status_code=400, detail="chat_id_and_text_required")

    bot: TelegramBot | None = getattr(app.state, "bot", None)
    if not bot:
        raise HTTPException(status_code=503, detail="bot_not_available")

    await bot.send_message(chat_id, text)
    return {"status": "sent", "chat_id": chat_id}


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for daemon connections.

    Authentication flow:
    1. Client connects and sends: {"type": "hello", "client_id": "...", "signature": "...", "challenge": "..."}
    2. Server verifies Ed25519 signature and responds with {"type": "auth_ok", "client_id": "..."}
    3. On success, client can send/receive messages

    Message types:
    - Daemon -> Router: {"type": "response", "chat_id": int, "text": str}
    - Router -> Daemon: {"type": "incoming_message", "chat_id": int, "text": str, "message_id": int}
    """
    await websocket.accept()

    try:
        client = await app.state.ws_manager.handle_connection(websocket)
        if client is None:
            # Authentication failed (close code already sent)
            return

        logger.info(f"Client {client.client_id[:12]} connected via WebSocket")

        # Main message loop: receive responses from the daemon
        while True:
            try:
                data = await client.recv()
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.warning(f"Error receiving from {client.client_id[:12]}: {e}")
                break

            msg_type = data.get("type")

            if msg_type == "response":
                chat_id = data.get("chat_id")
                text = data.get("text")

                if not chat_id or text is None:
                    await client.send({"type": "error", "detail": "chat_id_and_text_required"})
                    continue

                # Forward the response to Telegram
                bot: TelegramBot | None = getattr(app.state, "bot", None)
                if bot:
                    await bot.send_message(chat_id, text)
                    await client.send({
                        "type": "response_sent",
                        "chat_id": chat_id,
                    })
                else:
                    await client.send({
                        "type": "error",
                        "detail": "bot_not_available",
                    })

            elif msg_type == "ping":
                await client.send({"type": "pong"})

            else:
                await client.send({
                    "type": "error",
                    "detail": f"unknown_message_type: {msg_type}",
                })

    except WebSocketDisconnect:
        logger.info(f"Client WebSocket disconnected")
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
    finally:
        if client:
            await app.state.ws_manager.remove_client(client.client_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Run the router server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    host = os.environ.get("ROUTER_HOST", "0.0.0.0")
    port = int(os.environ.get("ROUTER_PORT", "8080"))

    logger.info(f"Starting Anya Telegram Router on {host}:{port}")
    uvicorn.run(
        "router.main:app",
        host=host,
        port=port,
        reload=os.environ.get("ROUTER_RELOAD", "").lower() in ("1", "true"),
        log_level="info",
    )


if __name__ == "__main__":
    main()
