"""Web search using the Brave Search API.

Usage:
    from anya.libs import search

    results = search.web("neovim lua api")
    results = search.web("python asyncio", count=5)
"""

import json
import os
import urllib.error
import urllib.request
from typing import Optional

_API_BASE = "https://api.search.brave.com/res/v1"
_DEFAULT_COUNT = 10
_DEFAULT_TIMEOUT = 15


def _get_api_key() -> str:
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        raise RuntimeError(
            "BRAVE_API_KEY environment variable is not set. "
            "Get a free API key at https://brave.com/search/api/ and export it:\n"
            "  export BRAVE_API_KEY=your-key-here"
        )
    return key


def _fmt_result(r: dict) -> str:
    title = r.get("title", "").strip()
    url = r.get("url", "").strip()
    desc = r.get("description", "").strip()
    parts = [f"**{title}**  {url}"]
    if desc:
        parts.append(desc)
    return "\n".join(parts)


def web(
    query: str,
    count: int = _DEFAULT_COUNT,
    country: Optional[str] = None,
    search_lang: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Search the web using Brave Search and return formatted results.

    Requires the BRAVE_API_KEY environment variable to be set.

    Args:
        query: The search query string.
        count: Number of results to return (default 10, max 20).
        country: Two-letter country code to bias results (e.g. "US", "GB").
        search_lang: Language code for results (e.g. "en", "fr").
        timeout: Request timeout in seconds (default 15).

    Returns:
        Formatted string of search results (title, URL, description), or an
        error message prefixed with 'Error:' on failure.
    """
    key = _get_api_key()

    params = {
        "q": query,
        "count": str(min(max(1, count), 20)),
    }
    if country:
        params["country"] = country
    if search_lang:
        params["search_lang"] = search_lang

    query_string = urllib.parse.urlencode(params)
    url = f"{_API_BASE}/web/search?{query_string}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": key,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Brave may return gzip-encoded content even without explicit Accept-Encoding
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip

                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"Error: HTTP {e.code} {e.reason} — {body}"
    except urllib.error.URLError as e:
        return f"Error: Could not reach Brave Search API: {e.reason}"
    except Exception as e:
        return f"Error: {e}"

    web_results = data.get("web", {}).get("results", [])
    if not web_results:
        return f"No results found for: {query!r}"

    lines = [f"Search results for: {query!r}\n"]
    for i, r in enumerate(web_results, 1):
        lines.append(f"{i}. {_fmt_result(r)}")

    return "\n\n".join(lines)


def news(
    query: str,
    count: int = _DEFAULT_COUNT,
    country: Optional[str] = None,
    search_lang: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Search Brave News and return formatted results.

    Requires the BRAVE_API_KEY environment variable to be set.

    Args:
        query: The search query string.
        count: Number of results to return (default 10, max 20).
        country: Two-letter country code to bias results (e.g. "US", "GB").
        search_lang: Language code for results (e.g. "en", "fr").
        timeout: Request timeout in seconds (default 15).

    Returns:
        Formatted string of news results (title, URL, description, age), or an
        error message prefixed with 'Error:' on failure.
    """
    key = _get_api_key()

    params = {
        "q": query,
        "count": str(min(max(1, count), 20)),
    }
    if country:
        params["country"] = country
    if search_lang:
        params["search_lang"] = search_lang

    query_string = urllib.parse.urlencode(params)
    url = f"{_API_BASE}/news/search?{query_string}"

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": key,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip

                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"Error: HTTP {e.code} {e.reason} — {body}"
    except urllib.error.URLError as e:
        return f"Error: Could not reach Brave Search API: {e.reason}"
    except Exception as e:
        return f"Error: {e}"

    results = data.get("results", [])
    if not results:
        return f"No news results found for: {query!r}"

    lines = [f"News results for: {query!r}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        url_r = r.get("url", "").strip()
        desc = r.get("description", "").strip()
        age = r.get("age", "").strip()
        entry = f"**{title}**  {url_r}"
        if age:
            entry += f"  _{age}_"
        if desc:
            entry += f"\n{desc}"
        lines.append(f"{i}. {entry}")

    return "\n\n".join(lines)
