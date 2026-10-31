"""Discovery orchestration: several independent, bounded, cancellable methods."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from printmedic.core.cancel import Cancelled, CancelToken, ProgressFn, no_progress
from printmedic.core.models import DiscoveredEndpoint, PortState, ServiceAdvert, SystemSnapshot
from printmedic.core.validation import subnet_hosts
from printmedic.network import ipp
from printmedic.network.probe import NetworkProbe

METHODS = ("installed", "usb", "mdns", "wsd", "subnet")


@dataclass
class MethodResult:
    method: str
    status: str = "pending"  # pending | running | ok | failed | skipped | cancelled
    count: int = 0
    error: str = ""
    duration_ms: int = 0


@dataclass
class DiscoveryOptions:
    methods: tuple[str, ...] = ("installed", "usb", "mdns", "wsd")
    duration: float = 6.0
    interface_address: str | None = None
    interface_prefix: int = 24


@dataclass
class DiscoveryReport:
    snapshot: SystemSnapshot | None = None
    endpoints: list[DiscoveredEndpoint] = field(default_factory=list)
    methods: dict[str, MethodResult] = field(default_factory=dict)
    cancelled: bool = False


def merge_endpoints(target: dict[str, DiscoveredEndpoint], new: list[DiscoveredEndpoint]) -> None:
    for ep in new:
        existing = target.get(ep.host) or next((e for e in target.values() if ep.uuid and e.uuid == ep.uuid), None)
        if existing is None:
            target[ep.host] = ep
            continue
        existing.sources |= ep.sources
        existing.name = existing.name or ep.name
        existing.model = existing.model or ep.model
        existing.manufacturer = existing.manufacturer or ep.manufacturer
        existing.uuid = existing.uuid or ep.uuid
        known = {(s.protocol, s.port) for s in existing.services}
        existing.services += [s for s in ep.services if (s.protocol, s.port) not in known]


class DiscoveryService:
    def __init__(self, snapshot_fn: Callable[[], SystemSnapshot], net: NetworkProbe,
                 mdns_fn: Callable[[float, CancelToken, str | None], list[DiscoveredEndpoint]] | None = None,
                 wsd_fn: Callable[[float, CancelToken, str | None], list[DiscoveredEndpoint]] | None = None) -> None:
        self._snapshot_fn = snapshot_fn
        self._net = net
        if mdns_fn is None:
            from printmedic.network import mdns
            mdns_fn = mdns.browse
        if wsd_fn is None:
            from printmedic.network import wsd
            wsd_fn = wsd.probe
        self._mdns = mdns_fn
        self._wsd = wsd_fn

    def subnet_sweep(self, address: str, prefix: int, cancel: CancelToken,
                     progress: ProgressFn = no_progress) -> list[DiscoveredEndpoint]:
        """Check only IPP (631) and RAW (9100) on the local /24 around `address`. Opt-in only."""
        hosts = subnet_hosts(address, prefix)
        found: list[DiscoveredEndpoint] = []
        done = 0
        with ThreadPoolExecutor(max_workers=48) as pool:
            futures = {}
            for h in hosts:
                futures[pool.submit(self._check_host, h, cancel)] = h
            for fut in as_completed(futures):
                done += 1
                if done % 16 == 0:
                    progress(f"subnet {done}/{len(hosts)}", done / len(hosts))
                ep = fut.result()
                if ep:
                    found.append(ep)
                if cancel.cancelled:
                    for f in futures:
                        f.cancel()
                    break
        for ep in found:
            if cancel.cancelled:
                break
            if any(s.protocol == "ipp" for s in ep.services):
                try:
                    st = self._net.ipp_status(ep.host, 631, False, 4.0)
                except ipp.IppError:
                    continue
                ep.model = st.make_and_model
                ep.name = st.name or st.make_and_model
                ep.uuid = st.uuid.replace("urn:uuid:", "")
        return found

    def _check_host(self, host: str, cancel: CancelToken) -> DiscoveredEndpoint | None:
        if cancel.cancelled:
            return None
        services = []
        for port, proto in ((631, "ipp"), (9100, "raw")):
            if self._net.tcp_check(host, port, 0.5) == PortState.OPEN:
                services.append(ServiceAdvert(protocol=proto, port=port, source="subnet"))
        if not services:
            return None
        return DiscoveredEndpoint(host=host, services=services, sources={"subnet"},
                                  simulated=getattr(self._net, "simulated", False))

    def run(self, options: DiscoveryOptions, cancel: CancelToken,
            progress: ProgressFn = no_progress,
            on_method: Callable[[MethodResult], None] | None = None) -> DiscoveryReport:
        report = DiscoveryReport()
        report.methods = {m: MethodResult(m, "pending" if m in options.methods else "skipped") for m in METHODS}
        endpoints: dict[str, DiscoveredEndpoint] = {}
        simulated = getattr(self._net, "simulated", False)

        def step(method: str, fn: Callable[[], int]) -> None:
            mr = report.methods[method]
            if method not in options.methods:
                return
            if cancel.cancelled:
                mr.status = "cancelled"
                return
            mr.status = "running"
            if on_method:
                on_method(mr)
            started = time.monotonic()
            try:
                mr.count = fn()
                mr.status = "cancelled" if cancel.cancelled else "ok"
            except Cancelled:
                mr.status = "cancelled"
            except Exception as exc:  # each method fails independently
                mr.status = "failed"
                mr.error = str(exc) or exc.__class__.__name__
            mr.duration_ms = int((time.monotonic() - started) * 1000)
            if on_method:
                on_method(mr)

        def installed() -> int:
            progress("installed", None)
            report.snapshot = self._snapshot_fn()
            return len(report.snapshot.queues)

        def usb() -> int:
            if report.snapshot is None:
                report.snapshot = self._snapshot_fn()
            return len([u for u in report.snapshot.usb_devices if u.is_print_function or u.pnp_class == "Printer"])

        def mdns() -> int:
            progress("mdns", None)
            eps = self._mdns(options.duration, cancel, options.interface_address)
            merge_endpoints(endpoints, eps)
            return len(eps)

        def wsd() -> int:
            progress("wsd", None)
            eps = self._wsd(min(options.duration, 5.0), cancel, options.interface_address)
            merge_endpoints(endpoints, eps)
            return len(eps)

        def subnet() -> int:
            if not options.interface_address:
                raise ValueError("select a network interface for the subnet check")
            progress("subnet", 0.0)
            eps = self.subnet_sweep(options.interface_address, options.interface_prefix, cancel, progress)
            merge_endpoints(endpoints, eps)
            return len(eps)

        step("installed", installed)
        step("usb", usb)
        step("mdns", mdns)
        step("wsd", wsd)
        step("subnet", subnet)
        for ep in endpoints.values():
            ep.simulated = ep.simulated or simulated
        report.endpoints = list(endpoints.values())
        report.cancelled = cancel.cancelled
        return report
