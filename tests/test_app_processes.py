from __future__ import annotations

from local_assistant.app.processes import OwnedProcessRegistry


class FakeProcess:
    def __init__(self) -> None:
        self.running = True
        self.terminated = 0
        self.killed = 0

    def poll(self) -> int | None:
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated += 1
        self.running = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.running = False
        return 0

    def kill(self) -> None:
        self.killed += 1
        self.running = False


def test_shutdown_terminates_only_registered_relay_owned_processes() -> None:
    registry = OwnedProcessRegistry()
    relay_helper = FakeProcess()
    external_ollama = FakeProcess()
    registry.register(relay_helper)
    registry.shutdown()
    assert relay_helper.terminated == 1
    assert external_ollama.terminated == 0


def test_shutdown_is_idempotent() -> None:
    registry = OwnedProcessRegistry()
    helper = FakeProcess()
    registry.register(helper)
    registry.shutdown()
    registry.shutdown()
    assert helper.terminated == 1
