import base64
import json
import pathlib
import re
import sys

import pytest

from printmedic.core.models import ConnectionType, QueueState
from printmedic.platform.windows import powershell
from printmedic.platform.windows.adapter import (
    classify_connection,
    decode_driver_version,
    interpret_mutation,
    map_state,
    parse_inventory,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "windows_inventory.json"


def test_parse_inventory_fixture():
    snap = parse_inventory(json.loads(FIXTURE.read_text(encoding="utf-8")))
    hp = snap.queue("HPE8F1F1 (HP DeskJet 3700 series)")
    assert hp and hp.connection == ConnectionType.WSD
    assert hp.state == QueueState.USER_INTERVENTION and hp.is_default
    assert hp.device_host == "192.168.0.149" and hp.device_port == 3911
    assert [j.job_id for j in hp.jobs] == [3, 4] and hp.jobs[1].is_error
    assert hp.jobs[0].submitted is not None
    pdf = snap.queue("Microsoft Print to PDF")
    assert pdf and pdf.connection == ConnectionType.VIRTUAL
    laser = snap.queue("Office Laser")
    assert laser and laser.connection == ConnectionType.TCPIP and laser.device_host == "10.0.0.50"
    assert laser.state == QueueState.PAUSED and len(laser.jobs) == 1  # single object coerced to list
    assert snap.drivers[0].version == "20.79.1.6341"
    assert snap.usb_devices[0].driver_missing and snap.usb_devices[0].is_print_function
    assert snap.spooler and snap.spooler.running
    assert snap.interfaces[0].kind == "wifi"


def test_driver_version_decoding():
    assert decode_driver_version(str((10 << 48) | (0 << 32) | (26100 << 16) | 1)) == "10.0.26100.1"
    assert decode_driver_version("garbage") == "garbage"
    assert decode_driver_version("0") == ""


@pytest.mark.parametrize("flags,offline,state", [
    (["Normal"], False, QueueState.READY), ([], False, QueueState.READY),
    (["Paused", "Error"], False, QueueState.PAUSED), (["Normal"], True, QueueState.OFFLINE),
    (["PaperJam"], False, QueueState.ERROR), (["UserIntervention"], False, QueueState.USER_INTERVENTION),
    (["Printing"], False, QueueState.PRINTING), (["SomethingNew"], False, QueueState.UNKNOWN),
])
def test_map_state(flags, offline, state):
    assert map_state(flags, offline) == state


def test_classify_connection():
    assert classify_connection("q", "USB001", "x", {}) == ConnectionType.USB
    assert classify_connection("q", "http://1.2.3.4:631/ipp/print", "x", {}) == ConnectionType.IPP
    assert classify_connection("\\\\srv\\q", "x", "x", {}) == ConnectionType.SHARED
    assert classify_connection("q", "nul:", "x", {}) == ConnectionType.VIRTUAL
    assert classify_connection("q", "LPT1:", "x", {}) == ConnectionType.LOCAL


def test_interpret_mutation_codes():
    assert interpret_mutation({"ok": True, "rv": 0}).ok
    assert interpret_mutation({"ok": True, "rv": 5}).code == "access_denied"
    assert interpret_mutation({"ok": False, "message": "x", "native": 1223}).code == "cancelled_by_user"
    assert interpret_mutation({"ok": False, "message": "x", "category": "PermissionDenied"}).code == "access_denied"
    assert interpret_mutation({"ok": False, "message": "PM_NOT_FOUND"}).code == "not_found"
    assert interpret_mutation(None, "boom").code == "failed"


# --- security properties of the PowerShell bridge -------------------------------------------------
def test_parameters_travel_only_through_environment():
    evil = "HP\"; Remove-Item C:\\ -Recurse; $(calc) '`"
    argv, env = powershell.build_command("resume_queue", {"QUEUE": evil})
    assert env["PM_QUEUE"] == evil
    assert all(evil not in a for a in argv)
    decoded = base64.b64decode(argv[-1]).decode("utf-16-le")
    assert evil not in decoded and "$env:PM_QUEUE" in decoded
    assert argv[:5] == [powershell.powershell_path(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"]


def test_unknown_scripts_and_parameters_rejected():
    with pytest.raises(KeyError):
        powershell.build_command("format_disk", {})
    with pytest.raises(ValueError):
        powershell.build_command("resume_queue", {"COMMAND": "x"})
    with pytest.raises(ValueError):
        powershell.build_command("resume_queue", {"QUEUE": "a\x00b"})


def test_scripts_contain_no_dynamic_execution():
    for name, (body, _allowed) in powershell.SCRIPTS.items():
        lowered = body.lower()
        for forbidden in ("invoke-expression", "iex ", "downloadstring", "invoke-webrequest", "start-bitstransfer",
                          "remove-printer", "remove-printerdriver", "set-service", "disable-"):
            assert forbidden not in lowered, (name, forbidden)


def test_inherited_pm_variables_are_not_leaked(monkeypatch):
    monkeypatch.setenv("PM_QUEUE", "inherited")
    _argv, env = powershell.build_command("inventory", {})
    assert "PM_QUEUE" not in env


def test_no_shell_true_anywhere():
    root = pathlib.Path(__file__).parents[1] / "printmedic"
    for p in root.rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        assert not re.search(r"shell\s*=\s*True", src), p
        assert "os.system(" not in src, p


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows")
def test_real_windows_inventory_is_readable():
    """Read-only smoke test against the real print subsystem of the machine running the tests."""
    from printmedic.platform.windows.adapter import WindowsAdapter
    snap = WindowsAdapter().snapshot()
    assert snap.platform == "windows"
    assert snap.spooler is not None
    assert isinstance(snap.queues, list)
