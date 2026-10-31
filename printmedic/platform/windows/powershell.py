"""Allowlisted PowerShell execution.

Security model
--------------
* Every script PrintMedic can run is a constant in `SCRIPTS`. Nothing is ever composed from
  user input, device metadata, or model output.
* Parameters reach the script only through environment variables named ``PM_*``; the script
  reads them as data (``$env:PM_QUEUE``). Quotes, semicolons or ``$()`` inside a printer name
  therefore cannot change what is executed.
* Scripts are passed with ``-EncodedCommand`` (no shell, no quoting layer) and a hard timeout.
* Output is a single JSON document which is parsed defensively.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

_PARAM_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")

_PRELUDE = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
"""

# Wraps a mutating body: the body must set $rv (Win32 method return value, 0 = success)
# or leave it $null. Errors are reported as JSON with language-independent fields.
_MUTATION_WRAPPER = r"""
$rv = $null
try {
%s
  [ordered]@{ ok = $true; rv = $rv } | ConvertTo-Json -Compress
} catch {
  $e = $_.Exception
  $native = 0
  $inner = $e
  while ($inner) {
    if ($inner -is [System.ComponentModel.Win32Exception]) { $native = $inner.NativeErrorCode; break }
    if ($inner.PSObject.Properties['NativeErrorCode'] -and $inner.NativeErrorCode -is [int]) {
      $native = [int]$inner.NativeErrorCode
    }
    $inner = $inner.InnerException
  }
  [ordered]@{
    ok = $false; message = [string]$e.Message; hresult = [int64]$e.HResult; native = $native
    category = [string]$_.CategoryInfo.Category; fqid = [string]$_.FullyQualifiedErrorId
  } | ConvertTo-Json -Compress
}
"""

_FIND_PRINTER = r"""
  $p = Get-CimInstance -ClassName Win32_Printer | Where-Object { $_.Name -eq $env:PM_QUEUE } | Select-Object -First 1
  if (-not $p) { throw [System.Management.Automation.ItemNotFoundException]::new('PM_NOT_FOUND') }
"""

