"""Cooperative cancellation shared by discovery, diagnostics and repairs."""

from __future__ import annotations

import threading
from collections.abc import Callable


class Cancelled(Exception):
    pass


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled()

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`; returns True if cancelled meanwhile."""
        return self._event.wait(seconds)


ProgressFn = Callable[[str, float | None], None]
"""progress(message, fraction 0..1 or None for indeterminate)."""


def no_progress(_msg: str, _frac: float | None = None) -> None:
    return None
