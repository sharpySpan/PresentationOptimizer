import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Showrunner")
    app.setOrganizationName("Showrunner")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    return app.exec()
