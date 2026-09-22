from __future__ import annotations

import subprocess


class MacOSTextToSpeechProvider:
    def speak(self, text: str) -> None:
        subprocess.run(["say", text[:1000]], check=False, capture_output=True)


class NullTextToSpeechProvider:
    def speak(self, text: str) -> None:
        del text
