"""Qt UI smoke tests (offscreen). They drive real pages against the simulation backend."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QDialog, QPushButton

from printmedic.repair.actions import ClearQueue, SetOnline
from printmedic.ui import theme
from printmedic.ui.consent import ConsentDialog
from printmedic.ui.context import UiContext
from printmedic.ui.main_window import NAV, MainWindow

pytestmark = pytest.mark.ui


@pytest.fixture
def window(qtbot, core):
    core.refresh()
    ctx = UiContext(core, theme.LIGHT)
    w = MainWindow(ctx)
    qtbot.addWidget(w)
    w.show()
    ctx.bus.inventory_changed.emit()
    yield w
    QThreadPool.globalInstance().waitForDone(5000)


def test_every_menu_item_opens_a_real_page(window, qtbot):
    for i, key in enumerate(NAV):
        window.navigate(key, None)
        assert window.stack.currentIndex() == i
        page = window.stack.currentWidget()
        assert page is window.pages[key]
        assert page.findChildren(QPushButton) or key == "overview"


def test_no_dead_buttons(window):
    """Every visible push button is connected to something."""
    for key in NAV:
        window.navigate(key, None)
        for b in window.pages[key].findChildren(QPushButton):
            if b.isVisible() and b.isEnabled():
                assert b.receivers("2clicked(bool)") > 0 or b.receivers("2clicked()") > 0, (key, b.text())


def test_diagnostics_run_from_ui(window, qtbot):
    window.navigate("diagnostics", {"autorun": True})
    page = window.pages["diagnostics"]
    qtbot.waitUntil(lambda: page.task is None and bool(page.findings), timeout=20000)
    assert page.tree.topLevelItemCount() == 3
    assert window.ctx.core.plan


def test_language_and_theme_switch_rebuilds(window):
    s = window.ctx.core.settings
    s.language, s.theme = "pl", "dark"
    window.rebuild()
    assert window.nav.item(0).text() == "Przegląd"
    assert window.ctx.tokens.name == "dark"
    s.language, s.theme = "en", "light"
    window.rebuild()


def test_consent_dialog_blocks_and_requires_ack(window, qtbot):
    snap = window.ctx.core.snapshot
    blocked = ConsentDialog(SetOnline(queue="SIM HP OfficeJet Pro 9010"), snap, window)
    qtbot.addWidget(blocked)
    assert not blocked.ok.isEnabled()
    dlg = ConsentDialog(ClearQueue(queue="SIM Brother HL-L2350DW"), snap, window)
    qtbot.addWidget(dlg)
    assert not dlg.ok.isEnabled()
    dlg.ack.setChecked(True)
    assert dlg.ok.isEnabled()
    dlg.ok.click()
    assert dlg.result() == QDialog.DialogCode.Accepted and dlg.consent and dlg.consent.acknowledged_data_loss


def test_no_fake_mode_wording_in_ui(window):
    from PySide6.QtWidgets import QFrame, QLabel
    assert not any(f.objectName() == "simBanner" for f in window.findChildren(QFrame))
    for key in NAV:
        window.navigate(key, None)
        for w in [*window.findChildren(QLabel), *window.findChildren(QPushButton)]:
            text = w.text().lower()
            assert "simulat" not in text and "symulac" not in text and "[sim]" not in text, w.text()
