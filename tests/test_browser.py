import asyncio

from local_assistant.browser.playwright_adapter import PlaywrightBrowserAdapter


class FakeMouse:
    def __init__(self) -> None:
        self.wheels: list[tuple[int, int]] = []

    def wheel(self, x: int, y: int) -> None:
        self.wheels.append((x, y))


class FakePage:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.mouse = FakeMouse()

    def goto(self, url: str, **kwargs: object) -> str:
        self.urls.append(url)
        return "ok"


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        pass


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    def launch(self, **kwargs: object) -> FakeBrowser:
        return self.browser


class FakePlaywright:
    def __init__(self, page: FakePage) -> None:
        self.chromium = FakeChromium(FakeBrowser(page))

    def stop(self) -> None:
        pass


class FakeStarter:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    def start(self) -> FakePlaywright:
        return self.playwright


def test_browser_search_url_encodes_query() -> None:
    page = FakePage()
    adapter = PlaywrightBrowserAdapter(
        "https://search.test/?q={query}",
        playwright_factory=lambda: FakeStarter(FakePlaywright(page)),
    )
    result = adapter.search_web("local weather & news")
    assert result.success
    assert page.urls == ["https://search.test/?q=local+weather+%26+news"]
    adapter.close()


def test_browser_scroll_uses_page_mouse() -> None:
    page = FakePage()
    adapter = PlaywrightBrowserAdapter(
        "https://search.test/?q={query}",
        playwright_factory=lambda: FakeStarter(FakePlaywright(page)),
    )
    assert adapter.scroll("up", 500).success
    assert page.mouse.wheels == [(0, -500)]
    adapter.close()


def test_explicit_browser_search_uses_native_opener_without_playwright() -> None:
    calls: list[tuple[str, str]] = []

    def native_open(url: str, app_name: str | None):
        from local_assistant.runtime.types import ExecutionResult

        assert app_name is not None
        calls.append((app_name, url))
        return ExecutionResult(True, "opened")

    adapter = PlaywrightBrowserAdapter(
        "https://search.test/?q={query}",
        playwright_factory=lambda: (_ for _ in ()).throw(
            AssertionError("Playwright must not run for explicit native browser searches")
        ),
        native_url_opener=native_open,
    )
    result = adapter.search_web("YouTube", "Google Chrome")
    assert result.success
    assert calls == [("Google Chrome", "https://search.test/?q=YouTube")]
    adapter.close()


def test_default_browser_navigation_uses_native_opener() -> None:
    from local_assistant.runtime.types import ExecutionResult

    calls: list[tuple[str, str | None]] = []

    def native_open(url: str, app_name: str | None) -> ExecutionResult:
        calls.append((url, app_name))
        return ExecutionResult(True, "opened")

    adapter = PlaywrightBrowserAdapter(
        "https://search.test/?q={query}", native_url_opener=native_open
    )
    assert adapter.open_url("https://www.youtube.com").success
    assert calls == [("https://www.youtube.com", None)]
    adapter.close()


def test_playwright_sync_api_runs_outside_callers_asyncio_loop() -> None:
    page = FakePage()

    class LoopCheckingStarter(FakeStarter):
        def start(self) -> FakePlaywright:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return super().start()
            raise AssertionError("Playwright was started inside the caller's asyncio loop")

    adapter = PlaywrightBrowserAdapter(
        "https://search.test/?q={query}",
        playwright_factory=lambda: LoopCheckingStarter(FakePlaywright(page)),
    )

    async def search() -> None:
        assert adapter.search_web("YouTube").success

    asyncio.run(search())
    adapter.close()
