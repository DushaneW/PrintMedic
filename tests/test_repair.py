import time

from printmedic.core.models import QueueState
from printmedic.platform.base import OpResult
from printmedic.repair.actions import (
    AddIppPrinter,
    ClearQueue,
    Env,
    GuidedPhysical,
    OpenSupportUrl,
    RemoveJob,
    RestartSpooler,
    ResumeQueue,
    SetDefault,
    SetOnline,
    create,
)
from printmedic.repair.executor import Consent, RepairExecutor, grant

HP = "SIM HP OfficeJet Pro 9010"
BROTHER = "SIM Brother HL-L2350DW"


class Audit:
    def __init__(self):
        self.records = []

    def save_repair(self, r):
        self.records.append(r)


def make(sim, sim_net):
    audit = Audit()
    return RepairExecutor(Env(sim, sim_net), audit), audit


def test_refuses_without_consent_and_records_it(sim, sim_net):
    ex, audit = make(sim, sim_net)
    rec = ex.run(ResumeQueue(queue=HP), None)
    assert rec.status == "refused" and audit.records == [rec]
    assert sim.snapshot().queue(HP).state == QueueState.PAUSED


def test_consent_for_other_action_is_rejected(sim, sim_net):
    ex, _ = make(sim, sim_net)
    other = grant(ResumeQueue(queue=BROTHER), sim.snapshot())
    rec = ex.run(ResumeQueue(queue=HP), other)
    assert rec.status == "refused"


def test_resume_is_verified_by_rereading_state(sim, sim_net):
    ex, _ = make(sim, sim_net)
    a = ResumeQueue(queue=HP)
    rec = ex.run(a, grant(a, sim.snapshot()))
    assert rec.status == "verified"
    assert rec.before["queue"]["state"] == "paused" and rec.after["queue"]["state"] == "ready"


def test_exit_code_alone_is_never_success(sim, sim_net, monkeypatch):
    """The adapter claims success but nothing changes: the result must be 'failed'."""
    monkeypatch.setattr(sim, "resume_queue", lambda q: OpResult(True))
    ex, _ = make(sim, sim_net)
    a = ResumeQueue(queue=HP)
    rec = ex.run(a, grant(a, sim.snapshot()))
    assert rec.status == "failed"


def test_data_loss_requires_acknowledgement(sim, sim_net):
    ex, _ = make(sim, sim_net)
    a = ClearQueue(queue=BROTHER)
    assert ex.run(a, grant(a, sim.snapshot())).status == "refused"
    rec = ex.run(a, grant(a, sim.snapshot(), data_loss_ack=True))
    assert rec.status == "verified" and sim.snapshot().queue(BROTHER).jobs == []


def test_preview_change_voids_consent(sim, sim_net):
    ex, _ = make(sim, sim_net)
    a = ClearQueue(queue=HP)
    consent = grant(a, sim.snapshot(), data_loss_ack=True)
    sim.print_test_page(HP)  # a new job arrives after the user reviewed the preview
    rec = ex.run(a, consent)
    assert rec.status == "refused" and rec.exec_result["code"] == "preview_changed"
    assert len(sim.snapshot().queue(HP).jobs) == 3


def test_preconditions_block_execution(sim, sim_net):
    ex, _ = make(sim, sim_net)
    a = SetOnline(queue=HP)  # HP is paused, not in offline mode
    rec = ex.run(a, grant(a, sim.snapshot()))
    assert rec.status == "blocked" and rec.blocked_by[0][0] == "pre.not_offline_mode"


def test_remove_single_job(sim, sim_net):
    ex, _ = make(sim, sim_net)
    a = RemoveJob(queue=BROTHER, job_id="21")
    rec = ex.run(a, grant(a, sim.snapshot(), data_loss_ack=True))
    assert rec.status == "verified"
    assert [j.job_id for j in sim.snapshot().queue(BROTHER).jobs] == [22]


def test_set_default_records_rollback(sim, sim_net):
    a = SetDefault(queue=HP)
    prev = a.preview(sim.snapshot())
    assert prev.details["previous_default"] == "SIM Microsoft Print to PDF"
    ex, _ = make(sim, sim_net)
    assert ex.run(a, grant(a, sim.snapshot())).status == "verified"


def test_restart_spooler(sim, sim_net):
    sim.stop_spooler_for_test()
    ex, _ = make(sim, sim_net)
    a = RestartSpooler()
    assert a.requires_admin
    assert ex.run(a, grant(a, sim.snapshot(), admin_ack=True)).status == "verified"


def test_timeout_is_reported_not_success(sim, sim_net, monkeypatch):
    def slow(q):
        time.sleep(2)
        return OpResult(True)
    monkeypatch.setattr(sim, "set_online", slow)
    a = SetOnline(queue="SIM Kyocera ECOSYS P2040dn")
    a.timeout_s = 0.2
    ex, _ = make(sim, sim_net)
    rec = ex.run(a, grant(a, sim.snapshot()))
    assert rec.status == "timeout"


def test_support_url_allowlist(sim, sim_net):
    ex, _ = make(sim, sim_net)
    bad = OpenSupportUrl(url="https://evil.example.com/driver.exe", vendor="x")
    assert ex.run(bad, grant(bad, sim.snapshot())).status == "blocked"
    good = OpenSupportUrl(url="https://support.hp.com/", vendor="HP")
    assert ex.run(good, grant(good, sim.snapshot())).status == "done"
    assert sim.opened_urls == ["https://support.hp.com/"]


def test_add_ipp_printer_requires_device_answer(sim, sim_net):
    ex, _ = make(sim, sim_net)
    ok = AddIppPrinter(host="198.51.100.80", url="ipp://198.51.100.80:631/ipp/print", name="Xerox (IPP)", port="631")
    rec = ex.run(ok, grant(ok, sim.snapshot()))
    assert rec.status == "verified" and sim.snapshot().queue("Xerox (IPP)")
    dead = AddIppPrinter(host="192.0.2.50", url="ipp://192.0.2.50:631/ipp/print", name="Dead", port="631")
    rec = ex.run(dead, grant(dead, sim.snapshot()))
    assert rec.status == "failed" and sim.snapshot().queue("Dead") is None


def test_guided_physical_partial_while_device_still_reports_problem(sim, sim_net):
    ex, _ = make(sim, sim_net)
    resume = ResumeQueue(queue=HP)
    ex.run(resume, grant(resume, sim.snapshot()))
    g = GuidedPhysical(queue=HP, condition="replace_supply")
    rec = ex.run(g, grant(g, sim.snapshot()))
    assert rec.status == "partial"  # printer still reports marker-supply-empty


def test_action_registry_rejects_unknown():
    import pytest
    with pytest.raises(KeyError):
        create("format_c_drive", {})


def test_consent_is_immutable():
    import dataclasses

    import pytest
    c = Consent("k", "f", __import__("datetime").datetime.now())
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.action_key = "other"  # type: ignore[misc]
