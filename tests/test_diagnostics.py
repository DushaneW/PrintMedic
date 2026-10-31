import copy

from printmedic.core.cancel import CancelToken
from printmedic.core.models import Certainty, Consumable, QueueState, ServiceStatus
from printmedic.diagnostics.catalog import CATALOG, render
from printmedic.diagnostics.checks import CHECKS, DiagnosticContext
from printmedic.diagnostics.engine import DiagnosticEngine
from printmedic.services.inventory import build_devices


def run(sim, sim_net, **kw):
    session, ctx = DiagnosticEngine(sim, sim_net).run(cancel=CancelToken(), **kw)
    return session, ctx


def codes(session):
    return {f.code for f in session.findings}


def test_simulated_scenario_findings(sim, sim_net):
    session, _ = run(sim, sim_net)
    c = codes(session)
    expected = {"PM-Q-001", "PM-Q-002", "PM-JOB-001", "PM-NET-002", "PM-USB-001", "PM-INK-001", "PM-INK-002",
                "PM-IPP-001", "PM-DEF-001", "PM-Q-006", "PM-PORT-004"}
    assert expected <= c, expected - c
    assert not session.errors


def test_every_produced_code_has_both_languages(sim, sim_net):
    session, _ = run(sim, sim_net, symptoms=["paper_jam", "blank_pages", "missing_color", "streaks", "faded",
                                             "misaligned", "nothing_prints", "offline_message", "not_found"])
    for f in session.findings:
        assert f.code in CATALOG, f.code
        assert {"en", "pl"} <= set(CATALOG[f.code])
        for lang in ("en", "pl"):
            assert "{" not in render(f.code, lang, f.params)["title"], (f.code, lang)


def test_findings_are_ranked_confirmed_first(sim, sim_net):
    session, _ = run(sim, sim_net, symptoms=["streaks"])
    ranks = [f.certainty.rank for f in session.findings]
    assert ranks == sorted(ranks, reverse=True)


def test_spooler_stopped_is_critical_and_planned_first(core):
    core.adapter.stop_spooler_for_test()
    session = core.diagnose(target=None, categories=None, symptoms=[], cancel=CancelToken())
    top = session.findings[0]
    assert top.code == "PM-SPL-001" and top.certainty == Certainty.CONFIRMED
    assert core.plan[0].action.action_id == "restart_spooler"


def test_user_symptom_without_evidence_is_only_possible(sim, sim_net):
    target = "queue:SIM Canon PIXMA TS5350"
    session, _ = run(sim, sim_net, target=target, symptoms=["streaks", "blank_pages"])
    for f in session.findings:
        if f.code in ("PM-SYM-STREAKS", "PM-SYM-BLANK"):
            assert f.certainty == Certainty.POSSIBLE


def test_symptom_corroborated_by_device_becomes_likely(sim, sim_net):
    target = "queue:SIM HP OfficeJet Pro 9010"
    session, _ = run(sim, sim_net, target=target, symptoms=["blank_pages", "missing_color"])
    by = {f.code: f for f in session.findings}
    assert by["PM-SYM-BLANK"].certainty == Certainty.LIKELY  # magenta reported empty
    assert by["PM-SYM-COLOR"].certainty == Certainty.LIKELY


def test_healthy_system_has_no_findings(sim, sim_net):
    snap = sim.snapshot()
    canon = snap.queue("SIM Canon PIXMA TS5350")
    canon.is_default = True
    snap.queues = [canon]
    snap.usb_devices = [u for u in snap.usb_devices if not u.driver_missing]
    session, _ = DiagnosticEngine(sim, sim_net).run(snapshot=snap, cancel=CancelToken())
    assert session.findings == []


def test_unreachable_network_printer_off_subnet_is_likely_routing(sim, sim_net):
    snap = sim.snapshot()
    for q in snap.queues:
        if q.device_host == "192.0.2.50":
            q.device_host = "203.0.113.9"
    session, _ = DiagnosticEngine(sim, sim_net).run(snapshot=snap, cancel=CancelToken())
    by = {(f.code, f.target): f for f in session.findings}
    f = by[("PM-NET-004", "SIM Brother HL-L2350DW")]
    assert f.certainty == Certainty.LIKELY


def test_consumables_info_only_when_quality_symptom_and_no_levels(sim, sim_net):
    session, _ = run(sim, sim_net, target="queue:SIM Canon PIXMA TS5350", symptoms=["faded"])
    assert "PM-INK-003" in codes(session)
    session, _ = run(sim, sim_net, target="queue:SIM Canon PIXMA TS5350")
    assert "PM-INK-003" not in codes(session)


def test_checks_are_pure_functions(sim, sim_net):
    snap = sim.snapshot()
    devices = build_devices(snap)
    ctx = DiagnosticContext(snapshot=snap, devices=devices)
    before = copy.deepcopy(snap)
    for spec in CHECKS:
        spec.fn(ctx)
    assert snap.queues == before.queues


def test_cancellation_marks_session(sim, sim_net):
    token = CancelToken()
    token.cancel()
    session, ctx = DiagnosticEngine(sim, sim_net).run(cancel=token)
    assert session.cancelled and ctx is None


def test_broken_check_does_not_hide_others(sim, sim_net, monkeypatch):
    from printmedic.diagnostics import checks
    def boom(ctx):
        raise RuntimeError("bug")
    monkeypatch.setattr(checks, "CHECKS", [checks.CheckSpec("boom", "queues", boom), *checks.CHECKS])
    from printmedic.diagnostics import engine
    monkeypatch.setattr(engine, "CHECKS", checks.CHECKS)
    session, _ = DiagnosticEngine(sim, sim_net).run(cancel=CancelToken())
    assert any("boom" in e for e in session.errors)
    assert "PM-Q-001" in codes(session)


def test_low_level_is_not_reported_as_empty(sim, sim_net):
    host = sim.hosts["192.0.2.21"]
    host.ipp.consumables = [Consumable("black", "#000000", 30, "ok", source="IPP"),]
    host.ipp.state_reasons = ["none"]
    host.ipp.state = "idle"
    session, _ = run(sim, sim_net, target="queue:SIM HP OfficeJet Pro 9010")
    assert not {"PM-INK-001", "PM-INK-002", "PM-IPP-001"} & codes(session)


def test_spooler_disabled_never_auto_repaired(sim, sim_net):
    snap = sim.snapshot()
    snap.spooler = ServiceStatus("Spooler", False, "Stopped", "Disabled")
    session, _ = DiagnosticEngine(sim, sim_net).run(snapshot=snap, cancel=CancelToken())
    f = next(f for f in session.findings if f.code == "PM-SPL-002")
    assert f.actions == []
    assert snap.queue("SIM HP OfficeJet Pro 9010").state == QueueState.PAUSED
