from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote_plus

from local_assistant.runtime.types import ExecutionResult

NativeUrlOpener = Callable[[str, str | None], ExecutionResult]


class PlaywrightBrowserAdapter:
    def __init__(
        self,
        search_engine: str,
        headless: bool = False,
        playwright_factory: Any | None = None,
        native_url_opener: NativeUrlOpener | None = None,
    ) -> None:
        self._search_engine = search_engine
        self._headless = headless
        self._factory = playwright_factory
        self._native_url_opener = native_url_opener
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._page: Any | None = None
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="relay-playwright")
        self._closed = False

    def _ensure_page(self) -> Any:
        if self._page is not None:
            return self._page
        if self._factory is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Playwright is not installed. Run: uv sync --extra browser"
                ) from exc
            self._factory = sync_playwright
        self._playwright = self._factory().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._page = self._browser.new_page()
        return self._page

    def _perform(self, operation: Any, success: str) -> ExecutionResult:
        if self._closed:
            return ExecutionResult(False, "Browser adapter is closed")

        def perform() -> ExecutionResult:
            started = time.perf_counter()
            try:
                data = operation()
            except Exception as exc:
                return ExecutionResult(
                    False,
                    f"Browser action failed: {exc}",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            return ExecutionResult(
                True, success, data=data, duration_ms=(time.perf_counter() - started) * 1000
            )

        return self._worker.submit(perform).result()

    def _native_open(self, url: str, browser_app: str | None) -> ExecutionResult | None:
        opener = self._native_url_opener
        if opener is None:
            return None
        if self._closed:
            return ExecutionResult(False, "Browser adapter is closed")
        try:
            return self._worker.submit(opener, url, browser_app).result()
        except Exception as exc:
            return ExecutionResult(False, f"Browser action failed: {exc}")

    def open_url(self, url: str, browser_app: str | None = None) -> ExecutionResult:
        native = self._native_open(url, browser_app)
        if native is not None:
            return native
        return self._perform(
            lambda: self._ensure_page().goto(url, wait_until="domcontentloaded"), f"Opened {url}"
        )

    def search_web(self, query: str, browser_app: str | None = None) -> ExecutionResult:
        url = self._search_engine.format(query=quote_plus(query))
        native = self._native_open(url, browser_app)
        if native is not None:
            return native
        if browser_app is not None:
            return ExecutionResult(
                False,
                f"Cannot target {browser_app}: native browser opener is unavailable",
            )
        return self._perform(
            lambda: self._ensure_page().goto(url, wait_until="domcontentloaded"),
            f"Searched for {query}",
        )

    def click_element(self, selector: str) -> ExecutionResult:
        def click() -> None:
            page = self._ensure_page()
            if selector.startswith(("#", ".", "[", "//")):
                page.locator(selector).first.click()
            else:
                page.get_by_text(selector, exact=False).first.click()

        return self._perform(click, f"Clicked {selector}")

    def scroll(self, direction: str, amount: int) -> ExecutionResult:
        dx = amount if direction == "right" else -amount if direction == "left" else 0
        dy = amount if direction == "down" else -amount if direction == "up" else 0
        return self._perform(
            lambda: self._ensure_page().mouse.wheel(dx, dy), f"Scrolled {direction}"
        )

    def close(self) -> None:
        if self._closed:
            return

        def close_on_worker() -> None:
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
            self._page = self._browser = self._playwright = None

        self._worker.submit(close_on_worker).result()
        self._closed = True
        self._worker.shutdown(wait=True, cancel_futures=True)
