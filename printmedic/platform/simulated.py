"""Simulation environment: a stateful fake print subsystem and network for development and tests.

Everything produced here is flagged `simulated=True` and the UI labels it visibly. Hosts use the
RFC 5737 documentation ranges (192.0.2.0/24, 198.51.100.0/24) so they can never collide with real
devices. Mutations change the simulated state, so repair verification is exercised for real.
"""

from __future__ import annotations

import copy
import datetime as _dt
import threading
from dataclasses import dataclass, field

from printmedic.core.models import (
    ConnectionType,
    Consumable,
    DiscoveredEndpoint,
    DriverInfo,
    IppStatus,
    NetInterface,
    PortInfo,
    PortState,
    PrinterQueue,
    PrintJob,
    QueueState,
    ServiceAdvert,
    ServiceStatus,
    SystemSnapshot,
    UsbDevice,
    utcnow,
)
from printmedic.core.validation import ValidationError, validate_ipp_url
from printmedic.network.ipp import IppError
from printmedic.platform.base import OpResult
from printmedic.services.urls import is_allowed_url

PREFIX = "SIM "


def _job(job_id: int, doc: str, status: list[str], minutes_ago: int) -> PrintJob:
    return PrintJob(job_id=job_id, document=doc, status=status,
                    submitted=utcnow() - _dt.timedelta(minutes=minutes_ago), pages=1, size_bytes=120_000,
                    owner="simuser")


@dataclass
class SimHost:
    ports: dict[int, PortState]
    ipp: IppStatus | None = None
    hostnames: list[str] = field(default_factory=list)


def _ipp(make: str, state: str, reasons: list[str], consumables: list[tuple[str, str, int | None]],
         uri: str) -> IppStatus:
    cons = []
    for name, color, level in consumables:
        if level is None:
            st = "unknown"
        elif level == 0:
            st = "empty"
        elif level <= 10:
            st = "low"
        else:
            st = "ok"
        cons.append(Consumable(name=name, color=color, level_percent=level, state=st, kind="ink-cartridge",
                               source="IPP marker-levels (simulated)", read_at=utcnow()))
    return IppStatus(uri=uri, state=state, state_reasons=reasons, make_and_model=make, name=make,
                     firmware="SIM.2026.10", accepting_jobs=True,
                     formats=["application/pdf", "image/urf", "image/pwg-raster"], ipp_versions=["1.1", "2.0"],
                     uuid="urn:uuid:00000000-0000-4000-8000-0000000000" + str(len(make) % 90 + 10),
                     consumables=cons)


