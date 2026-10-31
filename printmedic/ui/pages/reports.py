"""Reports & History: past sessions, repair audit trail, redacted export, and clearing history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
)

from printmedic.i18n import language, tr
from printmedic.services import reports
from printmedic.ui.widgets import Callout, Card, Page, amp, button, label


class ReportsPage(Page):
    title_key = "nav.reports"
    subtitle_key = "reports.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("reports.search"))
        self.search.setAccessibleName(tr("reports.search"))
        self.search.textChanged.connect(self._fill)
        bar.addWidget(self.search, 2)
        self.kind = QComboBox()
        self.kind.setAccessibleName(tr("reports.filter"))
        self.kind.addItem(tr("reports.filter_all"), "all")
        self.kind.addItem(tr("reports.filter_findings"), "findings")
        self.kind.currentIndexChanged.connect(self._fill)
        bar.addWidget(self.kind, 1)
        self.content.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("report.when"), tr("report.target"), tr("report.findings")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(360)
        self.table.itemSelectionChanged.connect(self._show)
        split.addWidget(self.table)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        split.addWidget(self.view)
        split.setSizes([480, 620])
        self.content.addWidget(split, 1)

        ex = Card(tr("reports.export"))
        self.redact = QCheckBox(amp(tr("reports.redact")))
        self.redact.setChecked(ctx.core.settings.redact_exports)
        self.redact.toggled.connect(self._redact_toggled)
        ex.add(self.redact)
        ex.add(label(tr("reports.redact_note"), "small", wrap=True))
        row = QHBoxLayout()
        row.addWidget(button(tr("reports.export_selected"), lambda: self._export(selected=True), "primary"))
        row.addWidget(button(tr("reports.export_all"), lambda: self._export(selected=False)))
        row.addStretch(1)
        row.addWidget(button(tr("reports.clear"), self._clear, "danger"))
        ex.add(row)
        self.content.addWidget(ex)
        self.content.addWidget(Callout(tr("reports.local_only"), "info"))
        self.sessions: list[dict[str, Any]] = []
        ctx.bus.history_changed.connect(self._fill)

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        self._fill()

    def _fill(self) -> None:
        hist = self.ctx.core.history
        rows = hist.sessions(self.search.text().strip())
        k = self.kind.currentData()
        if k == "findings":
            rows = [s for s in rows if s.get("summary", {}).get("total")]
        self.sessions = rows
        self.table.setRowCount(len(rows))
        for r, s in enumerate(rows):
            summ = s.get("summary", {})
            vals = [s["started_at"][:19].replace("T", " "), s.get("target_label") or tr("diag.all_printers"),
                    tr("reports.findings_cell", total=summ.get("total", 0), confirmed=summ.get("confirmed", 0))]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        if not rows:
            self.view.setPlainText(tr("reports.empty"))

    def _selected(self) -> dict[str, Any] | None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self.sessions[min(rows)] if rows else None

    def _report(self, sessions: list[dict[str, Any]], redact: bool) -> dict[str, Any]:
        hist = self.ctx.core.history
        repairs = []
        for s in sessions:
            repairs += hist.repairs(session_id=s["session_id"])
        if len(sessions) != 1:
            repairs = hist.repairs()
        return reports.build_report(sessions, repairs, lang=language(), tr=tr, redact=redact)

    def _show(self) -> None:
        s = self._selected()
        if not s:
            return
        rep = self._report([s], redact=False)
        self.view.setHtml(reports.to_html(rep, tr))

    def _redact_toggled(self, on: bool) -> None:
        if not on:
            ans = QMessageBox.warning(self, tr("app.name"), tr("reports.redact_off_warning"),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                self.redact.blockSignals(True)
                self.redact.setChecked(True)
                self.redact.blockSignals(False)

    def _export(self, selected: bool) -> None:
        if selected:
            s = self._selected()
            if not s:
                QMessageBox.information(self, tr("app.name"), tr("reports.select_first"))
                return
            sessions = [s]
        else:
            sessions = self.ctx.core.history.sessions()
        path, _f = QFileDialog.getSaveFileName(self, tr("reports.export"), str(Path.home() / "printmedic-report.html"),
                                               "HTML (*.html);;Markdown (*.md);;JSON (*.json)")
        if not path:
            return
        rep = self._report(sessions, redact=self.redact.isChecked())
        try:
            out = reports.export(rep, Path(path), tr)
        except OSError as exc:
            QMessageBox.critical(self, tr("app.name"), tr("reports.export_failed", error=str(exc)))
            return
        self.ctx.bus.status.emit(tr("reports.exported", path=str(out)))

    def _clear(self) -> None:
        ans = QMessageBox.warning(self, tr("app.name"), tr("reports.clear_confirm"),
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            self.ctx.core.history.clear()
            self.ctx.bus.history_changed.emit()
            self.view.clear()
