"""Windows implementation of `PlatformAdapter`."""

from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from typing import Any
from urllib.parse import urlsplit

from printmedic.core.models import (
    ConnectionType,
    DriverInfo,
    NetInterface,
    PortInfo,
    PrinterQueue,
    PrintJob,
    QueueState,
    ServiceStatus,
    SystemSnapshot,
    UsbDevice,
    utcnow,
)
from printmedic.core.validation import (
    ValidationError,
    validate_ipp_url,
    validate_job_id,
    validate_queue_name,
)
from printmedic.platform.base import OpResult, PlatformError
from printmedic.platform.windows.powershell import ScriptError, run_script
from printmedic.services.urls import is_allowed_url

DETECTED_ERROR = {
    0: None, 1: "other", 2: None, 3: "low_paper", 4: "no_paper", 5: "low_toner",
    6: "no_toner", 7: "door_open", 8: "jammed", 9: "offline", 10: "service_requested",
    11: "output_bin_full",
}

_VIRTUAL_PORTS = {"PORTPROMPT:", "FILE:", "NUL:", "SHRFAX:", "XPSPORT:"}
_VIRTUAL_DRIVER = re.compile(r"print to pdf|xps document writer|onenote|fax|virtual print class", re.I)
_ERROR_FLAGS = {"error", "paperjam", "paperout", "paperproblem", "dooropen", "notoner",
                "outofmemory", "outputbinfull", "pagepunt"}


def decode_driver_version(raw: str) -> str:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return raw or ""
    if v <= 0:
        return ""
    return ".".join(str((v >> s) & 0xFFFF) for s in (48, 32, 16, 0))


def _flags(status: str) -> list[str]:
    return [s.strip() for s in (status or "").split(",") if s.strip()]


def map_state(flags: list[str], work_offline: bool) -> QueueState:
    low = {f.lower() for f in flags}
    if "paused" in low:
        return QueueState.PAUSED
    if work_offline or "offline" in low:
        return QueueState.OFFLINE
    if low & _ERROR_FLAGS:
        return QueueState.ERROR
    if "userintervention" in low:
        return QueueState.USER_INTERVENTION
    if "notavailable" in low or "serverunknown" in low:
        return QueueState.NOT_AVAILABLE
    if "powersave" in low:
        return QueueState.POWER_SAVE
    if low & {"printing", "busy", "processing", "ioactive", "warmingup", "initialization", "waiting"}:
        return QueueState.PRINTING
    if not low or low == {"normal"}:
        return QueueState.READY
    return QueueState.UNKNOWN


def classify_connection(queue_name: str, port: str, driver: str,
                        ports: dict[str, PortInfo]) -> ConnectionType:
    p = (port or "").strip()
    pu = p.upper()
    if queue_name.startswith("\\\\"):
        return ConnectionType.SHARED
    if pu in _VIRTUAL_PORTS or pu.startswith("NUL") or _VIRTUAL_DRIVER.search(driver or ""):
        return ConnectionType.VIRTUAL
    if pu.startswith("WSD-") or (ports.get(p) and "WSD" in ports[p].monitor.upper()):
        return ConnectionType.WSD
    if pu.startswith("USB") or pu.startswith("DOT4"):
        return ConnectionType.USB
    if pu.startswith(("HTTP://", "HTTPS://", "IPP://", "IPPS://")):
        return ConnectionType.IPP
    info = ports.get(p)
    if info and info.host:
        return ConnectionType.TCPIP
    if info and "IPP" in info.monitor.upper():
        return ConnectionType.IPP
    if pu.startswith(("LPT", "COM")):
        return ConnectionType.LOCAL
    return ConnectionType.UNKNOWN


def derive_host(conn: ConnectionType, port: str, location: str,
                ports: dict[str, PortInfo]) -> tuple[str | None, int | None]:
    if conn == ConnectionType.TCPIP:
        info = ports.get(port)
        if info and info.host:
            return info.host, info.port_number
    if conn == ConnectionType.IPP:
        parts = urlsplit(port)
        if parts.hostname:
            return parts.hostname, parts.port
    if conn == ConnectionType.WSD and location.lower().startswith(("http://", "https://")):
        parts = urlsplit(location)
        if parts.hostname:
            return parts.hostname, parts.port
    return None, None


