"""Picking the beamer and console screens: two-screen layouts, mirroring, and the 'Extend' help."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QStandardPaths  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from showrunner import screens, system  # noqa: E402

HERE = Path(__file__).parent
LAPTOP = {"name": "Built-in Retina Display", "width": 1440, "height": 900, "logicalDpi": 96, "dpr": 1}
BEAMER = {"name": "EPSON PJ", "width": 1920, "height": 1080, "logicalDpi": 96, "dpr": 1}
LAYOUTS = {
    # Usual: laptop is the main screen, beamer extends to the right.
    "laptop-main": [dict(LAPTOP, x=0, y=0), dict(BEAMER, x=1440, y=0)],
    # Beamer made the main screen (Windows sometimes does this): the console still goes on the laptop.
    "beamer-main": [dict(BEAMER, x=0, y=0), dict(LAPTOP, x=1920, y=0)],
}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_laptop_and_beamer(tmp_path, layout):
    (tmp_path / "screens.json").write_text(json.dumps({"screens": LAYOUTS[layout]}))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(HERE.parent), str(HERE)]))
    env.pop("QT_QPA_PLATFORM", None)
    result = subprocess.run([sys.executable, str(HERE / "screens_scenario.py"), layout], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("ShowrunnerTest")
    app.setApplicationName("ShowrunnerOneScreen")
    QSettings().clear()
    monkeypatch.setattr(system, "converter_names", lambda: ["LibreOffice"])
    monkeypatch.setattr(QStandardPaths, "writableLocation",
                        lambda loc, real=QStandardPaths.writableLocation: str(tmp_path / "Documents")
                        if loc == QStandardPaths.DocumentsLocation else real(loc))
    from showrunner.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()
    QSettings().clear()


def test_one_screen_explains_mirroring(window, monkeypatch):
    """Beamer in duplicate/mirror mode = one screen: say so, offer to extend, don't start silently."""
    assert not window.screen_hint.isHidden() and "Only one screen" in window.screen_hint.text()
    assert not window.extend_button.isHidden()

    from test_show import _pdf

    _pdf(window.folder / "a.pdf", 1, "a")
    window.reload_folder()
    window.windowed.setChecked(False)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[1]) or QMessageBox.Cancel)
    window.start_show(False)
    assert asked == ["Only one screen"] and window.controller is None

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    window.start_show(False)
    assert window.controller is not None and window.controller.console is None
    window.controller.stop()


def test_extend_button(window, monkeypatch):
    launched = []
    monkeypatch.setattr(screens.subprocess, "Popen", lambda args, **kw: launched.append(args))
    opened = []
    monkeypatch.setattr(screens.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    window.extend_button.click()
    if system.IS_WINDOWS:
        assert launched and launched[0][0].endswith("DisplaySwitch.exe") and launched[0][1] == "/extend"
    elif system.IS_MAC:
        assert opened and "Displays" in opened[0]
    assert window.screen_hint.text() and not window.screen_hint.isHidden()


class FakeScreen:
    def __init__(self, name, model="", manufacturer=""):
        self._name, self._model, self._manufacturer = name, model, manufacturer

    def name(self):
        return self._name

    def model(self):
        return self._model

    def manufacturer(self):
        return self._manufacturer

    def geometry(self):
        from PySide6.QtCore import QRect

        return QRect(0, 0, 1280, 800)


@pytest.mark.parametrize("screen, builtin", [
    (FakeScreen("Built-in Retina Display"), True),
    (FakeScreen("Color LCD"), True),
    (FakeScreen("eDP-1"), True),
    (FakeScreen("\\\\.\\DISPLAY1", "", "BOE"), True),
    (FakeScreen("HDMI-1", "EPSON PJ", "Seiko Epson"), False),
    (FakeScreen("Generic PnP Monitor"), False),
])
def test_builtin_detection(screen, builtin):
    assert screens.is_builtin(screen) is builtin


def test_labels_are_readable():
    QApplication.instance() or QApplication([])
    assert screens.label(FakeScreen("\\\\.\\DISPLAY2"), 2).startswith("2 · Screen (1280×800)")
    assert "probably the beamer" in screens.label(FakeScreen("HDMI-1", "EPSON PJ"), 2)
    assert "this laptop's own screen" in screens.label(FakeScreen("eDP-1"), 1)
