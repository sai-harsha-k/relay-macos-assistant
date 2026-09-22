from __future__ import annotations

import importlib
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import rumps  # type: ignore[import-untyped]
from PyObjCTools import AppHelper  # type: ignore[import-untyped]

from local_assistant.app.configuration import ensure_relay_configuration, relay_support_directory
from local_assistant.app.health import HealthReport, HealthService, MacOSPermissionProbe
from local_assistant.app.hotkey import PushToTalkHotkey, QuartzHotkeyBackend
from local_assistant.app.lifecycle import RelayState, SingleInstanceLock, VoiceSession
from local_assistant.app.overlay import OverlayPresenter, OverlaySnapshot
from local_assistant.app.preferences import PreferencesService, RelayPreferences
from local_assistant.app.processes import OwnedProcessRegistry
from local_assistant.audio.recorder import MicrophoneRecorder
from local_assistant.config.settings import Settings
from local_assistant.macos_overlay import RelayOverlayWindow
from local_assistant.macos_settings import RelaySettingsWindow
from local_assistant.observability.logging_setup import configure_logging
from local_assistant.runtime.factory import RuntimeBundle, build_runtime
from local_assistant.runtime.types import PipelineEvent

BUNDLE_IDENTIFIER = "dev.relay.assistant"
REOPEN_NOTIFICATION = "dev.relay.assistant.reopen"


def bundle_smoke_check() -> None:
    """Import dependencies that modulegraph may miss, without starting AppKit."""
    modules = (
        "AVFoundation",
        "CoreFoundation",
        "Quartz",
        "httpx",
        "numpy",
        "playwright.sync_api",
        "pydantic",
        "sounddevice",
        "structlog",
        "typesafe_sdk",
    )
    for module in modules:
        importlib.import_module(module)
    from playwright._impl._driver import compute_driver_executable

    driver, cli = (Path(item) for item in compute_driver_executable())
    missing = [str(path) for path in (driver, cli) if not path.is_file()]
    if missing:
        raise RuntimeError(f"Playwright bundle resources are not real files: {missing}")
    print(f"Relay bundle smoke check passed: {', '.join(modules)}")


def _resource_path(filename: str) -> Path | None:
    try:
        from Foundation import NSBundle  # type: ignore[import-untyped]

        resource_path = NSBundle.mainBundle().resourcePath()
        if resource_path:
            candidate = Path(str(resource_path)) / "assets" / filename
            if candidate.is_file():
                return candidate
    except ImportError:
        pass
    candidate = Path(__file__).resolve().parents[2] / "assets" / filename
    return candidate if candidate.is_file() else None


def activate_existing_instance() -> None:
    try:
        from AppKit import (  # type: ignore[import-untyped]
            NSApplicationActivateIgnoringOtherApps,
            NSRunningApplication,
        )

        applications = NSRunningApplication.runningApplicationsWithBundleIdentifier_(
            BUNDLE_IDENTIFIER
        )
        for application in applications:
            if application.processIdentifier() != os.getpid():
                application.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                break
        from Foundation import NSDistributedNotificationCenter

        NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_(
            REOPEN_NOTIFICATION, None
        )
    except Exception:
        return


