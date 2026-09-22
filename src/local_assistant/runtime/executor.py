from __future__ import annotations

from local_assistant.actions.models import (
    Action,
    AskUser,
    ClickElement,
    CloseApp,
    FindFile,
    FocusApp,
    GenerateText,
    KeyboardShortcut,
    MediaControl,
    OpenApp,
    OpenFolder,
    OpenUrl,
    PressKey,
    ReadClipboard,
    Scroll,
    SearchWeb,
    SendMessage,
    SetVolume,
    TakeScreenshot,
    TypeText,
    WriteClipboard,
)
from local_assistant.runtime.errors import AssistantError
from local_assistant.runtime.types import (
    BrowserAdapter,
    ExecutionResult,
    PlatformAdapter,
    WriterProvider,
)


class ActionExecutor:
    def __init__(
        self,
        platform: PlatformAdapter,
        browser: BrowserAdapter,
        writer: WriterProvider,
    ) -> None:
        self._platform = platform
        self._browser = browser
        self._writer = writer

    def execute(self, action: Action) -> ExecutionResult:
        try:
            if isinstance(action, OpenApp):
                return self._platform.open_app(action.app_name)
            if isinstance(action, CloseApp):
                return self._platform.close_app(action.app_name)
            if isinstance(action, FocusApp):
                return self._platform.focus_app(action.app_name)
            if isinstance(action, OpenUrl):
                return self._browser.open_url(str(action.url), action.browser_app)
            if isinstance(action, SearchWeb):
                query = action.query
                if query is None:
                    query = self._writer.generate(action.query_generation_instruction or "")
                result = self._browser.search_web(query, action.browser_app)
                if action.query_generation_instruction is not None:
                    return ExecutionResult(
                        result.success,
                        result.message,
                        data={"generated_query": query, "result": result.data},
                        duration_ms=result.duration_ms,
                    )
                return result
            if isinstance(action, TypeText):
                return self._platform.type_text(action.text)
            if isinstance(action, GenerateText):
                text = self._writer.generate(action.instruction)
                if action.type_after_generation:
                    typed = self._platform.type_text(text)
                    return ExecutionResult(
                        typed.success,
                        "Generated and typed text" if typed.success else typed.message,
                        data=text,
                        duration_ms=typed.duration_ms,
                    )
                return ExecutionResult(True, "Generated text", data=text)
            if isinstance(action, PressKey):
                return self._platform.press_key(action.key)
            if isinstance(action, KeyboardShortcut):
                return self._platform.keyboard_shortcut(action.keys)
            if isinstance(action, ClickElement):
                return self._platform.click_element(action.selector)
            if isinstance(action, Scroll):
                return self._platform.scroll(action.direction, action.amount)
            if isinstance(action, SetVolume):
                return self._platform.set_volume(action.level)
            if isinstance(action, MediaControl):
                return self._platform.media_control(action.command)
            if isinstance(action, OpenFolder):
                return self._platform.open_folder(action.path)
            if isinstance(action, FindFile):
                return self._platform.find_file(action.query, action.root)
            if isinstance(action, ReadClipboard):
                return self._platform.read_clipboard()
            if isinstance(action, WriteClipboard):
                return self._platform.write_clipboard(action.text)
            if isinstance(action, TakeScreenshot):
                return self._platform.take_screenshot(action.path)
            if isinstance(action, SendMessage):
                content = action.content
                if content is None:
                    content = self._writer.generate(action.generation_instruction or "")
                return self._platform.send_message(action.app_name, action.recipient, content)
            if isinstance(action, AskUser):
                return ExecutionResult(False, action.question, data={"reason": action.reason})
        except AssistantError as exc:
            return ExecutionResult(False, str(exc))
        except Exception as exc:  # adapter boundary: never crash the voice loop
            return ExecutionResult(False, f"Action failed: {exc}")
        return ExecutionResult(False, f"No executor for {action.kind}")