INVENTORY = r"""
$errs = New-Object System.Collections.Generic.List[string]
$out = [ordered]@{}

$cim = @{}
try { Get-CimInstance -ClassName Win32_Printer | ForEach-Object { $cim[$_.Name] = $_ } }
catch { $errs.Add('Win32_Printer: ' + $_.Exception.Message) }

$queues = @()
try {
  $queues = @(Get-Printer | ForEach-Object {
    $p = $_
    $c = $cim[$p.Name]
    $jobs = @()
    try {
      $jobs = @(Get-PrintJob -PrinterName $p.Name | ForEach-Object {
        $j = $_
        [ordered]@{
          id = [int]$j.Id; document = [string]$j.DocumentName; status = [string]$j.JobStatus
          submitted = $(if ($j.SubmittedTime) { $j.SubmittedTime.ToUniversalTime().ToString('o') } else { $null })
          pages = $j.TotalPages; size = $j.Size; owner = [string]$j.UserName
        }
      })
    } catch { $errs.Add('Get-PrintJob: ' + $_.Exception.Message) }
    [ordered]@{
      name = [string]$p.Name; driver = [string]$p.DriverName; port = [string]$p.PortName
      status = [string]$p.PrinterStatus; type = [string]$p.Type; shared = [bool]$p.Shared
      location = [string]$p.Location; comment = [string]$p.Comment
      isDefault = $(if ($c) { [bool]$c.Default } else { $false })
      workOffline = $(if ($c) { [bool]$c.WorkOffline } else { $false })
      detectedError = $(if ($c) { [int]$c.DetectedErrorState } else { $null })
      cimLocation = $(if ($c) { [string]$c.Location } else { '' })
      jobs = $jobs
    }
  })
} catch { $errs.Add('Get-Printer: ' + $_.Exception.Message) }
$out.queues = $queues

$ports = @()
try {
  $ports = @(Get-PrinterPort | ForEach-Object {
    [ordered]@{
      name = [string]$_.Name; monitor = [string]$_.PortMonitor; description = [string]$_.Description
      host = [string]$_.PrinterHostAddress; portNumber = $_.PortNumber; protocol = [string]$_.Protocol
    }
  })
} catch { $errs.Add('Get-PrinterPort: ' + $_.Exception.Message) }
$out.ports = $ports

$drivers = @()
try {
  $drivers = @(Get-PrinterDriver | ForEach-Object {
    [ordered]@{
      name = [string]$_.Name; manufacturer = [string]$_.Manufacturer; version = [string]$_.DriverVersion
      env = [string]$_.PrinterEnvironment; major = [int]$_.MajorVersion; inf = [string]$_.InfPath
      provider = [string]$_.Provider
    }
  })
} catch { $errs.Add('Get-PrinterDriver: ' + $_.Exception.Message) }
$out.drivers = $drivers

$usb = @()
try {
  $usb = @(Get-CimInstance -ClassName Win32_PnPEntity | Where-Object {
      $_.DeviceID -like 'USBPRINT\*' -or
      ($_.DeviceID -like 'USB\*' -and (($_.PNPClass -eq 'Printer') -or ($_.CompatibleID -match '^USB\\Class_07')))
    } | ForEach-Object {
    [ordered]@{
      id = [string]$_.DeviceID; name = [string]$_.Name; manufacturer = [string]$_.Manufacturer
      status = [string]$_.Status; problem = [int]$_.ConfigManagerErrorCode; pnpClass = [string]$_.PNPClass
      present = $(if ($null -ne $_.Present) { [bool]$_.Present } else { $true })
      hardwareIds = @($_.HardwareID) + @($_.CompatibleID)
    }
  })
} catch { $errs.Add('Win32_PnPEntity: ' + $_.Exception.Message) }
$out.usb = $usb

try {
  $s = Get-CimInstance -ClassName Win32_Service -Filter "Name='Spooler'"
  $out.spooler = [ordered]@{ state = [string]$s.State; startMode = [string]$s.StartMode }
} catch { $errs.Add('Spooler: ' + $_.Exception.Message); $out.spooler = $null }

$ifs = @()
try {
  $ifs = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
      $_.IPAddress -ne '127.0.0.1' -and $_.AddressState -eq 'Preferred' } | ForEach-Object {
    $a = $_
    $ad = Get-NetAdapter -InterfaceIndex $a.InterfaceIndex -ErrorAction SilentlyContinue
    [ordered]@{
      name = [string]$a.InterfaceAlias; address = [string]$a.IPAddress; prefix = [int]$a.PrefixLength
      media = $(if ($ad) { [string]$ad.PhysicalMediaType } else { '' })
      up = $(if ($ad) { [string]$ad.Status -eq 'Up' } else { $true })
    }
  })
} catch { $errs.Add('Get-NetIPAddress: ' + $_.Exception.Message) }
$out.interfaces = $ifs

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$adminRole = [Security.Principal.WindowsBuiltInRole]::Administrator
$out.isAdmin = ([Security.Principal.WindowsPrincipal]$id).IsInRole($adminRole)
$out.errors = @($errs)
$out | ConvertTo-Json -Depth 6 -Compress
"""

JOBS = r"""
$jobs = @(Get-PrintJob -PrinterName $env:PM_QUEUE | ForEach-Object {
  $j = $_
  [ordered]@{
    id = [int]$j.Id; document = [string]$j.DocumentName; status = [string]$j.JobStatus
    submitted = $(if ($j.SubmittedTime) { $j.SubmittedTime.ToUniversalTime().ToString('o') } else { $null })
    pages = $j.TotalPages; size = $j.Size; owner = [string]$j.UserName
  }
})
ConvertTo-Json -InputObject $jobs -Depth 4 -Compress
"""

