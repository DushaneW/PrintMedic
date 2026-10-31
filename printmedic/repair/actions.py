"""Repair action definitions.

Every action declares risk, admin needs, data loss and reversibility, and implements:
  preconditions(snapshot) → problems that block execution
  preview(snapshot)       → exactly what will change (shown before consent)
  execute(env)            → performs the change through the platform adapter
  verify(env, before)     → re-reads state and decides whether the goal was reached

User-facing text is expressed as i18n keys + params so the engine stays language-neutral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from printmedic.core.models import ConnectionType, QueueState, SystemSnapshot
from printmedic.core.validation import ValidationError, normalize_host, validate_ipp_url
from printmedic.network.ipp import IppError
from printmedic.network.probe import NetworkProbe, interpret, probe_host
from printmedic.platform.base import OpResult, PlatformAdapter
from printmedic.services.urls import is_allowed_url

Msg = tuple[str, dict[str, Any]]


class Risk(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Kind(str, Enum):
    SYSTEM = "system"      # changes OS state through the adapter
    READ = "read"          # read-only re-check (network/status)
    EXTERNAL = "external"  # opens a browser / OS settings page
    GUIDED = "guided"      # a person performs the steps; PrintMedic verifies afterwards
    NAVIGATE = "navigate"  # moves the user to another part of the app


class VerifyStatus(str, Enum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_USER = "needs_user"          # outcome is physical (e.g. a page on paper)
    NOT_APPLICABLE = "not_applicable"  # nothing to verify (e.g. a web page was opened)


@dataclass
class Preview:
    effects: list[Msg] = field(default_factory=list)
    warnings: list[Msg] = field(default_factory=list)
    steps: list[Msg] = field(default_factory=list)       # guided checklist
    rollback: Msg | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return repr((self.effects, self.warnings, sorted(self.details.items())))


@dataclass
class Verification:
    status: VerifyStatus
    observations: list[Msg] = field(default_factory=list)


@dataclass
class Env:
    adapter: PlatformAdapter
    net: NetworkProbe


class Action:
    action_id: str = ""
    kind: Kind = Kind.SYSTEM
    risk: Risk = Risk.LOW
    requires_admin: bool = False
    data_loss: bool = False
    reversible: bool = True
    timeout_s: float = 60.0

    def __init__(self, **params: str) -> None:
        self.params = params

    @property
    def key(self) -> str:
        return self.action_id + "|" + "|".join(f"{k}={v}" for k, v in sorted(self.params.items()))

    @property
    def queue(self) -> str:
        return self.params.get("queue", "")

    def title(self) -> Msg:
        return (f"action.{self.action_id}", dict(self.params))

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        return []

    def preview(self, snap: SystemSnapshot) -> Preview:
        return Preview()

    def execute(self, env: Env) -> OpResult:
        return OpResult(True)

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        return Verification(VerifyStatus.NOT_APPLICABLE)

    # helpers
    def _need_queue(self, snap: SystemSnapshot) -> list[Msg]:
        if not snap.queue(self.queue):
            return [("pre.queue_missing", {"queue": self.queue})]
        return []

    def _need_spooler(self, snap: SystemSnapshot) -> list[Msg]:
        if snap.spooler is not None and not snap.spooler.running:
            return [("pre.spooler_stopped", {})]
        return []


class ResumeQueue(Action):
    action_id = "resume_queue"

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        out = self._need_queue(snap) + self._need_spooler(snap)
        q = snap.queue(self.queue)
        if q and q.state != QueueState.PAUSED:
            out.append(("pre.not_paused", {"queue": self.queue}))
        return out

    def preview(self, snap: SystemSnapshot) -> Preview:
        q = snap.queue(self.queue)
        return Preview(effects=[("preview.resume", {"queue": self.queue, "jobs": len(q.jobs) if q else 0})],
                       rollback=("rollback.pause_again", {}))

    def execute(self, env: Env) -> OpResult:
        return env.adapter.resume_queue(self.queue)

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        q = env.adapter.snapshot().queue(self.queue)
        if q is None:
            return Verification(VerifyStatus.FAILED, [("verify.queue_gone", {"queue": self.queue})])
        if q.state == QueueState.PAUSED or "paused" in {f.lower() for f in q.status_flags}:
            return Verification(VerifyStatus.FAILED, [("verify.still_paused", {"queue": self.queue})])
        if q.state in (QueueState.READY, QueueState.PRINTING):
            return Verification(VerifyStatus.VERIFIED, [("verify.queue_state", {"state": q.state.value})])
        return Verification(VerifyStatus.PARTIAL, [("verify.resumed_but", {"state": q.state.value})])


class SetOnline(Action):
    action_id = "set_online"

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        out = self._need_queue(snap)
        q = snap.queue(self.queue)
        if q and not q.work_offline:
            out.append(("pre.not_offline_mode", {"queue": self.queue}))
        return out

    def preview(self, snap: SystemSnapshot) -> Preview:
        return Preview(effects=[("preview.set_online", {"queue": self.queue})],
                       rollback=("rollback.offline_again", {}))

    def execute(self, env: Env) -> OpResult:
        return env.adapter.set_online(self.queue)

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        q = env.adapter.snapshot().queue(self.queue)
        if q is None:
            return Verification(VerifyStatus.FAILED, [("verify.queue_gone", {"queue": self.queue})])
        if q.work_offline:
            return Verification(VerifyStatus.FAILED, [("verify.still_offline_mode", {})])
        if q.state in (QueueState.READY, QueueState.PRINTING):
            return Verification(VerifyStatus.VERIFIED, [("verify.queue_state", {"state": q.state.value})])
        return Verification(VerifyStatus.PARTIAL, [("verify.online_but", {"state": q.state.value})])


class SetDefault(Action):
    action_id = "set_default"

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        out = self._need_queue(snap) + self._need_spooler(snap)
        q = snap.queue(self.queue)
        if q and q.is_default:
            out.append(("pre.already_default", {"queue": self.queue}))
        return out

    def preview(self, snap: SystemSnapshot) -> Preview:
        prev = snap.default_queue
        return Preview(effects=[("preview.set_default", {"queue": self.queue})],
                       warnings=[("warn.windows_manages_default", {})],
                       rollback=("rollback.set_default_back", {"queue": prev.name if prev else "—"}),
                       details={"previous_default": prev.name if prev else None})

    def execute(self, env: Env) -> OpResult:
        return env.adapter.set_default(self.queue)

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        d = env.adapter.snapshot().default_queue
        if d and d.name == self.queue:
            return Verification(VerifyStatus.VERIFIED, [("verify.default_is", {"queue": self.queue})])
        return Verification(VerifyStatus.FAILED, [("verify.default_is", {"queue": d.name if d else "—"})])


class RemoveJob(Action):
    action_id = "remove_job"
    risk = Risk.MEDIUM
    data_loss = True
    reversible = False

    @property
    def job_id(self) -> int:
        return int(self.params.get("job_id", "0"))

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        out = self._need_queue(snap) + self._need_spooler(snap)
        q = snap.queue(self.queue)
        if q and not any(j.job_id == self.job_id for j in q.jobs):
            out.append(("pre.job_missing", {"job": self.job_id}))
        return out

    def preview(self, snap: SystemSnapshot) -> Preview:
        q = snap.queue(self.queue)
        job = next((j for j in q.jobs if j.job_id == self.job_id), None) if q else None
        return Preview(
            effects=[("preview.remove_job", {"job": self.job_id, "queue": self.queue,
                                             "document": job.document if job else "?"})],
            warnings=[("warn.job_lost", {})],
            details={"job_id": self.job_id, "status": ", ".join(job.status) if job else ""})

    def execute(self, env: Env) -> OpResult:
        return env.adapter.remove_job(self.queue, self.job_id)

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        jobs = env.adapter.list_jobs(self.queue)
        if any(j.job_id == self.job_id for j in jobs):
            return Verification(VerifyStatus.FAILED, [("verify.job_still_there", {"job": self.job_id})])
        return Verification(VerifyStatus.VERIFIED, [("verify.job_removed", {"job": self.job_id,
                                                                              "remaining": len(jobs)})])


class ClearQueue(Action):
    action_id = "clear_queue"
    risk = Risk.HIGH
    data_loss = True
    reversible = False

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        out = self._need_queue(snap) + self._need_spooler(snap)
        q = snap.queue(self.queue)
        if q and not q.jobs:
            out.append(("pre.queue_empty", {"queue": self.queue}))
        return out

    def preview(self, snap: SystemSnapshot) -> Preview:
        q = snap.queue(self.queue)
        jobs = q.jobs if q else []
        return Preview(
            effects=[("preview.clear_queue", {"queue": self.queue, "count": len(jobs)})],
            warnings=[("warn.all_jobs_lost", {"count": len(jobs)})],
            details={"job_ids": [j.job_id for j in jobs], "documents": [j.document for j in jobs]})

    def execute(self, env: Env) -> OpResult:
        return env.adapter.clear_queue(self.queue)

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        bq = before.queue(self.queue)
        old = {j.job_id for j in bq.jobs} if bq else set()
        jobs = env.adapter.list_jobs(self.queue)
        left = [j for j in jobs if j.job_id in old]
        if left:
            return Verification(VerifyStatus.FAILED, [("verify.jobs_left", {"count": len(left)})])
        if jobs:
            return Verification(VerifyStatus.PARTIAL, [("verify.new_jobs_arrived", {"count": len(jobs)})])
        return Verification(VerifyStatus.VERIFIED, [("verify.queue_empty", {"queue": self.queue})])


class RestartSpooler(Action):
    action_id = "restart_spooler"
    risk = Risk.MEDIUM
    requires_admin = True
    timeout_s = 150.0

    def preview(self, snap: SystemSnapshot) -> Preview:
        jobs = sum(len(q.jobs) for q in snap.queues)
        printing = sum(1 for q in snap.queues for j in q.jobs if any(s.lower() == "printing" for s in j.status))
        warnings: list[Msg] = [("warn.admin_prompt", {})]
        if printing:
            warnings.append(("warn.printing_interrupted", {"count": printing}))
        return Preview(effects=[("preview.restart_spooler", {"jobs": jobs})], warnings=warnings,
                       details={"queued_jobs": jobs, "printing_jobs": printing})

    def execute(self, env: Env) -> OpResult:
        return env.adapter.restart_spooler()

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        snap = env.adapter.snapshot()
        if snap.spooler and snap.spooler.running:
            return Verification(VerifyStatus.VERIFIED, [("verify.spooler_running", {})])
        return Verification(VerifyStatus.FAILED, [("verify.spooler_state", {"state": snap.spooler.state
                                                                             if snap.spooler else "?"})])


class PrintTestPage(Action):
    action_id = "test_page"

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        out = self._need_queue(snap) + self._need_spooler(snap)
        q = snap.queue(self.queue)
        if q and q.is_virtual:
            out.append(("pre.virtual_queue", {"queue": self.queue}))
        return out

    def preview(self, snap: SystemSnapshot) -> Preview:
        return Preview(effects=[("preview.test_page", {"queue": self.queue})], warnings=[("warn.uses_paper", {})])

    def execute(self, env: Env) -> OpResult:
        return env.adapter.print_test_page(self.queue)

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        q = env.adapter.snapshot().queue(self.queue)
        obs: list[Msg] = [("verify.test_page_submitted", {})]
        if q and q.state not in (QueueState.READY, QueueState.PRINTING):
            obs.append(("verify.queue_state", {"state": q.state.value}))
            return Verification(VerifyStatus.PARTIAL, obs)
        return Verification(VerifyStatus.NEEDS_USER, obs)


class Rediscover(Action):
    action_id = "rediscover"
    kind = Kind.READ
    risk = Risk.NONE

    def _host(self, snap: SystemSnapshot) -> str | None:
        if self.params.get("host"):
            return self.params["host"]
        q = snap.queue(self.queue)
        return q.device_host if q else None

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        if not self._host(snap):
            return [("pre.no_host", {"queue": self.queue})]
        return []

    def preview(self, snap: SystemSnapshot) -> Preview:
        host = self._host(snap) or "?"
        return Preview(effects=[("preview.rediscover", {"host": host})], details={"host": host})

    def execute(self, env: Env) -> OpResult:
        return OpResult(True)

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        host = self._host(before)
        if not host:
            return Verification(VerifyStatus.FAILED, [("pre.no_host", {"queue": self.queue})])
        q = before.queue(self.queue)
        res = probe_host(env.net, host, (q.device_port,) if q and q.device_port else ())
        verdict = interpret(res)
        obs: list[Msg] = [("verify.probe_verdict", {"host": host, "verdict": verdict})]
        if verdict in ("ipp_ok", "printing_port_open"):
            return Verification(VerifyStatus.VERIFIED, obs)
        return Verification(VerifyStatus.FAILED, obs)


class AddIppPrinter(Action):
    action_id = "add_ipp_printer"
    risk = Risk.MEDIUM
    timeout_s = 200.0

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        out: list[Msg] = self._need_spooler(snap)
        try:
            validate_ipp_url(self.params.get("url", ""))
            normalize_host(self.params.get("host", "x"))
        except ValidationError:
            out.append(("pre.invalid_url", {}))
        name = self.params.get("name", "")
        if not name.strip():
            out.append(("pre.invalid_name", {}))
        elif snap.queue(name):
            out.append(("pre.name_exists", {"name": name}))
        return out

    def preview(self, snap: SystemSnapshot) -> Preview:
        return Preview(effects=[("preview.add_ipp", {"name": self.params.get("name", ""),
                                                     "url": self.params.get("url", "")})],
                       warnings=[("warn.driver_from_windows", {})],
                       rollback=("rollback.remove_queue", {"name": self.params.get("name", "")}))

    def execute(self, env: Env) -> OpResult:
        host = self.params.get("host", "")
        try:
            env.net.ipp_status(host, int(self.params.get("port", "631")), self.params.get("url", "").startswith(
                ("ipps", "https")), 5.0)
        except (IppError, OSError) as exc:
            return OpResult(False, "failed", f"device did not answer IPP before installation: {exc}")
        return env.adapter.add_ipp_printer(self.params["name"], self.params["url"])

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        snap = env.adapter.snapshot()
        q = snap.queue(self.params.get("name", ""))
        if q is None:
            return Verification(VerifyStatus.FAILED, [("verify.queue_not_created", {})])
        obs: list[Msg] = [("verify.queue_created", {"name": q.name, "state": q.state.value})]
        if q.connection not in (ConnectionType.IPP, ConnectionType.UNKNOWN, ConnectionType.WSD):
            obs.append(("verify.unexpected_connection", {"conn": q.connection.value}))
        return Verification(VerifyStatus.VERIFIED if q.state in (QueueState.READY, QueueState.PRINTING)
                            else VerifyStatus.PARTIAL, obs)


class OpenSupportUrl(Action):
    action_id = "open_support_url"
    kind = Kind.EXTERNAL

    def preconditions(self, snap: SystemSnapshot) -> list[Msg]:
        return [] if is_allowed_url(self.params.get("url", "")) else [("pre.url_not_allowed", {})]

    def preview(self, snap: SystemSnapshot) -> Preview:
        return Preview(effects=[("preview.open_url", {"url": self.params.get("url", "")})],
                       warnings=[("warn.external_site", {})])

    def execute(self, env: Env) -> OpResult:
        return env.adapter.open_url(self.params.get("url", ""))


class OpenPrinterSettings(Action):
    action_id = "open_printer_settings"
    kind = Kind.EXTERNAL

    def preview(self, snap: SystemSnapshot) -> Preview:
        return Preview(effects=[("preview.open_settings", {})])

    def execute(self, env: Env) -> OpResult:
        return env.adapter.open_printer_settings()


GUIDES = {
    "paper_jam": ["guide.power_off", "guide.open_covers", "guide.remove_paper_slowly", "guide.check_scraps",
                  "guide.close_covers", "guide.power_on"],
    "media-jam": ["guide.power_off", "guide.open_covers", "guide.remove_paper_slowly", "guide.check_scraps",
                  "guide.close_covers", "guide.power_on"],
    "paper_out": ["guide.load_paper", "guide.check_tray_guides"],
    "no_paper": ["guide.load_paper", "guide.check_tray_guides"],
    "media-empty": ["guide.load_paper", "guide.check_tray_guides"],
    "media-needed": ["guide.load_paper", "guide.check_tray_guides"],
    "door_open": ["guide.close_all_doors"],
    "door-open": ["guide.close_all_doors"],
    "cover-open": ["guide.close_all_doors"],
    "replace_supply": ["guide.check_display_supply", "guide.replace_supply", "guide.remove_tape", "guide.reseat"],
    "no_toner": ["guide.check_display_supply", "guide.replace_supply", "guide.reseat"],
    "toner-empty": ["guide.check_display_supply", "guide.replace_supply", "guide.reseat"],
    "blank_pages": ["guide.check_display_supply", "guide.remove_tape", "guide.reseat", "guide.run_cleaning"],
    "attention": ["guide.look_display", "guide.load_paper", "guide.close_all_doors", "guide.check_display_supply",
                  "guide.power_cycle"],
    "output_bin_full": ["guide.empty_output"],
    "output-area-full": ["guide.empty_output"],
    "input-tray-missing": ["guide.insert_tray"],
}


class GuidedPhysical(Action):
    action_id = "guided_physical"
    kind = Kind.GUIDED
    risk = Risk.NONE

    def preview(self, snap: SystemSnapshot) -> Preview:
        cond = self.params.get("condition", "attention")
        steps = GUIDES.get(cond, GUIDES["attention"])
        return Preview(effects=[("preview.guided", {"condition": cond})], steps=[(s, {}) for s in steps])

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        snap = env.adapter.snapshot()
        q = snap.queue(self.queue)
        obs: list[Msg] = []
        status = VerifyStatus.NEEDS_USER
        if q is not None:
            obs.append(("verify.queue_state", {"state": q.state.value}))
            bad = q.state in (QueueState.ERROR, QueueState.USER_INTERVENTION, QueueState.OFFLINE)
            status = VerifyStatus.PARTIAL if bad else VerifyStatus.VERIFIED
            if q.device_host:
                try:
                    res = probe_host(env.net, q.device_host, ())
                except ValidationError:
                    res = None
                if res and res.ipp:
                    reasons = [r for r in res.ipp.state_reasons if r != "none"]
                    obs.append(("verify.device_reasons", {"reasons": ", ".join(reasons) or "none"}))
                    if res.ipp.state != "stopped" and not bad:
                        status = VerifyStatus.VERIFIED
                    elif res.ipp.state == "stopped":
                        status = VerifyStatus.PARTIAL
        return Verification(status, obs)


class GuidedMaintenance(Action):
    action_id = "guided_maintenance"
    kind = Kind.GUIDED
    risk = Risk.NONE

    def preview(self, snap: SystemSnapshot) -> Preview:
        proc = self.params.get("procedure", "cleaning")
        return Preview(effects=[("preview.maintenance", {"procedure": proc})],
                       steps=[("guide.vendor_maintenance", {}), ("guide.nozzle_check", {}),
                              ("guide.max_two_cleanings", {}), ("guide.print_test_after", {})],
                       details={"procedure": proc})

    def verify(self, env: Env, before: SystemSnapshot) -> Verification:
        return Verification(VerifyStatus.NEEDS_USER, [("verify.check_print_quality", {})])


class OpenNetworkAssistant(Action):
    action_id = "open_network_assistant"
    kind = Kind.NAVIGATE
    risk = Risk.NONE


REGISTRY: dict[str, type[Action]] = {cls.action_id: cls for cls in (
    ResumeQueue, SetOnline, SetDefault, RemoveJob, ClearQueue, RestartSpooler, PrintTestPage, Rediscover,
    AddIppPrinter, OpenSupportUrl, OpenPrinterSettings, GuidedPhysical, GuidedMaintenance, OpenNetworkAssistant,
)}


def create(action_id: str, params: dict[str, str]) -> Action:
    cls = REGISTRY.get(action_id)
    if cls is None:
        raise KeyError(f"unknown action {action_id!r}")
    return cls(**params)
