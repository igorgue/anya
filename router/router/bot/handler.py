"""Telegram bot integration for the router.

Runs alongside the FastAPI server. Handles Telegram messages,
pairing flow, and message routing to daemon clients.
"""

import asyncio
import json
import logging
import os
from typing import Any

from ..db.database import RouterDB
from ..crypto.identity import generate_pairing_code
from ..ws.manager import WSManager

logger = logging.getLogger("anya.router.bot")


class PairingCodeStore:
    """In-memory store for pending pairing codes with TTL."""

    def __init__(self, ttl_seconds: int = 300):
        self._codes: dict[str, dict] = {}
        self._ttl = ttl_seconds

    def create(self, client_id: str, chat_id: int) -> str:
        """Create a pairing code linking a Telegram chat to a daemon client.

        The flow here is: the daemon generates a code (shown in Neovim UI),
        and sends it to the router. The router stores it and waits for the
        Telegram user to send /connect <code> to the bot.
        """
        code = generate_pairing_code()
        self._codes[code] = {
            "client_id": client_id,
            "chat_id": chat_id,
            "created_at": asyncio.get_event_loop().time(),
        }
        # Cleanup old codes periodically
        self._cleanup()
        return code

    def consume(self, code: str) -> dict | None:
        """Consume a pairing code and return the mapping."""
        entry = self._codes.pop(code.upper(), None)
        if not entry:
            return None
        # Check TTL
        age = asyncio.get_event_loop().time() - entry["created_at"]
        if age > self._ttl:
            return None
        return entry

    def _cleanup(self):
        """Remove expired codes."""
        now = asyncio.get_event_loop().time()
        expired = [
            code for code, entry in self._codes.items()
            if now - entry["created_at"] > self._ttl
        ]
        for code in expired:
            del self._codes[code]


