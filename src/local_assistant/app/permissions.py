from __future__ import annotations

import ctypes
from collections.abc import Callable
from typing import Any

_APPLICATION_SERVICES = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)


def macos_accessibility_trusted(
    loader: Callable[[str], Any] = ctypes.CDLL,
) -> bool:
    """Read macOS Accessibility trust through its stable native C API."""
    framework = loader(_APPLICATION_SERVICES)
    check = framework.AXIsProcessTrusted
    check.argtypes = []
    check.restype = ctypes.c_bool
    return bool(check())
