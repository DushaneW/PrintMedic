"""Qt application bootstrap."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from printmedic import APP_NAME
from printmedic.app_core import AppCore
from printmedic.services.settings import data_dir
from printmedic.ui import theme
from printmedic.ui.context import UiContext
from printmedic.ui.main_window import MainWindow


def _logging() -> None:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(d / "printmedic.log", encoding="utf-8")],
    )


def run(argv: list[str] | None = None) -> int:
    _logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    core = AppCore()
    ctx = UiContext(core, theme.resolve(core.settings.theme))
    win = MainWindow(ctx)
    win.show()
    win.navigate("overview", None)
    ctx.refresh_inventory()
    return app.exec()
