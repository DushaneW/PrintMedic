"""Main window: sidebar navigation, page stack and status bar."""

from __future__ import annotations

import contextlib
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from printmedic import __version__
from printmedic.i18n import set_language, tr
from printmedic.ui import theme
from printmedic.ui.context import UiContext
from printmedic.ui.icons import icon
from printmedic.ui.pages.consumables import ConsumablesPage
from printmedic.ui.pages.diagnostics import DiagnosticsPage
from printmedic.ui.pages.discover import DiscoverPage
from printmedic.ui.pages.drivers import DriversPage
from printmedic.ui.pages.network import NetworkPage
from printmedic.ui.pages.overview import OverviewPage
from printmedic.ui.pages.printers import PrintersPage
from printmedic.ui.pages.quality import QualityPage
from printmedic.ui.pages.repair import RepairPage
from printmedic.ui.pages.reports import ReportsPage
from printmedic.ui.pages.settings import SettingsPage
from printmedic.ui.widgets import label

NAV = ("overview", "printers", "discover", "diagnostics", "repair", "consumables", "quality", "drivers",
       "network", "reports", "settings")


class MainWindow(QMainWindow):
    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx = ctx
        ctx.window = self
        self.pages: dict[str, Any] = {}
        self.setMinimumSize(1100, 720)
        self.resize(1360, 880)
        self.tray: QSystemTrayIcon | None = None
        ctx.notify = self._notify
        ctx.bus.navigate.connect(self.navigate)
        ctx.bus.inventory_changed.connect(self._on_inventory)
        ctx.bus.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        ctx.bus.busy.connect(self._busy)
        self._busy_count = 0
        self._build()
        for i in range(min(9, len(NAV))):
            act = QAction(self)
            act.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            act.triggered.connect(lambda _=False, i=i: self.navigate(NAV[i], None))
            self.addAction(act)
        refresh = QAction(self)
        refresh.setShortcut(QKeySequence("F5"))
        refresh.triggered.connect(lambda: ctx.refresh_inventory())
        self.addAction(refresh)

    # ------------------------------------------------------------------------------------------
    def apply_theme(self) -> None:
        t = theme.resolve(self.ctx.core.settings.theme)
        self.ctx.tokens = t
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setPalette(theme.palette(t))
            app.setStyleSheet(theme.stylesheet(t))
        self.setWindowIcon(icon("printers", t.accent, 32))

    def _build(self) -> None:
        set_language(self.ctx.core.settings.language)
        self.apply_theme()
        self.setWindowTitle(tr("app.name"))
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 12)
        sv.setSpacing(0)
        brand = QLabel(tr("app.name"))
        brand.setObjectName("brand")
        sv.addWidget(brand)
        sub = QLabel(tr("app.tagline"))
        sub.setObjectName("brandSub")
        sub.setWordWrap(True)
        sv.addWidget(sub)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.nav.setAccessibleName(tr("nav.accessible"))
        t = self.ctx.tokens
        for key in NAV:
            item = QListWidgetItem(icon(key, t.muted), tr(f"nav.{key}"))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(tr(f"nav.{key}"))
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self._on_nav)
        sv.addWidget(self.nav, 1)
        foot = label(f"v{__version__} · " + self.ctx.core.adapter.name, "small", wrap=True)
        foot.setContentsMargins(18, 0, 12, 0)
        sv.addWidget(foot)
        h.addWidget(side)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        holder = QWidget()
        holder.setLayout(right)
        h.addWidget(holder, 1)
        self.setCentralWidget(root)

        self.pages = {
            "overview": OverviewPage(self.ctx),
            "printers": PrintersPage(self.ctx),
            "discover": DiscoverPage(self.ctx),
            "diagnostics": DiagnosticsPage(self.ctx),
            "repair": RepairPage(self.ctx),
            "consumables": ConsumablesPage(self.ctx),
            "quality": QualityPage(self.ctx),
            "drivers": DriversPage(self.ctx),
            "network": NetworkPage(self.ctx),
            "reports": ReportsPage(self.ctx),
            "settings": SettingsPage(self.ctx, self.rebuild),
        }
        for key in NAV:
            self.stack.addWidget(self.pages[key])

        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setFixedWidth(120)
        self.busy_bar.setMaximumHeight(8)
        self.busy_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.busy_bar)

    def rebuild(self) -> None:
        current = NAV[self.nav.currentRow()] if self.nav.currentRow() >= 0 else "overview"
        old = self.centralWidget()
        for sig in (self.ctx.bus.inventory_changed, self.ctx.bus.history_changed, self.ctx.bus.session_changed,
                    self.ctx.bus.busy):
            with contextlib.suppress(RuntimeError, TypeError):
                sig.disconnect()
        self.ctx.bus.inventory_changed.connect(self._on_inventory)
        self.ctx.bus.busy.connect(self._busy)
        self._busy_count = 0
        self.statusBar().removeWidget(self.busy_bar)
        self._build()
        if old is not None:
            old.deleteLater()
        self.navigate(current, None)
        if self.ctx.core.snapshot is None:
            self.ctx.refresh_inventory()

    # ------------------------------------------------------------------------------------------
    def navigate(self, key: str, payload: Any) -> None:
        if key not in NAV:
            return
        row = NAV.index(key)
        self._pending_payload = payload
        if self.nav.currentRow() == row:
            self._on_nav(row)
        else:
            self.nav.setCurrentRow(row)

    def _on_nav(self, row: int) -> None:
        if row < 0:
            return
        key = NAV[row]
        t = self.ctx.tokens
        for i in range(self.nav.count()):
            it = self.nav.item(i)
            it.setIcon(icon(NAV[i], t.accent if i == row else t.muted))
        self.stack.setCurrentIndex(row)
        payload = getattr(self, "_pending_payload", None)
        self._pending_payload = None
        self.pages[key].on_shown(payload)

    def _on_inventory(self) -> None:
        w = self.stack.currentWidget()
        if w is not None and hasattr(w, "on_inventory"):
            w.on_inventory()
        for key in ("overview",):
            if self.pages.get(key) is not w:
                self.pages[key].on_inventory()

    def _busy(self, on: bool) -> None:
        self._busy_count = max(0, self._busy_count + (1 if on else -1))
        self.busy_bar.setVisible(self._busy_count > 0)

    def _notify(self, title: str, message: str) -> None:
        if not self.ctx.core.settings.notifications or self.isActiveWindow():
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.tray is None:
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip(tr("app.name"))
            self.tray.show()
        self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 6000)

    def closeEvent(self, event: Any) -> None:
        from printmedic.ui import tasks
        tasks.wait_all(2000)
        super().closeEvent(event)
