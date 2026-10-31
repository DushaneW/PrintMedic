"""Light/dark design tokens and the application stylesheet."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette


@dataclass(frozen=True)
class Tokens:
    name: str
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    on_accent: str
    danger: str
    warning: str
    success: str
    info: str
    neutral: str
    sidebar: str
    selection: str


LIGHT = Tokens("light", bg="#F5F6F8", surface="#FFFFFF", surface2="#F0F2F5", border="#DDE1E7", text="#1A1E24",
               muted="#5A6372", accent="#2459D6", accent_hover="#1C48AF", on_accent="#FFFFFF", danger="#C0352B",
               warning="#A96500", success="#2D7A36", info="#1E63B5", neutral="#6B7280", sidebar="#FFFFFF",
               selection="#E3EBFC")
DARK = Tokens("dark", bg="#13161B", surface="#1B1F26", surface2="#232831", border="#313844", text="#E7EAEF",
              muted="#9BA4B1", accent="#6A98F0", accent_hover="#86ACF4", on_accent="#0D1117", danger="#F0675C",
              warning="#F2B451", success="#6CC27A", info="#6AAEF0", neutral="#9CA3AF", sidebar="#171A20",
              selection="#25324A")

ASSETS = Path(__file__).resolve().parent / "assets"
TONES = ("danger", "warning", "success", "info", "neutral", "accent")


def system_prefers_dark() -> bool:
    hints = QGuiApplication.styleHints()
    try:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def resolve(theme: str) -> Tokens:
    if theme == "dark":
        return DARK
    if theme == "light":
        return LIGHT
    return DARK if system_prefers_dark() else LIGHT


def rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{int(alpha * 255)})"


def tone_color(t: Tokens, tone: str) -> str:
    return getattr(t, tone, t.neutral)


def palette(t: Tokens) -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(t.bg))
    p.setColor(QPalette.ColorRole.WindowText, QColor(t.text))
    p.setColor(QPalette.ColorRole.Base, QColor(t.surface))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(t.surface2))
    p.setColor(QPalette.ColorRole.Text, QColor(t.text))
    p.setColor(QPalette.ColorRole.Button, QColor(t.surface))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(t.text))
    p.setColor(QPalette.ColorRole.Highlight, QColor(t.accent))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(t.on_accent))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(t.surface))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(t.text))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(t.muted))
    p.setColor(QPalette.ColorRole.Link, QColor(t.accent))
    return p


def stylesheet(t: Tokens) -> str:
    badges = "\n".join(
        f'QLabel[tone="{tone}"] {{ color: {tone_color(t, tone)}; background: {rgba(tone_color(t, tone), 0.13)};'
        f' border: 1px solid {rgba(tone_color(t, tone), 0.35)}; border-radius: 9px; padding: 1px 8px;'
        f' font-size: 12px; font-weight: 600; }}\n'
        f'QLabel[tone="{tone}"][origin="inferred"] {{ border-style: dashed; background: transparent; }}\n'
        f'QLabel[tone="{tone}"][origin="user"] {{ background: transparent; }}'
        for tone in TONES)
    check = (ASSETS / "check.svg").as_posix()
    dot = (ASSETS / "dot.svg").as_posix()
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif; font-size: 14px; }}
QMainWindow, QWidget#page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t.bg}; }}
QWidget {{ color: {t.text}; }}
QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 6px; }}

QFrame#sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
QLabel#brand {{ font-size: 18px; font-weight: 700; padding: 18px 18px 4px 18px; }}
QLabel#brandSub {{ color: {t.muted}; font-size: 12px; padding: 0 18px 12px 18px; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; padding: 4px 8px; }}
QListWidget#nav::item {{ padding: 9px 10px; border-radius: 8px; margin: 1px 0; color: {t.text}; }}
QListWidget#nav::item:hover {{ background: {t.surface2}; }}
QListWidget#nav::item:selected {{ background: {t.selection}; color: {t.accent}; font-weight: 600; }}


QLabel[role="h1"] {{ font-size: 24px; font-weight: 700; }}
QLabel[role="h2"] {{ font-size: 17px; font-weight: 650; }}
QLabel[role="h3"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {t.muted}; }}
QLabel[role="small"] {{ color: {t.muted}; font-size: 12px; }}
QLabel[role="metric"] {{ font-size: 28px; font-weight: 700; }}
QLabel[role="mono"] {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px; color: {t.muted}; }}

QFrame#card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#card[accent="danger"] {{ border-left: 4px solid {t.danger}; }}
QFrame#card[accent="warning"] {{ border-left: 4px solid {t.warning}; }}
QFrame#card[accent="success"] {{ border-left: 4px solid {t.success}; }}
QFrame#card[accent="info"] {{ border-left: 4px solid {t.info}; }}
QFrame#card[accent="neutral"] {{ border-left: 4px solid {t.neutral}; }}
QFrame#callout {{ background: {rgba(t.info, 0.08)}; border: 1px solid {rgba(t.info, 0.3)}; border-radius: 10px; }}
QFrame#callout[tone="warning"] {{ background: {rgba(t.warning, 0.09)}; border-color: {rgba(t.warning, 0.4)}; }}
QFrame#callout[tone="danger"] {{ background: {rgba(t.danger, 0.08)}; border-color: {rgba(t.danger, 0.4)}; }}
QFrame#callout[tone="success"] {{ background: {rgba(t.success, 0.08)}; border-color: {rgba(t.success, 0.4)}; }}
QFrame#hline {{ background: {t.border}; max-height: 1px; min-height: 1px; border: none; }}

QPushButton {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: {t.surface2}; }}
QPushButton:focus {{ border: 2px solid {t.accent}; padding: 6px 13px; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.surface2}; }}
QPushButton[kind="primary"] {{ background: {t.accent}; color: {t.on_accent}; border: 1px solid {t.accent};
  font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t.accent_hover}; }}
QPushButton[kind="primary"]:disabled {{ background: {rgba(t.accent, 0.4)}; border-color: transparent; }}
QPushButton[kind="danger"] {{ color: {t.danger}; border-color: {rgba(t.danger, 0.5)}; }}
QPushButton[kind="danger"]:hover {{ background: {rgba(t.danger, 0.08)}; }}
QTableWidget QPushButton, QTreeWidget QPushButton {{ padding: 2px 10px; margin: 3px; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {t.accent}; padding: 2px 4px; }}
QPushButton[kind="link"]:hover {{ text-decoration: underline; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser {{
  background: {t.surface}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px 8px;
  selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
  border: 2px solid {t.accent}; padding: 5px 7px; }}
QComboBox QAbstractItemView {{ background: {t.surface}; border: 1px solid {t.border};
  selection-background-color: {t.selection}; selection-color: {t.text}; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {t.muted};
  background: {t.surface}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({check}); }}
QRadioButton::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url({dot}); }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:disabled {{ background: {t.surface2}; }}
QCheckBox:focus, QRadioButton:focus {{ color: {t.accent}; }}

QTreeWidget, QTableWidget, QListWidget {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px;
  alternate-background-color: {t.surface2}; outline: none; }}
QTreeWidget::item, QTableWidget::item, QListWidget::item {{ padding: 5px 4px; }}
QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{
  background: {t.selection}; color: {t.text}; }}
QHeaderView::section {{ background: {t.surface2}; color: {t.muted}; border: none;
  border-bottom: 1px solid {t.border}; padding: 6px 8px; font-weight: 600; font-size: 12px; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; margin-right: 4px; color: {t.muted};
  border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {t.accent}; border-bottom: 2px solid {t.accent}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t.text}; }}

QProgressBar {{ background: {t.surface2}; border: none; border-radius: 5px; height: 10px; text-align: center;
  font-size: 11px; color: transparent; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 5px; }}
QSplitter::handle {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {rgba(t.muted, 0.35)}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {rgba(t.muted, 0.35)}; border-radius: 4px; min-width: 30px; }}
QStatusBar {{ background: {t.surface}; border-top: 1px solid {t.border}; color: {t.muted}; }}
{badges}
"""
