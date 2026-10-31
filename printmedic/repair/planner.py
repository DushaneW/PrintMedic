"""Builds an ordered repair plan from ranked findings."""

from __future__ import annotations

from dataclasses import dataclass, field

from printmedic.core.models import SystemSnapshot
from printmedic.diagnostics.findings import Finding
from printmedic.repair.actions import Action, Kind, Msg, Preview, Risk, create

_RISK_ORDER = {Risk.NONE: 0, Risk.LOW: 1, Risk.MEDIUM: 2, Risk.HIGH: 3}
_KIND_ORDER = {Kind.SYSTEM: 0, Kind.READ: 1, Kind.GUIDED: 2, Kind.EXTERNAL: 3, Kind.NAVIGATE: 4}


@dataclass
class PlanItem:
    action: Action
    finding_codes: list[str]
    preview: Preview
    blocked_by: list[Msg] = field(default_factory=list)
    alternative_of: str | None = None   # e.g. clear_queue is an alternative to removing single jobs

    @property
    def runnable(self) -> bool:
        return not self.blocked_by


def build_plan(findings: list[Finding], snapshot: SystemSnapshot) -> list[PlanItem]:
    items: dict[str, PlanItem] = {}
    order: dict[str, tuple[int, ...]] = {}
    for rank_idx, f in enumerate(findings):
        for ref in f.actions:
            try:
                action = create(ref.action_id, ref.param_dict)
            except KeyError:
                continue
            key = action.key
            if key in items:
                if f.code not in items[key].finding_codes:
                    items[key].finding_codes.append(f.code)
                continue
            items[key] = PlanItem(action, [f.code], action.preview(snapshot), action.preconditions(snapshot))
            spooler_first = 0 if action.action_id == "restart_spooler" else 1
            order[key] = (spooler_first, rank_idx, _KIND_ORDER[action.kind], _RISK_ORDER[action.risk])
    # clear_queue is offered as the riskier alternative to individual job removal
    for item in items.values():
        if item.action.action_id == "clear_queue" and any(
                i.action.action_id == "remove_job" and i.action.queue == item.action.queue for i in items.values()):
            item.alternative_of = "remove_job"
    return [items[k] for k in sorted(items, key=lambda k: order[k])]
