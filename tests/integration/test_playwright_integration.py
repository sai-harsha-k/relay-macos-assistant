import pytest


@pytest.mark.integration
def test_playwright_can_launch_and_render_data_url() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as manager:
            browser = manager.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("data:text/html,<title>Local Assistant</title><h1>ready</h1>")
            assert page.title() == "Local Assistant"
            assert page.get_by_role("heading").inner_text() == "ready"
            browser.close()
    except Exception as exc:
        pytest.skip(f"Playwright Chromium is unavailable: {exc}")
