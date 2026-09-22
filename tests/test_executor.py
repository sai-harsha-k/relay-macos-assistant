from local_assistant.actions.models import (
    ClickElement,
    GenerateText,
    OpenApp,
    Scroll,
    SearchWeb,
    SendMessage,
    TakeScreenshot,
)
from local_assistant.runtime.executor import ActionExecutor
from local_assistant.runtime.types import ExecutionResult
from tests.conftest import FakeBrowser, FakePlatform, FakeWriter


def test_executor_dispatches_platform_action() -> None:
    platform = FakePlatform()
    result = ActionExecutor(platform, FakeBrowser(), FakeWriter()).execute(
        OpenApp(app_name="Calculator")
    )
    assert result.success
    assert platform.calls == [("open_app", "Calculator")]


def test_executor_dispatches_typed_screenshot_action() -> None:
    platform = FakePlatform()
    result = ActionExecutor(platform, FakeBrowser(), FakeWriter()).execute(TakeScreenshot())
    assert result.success
    assert platform.calls == [("take_screenshot", None)]


def test_executor_dispatches_browser_action() -> None:
    browser = FakeBrowser()
    result = ActionExecutor(FakePlatform(), browser, FakeWriter()).execute(
        SearchWeb(query="weather")
    )
    assert result.success
    assert browser.calls == [("search_web", "weather")]


def test_executor_scrolls_frontmost_app_through_platform_not_playwright() -> None:
    platform = FakePlatform()
    browser = FakeBrowser()
    result = ActionExecutor(platform, browser, FakeWriter()).execute(
        Scroll(direction="down", amount=600)
    )
    assert result.success
    assert platform.calls == [("scroll", ("down", 600))]
    assert browser.calls == []


def test_executor_clicks_frontmost_app_through_platform_not_playwright() -> None:
    platform = FakePlatform()
    browser = FakeBrowser()
    result = ActionExecutor(platform, browser, FakeWriter()).execute(
        ClickElement(selector="Imagine Dragons - Believer")
    )
    assert result.success
    assert platform.calls == [("click_element", "Imagine Dragons - Believer")]
    assert browser.calls == []


def test_generated_text_is_returned_as_data_not_executed() -> None:
    platform = FakePlatform()
    writer = FakeWriter("A useful paragraph.")
    result = ActionExecutor(platform, FakeBrowser(), writer).execute(
        GenerateText(instruction="write a paragraph")
    )
    assert result.data == "A useful paragraph."
    assert platform.calls == []


def test_executor_contains_unexpected_adapter_failure() -> None:
    class BrokenPlatform(FakePlatform):
        def open_app(self, name: str) -> ExecutionResult:
            raise RuntimeError("boom")

    result = ActionExecutor(BrokenPlatform(), FakeBrowser(), FakeWriter()).execute(
        OpenApp(app_name="Calculator")
    )
    assert not result.success
    assert "boom" in result.message


def test_executor_dispatches_typed_message_action() -> None:
    platform = FakePlatform()
    result = ActionExecutor(platform, FakeBrowser(), FakeWriter()).execute(
        SendMessage(app_name="WhatsApp", recipient="ABC", content="hello")
    )
    assert result.success
    assert platform.calls == [("send_message", ("WhatsApp", "ABC", "hello"))]


def test_literal_search_and_message_do_not_call_writer() -> None:
    writer = FakeWriter("must not be used")
    browser = FakeBrowser()
    platform = FakePlatform()
    executor = ActionExecutor(platform, browser, writer)
    executor.execute(SearchWeb(query="Linux debugging tutorials"))
    executor.execute(SendMessage(app_name="WhatsApp", recipient="ABC", content="hello"))
    assert writer.calls == []
    assert browser.calls == [("search_web", "Linux debugging tutorials")]
    assert platform.calls == [("send_message", ("WhatsApp", "ABC", "hello"))]


def test_generated_search_and_message_use_writer_for_only_the_language_field() -> None:
    writer = FakeWriter("generated value")
    browser = FakeBrowser()
    platform = FakePlatform()
    executor = ActionExecutor(platform, browser, writer)
    search = SearchWeb(query_generation_instruction="Create a concise Linux query")
    message = SendMessage(
        app_name="WhatsApp",
        recipient="ABC",
        generation_instruction="Write a polite greeting",
    )
    executor.execute(search)
    executor.execute(message)
    assert writer.calls == ["Create a concise Linux query", "Write a polite greeting"]
    assert browser.calls == [("search_web", "generated value")]
    assert platform.calls == [("send_message", ("WhatsApp", "ABC", "generated value"))]
    assert search.kind.value == "SEARCH_WEB"
    assert message.recipient == "ABC"


def test_writer_failure_is_contained_before_action_execution() -> None:
    class BrokenWriter:
        def generate(self, instruction: str, context: str | None = None) -> str:
            raise RuntimeError("Ollama unavailable")

    browser = FakeBrowser()
    result = ActionExecutor(FakePlatform(), browser, BrokenWriter()).execute(
        SearchWeb(query_generation_instruction="rewrite this query")
    )
    assert not result.success
    assert "Ollama unavailable" in result.message
    assert browser.calls == []