def default_scenario() -> tuple[SystemSnapshot, dict[str, SimHost]]:
    hp_host, brother_host, xerox_host = "192.0.2.21", "192.0.2.50", "198.51.100.80"
    queues = [
        PrinterQueue(
            name=PREFIX + "HP OfficeJet Pro 9010", driver="HP OfficeJet Pro 9010 PCL-3", port="WSD-sim-hp-9010",
            connection=ConnectionType.WSD, state=QueueState.PAUSED, status_flags=["Paused"],
            location=f"http://{hp_host}:3911/", device_host=hp_host, device_port=3911,
            jobs=[_job(11, "Quarterly report.pdf", ["Error", "Printing"], 95), _job(12, "Boarding pass.pdf", [], 40)],
        ),
        PrinterQueue(
            name=PREFIX + "Brother HL-L2350DW", driver="Brother HL-L2350D series", port="IP_" + brother_host,
            connection=ConnectionType.TCPIP, state=QueueState.ERROR, status_flags=["Error"],
            detected_error="offline", device_host=brother_host, device_port=9100,
            jobs=[_job(21, "Invoice 2026-114.docx", ["Error"], 180), _job(22, "Notes.txt", ["Blocked"], 170)],
        ),
        PrinterQueue(
            name=PREFIX + "Canon PIXMA TS5350", driver="Canon TS5300 series", port="USB001",
            connection=ConnectionType.USB, state=QueueState.READY, status_flags=["Normal"],
        ),
        PrinterQueue(
            name=PREFIX + "Kyocera ECOSYS P2040dn", driver="Kyocera ECOSYS P2040dn KX", port="USB002",
            connection=ConnectionType.USB, state=QueueState.OFFLINE, status_flags=["Offline"], work_offline=True,
        ),
        PrinterQueue(
            name=PREFIX + "Kyocera ECOSYS P2040dn (Copy 1)", driver="Kyocera ECOSYS P2040dn KX", port="USB003",
            connection=ConnectionType.USB, state=QueueState.READY, status_flags=["Normal"],
        ),
        PrinterQueue(
            name=PREFIX + "Microsoft Print to PDF", driver="Microsoft Print To PDF", port="PORTPROMPT:",
            connection=ConnectionType.VIRTUAL, state=QueueState.READY, status_flags=["Normal"], is_default=True,
        ),
    ]
    ports = [
        PortInfo("WSD-sim-hp-9010", "WSD Port Monitor", "WSD Port"),
        PortInfo("IP_" + brother_host, "Standard TCP/IP Port", "Standard TCP/IP Port", brother_host, 9100, "raw"),
        PortInfo("USB001", "USB Monitor", "Virtual printer port for USB"),
        PortInfo("USB002", "USB Monitor", "Virtual printer port for USB"),
        PortInfo("USB003", "USB Monitor", "Virtual printer port for USB"),
        PortInfo("PORTPROMPT:", "Local Monitor", "Local Port"),
    ]
    drivers = [
        DriverInfo("HP OfficeJet Pro 9010 PCL-3", "HP", "54.4.6152.0", "Windows x64", 4, "hpkoj9010.inf", "HP"),
        DriverInfo("Brother HL-L2350D series", "Brother", "1.4.0.0", "Windows x64", 3, "brpo2350.inf", "Brother"),
        DriverInfo("Canon TS5300 series", "Canon", "1.0.0.0", "Windows x64", 4, "cnts53.inf", "Canon"),
        DriverInfo("Kyocera ECOSYS P2040dn KX", "Kyocera", "8.3.0.0", "Windows x64", 3, "oemsetup.inf", "Kyocera"),
        DriverInfo("Microsoft Print To PDF", "Microsoft", "10.0.26100.1", "Windows x64", 4, "prnms009.inf",
                   "Microsoft"),
    ]
    usb = [
        UsbDevice("USBPRINT\\CANONTS5300_SERIES\\SIM1", "Canon TS5300 series", "Canon", True, "OK", 0, "Printer",
                  ["USBPRINT\\CanonTS5300_series"]),
        UsbDevice("USB\\VID_04B8&PID_1186\\SIM2", "EPSON ET-2850 Series", "", True, "Error", 28, "",
                  ["USB\\VID_04B8&PID_1186", "USB\\Class_07&SubClass_01&Prot_02"]),
    ]
    snap = SystemSnapshot(
        platform="simulation", taken_at=utcnow(), spooler=ServiceStatus("Spooler", True, "Running", "Auto"),
        queues=queues, ports=ports, drivers=drivers, usb_devices=usb,
        interfaces=[NetInterface("SIM Wi-Fi", "192.0.2.10", 24, "wifi")], simulated=True,
    )
    hosts = {
        hp_host: SimHost(
            {631: PortState.OPEN, 443: PortState.OPEN, 9100: PortState.OPEN, 515: PortState.REFUSED,
             80: PortState.OPEN, 3911: PortState.OPEN},
            _ipp("HP OfficeJet Pro 9010 series", "stopped", ["marker-supply-empty-error", "marker-supply-low-warning"],
                 [("black ink", "#000000", 8), ("cyan ink", "#00FFFF", 45), ("magenta ink", "#FF00FF", 0),
                  ("yellow ink", "#FFFF00", 61)], f"ipp://{hp_host}:631/ipp/print"),
            ["sim-hp9010.local"],
        ),
        brother_host: SimHost({p: PortState.TIMEOUT for p in (631, 443, 9100, 515, 80)}, None,
                              ["sim-brother.local"]),
        xerox_host: SimHost(
            {631: PortState.OPEN, 443: PortState.OPEN, 9100: PortState.OPEN, 515: PortState.REFUSED,
             80: PortState.OPEN},
            _ipp("Xerox VersaLink C405", "idle", ["none"],
                 [("Black Toner", "#000000", 72), ("Cyan Toner", "#00FFFF", None), ("Magenta Toner", "#FF00FF", 55),
                  ("Yellow Toner", "#FFFF00", 49)], f"ipp://{xerox_host}:631/ipp/print"),
            ["sim-xerox.local"],
        ),
    }
    return snap, hosts


def sim_endpoints(hosts: dict[str, SimHost]) -> list[DiscoveredEndpoint]:
    eps = []
    for addr, h in hosts.items():
        if h.ipp is None:
            continue
        ep = DiscoveredEndpoint(host=addr, name=h.ipp.make_and_model, model=h.ipp.make_and_model,
                                manufacturer=h.ipp.make_and_model.split()[0], uuid=h.ipp.uuid, simulated=True,
                                sources={"mdns", "wsd"})
        ep.services = [ServiceAdvert("ipp", 631, "/ipp/print", "mdns"),
                       ServiceAdvert("ipps", 443, "/ipp/print", "mdns"),
                       ServiceAdvert("raw", 9100, "", "mdns")]
        eps.append(ep)
    return eps


