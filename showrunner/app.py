import os
import sys

from . import __version__


def main() -> int:
    if sys.platform == "win32":
        # Qt's own Windows Media Foundation backend: the default FFmpeg backend in Qt 6.12
        # can freeze the app when a clip plays to its end. Must be set before QtMultimedia loads.
        os.environ.setdefault("QT_MEDIA_BACKEND", "windows")

    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Showrunner")
    app.setOrganizationName("Showrunner")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()
