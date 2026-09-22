import subprocess

from local_assistant.platforms.macos import MacOSPlatformAdapter


def completed(
    stdout: str = "", stderr: str = "", code: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def test_platform_adapter_uses_argument_array_for_open_app() -> None:
    calls: list[object] = []

    def runner(command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed()

    result = MacOSPlatformAdapter(runner=runner).open_app("Calculator")
    assert result.success
    assert calls == [["open", "-a", "Calculator"]]


def test_platform_adapter_reports_command_failure() -> None:
    def runner(command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed(stderr="not permitted", code=1)

    result = MacOSPlatformAdapter(runner=runner).type_text("hello")
    assert not result.success
    assert result.message == "not permitted"


def test_platform_opens_url_in_named_or_default_browser() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed()

    adapter = MacOSPlatformAdapter(runner=runner)
    assert adapter.open_url("https://www.youtube.com").success
    assert adapter.open_url("https://search.test/?q=YouTube", "Safari").success
    assert calls == [
        ["open", "https://www.youtube.com"],
        ["open", "-a", "Safari", "https://search.test/?q=YouTube"],
    ]


def test_platform_shortcut_passes_text_as_osascript_argument() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed()

    MacOSPlatformAdapter(runner=runner).type_text('hello "world"')
    assert calls[0][-1] == 'hello "world"'
    assert 'hello "world"' not in calls[0][2]


def test_platform_scroll_uses_page_key_for_frontmost_application() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed()

    class SafariPlatform(MacOSPlatformAdapter):
        def frontmost_app(self) -> str | None:
            return "Safari"

    result = SafariPlatform(runner=runner).scroll("down", 600)
    assert result.success
    assert result.message == "Scrolled down in Safari"
    assert "key code 121" in calls[0][2]
    assert "repeat 1 times" in calls[0][2]


def test_platform_clicks_exact_accessible_element_in_frontmost_application() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed(stdout="activated")

    title = 'Imagine Dragons "Believer"'
    result = MacOSPlatformAdapter(runner=runner).click_element(title)
    assert result.success
    assert result.message == f"Opened {title}"
    script = calls[0][2]
    assert "first application process whose frontmost is true" in script
    assert 'perform action "AXPress"' in script
    assert '"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"' in script
    assert calls[0][-1] == title
    assert title not in script


def test_platform_reports_accessibility_element_not_found() -> None:
    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed(stderr="Relay could not find an exact visible element", code=1)

    result = MacOSPlatformAdapter(runner=runner).click_element("Missing video")
    assert not result.success
    assert "could not find" in result.message


def test_whatsapp_message_opens_new_chat_and_requires_ui_verification() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed(stdout="verified")

    result = MacOSPlatformAdapter(runner=runner).send_message(
        "WhatsApp", "Example Contact", "hello"
    )
    assert result.success
    assert result.message == "Sent and verified message to Example Contact"
    script = calls[0][2]
    assert 'keystroke "n" using {command down, control down}' in script
    assert "findMatchingElement" in script
    assert 'perform action "AXPress"' in script
    assert '"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"' in script
    assert "key code 36" in script
    assert "messageFound" in script
    assert calls[0][-3:] == ["WhatsApp", "Example Contact", "hello"]
    assert "Example Contact" not in script
    assert "hello" not in script


def test_unverified_whatsapp_send_is_not_reported_as_success() -> None:
    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed(stderr="could not verify the selected recipient", code=1)

    result = MacOSPlatformAdapter(runner=runner).send_message("WhatsApp", "ABC", "hello")
    assert not result.success
    assert "could not verify" in result.message


def test_unsupported_messaging_app_is_not_driven_by_generic_keystrokes() -> None:
    calls: list[object] = []

    def runner(command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed()

    result = MacOSPlatformAdapter(runner=runner).send_message("Notes", "ABC", "hello")
    assert not result.success
    assert calls == []
