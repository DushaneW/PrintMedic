"""Discover & Connect: multi-method discovery, add by address, USB devices, and troubleshooting."""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from printmedic.core.models import DiscoveredEndpoint, PortState, ProbeResult
from printmedic.core.validation import ValidationError, normalize_host
from printmedic.i18n import tr
from printmedic.network.probe import interpret
from printmedic.repair.actions import AddIppPrinter
from printmedic.services.discovery import METHODS, DiscoveryOptions, DiscoveryReport, MethodResult
from printmedic.ui import tasks
from printmedic.ui.widgets import Callout, Card, Page, amp, badge, button, clear_layout, label

METHOD_TONE = {"ok": "success", "failed": "danger", "running": "info", "cancelled": "warning",
               "skipped": "neutral", "pending": "neutral"}


def ipp_url_for(ep_host: str, probe: ProbeResult | None, ep: DiscoveredEndpoint | None = None) -> str | None:
    if probe and probe.ipp:
        return probe.ipp.uri
    if ep:
        for s in ep.services:
            if s.protocol == "ipp":
                return f"ipp://{ep_host}:{s.port}{s.path or '/ipp/print'}"
    return None


def suggest_name(model: str, host: str) -> str:
    base = re.sub(r"[^\w .()-]", "", model).strip()
    return f"{base or 'Printer ' + host} (IPP)"


