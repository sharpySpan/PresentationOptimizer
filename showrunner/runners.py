"""Playing slide decks in Keynote or PowerPoint and waiting until the slideshow ends.

These functions block; the show runs them on a worker thread.
"""
from __future__ import annotations

import hashlib
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import macos
from .library import Kind

KEYNOTE = "Keynote"
POWERPOINT = "Microsoft PowerPoint"

POLL_SECONDS = 0.5
START_TIMEOUT = 30


class RunnerError(RuntimeError):
    pass


def pick_app(kind: Kind, prefer: str) -> str | None:
    """Which app plays this file. `prefer` is 'keynote' or 'powerpoint' (used for .ppt/.pptx/.odp)."""
    has_keynote = macos.app_installed(KEYNOTE)
    has_powerpoint = macos.app_installed(POWERPOINT)
    if kind is Kind.KEYNOTE:
        return KEYNOTE if has_keynote else None
    if kind in (Kind.POWERPOINT, Kind.OPENDOCUMENT):
        if kind is Kind.OPENDOCUMENT and not macos.soffice_path():
            return None
        order = [POWERPOINT, KEYNOTE] if prefer == "powerpoint" else [KEYNOTE, POWERPOINT]
        for app in order:
            if (app == KEYNOTE and has_keynote) or (app == POWERPOINT and has_powerpoint):
                return app
    return None


def play(path: Path, kind: Kind, app: str, stop: threading.Event) -> None:
    if kind is Kind.OPENDOCUMENT:
        path = convert_with_libreoffice(path)
    if app == KEYNOTE:
        _play_keynote(path, stop)
    elif app == POWERPOINT:
        _play_powerpoint(path, stop)
    else:
        raise RunnerError(f"Don't know how to play with {app}")


def _wait(condition, stop: threading.Event, timeout: float | None) -> bool:
    """Poll `condition` until it is true. Returns False on timeout or stop."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while not stop.is_set():
        try:
            if condition():
                return True
        except macos.AppleScriptError:
            pass
        if deadline is not None and time.monotonic() > deadline:
            return False
        stop.wait(POLL_SECONDS)
    return False


def _try(script: str) -> None:
    """Best-effort clean-up command; failing here must not turn a finished talk into an error."""
    try:
        macos.osascript(script, timeout=20)
    except macos.AppleScriptError:
        pass


# --- Keynote -----------------------------------------------------------------

def _keynote_playing() -> bool:
    script = (
        f'if application "{KEYNOTE}" is running then\n'
        f'  tell application "{KEYNOTE}" to return playing\n'
        "end if\n"
        'return "false"'
    )
    return macos.osascript(script, timeout=10) == "true"


def _play_keynote(path: Path, stop: threading.Event) -> None:
    doc_id = macos.osascript(
        f"""
set theFile to POSIX file {macos.quote(str(path))}
tell application "{KEYNOTE}"
    activate
    set theDoc to open theFile
    delay 0.5
    start theDoc from first slide of theDoc
    try
        return id of theDoc
    on error
        return ""
    end try
end tell""",
        timeout=180,
    )
    try:
        if not _wait(_keynote_playing, stop, START_TIMEOUT):
            if stop.is_set():
                return
            raise RunnerError("Keynote opened the file but the slideshow did not start")
        _wait(lambda: not _keynote_playing(), stop, None)
        if stop.is_set():
            _try(f'tell application "{KEYNOTE}" to stop front document')
    finally:
        _close_keynote(doc_id)


def _close_keynote(doc_id: str) -> None:
    target = f"(first document whose id is {macos.quote(doc_id)})" if doc_id else "front document"
    _try(f'tell application "{KEYNOTE}" to close {target} saving no')


# --- PowerPoint --------------------------------------------------------------

def _powerpoint_showing() -> bool:
    script = (
        f'if application "{POWERPOINT}" is running then\n'
        f'  tell application "{POWERPOINT}" to return (count of slide show windows)\n'
        "end if\n"
        "return 0"
    )
    return macos.osascript(script, timeout=10) not in ("", "0")


def _play_powerpoint(path: Path, stop: threading.Event) -> None:
    name = macos.osascript(
        f"""
set theFile to POSIX file {macos.quote(str(path))}
tell application "{POWERPOINT}"
    activate
    open theFile
    delay 1
    set thePres to active presentation
    run slide show (slide show settings of thePres)
    return name of thePres
end tell""",
        timeout=180,
    )
    try:
        if not _wait(_powerpoint_showing, stop, START_TIMEOUT):
            if stop.is_set():
                return
            raise RunnerError("PowerPoint opened the file but the slideshow did not start")
        _wait(lambda: not _powerpoint_showing(), stop, None)
        if stop.is_set():
            _try(f'tell application "{POWERPOINT}" to exit slide show (slide show view of slide show window 1)')
    finally:
        _try(f'tell application "{POWERPOINT}" to close presentation {macos.quote(name)} saving no')


# --- LibreOffice conversion --------------------------------------------------

def convert_with_libreoffice(path: Path) -> Path:
    """Turn .odp into .pptx so Keynote/PowerPoint can play it (cached per file version)."""
    soffice = macos.soffice_path()
    if not soffice:
        raise RunnerError("LibreOffice is needed to open .odp files")
    stat = path.stat()
    digest = hashlib.sha1(f"{path}|{stat.st_mtime_ns}|{stat.st_size}".encode()).hexdigest()[:12]
    outdir = Path(tempfile.gettempdir()) / "showrunner-converted" / digest
    target = outdir / (path.stem + ".pptx")
    if target.exists():
        return target
    outdir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [soffice, "--headless", "--norestore", "--convert-to", "pptx", "--outdir", str(outdir), str(path)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0 or not target.exists():
        raise RunnerError("LibreOffice could not convert the file: " + (result.stderr.strip() or "unknown error"))
    return target
