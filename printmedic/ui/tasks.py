"""Background task runner: keeps the UI responsive and every long operation cancellable."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from printmedic.core.cancel import Cancelled, CancelToken
from printmedic.i18n import tr
from printmedic.platform.base import PlatformError

log = logging.getLogger("printmedic")


class _Signals(QObject):
    progress = Signal(str, object)
    done = Signal(object)
    failed = Signal(str, str)  # friendly message, technical details


class Task(QRunnable):
    def __init__(self, fn: Callable[[CancelToken, Callable[[str, float | None], None]], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.cancel = CancelToken()
        self.signals = _Signals()
        self.setAutoDelete(False)

    def _progress(self, msg: str, frac: float | None = None) -> None:
        self.signals.progress.emit(msg, frac)

    def run(self) -> None:
        try:
            result = self.fn(self.cancel, self._progress)
        except Cancelled:
            self.signals.failed.emit(tr("error.cancelled"), "")
        except PlatformError as exc:
            self.signals.failed.emit(tr("error.platform", detail=str(exc)), traceback.format_exc())
        except PermissionError as exc:
            self.signals.failed.emit(tr("error.permission"), str(exc))
        except Exception as exc:
            log.exception("background task failed")
            self.signals.failed.emit(tr("error.unexpected", detail=exc.__class__.__name__), traceback.format_exc())
        else:
            if self.cancel.cancelled:
                self.signals.failed.emit(tr("error.cancelled"), "")
            else:
                self.signals.done.emit(result)


_running: set[Task] = set()


def start(fn: Callable[[CancelToken, Callable[[str, float | None], None]], Any],
          on_done: Callable[[Any], None] | None = None,
          on_failed: Callable[[str, str], None] | None = None,
          on_progress: Callable[[str, Any], None] | None = None) -> Task:
    task = Task(fn)
    if on_done:
        task.signals.done.connect(on_done)
    if on_failed:
        task.signals.failed.connect(on_failed)
    if on_progress:
        task.signals.progress.connect(on_progress)
    task.signals.done.connect(lambda _r: _running.discard(task))
    task.signals.failed.connect(lambda _m, _d: _running.discard(task))
    _running.add(task)
    QThreadPool.globalInstance().start(task)
    return task


def wait_all(timeout_ms: int = 3000) -> None:
    for t in list(_running):
        t.cancel.cancel()
    QThreadPool.globalInstance().waitForDone(timeout_ms)
