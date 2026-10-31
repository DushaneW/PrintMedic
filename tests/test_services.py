import json

import pytest

from printmedic.core.cancel import Cancelled, CancelToken
from printmedic.core.models import (
    Capability,
    CapabilityLevel,
    ConnectionType,
    DiscoveredEndpoint,
    ServiceAdvert,
)
from printmedic.i18n import tr
from printmedic.services import reports
from printmedic.services.assistant import run_assistant
from printmedic.services.discovery import DiscoveryOptions, DiscoveryService, merge_endpoints
from printmedic.services.history import HistoryStore, session_to_dict
from printmedic.services.inventory import build_devices
from printmedic.services.settings import Settings, SettingsStore
from printmedic.services.urls import ALLOWED_DOMAINS, is_allowed_url
from printmedic.vendors.capabilities import compute_capabilities
from printmedic.vendors.profiles import GENERIC, PROFILES, detect_vendor


# --- vendors / capabilities ----------------------------------------------------------------
@pytest.mark.parametrize("text,vendor", [
    ("HP DJ 3700 series PCL-3", "hp"), ("Canon TS5300 series", "canon"), ("EPSON ET-2850 Series", "epson"),
    ("Brother HL-L2350D series", "brother"), ("Xerox VersaLink C405", "xerox"),
    ("Kyocera ECOSYS P2040dn KX", "kyocera"), ("Lexmark MS431", "lexmark"), ("RICOH SP C261", "ricoh"),
    ("Samsung Xpress M2020", "samsung"), ("Acme Printomatic", "generic"),
])
def test_vendor_detection(text, vendor):
    assert detect_vendor(text).vendor_id == vendor


def test_all_vendor_urls_on_allowlist():
    for p in (*PROFILES, GENERIC):
        assert is_allowed_url(p.support_url), p.support_url


@pytest.mark.parametrize("url", ["http://support.hp.com/", "https://support.hp.com.evil.io/",
                                 "https://user:pw@support.hp.com/", "https://support.hp.com:8443/",
                                 "file:///C:/x.exe", "https://example.com/"])
def test_url_allowlist_rejects(url):
    assert not is_allowed_url(url)
    assert "example.com" not in ALLOWED_DOMAINS


def test_capabilities_are_honest(sim, sim_net):
    from printmedic.network.probe import probe_host
    snap = sim.snapshot()
    probes = {"192.0.2.21": probe_host(sim_net, "192.0.2.21")}
    devs = {d.display_name: d for d in build_devices(snap, probes)}
    hp = devs["SIM HP OfficeJet Pro 9010"]
    assert hp.capability(Capability.CONSUMABLES).level == CapabilityLevel.AUTOMATED
    assert hp.capability(Capability.PRINTHEAD_CLEANING).level == CapabilityLevel.GUIDED
    canon = devs["SIM Canon PIXMA TS5350"]
    assert canon.connection == ConnectionType.USB
    assert canon.capability(Capability.CONSUMABLES).level == CapabilityLevel.UNSUPPORTED
    brother = devs["SIM Brother HL-L2350DW"]  # network but never probed
    assert brother.capability(Capability.DEVICE_STATUS).level == CapabilityLevel.UNKNOWN
    pdf = devs["SIM Microsoft Print to PDF"]
    assert pdf.capability(Capability.TEST_PAGE).level == CapabilityLevel.UNSUPPORTED
    # no capability is ever automated purely because of the brand name
    for d in devs.values():
        for c in compute_capabilities(d):
            if c.level == CapabilityLevel.AUTOMATED:
                assert c.source != "vendor profile (guidance only)"


def test_usb_device_without_queue_becomes_device(sim):
    devs = build_devices(sim.snapshot())
    epson = next(d for d in devs if d.device_id.startswith("usb:"))
    assert epson.vendor_id == "epson" and epson.queue is None and epson.usb.driver_missing


# --- discovery -----------------------------------------------------------------------------
def _ep(host, src, proto="ipp"):
    return DiscoveredEndpoint(host=host, sources={src}, services=[ServiceAdvert(proto, 631, "/ipp/print", src)])


def test_discovery_methods_fail_independently(sim, sim_net):
    def broken(d, c, i):
        raise OSError("multicast blocked")
    svc = DiscoveryService(sim.snapshot, sim_net, mdns_fn=broken, wsd_fn=lambda d, c, i: [_ep("192.0.2.99", "wsd")])
    rep = svc.run(DiscoveryOptions(methods=("installed", "usb", "mdns", "wsd")), CancelToken())
    assert rep.methods["mdns"].status == "failed" and "multicast" in rep.methods["mdns"].error
    assert rep.methods["wsd"].status == "ok" and rep.methods["installed"].count == 6
    assert rep.methods["subnet"].status == "skipped"
    assert [e.host for e in rep.endpoints] == ["192.0.2.99"]


def test_discovery_cancel(sim, sim_net):
    token = CancelToken()
    def mdns(d, c, i):
        token.cancel()
        return []
    svc = DiscoveryService(sim.snapshot, sim_net, mdns_fn=mdns, wsd_fn=lambda d, c, i: [])
    rep = svc.run(DiscoveryOptions(methods=("mdns", "wsd")), token)
    assert rep.cancelled and rep.methods["wsd"].status == "cancelled"