SCRIPTS: dict[str, tuple[str, frozenset[str]]] = {
    # id: (body, allowed parameter names)
    "inventory": (INVENTORY, frozenset()),
    "jobs": (JOBS, frozenset({"QUEUE"})),
    "resume_queue": (_MUTATION_WRAPPER % (_FIND_PRINTER + r"""
  $rv = [int](Invoke-CimMethod -InputObject $p -MethodName Resume).ReturnValue
"""), frozenset({"QUEUE"})),
    "set_online": (_MUTATION_WRAPPER % (_FIND_PRINTER + r"""
  Set-CimInstance -InputObject $p -Property @{ WorkOffline = $false }
  $rv = 0
"""), frozenset({"QUEUE"})),
    "set_default": (_MUTATION_WRAPPER % (_FIND_PRINTER + r"""
  $rv = [int](Invoke-CimMethod -InputObject $p -MethodName SetDefaultPrinter).ReturnValue
"""), frozenset({"QUEUE"})),
    "print_test_page": (_MUTATION_WRAPPER % (_FIND_PRINTER + r"""
  $rv = [int](Invoke-CimMethod -InputObject $p -MethodName PrintTestPage).ReturnValue
"""), frozenset({"QUEUE"})),
    "remove_job": (_MUTATION_WRAPPER % r"""
  Remove-PrintJob -PrinterName $env:PM_QUEUE -ID ([int]$env:PM_JOB)
  $rv = 0
""", frozenset({"QUEUE", "JOB"})),
    "clear_queue": (_MUTATION_WRAPPER % r"""
  Get-PrintJob -PrinterName $env:PM_QUEUE | Remove-PrintJob
  $rv = 0
""", frozenset({"QUEUE"})),
    "add_ipp_printer": (_MUTATION_WRAPPER % r"""
  Add-Printer -Name $env:PM_NAME -IppURL $env:PM_URL
  $rv = 0
""", frozenset({"NAME", "URL"})),
    "restart_spooler": (_MUTATION_WRAPPER % r"""
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $admin = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if ($admin) {
    Restart-Service -Name Spooler -Force
  } else {
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $proc = Start-Process -FilePath $ps -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
      -ArgumentList '-NoProfile', '-NonInteractive', '-Command', 'Restart-Service -Name Spooler -Force'
    if ($proc.ExitCode -ne 0) { throw ('elevated restart exited with ' + $proc.ExitCode) }
  }
  $rv = 0
""", frozenset()),
}


class ScriptError(RuntimeError):
    pass


@dataclass
class ScriptOutput:
    data: Any
    stderr: str
    returncode: int


def powershell_path() -> str:
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")  # Windows env lookups are case-insensitive
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def build_command(script_id: str, params: dict[str, str] | None = None) -> tuple[list[str], dict[str, str]]:
    """Return (argv, env) for an allowlisted script. Raises KeyError/ValueError on misuse."""
    body, allowed = SCRIPTS[script_id]
    params = params or {}
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PM_")}
    for key, value in params.items():
        if not _PARAM_NAME.match(key) or key not in allowed:
            raise ValueError(f"parameter {key!r} not allowed for {script_id}")
        if not isinstance(value, str) or "\x00" in value or len(value) > 2048:
            raise ValueError(f"invalid value for {key}")
        env["PM_" + key] = value
    encoded = base64.b64encode((_PRELUDE + body).encode("utf-16-le")).decode("ascii")
    argv = [powershell_path(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    return argv, env


def run_script(script_id: str, params: dict[str, str] | None = None, timeout: float = 60.0) -> ScriptOutput:
    argv, env = build_command(script_id, params)
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            argv, env=env, capture_output=True, timeout=timeout, check=False, creationflags=flags,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScriptError(f"timeout after {timeout:.0f}s") from exc
    except OSError as exc:
        raise ScriptError(f"cannot start PowerShell: {exc}") from exc
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    return ScriptOutput(parse_json_output(stdout), stderr.strip(), proc.returncode)


def parse_json_output(stdout: str) -> Any:
    text = stdout.strip().lstrip("\ufeff")
    if not text:
        return None
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScriptError("unparseable PowerShell output") from exc