def _parse_time(value: Any) -> _dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        # PowerShell emits 7 fractional digits; Python accepts at most 6.
        v = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
        return _dt.datetime.fromisoformat(v)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_jobs(raw: Any) -> list[PrintJob]:
    if isinstance(raw, dict):
        raw = [raw]
    jobs: list[PrintJob] = []
    for j in raw or []:
        if not isinstance(j, dict):
            continue
        jid = _int_or_none(j.get("id"))
        if jid is None:
            continue
        jobs.append(PrintJob(
            job_id=jid, document=str(j.get("document") or ""), status=_flags(str(j.get("status") or "")),
            submitted=_parse_time(j.get("submitted")), pages=_int_or_none(j.get("pages")),
            size_bytes=_int_or_none(j.get("size")), owner=str(j.get("owner") or "") or None,
        ))
    return jobs


def _media_kind(media: str, name: str) -> str:
    text = f"{media} {name}".lower()
    if "802.11" in text or "wireless" in text or "wi-fi" in text or "wlan" in text:
        return "wifi"
    if "802.3" in text or "ethernet" in text:
        return "ethernet"
    return "other"


def parse_inventory(data: dict[str, Any], *, platform: str = "windows") -> SystemSnapshot:
    """Convert the inventory script's JSON into a `SystemSnapshot`. Pure; unit-tested with fixtures."""
    def as_list(v: Any) -> list[Any]:
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    ports: dict[str, PortInfo] = {}
    for p in as_list(data.get("ports")):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        info = PortInfo(
            name=str(p["name"]), monitor=str(p.get("monitor") or ""),
            description=str(p.get("description") or ""), host=str(p.get("host") or "") or None,
            port_number=_int_or_none(p.get("portNumber")),
            protocol={"1": "raw", "2": "lpr", "raw": "raw", "lpr": "lpr"}.get(str(p.get("protocol") or "").lower()),
        )
        ports[info.name] = info

    queues: list[PrinterQueue] = []
    for q in as_list(data.get("queues")):
        if not isinstance(q, dict) or not q.get("name"):
            continue
        name = str(q["name"])
        port = str(q.get("port") or "")
        driver = str(q.get("driver") or "")
        flags = _flags(str(q.get("status") or ""))
        offline = bool(q.get("workOffline"))
        conn = classify_connection(name, port, driver, ports)
        location = str(q.get("cimLocation") or q.get("location") or "")
        host, hport = derive_host(conn, port, location, ports)
        queues.append(PrinterQueue(
            name=name, driver=driver, port=port, connection=conn, state=map_state(flags, offline),
            status_flags=flags, detected_error=DETECTED_ERROR.get(_int_or_none(q.get("detectedError")) or 0),
            is_default=bool(q.get("isDefault")), shared=bool(q.get("shared")), work_offline=offline,
            location=location, comment=str(q.get("comment") or ""), device_host=host, device_port=hport,
            jobs=parse_jobs(q.get("jobs")),
        ))

    drivers = [
        DriverInfo(
            name=str(d.get("name") or ""), manufacturer=str(d.get("manufacturer") or ""),
            version=decode_driver_version(str(d.get("version") or "")), environment=str(d.get("env") or ""),
            major_version=_int_or_none(d.get("major")), inf_path=str(d.get("inf") or ""),
            provider=str(d.get("provider") or ""),
        )
        for d in as_list(data.get("drivers")) if isinstance(d, dict) and d.get("name")
    ]

    usb = [
        UsbDevice(
            instance_id=str(u.get("id") or ""), name=str(u.get("name") or ""),
            manufacturer=str(u.get("manufacturer") or ""), present=bool(u.get("present", True)),
            status=str(u.get("status") or ""), problem_code=_int_or_none(u.get("problem")) or 0,
            pnp_class=str(u.get("pnpClass") or ""),
            hardware_ids=[str(h) for h in as_list(u.get("hardwareIds")) if h],
        )
        for u in as_list(data.get("usb")) if isinstance(u, dict) and u.get("id")
    ]

    sp = data.get("spooler")
    spooler = None
    if isinstance(sp, dict):
        spooler = ServiceStatus(name="Spooler", running=str(sp.get("state")).lower() == "running",
                                state=str(sp.get("state") or ""), start_type=str(sp.get("startMode") or ""))

    interfaces = [
        NetInterface(name=str(i.get("name") or ""), address=str(i.get("address") or ""),
                     prefix_length=_int_or_none(i.get("prefix")) or 24,
                     kind=_media_kind(str(i.get("media") or ""), str(i.get("name") or "")),
                     up=bool(i.get("up", True)))
        for i in as_list(data.get("interfaces")) if isinstance(i, dict) and i.get("address")
    ]

    return SystemSnapshot(
        platform=platform, taken_at=utcnow(), spooler=spooler, queues=queues, ports=list(ports.values()),
        drivers=drivers, usb_devices=usb, interfaces=interfaces, is_admin=bool(data.get("isAdmin")),
        errors=[str(e) for e in as_list(data.get("errors"))],
    )


