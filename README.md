<div align="center">

# 🖨️ PrintMedic

**Cross-vendor printer discovery, diagnostics, and verified desktop repair.**

[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](#)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6.svg)](#)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB.svg)](#)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#license)
[![Offline](https://img.shields.io/badge/telemetry-none-success.svg)](#)

</div>

PrintMedic inspects the Windows print subsystem, communicates directly with network and local printers via open standards, and resolves spooler, driver, and queue faults without guesswork.

Every diagnostic session runs through a strict, auditable pipeline:

```text
Discover ➔ Identify ➔ Connect ➔ Inspect ➔ Diagnose ➔ Plan ➔ Authorize ➔ Repair ➔ Verify ➔ Report
```

## Key Features

* **Subsystem Inspection:** Reads native Windows print queues, active jobs, ports, drivers, USB bindings, and the Print Spooler service.
* **Vendor-Neutral Protocols:** Queries devices natively over IPP/IPPS, mDNS/DNS-SD, and WS-Discovery—no bloated vendor software required.
* **Consent-First Execution:** Previews every planned system change with supporting evidence and waits for explicit user authorization.
* **Verified Repairs:** Re-reads system state after applying fixes and only reports success when confirmed by post-repair checks.
* **Offline & Private:** Zero telemetry, no cloud accounts, and no internet access required. Configuration and logs stay in `%LOCALAPPDATA%\PrintMedic` (configurable via `PRINTMEDIC_DATA_DIR`).

> **Platform Support:** Windows 10 and 11 are fully supported in **v0.1.0**. Linux (CUPS) and macOS adapters are on the roadmap. See [docs/FEATURES.md](docs/FEATURES.md) for the complete support matrix.

## Quick Start

Requires **Python 3.12+** on Windows 10/11.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m printmedic
```

### Command-Line Interface (Read-Only)

For headless diagnostics, scripting, and fleet inspection, use `printmedic-cli`:

```powershell
# List queues, drivers, and ports
printmedic-cli inventory

# Query a network printer directly via IPP / mDNS / WS-Discovery
printmedic-cli probe 192.168.1.40

# Run targeted diagnostics and export a redacted HTML report
printmedic-cli diagnose --symptom blank_pages --export report.html

# Run diagnostics with Polish localization
printmedic-cli --lang pl diagnose
```

*Note: The CLI is strictly read-only. Active repairs require the interactive GUI consent flow. `diagnose` exits with code `1` when a high-severity fault is confirmed.*

## Development & Testing

Run the test suite, linter, and static type checker:

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check printmedic tests
.venv\Scripts\python -m mypy
```

The suite covers unit, integration, security, end-to-end, and offscreen UI tests. `tests/test_windows_adapter.py::test_real_windows_inventory_is_readable` executes a safe, read-only inspection of the host machine (automatically skipped on non-Windows environments).

## Building Executables

Package PrintMedic into a standalone Windows executable using PyInstaller:

```powershell
.venv\Scripts\python -m pip install -e ".[package]"
.venv\Scripts\pyinstaller --noconfirm packaging/printmedic.spec
```



## License

Released under the **MIT License**. 

Third-party runtime dependencies include Qt for Python / PySide6 (`LGPL-3.0`) and `python-zeroconf` (`LGPL-2.1`).
