import pytest

from printmedic.core.redaction import Redactor
from printmedic.core.validation import (
    ValidationError,
    normalize_host,
    subnet_hosts,
    validate_ipp_url,
    validate_job_id,
    validate_queue_name,
)


@pytest.mark.parametrize("value,expected", [
    ("192.168.1.40", "192.168.1.40"),
    (" Printer.Local. ", "printer.local"),
    ("[fe80::1]", "fe80::1"),
    ("hp-9010", "hp-9010"),
])
def test_normalize_host_accepts(value, expected):
    assert normalize_host(value) == expected


@pytest.mark.parametrize("value", [
    "", "a" * 300, "printer;rm -rf", "$(calc)", "host name", "-bad.local", "224.0.0.1", "0.0.0.0", "999.1.1.1",
    "a..b", "`whoami`", "printer\x00.local",
])
def test_normalize_host_rejects(value):
    with pytest.raises(ValidationError):
        normalize_host(value)


def test_queue_and_job_validation():
    assert validate_queue_name("HP (Kopia 1) 'quoted' \"x\"; $(y)") .startswith("HP")
    with pytest.raises(ValidationError):
        validate_queue_name("bad\x00name")
    with pytest.raises(ValidationError):
        validate_job_id(0)
    with pytest.raises(ValidationError):
        validate_job_id(True)


def test_ipp_url_validation():
    assert validate_ipp_url("ipp://192.168.1.4:631/ipp/print")
    for bad in ("file:///etc/passwd", "ipp://bad host/", "javascript:alert(1)", "ipp://h:99999/"):
        with pytest.raises((ValidationError, ValueError)):
            validate_ipp_url(bad)


def test_subnet_is_clamped_to_24_and_private_only():
    hosts = subnet_hosts("10.1.2.3", 8)
    assert len(hosts) == 253 and "10.1.2.3" not in hosts and all(h.startswith("10.1.2.") for h in hosts)
    with pytest.raises(ValidationError):
        subnet_hosts("8.8.8.8", 24)


def test_redaction_removes_sensitive_data():
    r = Redactor(extra_terms=["alice"])
    text = (r"C:\Users\alice\Documents\x.pdf 192.168.0.149 aa:bb:cc:dd:ee:ff "
            "urn:uuid:a35079ec-4c75-76c9-d77f-3a8e37ba10a0 serial number: CN12345XYZ fe80::1a2b:3c4d")
    out = r.text(text)
    for secret in ("alice", "192.168.0.149", "aa:bb:cc", "a35079ec", "CN12345XYZ", "1a2b:3c4d"):
        assert secret not in out
    data = r.data({"document": "Payslip.pdf", "owner": "bob", "nested": [{"fact": "host 10.0.0.5"}]})
    assert data["document"] == "<redacted>" and data["owner"] == "<redacted>"
    assert "10.0.0.5" not in data["nested"][0]["fact"]


def test_redaction_can_be_disabled():
    assert Redactor(enabled=False).text("192.168.1.1") == "192.168.1.1"
