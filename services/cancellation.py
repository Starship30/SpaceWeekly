from __future__ import annotations

from threading import Event
from typing import Callable


class TaskCancelledError(RuntimeError):
    """Raised when the user requests cancellation of report generation."""


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.is_cancelled:
            raise TaskCancelledError("用户取消任务")

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


CancelCheck = Callable[[], None]


def no_cancel() -> None:
    """Default cancellation callback for non-GUI callers."""
