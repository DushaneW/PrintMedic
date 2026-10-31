"""Turns a `SystemSnapshot` (+ optional probe results and discovered endpoints) into `Device`s."""

from __future__ import annotations

import re

from printmedic.core.models import (
    ConnectionType,
    Device,
    DiscoveredEndpoint,
    ProbeResult,
    SystemSnapshot,
    UsbDevice,
)
from printmedic.vendors.capabilities import compute_capabilities
from printmedic.vendors.profiles import detect_vendor

_STOP = {"series", "printer", "class", "driver", "pcl", "pcl-3", "pcl6", "ps", "kx", "usb", "copy", "sim",
         "inc", "corp", "the", "v4", "v3"}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", text.lower()) if len(t) > 1 and t not in _STOP}


def usb_matches_queue(usb: UsbDevice, queue_name: str, driver: str) -> float:
    """Similarity 0..1 between a USB device name and a queue's name/driver (heuristic)."""
    a = tokens(f"{usb.name} {usb.manufacturer}")
    b = tokens(f"{queue_name} {driver}")
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def model_from(queue_name: str, driver: str) -> str:
    m = re.search(r"\(([^)]+)\)\s*$", queue_name)
    if m and not m.group(1).lower().startswith("copy"):
        return m.group(1)
    return re.sub(r"\s+(PCL[- ]?\d*|PS|KX|XPS|v4|class driver)$", "", driver, flags=re.I).strip() or queue_name


def build_devices(snapshot: SystemSnapshot, probes: dict[str, ProbeResult] | None = None,
                  endpoints: list[DiscoveredEndpoint] | None = None) -> list[Device]:
    probes = probes or {}
    endpoints = endpoints or []
    devices: list[Device] = []
    used_usb: set[str] = set()
    used_hosts: set[str] = set()

    print_usb = [u for u in snapshot.usb_devices if u.is_print_function or u.pnp_class.lower() == "printer"]

    for q in snapshot.queues:
        driver = next((d for d in snapshot.drivers if d.name == q.driver), None)
        model = model_from(q.name, q.driver)
        vendor = detect_vendor(driver.manufacturer if driver else "", q.driver, q.name)
        dev = Device(
            device_id="queue:" + q.name, display_name=q.name, vendor_id=vendor.vendor_id, vendor_name=vendor.name,
            model=model, connection=q.connection, queue=q, simulated=snapshot.simulated,
        )
        if q.connection == ConnectionType.USB:
            best = max(print_usb, key=lambda u: usb_matches_queue(u, q.name, q.driver), default=None)
            if best and best.instance_id not in used_usb and usb_matches_queue(best, q.name, q.driver) >= 0.5:
                dev.usb = best
                used_usb.add(best.instance_id)
        if q.device_host:
            dev.probe = probes.get(q.device_host)
            used_hosts.add(q.device_host)
            ep = next((e for e in endpoints if e.host == q.device_host), None)
            if ep:
                dev.endpoint = ep
        devices.append(dev)

    for u in print_usb:
        if u.instance_id in used_usb or not u.present:
            continue
        vendor = detect_vendor(u.manufacturer, u.name)
        devices.append(Device(
            device_id="usb:" + u.instance_id, display_name=u.name or u.instance_id, vendor_id=vendor.vendor_id,
            vendor_name=vendor.name, model=u.name, connection=ConnectionType.USB, usb=u,
            simulated=snapshot.simulated,
        ))

    for ep in endpoints:
        if ep.host in used_hosts:
            continue
        probe = probes.get(ep.host)
        model = ep.model or (probe.ipp.make_and_model if probe and probe.ipp else "") or ep.name
        vendor = detect_vendor(ep.manufacturer, model, ep.name)
        conn = ConnectionType.IPP if any(s.protocol in ("ipp", "ipps") for s in ep.services) else (
            ConnectionType.WSD if "wsd" in ep.sources else ConnectionType.TCPIP)
        devices.append(Device(
            device_id="net:" + ep.key, display_name=ep.name or model or ep.host, vendor_id=vendor.vendor_id,
            vendor_name=vendor.name, model=model, connection=conn, endpoint=ep, probe=probe,
            simulated=ep.simulated or snapshot.simulated,
        ))
        used_hosts.add(ep.host)

    for d in devices:
        d.capabilities = compute_capabilities(d)
    return devices