def test_subnet_requires_interface(sim, sim_net):
    svc = DiscoveryService(sim.snapshot, sim_net, mdns_fn=lambda *a: [], wsd_fn=lambda *a: [])
    rep = svc.run(DiscoveryOptions(methods=("subnet",)), CancelToken())
    assert rep.methods["subnet"].status == "failed"


def test_subnet_sweep_only_touches_printing_ports(sim, sim_net, monkeypatch):
    touched = set()
    orig = sim_net.tcp_check
    def spy(addr, port, timeout):
        touched.add(port)
        return orig(addr, port, timeout)
    monkeypatch.setattr(sim_net, "tcp_check", spy)
    svc = DiscoveryService(sim.snapshot, sim_net, mdns_fn=lambda *a: [], wsd_fn=lambda *a: [])
    rep = svc.run(DiscoveryOptions(methods=("subnet",), interface_address="192.0.2.10"), CancelToken())
    assert touched == {631, 9100}
    assert {e.host for e in rep.endpoints} == {"192.0.2.21"}


def test_merge_endpoints_dedupes():
    target = {}
    merge_endpoints(target, [_ep("10.0.0.5", "mdns")])
    merge_endpoints(target, [_ep("10.0.0.5", "wsd", "raw")])
    assert target["10.0.0.5"].sources == {"mdns", "wsd"} and len(target["10.0.0.5"].services) == 2


# --- assistant -----------------------------------------------------------------------------
def _assist(sim, sim_net, host, conn="wifi", eps=None):
    svc = DiscoveryService(sim.snapshot, sim_net, mdns_fn=lambda *a: list(eps or []), wsd_fn=lambda *a: [])
    return run_assistant(connection=conn, host=host, snapshot_fn=sim.snapshot, net=sim_net, discovery=svc,
                         cancel=CancelToken(), duration=0.1)


def test_assistant_verdicts(sim, sim_net):
    assert _assist(sim, sim_net, None, conn="usb").verdict == "usb_path"
    assert _assist(sim, sim_net, "no-such-printer.example").verdict == "dns"
    assert _assist(sim, sim_net, "192.0.2.50").verdict == "unreachable"
    assert _assist(sim, sim_net, "203.0.113.7").verdict == "routing"
    r = _assist(sim, sim_net, "198.51.100.80")
    assert r.verdict == "not_installed" and r.candidates[0].ipp_url
    assert _assist(sim, sim_net, "192.0.2.21").verdict == "os_config"
    assert _assist(sim, sim_net, None).verdict == "discovery"
    assert _assist(sim, sim_net, None, eps=[_ep("198.51.100.80", "mdns")]).verdict == "found_not_installed"


def test_assistant_respects_cancel(sim, sim_net):
    token = CancelToken()
    token.cancel()
    svc = DiscoveryService(sim.snapshot, sim_net, mdns_fn=lambda *a: [], wsd_fn=lambda *a: [])
    with pytest.raises(Cancelled):
        run_assistant(connection="wifi", host="192.0.2.21", snapshot_fn=sim.snapshot, net=sim_net, discovery=svc,
                      cancel=token)


# --- settings / history / reports ----------------------------------------------------------
def test_settings_roundtrip_and_validation(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.save(Settings(language="pl", theme="dark", discovery_duration=99, retention_days=-5))
    s = store.load()
    assert (s.language, s.theme, s.discovery_duration, s.retention_days) == ("pl", "dark", 20.0, 0)
    (tmp_path / "s.json").write_text("{not json", encoding="utf-8")
    assert store.load().language == "en"


def test_history_and_redacted_export(core, tmp_path):
    session = core.diagnose(target=None, categories=None, symptoms=[], cancel=CancelToken())
    item = next(i for i in core.plan if i.action.action_id == "resume_queue")
    from printmedic.repair.executor import grant
    core.run_action(item.action, grant(item.action, core.snapshot))
    h = core.history
    assert h.session(session.session_id)["summary"]["total"] == len(session.findings)
    assert h.repairs()[0]["status"] == "verified"
    rep = reports.build_report(h.sessions(), h.repairs(), lang="en", tr=tr, redact=True)
    for suffix in (".md", ".html", ".json"):
        out = reports.export(rep, tmp_path / f"r{suffix}", tr)
        text = out.read_text(encoding="utf-8")
        assert "192.0.2.50" not in text and "Quarterly report.pdf" not in text
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert data["redacted"] is True and data["sessions"][0]["findings"]
    raw = reports.build_report(h.sessions(), h.repairs(), lang="pl", tr=tr, redact=False)
    assert "192.0.2.50" in json.dumps(raw)
    h.clear()
    assert h.sessions() == [] and h.repairs() == []


def test_history_retention(tmp_path):
    from printmedic.diagnostics.engine import DiagnosticSession
    h = HistoryStore(tmp_path / "h.db")
    h.save_session(DiagnosticSession("old", "2000-01-01T00:00:00+00:00"))
    h.save_session(DiagnosticSession("new", "2999-01-01T00:00:00+00:00"))
    assert h.apply_retention(30) == 1
    assert [s["session_id"] for s in h.sessions()] == ["new"]
    assert session_to_dict(DiagnosticSession("x", "t"))["findings"] == []
