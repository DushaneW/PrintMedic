"""My Printers: searchable, filterable device list with a detail panel."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from printmedic.core.models import ConnectionType, Device, StatusOrigin
from printmedic.i18n import tr
from printmedic.repair.actions import PrintTestPage, RemoveJob, ResumeQueue, SetDefault, SetOnline
from printmedic.ui import tasks
from printmedic.ui.pages.common import conn_label, device_state, needs_attention
from printmedic.ui.widgets import (
    LEVEL_TONE,
    Callout,
    Card,
    EmptyState,
    Page,
    badge,
    button,
    clear_layout,
    kv_grid,
    label,
    scroll_page,
    state_badge,
)


class PrintersPage(Page):
    title_key = "nav.printers"
    subtitle_key = "printers.subtitle"

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.header_row.addWidget(button(tr("common.refresh"), ctx.refresh_inventory))
        self.selected: str | None = None

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("printers.search"))
        self.search.setAccessibleName(tr("printers.search"))
        self.search.textChanged.connect(self._fill)
        bar.addWidget(self.search, 2)
        self.f_vendor = QComboBox()
        self.f_conn = QComboBox()
        self.f_status = QComboBox()
        self.group = QComboBox()
        for combo, name in ((self.f_vendor, "printers.filter_vendor"), (self.f_conn, "printers.filter_conn"),
                            (self.f_status, "printers.filter_status"), (self.group, "printers.group")):
            combo.setAccessibleName(tr(name))
            combo.setToolTip(tr(name))
            combo.currentIndexChanged.connect(self._fill)
            bar.addWidget(combo, 1)
        self.group.addItem(tr("printers.group_none"), "none")
        self.group.addItem(tr("printers.group_conn"), "conn")
        self.group.addItem(tr("printers.group_vendor"), "vendor")
        self.f_status.addItem(tr("printers.status_all"), "all")
        self.f_status.addItem(tr("printers.status_attention"), "attention")
        self.f_status.addItem(tr("printers.status_ok"), "ok")
        self.content.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("printers.col_name"), tr("printers.col_vendor"), tr("printers.col_conn"),
                                   tr("printers.col_status"), tr("printers.col_jobs")])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setMinimumWidth(420)
        self.tree.setMinimumHeight(520)
        self.tree.itemSelectionChanged.connect(self._on_select)
        hdr = self.tree.header()
        hdr.setStretchLastSection(True)
        for col in range(4):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        split.addWidget(self.tree)
        self.detail_host = QWidget()
        self.detail = QVBoxLayout(self.detail_host)
        self.detail.setContentsMargins(8, 0, 0, 0)
        self.detail.setSpacing(12)
        split.addWidget(scroll_page(self.detail_host))
        split.setSizes([540, 640])
        split.setMinimumHeight(640)
        self.content.addWidget(split, 1)

    def on_shown(self, payload: dict[str, Any] | None = None) -> None:
        if payload and payload.get("device"):
            self.selected = payload["device"]
        self.on_inventory()

    def on_inventory(self) -> None:
        devices = self.ctx.core.devices
        for combo, values in ((self.f_vendor, sorted({d.vendor_name for d in devices})),
                              (self.f_conn, sorted({d.connection.value for d in devices}))):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr("printers.all"), None)
            for v in values:
                combo.addItem(conn_label(ConnectionType(v)) if combo is self.f_conn else v, v)
            idx = combo.findData(cur)
            combo.setCurrentIndex(max(idx, 0))
            combo.blockSignals(False)
        self._fill()

    def _visible(self) -> list[Device]:
        q = self.search.text().strip().lower()
        out = []
        for d in self.ctx.core.devices:
            if q and q not in f"{d.display_name} {d.model} {d.vendor_name} {d.host or ''}".lower():
                continue
            if self.f_vendor.currentData() and d.vendor_name != self.f_vendor.currentData():
                continue
            if self.f_conn.currentData() and d.connection.value != self.f_conn.currentData():
                continue
            st = self.f_status.currentData()
            if st == "attention" and not needs_attention(d):
                continue
            if st == "ok" and needs_attention(d):
                continue
            out.append(d)
        return out

    def _fill(self) -> None:
        if not hasattr(self, "tree"):
            return  # filter combos emit while the page is still being built
        self.tree.blockSignals(True)
        self.tree.clear()
        grouping = self.group.currentData()
        groups: dict[str, QTreeWidgetItem] = {}
        self.tree.setRootIsDecorated(grouping != "none")
        select_item = None
        for d in self._visible():
            parent: Any = self.tree
            if grouping in ("conn", "vendor"):
                key = conn_label(d.connection) if grouping == "conn" else d.vendor_name
                if key not in groups:
                    g = QTreeWidgetItem(self.tree, [key])
                    g.setFirstColumnSpanned(True)
                    g.setExpanded(True)
                    g.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    groups[key] = g
                parent = groups[key]
            name = d.display_name
            if d.queue and d.queue.is_default:
                name += "  ★"
            item = QTreeWidgetItem(parent, [name, d.vendor_name, conn_label(d.connection),
                                            tr(f"state.{device_state(d).value}"),
                                            str(len(d.queue.jobs)) if d.queue else "—"])
            item.setData(0, Qt.ItemDataRole.UserRole, d.device_id)
            item.setToolTip(0, d.display_name)
            if needs_attention(d):
                item.setText(3, "● " + item.text(3))
            if d.device_id == self.selected:
                select_item = item
        self.tree.blockSignals(False)
        if select_item:
            self.tree.setCurrentItem(select_item)
        self._render_detail()
        if not self.ctx.core.devices:
            clear_layout(self.detail)
            self.detail.addWidget(EmptyState(tr("printers.empty_title"), tr("printers.empty_text"),
                                             [button(tr("overview.discover"),
                                                     lambda: self.ctx.bus.navigate.emit("discover", None), "primary")]))

    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        did = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
        if did:
            self.selected = did
            self._render_detail()

    # ------------------------------------------------------------------------------------------
    def _render_detail(self) -> None:
        clear_layout(self.detail)
        d = self.ctx.core.device(self.selected) if self.selected else None
        if d is None:
            self.detail.addWidget(label(tr("printers.select_hint"), "muted"))
            self.detail.addStretch(1)
            return
        head = Card()
        top = QHBoxLayout()
        top.addWidget(label(d.display_name, "h2", wrap=True), 1)
        head.add(top)
        states = QHBoxLayout()
        if d.queue:
            states.addWidget(label(tr("printers.queue_status"), "muted"))
            states.addWidget(state_badge(d.queue.state, StatusOrigin.REPORTED_OS))
        if d.probe and d.probe.ipp:
            states.addWidget(label(tr("printers.device_status"), "muted"))
            tone = {"idle": "success", "processing": "info", "stopped": "danger"}.get(d.probe.ipp.state, "neutral")
            states.addWidget(badge(tr(f"ippstate.{d.probe.ipp.state}"), tone, "reported_device",
                                   tr("origin.reported_device.long")))
        elif d.probe and d.probe.ports:
            states.addWidget(label(tr("printers.network_status"), "muted"))
            ok = d.probe.host_answered
            states.addWidget(badge(tr("printers.answers") if ok else tr("printers.no_answer"),
                                   "success" if ok else "danger", "inferred", tr("origin.inferred.long")))
        last = self._last_verified(d)
        if last:
            states.addWidget(badge("✓ " + tr("printers.last_verified", when=last), "success", "verified",
                                   tr("origin.verified.long")))
        states.addStretch(1)
        head.add(states)
        head.add(label(tr("printers.legend"), "small", wrap=True))
        q = d.queue
        rows = [(tr("printers.model"), d.model), (tr("printers.vendor"), d.vendor_name),
                (tr("printers.connection"), conn_label(d.connection))]
        if q:
            rows += [(tr("printers.queue"), q.name), (tr("printers.driver"), q.driver), (tr("printers.port"), q.port),
                     (tr("printers.default"), tr("common.yes") if q.is_default else tr("common.no")),
                     (tr("printers.flags"), ", ".join(q.status_flags) or "—")]
        if d.host:
            rows.append((tr("printers.host"), d.host))
        if d.usb:
            rows.append((tr("printers.usb_device"), f"{d.usb.name} ({d.usb.status or '?'})"))
        if d.probe and d.probe.ipp:
            rows += [(tr("printers.reported_model"), d.probe.ipp.make_and_model),
                     (tr("printers.firmware"), d.probe.ipp.firmware or "—"),
                     (tr("printers.state_reasons"), ", ".join(d.probe.ipp.state_reasons) or "—")]
        head.add(kv_grid(rows))

        btns: list[Any] = []
        did = d.device_id
        btns.append(button(tr("common.diagnose"), lambda: self.ctx.bus.navigate.emit(
            "diagnostics", {"target": did, "autorun": True}), "primary"))
        if d.host:
            btns.append(button(tr("printers.probe"), lambda: self._probe(d)))
        if q and not q.is_default:
            btns.append(button(tr("printers.set_default"), lambda: self.ctx.run_action(SetDefault(queue=q.name))))
        if q and q.state.value == "paused":
            btns.append(button(tr("action.resume_queue", queue=""), lambda: self.ctx.run_action(
                ResumeQueue(queue=q.name))))
        if q and q.work_offline:
            btns.append(button(tr("action.set_online", queue=""), lambda: self.ctx.run_action(
                SetOnline(queue=q.name))))
        if q and not q.is_virtual:
            btns.append(button(tr("printers.test_page"), lambda: self.ctx.run_action(PrintTestPage(queue=q.name))))
        acts = QGridLayout()
        acts.setHorizontalSpacing(8)
        acts.setVerticalSpacing(8)
        for i, b in enumerate(btns):
            acts.addWidget(b, i // 2, i % 2)
        acts.setColumnStretch(2, 1)
        head.add(acts)
        self.detail.addWidget(head)

        if q:
            jobs = Card(tr("printers.jobs_title", n=len(q.jobs)))
            if not q.jobs:
                jobs.add(label(tr("printers.no_jobs"), "muted"))
            else:
                t = QTableWidget(len(q.jobs), 5)
                t.setHorizontalHeaderLabels([tr("jobs.id"), tr("jobs.document"), tr("jobs.status"),
                                             tr("jobs.age"), ""])
                t.verticalHeader().setVisible(False)
                t.verticalHeader().setDefaultSectionSize(40)
                t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
                for r, j in enumerate(q.jobs):
                    age = j.age_minutes()
                    for c, v in enumerate([str(j.job_id), j.document, ", ".join(j.status) or tr("jobs.waiting"),
                                           tr("jobs.minutes", n=int(age)) if age is not None else "—"]):
                        t.setItem(r, c, QTableWidgetItem(v))
                    jid = j.job_id
                    t.setCellWidget(r, 4, button(tr("jobs.remove"), lambda jid=jid: self.ctx.run_action(
                        RemoveJob(queue=q.name, job_id=str(jid))), "danger"))
                t.horizontalHeader().setStretchLastSection(False)
                t.resizeColumnsToContents()
                t.setColumnWidth(1, 220)
                t.setMinimumHeight(min(48 + 40 * len(q.jobs), 280))
                jobs.add(t)
            self.detail.addWidget(jobs)

        if d.consumables:
            self.detail.addWidget(Callout(tr("printers.consumables_available"), "info"))

        caps = Card(tr("printers.capabilities"))
        caps.add(label(tr("printers.capabilities_note"), "small", wrap=True))
        t = QTableWidget(len(d.capabilities), 4)
        t.setHorizontalHeaderLabels([tr("caps.capability"), tr("caps.level"), tr("caps.source"), tr("caps.note")])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for r, c in enumerate(d.capabilities):
            t.setItem(r, 0, QTableWidgetItem(tr(f"cap.{c.capability.value}")))
            t.setCellWidget(r, 1, badge(tr(f"level.{c.level.value}"), LEVEL_TONE[c.level]))
            t.setItem(r, 2, QTableWidgetItem(c.source))
            t.setItem(r, 3, QTableWidgetItem(tr(c.note) if c.note else ""))
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        t.setMinimumHeight(36 * len(d.capabilities) + 40)
        caps.add(t)
        self.detail.addWidget(caps)
        self.detail.addStretch(1)

    def _last_verified(self, d: Device) -> str | None:
        if not d.queue:
            return None
        for r in self.ctx.core.history.repairs(limit=200):
            if r.get("params", {}).get("queue") == d.queue.name and r.get("status") == "verified":
                return str(r.get("finished_at", ""))[:16].replace("T", " ")
        return None

    def _probe(self, d: Device) -> None:
        host = d.host
        if not host:
            return
        extra = (d.queue.device_port,) if d.queue and d.queue.device_port else ()
        self.ctx.bus.status.emit(tr("status.probing", host=host))
        self.ctx.bus.busy.emit(True)

        def done(_r: Any) -> None:
            self.ctx.bus.busy.emit(False)
            self.ctx.bus.status.emit(tr("status.probe_done", host=host))
            self.ctx.bus.inventory_changed.emit()

        def failed(m: str, _d: str) -> None:
            self.ctx.bus.busy.emit(False)
            self.ctx.bus.status.emit(m)

        tasks.start(lambda c, p: self.ctx.core.probe(host, extra, c), done, failed)
