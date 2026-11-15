"""Telling screens apart: names people recognise, an 'Identify' overlay, and turning a mirrored
(duplicated) beamer into a second screen.

Plugging in a beamer usually starts in *duplicate/mirror* mode: the computer then reports one
screen, so there is nothing to choose. Showrunner needs *extend*: two separate screens.
"""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QScreen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .system import IS_LINUX, IS_MAC, IS_WINDOWS, NO_WINDOW

# Names laptops give their own panel (macOS, Linux connectors) and common laptop panel makers.
BUILTIN_HINTS = ("built-in", "builtin", "internal", "color lcd", "edp", "lvds", "dsi")
PANEL_MAKERS = ("boe", "auo", "au optronics", "lg display", "lgd", "sharp", "samsung display", "sdc",
                "innolux", "chi mei", "cmn", "ivo", "infovision", "csot", "panda", "tianma")


def screen_id(screen: QScreen) -> str:
    """Name plus position: two identical monitors ("Generic PnP Monitor") still tell apart."""
    g = screen.geometry()
    return f"{screen.name()}@{g.x()},{g.y()}"


def is_builtin(screen: QScreen) -> bool:
    text = " ".join((screen.name(), screen.manufacturer(), screen.model())).casefold()
    return any(hint in text for hint in BUILTIN_HINTS) or screen.manufacturer().casefold().startswith(PANEL_MAKERS)


def ordered() -> list[QScreen]:
    """Left to right, top to bottom: the same order as the computer's own display settings."""
    return sorted(QGuiApplication.screens(), key=lambda s: (s.geometry().x(), s.geometry().y()))


def label(screen: QScreen, number: int) -> str:
    """'2 · Epson projector (1920×1080) - extra screen, probably the beamer'."""
    g = screen.geometry()
    name = screen.model().strip() or screen.name().strip() or "Screen"
    if name.startswith("\\\\.\\"):  # Windows device path like \\.\DISPLAY2: not helpful
        name = "Screen"
    if is_builtin(screen):
        role = "this laptop's own screen"
    elif screen is QGuiApplication.primaryScreen():
        role = "main screen"
    else:
        role = "extra screen, probably the beamer"
    return f"{number} · {name} ({g.width()}×{g.height()}) - {role}"


def default_pair(screens: list[QScreen]) -> tuple[QScreen, QScreen]:
    """(beamer, console). Console: the laptop's own screen, else the main screen. Beamer: another one."""
    primary = QGuiApplication.primaryScreen()
    console = next((s for s in screens if is_builtin(s)), primary if primary in screens else screens[0])
    beamer = next((s for s in screens if s is not console), console)
    return beamer, console


class IdentifyOverlay(QWidget):
    """A big number on every screen for a few seconds, so you can see which one is which."""

    def __init__(self, screen: QScreen, number: int, role: str):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setObjectName("identify")
        self.setStyleSheet("#identify { background:#111; border:8px solid #f5b841; }")
        big = QLabel(str(number))
        big.setAlignment(Qt.AlignCenter)
        big.setStyleSheet("color:#f5b841; font-size:200px; font-weight:800;")
        text = QLabel(f"{role}\n{label(screen, number)}")
        text.setAlignment(Qt.AlignCenter)
        text.setWordWrap(True)
        text.setStyleSheet("color:white; font-size:24px;")
        layout = QVBoxLayout(self)
        layout.addWidget(big)
        layout.addWidget(text)
        self.setScreen(screen)
        g = screen.geometry()
        w, h = min(700, g.width() - 40), min(420, g.height() - 40)
        self.setGeometry(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2, w, h)
        QTimer.singleShot(4000, self.close)

    def mousePressEvent(self, event):
        self.close()


def identify(roles: dict[str, str]) -> list[IdentifyOverlay]:
    """roles: screen_id -> what it's used for. Returns the overlays (keep a reference)."""
    overlays = []
    for number, screen in enumerate(ordered(), 1):
        overlay = IdentifyOverlay(screen, number, roles.get(screen_id(screen), "Not used by the show"))
        overlay.show()
        overlays.append(overlay)
    return overlays


def extend_displays() -> str:
    """Try to switch from duplicate/mirror to extend. Returns what the user should know."""
    if IS_WINDOWS:
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "DisplaySwitch.exe")
        try:
            subprocess.Popen([exe, "/extend"], creationflags=NO_WINDOW)
            return ("Switching to 'Extend'… the screens flash for a moment. If nothing changes, "
                    "press Windows key + P and choose 'Extend'.")
        except OSError:
            return "Press Windows key + P and choose 'Extend'."
    if IS_MAC:
        QDesktopServices.openUrl(QUrl("x-apple.systempreferences:com.apple.Displays-Settings.extension"))
        return ("In Displays, set the beamer to 'Extended display' instead of 'Mirror' "
                "(or press Cmd + F1 to switch mirroring off).")
    if IS_LINUX:
        return "Open your Display settings and switch 'Mirror displays' off (join the displays instead)."
    return "Switch your display settings from 'Duplicate/Mirror' to 'Extend'."
