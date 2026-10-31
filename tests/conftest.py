from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from printmedic.app_core import AppCore
from printmedic.i18n import set_language
from printmedic.platform.simulated import SimulatedAdapter, SimulatedNetworkProbe, sim_endpoints
from printmedic.services.discovery import DiscoveryService
from printmedic.services.settings import Settings, SettingsStore

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRINTMEDIC_DATA_DIR", str(tmp_path / "data"))
    set_language("en")


@pytest.fixture
def sim() -> SimulatedAdapter:
    return SimulatedAdapter()


@pytest.fixture
def sim_net(sim: SimulatedAdapter) -> SimulatedNetworkProbe:
    return SimulatedNetworkProbe(sim)


@pytest.fixture
def core(tmp_path: Path) -> AppCore:
    store = SettingsStore(tmp_path / "settings.json")
    # Test-only fake print subsystem; the product itself never creates it.
    sim = SimulatedAdapter()
    net = SimulatedNetworkProbe(sim)
    disc = DiscoveryService(sim.snapshot, net, mdns_fn=lambda d, c, i: sim_endpoints(sim.hosts),
                            wsd_fn=lambda d, c, i: [])
    return AppCore(settings=Settings(), settings_store=store, history_path=tmp_path / "h.db",
                   backend=(sim, net, disc))
