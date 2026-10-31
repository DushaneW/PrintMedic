"""Consent-gated repair execution with timeouts and mandatory post-action verification.

Invariants (enforced here, not in the UI):
  * nothing runs without a `Consent` issued for this exact action key and preview;
  * if the preview changed since consent (e.g. more jobs arrived), consent is void;
  * preconditions are re-checked against a fresh snapshot immediately before execution;
  * success is decided by verification, never by the command's exit status alone;
  * every attempt — including refusals — is written to the audit log.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Protocol

from printmedic.core.models import SystemSnapshot, utcnow
from printmedic.platform.base import OpResult
from printmedic.repair.actions import Action, Env, Kind, Msg, Verification, VerifyStatus


@dataclass(frozen=True)
class Consent:
    action_key: str
    preview_fingerprint: str
    granted_at: _dt.datetime
    acknowledged_data_loss: bool = False
    acknowledged_admin: bool = False


def grant(action: Action, snapshot: SystemSnapshot, *, data_loss_ack: bool = False,
          admin_ack: bool = False) -> Consent:
    """Create a consent record. Call only from an explicit user interaction."""
    return Consent(action.key, action.preview(snapshot).fingerprint(), utcnow(), data_loss_ack, admin_ack)


@dataclass
class RepairRecord:
    record_id: str
    action_id: str
    action_key: str
    params: dict[str, str]
    risk: str
    status: str            # refused | blocked | failed | verified | partial | needs_user | done | timeout
    exec_result: dict[str, Any] = field(default_factory=dict)
    verification: list[Msg] = field(default_factory=list)
    blocked_by: list[Msg] = field(default_factory=list)
    consent_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    finding_codes: list[str] = field(default_factory=list)
    session_id: str | None = None
    simulated: bool = False
    user_confirmation: str | None = None


class AuditSink(Protocol):
    def save_repair(self, record: RepairRecord) -> None: ...


def state_summary(snap: SystemSnapshot, queue: str | None) -> dict[str, Any]:
    """Compact before/after evidence for the audit log (redacted again on export)."""
    out: dict[str, Any] = {"spooler": snap.spooler.state if snap.spooler else None,
                           "default": snap.default_queue.name if snap.default_queue else None}
    q = snap.queue(queue) if queue else None
    if q:
        out["queue"] = {"state": q.state.value, "flags": q.status_flags, "work_offline": q.work_offline,
                        "jobs": [{"id": j.job_id, "status": j.status} for j in q.jobs]}
    return out


class RepairExecutor:
    def __init__(self, env: Env, audit: AuditSink | None = None) -> None:
        self.env = env
        self.audit = audit

    def _finish(self, rec: RepairRecord) -> RepairRecord:
        rec.finished_at = utcnow().isoformat()
        if self.audit:
            self.audit.save_repair(rec)
        return rec

    def run(self, action: Action, consent: Consent | None, *, session_id: str | None = None,
            finding_codes: list[str] | None = None) -> RepairRecord:
        rec = RepairRecord(
            record_id=uuid.uuid4().hex[:12], action_id=action.action_id, action_key=action.key,
            params=dict(action.params), risk=action.risk.value, status="refused", started_at=utcnow().isoformat(),
            session_id=session_id, finding_codes=list(finding_codes or []),
            simulated=getattr(self.env.adapter, "simulated", False),
        )
        if action.kind == Kind.NAVIGATE:
            rec.status = "done"
            return rec
        if consent is None or consent.action_key != action.key:
            rec.exec_result = {"code": "no_consent"}
            return self._finish(rec)
        rec.consent_at = consent.granted_at.isoformat()
        if action.data_loss and not consent.acknowledged_data_loss:
            rec.exec_result = {"code": "data_loss_not_acknowledged"}
            return self._finish(rec)

        before = self.env.adapter.snapshot()
        rec.before = state_summary(before, action.queue or None)
        if action.preview(before).fingerprint() != consent.preview_fingerprint:
            rec.exec_result = {"code": "preview_changed"}
            return self._finish(rec)
        blocked = action.preconditions(before)
        if blocked:
            rec.status = "blocked"
            rec.blocked_by = blocked
            return self._finish(rec)

        result = self._execute_with_timeout(action)
        rec.exec_result = {"ok": result.ok, "code": result.code, "message": result.message[:500]}
        if not result.ok:
            rec.status = "timeout" if result.code == "timeout" else "failed"
            rec.after = state_summary(self.env.adapter.snapshot(), action.queue or None)
            return self._finish(rec)

        if action.kind == Kind.EXTERNAL:
            rec.status = "done"
            return self._finish(rec)

        try:
            ver = action.verify(self.env, before)
        except Exception as exc:  # verification problems must surface, never be read as success
            ver = Verification(VerifyStatus.FAILED, [("verify.error", {"error": str(exc)[:200]})])
        rec.verification = ver.observations
        rec.status = {VerifyStatus.VERIFIED: "verified", VerifyStatus.PARTIAL: "partial",
                      VerifyStatus.FAILED: "failed", VerifyStatus.NEEDS_USER: "needs_user",
                      VerifyStatus.NOT_APPLICABLE: "done"}[ver.status]
        try:
            rec.after = state_summary(self.env.adapter.snapshot(), action.queue or None)
        except Exception as exc:
            rec.after = {"error": str(exc)[:200]}
        return self._finish(rec)

    def _execute_with_timeout(self, action: Action) -> OpResult:
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(action.execute, self.env)
        try:
            return fut.result(timeout=action.timeout_s)
        except FutureTimeout:
            return OpResult(False, "timeout", f"no result within {action.timeout_s:.0f}s")
        except Exception as exc:
            return OpResult(False, "failed", str(exc))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
