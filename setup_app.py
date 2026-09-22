from __future__ import annotations

import sys
from pathlib import Path

from py2app.build_app import py2app as Py2AppCommand
from setuptools import setup

ROOT = Path(__file__).resolve().parent
# Editable installs contain a physical fallback package for macOS hidden-.pth
# resilience. Analyze the current source tree so app builds never use a stale
# fallback copy after a source edit.
sys.path.insert(0, str(ROOT / "src"))

APP = ["src/local_assistant/macos_app.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "assets/relay-icon.icns",
    # local_assistant is reached statically from the entry script. Forcing it
    # as a package makes py2app 0.28 flatten local_assistant.logging into the
    # stdlib logging slot and produces a bundle that fails during bootstrap.
    # Playwright's Python code computes paths to its native Node driver and
    # JavaScript package. It cannot run from py2app's python312.zip archive.
    "packages": ["rumps", "playwright"],
    "excludes": ["tkinter", "playwright._impl.__pyinstaller"],
    "plist": {
        "CFBundleName": "Relay",
        "CFBundleDisplayName": "Relay",
        "CFBundleIdentifier": "dev.relay.assistant",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "LSMultipleInstancesProhibited": True,
        "NSMicrophoneUsageDescription": "Relay records audio only while Push to Talk is active.",
        "NSScreenCaptureUsageDescription": (
            "Relay uses screen capture only for requested screenshot actions."
        ),
        "NSAppleEventsUsageDescription": (
            "Relay uses macOS automation for explicitly requested desktop actions."
        ),
    },
}


class RelayPy2AppCommand(Py2AppCommand):  # type: ignore[misc]
    """py2app bundles the synchronized environment; it must not install requirements."""

    def finalize_options(self) -> None:
        self.distribution.install_requires = []
        super().finalize_options()


setup(
    name="Relay",
    version="0.1.0",
    app=APP,
    data_files=[("assets", ["assets/relay-menubar.png"])],
    options={"py2app": OPTIONS},
    cmdclass={"py2app": RelayPy2AppCommand},
)
