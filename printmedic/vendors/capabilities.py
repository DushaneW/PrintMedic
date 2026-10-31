"""Capability registry: decides, per device, what PrintMedic can actually do and why.

A capability is only AUTOMATED when a concrete interface backs it for *this* device (the OS
spooler for queue operations, an IPP response for device status/consumables). Brand alone
never enables anything.
"""

from __future__ import annotations

from printmedic.core.models import (
    Capability,
    CapabilityInfo,
    CapabilityLevel,
    ConnectionType,
    Device,
)
from printmedic.vendors.profiles import profile_by_id

A = CapabilityLevel.AUTOMATED
G = CapabilityLevel.GUIDED
U = CapabilityLevel.UNSUPPORTED
K = CapabilityLevel.UNKNOWN

SPOOLER = "OS print spooler"
IPP = "IPP Get-Printer-Attributes"
VENDOR = "vendor profile (guidance only)"


def compute_capabilities(device: Device) -> list[CapabilityInfo]:
    caps: list[CapabilityInfo] = []
    q = device.queue
    ipp = device.probe.ipp if device.probe else None
    is_network = device.connection in (ConnectionType.WSD, ConnectionType.IPP, ConnectionType.TCPIP)
    virtual = device.connection == ConnectionType.VIRTUAL
    vendor = profile_by_id(device.vendor_id)

    sources = []
    if q:
        sources.append(SPOOLER)
    if device.usb:
        sources.append("USB/PnP enumeration")
    if device.endpoint:
        sources.extend(sorted(device.endpoint.sources))
    caps.append(CapabilityInfo(Capability.DISCOVERY, A, ", ".join(sources) or "unknown"))

    if ipp and ipp.make_and_model:
        caps.append(CapabilityInfo(Capability.MODEL_ID, A, IPP))
    elif device.model:
        caps.append(CapabilityInfo(Capability.MODEL_ID, A, "driver / PnP metadata",
                                   "capnote.model_inferred"))
    else:
        caps.append(CapabilityInfo(Capability.MODEL_ID, K, "none"))

    if q:
        caps.append(CapabilityInfo(Capability.QUEUE_STATUS, A, SPOOLER))
        caps.append(CapabilityInfo(Capability.JOB_MANAGEMENT, A, SPOOLER))
        caps.append(CapabilityInfo(Capability.SET_DEFAULT, A, SPOOLER))
        caps.append(CapabilityInfo(Capability.TEST_PAGE, U if virtual else A, SPOOLER,
                                   "capnote.virtual_file" if virtual else ""))
    else:
        for cap in (Capability.QUEUE_STATUS, Capability.JOB_MANAGEMENT, Capability.SET_DEFAULT,
                    Capability.TEST_PAGE):
            caps.append(CapabilityInfo(cap, U, "none", "capnote.no_queue"))

    if virtual:
        for cap in (Capability.DEVICE_STATUS, Capability.CONSUMABLES, Capability.FIRMWARE_INFO,
                    Capability.PRINTHEAD_CLEANING, Capability.ALIGNMENT, Capability.NETWORK_CONFIG,
                    Capability.DIAGNOSTIC_INFO):
            caps.append(CapabilityInfo(cap, U, "n/a", "capnote.virtual"))
        return caps

    if ipp:
        caps.append(CapabilityInfo(Capability.DEVICE_STATUS, A, IPP))
        levels = [c for c in ipp.consumables if c.level_percent is not None or c.state != "unknown"]
        if levels:
            caps.append(CapabilityInfo(Capability.CONSUMABLES, A, IPP))
        else:
            caps.append(CapabilityInfo(Capability.CONSUMABLES, U, IPP,
                                       "capnote.no_markers"))
        caps.append(CapabilityInfo(Capability.FIRMWARE_INFO, A if ipp.firmware else U, IPP))
        caps.append(CapabilityInfo(Capability.DIAGNOSTIC_INFO, A, IPP, "capnote.ipp_state"))
    elif is_network:
        note = "capnote.not_probed"
        for cap in (Capability.DEVICE_STATUS, Capability.CONSUMABLES, Capability.FIRMWARE_INFO):
            caps.append(CapabilityInfo(cap, K, "network probe", note))
        caps.append(CapabilityInfo(Capability.DIAGNOSTIC_INFO, G, VENDOR))
    else:
        caps.append(CapabilityInfo(Capability.DEVICE_STATUS, U, SPOOLER, "capnote.usb_status"))
        caps.append(CapabilityInfo(Capability.CONSUMABLES, U, "none", "capnote.usb_supplies"))
        caps.append(CapabilityInfo(Capability.FIRMWARE_INFO, U, "none", "capnote.usb_no_channel"))
        caps.append(CapabilityInfo(Capability.DIAGNOSTIC_INFO, G, VENDOR))

    guidance = "capnote.guided"
    caps.append(CapabilityInfo(Capability.PRINTHEAD_CLEANING, G if vendor.maintenance else U, VENDOR, guidance))
    caps.append(CapabilityInfo(Capability.ALIGNMENT, G if vendor.maintenance else U, VENDOR, guidance))
    caps.append(CapabilityInfo(Capability.NETWORK_CONFIG, G if is_network else U, VENDOR,
                               "capnote.no_network_changes"))
    return caps
