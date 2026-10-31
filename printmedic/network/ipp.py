"""Minimal, defensive IPP/1.1–2.0 client (RFC 8010/8011) for Get-Printer-Attributes only.

PrintMedic only *reads* attributes over IPP. It never submits jobs or changes settings this way.
Responses are untrusted: the parser enforces size limits and bounds-checks every field.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import struct
from dataclasses import dataclass, field
from typing import Any

from printmedic.core.models import Consumable, IppStatus, utcnow

MAX_RESPONSE = 1_000_000
OP_GET_PRINTER_ATTRIBUTES = 0x000B

TAG_OPERATION = 0x01
TAG_END = 0x03

VT_INTEGER, VT_BOOLEAN, VT_ENUM = 0x21, 0x22, 0x23
VT_OCTET, VT_DATETIME, VT_RESOLUTION, VT_RANGE = 0x30, 0x31, 0x32, 0x33
VT_BEGIN_COLLECTION, VT_END_COLLECTION, VT_MEMBER_NAME = 0x34, 0x37, 0x4A
VT_TEXT_LANG, VT_NAME_LANG = 0x35, 0x36
VT_TEXT, VT_NAME, VT_KEYWORD, VT_URI, VT_CHARSET, VT_LANGUAGE, VT_MIME = (
    0x41, 0x42, 0x44, 0x45, 0x47, 0x48, 0x49)

REQUESTED = (
    "printer-state", "printer-state-reasons", "printer-state-message", "printer-make-and-model",
    "printer-name", "printer-info", "printer-uuid", "printer-firmware-string-version",
    "printer-is-accepting-jobs", "document-format-supported", "ipp-versions-supported",
    "marker-names", "marker-levels", "marker-colors", "marker-types", "marker-low-levels",
    "marker-high-levels", "printer-supply", "printer-supply-description",
)

PRINTER_STATES = {3: "idle", 4: "processing", 5: "stopped"}


class IppError(Exception):
    pass


@dataclass
class IppResponse:
    version: tuple[int, int]
    status_code: int
    request_id: int
    attributes: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status_code < 0x0100

    def first(self, name: str, default: Any = None) -> Any:
        values = self.attributes.get(name)
        return values[0] if values else default


def _attr(tag: int, name: str, value: bytes) -> bytes:
    n = name.encode("utf-8")
    return struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(value)) + value


def encode_get_printer_attributes(printer_uri: str, request_id: int = 1,
                                  version: tuple[int, int] = (2, 0),
                                  requested: tuple[str, ...] = REQUESTED) -> bytes:
    body = struct.pack(">BBHI", version[0], version[1], OP_GET_PRINTER_ATTRIBUTES, request_id)
    body += bytes([TAG_OPERATION])
    body += _attr(VT_CHARSET, "attributes-charset", b"utf-8")
    body += _attr(VT_LANGUAGE, "attributes-natural-language", b"en")
    body += _attr(VT_URI, "printer-uri", printer_uri.encode("utf-8"))
    for i, kw in enumerate(requested):
        body += _attr(VT_KEYWORD, "requested-attributes" if i == 0 else "", kw.encode("ascii"))
    body += bytes([TAG_END])
    return body


def _decode_value(tag: int, raw: bytes) -> Any:
    if tag in (VT_INTEGER, VT_ENUM):
        if len(raw) != 4:
            raise IppError("bad integer length")
        return struct.unpack(">i", raw)[0]
    if tag == VT_BOOLEAN:
        if len(raw) != 1:
            raise IppError("bad boolean length")
        return raw[0] != 0
    if tag == VT_RANGE and len(raw) == 8:
        return struct.unpack(">ii", raw)
    if tag == VT_RESOLUTION and len(raw) == 9:
        return struct.unpack(">iib", raw)
    if tag in (VT_TEXT_LANG, VT_NAME_LANG):
        if len(raw) < 4:
            raise IppError("bad text-with-language")
        llen = struct.unpack(">H", raw[:2])[0]
        off = 2 + llen
        if off + 2 > len(raw):
            raise IppError("bad text-with-language")
        tlen = struct.unpack(">H", raw[off:off + 2])[0]
        return raw[off + 2:off + 2 + tlen].decode("utf-8", errors="replace")
    if 0x40 <= tag <= 0x5F:
        return raw.decode("utf-8", errors="replace")
    if 0x10 <= tag <= 0x1F:  # out-of-band: unsupported / unknown / no-value
        return None
    return raw  # octetString, dateTime, unknown


def decode_response(data: bytes) -> IppResponse:
    if len(data) > MAX_RESPONSE:
        raise IppError("response too large")
    if len(data) < 9:
        raise IppError("response too short")
    major, minor, status, req_id = struct.unpack(">BBHI", data[:8])
    if major not in (1, 2):
        raise IppError(f"unsupported IPP version {major}.{minor}")
    resp = IppResponse((major, minor), status, req_id)
    pos = 8
    current: str | None = None
    depth = 0
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == TAG_END:
            return resp
        if tag < 0x10:  # begin-attribute-group
            current = None
            continue
        if pos + 2 > len(data):
            raise IppError("truncated name length")
        (nlen,) = struct.unpack(">H", data[pos:pos + 2])
        pos += 2
        if pos + nlen + 2 > len(data):
            raise IppError("truncated name")
        name = data[pos:pos + nlen].decode("utf-8", errors="replace")
        pos += nlen
        (vlen,) = struct.unpack(">H", data[pos:pos + 2])
        pos += 2
        if pos + vlen > len(data):
            raise IppError("truncated value")
        raw = data[pos:pos + vlen]
        pos += vlen
        if tag == VT_BEGIN_COLLECTION:
            if depth == 0 and name:
                current = name
                resp.attributes.setdefault(name, []).append({})
            depth += 1
            continue
        if tag == VT_END_COLLECTION:
            depth = max(0, depth - 1)
            continue
        if depth > 0:
            continue  # collection members are not needed for diagnostics
        value = _decode_value(tag, raw)
        if name:
            current = name
            resp.attributes.setdefault(name, []).append(value)
        elif current is not None:
            resp.attributes[current].append(value)
    raise IppError("missing end-of-attributes tag")


def _http_post(host: str, port: int, path: str, body: bytes, tls: bool, timeout: float) -> bytes:
    conn: http.client.HTTPConnection
    if tls:
        # Printers almost always present self-signed certificates. We only *read* status here and
        # send no credentials, so certificate validation is relaxed for this request only.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": "application/ipp",
                                                        "Accept": "application/ipp"})
        r = conn.getresponse()
        if r.status != 200:
            raise IppError(f"HTTP {r.status}")
        ctype = r.getheader("Content-Type", "")
        if "application/ipp" not in ctype.lower():
            raise IppError(f"unexpected content type {ctype!r}")
        data = r.read(MAX_RESPONSE + 1)
        return data
    finally:
        conn.close()


def query_printer(host: str, port: int = 631, path: str = "/ipp/print", tls: bool = False,
                  timeout: float = 4.0) -> IppResponse:
    scheme = "ipps" if tls else "ipp"
    uri_host = f"[{host}]" if ":" in host else host
    uri = f"{scheme}://{uri_host}:{port}{path}"
    last: Exception | None = None
    for version in ((2, 0), (1, 1)):
        try:
            data = _http_post(host, port, path, encode_get_printer_attributes(uri, version=version), tls, timeout)
            resp = decode_response(data)
            if resp.status_code == 0x0503 and version == (2, 0):  # version-not-supported
                continue
            return resp
        except (TimeoutError, OSError, http.client.HTTPException, IppError) as exc:
            last = exc
            if isinstance(exc, OSError | socket.timeout):
                break
    raise IppError(str(last) if last else "no response")


COMMON_PATHS = ("/ipp/print", "/ipp/printer", "/ipp", "/printers/ipp", "/")


def _consumables(resp: IppResponse) -> list[Consumable]:
    names = resp.attributes.get("marker-names") or []
    levels = resp.attributes.get("marker-levels") or []
    colors = resp.attributes.get("marker-colors") or []
    types = resp.attributes.get("marker-types") or []
    lows = resp.attributes.get("marker-low-levels") or []
    reasons = [str(r) for r in resp.attributes.get("printer-state-reasons") or []]
    out: list[Consumable] = []
    now = utcnow()
    for i, name in enumerate(names):
        level = levels[i] if i < len(levels) else None
        low = lows[i] if i < len(lows) else None
        percent: int | None = None
        state = "unknown"
        if isinstance(level, int):
            if 0 <= level <= 100:
                percent = level
                state = "empty" if level == 0 else ("low" if isinstance(low, int) and level <= low else "ok")
            elif level == -3:
                state = "some_remaining"  # RFC 3805: unknown amount, but some remains
        out.append(Consumable(
            name=str(name), color=str(colors[i]) if i < len(colors) and colors[i] else None,
            level_percent=percent, state=state, kind=str(types[i]) if i < len(types) else "",
            source="IPP marker-levels", read_at=now,
        ))
    # Device-wide supply reasons (no index) are surfaced by diagnostics from state_reasons.
    if not out and any(r.startswith(("marker-supply", "toner-", "ink-")) for r in reasons):
        out.append(Consumable(name="supply", color=None, level_percent=None, state="unknown",
                              source="IPP printer-state-reasons", read_at=now))
    return out


def to_status(resp: IppResponse, uri: str) -> IppStatus:
    state_raw = resp.first("printer-state")
    return IppStatus(
        uri=uri,
        state=PRINTER_STATES.get(state_raw, "unknown") if isinstance(state_raw, int) else "unknown",
        state_reasons=[str(r) for r in resp.attributes.get("printer-state-reasons") or [] if r],
        make_and_model=str(resp.first("printer-make-and-model") or ""),
        name=str(resp.first("printer-name") or resp.first("printer-info") or ""),
        firmware=str(resp.first("printer-firmware-string-version") or ""),
        accepting_jobs=resp.first("printer-is-accepting-jobs"),
        formats=[str(f) for f in resp.attributes.get("document-format-supported") or []],
        ipp_versions=[str(v) for v in resp.attributes.get("ipp-versions-supported") or []],
        uuid=str(resp.first("printer-uuid") or ""),
        consumables=_consumables(resp),
        raw_attribute_names=sorted(resp.attributes.keys()),
    )


def fetch_status(host: str, port: int = 631, tls: bool = False, paths: tuple[str, ...] = COMMON_PATHS,
                 timeout: float = 4.0) -> IppStatus:
    """Try the common IPP resource paths in order and return the first valid answer."""
    errors: list[str] = []
    for path in paths:
        try:
            resp = query_printer(host, port, path, tls=tls, timeout=timeout)
        except IppError as exc:
            errors.append(f"{path}: {exc}")
            if "timed out" in str(exc).lower() or "refused" in str(exc).lower():
                break
            continue
        if resp.ok and resp.attributes:
            scheme = "ipps" if tls else "ipp"
            return to_status(resp, f"{scheme}://{host}:{port}{path}")
        errors.append(f"{path}: IPP status 0x{resp.status_code:04x}")
    raise IppError("; ".join(errors) or "no IPP response")
