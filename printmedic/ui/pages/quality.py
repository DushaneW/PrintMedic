"""Print Quality & Maintenance: symptom-guided troubleshooting with honest maintenance support levels."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QButtonGroup, QComboBox, QGridLayout, QHBoxLayout, QRadioButton, QVBoxLayout

from printmedic.core.models import Capability
from printmedic.i18n import language, tr
from printmedic.i18n.en import STRINGS as EN
from printmedic.repair.actions import PrintTestPage
from printmedic.ui.pages.common import physical
from printmedic.ui.widgets import LEVEL_TONE, Callout, Card, Page, amp, badge, button, clear_layout, label
from printmedic.vendors.profiles import profile_by_id

QUALITY_SYMPTOMS = ("missing_color", "streaks", "faded", "misaligned", "blank_pages", "paper_jam")


class QualityPage(Page):
    title_key = "nav.quality"
    subtitle_key = "quality.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        pick = Card(tr("quality.pick"))
        row = QHBoxLayout()
        row.addWidget(label(tr("repair.printer")))
        self.device = QComboBox()
        self.device.setAccessibleName(tr("repair.printer"))
        self.device.currentIndexChanged.connect(self._render)
        row.addWidget(self.device, 1)
        pick.add(row)
        grid = QGridLayout()
        self.group = QButtonGroup(self)
        for i, s in enumerate(QUALITY_SYMPTOMS):
            rb = QRadioButton(amp(tr(f"sym.{s}")))
            rb.setProperty("symptom", s)
            self.group.addButton(rb, i)
            grid.addWidget(rb, i // 3, i % 3)
        self.group.button(0).setChecked(True)
        self.group.idToggled.connect(lambda _i, on: on and self._render())
        pick.add(grid)
        self.content.addWidget(pick)
        self.out = QVBoxLayout()
        self.out.setSpacing(14)
        self.content.addLayout(self.out)
        self.content.addStretch(1)

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        self.on_inventory()

    def on_inventory(self) -> None:
        cur = self.device.currentData()
        self.device.blockSignals(True)
        self.device.clear()
        for d in physical(self.ctx.core.devices):
            self.device.addItem(d.display_name, d.device_id)
        if self.device.count() == 0:
            self.device.addItem(tr("quality.no_printer"), None)
        self.device.setCurrentIndex(max(self.device.findData(cur), 0))
        self.device.blockSignals(False)
        self._render()

    def _symptom(self) -> str:
        b = self.group.checkedButton()
        return str(b.property("symptom")) if b else QUALITY_SYMPTOMS[0]

    def _render(self) -> None:
        clear_layout(self.out)
        sym = self._symptom()
        d = self.ctx.core.device(self.device.currentData()) if self.device.currentData() else None

        cls = Card(tr("quality.classification"))
        cls.add(label(tr(f"quality.{sym}.software"), wrap=True))
        cls.add(label(tr(f"quality.{sym}.physical"), wrap=True))
        self.out.addWidget(cls)

        steps = Card(tr("quality.steps"))
        for i in range(1, 6):
            key = f"quality.{sym}.step{i}"
            if key not in EN:
                break
            steps.add(label(f"{i}.  {tr(key)}", wrap=True))
        self.out.addWidget(steps)

        if d is not None:
            m = Card(tr("quality.maintenance", name=d.display_name))
            for cap in (Capability.PRINTHEAD_CLEANING, Capability.ALIGNMENT, Capability.TEST_PAGE):
                info = d.capability(cap)
                if not info:
                    continue
                r = QHBoxLayout()
                r.addWidget(label(tr(f"cap.{cap.value}")), 1)
                r.addWidget(badge(tr(f"level.{info.level.value}"), LEVEL_TONE[info.level]))
                m.add(r)
            vendor = profile_by_id(d.vendor_id)
            text = vendor.maintenance.get(language()) or vendor.maintenance.get("en") or ""
            if text:
                m.add(Callout(text, "info", tr("ink.vendor_maintenance", vendor=vendor.name)))
            m.add(label(tr("quality.why_guided"), "small", wrap=True))
            row = QHBoxLayout()
            if d.queue and not d.queue.is_virtual:
                q = d.queue.name
                row.addWidget(button(tr("printers.test_page"), lambda: self.ctx.run_action(PrintTestPage(queue=q))))
            did = d.device_id
            row.addWidget(button(tr("quality.diagnose_with"), lambda: self.ctx.bus.navigate.emit(
                "diagnostics", {"target": did, "symptoms": [sym], "autorun": True}), "primary"))
            row.addStretch(1)
            m.add(row)
            self.out.addWidget(m)
