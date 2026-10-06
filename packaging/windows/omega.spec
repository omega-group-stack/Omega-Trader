# PyInstaller build spec for the Omega-Trader desktop app.
#
# Build (on Windows):   pyinstaller packaging/windows/omega.spec --noconfirm
# Output:               dist/OmegaTrader/OmegaTrader.exe
#
# One-folder, not one-file. One-file unpacks the whole bundle to a temp
# directory on every launch, which for a pandas/numpy app means several
# seconds of startup and antivirus scanning each time. The installer hides
# the folder anyway, so the user never sees the difference.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]

# uvicorn resolves its loop/protocol implementations by string at runtime, so
# static analysis cannot see them and they must be named explicitly.
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
]
# Strategy blocks, indicators and feeds are selected from config by name.
hiddenimports += collect_submodules("omega")

# MetaTrader5 only exists on Windows; everywhere else this must not be fatal.
if sys.platform == "win32":
    hiddenimports.append("MetaTrader5")

datas = [
    (str(ROOT / "omega" / "dashboard" / "static"), "omega/dashboard/static"),
    (str(ROOT / "config" / "config.yaml"), "config"),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "BACKTEST.md"), "."),
]

icon = ROOT / "packaging" / "windows" / "omega.ico"

a = Analysis(
    [str(ROOT / "omega" / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing in the shipped app plots or opens notebooks; excluding these
    # keeps the installer in the tens of megabytes instead of hundreds.
    excludes=[
        "matplotlib", "tkinter", "PyQt5", "PySide2", "PIL", "notebook",
        "IPython", "jupyter", "pytest", "scipy",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OmegaTrader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console on purpose. The window is the app's status display and its
    # close button is the stop button; a windowed build would hide every
    # startup error from exactly the users least able to find a log file.
    console=True,
    disable_windowed_traceback=False,
    icon=str(icon) if icon.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OmegaTrader",
)
