# PyInstaller spec for macOS, Windows and Linux. Run via the build script for your platform.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
from showrunner import __version__

a = Analysis(
    [os.path.join(ROOT, "run_showrunner.py")],
    pathex=[ROOT],
    # The bundled ffmpeg binary lives in imageio_ffmpeg/binaries.
    datas=collect_data_files("imageio_ffmpeg"),
    hiddenimports=["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Showrunner", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Showrunner")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Showrunner.app",
        bundle_identifier="app.showrunner.presentations",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "Showrunner",
            "CFBundleShortVersionString": __version__,
            "LSMinimumSystemVersion": "12.0",  # Qt 6.9's minimum
            "NSHighResolutionCapable": True,
            "NSAppleEventsUsageDescription": "Showrunner asks Keynote to convert presentations before the show.",
        },
    )
