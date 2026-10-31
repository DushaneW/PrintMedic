"""Automatic Diagnostics: choose scope/checks/symptoms, watch progress, inspect ranked findings."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QProgressBar,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from printmedic.core.models import Certainty
from printmedic.diagnostics.catalog import render
from printmedic.diagnostics.checks import CATEGORIES, SYMPTOMS
from printmedic.diagnostics.engine import DiagnosticSession
from printmedic.diagnostics.findings import Finding
from printmedic.i18n import language, tr
from printmedic.repair.actions import create
from printmedic.ui import tasks
from printmedic.ui.widgets import (
    CERTAINTY_TONE,
    SEVERITY_TONE,
    Callout,
    Card,
    Page,
    amp,
    badge,
    button,
    clear_layout,
    label,
    scroll_page,
)


def finding_params(f: Finding) -> dict[str, str]:
    p = dict(f.params)
    for k in ("condition", "reason"):
        if k in p:
            p[k] = tr(f"cond.{p[k]}")
    return p


class DiagnosticsPage(Page):
    title_key = "nav.diagnostics"
    subtitle_key = "diag.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.task: tasks.Task | None = None
        self.session: DiagnosticSession | None = None

        setup = Card(tr("diag.setup"))
        row = QHBoxLayout()
        row.addWidget(label(tr("diag.target")))
        self.target = QComboBox()
        self.target.setAccessibleName(tr("diag.target"))
        row.addWidget(self.target, 1)
        row.addWidget(button(tr("diag.profile_full"), lambda: self._profile("full")))
        row.addWidget(button(tr("diag.profile_quick"), lambda: self._profile("quick")))
        setup.add(row)
        grid = QGridLayout()
        grid.addWidget(label(tr("diag.checks"), "h3"), 0, 0, 1, 4)
        self.cat_boxes: dict[str, QCheckBox] = {}
        for i, cat in enumerate(CATEGORIES):
            if cat == "symptoms":
                continue
            cb = QCheckBox(amp(tr(f"cat.{cat}")))
            cb.setChecked(True)
            cb.setToolTip(tr(f"cat.{cat}.tip"))
            self.cat_boxes[cat] = cb
            grid.addWidget(cb, 1 + i // 4, i % 4)
        setup.add(grid)
        sgrid = QGridLayout()
        sgrid.addWidget(label(tr("diag.symptoms"), "h3"), 0, 0, 1, 3)
        self.sym_boxes: dict[str, QCheckBox] = {}
        for i, s in enumerate(SYMPTOMS):
            cb = QCheckBox(amp(tr(f"sym.{s}")))
            self.sym_boxes[s] = cb
            sgrid.addWidget(cb, 1 + i // 3, i % 3)
        setup.add(sgrid)
        actions = QHBoxLayout()
        self.run_btn = button(tr("diag.run"), self._run, "primary")
        self.cancel_btn = button(tr("common.cancel"), self._cancel)
        self.cancel_btn.setEnabled(False)
        actions.addWidget(self.run_btn)
        actions.addWidget(self.cancel_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        actions.addWidget(self.progress, 1)
        setup.add(actions)
        self.log = QListWidget()
        self.log.setMaximumHeight(110)
        self.log.setAccessibleName(tr("diag.live_log"))
        setup.add(self.log)
        self.content.addWidget(setup)

        self.summary_box = QVBoxLayout()
        self.content.addLayout(self.summary_box)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("diag.col_finding"), tr("diag.col_severity"), tr("diag.col_target")])
        self.tree.setColumnWidth(0, 330)
        self.tree.setMinimumHeight(420)
        self.tree.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self.tree)
        self.detail_host = QWidget()
        self.detail = QVBoxLayout(self.detail_host)
        self.detail.setContentsMargins(8, 0, 0, 0)
        split.addWidget(scroll_page(self.detail_host))
        split.setSizes([480, 620])
        split.setMinimumHeight(620)
        self.content.addWidget(split, 1)
        self.findings: list[Finding] = []
        self._render_empty()

    # ------------------------------------------------------------------------------------------
    def on_inventory(self) -> None:
        cur = self.target.currentData()
        self.target.blockSignals(True)
        self.target.clear()
        self.target.addItem(tr("diag.all_printers"), None)
        for d in self.ctx.core.devices:
            self.target.addItem(d.display_name, d.device_id)
        idx = self.target.findData(cur)
        self.target.setCurrentIndex(max(idx, 0))
        self.target.blockSignals(False)

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        self.on_inventory()
        payload = payload or {}
        if "target" in payload:
            self.target.setCurrentIndex(max(self.target.findData(payload["target"]), 0))
        if payload.get("symptoms"):
            for s, cb in self.sym_boxes.items():
                cb.setChecked(s in payload["symptoms"])
        if payload.get("autorun") and self.task is None:
            self._run()

    def _profile(self, name: str) -> None:
        for cat, cb in self.cat_boxes.items():
            cb.setChecked(name == "full" or cat not in ("network", "device", "consumables"))

    def _run(self) -> None:
        cats = [c for c, cb in self.cat_boxes.items() if cb.isChecked()]
        syms = [s for s, cb in self.sym_boxes.items() if cb.isChecked()]
        if syms:
            cats.append("symptoms")
        if not cats:
            self.log.addItem(tr("diag.no_checks"))
            return
        target = self.target.currentData()
        self.log.clear()
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.ctx.bus.busy.emit(True)

        def on_progress(msg: str, frac: Any) -> None:
            kind, _, arg = msg.partition(":")
            self.log.addItem(tr(f"progress.{kind}", arg=arg))
            self.log.scrollToBottom()
            if isinstance(frac, float):
                self.progress.setValue(int(frac * 100))

        def done(session: DiagnosticSession) -> None:
            self._finish()
            self.session = session
            self.ctx.bus.inventory_changed.emit()
            self.ctx.bus.history_changed.emit()
            self.ctx.bus.session_changed.emit()
            self._render(session)
            self.ctx.notify(tr("app.name"), tr("diag.finished", n=len(session.findings)))

        def failed(m: str, _d: str) -> None:
            self._finish()
            self.log.addItem(m)

        self.task = tasks.start(lambda c, p: self.ctx.core.diagnose(target=target, categories=cats, symptoms=syms,
                                                                    cancel=c, progress=p),
                                done, failed, on_progress)

    def _finish(self) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.setValue(100)
        self.ctx.bus.busy.emit(False)
        self.task = None

    def _cancel(self) -> None:
        if self.task:
            self.task.cancel.cancel()
            self.cancel_btn.setEnabled(False)

    # ------------------------------------------------------------------------------------------
    def _render_empty(self) -> None:
        clear_layout(self.detail)
        self.detail.addWidget(label(tr("diag.no_results"), "muted", wrap=True))
        self.detail.addStretch(1)

    def _render(self, s: DiagnosticSession) -> None:
        clear_layout(self.summary_box)
        if s.cancelled:
            self.summary_box.addWidget(Callout(tr("diag.cancelled"), "warning"))
            return
        sm = s.summary
        if not s.findings:
            self.summary_box.addWidget(Callout(tr("diag.no_findings_text"), "success", tr("diag.no_findings")))
        else:
            self.summary_box.addWidget(Callout(
                tr("diag.summary_text", total=sm["total"], confirmed=sm["confirmed"], likely=sm["likely"],
                   possible=sm["possible"]), "danger" if sm["confirmed"] else "warning", tr("diag.summary")))
        if s.errors:
            self.summary_box.addWidget(Callout("\n".join(s.errors[:6]), "warning", tr("diag.partial")))
        row = QHBoxLayout()
        row.addStretch(1)
        plan_btn = button(tr("diag.make_plan"), lambda: self.ctx.bus.navigate.emit("repair", None), "primary")
        plan_btn.setEnabled(bool(self.ctx.core.plan))
        row.addWidget(plan_btn)
        self.summary_box.addLayout(row)

        self.tree.clear()
        self.findings = s.findings
        groups = {}
        for cert in Certainty:
            n = sum(1 for f in s.findings if f.certainty == cert)
            g = QTreeWidgetItem(self.tree, [f"{tr('certainty.' + cert.value + '.group')} ({n})"])
            g.setFirstColumnSpanned(True)
            g.setFlags(Qt.ItemFlag.ItemIsEnabled)
            g.setExpanded(True)
            groups[cert] = g
        first = None
        for i, f in enumerate(s.findings):
            text = render(f.code, language(), finding_params(f))
            item = QTreeWidgetItem(groups[f.certainty], [text["title"], tr(f"sev.{f.severity.name.lower()}"),
                                                         f.target or "—"])
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            item.setToolTip(0, text["title"])
            first = first or item
        if first:
            self.tree.setCurrentItem(first)
        else:
            self._render_empty()

    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        idx = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
        if idx is None:
            return
        self._render_finding(self.findings[idx])

    def _render_finding(self, f: Finding) -> None:
        clear_layout(self.detail)
        text = render(f.code, language(), finding_params(f))
        c = Card(accent=SEVERITY_TONE[f.severity])
        c.add(label(text["title"], "h2", wrap=True))
        row = QHBoxLayout()
        row.addWidget(badge(tr(f"sev.{f.severity.name.lower()}"), SEVERITY_TONE[f.severity]))
        row.addWidget(badge(tr(f"certainty.{f.certainty.value}"), CERTAINTY_TONE[f.certainty],
                            tooltip=tr(f"certainty.{f.certainty.value}.long")))
        row.addWidget(label(f.code, "mono"))
        row.addStretch(1)
        c.add(row)
        c.add(label(tr(f"certainty.{f.certainty.value}.long"), "small", wrap=True))
        if f.target:
            c.add(label(tr("diag.target_is", target=f.target), "muted", wrap=True))

        c.add(label(tr("diag.evidence"), "h3"))
        t = QTableWidget(len(f.evidence), 3)
        t.setHorizontalHeaderLabels([tr("diag.ev_fact"), tr("diag.ev_source"), tr("diag.ev_origin")])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setWordWrap(True)
        for r, e in enumerate(f.evidence):
            t.setItem(r, 0, QTableWidgetItem(e.fact))
            t.setItem(r, 1, QTableWidgetItem(e.source))
            t.setItem(r, 2, QTableWidgetItem(tr(f"origin.{e.origin.value}")))
        t.resizeColumnsToContents()
        t.setColumnWidth(0, min(max(t.columnWidth(0), 260), 420))
        t.resizeRowsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        t.setMinimumHeight(min(50 + 32 * len(f.evidence), 220))
        c.add(t)

        for key, title in (("causes", "diag.causes"), ("steps", "diag.steps")):
            items = text.get(key) or []
            if items:
                c.add(label(tr(title), "h3"))
                for i, s in enumerate(items, 1):
                    c.add(label(f"{i}.  {s}" if key == "steps" else f"•  {s}", wrap=True))
        if f.actions:
            c.add(label(tr("diag.repairs"), "h3"))
            for a in f.actions:
                try:
                    act = create(a.action_id, a.param_dict)
                except KeyError:
                    continue
                r = QHBoxLayout()
                k, p = act.title()
                r.addWidget(label(tr(k, **p), wrap=True), 1)
                r.addWidget(badge(tr(f"risk.{act.risk.value}"), "neutral"))
                r.addWidget(button(tr("diag.preview_run"), lambda act=act, code=f.code: self.ctx.run_action(
                    act, [code])))
                c.add(r)
        if text.get("verify"):
            c.add(label(tr("diag.verification"), "h3"))
            c.add(label(text["verify"], "muted", wrap=True))
        if text.get("limits"):
            c.add(label(tr("diag.limitations"), "h3"))
            c.add(label(text["limits"], "muted", wrap=True))
        self.detail.addWidget(c)
        self.detail.addStretch(1)
