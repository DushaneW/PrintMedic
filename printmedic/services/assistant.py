"""Network connection assistant: "my printer is on Wi-Fi but my computer cannot find it".

A deterministic, step-by-step investigation that ends in one of a fixed set of verdicts and
offers the safest supported next step. It never asks for Wi-Fi passwords and never changes
router or printer network settings.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from printmedic.core.cancel import CancelToken
from printmedic.core.models import DiscoveredEndpoint, ProbeResult, SystemSnapshot
from printmedic.core.validation import ValidationError, normalize_host
from printmedic.network.probe import NetworkProbe, interpret, probe_host
from printmedic.services.discovery import DiscoveryOptions, DiscoveryService

VERDICTS = ("usb_path", "pc_offline", "dns", "routing", "unreachable", "protocol", "os_config",
            "not_installed", "found_not_installed", "discovery", "uncertain")


@dataclass
class Step:
    key: str
    status: str            # "ok" | "warn" | "fail" | "info" | "skipped"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    host: str
    model: str
    ipp_url: str | None
    installed_queue: str | None = None


@dataclass
class AssistantResult:
    verdict: str
    steps: list[Step]
    candidates: list[Candidate] = field(default_factory=list)
    probe: ProbeResult | None = None


def _in_local_subnet(addr: str, snap: SystemSnapshot) -> bool | None:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return None
    nets = []
    for i in snap.interfaces:
        try:
            nets.append(ipaddress.ip_network(f"{i.address}/{i.prefix_length}", strict=False))
        except ValueError:
            continue
    if not nets:
        return None
    return any(ip in n for n in nets if n.version == ip.version)


def run_assistant(*, connection: str, host: str | None, snapshot_fn: Callable[[], SystemSnapshot],
                  net: NetworkProbe, discovery: DiscoveryService, cancel: CancelToken,
                  interface: tuple[str, int] | None = None, duration: float = 5.0,
                  on_step: Callable[[Step], None] = lambda s: None) -> AssistantResult:
    steps: list[Step] = []

    def add(key: str, status: str, **params: Any) -> None:
        s = Step(key, status, params)
        steps.append(s)
        on_step(s)

    # 1. connection type
    if connection == "usb":
        add("assist.step.usb", "info")
        return AssistantResult("usb_path", steps)
    add("assist.step.type", "info", type=connection)

    # 2. OS queues
    snap = snapshot_fn()
    net_queues = [q for q in snap.queues if q.device_host]
    add("assist.step.queues", "ok" if net_queues else "info", n=len(net_queues),
        names=", ".join(q.name for q in net_queues[:4]) or "—")

    # 3. interfaces
    if not snap.interfaces:
        add("assist.step.no_interfaces", "fail")
        return AssistantResult("pc_offline", steps)
    add("assist.step.interfaces", "ok", list=", ".join(f"{i.name} {i.address}/{i.prefix_length}"
                                                       for i in snap.interfaces[:4]))

    # 4. bounded discovery
    cancel.raise_if_cancelled()
    opts = DiscoveryOptions(methods=("mdns", "wsd"), duration=duration,
                            interface_address=interface[0] if interface else None,
                            interface_prefix=interface[1] if interface else 24)
    report = discovery.run(opts, cancel)
    eps: list[DiscoveredEndpoint] = report.endpoints
    failed = [m for m, r in report.methods.items() if r.status == "failed"]
    add("assist.step.discovery", "ok" if eps else "warn", n=len(eps), failed=", ".join(failed) or "—")
    installed_hosts = {q.device_host: q.name for q in snap.queues if q.device_host}
    candidates = []
    for ep in eps:
        url = next((f"ipp://{ep.host}:{s.port}{s.path or '/ipp/print'}" for s in ep.services if s.protocol == "ipp"),
                   None)
        candidates.append(Candidate(ep.host, ep.model or ep.name, url, installed_hosts.get(ep.host)))

    if not host:
        add("assist.step.no_host", "info")
        not_installed = [c for c in candidates if c.installed_queue is None]
        if not_installed:
            add("assist.step.found_new", "ok", n=len(not_installed))
            return AssistantResult("found_not_installed", steps, candidates)
        if candidates:
            add("assist.step.all_installed", "info")
            return AssistantResult("os_config", steps, candidates)
        return AssistantResult("discovery", steps, candidates)

    # 5. resolve / 6. reachability / 7. protocols
    try:
        host = normalize_host(host)
    except ValidationError as exc:
        add("assist.step.invalid_host", "fail", error=str(exc))
        return AssistantResult("uncertain", steps, candidates)
    cancel.raise_if_cancelled()
    res = probe_host(net, host, cancel=cancel)
    verdict = interpret(res)
    if verdict == "dns_failure":
        add("assist.step.dns_fail", "fail", host=host, error=res.dns_error or "")
        return AssistantResult("dns", steps, candidates, res)
    add("assist.step.dns_ok", "ok", host=host, addrs=", ".join(res.addresses))
    local = _in_local_subnet(res.addresses[0], snap) if res.addresses else None
    if verdict in ("no_answer", "no_route"):
        add("assist.step.no_answer", "fail", host=host)
        if local is False:
            add("assist.step.other_subnet", "warn")
            return AssistantResult("routing", steps, candidates, res)
        return AssistantResult("unreachable", steps, candidates, res)
    if verdict == "host_up_no_printing_service":
        add("assist.step.no_service", "fail")
        return AssistantResult("protocol", steps, candidates, res)
    open_ports = ", ".join(str(p) for p, s in sorted(res.ports.items()) if s.value == "open")
    add("assist.step.services", "ok", ports=open_ports, ipp=res.ipp.make_and_model if res.ipp else "—")
    if local is False:
        add("assist.step.other_subnet_ok", "info")

    # 8. classify
    addr_set = set(res.addresses) | {host}
    queue = next((q for h, q in installed_hosts.items() if h in addr_set), None)
    model = res.ipp.make_and_model if res.ipp else ""
    cand = Candidate(host, model, res.ipp.uri if res.ipp else None, queue)
    if queue:
        add("assist.step.queue_exists", "info", queue=queue)
        return AssistantResult("os_config", steps, [cand, *candidates], res)
    return AssistantResult("not_installed", steps, [cand, *candidates], res)
