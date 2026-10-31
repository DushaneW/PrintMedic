"""Network Tools: interfaces, bounded host checks, and the guided connection assistant."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from printmedic.core.models import ProbeResult
from printmedic.core.validation import ValidationError, normalize_host
from printmedic.i18n import tr
from printmedic.repair.actions import AddIppPrinter, OpenPrinterSettings
from printmedic.services.assistant import AssistantResult, Step, run_assistant
from printmedic.ui import tasks
from printmedic.ui.pages.discover import probe_card, suggest_name
from printmedic.ui.widgets import Callout, Card, Page, amp, badge, button, clear_layout, label

STEP_TONE = {"ok": "success", "warn": "warning", "fail": "danger", "info": "info", "skipped": "neutral"}
VERDICT_TONE = {"usb_path": "info", "pc_offline": "danger", "dns": "danger", "routing": "warning",
                "unreachable": "danger", "protocol": "warning", "os_config": "info", "not_installed": "success",
                "found_not_installed": "success", "discovery": "warning", "uncertain": "neutral"}


class NetworkPage(Page):
    title_key = "nav.network"
    subtitle_key = "net.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.task: tasks.Task | None = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._assistant_tab(), amp(tr("net.tab_assistant")))
        self.tabs.addTab(self._tester_tab(), amp(tr("net.tab_tester")))
        self.tabs.addTab(self._interfaces_tab(), amp(tr("net.tab_interfaces")))
        self.content.addWidget(self.tabs, 1)

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        if payload and payload.get("assistant"):
            self.tabs.setCurrentIndex(0)
        self.on_inventory()

    def on_inventory(self) -> None:
        snap = self.ctx.core.snapshot
        ifs = snap.interfaces if snap else []
        self.if_table.setRowCount(len(ifs))
        for r, i in enumerate(ifs):
            for c, v in enumerate([i.name, f"{i.address}/{i.prefix_length}", tr(f"iface.{i.kind}"),
                                   tr("common.yes") if i.up else tr("common.no")]):
                self.if_table.setItem(r, c, QTableWidgetItem(v))
        self.if_table.resizeColumnsToContents()
        cur = self.a_iface.currentData()
        self.a_iface.clear()
        self.a_iface.addItem(tr("discover.all_interfaces"), None)
        for i in ifs:
            self.a_iface.addItem(f"{i.name} — {i.address}", (i.address, i.prefix_length))
        self.a_iface.setCurrentIndex(max(self.a_iface.findData(cur), 0))
        self.queue_hosts.clear()
        self.queue_hosts.addItem(tr("net.pick_queue"), None)
        for q in snap.queues if snap else []:
            if q.device_host:
                self.queue_hosts.addItem(f"{q.name} — {q.device_host}", q.device_host)

    # --- interfaces ---------------------------------------------------------------------------
    def _interfaces_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        c = Card(tr("net.interfaces"))
        self.if_table = QTableWidget(0, 4)
        self.if_table.setHorizontalHeaderLabels([tr("net.col_name"), tr("net.col_address"), tr("net.col_kind"),
                                                 tr("net.col_up")])
        self.if_table.verticalHeader().setVisible(False)
        self.if_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.if_table.horizontalHeader().setStretchLastSection(True)
        c.add(self.if_table)
        c.add(label(tr("net.interfaces_note"), "small", wrap=True))
        lay.addWidget(c)
        lay.addStretch(1)
        return w

    # --- tester -------------------------------------------------------------------------------
    def _tester_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(14)
        c = Card(tr("net.tester"))
        c.add(label(tr("net.tester_text"), "muted", wrap=True))
        row = QHBoxLayout()
        self.t_host = QLineEdit()
        self.t_host.setPlaceholderText(tr("discover.host_placeholder"))
        self.t_host.setAccessibleName(tr("discover.host_placeholder"))
        self.t_host.returnPressed.connect(self._test)
        row.addWidget(self.t_host, 2)
        self.queue_hosts = QComboBox()
        self.queue_hosts.setAccessibleName(tr("net.pick_queue"))
        self.queue_hosts.currentIndexChanged.connect(
            lambda _i: self.queue_hosts.currentData() and self.t_host.setText(self.queue_hosts.currentData()))
        row.addWidget(self.queue_hosts, 2)
        self.t_btn = button(tr("net.run_checks"), self._test, "primary")
        row.addWidget(self.t_btn)
        c.add(row)
        lay.addWidget(c)
        self.t_out = QVBoxLayout()
        lay.addLayout(self.t_out)
        lay.addStretch(1)
        return w

    def _test(self) -> None:
        clear_layout(self.t_out)
        try:
            host = normalize_host(self.t_host.text())
        except ValidationError as exc:
            self.t_out.addWidget(Callout(tr(f"validation.{exc}"), "danger"))
            return
        self.t_btn.setEnabled(False)
        self.t_out.addWidget(label(tr("discover.testing", host=host), "muted"))

        def done(res: ProbeResult) -> None:
            self.t_btn.setEnabled(True)
            clear_layout(self.t_out)
            self.t_out.addWidget(probe_card(res))
            self.ctx.bus.inventory_changed.emit()

        def failed(m: str, _d: str) -> None:
            self.t_btn.setEnabled(True)
            clear_layout(self.t_out)
            self.t_out.addWidget(Callout(m, "danger"))

        tasks.start(lambda cancel, p: self.ctx.core.probe(host, (), cancel), done, failed)

    # --- assistant ----------------------------------------------------------------------------
    def _assistant_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(14)
        c = Card(tr("assist.title"))
        c.add(label(tr("assist.intro"), "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(label(tr("assist.connection")))
        self.a_conn = QComboBox()
        self.a_conn.setAccessibleName(tr("assist.connection"))
        for k in ("wifi", "ethernet", "usb", "unknown"):
            self.a_conn.addItem(tr(f"assist.conn.{k}"), k)
        row.addWidget(self.a_conn, 1)
        row.addWidget(label(tr("discover.interface")))
        self.a_iface = QComboBox()
        self.a_iface.setAccessibleName(tr("discover.interface"))
        row.addWidget(self.a_iface, 1)
        c.add(row)
        row2 = QHBoxLayout()
        self.a_host = QLineEdit()
        self.a_host.setPlaceholderText(tr("assist.host_optional"))
        self.a_host.setAccessibleName(tr("assist.host_optional"))
        row2.addWidget(self.a_host, 1)
        self.a_run = button(tr("assist.run"), self._assist, "primary")
        self.a_cancel = button(tr("common.cancel"), self._cancel_assist)
        self.a_cancel.setEnabled(False)
        row2.addWidget(self.a_run)
        row2.addWidget(self.a_cancel)
        c.add(row2)
        c.add(label(tr("assist.privacy"), "small", wrap=True))
        lay.addWidget(c)
        self.a_steps = Card(tr("assist.steps"))
        self.a_steps_box = QVBoxLayout()
        self.a_steps.add(self.a_steps_box)
        lay.addWidget(self.a_steps)
        self.a_out = QVBoxLayout()
        lay.addLayout(self.a_out)
        lay.addStretch(1)
        self.a_steps_box.addWidget(label(tr("assist.not_started"), "muted"))
        return w

    def _add_step(self, s: Step) -> None:
        row = QHBoxLayout()
        row.addWidget(badge(tr(f"assist.status.{s.status}"), STEP_TONE.get(s.status, "neutral")))
        row.addWidget(label(tr(s.key, **s.params), wrap=True), 1)
        holder = QWidget()
        holder.setLayout(row)
        self.a_steps_box.addWidget(holder)

    def _assist(self) -> None:
        clear_layout(self.a_steps_box)
        clear_layout(self.a_out)
        conn = self.a_conn.currentData()
        host = self.a_host.text().strip() or None
        iface = self.a_iface.currentData()
        core = self.ctx.core
        self.a_run.setEnabled(False)
        self.a_cancel.setEnabled(True)
        self.ctx.bus.busy.emit(True)

        def work(cancel: Any, progress: Any) -> AssistantResult:
            return run_assistant(connection=conn, host=host, snapshot_fn=core.adapter.snapshot, net=core.net,
                                 discovery=core.discovery, cancel=cancel, interface=iface,
                                 duration=min(core.settings.discovery_duration, 8.0),
                                 on_step=lambda s: progress("step", s))

        def on_progress(_m: str, s: Any) -> None:
            if isinstance(s, Step):
                self._add_step(s)

        def done(res: AssistantResult) -> None:
            self._assist_finished()
            self._render_verdict(res)

        def failed(m: str, _d: str) -> None:
            self._assist_finished()
            self.a_out.addWidget(Callout(m, "warning"))

        self.task = tasks.start(work, done, failed, on_progress)

    def _assist_finished(self) -> None:
        self.a_run.setEnabled(True)
        self.a_cancel.setEnabled(False)
        self.ctx.bus.busy.emit(False)
        self.task = None

    def _cancel_assist(self) -> None:
        if self.task:
            self.task.cancel.cancel()

    def _render_verdict(self, res: AssistantResult) -> None:
        tone = VERDICT_TONE.get(res.verdict, "neutral")
        self.a_out.addWidget(Callout(tr(f"assist.verdict.{res.verdict}.text"), tone,
                                     tr(f"assist.verdict.{res.verdict}")))
        if res.probe:
            self.a_out.addWidget(probe_card(res.probe))
        offers = [c for c in res.candidates if c.ipp_url and not c.installed_queue]
        if offers:
            card = Card(tr("assist.offer"))
            card.add(label(tr("assist.offer_text"), "muted", wrap=True))
            for cnd in offers:
                row = QHBoxLayout()
                row.addWidget(label(f"{cnd.model or cnd.host} — {cnd.host}"), 1)
                row.addWidget(button(tr("discover.add_ipp"), lambda c=cnd: self.ctx.run_action(
                    AddIppPrinter(host=c.host, url=c.ipp_url or "", name=suggest_name(c.model, c.host),
                                  port=(c.ipp_url or "").split(":")[-1].split("/")[0] or "631")), "primary"))
                card.add(row)
            self.a_out.addWidget(card)
        if res.verdict in ("discovery", "unreachable", "routing", "protocol", "uncertain", "os_config", "usb_path"):
            row = QHBoxLayout()
            row.addWidget(button(tr("discover.open_settings"),
                                 lambda: self.ctx.run_action(OpenPrinterSettings())))
            if res.verdict == "usb_path":
                row.addWidget(button(tr("discover.tab_usb"), lambda: self.ctx.bus.navigate.emit("discover", None)))
            if res.verdict == "os_config":
                row.addWidget(button(tr("overview.run_full"), lambda: self.ctx.bus.navigate.emit(
                    "diagnostics", {"autorun": True}), "primary"))
            row.addStretch(1)
            self.a_out.addLayout(row)
