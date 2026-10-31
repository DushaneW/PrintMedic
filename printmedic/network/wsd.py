"""WS-Discovery (UDP 3702 multicast) probe for printers that only advertise via WSD.

One Probe message is sent per call; replies are collected for a bounded time. Replies are
untrusted XML: size-limited, DOCTYPE rejected, parsed with the stdlib parser (expat ≥ 2.4
has entity-expansion protection).
"""

from __future__ import annotations

import socket
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from printmedic.core.cancel import CancelToken
from printmedic.core.models import DiscoveredEndpoint, ServiceAdvert

MULTICAST = ("239.255.255.250", 3702)
MAX_DATAGRAM = 65_507
_NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "a": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
    "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
}

PROBE_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
 xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:wsd="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:wsdp="http://schemas.xmlsoap.org/ws/2006/02/devprof"
 xmlns:wprt="http://schemas.microsoft.com/windows/2006/08/wdp/print">
<soap:Header>
<wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>
<wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action>
<wsa:MessageID>urn:uuid:{mid}</wsa:MessageID>
</soap:Header>
<soap:Body><wsd:Probe><wsd:Types>wprt:PrintDeviceType</wsd:Types></wsd:Probe></soap:Body>
</soap:Envelope>"""


def parse_probe_match(data: bytes) -> list[tuple[str, list[str], str]]:
    """Return [(endpoint_reference, xaddrs, types)] from a ProbeMatches message."""
    if len(data) > MAX_DATAGRAM or b"<!DOCTYPE" in data.upper():
        return []
    try:
        root = ET.fromstring(data)  # noqa: S314 - size-limited, DOCTYPE rejected above
    except ET.ParseError:
        return []
    out = []
    for match in root.iter(f"{{{_NS['d']}}}ProbeMatch"):
        epr = match.findtext(f"{{{_NS['a']}}}EndpointReference/{{{_NS['a']}}}Address", default="")
        xaddrs = (match.findtext(f"{{{_NS['d']}}}XAddrs", default="") or "").split()
        types = match.findtext(f"{{{_NS['d']}}}Types", default="") or ""
        out.append((epr.strip()[:256], [x[:512] for x in xaddrs[:8]], types[:512]))
    return out


def probe(duration: float, cancel: CancelToken, interface_address: str | None = None) -> list[DiscoveredEndpoint]:
    msg = PROBE_TEMPLATE.format(mid=uuid.uuid4()).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    found: dict[str, DiscoveredEndpoint] = {}
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # never leave the local link
        if interface_address:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_address))
            sock.bind((interface_address, 0))
        sock.settimeout(0.3)
        sock.sendto(msg, MULTICAST)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and not cancel.cancelled:
            try:
                data, (addr, _port) = sock.recvfrom(MAX_DATAGRAM)
            except TimeoutError:
                continue
            except OSError:
                break
            for epr, xaddrs, types in parse_probe_match(data):
                if "print" not in types.lower() and "Print" not in types:
                    continue
                key = epr or addr
                ep = found.setdefault(key, DiscoveredEndpoint(host=addr, uuid=epr.replace("urn:uuid:", "")))
                ep.sources.add("wsd")
                for xa in xaddrs:
                    parts = urlsplit(xa)
                    if parts.hostname and ":" not in parts.hostname:
                        ep.host = parts.hostname
                    ep.services.append(ServiceAdvert(protocol="wsd", port=parts.port or 80,
                                                     path=parts.path, source="wsd"))
                if len(found) >= 256:
                    break
    finally:
        sock.close()
    return list(found.values())
