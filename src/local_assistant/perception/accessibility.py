from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccessibilityElement:
    role: str
    title: str


class MacOSAccessibilityPerception:
    """Small AX-backed surface; future perception can replace it without changing routing."""

    def frontmost_controls(self) -> list[AccessibilityElement]:
        script = """
tell application "System Events"
  set frontProcess to first application process whose frontmost is true
  set output to {}
  try
    repeat with itemRef in (UI elements of front window of frontProcess)
      set end of output to ((role of itemRef as text) & tab & (description of itemRef as text))
    end repeat
  end try
  return output as text
end tell
"""
        completed = subprocess.run(
            ["osascript", "-e", script], check=False, capture_output=True, text=True, timeout=10
        )
        if completed.returncode != 0:
            return []
        controls: list[AccessibilityElement] = []
        for line in completed.stdout.splitlines():
            role, _, title = line.partition("\t")
            if role or title:
                controls.append(AccessibilityElement(role=role, title=title))
        return controls
