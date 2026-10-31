"""Repair Center: repair plan from diagnostics, manual consent-gated actions, and repair history."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from printmedic.i18n import tr
from printmedic.repair.actions import (
    ClearQueue,
    PrintTestPage,
    RemoveJob,
    RestartSpooler,
    ResumeQueue,
    Risk,
    SetOnline,
)
from printmedic.repair.planner import PlanItem
from printmedic.ui.context import action_title
from printmedic.ui.widgets import REPAIR_TONE, Callout, Card, Page, badge, button, clear_layout, label

RISK_TONE = {Risk.NONE: "neutral", Risk.LOW: "info", Risk.MEDIUM: "warning", Risk.HIGH: "danger"}


class RepairPage(Page):
    title_key = "nav.repair"
    subtitle_key = "repair.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.header_row.addWidget(button(tr("repair.recheck_plan"), self._replan))
        self.content.addWidget(Callout(tr("repair.policy"), "info", tr("repair.policy_title")))
        self.plan_card = Card(tr("repair.plan"))
        self.plan_box = QVBoxLayout()
        self.plan_card.add(self.plan_box)
        self.content.addWidget(self.plan_card)

        manual = Card(tr("repair.manual"))
        manual.add(label(tr("repair.manual_text"), "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(label(tr("repair.printer")))
        self.queue = QComboBox()
        self.queue.setAccessibleName(tr("repair.printer"))
        self.queue.currentIndexChanged.connect(self._fill_jobs)
        row.addWidget(self.queue, 1)
        manual.add(row)
        btns = QHBoxLayout()
        btns.addWidget(button(tr("repair.btn_resume"), lambda: self._q(ResumeQueue)))
        btns.addWidget(button(tr("repair.btn_online"), lambda: self._q(SetOnline)))
        btns.addWidget(button(tr("repair.btn_test"), lambda: self._q(PrintTestPage)))
        btns.addWidget(button(tr("repair.btn_clear"), lambda: self._q(ClearQueue), "danger"))
        btns.addStretch(1)
        manual.add(btns)
        jobrow = QHBoxLayout()
        jobrow.addWidget(label(tr("repair.job")))
        self.job = QComboBox()
        self.job.setAccessibleName(tr("repair.job"))
        jobrow.addWidget(self.job, 1)
        jobrow.addWidget(button(tr("repair.btn_remove_job"), self._remove_job, "danger"))
        manual.add(jobrow)
        sp = QHBoxLayout()
        sp.addWidget(label(tr("repair.spooler_text"), "muted", wrap=True), 1)
        sp.addWidget(button(tr("repair.btn_spooler"), lambda: self.ctx.run_action(RestartSpooler())))
        manual.add(sp)
        self.content.addWidget(manual)

        hist = Card(tr("repair.history"))
        self.hist = QTableWidget(0, 5)
        self.hist.setHorizontalHeaderLabels([tr("report.when"), tr("report.action"), tr("caps.level"),
                                             tr("report.status"), tr("report.verification")])
        self.hist.verticalHeader().setVisible(False)
        self.hist.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.hist.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.hist.setMinimumHeight(220)
        self.hist.itemSelectionChanged.connect(self._show_record)
        hist.add(self.hist)
        self.record_view = QPlainTextEdit()
        self.record_view.setReadOnly(True)
        self.record_view.setMaximumHeight(180)
        self.record_view.setPlaceholderText(tr("repair.select_record"))
        hist.add(self.record_view)
        self.content.addWidget(hist)
        self.records: list[dict[str, Any]] = []
        ctx.bus.history_changed.connect(self._fill_history)
        ctx.bus.session_changed.connect(self._render_plan)

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        self.on_inventory()
        self._render_plan()
        self._fill_history()

    def on_inventory(self) -> None:
        cur = self.queue.currentData()
        self.queue.blockSignals(True)
        self.queue.clear()
        snap = self.ctx.core.snapshot
        for q in snap.queues if snap else []:
            self.queue.addItem(q.name, q.name)
        self.queue.setCurrentIndex(max(self.queue.findData(cur), 0))
        self.queue.blockSignals(False)
        self._fill_jobs()
        self._render_plan()

    def _fill_jobs(self) -> None:
        self.job.clear()
        snap = self.ctx.core.snapshot
        q = snap.queue(self.queue.currentData()) if snap and self.queue.currentData() else None
        for j in q.jobs if q else []:
            self.job.addItem(f"#{j.job_id} — {j.document} ({', '.join(j.status) or tr('jobs.waiting')})", j.job_id)
        if self.job.count() == 0:
            self.job.addItem(tr("repair.no_jobs"), None)

    def _q(self, cls: Any) -> None:
        name = self.queue.currentData()
        if not name:
            return
        self.ctx.run_action(cls(queue=name))

    def _remove_job(self) -> None:
        jid = self.job.currentData()
        if jid is None:
            return
        self.ctx.run_action(RemoveJob(queue=self.queue.currentData(), job_id=str(jid)))

    def _replan(self) -> None:
        from printmedic.ui import tasks
        tasks.start(lambda c, p: self.ctx.core.replan(), lambda _r: (self._render_plan(),
                                                                     self.ctx.bus.inventory_changed.emit()))

    def _render_plan(self) -> None:
        clear_layout(self.plan_box)
        core = self.ctx.core
        if core.last_session is None:
            self.plan_box.addWidget(label(tr("repair.no_session"), "muted", wrap=True))
            row = QHBoxLayout()
            row.addWidget(button(tr("overview.run_full"), lambda: self.ctx.bus.navigate.emit(
                "diagnostics", {"autorun": True}), "primary"))
            row.addStretch(1)
            self.plan_box.addLayout(row)
            return
        if not core.plan:
            self.plan_box.addWidget(label(tr("repair.plan_empty"), "muted", wrap=True))
            return
        self.plan_box.addWidget(label(tr("repair.plan_note"), "small", wrap=True))
        for item in core.plan:
            self.plan_box.addWidget(self._plan_row(item))

    def _plan_row(self, item: PlanItem) -> Card:
        a = item.action
        c = Card(accent=RISK_TONE[a.risk], margins=(14, 10, 14, 10), spacing=4)
        top = QHBoxLayout()
        top.addWidget(label(action_title(a), "h3", wrap=True), 1)
        top.addWidget(badge(tr(f"risk.{a.risk.value}"), RISK_TONE[a.risk]))
        top.addWidget(badge(tr(f"kind.{a.kind.value}"), "neutral"))
        if a.data_loss:
            top.addWidget(badge(tr("consent.data_loss"), "danger"))
        btn = button(tr("diag.preview_run"), lambda: self.ctx.run_action(a, item.finding_codes),
                     "primary" if item.runnable else None)
        top.addWidget(btn)
        c.add(top)
        meta = tr("repair.for_findings", codes=", ".join(item.finding_codes))
        if item.alternative_of:
            meta += " · " + tr("repair.alternative")
        c.add(label(meta, "small", wrap=True))
        if item.blocked_by:
            c.add(label(tr("repair.blocked") + " " + "; ".join(tr(k, **p) for k, p in item.blocked_by), "small",
                        wrap=True))
        return c

    def _fill_history(self) -> None:
        self.records = self.ctx.core.history.repairs(limit=300)
        self.hist.setRowCount(len(self.records))
        for r, rec in enumerate(self.records):
            from printmedic.repair.actions import create
            try:
                title = action_title(create(rec["action_id"], rec.get("params", {})))
            except KeyError:
                title = rec["action_id"]
            ver = "; ".join(tr(m[0], **(m[1] or {})) for m in rec.get("verification", []) or [])
            values = [str(rec.get("started_at", ""))[:19].replace("T", " "), title, tr(f"risk.{rec['risk']}"),
                      None, ver or tr(f"exec.{rec.get('exec_result', {}).get('code', 'ok')}")]
            for col, v in enumerate(values):
                if v is not None:
                    self.hist.setItem(r, col, QTableWidgetItem(v))
            self.hist.setCellWidget(r, 3, badge(tr(f"repair.status.{rec['status']}"),
                                                REPAIR_TONE.get(rec["status"], "neutral")))
        self.hist.resizeColumnsToContents()
        self.hist.setColumnWidth(1, min(self.hist.columnWidth(1), 360))
        self.hist.horizontalHeader().setStretchLastSection(True)

    def _show_record(self) -> None:
        rows = {i.row() for i in self.hist.selectedIndexes()}
        if not rows:
            return
        rec = self.records[min(rows)]
        view = {k: rec.get(k) for k in ("action_id", "params", "status", "risk", "consent_at", "started_at",
                                        "finished_at", "exec_result", "before", "after", "user_confirmation",
                                        "finding_codes")}
        self.record_view.setPlainText(json.dumps(view, indent=2, ensure_ascii=False, default=str))
        self.record_view.moveCursor(self.record_view.textCursor().MoveOperation.Start)
