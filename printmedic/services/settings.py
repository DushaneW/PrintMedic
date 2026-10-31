"""User settings persisted as JSON in the per-user application data folder."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    override = os.environ.get("PRINTMEDIC_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "PrintMedic"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PrintMedic"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "printmedic"


@dataclass
class Settings:
    language: str = "en"                  # "en" | "pl"
    theme: str = "system"                 # "system" | "light" | "dark"
    discovery_mdns: bool = True
    discovery_wsd: bool = True
    discovery_subnet: bool = False        # opt-in: probes 631/9100 on the local /24 only
    discovery_duration: float = 6.0
    discovery_interface: str = ""         # interface address; "" = all
    redact_exports: bool = True
    verbosity: str = "normal"             # "normal" | "detailed"
    retention_days: int = 90
    notifications: bool = True
    first_run_done: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> Settings:
        if self.language not in ("en", "pl"):
            self.language = "en"
        if self.theme not in ("system", "light", "dark"):
            self.theme = "system"
        if self.verbosity not in ("normal", "detailed"):
            self.verbosity = "normal"
        self.discovery_duration = float(min(max(self.discovery_duration, 2.0), 20.0))
        self.retention_days = int(min(max(self.retention_days, 0), 3650))
        return self


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "settings.json"

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Settings().validate()
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in raw.items() if k in known}).validate()

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(settings.validate()), indent=2), encoding="utf-8")
        tmp.replace(self.path)
