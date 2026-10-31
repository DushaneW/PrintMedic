"""End-to-end workflow in simulation: DISCOVER → IDENTIFY → INSPECT → DIAGNOSE → PLAN → AUTHORIZE → REPAIR →
VERIFY → REPORT."""

from printmedic.core.cancel import CancelToken
from printmedic.core.models import Capability, CapabilityLevel, QueueState
from printmedic.i18n import tr
from printmedic.repair.executor import grant
from printmedic.services import reports
from printmedic.services.discovery import DiscoveryOptions


def test_full_workflow(core, tmp_path):
    # discover
    rep = core.discover(DiscoveryOptions(methods=("installed", "usb", "mdns", "wsd")), CancelToken())
    assert rep.methods["installed"].status == "ok"
    assert any(e.host == "198.51.100.80" for e in core.endpoints)  # uninstalled Xerox found via (sim) mDNS
    # identify / inspect
    hp = core.device("queue:SIM HP OfficeJet Pro 9010")
    assert hp and hp.vendor_id == "hp" and hp.simulated
    assert hp.queue.state == QueueState.PAUSED
    # diagnose
    session = core.diagnose(target=hp.device_id, categories=None, symptoms=[], cancel=CancelToken())
    codes = [f.code for f in session.findings]
    assert "PM-Q-001" in codes and "PM-INK-001" in codes
    hp = core.device(hp.device_id)
    assert hp.capability(Capability.CONSUMABLES).level == CapabilityLevel.AUTOMATED
    # plan
    item = next(i for i in core.plan if i.action.action_id == "resume_queue")
    assert item.runnable and "PM-Q-001" in item.finding_codes
    # preview → authorize → repair → verify
    preview = item.action.preview(core.snapshot)
    assert preview.effects[0][0] == "preview.resume"
    rec = core.run_action(item.action, grant(item.action, core.snapshot), item.finding_codes)
    assert rec.status == "verified"
    assert core.device(hp.device_id).queue.state == QueueState.READY
    # re-diagnose: the queue finding is gone, the physical ink problem honestly remains
    session2 = core.diagnose(target=hp.device_id, categories=None, symptoms=[], cancel=CancelToken())
    codes2 = {f.code for f in session2.findings}
    assert "PM-Q-001" not in codes2 and "PM-INK-001" in codes2
    # record → report
    assert core.history.repairs()[0]["finding_codes"] == ["PM-Q-001"]
    report = reports.build_report(core.history.sessions(), core.history.repairs(), lang="en", tr=tr)
    path = reports.export(report, tmp_path / "report.md", tr)
    text = path.read_text(encoding="utf-8")
    assert "Print queue is paused" in text and "Verified" in text


def test_add_discovered_printer_flow(core):
    core.discover(DiscoveryOptions(methods=("installed", "mdns")), CancelToken())
    res = core.probe("198.51.100.80")
    assert res.ipp is not None
    from printmedic.repair.actions import AddIppPrinter
    a = AddIppPrinter(host="198.51.100.80", url=res.ipp.uri, name="Xerox VersaLink (IPP)", port="631")
    rec = core.run_action(a, grant(a, core.snapshot))
    assert rec.status == "verified"
    assert core.device("queue:Xerox VersaLink (IPP)") is not None
