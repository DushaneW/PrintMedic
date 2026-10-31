"""DNS resolution, printing-port TCP checks and combined host probes.

Only the ports in `PRINTING_PORTS` (plus a port explicitly taken from an installed queue's
configuration) are ever contacted, each with a short timeout. No banner grabbing, no sweeps
of arbitrary ports.
"""

from __future__ import annotations

import errno
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from printmedic.core.cancel import CancelToken
from printmedic.core.models import PortState, ProbeResult
from printmedic.core.validation import PRINTING_PORTS, normalize_host, validate_port
from printmedic.network import ipp


class NetworkProbe(Protocol):
    simulated: bool

    def resolve(self, host: str) -> tuple[list[str], str | None]: ...
    def tcp_check(self, address: str, port: int, timeout: float) -> PortState: ...
    def ipp_status(self, host: str, port: int, tls: bool, timeout: float) -> ipp.IppStatus: ...


class RealNetworkProbe:
    simulated = False

    def resolve(self, host: str) -> tuple[list[str], str | None]:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            return [], f"{exc.strerror or exc}"
        seen: list[str] = []
        for info in infos:
            addr = str(info[4][0])
            if addr not in seen:
                seen.append(addr)
        return seen, None

    def tcp_check(self, address: str, port: int, timeout: float) -> PortState:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            code = s.connect_ex((address, port))
        except TimeoutError:
            return PortState.TIMEOUT
        except OSError:
            return PortState.ERROR
        finally:
            s.close()
        if code == 0:
            return PortState.OPEN
        if code in (errno.ECONNREFUSED, 10061):
            return PortState.REFUSED
        if code in (errno.ETIMEDOUT, 10060, errno.EAGAIN, errno.EWOULDBLOCK, 10035):
            return PortState.TIMEOUT
        if code in (errno.EHOSTUNREACH, errno.ENETUNREACH, 10065, 10051):
            return PortState.UNREACHABLE
        return PortState.ERROR

    def ipp_status(self, host: str, port: int, tls: bool, timeout: float) -> ipp.IppStatus:
        return ipp.fetch_status(host, port, tls=tls, timeout=timeout)


def probe_host(net: NetworkProbe, host: str, extra_ports: tuple[int, ...] = (), timeout: float = 2.0,
               cancel: CancelToken | None = None, with_ipp: bool = True) -> ProbeResult:
    """Resolve `host`, check the printing ports, and query IPP if 631/443 is open."""
    started = time.monotonic()
    host = normalize_host(host)
    result = ProbeResult(host=host)
    addresses, err = net.resolve(host)
    result.addresses = addresses
    result.dns_error = err
    if not addresses:
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result
    ports = list(dict.fromkeys([*PRINTING_PORTS, *(validate_port(p) for p in extra_ports)]))
    target = next((a for a in addresses if ":" not in a), addresses[0])
    with ThreadPoolExecutor(max_workers=len(ports)) as pool:
        futures = {p: pool.submit(net.tcp_check, target, p, timeout) for p in ports}
        for p, fut in futures.items():
            result.ports[p] = fut.result()
    if cancel:
        cancel.raise_if_cancelled()
    if with_ipp:
        attempts = []
        if result.ports.get(631) == PortState.OPEN:
            attempts.append((631, False))
        if result.ports.get(443) == PortState.OPEN:
            attempts.append((443, True))
        errors = []
        for port, tls in attempts:
            try:
                result.ipp = net.ipp_status(target, port, tls, timeout + 2)
                result.ipp_error = None
                break
            except ipp.IppError as exc:
                errors.append(f"{'ipps' if tls else 'ipp'}:{port} {exc}")
        if result.ipp is None and errors:
            result.ipp_error = "; ".join(errors)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result


def interpret(result: ProbeResult) -> str:
    """A short machine-readable verdict used by diagnostics and the Network Tools page."""
    if result.dns_error and not result.addresses:
        return "dns_failure"
    states = set(result.ports.values())
    if not states:
        return "not_probed"
    if result.ipp is not None:
        return "ipp_ok"
    if PortState.OPEN in states:
        return "printing_port_open"
    if PortState.REFUSED in states:
        return "host_up_no_printing_service"
    if states <= {PortState.UNREACHABLE, PortState.ERROR}:
        return "no_route"
    return "no_answer"