def interpret_mutation(data: Any, stderr: str = "") -> OpResult:
    if not isinstance(data, dict):
        return OpResult(False, "failed", stderr or "no result from PowerShell")
    if data.get("ok"):
        rv = data.get("rv")
        if rv in (None, 0):
            return OpResult(True)
        if rv == 5:
            return OpResult(False, "access_denied", "Windows returned 'access denied' (5).")
        return OpResult(False, "failed", f"Windows returned code {rv}.")
    message = str(data.get("message") or "")
    hresult = _int_or_none(data.get("hresult")) or 0
    native = _int_or_none(data.get("native")) or 0
    category = str(data.get("category") or "")
    if native == 1223 or "canceled by the user" in message.lower():
        return OpResult(False, "cancelled_by_user", "The administrator prompt was declined.")
    if category == "PermissionDenied" or native == 5 or hresult in (-2147024891, 0x80070005):
        return OpResult(False, "access_denied", message)
    if "PM_NOT_FOUND" in message or category == "ObjectNotFound":
        return OpResult(False, "not_found", message)
    return OpResult(False, "failed", message, {"category": category, "hresult": str(hresult)})


class WindowsAdapter:
    name = "Windows print spooler"
    simulated = False

    def __init__(self, timeout: float = 60.0) -> None:
        if sys.platform != "win32":
            raise PlatformError("WindowsAdapter requires Windows")
        self._timeout = timeout

    def snapshot(self) -> SystemSnapshot:
        try:
            out = run_script("inventory", timeout=self._timeout)
        except ScriptError as exc:
            raise PlatformError(str(exc)) from exc
        if not isinstance(out.data, dict):
            raise PlatformError(out.stderr or "inventory returned no data")
        return parse_inventory(out.data)

    def list_jobs(self, queue: str) -> list[PrintJob]:
        out = run_script("jobs", {"QUEUE": validate_queue_name(queue)}, timeout=30)
        return parse_jobs(out.data)

    def _mutate(self, script: str, params: dict[str, str], timeout: float = 45.0) -> OpResult:
        try:
            out = run_script(script, params, timeout=timeout)
        except ScriptError as exc:
            code = "timeout" if "timeout" in str(exc) else "failed"
            return OpResult(False, code, str(exc))
        return interpret_mutation(out.data, out.stderr)

    def resume_queue(self, queue: str) -> OpResult:
        return self._mutate("resume_queue", {"QUEUE": validate_queue_name(queue)})

    def set_online(self, queue: str) -> OpResult:
        return self._mutate("set_online", {"QUEUE": validate_queue_name(queue)})

    def set_default(self, queue: str) -> OpResult:
        return self._mutate("set_default", {"QUEUE": validate_queue_name(queue)})

    def remove_job(self, queue: str, job_id: int) -> OpResult:
        return self._mutate("remove_job", {"QUEUE": validate_queue_name(queue),
                                           "JOB": str(validate_job_id(job_id))})

    def clear_queue(self, queue: str) -> OpResult:
        return self._mutate("clear_queue", {"QUEUE": validate_queue_name(queue)})

    def restart_spooler(self) -> OpResult:
        return self._mutate("restart_spooler", {}, timeout=120)

    def print_test_page(self, queue: str) -> OpResult:
        return self._mutate("print_test_page", {"QUEUE": validate_queue_name(queue)})

    def add_ipp_printer(self, name: str, url: str) -> OpResult:
        try:
            params = {"NAME": validate_queue_name(name), "URL": validate_ipp_url(url)}
        except ValidationError as exc:
            return OpResult(False, "failed", f"invalid input: {exc}")
        return self._mutate("add_ipp_printer", params, timeout=180)

    def open_url(self, url: str) -> OpResult:
        if not is_allowed_url(url):
            return OpResult(False, "failed", "URL is not on the verified vendor allowlist")
        os.startfile(url)  # noqa: S606 - allowlisted https URL only
        return OpResult(True)

    def open_printer_settings(self) -> OpResult:
        os.startfile("ms-settings:printers")  # noqa: S606 - constant URI
        return OpResult(True)
