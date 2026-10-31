import struct

import pytest

from printmedic.network import ipp
from printmedic.network.wsd import parse_probe_match


def _attr(tag: int, name: str, value: bytes) -> bytes:
    n = name.encode()
    return struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(value)) + value


def _int(tag: int, name: str, v: int) -> bytes:
    return _attr(tag, name, struct.pack(">i", v))


def build_response(levels=(80, 5, 0, -3), state=5, reasons=(b"marker-supply-empty-error",)) -> bytes:
    body = struct.pack(">BBHI", 2, 0, 0x0000, 1)
    body += bytes([0x01]) + _attr(0x47, "attributes-charset", b"utf-8")
    body += bytes([0x04])
    body += _int(0x23, "printer-state", state)
    body += _attr(0x44, "printer-state-reasons", reasons[0])
    for r in reasons[1:]:
        body += _attr(0x44, "", r)
    body += _attr(0x41, "printer-make-and-model", b"Test Inkjet 1000")
    body += _attr(0x41, "printer-firmware-string-version", b"1.2.3")
    body += _attr(0x22, "printer-is-accepting-jobs", b"\x01")
    names = [b"Black", b"Cyan", b"Magenta", b"Yellow"]
    body += _attr(0x42, "marker-names", names[0]) + b"".join(_attr(0x42, "", n) for n in names[1:])
    body += _int(0x21, "marker-levels", levels[0]) + b"".join(_int(0x21, "", v) for v in levels[1:])
    body += _int(0x21, "marker-low-levels", 10) + b"".join(_int(0x21, "", 10) for _ in levels[1:])
    body += _attr(0x42, "marker-colors", b"#000000") + _attr(0x42, "", b"#00FFFF") + _attr(0x42, "", b"#FF00FF") \
        + _attr(0x42, "", b"#FFFF00")
    # a collection that must be skipped safely
    body += _attr(0x34, "printer-supply-col", b"") + _attr(0x4A, "", b"member") + _attr(0x44, "", b"x") \
        + _attr(0x37, "", b"")
    body += _attr(0x35, "printer-info", struct.pack(">H", 2) + b"en" + struct.pack(">H", 5) + b"hello")
    body += bytes([0x03])
    return body


def test_encode_request_structure():
    req = ipp.encode_get_printer_attributes("ipp://h:631/ipp/print", request_id=7)
    assert req[:2] == b"\x02\x00"
    assert struct.unpack(">H", req[2:4])[0] == ipp.OP_GET_PRINTER_ATTRIBUTES
    assert struct.unpack(">I", req[4:8])[0] == 7
    assert req.endswith(b"\x03")
    assert b"marker-levels" in req and b"printer-uri" in req


def test_decode_and_status_mapping():
    resp = ipp.decode_response(build_response())
    assert resp.ok
    st = ipp.to_status(resp, "ipp://h:631/ipp/print")
    assert st.state == "stopped"
    assert st.make_and_model == "Test Inkjet 1000"
    assert st.firmware == "1.2.3"
    assert st.accepting_jobs is True
    assert resp.first("printer-info") == "hello"
    by = {c.name: c for c in st.consumables}
    assert by["Black"].level_percent == 80 and by["Black"].state == "ok"
    assert by["Cyan"].state == "low"
    assert by["Magenta"].state == "empty"
    assert by["Yellow"].level_percent is None and by["Yellow"].state == "some_remaining"
    assert "printer-supply-col" in resp.attributes  # collection recorded, members skipped
    assert "member" not in resp.attributes


@pytest.mark.parametrize("cut", [5, 12, 30, 60])
def test_truncated_responses_raise(cut):
    with pytest.raises(ipp.IppError):
        ipp.decode_response(build_response()[:cut])


def test_oversized_and_bad_version_rejected():
    with pytest.raises(ipp.IppError):
        ipp.decode_response(b"\x02\x00" + b"\x00" * (ipp.MAX_RESPONSE + 10))
    with pytest.raises(ipp.IppError):
        ipp.decode_response(b"\x09\x00\x00\x00\x00\x00\x00\x01\x03")


def test_unknown_levels_never_invented():
    st = ipp.to_status(ipp.decode_response(build_response(levels=(-1, -2, 101, 50))), "u")
    states = [(c.level_percent, c.state) for c in st.consumables]
    assert states[0] == (None, "unknown") and states[1] == (None, "unknown") and states[2] == (None, "unknown")
    assert states[3] == (50, "ok")


PROBE_MATCH = b"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><s:Body><d:ProbeMatches><d:ProbeMatch>
<a:EndpointReference><a:Address>urn:uuid:1234</a:Address></a:EndpointReference>
<d:Types>wprt:PrintDeviceType wsdp:Device</d:Types><d:XAddrs>http://192.168.0.149:3911/ http://[fe80::1]:3911/</d:XAddrs>
</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"""


def test_wsd_probe_match_parsing():
    out = parse_probe_match(PROBE_MATCH)
    assert out == [("urn:uuid:1234", ["http://192.168.0.149:3911/", "http://[fe80::1]:3911/"],
                    "wprt:PrintDeviceType wsdp:Device")]


def test_wsd_rejects_doctype_and_garbage():
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    assert parse_probe_match(evil) == []
    assert parse_probe_match(b"not xml") == []
