from __future__ import annotations

import subprocess
from typing import Protocol


class ManagedProcess(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def kill(self) -> None: ...


class OwnedProcessRegistry:
    """Tracks only helpers started by Relay; external services are never registered."""

    def __init__(self) -> None:
        self._processes: list[ManagedProcess] = []

    def register(self, process: ManagedProcess) -> None:
        self._processes.append(process)

    def shutdown(self, timeout_seconds: float = 3.0) -> None:
        processes, self._processes = self._processes, []
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=timeout_seconds)
            except (TimeoutError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=timeout_seconds)
