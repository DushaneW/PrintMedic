"""Reusable UI building blocks."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from printmedic.core.models import CapabilityLevel, Certainty, QueueState, Severity, StatusOrigin
from printmedic.i18n import tr


def label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def amp(text: str) -> str:
    """Escape '&' so Qt does not turn it into a keyboard mnemonic on buttons, checkboxes and tabs."""
    return text.replace("&", "&&")


def button(text: str, on_click: Callable[[], Any] | None = None, kind: str | None = None,
           tooltip: str = "") -> QPushButton:
    b = QPushButton(amp(text))
    if kind:
        b.setProperty("kind", kind)
    if on_click:
        b.clicked.connect(lambda _=False: on_click())
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def badge(text: str, tone: str = "neutral", origin: str | None = None, tooltip: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("tone", tone)
    if origin:
        lbl.setProperty("origin", origin)
    lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("hline")
    return f


class Card(QFrame):
    def __init__(self, title: str | None = None, accent: str | None = None, spacing: int = 10,
                 margins: tuple[int, int, int, int] = (18, 16, 18, 16)) -> None:
        super().__init__()
        self.setObjectName("card")
        if accent:
            self.setProperty("accent", accent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)
        if title:
            self.body.addWidget(label(title, "h2"))

    def add(self, w: QWidget | QLayout, stretch: int = 0) -> None:
        if isinstance(w, QLayout):
            self.body.addLayout(w, stretch)
        else:
            self.body.addWidget(w, stretch)


class Callout(QFrame):
    def __init__(self, text: str, tone: str = "info", title: str | None = None) -> None:
        super().__init__()
        self.setObjectName("callout")
        self.setProperty("tone", tone)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        if title:
            lay.addWidget(label(title, "h3", wrap=True))
        self.text = label(text, wrap=True)
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.text)


class EmptyState(QWidget):
    def __init__(self, title: str, text: str, actions: Iterable[QPushButton] = ()) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 40, 24, 40)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t = label(title, "h2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d = label(text, "muted", wrap=True)
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.setMaximumWidth(560)
        lay.addWidget(t, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(d, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for b in actions:
            row.addWidget(b)
        lay.addSpacing(8)
        lay.addLayout(row)


def kv_grid(rows: Iterable[tuple[str, str]]) -> QGridLayout:
    g = QGridLayout()
    g.setHorizontalSpacing(18)
    g.setVerticalSpacing(6)
    for i, (k, v) in enumerate(rows):
        g.addWidget(label(k, "muted"), i, 0, Qt.AlignmentFlag.AlignTop)
        val = label(v or "—", wrap=True, selectable=True)
        g.addWidget(val, i, 1)
    g.setColumnStretch(1, 1)
    return g


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def scroll_page(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    return sa


class Page(QWidget):
    """Base class: a scrollable page with a header. Subclasses fill `self.content`."""

    title_key = ""
    subtitle_key = ""

    def __init__(self, ctx: Any) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        inner.setObjectName("page")
        self.content = QVBoxLayout(inner)
        self.content.setContentsMargins(28, 22, 28, 28)
        self.content.setSpacing(16)
        head = QVBoxLayout()
        head.setSpacing(2)
        self.header_row = QHBoxLayout()
        self.header_row.addWidget(label(tr(self.title_key), "h1"))
        self.header_row.addStretch(1)
        head.addLayout(self.header_row)
        if self.subtitle_key:
            head.addWidget(label(tr(self.subtitle_key), "muted", wrap=True))
        self.content.addLayout(head)
        outer.addWidget(scroll_page(inner))

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        """Called whenever the page becomes visible (payload from navigation)."""

    def on_inventory(self) -> None:
        """Called after the device inventory changed."""


# --- semantic mappings -----------------------------------------------------------------------
STATE_TONE = {
    QueueState.READY: "success", QueueState.PRINTING: "info", QueueState.PAUSED: "warning",
    QueueState.OFFLINE: "danger", QueueState.ERROR: "danger", QueueState.USER_INTERVENTION: "danger",
    QueueState.POWER_SAVE: "neutral", QueueState.NOT_AVAILABLE: "danger", QueueState.UNKNOWN: "neutral",
}
SEVERITY_TONE = {Severity.INFO: "neutral", Severity.LOW: "info", Severity.MEDIUM: "warning",
                 Severity.HIGH: "danger", Severity.CRITICAL: "danger"}
CERTAINTY_TONE = {Certainty.CONFIRMED: "danger", Certainty.LIKELY: "warning", Certainty.POSSIBLE: "neutral"}
LEVEL_TONE = {CapabilityLevel.AUTOMATED: "success", CapabilityLevel.GUIDED: "info",
              CapabilityLevel.UNSUPPORTED: "neutral", CapabilityLevel.UNKNOWN: "warning"}
REPAIR_TONE = {"verified": "success", "partial": "warning", "failed": "danger", "timeout": "danger",
               "refused": "neutral", "blocked": "warning", "needs_user": "info", "done": "success"}


def state_badge(state: QueueState, origin: StatusOrigin = StatusOrigin.REPORTED_OS) -> QLabel:
    text = tr(f"state.{state.value}")
    return badge(text, STATE_TONE.get(state, "neutral"), origin.value,
                 tooltip=tr(f"origin.{origin.value}.long"))


def origin_label(origin: StatusOrigin) -> QLabel:
    prefix = "✓ " if origin == StatusOrigin.VERIFIED else ""
    return label(prefix + tr(f"origin.{origin.value}"), "small")
