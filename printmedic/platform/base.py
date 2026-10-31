"""The contract every OS adapter implements.

Read methods must never change system state. Mutating methods are only called by the repair
executor after explicit user consent, and must return an `OpResult` rather than raising for
expected failures (access denied, not found, timeout).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from printmedic.core.models import PrintJob, SystemSnapshot


@dataclass
class OpResult:
    ok: bool
    code: str = "ok"           # "ok" | "access_denied" | "not_found" | "timeout" | "failed" | "cancelled_by_user"
    message: str = ""
    details: dict[str, str] = field(default_factory=dict)


class PlatformError(RuntimeError):
    """The adapter could not talk to the OS at all (not a per-item failure)."""


@runtime_checkable
class PlatformAdapter(Protocol):
    name: str
    simulated: bool

    # --- read-only -------------------------------------------------------------------------
    def snapshot(self) -> SystemSnapshot: ...
    def list_jobs(self, queue: str) -> list[PrintJob]: ...

    # --- state-changing (consent required, see repair.executor) ----------------------------
    def resume_queue(self, queue: str) -> OpResult: ...
    def set_online(self, queue: str) -> OpResult: ...
    def set_default(self, queue: str) -> OpResult: ...
    def remove_job(self, queue: str, job_id: int) -> OpResult: ...
    def clear_queue(self, queue: str) -> OpResult: ...
    def restart_spooler(self) -> OpResult: ...
    def print_test_page(self, queue: str) -> OpResult: ...
    def add_ipp_printer(self, name: str, url: str) -> OpResult: ...
    def open_url(self, url: str) -> OpResult: ...
    def open_printer_settings(self) -> OpResult: ...