class DiscoverPage(Page):
    title_key = "nav.discover"
    subtitle_key = "discover.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.task: tasks.Task | None = None
        self.report: DiscoveryReport | None = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scan_tab(), amp(tr("discover.tab_scan")))
        self.tabs.addTab(self._manual_tab(), amp(tr("discover.tab_manual")))
        self.tabs.addTab(self._usb_tab(), amp(tr("discover.tab_usb")))
        self.tabs.addTab(self._help_tab(), amp(tr("discover.tab_help")))
        self.content.addWidget(self.tabs, 1)

    # --- scan ---------------------------------------------------------------------------------
    def _scan_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(14)
        opts = Card(tr("discover.methods"))
        s = self.ctx.core.settings
        self.cb = {
            "installed": QCheckBox(amp(tr("method.installed"))),
            "usb": QCheckBox(amp(tr("method.usb"))),
            "mdns": QCheckBox(amp(tr("method.mdns"))),
            "wsd": QCheckBox(amp(tr("method.wsd"))),
            "subnet": QCheckBox(amp(tr("method.subnet"))),
        }
        self.cb["installed"].setChecked(True)
        self.cb["usb"].setChecked(True)
        self.cb["mdns"].setChecked(s.discovery_mdns)
        self.cb["wsd"].setChecked(s.discovery_wsd)
        self.cb["subnet"].setChecked(s.discovery_subnet)
        grid = QGridLayout()
        for i, m in enumerate(METHODS):
            grid.addWidget(self.cb[m], i // 3, i % 3)
        opts.add(grid)
        opts.add(label(tr("discover.subnet_note"), "small", wrap=True))
        row = QHBoxLayout()
        row.addWidget(label(tr("discover.interface")))
        self.iface = QComboBox()
        self.iface.setAccessibleName(tr("discover.interface"))
        row.addWidget(self.iface, 1)
        row.addWidget(label(tr("discover.duration")))
        self.duration = QDoubleSpinBox()
        self.duration.setRange(2, 20)
        self.duration.setSuffix(" s")
        self.duration.setValue(s.discovery_duration)
        self.duration.setAccessibleName(tr("discover.duration"))
        row.addWidget(self.duration)
        self.start_btn = button(tr("discover.start"), self._start, "primary")
        self.cancel_btn = button(tr("common.cancel"), self._cancel)
        self.cancel_btn.setEnabled(False)
        row.addWidget(self.start_btn)
        row.addWidget(self.cancel_btn)
        opts.add(row)
        lay.addWidget(opts)

        prog = Card(tr("discover.progress"))
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        prog.add(self.progress)
        self.method_rows = QGridLayout()
        self.method_rows.setHorizontalSpacing(16)
        prog.add(self.method_rows)
        lay.addWidget(prog)

        res = Card(tr("discover.results"))
        top = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("discover.filter"))
        self.filter.setAccessibleName(tr("discover.filter"))
        self.filter.textChanged.connect(self._render_results)
        top.addWidget(self.filter, 1)
        res.add(top)
        self.results = QTreeWidget()
        self.results.setHeaderLabels([tr("discover.col_device"), tr("discover.col_address"),
                                      tr("discover.col_protocols"), tr("discover.col_source"), ""])
        self.results.setMinimumHeight(260)
        self.results.setColumnWidth(0, 300)
        self.results.setColumnWidth(1, 150)
        self.results.setColumnWidth(2, 180)
        res.add(self.results)
        self.results_note = label("", "small", wrap=True)
        res.add(self.results_note)
        lay.addWidget(res)
        self._render_methods({m: MethodResult(m, "pending") for m in METHODS})
        return w

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        self._fill_interfaces()
        self._render_results()
        self._render_usb()

    def on_inventory(self) -> None:
        self._fill_interfaces()
        self._render_usb()

    def _fill_interfaces(self) -> None:
        snap = self.ctx.core.snapshot
        cur = self.iface.currentData()
        self.iface.clear()
        self.iface.addItem(tr("discover.all_interfaces"), None)
        if snap:
            for i in snap.interfaces:
                self.iface.addItem(f"{i.name} — {i.address}/{i.prefix_length} ({tr('iface.' + i.kind)})",
                                   (i.address, i.prefix_length))
        pref = cur or next(((i.address, i.prefix_length) for i in (snap.interfaces if snap else [])
                            if i.address == self.ctx.core.settings.discovery_interface), None)
        idx = self.iface.findData(pref)
        self.iface.setCurrentIndex(max(idx, 0))

    def _render_methods(self, methods: dict[str, MethodResult]) -> None:
        clear_layout(self.method_rows)
        for r, m in enumerate(METHODS):
            mr = methods.get(m, MethodResult(m, "skipped"))
            self.method_rows.addWidget(label(tr(f"method.{m}")), r, 0)
            self.method_rows.addWidget(badge(tr(f"mstatus.{mr.status}"), METHOD_TONE.get(mr.status, "neutral")), r, 1)
            detail = ""
            if mr.status in ("ok", "cancelled"):
                detail = tr("discover.found_n", n=mr.count, ms=mr.duration_ms)
            elif mr.status == "failed":
                detail = mr.error
            self.method_rows.addWidget(label(detail, "small", wrap=True), r, 2)
        self.method_rows.setColumnStretch(2, 1)

    def _start(self) -> None:
        methods = tuple(m for m in METHODS if self.cb[m].isChecked())
        iface = self.iface.currentData()
        if "subnet" in methods and not iface:
            self.results_note.setText(tr("discover.subnet_needs_iface"))
            return
        opts = DiscoveryOptions(methods=methods, duration=self.duration.value(),
                                interface_address=iface[0] if iface else None,
                                interface_prefix=iface[1] if iface else 24)
        state = {m: MethodResult(m, "pending" if m in methods else "skipped") for m in METHODS}
        self._render_methods(state)
        self.progress.setRange(0, 0)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.ctx.bus.busy.emit(True)

        def on_method_signal(msg: str, mr: Any) -> None:
            if isinstance(mr, MethodResult):
                state[mr.method] = mr
                self._render_methods(state)

        def work(cancel: Any, progress: Any) -> DiscoveryReport:
            return self.ctx.core.discover(opts, cancel, lambda m, f: None,
                                          on_method=lambda mr: progress("method", MethodResult(**vars(mr))))

        def done(report: DiscoveryReport) -> None:
            self._finish()
            self.report = report
            self._render_methods(report.methods)
            self._render_results()
            self.ctx.bus.inventory_changed.emit()
            self.ctx.notify(tr("app.name"), tr("discover.finished", n=len(report.endpoints)))

        def failed(m: str, _d: str) -> None:
            self._finish()
            for k, v in state.items():
                if v.status in ("pending", "running"):
                    state[k] = MethodResult(k, "cancelled")
            self._render_methods(state)
            self.results_note.setText(m)

        self.task = tasks.start(work, done, failed, on_method_signal)

    def _finish(self) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.ctx.bus.busy.emit(False)
        self.task = None

    def _cancel(self) -> None:
        if self.task:
            self.task.cancel.cancel()
            self.cancel_btn.setEnabled(False)

    def _render_results(self) -> None:
        core = self.ctx.core
        self.results.clear()
        q = self.filter.text().strip().lower()
        snap = core.snapshot
        groups = {}
        for key in ("installed", "usb", "network"):
            g = QTreeWidgetItem(self.results, [tr(f"discover.group_{key}")])
            g.setFirstColumnSpanned(True)
            g.setExpanded(True)
            g.setFlags(Qt.ItemFlag.ItemIsEnabled)
            groups[key] = g
        if snap:
            for qu in snap.queues:
                if q and q not in f"{qu.name} {qu.driver} {qu.device_host or ''}".lower():
                    continue
                QTreeWidgetItem(groups["installed"], [qu.name,
                                                      qu.device_host or qu.port,
                                                      tr(f"conn.{qu.connection.value}"), tr("source.spooler")])
            for u in snap.usb_devices:
                if q and q not in u.name.lower():
                    continue
                status = tr("discover.driver_missing") if u.driver_missing else (u.status or "")
                QTreeWidgetItem(groups["usb"], [u.name, "USB", status, "PnP"])
        installed_hosts = {qu.device_host for qu in snap.queues} if snap else set()
        for ep in core.endpoints:
            if q and q not in f"{ep.name} {ep.model} {ep.host}".lower():
                continue
            protos = ", ".join(sorted({f"{s.protocol}:{s.port}" for s in ep.services}))
            item = QTreeWidgetItem(groups["network"], [ep.name or ep.model or ep.host, ep.host, protos,
                                                       ", ".join(sorted(ep.sources))])
            if ep.host in installed_hosts:
                item.setText(4, tr("discover.already_installed"))
            elif any(s.protocol == "ipp" for s in ep.services):
                url = ipp_url_for(ep.host, core.probes.get(ep.host), ep)
                btn = button(tr("discover.add"), lambda ep=ep, url=url: self._add(ep.host, url,
                                                                                  ep.model or ep.name), "primary")
                self.results.setItemWidget(item, 4, btn)
            else:
                item.setText(4, tr("discover.no_ipp"))
        for key, g in groups.items():
            g.setText(0, f"{tr(f'discover.group_{key}')} ({g.childCount()})")
        self.results_note.setText(tr("discover.results_note"))

    def _add(self, host: str, url: str | None, model: str) -> None:
        if not url:
            return
        action = AddIppPrinter(host=host, url=url, name=suggest_name(model, host), port="631")
        self.ctx.run_action(action, on_done=lambda _r: self._render_results())

    # --- manual -------------------------------------------------------------------------------
    def _manual_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(14)
        c = Card(tr("discover.manual_title"))
        c.add(label(tr("discover.manual_text"), "muted", wrap=True))
        row = QHBoxLayout()
        self.host = QLineEdit()
        self.host.setPlaceholderText(tr("discover.host_placeholder"))
        self.host.setAccessibleName(tr("discover.host_placeholder"))
        self.host.returnPressed.connect(self._test_host)
        row.addWidget(self.host, 1)
        self.test_btn = button(tr("discover.test"), self._test_host, "primary")
        row.addWidget(self.test_btn)
        c.add(row)
        lay.addWidget(c)
        self.manual_out = QVBoxLayout()
        lay.addLayout(self.manual_out)
        lay.addStretch(1)
        return w

    def _test_host(self) -> None:
        clear_layout(self.manual_out)
        try:
            host = normalize_host(self.host.text())
        except ValidationError as exc:
            self.manual_out.addWidget(Callout(tr(f"validation.{exc}"), "danger"))
            return
        self.test_btn.setEnabled(False)
        self.manual_out.addWidget(label(tr("discover.testing", host=host), "muted"))

        def done(res: ProbeResult) -> None:
            self.test_btn.setEnabled(True)
            clear_layout(self.manual_out)
            self.manual_out.addWidget(probe_card(res))
            if res.ipp:
                add = Card(tr("discover.add_title"))
                name = QLineEdit(suggest_name(res.ipp.make_and_model, host))
                name.setAccessibleName(tr("discover.queue_name"))
                add.add(label(tr("discover.queue_name")))
                add.add(name)
                add.add(button(tr("discover.add_ipp"), lambda: self.ctx.run_action(AddIppPrinter(
                    host=host, url=res.ipp.uri, name=name.text().strip(), port=str(res.ipp.uri.split(":")[-1]
                                                                                   .split("/")[0]))), "primary"))
                self.manual_out.addWidget(add)
            else:
                self.manual_out.addWidget(button(tr("discover.open_settings"),
                                                 lambda: self.ctx.core.adapter.open_printer_settings()))
            self.ctx.bus.inventory_changed.emit()

        def failed(m: str, _d: str) -> None:
            self.test_btn.setEnabled(True)
            clear_layout(self.manual_out)
            self.manual_out.addWidget(Callout(m, "danger"))

        tasks.start(lambda cancel, p: self.ctx.core.probe(host, (), cancel), done, failed)

    # --- usb ----------------------------------------------------------------------------------
    def _usb_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(Callout(tr("discover.usb_intro"), "info"))
        self.usb_box = QVBoxLayout()
        lay.addLayout(self.usb_box)
        row = QHBoxLayout()
        row.addWidget(button(tr("discover.usb_rescan"), self.ctx.refresh_inventory, "primary"))
        row.addWidget(button(tr("discover.open_settings"), lambda: self.ctx.core.adapter.open_printer_settings()))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return w

    def _render_usb(self) -> None:
        clear_layout(self.usb_box)
        snap = self.ctx.core.snapshot
        if snap is None:
            self.usb_box.addWidget(label(tr("overview.not_loaded"), "muted"))
            return
        usb_devs = [u for u in snap.usb_devices if u.is_print_function or u.pnp_class == "Printer"]
        if not usb_devs:
            self.usb_box.addWidget(label(tr("discover.usb_none"), "muted"))
        usb_queues = [d for d in self.ctx.core.devices if d.connection.value == "usb"]
        for u in usb_devs:
            c = Card(accent="danger" if u.driver_missing or u.problem_code else "success")
            top = QHBoxLayout()
            top.addWidget(label(u.name or u.instance_id, "h3"), 1)
            c.add(top)
            linked = next((d for d in usb_queues if d.usb and d.usb.instance_id == u.instance_id), None)
            if u.driver_missing:
                c.add(label(tr("discover.usb_driver_missing"), wrap=True))
            elif u.problem_code:
                c.add(label(tr("discover.usb_problem", code=u.problem_code), wrap=True))
            elif linked:
                c.add(label(tr("discover.usb_linked", queue=linked.display_name), wrap=True))
            else:
                c.add(label(tr("discover.usb_no_queue"), wrap=True))
            c.add(label(u.instance_id, "mono", selectable=True))
            self.usb_box.addWidget(c)

    # --- help ---------------------------------------------------------------------------------
    def _help_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)
        for i in range(1, 8):
            c = Card()
            c.add(label(tr(f"discover.help{i}.title"), "h3", wrap=True))
            c.add(label(tr(f"discover.help{i}.text"), "muted", wrap=True))
            lay.addWidget(c)
        lay.addWidget(button(tr("discover.open_assistant"),
                             lambda: self.ctx.bus.navigate.emit("network", {"assistant": True}), "primary"))
        lay.addStretch(1)
        return w


