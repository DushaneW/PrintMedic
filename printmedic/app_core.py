"""UI-independent application core: owns services and the latest known state.

Both the Qt UI and the command-line interface drive PrintMedic through this class.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

from printmedic.core.cancel import CancelToken, ProgressFn, no_progress
from printmedic.core.models import Device, DiscoveredEndpoint, ProbeResult, SystemSnapshot
from printmedic.diagnostics.engine import DiagnosticEngine, DiagnosticSession
from printmedic.network.probe import NetworkProbe, probe_host
from printmedic.platform import factory
from printmedic.platform.base import PlatformAdapter
from printmedic.repair.actions import Action, Env
from printmedic.repair.executor import Consent, RepairExecutor, RepairRecord
from printmedic.repair.planner import PlanItem, build_plan
from printmedic.services.discovery import DiscoveryOptions, DiscoveryReport, DiscoveryService
from printmedic.services.history import HistoryStore
from printmedic.services.inventory import build_devices
from printmedic.services.settings import Settings, SettingsStore, data_dir


class AppCore:
    def __init__(self, settings: Settings | None = None, settings_store: SettingsStore | None = None,
                 history_path: Path | str | None = None,
                 backend: tuple[PlatformAdapter, NetworkProbe, DiscoveryService | None] | None = None) -> None:
        self._backend = backend
        self.settings_store = settings_store or SettingsStore()
        self.settings = settings or self.settings_store.load()
        self.history = HistoryStore(history_path or data_dir() / "history.db")
        self.history.apply_retention(self.settings.retention_days)
        self._lock = threading.Lock()
        self.snapshot: SystemSnapshot | None = None
        self.endpoints: list[DiscoveredEndpoint] = []
        self.probes: dict[str, ProbeResult] = {}
        self.devices: list[Device] = []
        self.last_session: DiagnosticSession | None = None
        self.plan: list[PlanItem] = []
        self.active_repairs: dict[str, str] = {}  # action key -> label, for the Overview page
        self.last_error: str | None = None
        self._build_backend()

    # --- backend selection -----------------------------------------------------------------
    def _build_backend(self) -> None:
        self.adapter: PlatformAdapter
        self.net: NetworkProbe
        if self._backend is not None:  # injected by automated tests
            self.adapter, self.net, discovery = self._backend
        else:
            self.adapter, self.net = factory.create()
            discovery = None
        self.discovery = discovery or DiscoveryService(self.adapter.snapshot, self.net)
        self.engine = DiagnosticEngine(self.adapter, self.net)
        self.executor = RepairExecutor(Env(self.adapter, self.net), self.history)
        self.snapshot = None
        self.endpoints = []
        self.probes = {}
        self.devices = []
        self.plan = []
        self.last_session = None

    def save_settings(self) -> None:
        self.settings_store.save(self.settings)

    # --- state -----------------------------------------------------------------------------
    def _rebuild_devices(self) -> None:
        if self.snapshot is None:
            self.devices = []
            return
        self.devices = build_devices(self.snapshot, self.probes, self.endpoints)

    def refresh(self) -> list[Device]:
        snap = self.adapter.snapshot()
        with self._lock:
            self.snapshot = snap
            self._rebuild_devices()
            self.last_error = None
            return list(self.devices)

    def discover(self, options: DiscoveryOptions, cancel: CancelToken, progress: ProgressFn = no_progress,
                 on_method=None) -> DiscoveryReport:
        report = self.discovery.run(options, cancel, progress, on_method)
        with self._lock:
            if report.snapshot is not None:
                self.snapshot = report.snapshot
            known = {e.host for e in self.endpoints}
            for ep in report.endpoints:
                if ep.host not in known:
                    self.endpoints.append(ep)
                else:
                    idx = next(i for i, e in enumerate(self.endpoints) if e.host == ep.host)
                    self.endpoints[idx] = ep
            self._rebuild_devices()
        return report

    def probe(self, host: str, extra_ports: Iterable[int] = (), cancel: CancelToken | None = None) -> ProbeResult:
        res = probe_host(self.net, host, tuple(extra_ports), cancel=cancel)
        with self._lock:
            self.probes[res.host] = res
            self._rebuild_devices()
        return res

    def device(self, device_id: str) -> Device | None:
        return next((d for d in self.devices if d.device_id == device_id), None)

    # --- diagnostics & repair --------------------------------------------------------------
    def diagnose(self, *, target: str | None, categories: Iterable[str] | None, symptoms: Iterable[str],
                 cancel: CancelToken, progress: ProgressFn = no_progress) -> DiagnosticSession:
        session, ctx = self.engine.run(target=target, categories=categories, symptoms=symptoms,
                                       endpoints=self.endpoints, cancel=cancel, progress=progress)
        if ctx is not None:
            with self._lock:
                self.snapshot = ctx.snapshot
                self.probes.update(ctx.probes)
                self._rebuild_devices()
                self.plan = build_plan(session.findings, ctx.snapshot)
        self.last_session = session
        self.history.save_session(session)
        return session

    def replan(self) -> list[PlanItem]:
        if self.last_session is None:
            self.plan = []
            return self.plan
        snap = self.adapter.snapshot()
        with self._lock:
            self.snapshot = snap
            self._rebuild_devices()
        self.plan = build_plan(self.last_session.findings, snap)
        return self.plan

    def run_action(self, action: Action, consent: Consent | None, finding_codes: list[str] | None = None
                   ) -> RepairRecord:
        label = action.key
        self.active_repairs[action.key] = label
        try:
            rec = self.executor.run(action, consent, session_id=self.last_session.session_id
                                    if self.last_session else None, finding_codes=finding_codes)
        finally:
            self.active_repairs.pop(action.key, None)
        try:
            self.refresh()
        except Exception as exc:
            self.last_error = str(exc)
        return rec
