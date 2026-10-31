"""Selects the platform adapter and network probe for the current OS."""

from __future__ import annotations

import sys

from printmedic.core.models import PrintJob, SystemSnapshot
from printmedic.network.probe import NetworkProbe, RealNetworkProbe
from printmedic.platform.base import OpResult, PlatformAdapter, PlatformError


class UnsupportedPlatformAdapter:
    """Honest placeholder for Linux (CUPS) and macOS, which are planned but not implemented."""

    simulated = False

    def __init__(self, platform: str) -> None:
        self.name = f"{platform} (not yet supported)"
        self._platform = platform

    def snapshot(self) -> SystemSnapshot:
        raise PlatformError(f"The {self._platform} adapter is not implemented yet.")

    def list_jobs(self, queue: str) -> list[PrintJob]:
        return []

    def _no(self, *_a: object) -> OpResult:
        return OpResult(False, "failed", f"not supported on {self._platform}")

    resume_queue = set_online = set_default = clear_queue = print_test_page = _no
    remove_job = add_ipp_printer = open_url = _no

    def restart_spooler(self) -> OpResult:
        return self._no()

    def open_printer_settings(self) -> OpResult:
        return self._no()


def create() -> tuple[PlatformAdapter, NetworkProbe]:
    if sys.platform == "win32":
        from printmedic.platform.windows.adapter import WindowsAdapter
        return WindowsAdapter(), RealNetworkProbe()
    name = "macOS" if sys.platform == "darwin" else "Linux"
    return UnsupportedPlatformAdapter(name), RealNetworkProbe()
