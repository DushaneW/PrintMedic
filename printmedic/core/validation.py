"""Input validation for anything a user types or a device reports before it reaches an OS call."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_HOST_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
# Printer queue names may contain most printable characters (including parentheses and Polish
# letters) but never control characters. They are passed to PowerShell through environment
# variables, never through the command text, so quotes are harmless — we still bound length.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

PRINTING_PORTS: tuple[int, ...] = (631, 443, 9100, 515, 80)
"""The only TCP ports PrintMedic ever probes: IPP, IPPS/HTTPS, RAW, LPD, HTTP."""


class ValidationError(ValueError):
    pass


def normalize_host(value: str) -> str:
    """Validate a hostname or IP address entered by the user. Returns the normalized form."""
    v = (value or "").strip()
    if not v:
        raise ValidationError("empty")
    if len(v) > 253:
        raise ValidationError("too_long")
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        pass
    else:
        if ip.is_multicast or ip.is_unspecified:
            raise ValidationError("invalid_address")
        return str(ip)
    host = v.rstrip(".")
    labels = host.split(".")
    if not all(_HOST_LABEL.match(lbl) for lbl in labels):
        raise ValidationError("invalid_hostname")
    if all(lbl.isdigit() for lbl in labels):
        raise ValidationError("invalid_address")
    return host.lower()


def is_private_or_local(host: str) -> bool:
    """True for addresses on private/link-local ranges. Hostnames return True (resolved later)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return ip.is_private or ip.is_link_local or ip.is_loopback


def validate_queue_name(name: str) -> str:
    if not name or len(name) > 256 or _CONTROL.search(name):
        raise ValidationError("invalid_queue_name")
    return name


def validate_job_id(job_id: int) -> int:
    if not isinstance(job_id, int) or isinstance(job_id, bool) or not (0 < job_id < 2**31):
        raise ValidationError("invalid_job_id")
    return job_id


def validate_port(port: int) -> int:
    if not isinstance(port, int) or not (0 < port < 65536):
        raise ValidationError("invalid_port")
    return port


def validate_ipp_url(url: str) -> str:
    """Accept only ipp://, ipps://, http:// or https:// URLs whose host passes normalize_host."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("ipp", "ipps", "http", "https"):
        raise ValidationError("invalid_scheme")
    if not parts.hostname:
        raise ValidationError("invalid_hostname")
    normalize_host(parts.hostname)
    if parts.port is not None:
        validate_port(parts.port)
    if _CONTROL.search(url) or " " in url.strip():
        raise ValidationError("invalid_url")
    return url.strip()


def subnet_hosts(address: str, prefix_length: int, max_hosts: int = 254) -> list[str]:
    """Hosts of the local /24 (or smaller) network containing `address`, excluding it.

    Larger networks are deliberately clamped to the /24 around the interface address so a
    discovery sweep can never grow beyond a few hundred hosts.
    """
    ip = ipaddress.ip_address(address)
    if ip.version != 4:
        raise ValidationError("ipv4_only")
    if not ip.is_private:
        raise ValidationError("not_private")
    prefix = max(prefix_length, 24)
    net = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
    hosts = [str(h) for h in net.hosts() if h != ip]
    return hosts[:max_hosts]
