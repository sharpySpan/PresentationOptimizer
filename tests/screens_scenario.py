"""Run by test_screens.py in its own process: Qt's offscreen platform then pretends there are
two screens (a laptop panel and a beamer), laid out as given by the config file in the cwd."""
import json
import os
import sys
import tempfile
from pathlib import Path

layout = sys.argv[1]
os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=screens.json"  # Relative: 'C:' would split the option.

from PySide6.QtCore import QSettings, QStandardPaths  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from showrunner import screens, system  # noqa: E402

app = QApplication(["test"])
app.setOrganizationName("ShowrunnerTest")
app.setApplicationName(f"ShowrunnerScreens{layout}")
QSettings().clear()
system.converter_names = lambda: ["LibreOffice"]
documents = Path(tempfile.mkdtemp())
real = QStandardPaths.writableLocation
QStandardPaths.writableLocation = lambda loc: str(documents) if loc == QStandardPaths.DocumentsLocation else real(loc)

from showrunner.main_window import MainWindow  # noqa: E402

laptop = next(s for s in QGuiApplication.screens() if "Built-in" in s.name())
beamer = next(s for s in QGuiApplication.screens() if s is not laptop)
assert screens.is_builtin(laptop) and not screens.is_builtin(beamer)

window = MainWindow()
labels = [window.beamer_combo.itemText(i) for i in range(window.beamer_combo.count())]
assert any("this laptop's own screen" in text for text in labels), labels
assert any("EPSON" in text for text in labels), labels

# Sensible defaults: slides on the beamer, console on the laptop - whichever one is the main screen.
assert window._screen(window.beamer_combo) is beamer, window.beamer_combo.currentText()
assert window._screen(window.console_combo) is laptop, window.console_combo.currentText()
assert window.extend_button.isHidden() and window.screen_hint.isHidden()

# Picking the laptop as beamer moves the console to the other screen instead of clashing.
window.beamer_combo.setCurrentIndex(window.beamer_combo.findData(screens.screen_id(laptop)))
window._on_screen_chosen(window.beamer_combo)
assert window._screen(window.console_combo) is beamer

window._swap_screens()
assert window._screen(window.beamer_combo) is beamer and window._screen(window.console_combo) is laptop
assert {o.screen() for o in window.overlays} == {laptop, beamer}  # Identify: a number on each screen.
assert any("BEAMER" in o.findChildren(type(window.screen_hint))[1].text() for o in window.overlays
           if o.screen() is beamer)
for overlay in window.overlays:
    overlay.close()

# The choice survives a restart.
window._save_settings()
window.close()
window = MainWindow()
assert window._screen(window.beamer_combo) is beamer and window._screen(window.console_combo) is laptop

# A real (full-screen) show: slides on the beamer, console on the laptop.
from test_show import _pdf  # noqa: E402

_pdf(window.folder / "a.pdf", 2, "a")
window.reload_folder()
window.windowed.setChecked(False)
window.start_show(False)
show = window.controller
assert show.console is not None
assert show.audience.screen() is beamer and show.audience.geometry() == beamer.geometry(), show.audience.geometry()
assert show.console.screen() is laptop

# Screens rearranged mid-show (cable wobble, Win+P): the slides go back to the beamer, never over the console.
show.settings.audience_screen = laptop
show._replace_windows()
assert show.audience.screen() is beamer and show.audience.geometry() == beamer.geometry()
show.stop()
window.close()
QSettings().clear()
print("OK " + json.dumps({"layout": layout}))
