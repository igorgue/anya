"""Playwright browser automation lib.

Provides a generic run() function that spins up a Google Chrome page,
executes user-supplied Python async code, and returns whatever the
callback produces. The page and browser are closed automatically
after the script finishes.

Always launches the system Google Chrome Stable (not bundled Chromium).
Login sessions persist via a real Chrome user data directory.

Usage::

    from anya.libs import playwright as pw

    result = await pw.run(
        script='await page.goto("https://example.com"); return await page.title()'
        url="https://example.com",
        headless=False,
    )

    # Persistent session
    session = await pw.start(headless=False)
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


DEFAULT_USER_DATA_DIR = os.path.expanduser("~/.config/google-chrome/Default")


async def run(
    script: str,
    url: str | None = None,
    headless: bool = True,
    timeout: int = 60_000,
    viewport: dict | None = None,
    user_agent: str | None = None,
    user_data_dir: str | None = None,
    launch_args: list[str] | None = None,
) -> Any:
    """Run an async Playwright script inside a fresh Google Chrome page.



    The script receives page, context, browser as variables.

    Use return to pass data back. Print statements are captured

    and appended to the return value.



    Launches the system Google Chrome Stable (not Chromium).

    Login cookies persist via a real Chrome user data directory.



    Args:

        script: Python code (can use await). Variables page, context,

               browser are available. Use return to send data back.

        url: Starting URL (calls page.goto before script runs).

        headless: Run headless (default True).

        timeout: Navigation timeout in ms (default 60000).

        viewport: Viewport dict, e.g. {"width": 1280, "height": 720}.

        user_agent: Override User-Agent string.

        user_data_dir: Chrome user data directory for persistent login.

                       Defaults to ~/.config/google-chrome/Default/.

        launch_args: Extra args to pass to Chrome launch.



    Returns:

        Whatever the script returns. Printed output is appended to the result.

    """

    if viewport is None:
        viewport = {"width": 1280, "height": 720}

    if launch_args is None:
        launch_args = []

    import io

    captured = io.StringIO()

    def _capturing_print(*args, **kwargs):

        kwargs.setdefault("file", captured)

        builtins.print(*args, **kwargs)

    indented_script = chr(10).join("        " + line for line in script.splitlines())

    template = (
        "async def _pw_user_script(page, context, browser):"
        + chr(10)
        + indented_script
        + chr(10)
    )

    ns: dict[str, Any] = {"print": _capturing_print}

    try:
        exec(compile(template, "<playwright_script>", "exec"), ns)

    except SyntaxError as exc:
        raise RuntimeError(
            f"Script syntax error: {exc}"
            + chr(10)
            + "--- script ---"
            + chr(10)
            + script
        ) from exc

    data_dir = user_data_dir or DEFAULT_USER_DATA_DIR

    os.makedirs(data_dir, exist_ok=True)

    args = [
        "--disable-blink-features=AutomationControlled",
    ] + launch_args

    if headless:
        args.append("--headless=new")

    async with async_playwright() as p:
        context: BrowserContext = await p.chromium.launch_persistent_context(
            user_data_dir=data_dir,
            channel="chrome",
            headless=headless,
            viewport=viewport,
            args=args,
            no_viewport=False,
        )

        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page: Page = context.pages[0] if context.pages else await context.new_page()

        page.set_default_timeout(timeout)

        page.set_default_navigation_timeout(timeout)

        try:
            if url:
                await page.goto(url, wait_until="domcontentloaded")

            result = await ns["_pw_user_script"](page, context, context.browser)

            captured_text = captured.getvalue()

            if captured_text and result is not None:
                return captured_text.rstrip() + chr(10) + str(result)

            elif captured_text:
                return captured_text.rstrip()

            return result

        except Exception as exc:
            raise RuntimeError(
                f"Playwright script error: {exc}"
                + chr(10)
                + chr(10)
                + traceback.format_exc()
            ) from exc

        finally:
            await context.close()


async def start(
    headless: bool = False,
    timeout: int = 60_000,
    viewport: dict | None = None,
    user_agent: str | None = None,
    user_data_dir: str | None = None,
    launch_args: list[str] | None = None,
) -> dict[str, Any]:
    """Start a persistent Google Chrome session and return handles.



    Launches the system Google Chrome Stable (not Chromium).

    Login cookies persist via a real Chrome user data directory,

    so Beatport and other site logins survive between sessions.



    Returns a dict with page, context, browser, and a close() callback.

    Caller must call close() when finished.



    Args:

        headless: Run headless (default False - shows browser).

        timeout: Navigation timeout in ms (default 60000).

        viewport: Viewport dict, e.g. {"width": 1280, "height": 720}.

        user_agent: Override User-Agent string.

        user_data_dir: Chrome user data directory for persistent login.

                       Defaults to ~/.config/google-chrome/Default/.

        launch_args: Extra args to pass to Chrome launch.



    Returns:

        Dict with keys: page, context, browser, playwright, close.

    """

    if viewport is None:
        viewport = {"width": 1280, "height": 720}

    if launch_args is None:
        launch_args = []

    data_dir = user_data_dir or DEFAULT_USER_DATA_DIR

    os.makedirs(data_dir, exist_ok=True)

    args = [
        "--disable-blink-features=AutomationControlled",
    ] + launch_args

    if headless:
        args.append("--headless=new")

    p = await async_playwright().start()

    context: BrowserContext = await p.chromium.launch_persistent_context(
        user_data_dir=data_dir,
        channel="chrome",
        headless=headless,
        viewport=viewport,
        args=args,
        no_viewport=False,
    )

    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )

    page: Page = context.pages[0] if context.pages else await context.new_page()

    page.set_default_timeout(timeout)

    page.set_default_navigation_timeout(timeout)

    async def _close():

        await context.close()

        await p.stop()

    return {
        "page": page,
        "context": context,
        "browser": context.browser,
        "playwright": p,
        "close": _close,
    }