class RelayMenuBar(rumps.App):  # type: ignore[misc]
    def __init__(self, config_path: Path) -> None:
        icon = _resource_path("relay-menubar.png")
        super().__init__(
            "Relay",
            title="Relay",
            icon=str(icon) if icon else None,
            template=bool(icon),
            quit_button=None,
        )
        self._config_path = config_path
        self._settings = Settings.load(env_file=config_path)
        self._preferences_service = PreferencesService(relay_support_directory() / "settings.json")
        loaded_preferences = self._preferences_service.load()
        self._preferences = loaded_preferences.model_copy(
            update={"continuous_listening": loaded_preferences.auto_send_messages}
        )
        if loaded_preferences.continuous_listening != self._preferences.continuous_listening:
            self._preferences_service.save(self._preferences)
        configure_logging(self._settings.log_level)
        self._owned_processes = OwnedProcessRegistry()
        self._workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="relay-shell")
        self._shutdown_complete = False
        self._shutdown_event = threading.Event()
        self._reopen_observer: object | None = None
        self._overlay_presenter = OverlayPresenter()
        runtime_settings = self._settings.model_copy(
            update={
                "decision_confidence": self._preferences.jev_confidence_threshold,
                "ollama_model": self._preferences.local_model_name,
                "tts_enabled": True,
            }
        )
        self._runtime: RuntimeBundle = build_runtime(
            runtime_settings,
            confirmation=self._confirm_action,
            pipeline_listener=self._on_pipeline_event,
        )
        self._runtime.controller.update_runtime_settings(
            confidence_threshold=self._preferences.jev_confidence_threshold,
            auto_send_messages=self._preferences.auto_send_messages,
            tts_enabled=self._preferences.tts_enabled,
        )
        self._session = VoiceSession(
            MicrophoneRecorder(activity_listener=self._overlay_presenter.speech_activity),
            self._runtime.controller,
            self._on_state_change,
            self._on_pipeline_event,
            early_confidence=self._settings.early_commit_confidence,
            stability_ms=self._preferences.transcript_stability_ms,
            preview_interval=self._settings.live_preview_interval_ms / 1000,
            utterance_silence_ms=self._preferences.silence_endpoint_ms,
            continuous=self._preferences.auto_send_messages,
            return_to_idle=True,
            release_to_submit=not self._preferences.auto_send_messages,
        )
        self._hotkey = PushToTalkHotkey(
            QuartzHotkeyBackend(),
            self._preferences.push_to_talk_hotkey,
            self._hotkey_pressed,
            self._hotkey_released,
            self._hotkey_error,
        )
        self._overlay = RelayOverlayWindow.alloc().initWithPresenter_callbacks_preferences_(
            self._overlay_presenter,
            (
                self._listen_from_overlay,
                self._pause_from_overlay,
                self._stop_from_overlay,
                self._show_settings,
            ),
            (
                self._preferences.overlay_always_on_top,
                self._preferences.overlay_x,
                self._preferences.overlay_y,
            ),
        )
        self._settings_window = RelaySettingsWindow.alloc().initWithPreferences_onSave_(
            self._preferences, self._apply_preferences
        )
        self._overlay_presenter.set_listener(self._render_overlay)
        self._overlay.show()

        self._start_item = rumps.MenuItem("Start / Push to Talk", callback=self.start_listening)
        self._stop_item = rumps.MenuItem("Stop Listening", callback=self.stop_listening)
        self._stop_item.set_callback(None)
        self._status_item = rumps.MenuItem("Status: Ready")
        self._status_item.set_callback(None)
        self.menu = [
            self._start_item,
            self._stop_item,
            None,
            self._status_item,
            None,
            rumps.MenuItem("Show Relay Panel", callback=self.show_panel),
            rumps.MenuItem("Relay Settings", callback=self.open_settings),
            rumps.MenuItem("Open Secret Configuration", callback=self.open_configuration),
            rumps.MenuItem("Run Diagnostics", callback=self.run_diagnostics),
            None,
            rumps.MenuItem("Quit Relay", callback=self.quit_relay),
        ]
        rumps.events.before_quit.register(self._shutdown)
        self._register_reopen_observer()

    def startup(self) -> None:
        if self._preferences.auto_send_messages:
            self._workers.submit(self._session.start_listening)
        else:
            self._workers.submit(self._hotkey.start)

    def _hotkey_pressed(self) -> None:
        if self._preferences.auto_send_messages:
            return
        if self._session.state in {
            RelayState.IDLE,
            RelayState.PAUSED,
            RelayState.DONE,
            RelayState.ERROR,
        }:
            self._session.start_listening()

    def _hotkey_released(self) -> None:
        if self._preferences.auto_send_messages:
            return
        if self._session.state is RelayState.LISTENING:
            self._session.stop_listening()

    def _hotkey_error(self, message: str) -> None:
        self._overlay_presenter.handle_stage(RelayState.ERROR, message)
        AppHelper.callAfter(self._apply_state, RelayState.ERROR, message)

    def _register_reopen_observer(self) -> None:
        try:
            from Foundation import (
                NSDistributedNotificationCenter,
                NSOperationQueue,
            )

            center = NSDistributedNotificationCenter.defaultCenter()
            self._reopen_observer = center.addObserverForName_object_queue_usingBlock_(
                REOPEN_NOTIFICATION,
                None,
                NSOperationQueue.mainQueue(),
                lambda notification: self._show_menu(),
            )
        except Exception:
            self._reopen_observer = None

    def _show_menu(self) -> None:
        nsapp = getattr(self, "_nsapp", None)
        status_item = getattr(nsapp, "nsstatusitem", None)
        if status_item is not None:
            status_item.popUpStatusItemMenu_(self._menu._menu)

    def start_listening(self, _sender: Any) -> None:
        try:
            self._session.start_listening()
        except Exception as exc:
            rumps.alert("Relay could not start listening", str(exc))

    def stop_listening(self, _sender: Any) -> None:
        try:
            self._session.stop_listening()
        except Exception as exc:
            rumps.alert("Relay could not stop listening", str(exc))

    def _on_state_change(self, state: RelayState, detail: str) -> None:
        self._overlay_presenter.handle_stage(state, detail)
        AppHelper.callAfter(self._apply_state, state, detail)

    def _on_pipeline_event(self, event: PipelineEvent) -> None:
        self._overlay_presenter.handle_event(event)
        detail = event.error or event.result or event.stage.value
        AppHelper.callAfter(self._apply_state, event.stage, detail)

    def _render_overlay(self, snapshot: OverlaySnapshot) -> None:
        AppHelper.callAfter(self._overlay.render, snapshot)

    def _apply_state(self, state: RelayState, detail: str) -> None:
        status = "Ready" if state is RelayState.IDLE else state.value
        self._status_item.title = f"Status: {status}"
        self._status_item._menuitem.setToolTip_(detail)
        self._start_item.set_callback(
            self.start_listening
            if state
            in {
                RelayState.IDLE,
                RelayState.PAUSED,
                RelayState.DONE,
                RelayState.ERROR,
            }
            else None
        )
        self._stop_item.set_callback(self.stop_listening if state is RelayState.LISTENING else None)

    def _listen_from_overlay(self) -> None:
        try:
            if self._session.state is RelayState.PAUSED:
                self._session.resume()
            else:
                self._session.start_listening()
        except Exception as exc:
            rumps.alert("Relay could not start listening", str(exc))

    def _pause_from_overlay(self) -> None:
        try:
            self._session.pause()
        except Exception as exc:
            rumps.alert("Relay could not pause", str(exc))

    def _stop_from_overlay(self) -> None:
        try:
            self._session.stop_current()
        except Exception as exc:
            rumps.alert("Relay could not stop", str(exc))

    def _confirm_action(self, action: object, reason: str) -> bool:
        event = threading.Event()
        response = [False]

        def show_confirmation() -> None:
            if self._shutdown_event.is_set():
                event.set()
                return
            result = rumps.alert(
                "Relay confirmation required",
                f"{reason}\n\nProposed action: {action}",
                ok="Allow Once",
                cancel="Cancel",
            )
            response[0] = result == 1
            event.set()

        AppHelper.callAfter(show_confirmation)
        while not event.wait(0.1):
            if self._shutdown_event.is_set():
                return False
        return response[0]

    def open_settings(self, _sender: Any) -> None:
        self._show_settings()

    def _show_settings(self) -> None:
        self._settings_window.show()

    def show_panel(self, _sender: Any = None) -> None:
        del _sender
        self._overlay.show()

    def open_configuration(self, _sender: Any) -> None:
        subprocess.run(["open", "-t", str(self._config_path)], check=False)

    def _apply_preferences(self, preferences: RelayPreferences) -> None:
        preferences = preferences.model_copy(
            update={"continuous_listening": preferences.auto_send_messages}
        )
        x, y = self._overlay.position()
        preferences = preferences.model_copy(update={"overlay_x": x, "overlay_y": y})
        self._preferences_service.save(preferences)
        self._preferences = preferences
        self._runtime.controller.update_runtime_settings(
            confidence_threshold=preferences.jev_confidence_threshold,
            auto_send_messages=preferences.auto_send_messages,
            tts_enabled=preferences.tts_enabled,
        )
        self._workers.submit(self._apply_interaction_mode, preferences)
        self._overlay.set_always_on_top(preferences.overlay_always_on_top)

    def _apply_interaction_mode(self, preferences: RelayPreferences) -> None:
        if preferences.auto_send_messages:
            self._hotkey.stop()
        self._session.update_runtime_settings(
            continuous=preferences.auto_send_messages,
            stability_ms=preferences.transcript_stability_ms,
            silence_endpoint_ms=preferences.silence_endpoint_ms,
            release_to_submit=not preferences.auto_send_messages,
        )
        if preferences.auto_send_messages:
            return
        if self._hotkey.update_binding(preferences.push_to_talk_hotkey):
            self._overlay_presenter.handle_stage(RelayState.IDLE, "Ready")
            AppHelper.callAfter(
                self._apply_state,
                RelayState.IDLE,
                "Global push-to-talk ready",
            )

    def run_diagnostics(self, _sender: Any = None, *, first_launch: bool = False) -> None:
        del _sender

        def check() -> None:
            report = HealthService(self._settings, MacOSPermissionProbe()).run()
            AppHelper.callAfter(self._show_diagnostics, report, first_launch)

        self._workers.submit(check)

    def _show_diagnostics(self, report: HealthReport, first_launch: bool) -> None:
        title = "Relay first-launch checks" if first_launch else "Relay diagnostics"
        if report.has_errors:
            self._apply_state(RelayState.ERROR, "Diagnostics found required setup")
        rumps.alert(title, report.format(), ok="OK")

    def quit_relay(self, _sender: Any) -> None:
        self._status_item.title = "Status: Stopping"
        self._shutdown()
        rumps.quit_application()

    def _shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self._shutdown_event.set()
        self._hotkey.stop()
        self._session.shutdown()
        self._workers.shutdown(wait=True, cancel_futures=True)
        self._owned_processes.shutdown()
        self._runtime.close()
        x, y = self._overlay.position()
        self._preferences_service.save(
            self._preferences.model_copy(update={"overlay_x": x, "overlay_y": y})
        )
        self._overlay_presenter.close()
        self._settings_window.close()
        self._overlay.close()
        if self._reopen_observer is not None:
            try:
                from Foundation import (
                    NSDistributedNotificationCenter,
                )

                NSDistributedNotificationCenter.defaultCenter().removeObserver_(
                    self._reopen_observer
                )
            finally:
                self._reopen_observer = None


def main() -> None:
    if os.environ.get("RELAY_BUNDLE_SMOKE") == "1":
        bundle_smoke_check()
        return
    # Finder-launched applications do not inherit the user's shell PATH.
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get(
        "PATH", ""
    )
    lock = SingleInstanceLock(relay_support_directory() / "relay.lock")
    if not lock.acquire():
        activate_existing_instance()
        return
    configuration = ensure_relay_configuration()
    relay = RelayMenuBar(configuration.path)
    if configuration.created:
        relay.run_diagnostics(first_launch=True)
    try:
        AppHelper.callAfter(relay.startup)
        relay.run()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
