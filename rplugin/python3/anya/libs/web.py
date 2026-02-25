"""Fetch web pages and convert them to markdown or plain text.

Usage:
    from anya.libs import web

    md = web.fetch_markdown("https://example.com")
    txt = web.fetch_text("https://example.com")
"""

import re
import urllib.request
import urllib.error
from typing import Optional


_DEFAULT_TIMEOUT = 15
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _fetch_raw(url: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Fetch URL and return raw response body as a string."""
    req = urllib.request.Request(url, headers=_DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = "utf-8"
        content_type = resp.headers.get_content_type() or ""
        if "charset=" in resp.headers.get("Content-Type", ""):
            charset = resp.headers.get_param("charset") or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _html_to_markdown(html: str) -> str:
    """Convert HTML to markdown, using html2text if available, otherwise basic stripping."""
    try:
        import html2text  # type: ignore

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0  # no wrapping
        return h.handle(html).strip()
    except ImportError:
        pass

    # Basic fallback: strip tags, decode common entities
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Convert common block elements to newlines
    text = re.sub(
        r"<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", text, flags=re.IGNORECASE
    )
    text = re.sub(r"<[^>]+>", "", text)
    # Decode basic HTML entities
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def fetch_markdown(url: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Fetch a URL and return the page content as markdown.

    Uses html2text if installed for clean conversion, otherwise falls back to
    basic HTML stripping. Returns an error string on failure.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds (default 15).

    Returns:
        Page content as markdown string, or an error message prefixed with 'Error:'.
    """
    try:
        html = _fetch_raw(url, timeout)
        return _html_to_markdown(html)
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} {e.reason} for {url}"
    except urllib.error.URLError as e:
        return f"Error: Could not reach {url}: {e.reason}"
    except Exception as e:
        return f"Error: {e}"


def fetch_text(url: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """Fetch a URL and return the page content as plain text (all tags stripped).

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds (default 15).

    Returns:
        Plain text content, or an error message prefixed with 'Error:'.
    """
    md = fetch_markdown(url, timeout)
    if md.startswith("Error:"):
        return md
    # Strip remaining markdown formatting for truly plain text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)  # links -> label only
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_json(url: str, timeout: int = _DEFAULT_TIMEOUT) -> object:
    """Fetch a URL and parse the response as JSON.

    Args:
        url: The URL to fetch (must return JSON).
        timeout: Request timeout in seconds (default 15).

    Returns:
        Parsed JSON object (dict, list, etc.), or raises on failure.
    """
    import json

    raw = _fetch_raw(url, timeout)
    return json.loads(raw)
