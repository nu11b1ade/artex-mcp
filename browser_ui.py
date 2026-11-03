"""Optional Chromium session for Web actions implemented only in the browser."""
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.fastmcp.utilities.types import Image


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


class BrowserUI:
    def __init__(self, base_url, settings):
        self.base_url = base_url
        self.settings = settings
        self.driver = self.browser = self.context = self.page = None

    async def start(self):
        from playwright.async_api import async_playwright

        self.driver = await async_playwright().start()
        options = {"chromium_sandbox": True}
        if executable := self.settings.get("ARTEX_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = executable
        if profile := self.settings.get("ARTEX_BROWSER_PROFILE"):
            self.context = await self.driver.chromium.launch_persistent_context(profile, service_workers="block", **options)
        else:
            self.browser = await self.driver.chromium.launch(**options)
            self.context = await self.browser.new_context(service_workers="block")
        await self.context.route("**/*", self.guard_navigation)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def guard_navigation(self, route):
        request = route.request
        if request.is_navigation_request() and origin(request.url) != origin(self.base_url):
            await route.abort()
        else:
            await route.continue_()

    async def close(self):
        try:
            if self.context is not None:
                await self.context.close()
            if self.browser is not None:
                await self.browser.close()
        finally:
            if self.driver is not None:
                await self.driver.stop()
            self.driver = self.browser = self.context = self.page = None

    async def clear_auth(self):
        if self.context is None:
            return
        for page in self.context.pages:
            if origin(page.url) == origin(self.base_url):
                await page.evaluate("() => localStorage.removeItem('artex_token')")
        for name in ("artex_basic_auth", "artex_token"):
            await self.context.clear_cookies(name=name)

    async def sign_in(self, token, basic_cookie):
        cookies = [{"name": "artex_token", "value": token, "url": self.base_url}]
        if basic_cookie:
            cookies.append({"name": "artex_basic_auth", "value": basic_cookie, "url": self.base_url,
                            "httpOnly": True, "sameSite": "Strict"})
        await self.context.add_cookies(cookies)
        # Use a same-origin JSON document to avoid login-page redirects from a stale
        # persistent token while synchronizing the browser's localStorage.
        response = await self.page.goto(self.base_url + "/api/auth/status", wait_until="domcontentloaded")
        if response is None or not response.ok:
            raise ValueError("Chromium could not establish an ARTEX Web session.")
        await self.page.evaluate("(token) => localStorage.setItem('artex_token', token)", token)
        await self.page.goto(self.base_url + "/function/tasks", wait_until="domcontentloaded")

    async def operate(self, action, selector, value, destination, files):
        page = self.page
        if action == "navigate":
            if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
                raise ValueError("Pass a same-origin path beginning with a single slash.")
            await page.goto(self.base_url + value, wait_until="domcontentloaded")
        elif action == "tab":
            if not value or not value.isdecimal() or int(value) >= len(self.context.pages):
                raise ValueError("Pass a tab index from the browser snapshot.")
            page = self.page = self.context.pages[int(value)]
        elif action == "screenshot":
            return Image(data=await page.screenshot(timeout=10000), format="png")
        elif action not in {"snapshot", "sign_in"}:
            if not selector:
                raise ValueError("A Playwright locator selector is required for this action.")
            locator = page.locator(selector)
            if action == "click":
                if destination is None:
                    await locator.click(timeout=10000)
                else:
                    async with page.expect_download(timeout=20000) as pending:
                        await locator.click(timeout=10000)
                    download = await pending.value
                    downloaded = await download.path()
                    max_size = int(self.settings.get("ARTEX_MAX_FILE_BYTES", str(256 * 1024 * 1024)))
                    if downloaded.stat().st_size > max_size:
                        raise ValueError("Download exceeds ARTEX_MAX_FILE_BYTES.")
                    # Commit only a complete copy and never overwrite an existing path.
                    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".artex-", delete=False) as dst:
                        tmp = Path(dst.name)
                        try:
                            with downloaded.open("rb") as src:
                                shutil.copyfileobj(src, dst)
                            dst.close()
                            os.link(tmp, destination)
                        finally:
                            tmp.unlink(missing_ok=True)
                    return {"bytes": downloaded.stat().st_size}
            elif action == "fill":
                await locator.fill(value or "", timeout=10000)
            elif action == "press":
                await locator.press(value, timeout=10000)
            elif action == "select":
                await locator.select_option(value, timeout=10000)
            elif action == "hover":
                await locator.hover(timeout=10000)
            elif action == "drag":
                await locator.drag_to(page.locator(value), timeout=10000)
            elif action == "upload":
                await locator.set_input_files(files, timeout=10000)
        return {
            "url": page.url,
            "tabs": [{"index": i, "url": tab.url} for i, tab in enumerate(self.context.pages)],
            "snapshot": await page.locator(selector or "body").aria_snapshot(timeout=10000)
            if action == "snapshot" else None,
        }
