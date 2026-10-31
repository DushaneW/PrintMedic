"""Ink & Toner: levels only from trustworthy device reports (IPP); unknowns are explained, never invented."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QProgressBar, QVBoxLayout

from printmedic.core.models import Capability, CapabilityLevel, Device
from printmedic.i18n import language, tr
from printmedic.ui import tasks
from printmedic.ui.pages.common import physical
from printmedic.ui.widgets import Callout, Card, EmptyState, Page, badge, button, clear_layout, label
from printmedic.vendors.profiles import profile_by_id

STATE_TONE = {"ok": "success", "low": "warning", "empty": "danger", "unknown": "neutral", "some_remaining": "info"}


class ConsumablesPage(Page):
    title_key = "nav.consumables"
    subtitle_key = "ink.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.refresh_btn = button(tr("ink.refresh"), self._refresh, "primary")
        self.header_row.addWidget(self.refresh_btn)
        self.content.addWidget(Callout(tr("ink.policy"), "info"))
        self.box = QVBoxLayout()
        self.box.setSpacing(14)
        self.content.addLayout(self.box)
        guide = Card(tr("ink.install_title"))
        for i in range(1, 7):
            guide.add(label(f"{i}.  " + tr(f"ink.install{i}"), wrap=True))
        self.content.addWidget(guide)
        self.content.addStretch(1)

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        self.on_inventory()

    def on_inventory(self) -> None:
        clear_layout(self.box)
        devs = physical(self.ctx.core.devices)
        if not devs:
            self.box.addWidget(EmptyState(tr("ink.empty_title"), tr("ink.empty_text")))
            return
        for d in devs:
            self.box.addWidget(self._card(d))

    def _card(self, d: Device) -> Card:
        c = Card()
        top = QHBoxLayout()
        top.addWidget(label(d.display_name, "h2", wrap=True), 1)
        c.add(top)
        cap = d.capability(Capability.CONSUMABLES)
        cons = d.consumables
        if cons:
            read_at = cons[0].read_at.astimezone().strftime("%Y-%m-%d %H:%M") if cons[0].read_at else "?"
            c.add(label(tr("ink.source", source=cons[0].source, when=read_at), "small"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            for r, s in enumerate(cons):
                grid.addWidget(label(s.name), r, 0)
                if s.level_percent is not None:
                    bar = QProgressBar()
                    bar.setRange(0, 100)
                    bar.setValue(s.level_percent)
                    bar.setAccessibleName(f"{s.name} {s.level_percent}%")
                    if s.color and s.color.startswith("#") and len(s.color) == 7:
                        bar.setStyleSheet(f"QProgressBar::chunk {{ background: {s.color}; border-radius: 5px; }}")
                    grid.addWidget(bar, r, 1)
                    grid.addWidget(label(f"{s.level_percent}%"), r, 2)
                else:
                    grid.addWidget(label(tr("ink.level_not_reported"), "muted"), r, 1)
                    grid.addWidget(label("—"), r, 2)
                grid.addWidget(badge(tr(f"ink.state.{s.state}"), STATE_TONE.get(s.state, "neutral"),
                                     "reported_device", tr("origin.reported_device.long")), r, 3)
            grid.setColumnStretch(1, 1)
            c.add(grid)
            c.add(label(tr("ink.estimate_note"), "small", wrap=True))
        else:
            reason = tr(cap.note) if cap and cap.note else ""
            key = "ink.unknown_not_probed" if cap and cap.level == CapabilityLevel.UNKNOWN else "ink.unavailable"
            c.add(Callout(tr(key) + (f"\n{reason}" if reason else ""), "neutral" if key == "ink.unavailable"
                          else "warning"))
        vendor = profile_by_id(d.vendor_id)
        maint = vendor.maintenance.get(language()) or vendor.maintenance.get("en")
        if maint:
            c.add(label(tr("ink.vendor_maintenance", vendor=vendor.name), "h3"))
            c.add(label(maint, "muted", wrap=True))
        return c

    def _refresh(self) -> None:
        hosts = [(d.host, d.queue.device_port if d.queue else None) for d in physical(self.ctx.core.devices) if d.host]
        if not hosts:
            self.ctx.refresh_inventory()
            return
        self.refresh_btn.setEnabled(False)
        self.ctx.bus.busy.emit(True)

        def work(cancel: Any, progress: Any) -> int:
            for h, p in hosts:
                cancel.raise_if_cancelled()
                progress(h, None)
                self.ctx.core.probe(h, (p,) if p else (), cancel)
            return len(hosts)

        def done(_n: int) -> None:
            self.refresh_btn.setEnabled(True)
            self.ctx.bus.busy.emit(False)
            self.ctx.bus.inventory_changed.emit()

        def failed(m: str, _d: str) -> None:
            self.refresh_btn.setEnabled(True)
            self.ctx.bus.busy.emit(False)
            self.ctx.bus.status.emit(m)

        tasks.start(work, done, failed, lambda h, _f: self.ctx.bus.status.emit(tr("status.probing", host=h)))
