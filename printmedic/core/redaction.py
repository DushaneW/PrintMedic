"""Redaction for exported reports. Enabled by default; disabling it is an explicit user choice."""

from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass, field
from typing import Any

_IPV4 = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b|\bfe80::[0-9A-Fa-f:]+\b", re.I)
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_USER_PATH = re.compile(r"(?i)([A-Z]:\\Users\\)([^\\/\s]+)|(/home/|/Users/)([^/\s]+)")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_SERIAL_KV = re.compile(r"(?i)(serial(?:[ _-]?(?:number|no))?\s*[:=]\s*)([A-Za-z0-9-]{4,})")

# Keys whose values are replaced wholesale when exporting structured data.
SENSITIVE_KEYS = {
    "document", "document_name", "job_title", "owner", "user", "username",
    "serial", "serial_number", "ssid", "mac", "uuid", "location", "comment",
}


@dataclass
class Redactor:
    enabled: bool = True
    extra_terms: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        terms: set[str] = set(self.extra_terms)
        for getter in (getpass.getuser, lambda: os.environ.get("USERNAME", ""),
                       lambda: os.environ.get("COMPUTERNAME", "")):
            try:
                v = getter()
            except Exception:  # noqa: S112 - best effort only
                continue
            if v and len(v) >= 2:
                terms.add(v)
        self._terms = sorted(terms, key=len, reverse=True)

    def text(self, value: str) -> str:
        if not self.enabled or not value:
            return value
        out = _USER_PATH.sub(lambda m: (m.group(1) or m.group(3)) + "<user>", value)
        out = _MAC.sub("<mac>", out)
        out = _UUID.sub("<uuid>", out)
        out = _IPV4.sub("<ip>", out)
        out = _IPV6.sub("<ip6>", out)
        out = _SERIAL_KV.sub(lambda m: m.group(1) + "<serial>", out)
        for term in self._terms:
            out = re.sub(re.escape(term), "<redacted>", out, flags=re.I)
        return out

    def data(self, value: Any, key: str | None = None) -> Any:
        """Recursively redact a JSON-like structure."""
        if not self.enabled:
            return value
        if key and key.lower() in SENSITIVE_KEYS and value not in (None, "", [], {}):
            return "<redacted>"
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.data(v, str(k)) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.data(v) for v in value]
        return value
