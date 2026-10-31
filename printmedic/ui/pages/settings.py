"""Settings: language, theme, discovery, privacy, diagnostics, retention, and About."""

from __future__ import annotations

import platform
import sys
from importlib import metadata
from typing import Any

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QSpinBox

from printmedic import __version__
from printmedic.i18n import tr
from printmedic.ui.widgets import Callout, Card, Page, amp, button, kv_grid, label


def _ver(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return tr("settings.not_installed")


class SettingsPage(Page):
    title_key = "nav.settings"
    subtitle_key = "settings.subtitle"

    def __init__(self, ctx: Any, on_apply_appearance: Any) -> None:
        super().__init__(ctx)
        self._apply_appearance = on_apply_appearance
        s = ctx.core.settings

        ap = Card(tr("settings.appearance"))
        form = QFormLayout()
        self.lang = QComboBox()
        self.lang.addItem("English", "en")
        self.lang.addItem("Polski", "pl")
        self.lang.setCurrentIndex(max(self.lang.findData(s.language), 0))
        self.theme = QComboBox()
        for k in ("system", "light", "dark"):
            self.theme.addItem(tr(f"settings.theme.{k}"), k)
        self.theme.setCurrentIndex(max(self.theme.findData(s.theme), 0))
        form.addRow(tr("settings.language"), self.lang)
        form.addRow(tr("settings.theme"), self.theme)
        ap.add(form)
        ap.add(button(tr("settings.apply_appearance"), self._appearance, "primary"))
        self.content.addWidget(ap)

        disc = Card(tr("settings.discovery"))
        self.mdns = QCheckBox(amp(tr("method.mdns")))
        self.mdns.setChecked(s.discovery_mdns)
        self.wsd = QCheckBox(amp(tr("method.wsd")))
        self.wsd.setChecked(s.discovery_wsd)
        self.subnet = QCheckBox(amp(tr("method.subnet")))
        self.subnet.setChecked(s.discovery_subnet)
        for cb in (self.mdns, self.wsd, self.subnet):
            cb.toggled.connect(self._save)
            disc.add(cb)
        disc.add(label(tr("discover.subnet_note"), "small", wrap=True))
        form2 = QFormLayout()
        self.duration = QDoubleSpinBox()
        self.duration.setRange(2, 20)
        self.duration.setSuffix(" s")
        self.duration.setValue(s.discovery_duration)
        self.duration.valueChanged.connect(self._save)
        form2.addRow(tr("discover.duration"), self.duration)
        self.iface = QComboBox()
        self.iface.addItem(tr("discover.all_interfaces"), "")
        snap = ctx.core.snapshot
        for i in snap.interfaces if snap else []:
            self.iface.addItem(f"{i.name} — {i.address}", i.address)
        self.iface.setCurrentIndex(max(self.iface.findData(s.discovery_interface), 0))
        self.iface.currentIndexChanged.connect(self._save)
        form2.addRow(tr("discover.interface"), self.iface)
        disc.add(form2)
        self.content.addWidget(disc)

        priv = Card(tr("settings.privacy"))
        self.redact = QCheckBox(amp(tr("settings.redact_default")))
        self.redact.setChecked(s.redact_exports)
        self.redact.toggled.connect(self._save)
        priv.add(self.redact)
        priv.add(kv_grid([(tr("settings.telemetry"), tr("settings.telemetry_none")),
                          (tr("settings.network_requests"), tr("settings.network_requests_text")),
                          (tr("settings.documents"), tr("settings.documents_text"))]))
        self.content.addWidget(priv)

        diag = Card(tr("settings.diagnostics"))
        form3 = QFormLayout()
        self.verbosity = QComboBox()
        self.verbosity.addItem(tr("settings.verbosity.normal"), "normal")
        self.verbosity.addItem(tr("settings.verbosity.detailed"), "detailed")
        self.verbosity.setCurrentIndex(max(self.verbosity.findData(s.verbosity), 0))
        self.verbosity.currentIndexChanged.connect(self._save)
        form3.addRow(tr("settings.verbosity"), self.verbosity)
        self.retention = QSpinBox()
        self.retention.setRange(0, 3650)
        self.retention.setSuffix(" " + tr("settings.days"))
        self.retention.setSpecialValueText(tr("settings.keep_forever"))
        self.retention.setValue(s.retention_days)
        self.retention.valueChanged.connect(self._save)
        form3.addRow(tr("settings.retention"), self.retention)
        self.notify = QCheckBox(amp(tr("settings.notifications_enable")))
        self.notify.setChecked(s.notifications)
        self.notify.toggled.connect(self._save)
        form3.addRow(tr("settings.notifications"), self.notify)
        diag.add(form3)
        row = QHBoxLayout()
        row.addWidget(button(tr("settings.apply_retention"), self._retention_now))
        row.addStretch(1)
        diag.add(row)
        self.content.addWidget(diag)

        core = ctx.core
        integ = Card(tr("settings.integrations"))
        snap = core.snapshot
        integ.add(kv_grid([
            (tr("settings.adapter"), core.adapter.name),
            (tr("settings.platform"), f"{platform.system()} {platform.release()} ({platform.machine()})"),
            (tr("settings.admin"), (tr("common.yes") if snap.is_admin else tr("common.no")) if snap else "—"),
            (tr("settings.ipp"), tr("settings.ipp_text")),
            (tr("settings.mdns"), "python-zeroconf " + _ver("zeroconf")),
            (tr("settings.wsd"), tr("settings.wsd_text")),
            (tr("settings.ai"), tr("settings.ai_text")),
            (tr("settings.linux_mac"), tr("settings.linux_mac_text")),
        ]))
        self.content.addWidget(integ)

        about = Card(tr("settings.about"))
        about.add(kv_grid([
            (tr("settings.version"), f"PrintMedic {__version__}"),
            ("Python", sys.version.split()[0]),
            ("Qt / PySide6", _ver("PySide6")),
            (tr("settings.licenses"), tr("settings.licenses_text")),
            (tr("settings.updates"), tr("settings.updates_text")),
            (tr("settings.data_folder"), str(core.history.path.parent)),
        ]))
        self.content.addWidget(about)
        self.content.addWidget(Callout(tr("settings.honesty"), "info"))
        self.content.addStretch(1)

    def _save(self, *_a: Any) -> None:
        s = self.ctx.core.settings
        s.discovery_mdns = self.mdns.isChecked()
        s.discovery_wsd = self.wsd.isChecked()
        s.discovery_subnet = self.subnet.isChecked()
        s.discovery_duration = self.duration.value()
        s.discovery_interface = self.iface.currentData() or ""
        s.redact_exports = self.redact.isChecked()
        s.verbosity = self.verbosity.currentData()
        s.retention_days = self.retention.value()
        s.notifications = self.notify.isChecked()
        self.ctx.core.save_settings()
        self.ctx.bus.status.emit(tr("settings.saved"))

    def _appearance(self) -> None:
        s = self.ctx.core.settings
        s.language = self.lang.currentData()
        s.theme = self.theme.currentData()
        self.ctx.core.save_settings()
        self._apply_appearance()

    def _retention_now(self) -> None:
        n = self.ctx.core.history.apply_retention(self.ctx.core.settings.retention_days)
        self.ctx.bus.history_changed.emit()
        self.ctx.bus.status.emit(tr("settings.retention_done", n=n))
