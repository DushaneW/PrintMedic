"""Helpers shared by several pages."""

from __future__ import annotations

from printmedic.core.models import ConnectionType, Device, QueueState
from printmedic.i18n import tr

HEALTHY = (QueueState.READY, QueueState.PRINTING, QueueState.POWER_SAVE)


def device_state(d: Device) -> QueueState:
    if d.queue:
        return d.queue.state
    if d.usb and (d.usb.driver_missing or d.usb.problem_code):
        return QueueState.ERROR
    if d.probe and d.probe.ipp:
        return {"idle": QueueState.READY, "processing": QueueState.PRINTING,
                "stopped": QueueState.ERROR}.get(d.probe.ipp.state, QueueState.UNKNOWN)
    return QueueState.UNKNOWN


def needs_attention(d: Device) -> bool:
    if d.connection == ConnectionType.VIRTUAL:
        return False
    if d.usb and d.usb.driver_missing:
        return True
    if d.queue and (d.queue.state not in HEALTHY or any(j.is_error for j in d.queue.jobs)):
        return True
    if d.probe and d.probe.ports and not d.probe.host_answered:
        return True
    return bool(d.probe and d.probe.ipp and d.probe.ipp.state == "stopped")


def attention_reason(d: Device) -> str:
    if d.usb and d.usb.driver_missing:
        return tr("attention.driver_missing")
    if d.queue and any(j.is_error for j in d.queue.jobs):
        return tr("attention.job_errors", n=sum(1 for j in d.queue.jobs if j.is_error))
    if d.probe and d.probe.ports and not d.probe.host_answered:
        return tr("attention.no_answer")
    if d.probe and d.probe.ipp and d.probe.ipp.state == "stopped":
        return tr("attention.device_stopped")
    if d.queue:
        return tr(f"state.{d.queue.state.value}")
    return ""


def conn_label(c: ConnectionType) -> str:
    return tr(f"conn.{c.value}")


def physical(devices: list[Device]) -> list[Device]:
    return [d for d in devices if d.connection != ConnectionType.VIRTUAL]
