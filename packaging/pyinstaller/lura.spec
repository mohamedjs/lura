# PyInstaller spec for every platform: `pyinstaller packaging/pyinstaller/lura.spec`
#
# Two executables share one folder:
#   lura  — console, the CLI (`lura selftest`, `lura dashboard`, …)
#   LuraApp — windowed (no console on Windows), runs the assistant; it is the
#           macOS .app's executable and what the installers start at login.
#           (Not "Lura": macOS and Windows filesystems ignore case.)
# -*- mode: python -*-
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src"
LURA = SRC / "lura"
ICON_PNG = ROOT / "packaging" / "icons" / "lura.png"

datas = [
    (str(LURA / "shared" / "i18n" / "*.json"), "lura/shared/i18n"),
    (str(LURA / "modules" / "billing" / "store.json"), "lura/modules/billing"),
    (str(LURA / "modules" / "dashboard" / "static"), "lura/modules/dashboard/static"),
]
binaries = []
hidden = collect_submodules("lura")
for package in ("vosk", "sounddevice", "_sounddevice_data", "anthropic", "google.genai", "cairo",
                "webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass

# Qt WebEngine (~200 MB) draws the settings window on Linux only; Windows and
# macOS use the system's web engine through pywebview instead.
excludes = ["tkinter", "gi"]
if sys.platform.startswith("linux"):
    hidden += ["PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore"]
else:
    excludes += ["PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick"]

a = Analysis([str(Path(SPECPATH) / "entry.py")], pathex=[str(SRC)], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)

icon = str(ROOT / "packaging" / "icons" / ("lura.icns" if sys.platform == "darwin" else "lura.ico"))
if not Path(icon).exists():
    icon = str(ICON_PNG)   # PyInstaller converts a PNG when Pillow is installed

cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="lura", console=True, icon=icon)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LuraApp", console=False, icon=icon)
coll = COLLECT(cli, gui, a.binaries, a.datas, name="lura")

if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="Lura.app", icon=icon, bundle_identifier="com.lura.assistant",
        info_plist={
            "CFBundleExecutable": "LuraApp",
            # Shown in permission prompts; without it macOS says "Python".
            "CFBundleName": "Lura",
            "CFBundleDisplayName": "Lura",
            "NSLocalNetworkUsageDescription": "Lura's dashboard and connectors talk to services on this computer.",
            "CFBundleShortVersionString": re.search(r'__version__ = "([^"]+)"', (LURA / "__init__.py").read_text()).group(1),
            # The one permission a voice assistant cannot work without.
            "NSMicrophoneUsageDescription": "Lura listens for her wake word and to what you say to her.",
            "NSAppleEventsUsageDescription": "Lura opens and controls apps when you ask her to.",
            # A menu-bar app: the face and the tray icon, no Dock icon.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
