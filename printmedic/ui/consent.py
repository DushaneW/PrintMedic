"""Consent dialog: the only place a repair can be authorized."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QVBoxLayout

from printmedic.core.models import SystemSnapshot
from printmedic.i18n import tr
from printmedic.repair.actions import Action, Kind, Risk
from printmedic.repair.executor import Consent, grant
from printmedic.ui.widgets import Callout, amp, badge, label

RISK_TONE = {Risk.NONE: "neutral", Risk.LOW: "info", Risk.MEDIUM: "warning", Risk.HIGH: "danger"}


def msg(m: tuple[str, dict]) -> str:  # type: ignore[type-arg]
    return tr(m[0], **m[1])


class ConsentDialog(QDialog):
    def __init__(self, action: Action, snapshot: SystemSnapshot, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.action = action
        self.snapshot = snapshot
        self.consent: Consent | None = None
        self.setWindowTitle(tr("consent.window_title"))
        self.setMinimumWidth(560)
        preview = action.preview(snapshot)
        blocked = action.preconditions(snapshot)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        t = action.title()
        lay.addWidget(label(tr(t[0], **t[1]), "h2", wrap=True))
        row = QHBoxLayout()
        row.addWidget(badge(tr(f"risk.{action.risk.value}"), RISK_TONE[action.risk]))
        row.addWidget(badge(tr(f"kind.{action.kind.value}"), "neutral"))
        if action.requires_admin:
            row.addWidget(badge(tr("consent.admin"), "warning"))
        if action.data_loss:
            row.addWidget(badge(tr("consent.data_loss"), "danger"))
        row.addStretch(1)
        lay.addLayout(row)

        if preview.effects:
            lay.addWidget(label(tr("consent.what_happens"), "h3"))
            for e in preview.effects:
                lay.addWidget(label("•  " + msg(e), wrap=True))
        if preview.steps:
            lay.addWidget(label(tr("consent.steps"), "h3"))
            for i, s in enumerate(preview.steps, 1):
                lay.addWidget(label(f"{i}.  " + msg(s), wrap=True))
        for w in preview.warnings:
            lay.addWidget(Callout(msg(w), "warning"))
        if preview.rollback:
            lay.addWidget(label(tr("consent.rollback") + " " + msg(preview.rollback), "muted", wrap=True))
        lay.addWidget(label(tr("consent.verification_note"), "small", wrap=True))
        if blocked:
            lay.addWidget(Callout("\n".join(msg(b) for b in blocked), "danger", tr("consent.blocked")))

        self.ack = QCheckBox(amp(tr("consent.ack_data_loss")))
        if action.data_loss:
            lay.addWidget(self.ack)

        buttons = QDialogButtonBox()
        confirm_text = tr("consent.confirm_guided") if action.kind == Kind.GUIDED else tr("consent.confirm")
        self.ok = buttons.addButton(confirm_text, QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setProperty("kind", "danger" if action.risk == Risk.HIGH else "primary")
        cancel = buttons.addButton(tr("common.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setDefault(True)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.ok.setEnabled(not blocked and not action.data_loss)
        self.ack.toggled.connect(lambda on: self.ok.setEnabled(on and not blocked))

    def _accept(self) -> None:
        self.consent = grant(self.action, self.snapshot, data_loss_ack=self.ack.isChecked(),
                             admin_ack=self.action.requires_admin)
        self.accept()


def ask(action: Action, snapshot: SystemSnapshot, parent=None) -> Consent | None:  # type: ignore[no-untyped-def]
    dlg = ConsentDialog(action, snapshot, parent)
    return dlg.consent if dlg.exec() == QDialog.DialogCode.Accepted else None

