"""Platform helpers: finding converter apps, keeping the machine awake, macOS kiosk mode."""
from __future__ import annotations

import ctypes
import functools
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# Don't flash a console window for every ffmpeg/soffice call on Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0


_RUNNING: set[subprocess.Popen] = set()
_RUNNING_LOCK = threading.Lock()


def run(args: list[str], timeout: float, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run that is killed on timeout and when the app quits (see kill_children)."""
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace", creationflags=NO_WINDOW, **kwargs)
    with _RUNNING_LOCK:
        _RUNNING.add(proc)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
        return subprocess.CompletedProcess(args, proc.returncode, out, err)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(proc)


def kill_children():
    """On quit: stop conversions still running, so nothing keeps working in the background."""
    with _RUNNING_LOCK:
        procs = list(_RUNNING)
    for proc in procs:
        try:
            proc.kill()
        except OSError:
            pass


class AppleScriptError(RuntimeError):
    pass


def osascript(script: str, timeout: float = 120) -> str:
    try:
        result = run(["osascript", "-e", script], timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AppleScriptError("The app did not respond in time") from exc
    if result.returncode != 0:
        raise AppleScriptError(result.stderr.strip() or "AppleScript failed")
    return result.stdout.strip()


def quote(text: str) -> str:
    """Quote a Python string as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


@functools.lru_cache(maxsize=None)
def has_keynote() -> bool:
    return IS_MAC and subprocess.run(["open", "-Ra", "Keynote"], capture_output=True).returncode == 0


@functools.lru_cache(maxsize=None)
def has_powerpoint() -> bool:
    """Windows only: PowerPoint is driven over COM. (On macOS we use Keynote.)"""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "PowerPoint.Application"))
        return True
    except OSError:
        return False


@functools.lru_cache(maxsize=None)
def soffice_path() -> str | None:
    candidates = []
    if IS_MAC:
        candidates += [
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
        ]
    elif IS_WINDOWS:
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidates.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    from .libreoffice import installed_soffice  # Our own download (see libreoffice.py).

    bundled = installed_soffice()
    return str(bundled) if bundled else None


def converter_names() -> list[str]:
    names = []
    if has_powerpoint():
        names.append("PowerPoint")
    if has_keynote():
        names.append("Keynote")
    if soffice_path():
        names.append("LibreOffice")
    return names


# --- keeping the machine awake ------------------------------------------------

class KeepAwake:
    """Stops the display and computer from sleeping while the show runs."""

    _ES_CONTINUOUS = 0x80000000
    _ES_SYSTEM_REQUIRED = 0x00000001
    _ES_DISPLAY_REQUIRED = 0x00000002

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        pid = str(os.getpid())
        try:
            if IS_WINDOWS:
                ctypes.windll.kernel32.SetThreadExecutionState(
                    self._ES_CONTINUOUS | self._ES_SYSTEM_REQUIRED | self._ES_DISPLAY_REQUIRED)
            elif IS_MAC:
                self._proc = subprocess.Popen(["caffeinate", "-dimsu", "-w", pid])
            elif shutil.which("systemd-inhibit"):
                self._proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep", "--who=Showrunner", "--why=Presenting",
                     "tail", f"--pid={pid}", "-f", "/dev/null"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, AttributeError):
            pass

    def release(self):
        if IS_WINDOWS:
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(self._ES_CONTINUOUS)
            except (OSError, AttributeError):
                pass
        if self._proc:
            self._proc.terminate()
            self._proc = None


# NSApplicationPresentationHideDock | NSApplicationPresentationHideMenuBar
_KIOSK_OPTIONS = (1 << 1) | (1 << 3)


def set_kiosk(enabled: bool) -> None:
    """macOS: hide the Dock and menu bar so they can't show up on the beamer."""
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication

        NSApplication.sharedApplication().setPresentationOptions_(_KIOSK_OPTIONS if enabled else 0)
    except Exception:
        pass
