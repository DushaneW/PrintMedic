"""Overview: fleet summary from real data, devices needing attention, recent sessions, active repairs."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from printmedic.i18n import tr
from printmedic.ui.pages.common import attention_reason, device_state, needs_attention, physical
from printmedic.ui.widgets import (
    Callout,
    Card,
    EmptyState,
    Page,
    badge,
    button,
    clear_layout,
    label,
    state_badge,
)


class OverviewPage(Page):
    title_key = "nav.overview"
    subtitle_key = "overview.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.header_row.addWidget(button(tr("overview.run_full"), self._run_full, "primary"))
        self.header_row.addWidget(button(tr("overview.discover"), lambda: ctx.bus.navigate.emit("discover", None)))
        self.body = QVBoxLayout()
        self.body.setSpacing(16)
        self.content.addLayout(self.body)
        self.content.addStretch(1)
        ctx.bus.history_changed.connect(self.on_inventory)
        ctx.bus.busy.connect(lambda _b: self.on_inventory())

    def _run_full(self) -> None:
        self.ctx.bus.navigate.emit("diagnostics", {"autorun": True})

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        self.on_inventory()

    def on_inventory(self) -> None:
        clear_layout(self.body)
        core = self.ctx.core
        if self.ctx.inventory_error and core.snapshot is None:
            self.body.addWidget(Callout(self.ctx.inventory_error, "danger", tr("overview.inventory_failed")))
        if core.snapshot is None:
            msg = tr("overview.loading") if self.ctx.refreshing else tr("overview.not_loaded")
            self.body.addWidget(EmptyState(tr("overview.no_data_title"), msg,
                                           [button(tr("common.refresh"), self.ctx.refresh_inventory, "primary")]))
            return
        devices = core.devices
        phys = physical(devices)
        attention = [d for d in phys if needs_attention(d)]
        jobs = sum(len(d.queue.jobs) for d in devices if d.queue)
        spooler = core.snapshot.spooler

        tiles = QGridLayout()
        tiles.setSpacing(12)
        spooler_text = tr("overview.spooler_running") if spooler and spooler.running else (
            tr("overview.spooler_stopped") if spooler else "—")
        specs = [
            (tr("overview.tile.printers"), str(len(phys)), tr("overview.tile.printers_src")),
            (tr("overview.tile.attention"), str(len(attention)), tr("overview.tile.attention_src")),
            (tr("overview.tile.jobs"), str(jobs), tr("overview.tile.jobs_src")),
            (tr("overview.tile.spooler"), spooler_text, tr("overview.tile.spooler_src")),
        ]
        for i, (title, value, src) in enumerate(specs):
            c = Card(margins=(16, 14, 16, 14), spacing=2)
            c.add(label(title, "muted"))
            c.add(label(value, "metric"))
            c.add(label(src, "small", wrap=True))
            tiles.addWidget(c, 0, i)
        self.body.addLayout(tiles)

        if not phys:
            actions = [button(tr("overview.discover"), lambda: self.ctx.bus.navigate.emit("discover", None), "primary")]
            self.body.addWidget(EmptyState(tr("overview.empty_title"), tr("overview.empty_text"), actions))

        att = Card(tr("overview.attention"))
        if not attention:
            att.add(label(tr("overview.no_attention") if phys else tr("overview.no_devices"), "muted"))
        for d in attention:
            row = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(label(d.display_name, "h3"))
            col.addWidget(label(attention_reason(d), "muted"))
            row.addLayout(col, 1)
            row.addWidget(state_badge(device_state(d)))
            did = d.device_id
            row.addWidget(button(tr("common.details"), lambda did=did: self.ctx.bus.navigate.emit(
                "printers", {"device": did})))
            row.addWidget(button(tr("common.diagnose"), lambda did=did: self.ctx.bus.navigate.emit(
                "diagnostics", {"target": did, "autorun": True}), "primary"))
            w = QWidget()
            w.setLayout(row)
            att.add(w)
        self.body.addWidget(att)

        lower = QHBoxLayout()
        lower.setSpacing(16)
        sess = Card(tr("overview.recent_sessions"))
        recent = core.history.sessions(limit=5)
        if not recent:
            sess.add(label(tr("overview.no_sessions"), "muted"))
        for s in recent:
            row = QHBoxLayout()
            summ = s.get("summary", {})
            target = s.get("target_label") or tr("diag.all_printers")
            row.addWidget(label(f"{s['started_at'][:16].replace('T', ' ')} · {target}"), 1)
            row.addWidget(badge(tr("overview.findings_n", n=summ.get("total", 0)),
                                "danger" if summ.get("confirmed") else "success"))
            sess.add(row)
        sess.add(button(tr("overview.open_reports"), lambda: self.ctx.bus.navigate.emit("reports", None), "link"))
        lower.addWidget(sess, 1)

        active = Card(tr("overview.active_repairs"))
        if core.active_repairs:
            for key in core.active_repairs:
                active.add(label("⏳ " + key.split("|", 1)[0].replace("_", " ")))
        else:
            active.add(label(tr("overview.no_active_repairs"), "muted"))
        plan_n = len([p for p in core.plan if p.runnable])
        if plan_n:
            active.add(label(tr("overview.plan_pending", n=plan_n)))
            active.add(button(tr("overview.open_repair"), lambda: self.ctx.bus.navigate.emit("repair", None), "link"))
        lower.addWidget(active, 1)
        self.body.addLayout(lower)
