"""Shared UI context: the application core, a signal bus and common flows (consent → run → verify)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from printmedic.app_core import AppCore
from printmedic.i18n import tr
from printmedic.repair.actions import Action, Kind
from printmedic.repair.executor import RepairRecord
from printmedic.ui import consent, tasks
from printmedic.ui.theme import Tokens
from printmedic.ui.widgets import REPAIR_TONE, Callout, badge, button, label


class Bus(QObject):
    inventory_changed = Signal()
    session_changed = Signal()
    history_changed = Signal()
    navigate = Signal(str, object)
    status = Signal(str)
    busy = Signal(bool)


class UiContext:
    def __init__(self, core: AppCore, tokens: Tokens) -> None:
        self.core = core
        self.bus = Bus()
        self.tokens = tokens
        self.window: QWidget | None = None
        self.refreshing = False
        self.inventory_error: str | None = None
        self.notify: Callable[[str, str], None] = lambda _t, _m: None

    # ------------------------------------------------------------------------------------------
    def refresh_inventory(self, on_done: Callable[[], None] | None = None) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.bus.busy.emit(True)
        self.bus.status.emit(tr("status.refreshing"))

        def done(_r: Any) -> None:
            self.refreshing = False
            self.inventory_error = None
            self.bus.busy.emit(False)
            self.bus.status.emit(tr("status.refreshed", n=len(self.core.devices)))
            self.bus.inventory_changed.emit()
            if on_done:
                on_done()

        def failed(m: str, d: str) -> None:
            self.refreshing = False
            self.inventory_error = m
            self.bus.busy.emit(False)
            self.bus.status.emit(m)
            self.bus.inventory_changed.emit()

        tasks.start(lambda c, p: self.core.refresh(), done, failed)

    def ensure_snapshot(self, then: Callable[[], None]) -> None:
        if self.core.snapshot is not None:
            then()
        else:
            self.refresh_inventory(then)

    def run_action(self, action: Action, finding_codes: list[str] | None = None,
                   on_done: Callable[[RepairRecord], None] | None = None) -> None:
        """Preview → explicit consent → background execution → verification result."""
        if action.kind == Kind.NAVIGATE:
            self.bus.navigate.emit("network", {"assistant": True})
            return

        def proceed() -> None:
            snap = self.core.snapshot
            if snap is None:
                QMessageBox.warning(self.window, tr("app.name"), self.inventory_error or tr("error.no_inventory"))
                return
            c = consent.ask(action, snap, self.window)
            if c is None:
                self.bus.status.emit(tr("status.repair_cancelled"))
                return
            self.bus.busy.emit(True)
            self.bus.status.emit(tr("status.repair_running", action=action_title(action)))

            def done(rec: RepairRecord) -> None:
                self.bus.busy.emit(False)
                self.bus.history_changed.emit()
                self.bus.inventory_changed.emit()
                self.bus.status.emit(tr("status.repair_finished", status=tr(f"repair.status.{rec.status}")))
                self.notify(tr("app.name"), tr("status.repair_finished", status=tr(f"repair.status.{rec.status}")))
                show_result(self, action, rec)
                if on_done:
                    on_done(rec)

            def failed(m: str, d: str) -> None:
                self.bus.busy.emit(False)
                QMessageBox.critical(self.window, tr("app.name"), m)

            tasks.start(lambda cancel, p: self.core.run_action(action, c, finding_codes), done, failed)

        self.ensure_snapshot(proceed)


def action_title(action: Action) -> str:
    key, params = action.title()
    return tr(key, **params)


def show_result(ctx: UiContext, action: Action, rec: RepairRecord) -> None:
    dlg = QDialog(ctx.window)
    dlg.setWindowTitle(tr("result.window_title"))
    dlg.setMinimumWidth(520)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(10)
    lay.addWidget(label(action_title(action), "h2", wrap=True))
    row = QHBoxLayout()
    row.addWidget(badge(tr(f"repair.status.{rec.status}"), REPAIR_TONE.get(rec.status, "neutral")))
    row.addStretch(1)
    lay.addLayout(row)
    lay.addWidget(label(tr(f"repair.explain.{rec.status}"), "muted", wrap=True))
    for m in rec.blocked_by:
        lay.addWidget(label("•  " + tr(m[0], **m[1]), wrap=True))
    for m in rec.verification:
        lay.addWidget(label("✓  " + tr(m[0], **m[1]) if rec.status == "verified" else "•  " + tr(m[0], **m[1]),
                            wrap=True))
    code = rec.exec_result.get("code")
    if code and code not in ("ok",):
        lay.addWidget(Callout(tr(f"exec.{code}") + (f"\n{rec.exec_result.get('message')}"
                                                     if rec.exec_result.get("message") else ""), "danger"))
    buttons = QDialogButtonBox()
    if rec.status == "needs_user":
        lay.addWidget(label(tr("result.did_it_work"), "h3"))

        def answer(a: str) -> None:
            ctx.core.history.update_repair_confirmation(rec.record_id, a)
            ctx.bus.history_changed.emit()
            dlg.accept()

        yes = button(tr("result.yes"), lambda: answer("yes"), "primary")
        no = button(tr("result.no"), lambda: answer("no"))
        buttons.addButton(yes, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(no, QDialogButtonBox.ButtonRole.RejectRole)
    else:
        close = buttons.addButton(tr("common.close"), QDialogButtonBox.ButtonRole.AcceptRole)
        close.clicked.connect(dlg.accept)
    lay.addWidget(buttons)
    dlg.exec()