class TelegramBot:
    """Async Telegram bot that routes messages to daemon clients.

    Uses python-telegram-bot v21+ with Application (async).
    """

    def __init__(
        self,
        token: str,
        db: RouterDB,
        ws_manager: WSManager,
        pairing_store: PairingCodeStore,
    ):
        self.token = token
        self.db = db
        self.ws_manager = ws_manager
        self.pairing_store = pairing_store
        self._app: Any | None = None
        self._logger = logger.getChild("handler")

    async def start(self):
        """Start the Telegram bot (long-polling)."""
        from telegram import Update
        from telegram.ext import (
            Application,
            CommandHandler,
            MessageHandler,
            filters,
        )

        self._app = Application.builder().token(self.token).build()

        # Register handlers
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("connect", self._cmd_connect))
        self._app.add_handler(CommandHandler("disconnect", self._cmd_disconnect))
        self._app.add_handler(CommandHandler("new", self._cmd_new))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        self._logger.info("Starting Telegram bot (long-polling)...")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        self._logger.info("Telegram bot started")

    async def stop(self):
        """Stop the Telegram bot."""
        if self._app:
            self._logger.info("Stopping Telegram bot...")
            await self._app.stop()
            await self._app.shutdown()
            self._logger.info("Telegram bot stopped")

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown"):
        """Send a message to a Telegram chat."""
        if not self._app:
            self._logger.warning("Bot not started, cannot send message")
            return
        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
            )
        except Exception as e:
            self._logger.error(f"Failed to send message to {chat_id}: {e}")

    # --- Command handlers ---

    async def _cmd_start(self, update: Any, context: Any):
        """Handle /start command."""
        await update.message.reply_text(
            "Welcome to Anya! I bridge Telegram messages to your AI assistant.\n\n"
            "To get started:\n"
            "1. Open Anya in Neovim and run `:Anya telegram pair`\n"
            "2. Send me the pairing code with `/connect <code>`\n"
            "3. Start chatting!\n\n"
            "Commands:\n"
            "/connect <code> - Link to your Anya daemon\n"
            "/disconnect - Unlink from your daemon\n"
            "/help - Show this message"
        )

    async def _cmd_connect(self, update: Any, context: Any):
        """Handle /connect <code> command."""
        chat_id = update.effective_chat.id
        args = context.args

        if not args:
            await update.message.reply_text(
                "Usage: /connect <pairing_code>\n"
                "Run `:Anya telegram pair` in Neovim to get a code."
            )
            return

        code = args[0].upper()
        entry = self.pairing_store.consume(code)

        if not entry:
            await update.message.reply_text(
                "Invalid or expired pairing code. "
                "Run `:Anya telegram pair` in Neovim to get a new one."
            )
            return

        client_id = entry["client_id"]

        # Create the pairing in the database
        self.db.create_pairing(chat_id, client_id)

        await update.message.reply_text(
            f"Connected to your Anya daemon! You can now send me messages "
            f"and I'll forward them to your assistant.\n\n"
            f"Use /disconnect to unlink at any time."
        )

        # Notify the daemon about the new pairing
        await self.ws_manager.send_to_client(client_id, {
            "type": "pairing_added",
            "chat_id": chat_id,
        })

        self._logger.info(
            f"Chat {chat_id} paired to client {client_id[:12]}"
        )

    async def _cmd_disconnect(self, update: Any, context: Any):
        """Handle /disconnect command."""
        chat_id = update.effective_chat.id
        pairing = self.db.get_pairing(chat_id)

        if not pairing:
            await update.message.reply_text("You're not currently connected.")
            return

        self.db.remove_pairing(chat_id, pairing.client_id)

        await update.message.reply_text(
            "Disconnected from your Anya daemon. "
            "Use /connect to link again anytime."
        )

        # Notify the daemon
        await self.ws_manager.send_to_client(pairing.client_id, {
            "type": "pairing_removed",
            "chat_id": chat_id,
        })

        self._logger.info(
            f"Chat {chat_id} unpaired from client {pairing.client_id[:12]}"
        )


    async def _cmd_new(self, update: Any, context: Any):
        """Handle /new command by asking the daemon to start a fresh conversation."""
        chat_id = update.effective_chat.id
        pairing = self.db.get_pairing(chat_id)

        if not pairing:
            await update.message.reply_text(
                "You're not connected to any Anya daemon.\n"
                "Use /connect <code> to link, or /help for info."
            )
            return

        sent = await self.ws_manager.send_to_client(pairing.client_id, {
            "type": "new_conversation",
            "chat_id": chat_id,
        })

        if sent:
            await update.message.reply_text(
                "Started a new Anya conversation. Send me a message to begin."
            )
            self._logger.info(
                f"Requested new conversation for chat {chat_id} on client {pairing.client_id[:12]}"
            )
        else:
            await update.message.reply_text(
                "Your daemon appears to be offline. Try /new again when it reconnects."
            )

    async def _cmd_help(self, update: Any, context: Any):
        """Handle /help command."""
        await update.message.reply_text(
            "Anya Telegram Bridge\n\n"
            "/connect <code> - Link to your Anya daemon\n"
            "/disconnect - Unlink from your daemon\n"
            "/help - Show this message\n\n"
            "Just send me any message and I'll route it to your AI assistant!"
        )

    async def _handle_message(self, update: Any, context: Any):
        """Handle a text message from a Telegram user.

        Routes the message to the paired daemon client (or queues it).
        """
        chat_id = update.effective_chat.id
        text = update.message.text

        pairing = self.db.get_pairing(chat_id)
        if not pairing:
            await update.message.reply_text(
                "You're not connected to any Anya daemon.\n"
                "Use /connect <code> to link, or /help for info."
            )
            return

        client_id = pairing.client_id

        # Try to send to the daemon via WebSocket
        sent = await self.ws_manager.send_to_client(client_id, {
            "type": "incoming_message",
            "chat_id": chat_id,
            "text": text,
        })

        if sent:
            self._logger.info(
                f"Routed message from chat {chat_id} to client {client_id[:12]}"
            )
        else:
            # Daemon offline - queue the message
            self.db.queue_message(client_id, chat_id, text)
            await update.message.reply_text(
                "Your daemon appears to be offline. "
                "I'll deliver this message when it reconnects."
            )
            self._logger.info(
                f"Queued message from chat {chat_id} for offline client {client_id[:12]}"
            )
