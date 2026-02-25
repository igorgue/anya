import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path.home() / ".config" / "anya" / "mcp" / "servers.json"


def _load_configs() -> list[dict]:
    try:
        with open(_CONFIG_PATH) as f:
            return json.load(f).get("servers", [])
    except Exception:
        return []


def _expand_env(value: Any) -> Any:
    """Recursively expand environment variables in config values."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


_CONFIGS: list[dict] = _load_configs()
_SERVER_NAMES: list[str] = [c.get("name", "") for c in _CONFIGS if c.get("name")]

_TOOLS_CACHE_PATH = Path.home() / ".local" / "share" / "anya" / "mcp_tools_cache.json"


def _load_tools_cache() -> dict[str, list[dict]]:
    try:
        with open(_TOOLS_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


_TOOLS_CACHE: dict[str, list[dict]] = _load_tools_cache()


def _build_doc() -> str:
    """Build the module docstring with available MCP servers and their tools."""
    if not _SERVER_NAMES:
        return (
            "Call MCP (Model Context Protocol) servers directly from generated code.\n\n"
            f"No servers configured. Add them to {_CONFIG_PATH}\n"
        )
    lines = [
        "Call MCP (Model Context Protocol) servers directly from generated code.",
        "",
        "- `mcp.list_servers()`: List the names of all configured MCP servers.",
        "- `mcp.list_tools(server)`: List available tools on an MCP server.",
        "- `mcp.call(...)`: Call a tool on an MCP server and return the result as a string.",
        "",
        "## Available Servers and Tools",
        "",
    ]
    for name in sorted(_SERVER_NAMES):
        tools = _TOOLS_CACHE.get(name, [])
        lines.append(f"### {name}")
        if tools:
            for t in tools:
                tool_name = t.get("name", "")
                tool_desc = t.get("description", "")
                if tool_name:
                    # Truncate long descriptions to first paragraph
                    desc_first = tool_desc.split("\n\n")[0].strip()
                    if len(desc_first) > 300:
                        desc_first = desc_first[:297] + "..."
                    lines.append(f"- `{tool_name}`: {desc_first}")
        else:
            lines.append(f"  (run `mcp.list_tools('{name}')` to discover tools)")
        lines.append("")

    lines.append(
        "When tools are listed above with descriptions, call them directly using `mcp.call(server, tool_name, arguments)`. Only use `mcp.list_tools` if you need to discover tools not shown or need full parameter schemas."
    )
    return "\n".join(lines) + "\n"


__doc__ = _build_doc()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class MCPError(Exception):
    pass


def _get_config(server_name: str) -> dict:
    for c in _CONFIGS:
        if c.get("name") == server_name:
            return c
    available = ", ".join(_SERVER_NAMES) or "none"
    raise MCPError(f"MCP server '{server_name}' not found. Available: {available}")


def _format_result(result: Any) -> str:
    """Convert a tools/call result to a readable string."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get("text", json.dumps(item)))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return json.dumps(result, indent=2)
    return str(result)


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------


