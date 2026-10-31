"""Diagnostic orchestration: collect evidence → run selected checks → rank findings."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from printmedic.core.cancel import Cancelled, CancelToken, ProgressFn, no_progress
from printmedic.core.models import ConnectionType, DiscoveredEndpoint, ProbeResult, SystemSnapshot, utcnow
from printmedic.diagnostics.checks import CHECKS, DiagnosticContext
from printmedic.diagnostics.findings import Finding, rank
from printmedic.network.probe import NetworkProbe, probe_host
from printmedic.platform.base import PlatformAdapter
from printmedic.services.inventory import build_devices

ALL_CATEGORIES = frozenset(spec.category for spec in CHECKS)


@dataclass
class DiagnosticSession:
    session_id: str
    started_at: str
    finished_at: str = ""
    target: str | None = None
    target_label: str = ""
    categories: list[str] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    check_log: list[tuple[str, str, int]] = field(default_factory=list)  # (check_id, status, n_findings)
    cancelled: bool = False
    simulated: bool = False
    summary: dict[str, int] = field(default_factory=dict)


class DiagnosticEngine:
    def __init__(self, adapter: PlatformAdapter, net: NetworkProbe) -> None:
        self.adapter = adapter
        self.net = net

    def collect_probes(self, snapshot: SystemSnapshot, hosts: Iterable[tuple[str, int | None]],
                       cancel: CancelToken, progress: ProgressFn) -> dict[str, ProbeResult]:
        probes: dict[str, ProbeResult] = {}
        for host, extra in hosts:
            cancel.raise_if_cancelled()
            if host in probes:
                continue
            progress(f"probe:{host}", None)
            extra_ports = (extra,) if extra else ()
            try:
                probes[host] = probe_host(self.net, host, extra_ports, cancel=cancel)
            except Cancelled:
                raise
            except Exception as exc:
                probes[host] = ProbeResult(host=host, dns_error=str(exc))
        return probes

    def run(self, *, target: str | None = None, categories: Iterable[str] | None = None,
            symptoms: Iterable[str] = (), endpoints: list[DiscoveredEndpoint] | None = None,
            cancel: CancelToken | None = None, progress: ProgressFn = no_progress,
            snapshot: SystemSnapshot | None = None) -> tuple[DiagnosticSession, DiagnosticContext | None]:
        cancel = cancel or CancelToken()
        cats = set(categories) if categories else set(ALL_CATEGORIES)
        session = DiagnosticSession(session_id=uuid.uuid4().hex[:12], started_at=utcnow().isoformat(),
                                    target=target, categories=sorted(cats), symptoms=sorted(symptoms),
                                    simulated=getattr(self.adapter, "simulated", False))
        try:
            progress("collect:snapshot", 0.05)
            snap = snapshot or self.adapter.snapshot()
            session.errors.extend(snap.errors)
            devices = build_devices(snap, endpoints=endpoints)
            scope = [d for d in devices if target is None or d.device_id == target]
            if target and scope:
                session.target_label = scope[0].display_name
            probes: dict[str, ProbeResult] = {}
            if cats & {"network", "device", "consumables", "ports", "drivers", "symptoms"}:
                hosts = [(d.host, d.queue.device_port if d.queue else None) for d in scope
                         if d.host and d.connection != ConnectionType.VIRTUAL]
                progress("collect:network", 0.2)
                probes = self.collect_probes(snap, [(h, p) for h, p in hosts if h], cancel, progress)
            devices = build_devices(snap, probes, endpoints)
            ctx = DiagnosticContext(snapshot=snap, devices=devices, probes=probes,
                                    symptoms=frozenset(symptoms), target=target)
            specs = [s for s in CHECKS if s.category in cats]
            findings: list[Finding] = []
            for i, spec in enumerate(specs):
                cancel.raise_if_cancelled()
                progress(f"check:{spec.check_id}", 0.4 + 0.6 * i / max(1, len(specs)))
                started = time.monotonic()
                try:
                    produced = spec.fn(ctx)
                except Exception as exc:  # a broken check must not hide others' results
                    session.check_log.append((spec.check_id, "error", 0))
                    session.errors.append(f"{spec.check_id}: {exc}")
                    continue
                for f in produced:
                    f.check_id = spec.check_id
                findings.extend(produced)
                _ = time.monotonic() - started
                session.check_log.append((spec.check_id, "ok", len(produced)))
            session.findings = rank(_dedupe(findings))
            progress("done", 1.0)
        except Cancelled:
            session.cancelled = True
            ctx = None
        session.finished_at = utcnow().isoformat()
        session.summary = summarize(session.findings)
        return session, ctx


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str | None, tuple[tuple[str, str], ...]]] = set()
    out = []
    for f in findings:
        key = (f.code, f.target, tuple(sorted(f.params.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def summarize(findings: list[Finding]) -> dict[str, int]:
    out = {"total": len(findings), "confirmed": 0, "likely": 0, "possible": 0, "high_or_worse": 0}
    for f in findings:
        out[f.certainty.value] += 1
        if f.severity >= 3:
            out["high_or_worse"] += 1
    return out
