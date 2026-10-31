# PyInstaller spec — build with:  pyinstaller --noconfirm packaging/printmedic.spec
# Produces dist/PrintMedic/PrintMedic.exe (one-folder build; no installer, no auto-update, unsigned).
# ruff: noqa
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "printmedic", "ui", "assets"), os.path.join("printmedic", "ui", "assets"))],
    hiddenimports=["zeroconf._utils.ipaddress", "zeroconf._handlers.answers"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PrintMedic", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PrintMedic")
