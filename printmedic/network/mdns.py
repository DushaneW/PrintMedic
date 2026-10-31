"""DNS-SD over mDNS printer browsing (Bonjour / IPP Everywhere / AirPrint advertisements)."""

from __future__ import annotations

import threading
import time
from typing import Any

from printmedic.core.cancel import CancelToken
from printmedic.core.models import DiscoveredEndpoint, ServiceAdvert

SERVICE_TYPES = {
    "_ipp._tcp.local.": "ipp",
    "_ipps._tcp.local.": "ipps",
    "_pdl-datastream._tcp.local.": "raw",
    "_printer._tcp.local.": "lpd",
}

_MAX_TXT = 64
_MAX_ENDPOINTS = 256


def _txt(props: dict[bytes, bytes | None]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in list(props.items())[:_MAX_TXT]:
        try:
            key = k.decode("utf-8", errors="replace")[:64]
            out[key] = (v or b"").decode("utf-8", errors="replace")[:256]
        except AttributeError:
            continue
    return out


def browse(duration: float, cancel: CancelToken, interface_address: str | None = None) -> list[DiscoveredEndpoint]:
    """Browse printer service types for at most `duration` seconds. Cancellable."""
    try:
        from zeroconf import IPVersion, ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError("python-zeroconf is not installed") from exc

    found: dict[str, DiscoveredEndpoint] = {}
    lock = threading.Lock()
    pending: list[tuple[str, str]] = []

    def on_change(zeroconf: Any, service_type: str, name: str, state_change: Any) -> None:
        if state_change is ServiceStateChange.Added:
            with lock:
                pending.append((service_type, name))

    interfaces: Any = [interface_address] if interface_address else None
    kwargs: dict[str, Any] = {"ip_version": IPVersion.V4Only}
    if interfaces:
        kwargs["interfaces"] = interfaces
    zc = Zeroconf(**kwargs)
    try:
        browsers = [ServiceBrowser(zc, st, handlers=[on_change]) for st in SERVICE_TYPES]
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and not cancel.cancelled:
            with lock:
                batch, pending[:] = pending[:], []
            for service_type, name in batch:
                info = zc.get_service_info(service_type, name, timeout=1500)
                if info is None:
                    continue
                addrs = info.parsed_addresses()
                if not addrs:
                    continue
                txt = _txt(info.properties or {})
                host = addrs[0]
                uuid = txt.get("UUID", "")
                key = uuid or host
                ep = found.get(key)
                if ep is None:
                    if len(found) >= _MAX_ENDPOINTS:
                        continue
                    ep = DiscoveredEndpoint(host=host, uuid=uuid)
                    found[key] = ep
                ep.sources.add("mdns")
                ep.name = ep.name or name.split("._", 1)[0]
                ep.model = ep.model or txt.get("ty", "") or txt.get("product", "").strip("()")
                ep.manufacturer = ep.manufacturer or txt.get("usb_MFG", "")
                proto = SERVICE_TYPES[service_type]
                path = "/" + txt.get("rp", "").lstrip("/") if proto in ("ipp", "ipps") else ""
                ep.services.append(ServiceAdvert(protocol=proto, port=int(info.port or 0), path=path,
                                                 source="mdns", txt=txt))
            cancel.wait(0.2)
        for b in browsers:
            b.cancel()
    finally:
        zc.close()
    return list(found.values())
