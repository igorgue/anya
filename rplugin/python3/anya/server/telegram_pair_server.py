"""Local HTTP page for Telegram pairing."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("anya.telegram_pair_server")


class TelegramPairServer:
    def __init__(
        self, telegram_client: Any, host: str | None = None, port: int | None = None
    ):
        self.telegram_client = telegram_client
        self.host = host or os.environ.get("ANYA_TELEGRAM_PAIR_HOST", "127.0.0.1")
        self.port = int(port or os.environ.get("ANYA_TELEGRAM_PAIR_PORT", "8081"))
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self):
        if self._server:
            return
        self._loop = asyncio.get_running_loop()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args):
                logger.info("%s - %s", self.address_string(), fmt % args)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._send_json({"ok": True})
                elif parsed.path == "/pair.json":
                    self._send_pair_json(owner)
                elif parsed.path in {"/", "/pair"}:
                    self._send_pair_page(owner)
                else:
                    self.send_error(404, "Not found")

            def _send_json(self, payload: dict, status: int = 200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _get_pairing_payload(self, owner: "TelegramPairServer") -> dict:
                if not owner._loop:
                    raise RuntimeError("Pairing server loop unavailable")
                future = asyncio.run_coroutine_threadsafe(
                    owner.telegram_client.get_pairing_code(), owner._loop
                )
                result = future.result(timeout=15)
                if not result:
                    raise RuntimeError("Router did not return a pairing code")
                return result if isinstance(result, dict) else {"code": str(result)}

            def _send_pair_json(self, owner: "TelegramPairServer"):
                try:
                    self._send_json(self._get_pairing_payload(owner))
                except Exception as exc:
                    logger.exception("Failed to create Telegram pairing code")
                    self._send_json({"error": str(exc)}, status=500)

            def _send_pair_page(self, owner: "TelegramPairServer"):
                try:
                    payload = self._get_pairing_payload(owner)
                    code = str(payload.get("code") or payload.get("pairing_code") or "")
                    url = str(payload.get("telegram_url") or payload.get("url") or "")
                    expires = payload.get("expires_in") or payload.get("ttl") or 300
                    command = f"/connect {code}" if code else ""
                    qr_target = url or command
                    body = _render_pair_page(code, command, url, qr_target, expires)
                    encoded = body.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except Exception as exc:
                    logger.exception("Failed to render Telegram pairing page")
                    body = _render_error_page(str(exc)).encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = Thread(
            target=self._server.serve_forever,
            name="anya-telegram-pair-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Telegram pairing page available at http://%s:%s/pair", self.host, self.port
        )

    async def stop(self):
        if not self._server:
            return
        server = self._server
        self._server = None
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        self._thread = None
        logger.info("Telegram pairing page stopped")


def _render_pair_page(
    code: str, command: str, url: str, qr_target: str, expires: Any
) -> str:
    safe_code = html.escape(code)
    safe_command = html.escape(command)
    safe_url = html.escape(url, quote=True)
    safe_expires = html.escape(str(expires))
    command_json = html.escape(json.dumps(command), quote=True)
    qr_json = html.escape(json.dumps(qr_target), quote=True)
    open_telegram = (
        f'<p><a class="button" href="{safe_url}">Open Telegram</a></p>' if url else ""
    )
    qr_markup = _render_qr_markup(qr_target)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Anya Telegram Pairing</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0b0d12; color: #e9edf5; }}
    main {{ width: min(720px, calc(100vw - 32px)); padding: 32px; border: 1px solid #293142; border-radius: 18px; background: #111622; box-shadow: 0 18px 60px #0008; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    p {{ color: #aab4c7; line-height: 1.5; }}
    .grid {{ display: grid; gap: 24px; grid-template-columns: minmax(320px, 460px) 1fr; align-items: center; }}
    #qrbox {{ width: min(82vw, 460px); aspect-ratio: 1 / 1; padding: 18px; border-radius: 16px; background: white; box-sizing: border-box; display: flex; align-items: stretch; justify-content: stretch; }}
    #qrbox svg, #qrbox img, #qrbox canvas {{ display: block; width: 100% !important; height: 100% !important; max-width: none !important; max-height: none !important; image-rendering: pixelated; }}
    #qrbox svg {{ flex: 1 1 auto; }}
    code {{ display: inline-block; padding: 6px 10px; border-radius: 8px; background: #05070c; color: #d9e7ff; font-size: 16px; }}
    .code {{ font-size: 30px; letter-spacing: .08em; font-weight: 800; color: #fff; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }}
    button, a.button {{ border: 0; border-radius: 10px; padding: 10px 14px; background: #5b8cff; color: white; text-decoration: none; cursor: pointer; font-weight: 700; }}
    button.secondary {{ background: #293142; }}
    .muted {{ color: #7f8ba3; font-size: 14px; }}
    @media (max-width: 720px) {{ .grid {{ grid-template-columns: 1fr; }} #qrbox {{ margin: auto; }} }}
  </style>
</head>
<body>
  <main>
    <h1>Anya Telegram Pairing</h1>
    <p>Scan this QR code with your phone to open the Telegram bot and pair it with this Anya daemon.</p>
    <div class="grid">
      <div id="qrbox">{qr_markup}</div>
      <section>
        <div class="muted">Pairing code</div>
        <div class="code">{safe_code}</div>
        <p>Manual command:</p>
        <code>{safe_command}</code>
        {open_telegram}
        <p class="muted">Expires in about {safe_expires} seconds. Refresh this page to generate a new code.</p>
        <div class="actions">
          <button onclick="copyText({command_json})">Copy /connect</button>
          <button class="secondary" onclick="location.reload()">New code</button>
        </div>
      </section>
    </div>
  </main>
  <script>
    async function copyText(text) {{ await navigator.clipboard.writeText(text); }}
  </script>
</body>
</html>"""


def _render_qr_markup(text: str) -> str:
    if not text:
        return "<div>No QR target available</div>"

    qrencode = shutil.which("qrencode")
    if qrencode:
        try:
            result = subprocess.run(
                [
                    qrencode,
                    "-t",
                    "SVG",
                    "-m",
                    "1",
                    "--foreground=000000",
                    "--background=FFFFFF",
                    text,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            svg = result.stdout.strip()
            if svg:
                return svg
        except Exception:
            logger.exception("Failed to render QR with qrencode")

    try:
        import qrcode
        import qrcode.image.svg

        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(text, image_factory=factory, border=1)
        svg_bytes = img.to_string(encoding="unicode")
        return str(svg_bytes)
    except Exception:
        logger.exception("Failed to render QR with python qrcode")

    return "<pre>" + html.escape(text) + "</pre>"


def _render_error_page(message: str) -> str:
    return f"""<!doctype html><meta charset=\"utf-8\"><title>Anya Telegram Pairing Error</title>
<body style=\"font-family: sans-serif; background: #0b0d12; color: #e9edf5; padding: 2rem\">
<h1>Could not create Telegram pairing code</h1><pre>{html.escape(message)}</pre></body>"""