class SimulatedAdapter:
    name = "Test fixture adapter"
    simulated = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap, self.hosts = default_scenario()
        self._next_job = 100
        self.opened_urls: list[str] = []

    # --- read ---------------------------------------------------------------------------------
    def snapshot(self) -> SystemSnapshot:
        with self._lock:
            snap = copy.deepcopy(self._snap)
        snap.taken_at = utcnow()
        return snap

    def list_jobs(self, queue: str) -> list[PrintJob]:
        with self._lock:
            q = self._snap.queue(queue)
            return copy.deepcopy(q.jobs) if q else []

    # --- helpers ------------------------------------------------------------------------------
    def _q(self, name: str) -> PrinterQueue | None:
        return self._snap.queue(name)

    def _spooler_ok(self) -> bool:
        return bool(self._snap.spooler and self._snap.spooler.running)

    # --- mutations ----------------------------------------------------------------------------
    def resume_queue(self, queue: str) -> OpResult:
        with self._lock:
            q = self._q(queue)
            if not q:
                return OpResult(False, "not_found", "queue not found")
            q.status_flags = [f for f in q.status_flags if f.lower() != "paused"] or ["Normal"]
            q.state = QueueState.READY if q.status_flags == ["Normal"] else q.state
            if q.state == QueueState.PAUSED:
                q.state = QueueState.READY
            return OpResult(True)

    def set_online(self, queue: str) -> OpResult:
        with self._lock:
            q = self._q(queue)
            if not q:
                return OpResult(False, "not_found", "queue not found")
            q.work_offline = False
            q.status_flags = [f for f in q.status_flags if f.lower() != "offline"] or ["Normal"]
            q.state = QueueState.READY
            return OpResult(True)

    def set_default(self, queue: str) -> OpResult:
        with self._lock:
            if not self._q(queue):
                return OpResult(False, "not_found", "queue not found")
            for q in self._snap.queues:
                q.is_default = q.name == queue
            return OpResult(True)

    def remove_job(self, queue: str, job_id: int) -> OpResult:
        with self._lock:
            q = self._q(queue)
            if not q or not any(j.job_id == job_id for j in q.jobs):
                return OpResult(False, "not_found", "job not found")
            q.jobs = [j for j in q.jobs if j.job_id != job_id]
            return OpResult(True)

    def clear_queue(self, queue: str) -> OpResult:
        with self._lock:
            q = self._q(queue)
            if not q:
                return OpResult(False, "not_found", "queue not found")
            q.jobs = []
            return OpResult(True)

    def restart_spooler(self) -> OpResult:
        with self._lock:
            self._snap.spooler = ServiceStatus("Spooler", True, "Running", "Auto")
            return OpResult(True)

    def stop_spooler_for_test(self) -> None:
        with self._lock:
            self._snap.spooler = ServiceStatus("Spooler", False, "Stopped", "Auto")

    def print_test_page(self, queue: str) -> OpResult:
        with self._lock:
            q = self._q(queue)
            if not q:
                return OpResult(False, "not_found", "queue not found")
            if q.state in (QueueState.PAUSED, QueueState.OFFLINE, QueueState.ERROR):
                self._next_job += 1
                q.jobs.append(_job(self._next_job, "Test Page", [], 0))
            return OpResult(True)

    def add_ipp_printer(self, name: str, url: str) -> OpResult:
        try:
            validate_ipp_url(url)
        except ValidationError as exc:
            return OpResult(False, "failed", f"invalid input: {exc}")
        from urllib.parse import urlsplit
        host = urlsplit(url).hostname or ""
        with self._lock:
            if self._q(name):
                return OpResult(False, "failed", "a queue with this name already exists")
            h = self.hosts.get(host)
            if not h or h.ports.get(urlsplit(url).port or 631) != PortState.OPEN:
                return OpResult(False, "failed", "device did not accept the IPP connection")
            self._snap.queues.append(PrinterQueue(
                name=name, driver="Microsoft IPP Class Driver", port=url, connection=ConnectionType.IPP,
                state=QueueState.READY, status_flags=["Normal"], device_host=host, device_port=631))
            return OpResult(True)

    def open_url(self, url: str) -> OpResult:
        if not is_allowed_url(url):
            return OpResult(False, "failed", "URL is not on the verified vendor allowlist")
        self.opened_urls.append(url)
        return OpResult(True, message="Simulation: the browser was not actually opened.")

    def open_printer_settings(self) -> OpResult:
        return OpResult(True, message="Simulation: Windows Settings was not actually opened.")


class SimulatedNetworkProbe:
    simulated = True

    def __init__(self, adapter: SimulatedAdapter) -> None:
        self._hosts = adapter.hosts

    def resolve(self, host: str) -> tuple[list[str], str | None]:
        if host in self._hosts:
            return [host], None
        for addr, h in self._hosts.items():
            if host in h.hostnames:
                return [addr], None
        if host.replace(".", "").isdigit():
            return [host], None
        return [], "Name or service not known (simulated)"

    def tcp_check(self, address: str, port: int, timeout: float) -> PortState:
        h = self._hosts.get(address)
        if not h:
            return PortState.TIMEOUT
        return h.ports.get(port, PortState.REFUSED)

    def ipp_status(self, host: str, port: int, tls: bool, timeout: float) -> IppStatus:
        h = self._hosts.get(host)
        if not h or not h.ipp:
            raise IppError("no IPP service (simulated)")
        status = copy.deepcopy(h.ipp)
        for c in status.consumables:
            c.read_at = utcnow()
        return status
