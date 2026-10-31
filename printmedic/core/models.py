"""Typed domain model shared by every layer.

Values in this module are plain data. Anything that came from the OS or a device is
untrusted: it is stored as-is for display but is never interpolated into commands.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum, IntEnum


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


class StatusOrigin(str, Enum):
    """Where a piece of state information came from. Shown to the user on every status."""

    REPORTED_OS = "reported_os"          # Windows/CUPS print subsystem said so
    REPORTED_DEVICE = "reported_device"  # the printer itself said so (e.g. IPP)
    INFERRED = "inferred"                # PrintMedic concluded it from other evidence
    VERIFIED = "verified"                # re-read after an action and matched expectation
    USER = "user"                        # the user told us (e.g. "paper is jammed")


class ConnectionType(str, Enum):
    USB = "usb"
    WSD = "wsd"
    IPP = "ipp"
    TCPIP = "tcpip"        # Standard TCP/IP port (RAW 9100 or LPR)
    LOCAL = "local"        # LPT/COM/FILE
    VIRTUAL = "virtual"    # PDF/XPS/OneNote/fax style queues with no device
    SHARED = "shared"      # \\server\queue
    UNKNOWN = "unknown"


class QueueState(str, Enum):
    READY = "ready"
    PRINTING = "printing"
    PAUSED = "paused"
    OFFLINE = "offline"
    ERROR = "error"
    USER_INTERVENTION = "user_intervention"
    POWER_SAVE = "power_save"
    NOT_AVAILABLE = "not_available"
    UNKNOWN = "unknown"


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class Certainty(str, Enum):
    """Separates confirmed faults from hypotheses. Never upgrade without evidence."""

    CONFIRMED = "confirmed"  # directly observed/reported fact
    LIKELY = "likely"        # several independent pieces of evidence agree
    POSSIBLE = "possible"    # plausible cause, weak or indirect evidence

    @property
    def rank(self) -> int:
        return {"confirmed": 3, "likely": 2, "possible": 1}[self.value]


class CapabilityLevel(str, Enum):
    AUTOMATED = "automated"      # PrintMedic can do/read it through a trustworthy interface
    GUIDED = "guided"            # PrintMedic can only explain the manual procedure
    UNSUPPORTED = "unsupported"  # no legitimate interface for this device
    UNKNOWN = "unknown"          # not probed yet (e.g. device unreachable)


class Capability(str, Enum):
    DISCOVERY = "discovery"
    MODEL_ID = "model_id"
    QUEUE_STATUS = "queue_status"
    DEVICE_STATUS = "device_status"
    JOB_MANAGEMENT = "job_management"
    CONSUMABLES = "consumables"
    TEST_PAGE = "test_page"
    PRINTHEAD_CLEANING = "printhead_cleaning"
    ALIGNMENT = "alignment"
    DIAGNOSTIC_INFO = "diagnostic_info"
    NETWORK_CONFIG = "network_config"
    FIRMWARE_INFO = "firmware_info"
    SET_DEFAULT = "set_default"


@dataclass(frozen=True)
class CapabilityInfo:
    capability: Capability
    level: CapabilityLevel
    source: str          # e.g. "Windows print spooler", "IPP Get-Printer-Attributes", "vendor profile"
    note: str = ""  # i18n key ("capnote.*") explaining the level, or ""


@dataclass
class PrintJob:
    job_id: int
    document: str
    status: list[str]
    submitted: _dt.datetime | None = None
    pages: int | None = None
    size_bytes: int | None = None
    owner: str | None = None

    @property
    def is_error(self) -> bool:
        bad = {"error", "blocked", "offline", "paperout", "userintervention", "restart"}
        return any(s.lower().replace(" ", "") in bad for s in self.status)

    def age_minutes(self, now: _dt.datetime | None = None) -> float | None:
        if self.submitted is None:
            return None
        now = now or utcnow()
        sub = self.submitted if self.submitted.tzinfo else self.submitted.replace(tzinfo=_dt.UTC)
        return (now - sub).total_seconds() / 60.0


@dataclass
class PortInfo:
    name: str
    monitor: str = ""
    description: str = ""
    host: str | None = None  # PrinterHostAddress for TCP/IP ports
    port_number: int | None = None
    protocol: str | None = None  # "raw" | "lpr" | None


@dataclass
class PrinterQueue:
    name: str
    driver: str
    port: str
    connection: ConnectionType
    state: QueueState
    status_flags: list[str] = field(default_factory=list)  # raw OS flags, e.g. ["UserIntervention"]
    detected_error: str | None = None   # Win32 DetectedErrorState mapped to a keyword
    is_default: bool = False
    shared: bool = False
    work_offline: bool = False
    location: str = ""
    comment: str = ""
    device_host: str | None = None   # host/IP derived from the port or WSD location
    device_port: int | None = None
    jobs: list[PrintJob] = field(default_factory=list)

    @property
    def is_virtual(self) -> bool:
        return self.connection == ConnectionType.VIRTUAL


@dataclass
class DriverInfo:
    name: str
    manufacturer: str = ""
    version: str = ""
    environment: str = ""    # e.g. "Windows x64"
    major_version: int | None = None  # 3 = v3, 4 = v4 driver model
    inf_path: str = ""
    provider: str = ""


@dataclass
class UsbDevice:
    instance_id: str
    name: str
    manufacturer: str = ""
    present: bool = True
    status: str = ""             # PnP status text
    problem_code: int = 0         # ConfigManagerErrorCode, 28 = no driver
    pnp_class: str = ""
    hardware_ids: list[str] = field(default_factory=list)

    @property
    def driver_missing(self) -> bool:
        return self.problem_code == 28

    @property
    def is_print_function(self) -> bool:
        iid = self.instance_id.upper()
        return iid.startswith("USBPRINT\\") or any("USB\\CLASS_07" in h.upper() for h in self.hardware_ids)


@dataclass
class ServiceStatus:
    name: str
    running: bool
    state: str = ""
    start_type: str = ""


@dataclass
class NetInterface:
    name: str
    address: str
    prefix_length: int
    kind: str = ""   # "wifi" | "ethernet" | "other"
    up: bool = True


class PortState(str, Enum):
    OPEN = "open"
    REFUSED = "refused"      # host answered with RST: host is up, service not listening
    TIMEOUT = "timeout"      # no answer: host down/asleep, filtered, or wrong network
    UNREACHABLE = "unreachable"  # OS reported no route / host unreachable
    ERROR = "error"


@dataclass
class ServiceAdvert:
    protocol: str       # "ipp" | "ipps" | "raw" | "lpd" | "wsd" | "http"
    port: int
    path: str = ""
    source: str = ""    # "mdns" | "wsd" | "queue" | "probe"
    txt: dict[str, str] = field(default_factory=dict)


@dataclass
class Consumable:
    name: str
    color: str | None
    level_percent: int | None   # None = device did not give a trustworthy numeric level
    state: str                  # "ok" | "low" | "empty" | "unknown" | "some_remaining"
    kind: str = ""              # "ink-cartridge", "toner", ...
    source: str = ""
    read_at: _dt.datetime | None = None


@dataclass
class IppStatus:
    uri: str
    state: str                     # "idle" | "processing" | "stopped" | "unknown"
    state_reasons: list[str]
    make_and_model: str = ""
    name: str = ""
    firmware: str = ""
    accepting_jobs: bool | None = None
    formats: list[str] = field(default_factory=list)
    ipp_versions: list[str] = field(default_factory=list)
    uuid: str = ""
    consumables: list[Consumable] = field(default_factory=list)
    raw_attribute_names: list[str] = field(default_factory=list)


@dataclass
class ProbeResult:
    """Outcome of probing one host with bounded, printing-port-only checks."""

    host: str
    addresses: list[str] = field(default_factory=list)
    dns_error: str | None = None
    ports: dict[int, PortState] = field(default_factory=dict)
    ipp: IppStatus | None = None
    ipp_error: str | None = None
    duration_ms: int = 0
    probed_at: _dt.datetime = field(default_factory=utcnow)

    @property
    def any_open(self) -> bool:
        return any(s == PortState.OPEN for s in self.ports.values())

    @property
    def host_answered(self) -> bool:
        return any(s in (PortState.OPEN, PortState.REFUSED) for s in self.ports.values())


@dataclass
class DiscoveredEndpoint:
    """A network device found by a discovery method (not necessarily installed)."""

    host: str
    name: str = ""
    model: str = ""
    manufacturer: str = ""
    services: list[ServiceAdvert] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)
    uuid: str = ""
    simulated: bool = False

    @property
    def key(self) -> str:
        return self.uuid or self.host


@dataclass
class SystemSnapshot:
    """Everything known about the local print subsystem at one moment."""

    platform: str
    taken_at: _dt.datetime
    spooler: ServiceStatus | None
    queues: list[PrinterQueue]
    ports: list[PortInfo]
    drivers: list[DriverInfo]
    usb_devices: list[UsbDevice]
    interfaces: list[NetInterface]
    is_admin: bool = False
    simulated: bool = False
    errors: list[str] = field(default_factory=list)  # partial-failure notes from collection

    def queue(self, name: str) -> PrinterQueue | None:
        return next((q for q in self.queues if q.name == name), None)

    @property
    def physical_queues(self) -> list[PrinterQueue]:
        return [q for q in self.queues if not q.is_virtual]

    @property
    def default_queue(self) -> PrinterQueue | None:
        return next((q for q in self.queues if q.is_default), None)


@dataclass
class Device:
    """A printer as presented to the user: queue + optional USB/network/device facts."""

    device_id: str
    display_name: str
    vendor_id: str
    vendor_name: str
    model: str
    connection: ConnectionType
    queue: PrinterQueue | None = None
    usb: UsbDevice | None = None
    endpoint: DiscoveredEndpoint | None = None
    probe: ProbeResult | None = None
    capabilities: list[CapabilityInfo] = field(default_factory=list)
    simulated: bool = False

    @property
    def host(self) -> str | None:
        if self.queue and self.queue.device_host:
            return self.queue.device_host
        if self.endpoint:
            return self.endpoint.host
        return None

    def capability(self, cap: Capability) -> CapabilityInfo | None:
        return next((c for c in self.capabilities if c.capability == cap), None)

    @property
    def consumables(self) -> list[Consumable]:
        if self.probe and self.probe.ipp:
            return self.probe.ipp.consumables
        return []
