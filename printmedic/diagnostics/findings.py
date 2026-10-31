"""Finding and evidence types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from printmedic.core.models import Certainty, Severity, StatusOrigin


@dataclass(frozen=True)
class Evidence:
    fact: str            # concise, language-neutral statement of what was observed
    source: str          # which interface produced it
    origin: StatusOrigin


@dataclass(frozen=True)
class ActionRef:
    action_id: str
    params: tuple[tuple[str, str], ...] = ()

    @staticmethod
    def of(action_id: str, **params: Any) -> ActionRef:
        return ActionRef(action_id, tuple(sorted((k, str(v)) for k, v in params.items())))

    @property
    def param_dict(self) -> dict[str, str]:
        return dict(self.params)


@dataclass
class Finding:
    code: str
    severity: Severity
    certainty: Certainty
    target: str | None                       # queue/device/host the finding is about
    evidence: list[Evidence]
    params: dict[str, str] = field(default_factory=dict)  # values substituted into catalog texts
    actions: list[ActionRef] = field(default_factory=list)
    check_id: str = ""

    @property
    def sort_key(self) -> tuple[int, int, int, str]:
        return (-self.certainty.rank, -int(self.severity), -len(self.evidence), self.code)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.name
        d["certainty"] = self.certainty.value
        d["evidence"] = [{"fact": e.fact, "source": e.source, "origin": e.origin.value} for e in self.evidence]
        d["actions"] = [{"action_id": a.action_id, "params": a.param_dict} for a in self.actions]
        return d


def rank(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: f.sort_key)
