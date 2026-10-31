"""Read-only command-line interface for scripting and support: inventory, probe, diagnose, export.

The CLI never performs repairs; those require the interactive consent flow in the desktop app.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from printmedic import __version__
from printmedic.app_core import AppCore
from printmedic.core.cancel import CancelToken
from printmedic.diagnostics.catalog import render
from printmedic.i18n import set_language, tr
from printmedic.network.probe import interpret
from printmedic.services import reports
from printmedic.services.settings import Settings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="printmedic-cli", description="PrintMedic read-only CLI")
    p.add_argument("--lang", choices=("en", "pl"), default="en")
    p.add_argument("--version", action="version", version=f"PrintMedic {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory", help="list printers, ports, drivers and USB devices")
    pr = sub.add_parser("probe", help="check printing ports and IPP on a host")
    pr.add_argument("host")
    dg = sub.add_parser("diagnose", help="run all diagnostic checks")
    dg.add_argument("--symptom", action="append", default=[])
    dg.add_argument("--export", type=Path, help="write a redacted report (.md/.html/.json)")
    dg.add_argument("--no-redact", action="store_true")
    args = p.parse_args(argv)
    set_language(args.lang)

    core = AppCore(settings=Settings(language=args.lang))
    if args.cmd == "inventory":
        devices = core.refresh()
        for d in devices:
            state = d.queue.state.value if d.queue else ("driver missing" if d.usb and d.usb.driver_missing else "-")
            print(f"{d.display_name:<48} {d.vendor_name:<10} {d.connection.value:<8} {state:<18} "
                  f"{d.host or ''}")
        return 0
    if args.cmd == "probe":
        res = core.probe(args.host)
        print(json.dumps({"host": res.host, "addresses": res.addresses, "dns_error": res.dns_error,
                          "ports": {str(k): v.value for k, v in res.ports.items()},
                          "verdict": interpret(res),
                          "ipp": None if not res.ipp else {"uri": res.ipp.uri, "state": res.ipp.state,
                                                           "reasons": res.ipp.state_reasons,
                                                           "model": res.ipp.make_and_model}}, indent=2))
        return 0
    session = core.diagnose(target=None, categories=None, symptoms=args.symptom, cancel=CancelToken())
    for f in session.findings:
        t = render(f.code, args.lang, f.params)
        print(f"[{f.certainty.value:<9}] [{f.severity.name:<8}] {f.code:<15} {t['title']}")
        for e in f.evidence:
            print(f"      - {e.fact}  ({e.source}, {e.origin.value})")
    if not session.findings:
        print(tr("diag.no_findings"))
    if args.export:
        from printmedic.services.history import session_to_dict
        rep = reports.build_report([session_to_dict(session)], [], lang=args.lang, tr=tr, redact=not args.no_redact)
        reports.export(rep, args.export, tr)
        print(f"report written to {args.export}")
    return 1 if any(f.certainty.value == "confirmed" and f.severity >= 3 for f in session.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