class _StdioSession:
    """Synchronous MCP session over a stdio subprocess."""

    def __init__(self, config: dict):
        self._config = config
        self._proc: subprocess.Popen | None = None
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def __enter__(self) -> "_StdioSession":
        cmd = [self._config["command"]] + self._config.get("args", [])
        env = {**os.environ, **_expand_env(self._config.get("env", {}))}

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # MCP handshake
        init_id = self._next_id()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "anya-libs", "version": "1.0"},
                },
            }
        )
        self._recv(init_id, timeout=15)
        # Notify server that we're ready
        self._send(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        return self

    def __exit__(self, *_):
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None

    def _send(self, obj: dict):
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _recv(self, req_id: int, timeout: int = 30) -> Any:
        """Read lines until we find a response matching req_id."""
        result: list[Any] = [None]
        error: list[str | None] = [None]
        done = threading.Event()

        def _reader():
            assert self._proc and self._proc.stdout
            try:
                while not done.is_set():
                    line = self._proc.stdout.readline()
                    if not line:
                        error[0] = "MCP server closed connection"
                        done.set()
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == req_id:
                        if "error" in msg:
                            error[0] = str(msg["error"])
                        else:
                            result[0] = msg.get("result")
                        done.set()
                        return
                    # Skip notifications and responses for other ids
            except Exception as e:
                error[0] = str(e)
                done.set()

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        done.wait(timeout=timeout)

        if not done.is_set():
            raise MCPError(f"Timeout ({timeout}s) waiting for MCP response")
        if error[0]:
            raise MCPError(error[0])
        return result[0]

    def list_tools(self) -> list[dict]:
        req_id = self._next_id()
        self._send(
            {"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}}
        )
        result = self._recv(req_id)
        return (result or {}).get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        req_id = self._next_id()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        )
        return self._recv(req_id, timeout=60)


# ---------------------------------------------------------------------------
# HTTP transport (streamable HTTP / SSE)
# ---------------------------------------------------------------------------


class _HttpSession:
    """Synchronous MCP session over HTTP."""

    def __init__(self, config: dict):
        self._url = _expand_env(config.get("url", ""))
        self._headers = {
            "Content-Type": "application/json",
            # Streamable HTTP MCP servers require this Accept header or they return 406/400.
            # They may respond with plain JSON or SSE (text/event-stream).
            "Accept": "application/json, text/event-stream",
            **config.get("headers", {}),
        }
        self._req_id = 0

    def __enter__(self) -> "_HttpSession":
        return self

    def __exit__(self, *_):
        pass

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _rpc(self, method: str, params: dict, timeout: int = 30) -> Any:
        req_id = self._next_id()
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
        ).encode()
        req = urllib.request.Request(
            self._url, data=body, headers=self._headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = self._parse_response(raw)
                if "error" in data:
                    raise MCPError(f"RPC error: {data['error']}")
                return data.get("result")
        except MCPError:
            raise
        except urllib.error.HTTPError as e:
            raise MCPError(f"HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise MCPError(f"Connection failed: {e.reason}")

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """Parse either plain JSON or SSE (text/event-stream) response."""
        raw = raw.strip()
        # Plain JSON
        if raw.startswith("{"):
            return json.loads(raw)
        # SSE: find the last `data:` line and parse it
        data_line = None
        for line in raw.splitlines():
            if line.startswith("data:"):
                data_line = line[5:].strip()
        if data_line:
            return json.loads(data_line)
        raise MCPError(f"Unrecognised MCP response format: {raw[:200]}")

    def list_tools(self) -> list[dict]:
        result = self._rpc("tools/list", {})
        return (result or {}).get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        return self._rpc(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=60,
        )


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def _make_session(server_name: str) -> _StdioSession | _HttpSession:
    config = _get_config(server_name)
    server_type = config.get("type", "stdio")
    if server_type == "stdio":
        return _StdioSession(config)
    if server_type in ("streamable_http", "http", "sse"):
        return _HttpSession(config)
    raise MCPError(f"Unsupported server type: '{server_type}'")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def call(server_name: str, tool_name: str, arguments: dict | None = None) -> str:
    """Call a tool on an MCP server and return the result as a string.

    Args:
        server_name: Name of the configured MCP server.
        tool_name: Name of the tool to call.
        arguments: Dictionary of arguments to pass to the tool.

    Returns:
        Tool result as a string.

    Raises:
        MCPError: If the server is not configured, connection fails, or tool errors.
    """
    with _make_session(server_name) as session:
        result = session.call_tool(tool_name, arguments or {})
        return _format_result(result)


def list_tools(server_name: str) -> list[dict]:
    """List available tools on an MCP server.

    Args:
        server_name: Name of the configured MCP server.

    Returns:
        List of tool dicts with 'name', 'description', and 'inputSchema' fields.
    """
    with _make_session(server_name) as session:
        return session.list_tools()


def list_servers() -> list[str]:
    """List the names of all configured MCP servers.

    Returns:
        List of server name strings from ~/.config/anya/mcp/servers.json.
    """
    return list(_SERVER_NAMES)
