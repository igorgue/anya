"""Playwright browser automation lib.

Provides a generic run() function that spins up a Google Chrome page,
executes user-supplied Python async code, and returns whatever the
callback produces. The page and browser are closed automatically
after the script finishes.

Usage:

    from anya.libs import playwright as pw

    result = await pw.run("return await page.title()", url="https://example.com")
    # Uses Google Chrome stable (channel="chrome") to preserve user logins

    # Persistent session with login persistence
    session = await pw.start(headless=False, profile="beatport")
    page = session["page"]
    await page.goto("https://example.com")
    await session["close"]()
"""

import asyncio
import builtins
import os
import traceback
from typing import Any

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

DEFAULT_PROFILE_DIR = os.path.expanduser("~/.local/share/anya/browser-profiles")


def _profile_path(name: str) -> str:
    """Return path for a storage state JSON file."""
    os.makedirs(DEFAULT_PROFILE_DIR, exist_ok=True)
    return os.path.join(DEFAULT_PROFILE_DIR, name + ".json")


async def run(
    script: str,
    url: str | None = None,
    headless: bool = True,
    timeout: int = 60_000,
    viewport: dict | None = None,
    user_agent: str | None = None,
    context_kwargs: dict | None = None,
    profile: str | None = None,
) -> Any:
    """Run an async Playwright script inside a fresh Chromium page.

    The script receives page, context, browser as variables.
    Use return to pass data back. Print statements are captured
    and appended to the return value.

    Args:
        script: Python code (can use await). Variables page, context,
               browser are available. Use return to send data back.
        url: Starting URL (calls page.goto before script runs).
        headless: Run headless (default True).
        timeout: Navigation timeout in ms (default 60000).
        viewport: Viewport dict, e.g. {"width": 1280, "height": 720}.
        user_agent: Override User-Agent string.
        context_kwargs: Extra kwargs for browser.new_context().
        profile: Browser profile name for persistent sessions.
                 Stores cookies, localStorage etc in
                 ~/.local/share/anya/browser-profiles/<name>/

    Returns:
        Whatever the script returns. Printed output is appended to the result.
    """
    if viewport is None:
        viewport = {"width": 1280, "height": 720}
    if context_kwargs is None:
        context_kwargs = {}

    import io
    captured = io.StringIO()

    def _capturing_print(*args, **kwargs):
        kwargs.setdefault("file", captured)
        builtins.print(*args, **kwargs)

    indented_script = chr(10).join('        ' + line for line in script.splitlines())

    template = (
        'async def _pw_user_script(page, context, browser):' + chr(10)
        + indented_script + chr(10)
    )

    ns: dict[str, Any] = {'print': _capturing_print}
    try:
        exec(compile(template, "<playwright_script>", "exec"), ns)
    except SyntaxError as exc:
        raise RuntimeError(f"Script syntax error: {exc}" + chr(10) + "--- script ---" + chr(10) + script) from exc

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            headless=headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )

        if profile:
            context: BrowserContext = await browser.new_context(
                storage_state=_profile_path(profile) if os.path.isfile(_profile_path(profile)) else None,
                **context_kwargs,
            )
        else:
            ctx_kwargs: dict[str, Any] = {
                "viewport": viewport,
                "no_viewport": False,
            }
            if user_agent:
                ctx_kwargs["user_agent"] = user_agent
            ctx_kwargs.update(context_kwargs or {})
            context = await browser.new_context(**ctx_kwargs)

        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page: Page = await context.new_page()
        page.set_default_timeout(timeout)
        page.set_default_navigation_timeout(timeout)

        try:
            if url:
                await page.goto(url, wait_until="domcontentloaded")
            result = await ns['_pw_user_script'](page, context, browser)
            captured_text = captured.getvalue()
            if captured_text and result is not None:
                return captured_text.rstrip() + chr(10) + str(result)
            elif captured_text:
                return captured_text.rstrip()
            return result
        except Exception as exc:
            raise RuntimeError(
                f"Playwright script error: {exc}" + chr(10) + chr(10) + traceback.format_exc()
            ) from exc
        finally:
            if profile:
                await context.storage_state(path=_profile_path(profile))
            await context.close()
            await browser.close()


async def start(
    headless: bool = False,
    timeout: int = 60_000,
    viewport: dict | None = None,
    user_agent: str | None = None,
    context_kwargs: dict | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Start a persistent browser session and return handles.

    Returns a dict with page, context, browser, and a close() callback.
    Caller must call close() when finished.

    Args:
        headless: Run headless (default False - shows browser).
        timeout: Navigation timeout in ms (default 60000).
        viewport: Viewport dict, e.g. {"width": 1280, "height": 720}.
        user_agent: Override User-Agent string.
        context_kwargs: Extra kwargs for browser.new_context().
        profile: Browser profile name for persistent sessions.
                 Stores cookies, localStorage etc in
                 ~/.local/share/anya/browser-profiles/<name>/
                 Login sessions survive between calls.

    Returns:
        Dict with keys: page, context, browser, playwright, close.
    """
    if viewport is None:
        viewport = {"width": 1280, "height": 720}
    if context_kwargs is None:
        context_kwargs = {}

    p = await async_playwright().start()
    browser: Browser = await p.chromium.launch(
        headless=headless,
        channel="chrome",
        args=["--disable-blink-features=AutomationControlled"],
    )

    ctx_kwargs: dict[str, Any] = {
        "viewport": viewport,
        "no_viewport": False,
    }
    if user_agent:
        ctx_kwargs["user_agent"] = user_agent
    ctx_kwargs.update(context_kwargs or {})
    if profile:
        storage = _profile_path(profile)
        if os.path.isfile(storage):
            ctx_kwargs["storage_state"] = storage
    context: BrowserContext = await browser.new_context(**ctx_kwargs)


    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )

    page: Page = await context.new_page()
    page.set_default_timeout(timeout)
    page.set_default_navigation_timeout(timeout)

    async def _close():
        if profile:
            await context.storage_state(path=_profile_path(profile))
        await context.close()
        await browser.close()
        await p.stop()

    return {
        "page": page,
        "context": context,
        "browser": browser,
        "playwright": p,
        "close": _close,
    }
