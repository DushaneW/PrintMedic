# PrintMedic

Cross-vendor printer discovery, diagnostics and **safe, verified** repair for the desktop.

PrintMedic follows one workflow on every screen:
**discover → identify → connect → inspect → diagnose → plan → authorize → repair → verify → report.**

It reads the real Windows print subsystem (queues, jobs, ports, drivers, USB devices, the Print Spooler),
talks to printers over vendor-neutral standards (IPP / IPPS, mDNS/DNS-SD, WS-Discovery), explains every
finding with evidence, and changes nothing without a preview and your explicit authorization. After a
repair it re-reads the state and only reports success if the check confirms it.

> Status: **v0.1.0** — Windows 10/11 is fully implemented. Linux (CUPS) and macOS adapters are planned and
> not implemented yet. See [docs/FEATURES.md](docs/FEATURES.md)
> for the honest list of what is and isn't supported.

## Quick start (from source)

Requires Python **3.12+** on Windows 10/11.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m printmedic
```

No account, no telemetry, no internet access required. Settings and history live in
`%LOCALAPPDATA%\PrintMedic` (override with `PRINTMEDIC_DATA_DIR`).

### Read-only command line

```bash
printmedic-cli inventory
printmedic-cli probe 192.168.1.40
printmedic-cli diagnose --symptom blank_pages --export report.html   # redacted by default
printmedic-cli --lang pl diagnose
```

The CLI never performs repairs; those require the interactive consent flow. `diagnose` exits with code 1
when a confirmed high-severity fault exists (useful for scripts).

## Tests, lint, types

```bash
.venv\Scripts\python -m pytest -q        # unit, integration, security, end-to-end and offscreen UI tests
.venv\Scripts\python -m ruff check printmedic tests
.venv\Scripts\python -m mypy
```

`tests/test_windows_adapter.py::test_real_windows_inventory_is_readable` performs a **read-only** inventory of
the machine running the tests (skipped on non-Windows).

## Building a distributable

```bash
.venv\Scripts\python -m pip install -e ".[package]"
.venv\Scripts\pyinstaller --noconfirm packaging/printmedic.spec
```

Produces `dist/PrintMedic/PrintMedic.exe` (one-folder, unsigned). CI builds the same artifact on every push to `main`
([.github/workflows/ci.yml](.github/workflows/ci.yml)). Code signing and an installer are not set up yet.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layers, data flow, extension points, technology choice
- [docs/SECURITY_PRIVACY.md](docs/SECURITY_PRIVACY.md) — threat model, command execution, network bounds, redaction
- [docs/FEATURES.md](docs/FEATURES.md) — implemented vs. unsupported features, vendor support matrix, diagnostic codes

## License

MIT for PrintMedic. Third-party: Qt for Python / PySide6 (LGPL-3.0), python-zeroconf (LGPL-2.1).
