"""Drivers & Compatibility: installed driver metadata, usage, and verified vendor support links."""

from __future__ import annotations

import platform
from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem

from printmedic.i18n import tr
from printmedic.repair.actions import OpenSupportUrl
from printmedic.ui.widgets import Callout, Card, Page, button, label
from printmedic.vendors.profiles import detect_vendor, profile_by_id


class DriversPage(Page):
    title_key = "nav.drivers"
    subtitle_key = "drivers.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.content.addWidget(Callout(tr("drivers.policy"), "info", tr("drivers.policy_title")))
        info = Card(tr("drivers.system"))
        info.add(label(tr("drivers.system_line", os=f"{platform.system()} {platform.release()}",
                          arch=platform.machine()), selectable=True))
        self.content.addWidget(info)
        c = Card(tr("drivers.installed"))
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("drivers.search"))
        self.search.setAccessibleName(tr("drivers.search"))
        self.search.textChanged.connect(self.on_inventory)
        c.add(self.search)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([tr("drivers.col_name"), tr("drivers.col_mfr"), tr("drivers.col_version"),
                                              tr("drivers.col_env"), tr("drivers.col_model"), tr("drivers.col_used"),
                                              tr("drivers.col_inf")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setMinimumHeight(300)
        c.add(self.table)
        self.content.addWidget(c)
        self.support = Card(tr("drivers.support"))
        self.support_row = QHBoxLayout()
        self.support.add(label(tr("drivers.support_text"), "muted", wrap=True))
        self.support.add(self.support_row)
        self.content.addWidget(self.support)
        guide = Card(tr("drivers.guide_title"))
        for i in range(1, 6):
            guide.add(label(f"{i}.  " + tr(f"drivers.guide{i}"), wrap=True))
        guide.add(button(tr("discover.open_settings"), lambda: self.ctx.core.adapter.open_printer_settings()))
        self.content.addWidget(guide)
        self.content.addStretch(1)

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        self.on_inventory()

    def on_inventory(self) -> None:
        snap = self.ctx.core.snapshot
        drivers = snap.drivers if snap else []
        q = self.search.text().strip().lower()
        drivers = [d for d in drivers if not q or q in f"{d.name} {d.manufacturer}".lower()]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(drivers))
        for r, d in enumerate(drivers):
            used = [qq.name for qq in snap.queues if qq.driver == d.name] if snap else []
            model = {3: tr("drivers.v3"), 4: tr("drivers.v4")}.get(d.major_version or 0, "—")
            for col, v in enumerate([d.name, d.manufacturer, d.version, d.environment, model,
                                     ", ".join(used) or "—", d.inf_path]):
                item = QTableWidgetItem(v)
                item.setToolTip(v)
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, min(self.table.columnWidth(0), 300))
        self.table.setColumnWidth(6, min(self.table.columnWidth(6), 320))
        self.table.setSortingEnabled(True)

        while self.support_row.count():
            w = self.support_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        vendors = {}
        for dev in self.ctx.core.devices:
            if dev.connection.value == "virtual":
                continue
            p = profile_by_id(dev.vendor_id)
            vendors[p.vendor_id] = p
        for d in drivers:
            p = detect_vendor(d.manufacturer)
            if p.vendor_id not in ("generic", "microsoft") and any(qq.driver == d.name for qq in (
                    snap.queues if snap else [])):
                vendors[p.vendor_id] = p
        if not vendors:
            self.support_row.addWidget(label(tr("drivers.no_vendors"), "muted"))
        for p in vendors.values():
            self.support_row.addWidget(button(
                tr("drivers.open_support", vendor=p.name),
                lambda p=p: self.ctx.run_action(OpenSupportUrl(url=p.support_url, vendor=p.name))))
        self.support_row.addStretch(1)
