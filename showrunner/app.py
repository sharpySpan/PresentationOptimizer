import os
import sys

from . import __version__


def _install_crash_handler() -> None:
    """Unexpected errors go to a log file and a message box instead of silently killing the show."""
    import traceback

    from .libreoffice import data_dir

    log = data_dir() / "showrunner.log"

    def handle(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"--- Showrunner {__version__}\n{text}\n")
        except OSError:
            pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance():
                QMessageBox.warning(None, "Showrunner", "Something went wrong, but the show keeps running.\n\n"
                                    f"{value}\n\nDetails were saved to:\n{log}")
        except Exception:  # noqa: BLE001 - never let the handler itself crash
            pass

    sys.excepthook = handle


def main() -> int:
    if sys.platform == "win32":
        # Qt's own Windows Media Foundation backend: the default FFmpeg backend in Qt 6.12
        # can freeze the app when a clip plays to its end. Must be set before QtMultimedia loads.
        os.environ.setdefault("QT_MEDIA_BACKEND", "windows")
    elif sys.platform.startswith("linux"):
        # X11 (via XWayland) first: on Wayland an app can't put its window on a chosen screen.
        # Falls back to Wayland when X11 isn't available.
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb;wayland")

    if "--ppt-convert" in sys.argv:  # Child process for PowerPoint (see convert.py).
        from .convert import powerpoint_child

        return powerpoint_child(*sys.argv[sys.argv.index("--ppt-convert") + 1:][:3])

    if "--self-test" in sys.argv:
        from . import selftest

        return selftest.run(sys.argv[sys.argv.index("--self-test") + 1])

    _install_crash_handler()
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Showrunner")
    app.setOrganizationName("Showrunner")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()
