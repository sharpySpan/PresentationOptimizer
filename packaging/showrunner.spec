# PyInstaller spec - builds dist/Showrunner.app.  Run via packaging/build_mac.sh.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from showrunner import __version__
ICON = os.path.join(SPECPATH, "Showrunner.icns")

a = Analysis(
    [os.path.join(ROOT, "run_showrunner.py")],
    pathex=[ROOT],
    hiddenimports=["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Showrunner", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Showrunner")
app = BUNDLE(
    coll,
    name="Showrunner.app",
    icon=ICON if os.path.exists(ICON) else None,
    bundle_identifier="app.showrunner.presentations",
    version=__version__,
    info_plist={
        "CFBundleDisplayName": "Showrunner",
        "CFBundleShortVersionString": __version__,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "Showrunner opens each presentation in Keynote or PowerPoint and starts the slideshow.",
    },
)
