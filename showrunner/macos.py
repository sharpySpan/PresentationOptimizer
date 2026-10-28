"""Small macOS helpers: AppleScript, app detection, focus, kiosk mode, keeping the Mac awake."""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"

SOFFICE_PATHS = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
]


class AppleScriptError(RuntimeError):
    pass


def osascript(script: str, timeout: float = 60) -> str:
    if not IS_MAC:
        raise AppleScriptError("AppleScript is only available on macOS")
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AppleScriptError("The app did not respond in time") from exc
    if result.returncode != 0:
        raise AppleScriptError(result.stderr.strip() or "AppleScript failed")
    return result.stdout.strip()


def quote(text: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


@functools.lru_cache(maxsize=None)
def app_installed(name: str) -> bool:
    if not IS_MAC:
        return False
    return subprocess.run(["open", "-Ra", name], capture_output=True).returncode == 0


def soffice_path() -> str | None:
    for candidate in SOFFICE_PATHS:
        if os.path.exists(candidate):
            return candidate
    return shutil.which("soffice")


def activate_self() -> None:
    """Bring this app back to the front after Keynote/PowerPoint finished."""
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        return
    except Exception:
        pass
    try:
        osascript(
            'tell application "System Events" to set frontmost of '
            f"(first process whose unix id is {os.getpid()}) to true",
            timeout=5,
        )
    except AppleScriptError:
        pass


# NSApplicationPresentationHideDock | NSApplicationPresentationHideMenuBar
_KIOSK_OPTIONS = (1 << 1) | (1 << 3)


def set_kiosk(enabled: bool) -> None:
    """Hide the Dock and menu bar while our show window is in front."""
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().setPresentationOptions_(_KIOSK_OPTIONS if enabled else 0)
    except Exception:
        pass


def keep_awake() -> subprocess.Popen | None:
    """Stop the display and Mac from sleeping for as long as this process lives."""
    if not IS_MAC:
        return None
    try:
        return subprocess.Popen(["caffeinate", "-dimsu", "-w", str(os.getpid())])
    except OSError:
        return None
