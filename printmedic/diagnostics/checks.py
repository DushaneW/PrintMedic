"""Diagnostic checks. Each check is a pure function of `DiagnosticContext` → findings.

Rules of evidence
-----------------
* CONFIRMED only for facts reported by the OS/device or verified by us.
* LIKELY when two independent observations point the same way.
* POSSIBLE for hypotheses (user symptoms without corroboration, heuristics).
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from printmedic.core.models import (
    Certainty,
    ConnectionType,
    Device,
    PortState,
    PrinterQueue,
    ProbeResult,
    QueueState,
    Severity,
    StatusOrigin,
    SystemSnapshot,
    utcnow,
)
from printmedic.diagnostics.findings import ActionRef, Evidence, Finding
from printmedic.network.probe import interpret
from printmedic.services.inventory import usb_matches_queue
from printmedic.vendors.profiles import detect_vendor, profile_by_id

OS = StatusOrigin.REPORTED_OS
DEV = StatusOrigin.REPORTED_DEVICE
INF = StatusOrigin.INFERRED
USR = StatusOrigin.USER
SPOOLER = "Windows print spooler"
PNP = "Plug and Play"
NET = "network probe"
IPP = "IPP"

SYMPTOMS = ("not_found", "nothing_prints", "paper_jam", "blank_pages", "missing_color", "streaks",
            "faded", "misaligned", "offline_message")

STALE_JOB_MINUTES = 30


@dataclass
class DiagnosticContext:
    snapshot: SystemSnapshot
    devices: list[Device]
    probes: dict[str, ProbeResult] = field(default_factory=dict)
    symptoms: frozenset[str] = frozenset()
    target: str | None = None  # device_id, or None for all devices
    now: _dt.datetime = field(default_factory=utcnow)

    def devices_in_scope(self) -> list[Device]:
        if self.target is None:
            return self.devices
        return [d for d in self.devices if d.device_id == self.target]

    def queues_in_scope(self) -> list[PrinterQueue]:
        return [d.queue for d in self.devices_in_scope() if d.queue is not None]

    def physical_queues_in_scope(self) -> list[PrinterQueue]:
        return [q for q in self.queues_in_scope() if not q.is_virtual]

    def device_for_queue(self, name: str) -> Device | None:
        return next((d for d in self.devices if d.queue and d.queue.name == name), None)


CheckFn = Callable[[DiagnosticContext], list[Finding]]


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    category: str
    fn: CheckFn
    needs_network: bool = False


CHECKS: list[CheckSpec] = []


def check(check_id: str, category: str, needs_network: bool = False) -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        CHECKS.append(CheckSpec(check_id, category, fn, needs_network))
        return fn
    return deco


def _vendor_url_action(device: Device | None) -> ActionRef | None:
    if not device:
        return None
    return ActionRef.of("open_support_url", url=profile_by_id(device.vendor_id).support_url,
                        vendor=device.vendor_name)


# --------------------------------------------------------------------------------------------
@check("spooler", "service")
def check_spooler(ctx: DiagnosticContext) -> list[Finding]:
    sp = ctx.snapshot.spooler
    if sp is None:
        return [Finding("PM-SPL-003", Severity.MEDIUM, Certainty.CONFIRMED, "Spooler",
                        [Evidence("spooler status could not be read", SPOOLER, OS)])]
    out = []
    if sp.start_type.lower() == "disabled":
        out.append(Finding("PM-SPL-002", Severity.CRITICAL, Certainty.CONFIRMED, "Spooler",
                           [Evidence(f"start type = {sp.start_type}", "Service Control Manager", OS)]))
    elif not sp.running:
        queued = sum(len(q.jobs) for q in ctx.snapshot.queues)
        out.append(Finding("PM-SPL-001", Severity.CRITICAL, Certainty.CONFIRMED, "Spooler",
                           [Evidence(f"service state = {sp.state}", "Service Control Manager", OS)],
                           {"jobs": str(queued)}, [ActionRef.of("restart_spooler")]))
    return out


@check("no_printers", "queues")
def check_no_printers(ctx: DiagnosticContext) -> list[Finding]:
    if ctx.target is not None:
        return []
    physical = ctx.snapshot.physical_queues
    usb_print = [u for u in ctx.snapshot.usb_devices if u.is_print_function]
    if physical or usb_print:
        return []
    ev = [Evidence(f"{len(ctx.snapshot.queues)} queue(s), all virtual", SPOOLER, OS),
          Evidence("no USB printer-class devices present", PNP, OS)]
    acts = [ActionRef.of("open_printer_settings")]
    return [Finding("PM-DISC-001", Severity.MEDIUM, Certainty.CONFIRMED, None, ev, {}, acts)]


@check("usb_devices", "usb")
def check_usb(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    in_scope = {d.usb.instance_id for d in ctx.devices_in_scope() if d.usb}
    for d in ctx.devices_in_scope():
        u = d.usb
        if not u or not u.present:
            continue
        name = u.name or u.instance_id
        if u.driver_missing:
            acts = [a for a in (_vendor_url_action(d), ActionRef.of("open_printer_settings")) if a]
            out.append(Finding("PM-USB-001", Severity.HIGH, Certainty.CONFIRMED, name,
                               [Evidence("PnP problem code 28 (drivers not installed)", PNP, OS),
                                Evidence(f"hardware id {u.hardware_ids[0] if u.hardware_ids else '?'}", PNP, OS)],
                               {"device": name}, acts))
        elif u.problem_code:
            out.append(Finding("PM-USB-003", Severity.MEDIUM, Certainty.CONFIRMED, name,
                               [Evidence(f"PnP problem code {u.problem_code}, status {u.status}", PNP, OS)],
                               {"device": name, "code": str(u.problem_code)}))
        elif d.queue is None and u.instance_id in in_scope and u.is_print_function:
            out.append(Finding("PM-USB-002", Severity.MEDIUM, Certainty.LIKELY, name,
                               [Evidence("USB printer function present and working", PNP, OS),
                                Evidence("no installed queue name/driver matches this device", "PrintMedic", INF)],
                               {"device": name}, [ActionRef.of("open_printer_settings")]))
    return out


@check("queue_state", "queues")
def check_queue_state(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    physical_flags = {"paperjam": "paper_jam", "paperout": "paper_out", "dooropen": "door_open",
                      "notoner": "no_toner", "tonerlow": "toner_low", "outputbinfull": "output_bin_full",
                      "paperproblem": "paper_problem", "manualfeed": "manual_feed"}
    for q in ctx.queues_in_scope():
        flags = ", ".join(q.status_flags) or "(none)"
        ev_flags = Evidence(f"queue status flags = {flags}", SPOOLER, OS)
        if q.state == QueueState.PAUSED:
            out.append(Finding("PM-Q-001", Severity.HIGH, Certainty.CONFIRMED, q.name, [ev_flags],
                               {"queue": q.name}, [ActionRef.of("resume_queue", queue=q.name)]))
        if q.work_offline:
            out.append(Finding("PM-Q-002", Severity.HIGH, Certainty.CONFIRMED, q.name,
                               [Evidence("'Use printer offline' is enabled (WorkOffline = true)", SPOOLER, OS)],
                               {"queue": q.name}, [ActionRef.of("set_online", queue=q.name)]))
        elif "offline" in {f.lower() for f in q.status_flags} or q.detected_error == "offline":
            out.append(Finding("PM-Q-003", Severity.HIGH, Certainty.CONFIRMED, q.name,
                               [ev_flags, Evidence(f"detected error state = {q.detected_error}", SPOOLER, OS)]
                               if q.detected_error else [ev_flags],
                               {"queue": q.name}, [ActionRef.of("rediscover", queue=q.name)]
                               if q.device_host else []))
        for f in q.status_flags:
            cond = physical_flags.get(f.lower())
            if cond:
                sev = Severity.LOW if cond == "toner_low" else Severity.HIGH
                out.append(Finding("PM-Q-005", sev, Certainty.CONFIRMED, q.name,
                                   [ev_flags], {"queue": q.name, "condition": cond},
                                   [ActionRef.of("guided_physical", queue=q.name, condition=cond)]))
        if q.detected_error and q.detected_error not in ("offline", "other"):
            out.append(Finding("PM-Q-005", Severity.MEDIUM, Certainty.CONFIRMED, q.name,
                               [Evidence(f"Win32 DetectedErrorState = {q.detected_error}", SPOOLER, OS)],
                               {"queue": q.name, "condition": q.detected_error},
                               [ActionRef.of("guided_physical", queue=q.name, condition=q.detected_error)]))
        if q.state == QueueState.USER_INTERVENTION or (q.state == QueueState.ERROR and not any(
                physical_flags.get(f.lower()) for f in q.status_flags)):
            ev = [ev_flags]
            if q.detected_error:
                ev.append(Evidence(f"Win32 DetectedErrorState = {q.detected_error}", SPOOLER, OS))
            out.append(Finding("PM-Q-004", Severity.HIGH, Certainty.CONFIRMED, q.name, ev,
                               {"queue": q.name, "flags": flags},
                               [ActionRef.of("guided_physical", queue=q.name, condition="attention")]))
        if q.state == QueueState.UNKNOWN:
            out.append(Finding("PM-STAT-001", Severity.LOW, Certainty.CONFIRMED, q.name, [ev_flags],
                               {"queue": q.name, "flags": flags}))
    return out


@check("jobs", "jobs")
def check_jobs(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    for q in ctx.queues_in_scope():
        errored = [j for j in q.jobs if j.is_error]
        if errored:
            acts = [ActionRef.of("remove_job", queue=q.name, job_id=j.job_id) for j in errored]
            acts.append(ActionRef.of("clear_queue", queue=q.name))
            ev = [Evidence(f"job #{j.job_id} status = {', '.join(j.status)}", SPOOLER, OS) for j in errored]
            out.append(Finding("PM-JOB-001", Severity.HIGH, Certainty.CONFIRMED, q.name, ev,
                               {"queue": q.name, "count": str(len(errored))}, acts))
        stale = [j for j in q.jobs if not j.is_error and (j.age_minutes(ctx.now) or 0) > STALE_JOB_MINUTES]
        if stale and q.state != QueueState.PRINTING:
            oldest = int(max(j.age_minutes(ctx.now) or 0 for j in stale))
            ev = [Evidence(f"{len(stale)} job(s) waiting, oldest {oldest} min", SPOOLER, OS),
                  Evidence(f"queue state = {q.state.value}", SPOOLER, OS)]
            certainty = Certainty.LIKELY if q.state != QueueState.READY else Certainty.POSSIBLE
            acts = [ActionRef.of("remove_job", queue=q.name, job_id=j.job_id) for j in stale]
            acts.append(ActionRef.of("clear_queue", queue=q.name))
            out.append(Finding("PM-JOB-002", Severity.MEDIUM, certainty, q.name, ev,
                               {"queue": q.name, "count": str(len(stale)), "minutes": str(oldest)}, acts))
    return out


@check("ports", "ports")
def check_ports(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    port_names = {p.name for p in ctx.snapshot.ports}
    print_usb = [u for u in ctx.snapshot.usb_devices if u.present and (u.is_print_function or u.pnp_class == "Printer")]
    for q in ctx.physical_queues_in_scope():
        if port_names and q.connection not in (ConnectionType.IPP, ConnectionType.SHARED, ConnectionType.WSD) \
                and q.port not in port_names:
            out.append(Finding("PM-PORT-001", Severity.HIGH, Certainty.CONFIRMED, q.name,
                               [Evidence(f"queue port '{q.port}' is not in the installed port list", SPOOLER, OS)],
                               {"queue": q.name, "port": q.port}))
        if q.connection == ConnectionType.USB:
            matched = any(usb_matches_queue(u, q.name, q.driver) >= 0.5 for u in print_usb)
            if not matched:
                out.append(Finding("PM-PORT-002", Severity.MEDIUM, Certainty.POSSIBLE, q.name,
                                   [Evidence(f"queue uses USB port {q.port}", SPOOLER, OS),
                                    Evidence("no present USB printer device matches this queue", PNP, INF)],
                                   {"queue": q.name, "port": q.port}))
        probe = ctx.probes.get(q.device_host or "")
        if probe and q.connection == ConnectionType.TCPIP and probe.ipp is not None:
            port = q.device_port or 9100
            port_state = probe.ports.get(port)
            if port_state in (PortState.REFUSED, PortState.TIMEOUT):
                out.append(Finding("PM-PORT-003", Severity.HIGH, Certainty.LIKELY, q.name,
                                   [Evidence(f"queue sends to TCP {port}: {port_state.value if port_state else '?'}",
                                             NET, OS),
                                    Evidence(f"device answers IPP at {probe.ipp.uri}", IPP, DEV)],
                                   {"queue": q.name, "port": str(port), "host": q.device_host or ""},
                                   [ActionRef.of("add_ipp_printer", host=q.device_host, url=probe.ipp.uri,
                                                 name=_suggest_name(probe.ipp.make_and_model or q.name))]))
        if probe and q.connection == ConnectionType.WSD and probe.ipp is not None:
            out.append(Finding("PM-PORT-004", Severity.INFO, Certainty.POSSIBLE, q.name,
                               [Evidence("queue uses a WSD port", SPOOLER, OS),
                                Evidence(f"device also answers IPP at {probe.ipp.uri}", IPP, DEV)],
                               {"queue": q.name, "host": q.device_host or ""},
                               [ActionRef.of("add_ipp_printer", host=q.device_host, url=probe.ipp.uri,
                                             name=_suggest_name(probe.ipp.make_and_model or q.name))]))
    return out


def _suggest_name(model: str) -> str:
    clean = re.sub(r"[^\w .()-]", "", model).strip() or "Printer"
    return f"{clean} (IPP)"


_COPY = re.compile(r"\s*\((copy|kopia)\s*\d+\)\s*$", re.I)


@check("duplicates", "queues")
def check_duplicates(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    queues = ctx.snapshot.physical_queues
    scope = {q.name for q in ctx.queues_in_scope()}
    for q in queues:
        if q.name not in scope:
            continue
        m = _COPY.search(q.name)
        base = _COPY.sub("", q.name)
        twins = [o for o in queues if o is not q and _COPY.sub("", o.name) == base]
        same_target = [o for o in queues if o is not q and o.device_host and o.device_host == q.device_host]
        if m and twins:
            out.append(Finding("PM-Q-006", Severity.LOW, Certainty.CONFIRMED, q.name,
                               [Evidence(f"'{q.name}' duplicates '{twins[0].name}'", SPOOLER, OS)],
                               {"queue": q.name, "other": twins[0].name}))
        elif same_target and q.name < same_target[0].name:
            out.append(Finding("PM-Q-006", Severity.LOW, Certainty.POSSIBLE, q.name,
                               [Evidence(f"'{q.name}' and '{same_target[0].name}' point to the same device",
                                         SPOOLER, INF)],
                               {"queue": q.name, "other": same_target[0].name}))
    return out


@check("default_printer", "defaults")
def check_default(ctx: DiagnosticContext) -> list[Finding]:
    if ctx.target is not None:
        return []
    physical = ctx.snapshot.physical_queues
    default = ctx.snapshot.default_queue
    healthy = [q for q in physical if q.state in (QueueState.READY, QueueState.PRINTING, QueueState.POWER_SAVE)]
    if default is None and ctx.snapshot.queues:
        acts = [ActionRef.of("set_default", queue=q.name) for q in (healthy or physical)[:3]]
        return [Finding("PM-DEF-002", Severity.LOW, Certainty.CONFIRMED, None,
                        [Evidence("no queue is marked as default", SPOOLER, OS)], {}, acts)]
    if default and default.is_virtual and physical:
        acts = [ActionRef.of("set_default", queue=q.name) for q in (healthy or physical)[:3]]
        return [Finding("PM-DEF-001", Severity.LOW, Certainty.CONFIRMED, default.name,
                        [Evidence(f"default printer is virtual: {default.name}", SPOOLER, OS),
                         Evidence(f"{len(physical)} physical queue(s) installed", SPOOLER, OS)],
                        {"queue": default.name}, acts)]
    return []


@check("drivers", "drivers")
def check_drivers(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    names = {d.name: d for d in ctx.snapshot.drivers}
    for dev in ctx.devices_in_scope():
        q = dev.queue
        if not q or q.is_virtual:
            continue
        drv = names.get(q.driver)
        if names and drv is None:
            out.append(Finding("PM-DRV-001", Severity.MEDIUM, Certainty.CONFIRMED, q.name,
                               [Evidence(f"driver '{q.driver}' not in installed driver list", SPOOLER, OS)],
                               {"queue": q.name, "driver": q.driver},
                               [a for a in (_vendor_url_action(dev),) if a]))
            continue
        if drv is None:
            continue
        generic = re.search(r"microsoft|ipp class|universal print|mopria|generic", f"{drv.manufacturer} {drv.name}",
                            re.I)
        reported = dev.probe.ipp.make_and_model if dev.probe and dev.probe.ipp else ""
        if reported and not generic:
            dv = detect_vendor(drv.manufacturer, drv.name).vendor_id
            rv = detect_vendor(reported).vendor_id
            if "generic" not in (dv, rv) and dv != rv:
                out.append(Finding("PM-DRV-002", Severity.MEDIUM, Certainty.LIKELY, q.name,
                                   [Evidence(f"driver manufacturer = {drv.manufacturer}", SPOOLER, OS),
                                    Evidence(f"device reports model = {reported}", IPP, DEV)],
                                   {"queue": q.name, "driver": drv.name, "model": reported},
                                   [a for a in (_vendor_url_action(dev),) if a]))
    return out


# --------------------------------------------------------------------------------------------
def _local_networks(ctx: DiagnosticContext) -> list[ipaddress.IPv4Network]:
    nets = []
    for i in ctx.snapshot.interfaces:
        try:
            nets.append(ipaddress.ip_network(f"{i.address}/{i.prefix_length}", strict=False))
        except ValueError:
            continue
    return [n for n in nets if isinstance(n, ipaddress.IPv4Network)]


@check("network", "network", needs_network=True)
def check_network(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    nets = _local_networks(ctx)
    for dev in ctx.devices_in_scope():
        host = dev.host
        if not host or dev.connection == ConnectionType.VIRTUAL:
            continue
        label = dev.queue.name if dev.queue else dev.display_name
        if not ctx.snapshot.interfaces:
            out.append(Finding("PM-NET-006", Severity.HIGH, Certainty.CONFIRMED, label,
                               [Evidence("no active IPv4 network interface", "network configuration", OS)],
                               {"queue": label}))
            continue
        probe = ctx.probes.get(host)
        if probe is None:
            continue
        verdict = interpret(probe)
        port_ev = Evidence(
            "TCP " + ", ".join(f"{p}={s.value}" for p, s in sorted(probe.ports.items())), NET, INF)
        acts = [ActionRef.of("rediscover", queue=dev.queue.name)] if dev.queue else []
        if verdict == "dns_failure":
            out.append(Finding("PM-NET-001", Severity.HIGH, Certainty.CONFIRMED, label,
                               [Evidence(f"cannot resolve '{host}': {probe.dns_error}", "DNS resolver", OS)],
                               {"queue": label, "host": host}, acts))
            continue
        try:
            addr = ipaddress.ip_address(probe.addresses[0])
        except (ValueError, IndexError):
            addr = None
        off_subnet = bool(addr and addr.version == 4 and nets and not any(addr in n for n in nets))
        if verdict in ("no_answer", "no_route"):
            ev = [port_ev]
            if dev.queue:
                ev.append(Evidence(f"queue state = {dev.queue.state.value}", SPOOLER, OS))
            out.append(Finding("PM-NET-002", Severity.HIGH, Certainty.CONFIRMED, label, ev,
                               {"queue": label, "host": host}, acts))
        elif verdict == "host_up_no_printing_service":
            out.append(Finding("PM-NET-003", Severity.HIGH, Certainty.CONFIRMED, label, [port_ev],
                               {"queue": label, "host": host}, acts))
        elif verdict == "printing_port_open" and probe.ipp_error and probe.ports.get(631) == PortState.OPEN:
            out.append(Finding("PM-NET-005", Severity.LOW, Certainty.POSSIBLE, label,
                               [port_ev, Evidence(f"IPP query failed: {probe.ipp_error[:160]}", IPP, INF)],
                               {"queue": label, "host": host}))
        if off_subnet:
            out.append(Finding("PM-NET-004", Severity.MEDIUM,
                               Certainty.LIKELY if verdict in ("no_answer", "no_route") else Certainty.POSSIBLE,
                               label,
                               [Evidence(f"{addr} is outside local networks "
                                         f"{', '.join(str(n) for n in nets)}", "network configuration", INF)],
                               {"queue": label, "host": host}))
    return out


_REASON_SEVERITY = {
    "media-jam": Severity.HIGH, "media-empty": Severity.HIGH, "media-needed": Severity.HIGH,
    "door-open": Severity.HIGH, "cover-open": Severity.HIGH, "input-tray-missing": Severity.HIGH,
    "output-area-full": Severity.MEDIUM, "toner-empty": Severity.HIGH, "marker-supply-empty": Severity.HIGH,
    "marker-waste-full": Severity.HIGH, "interlock-open": Severity.HIGH, "paused": Severity.MEDIUM,
    "spool-area-full": Severity.MEDIUM, "offline": Severity.HIGH, "shutdown": Severity.HIGH,
    "toner-low": Severity.LOW, "marker-supply-low": Severity.LOW, "media-low": Severity.LOW,
}


def _reason_key(reason: str) -> str:
    return re.sub(r"-(report|warning|error)$", "", reason)


@check("device_status", "device", needs_network=True)
def check_device_status(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    for dev in ctx.devices_in_scope():
        ipp = dev.probe.ipp if dev.probe else None
        if not ipp:
            continue
        label = dev.queue.name if dev.queue else dev.display_name
        reasons = [r for r in ipp.state_reasons if r and r != "none"]
        if ipp.state == "stopped":
            out.append(Finding("PM-IPP-001", Severity.HIGH, Certainty.CONFIRMED, label,
                               [Evidence(f"printer-state = stopped; reasons = {', '.join(reasons) or 'none'}",
                                         IPP, DEV)],
                               {"queue": label, "reasons": ", ".join(reasons) or "none"}))
        for r in reasons:
            key = _reason_key(r)
            if key.startswith("marker-supply") or key.startswith("toner"):
                continue  # consumables check reports these with per-supply detail
            sev = _REASON_SEVERITY.get(key)
            if sev is None:
                continue
            out.append(Finding("PM-IPP-002", sev, Certainty.CONFIRMED, label,
                               [Evidence(f"printer-state-reasons contains '{r}'", IPP, DEV)],
                               {"queue": label, "reason": key},
                               [ActionRef.of("guided_physical", queue=label, condition=key)]))
    return out


@check("consumables", "consumables", needs_network=True)
def check_consumables(ctx: DiagnosticContext) -> list[Finding]:
    out = []
    quality = bool(ctx.symptoms & {"blank_pages", "missing_color", "faded", "streaks"})
    for dev in ctx.devices_in_scope():
        if dev.connection == ConnectionType.VIRTUAL:
            continue
        label = dev.queue.name if dev.queue else dev.display_name
        cons = dev.consumables
        for c in cons:
            if c.state == "empty":
                out.append(Finding("PM-INK-001", Severity.HIGH, Certainty.CONFIRMED, label,
                                   [Evidence(f"{c.name}: level {c.level_percent}%", c.source or IPP, DEV)],
                                   {"queue": label, "supply": c.name},
                                   [ActionRef.of("guided_physical", queue=label, condition="replace_supply")]))
            elif c.state == "low":
                out.append(Finding("PM-INK-002", Severity.LOW, Certainty.CONFIRMED, label,
                                   [Evidence(f"{c.name}: level {c.level_percent}%", c.source or IPP, DEV)],
                                   {"queue": label, "supply": c.name, "level": str(c.level_percent)}))
        reasons = dev.probe.ipp.state_reasons if dev.probe and dev.probe.ipp else []
        if not any(c.state in ("empty", "low") for c in cons):
            for r in reasons:
                key = _reason_key(r)
                if key in ("marker-supply-empty", "toner-empty"):
                    out.append(Finding("PM-INK-001", Severity.HIGH, Certainty.CONFIRMED, label,
                                       [Evidence(f"printer-state-reasons contains '{r}'", IPP, DEV)],
                                       {"queue": label, "supply": "?"},
                                       [ActionRef.of("guided_physical", queue=label, condition="replace_supply")]))
        if quality and not cons:
            out.append(Finding("PM-INK-003", Severity.INFO, Certainty.CONFIRMED, label,
                               [Evidence("no trustworthy consumable levels available for this device",
                                         "capability registry", INF)],
                               {"queue": label}))
    return out


# --------------------------------------------------------------------------------------------
@check("symptoms", "symptoms")
def check_symptoms(ctx: DiagnosticContext) -> list[Finding]:
    out: list[Finding] = []
    if not ctx.symptoms:
        return out
    user = Evidence("reported by the user", "symptom questionnaire", USR)
    devs = [d for d in ctx.devices_in_scope() if d.connection != ConnectionType.VIRTUAL] or ctx.devices_in_scope()
    for dev in devs[:1] if ctx.target else devs:
        label = dev.queue.name if dev.queue else dev.display_name
        cons = dev.consumables
        empty = [c for c in cons if c.state == "empty"]
        low = [c for c in cons if c.state in ("low", "empty")]
        q = dev.queue
        reasons = {_reason_key(r) for r in (dev.probe.ipp.state_reasons if dev.probe and dev.probe.ipp else [])}
        params = {"queue": label}
        if "paper_jam" in ctx.symptoms:
            corroborated = "media-jam" in reasons or (q and "paperjam" in {f.lower() for f in q.status_flags})
            out.append(Finding("PM-SYM-JAM", Severity.HIGH,
                               Certainty.CONFIRMED if corroborated else Certainty.LIKELY, label,
                               [user] + ([Evidence("device/OS also reports a jam", IPP, DEV)] if corroborated else []),
                               params, [ActionRef.of("guided_physical", queue=label, condition="paper_jam")]))
        if "nothing_prints" in ctx.symptoms and q:
            blockers = []
            if q.state in (QueueState.PAUSED, QueueState.OFFLINE, QueueState.ERROR, QueueState.USER_INTERVENTION):
                blockers.append(Evidence(f"queue state = {q.state.value}", SPOOLER, OS))
            if q.jobs:
                blockers.append(Evidence(f"{len(q.jobs)} job(s) waiting in queue", SPOOLER, OS))
            if dev.probe and not dev.probe.host_answered and dev.probe.ports:
                blockers.append(Evidence("device does not answer on the network", NET, INF))
            out.append(Finding("PM-SYM-NOPRINT", Severity.HIGH,
                               Certainty.LIKELY if blockers else Certainty.POSSIBLE, label, [user, *blockers],
                               params, [ActionRef.of("test_page", queue=q.name)] if not blockers else []))
        if "blank_pages" in ctx.symptoms:
            ev = [user] + [Evidence(f"{c.name} reported empty", c.source, DEV) for c in empty]
            out.append(Finding("PM-SYM-BLANK", Severity.MEDIUM, Certainty.LIKELY if empty else Certainty.POSSIBLE,
                               label, ev, params,
                               [ActionRef.of("guided_physical", queue=label, condition="blank_pages")]))
        if "missing_color" in ctx.symptoms:
            colored = [c for c in low
                       if (c.color or "").upper() not in ("#000000", "") or "black" not in c.name.lower()]
            ev = [user] + [Evidence(f"{c.name}: {c.state}", c.source, DEV) for c in colored]
            out.append(Finding("PM-SYM-COLOR", Severity.MEDIUM, Certainty.LIKELY if colored else Certainty.POSSIBLE,
                               label, ev, params,
                               [ActionRef.of("guided_maintenance", queue=label, procedure="cleaning")]))
        if "faded" in ctx.symptoms:
            ev = [user] + [Evidence(f"{c.name}: {c.state}", c.source, DEV) for c in low]
            out.append(Finding("PM-SYM-FADED", Severity.LOW, Certainty.LIKELY if low else Certainty.POSSIBLE,
                               label, ev, params,
                               [ActionRef.of("guided_maintenance", queue=label, procedure="cleaning")]))
        if "streaks" in ctx.symptoms:
            out.append(Finding("PM-SYM-STREAKS", Severity.LOW, Certainty.POSSIBLE, label, [user], params,
                               [ActionRef.of("guided_maintenance", queue=label, procedure="cleaning")]))
        if "misaligned" in ctx.symptoms:
            out.append(Finding("PM-SYM-ALIGN", Severity.LOW, Certainty.POSSIBLE, label, [user], params,
                               [ActionRef.of("guided_maintenance", queue=label, procedure="alignment")]))
        if "offline_message" in ctx.symptoms and q:
            ev = [user, Evidence(f"queue state = {q.state.value}", SPOOLER, OS)]
            out.append(Finding("PM-SYM-OFFLINE", Severity.MEDIUM,
                               Certainty.LIKELY if q.state in (QueueState.OFFLINE, QueueState.ERROR,
                                                               QueueState.USER_INTERVENTION) else Certainty.POSSIBLE,
                               label, ev, params, [ActionRef.of("rediscover", queue=q.name)] if q.device_host else []))
    if "not_found" in ctx.symptoms and ctx.target is None:
        ev = [user, Evidence(f"{len(ctx.snapshot.physical_queues)} physical queue(s) installed", SPOOLER, OS),
              Evidence(f"{len(ctx.snapshot.interfaces)} active network interface(s)", "network configuration", OS)]
        out.append(Finding("PM-SYM-NOTFOUND", Severity.MEDIUM, Certainty.POSSIBLE, None, ev, {},
                           [ActionRef.of("open_network_assistant")]))
    return out


CATEGORIES = ("service", "queues", "jobs", "ports", "drivers", "usb", "network", "device", "consumables",
              "defaults", "symptoms")