PORT_NAMES = {631: "IPP", 443: "IPPS/HTTPS", 9100: "RAW (JetDirect)", 515: "LPD", 80: "HTTP"}
PORT_TONE = {PortState.OPEN: "success", PortState.REFUSED: "warning", PortState.TIMEOUT: "neutral",
             PortState.UNREACHABLE: "danger", PortState.ERROR: "danger"}


def probe_card(res: ProbeResult) -> Card:
    verdict = interpret(res)
    tone = {"ipp_ok": "success", "printing_port_open": "success", "host_up_no_printing_service": "warning",
            "dns_failure": "danger", "no_answer": "danger", "no_route": "danger"}.get(verdict, "neutral")
    c = Card(tr("probe.title", host=res.host), accent=tone)
    c.add(Callout(tr(f"verdict.{verdict}"), tone, tr(f"verdict.{verdict}.title")))
    c.add(label(tr("probe.addresses", addrs=", ".join(res.addresses) or "—",
                   err=res.dns_error or "") if res.dns_error else
                tr("probe.addresses_ok", addrs=", ".join(res.addresses)), wrap=True))
    grid = QGridLayout()
    for r, (port, state) in enumerate(sorted(res.ports.items())):
        grid.addWidget(label(f"TCP {port}"), r, 0)
        grid.addWidget(label(PORT_NAMES.get(port, tr("probe.configured_port")), "muted"), r, 1)
        grid.addWidget(badge(tr(f"port.{state.value}"), PORT_TONE[state], "inferred"), r, 2)
    grid.setColumnStretch(3, 1)
    c.add(grid)
    if res.ipp:
        i = res.ipp
        c.add(label(tr("probe.ipp_title"), "h3"))
        c.add(label(tr("probe.ipp_line", model=i.make_and_model or "?", state=tr(f"ippstate.{i.state}"),
                       reasons=", ".join(i.state_reasons) or "none", uri=i.uri), wrap=True, selectable=True))
        if i.formats:
            c.add(label(tr("probe.formats", formats=", ".join(i.formats[:8])), "small", wrap=True))
    elif res.ipp_error:
        c.add(label(tr("probe.ipp_error", error=res.ipp_error[:200]), "small", wrap=True))
    c.add(label(tr("probe.footer", ms=res.duration_ms), "small", wrap=True))
    return c
