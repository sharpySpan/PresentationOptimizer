"""Turning slide decks into a PDF (what we show) plus a .pptx (where notes and media come from).

Best available converter per platform:
  Windows: PowerPoint (COM)  -> LibreOffice
  macOS:   Keynote           -> LibreOffice
  Linux:   LibreOffice
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path

from . import system

PPTX_LIKE = {".pptx", ".pptm", ".ppsx", ".ppsm", ".potx"}


# PowerPoint and Keynote are single-window apps: one conversion at a time. LibreOffice runs in parallel.
_APP_LOCK = threading.Lock()


class ConvertError(RuntimeError):
    pass


def convert(src: Path, workdir: Path) -> tuple[Path, Path | None]:
    """Returns (pdf, pptx). pptx is None when notes/media can't be read (the PDF still plays)."""
    suffix = src.suffix.lower()
    pdf = workdir / "slides.pdf"
    need_pptx = suffix not in PPTX_LIKE
    pptx_out = workdir / "deck.pptx"
    errors = []

    for name, backend in _backends(suffix):
        try:
            if backend is _libreoffice:
                backend(src, pdf, pptx_out if need_pptx else None)
            else:
                with _APP_LOCK:
                    backend(src, pdf, pptx_out if need_pptx else None)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue
        if pdf.exists() and pdf.stat().st_size > 0:
            pptx = src if not need_pptx else (pptx_out if pptx_out.exists() else None)
            return pdf, pptx

    if not errors:
        if suffix == ".key":
            raise ConvertError("Keynote files can only be prepared on a Mac with Keynote - ask for a .pptx or .pdf")
        raise ConvertError("No converter found - install LibreOffice (free) to play slide decks")
    raise ConvertError("; ".join(errors))


def _backends(suffix: str):
    if system.has_powerpoint() and suffix != ".key":
        yield "PowerPoint", _powerpoint
    if system.has_keynote():
        yield "Keynote", _keynote
    if system.soffice_path():
        yield "LibreOffice", _libreoffice


POWERPOINT_TIMEOUT = 600


def _powerpoint(src: Path, pdf: Path, pptx: Path | None):
    """Runs in a child process: a hidden PowerPoint dialog (repair file? sign in?) can't hang the app."""
    was_running = _powerpoint_running()
    args = ["--ppt-convert", str(src), str(pdf), str(pptx or "")]
    command = [sys.executable, *args] if getattr(sys, "frozen", False) else [sys.executable, "-m", "showrunner", *args]
    try:
        result = system.run(command, timeout=POWERPOINT_TIMEOUT,
                            cwd=str(Path(__file__).resolve().parents[1]) if not getattr(sys, "frozen", False) else None)
    except subprocess.TimeoutExpired:
        if not was_running:  # Only a PowerPoint we started; never the user's own open decks.
            system.run(["taskkill", "/F", "/IM", "POWERPNT.EXE"], timeout=30)
        raise ConvertError("PowerPoint did not respond (is it asking to activate, sign in or repair the file?)")
    if result.returncode != 0:
        error_file = pdf.with_suffix(".error")
        message = error_file.read_text(encoding="utf-8") if error_file.exists() else result.stderr.strip()
        raise ConvertError(message or "PowerPoint failed")


def _powerpoint_running() -> bool:
    try:
        out = system.run(["tasklist", "/FI", "IMAGENAME eq POWERPNT.EXE", "/NH"], timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True  # Unknown: then never kill it.
    return "POWERPNT.EXE" in out.upper()


def powerpoint_child(src: str, pdf: str, pptx: str) -> int:
    """Entry point of the child process (Showrunner --ppt-convert src pdf pptx)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = pres = None
    pptx = pptx or None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        try:
            app.DisplayAlerts = 1  # ppAlertsNone
        except Exception:  # noqa: BLE001 - older PowerPoint versions
            pass
        # "file::password::": a password-protected deck fails right away instead of showing a prompt.
        # ReadOnly, Untitled, WithWindow=False
        pres = app.Presentations.Open(f"{src}::showrunner::", True, False, False)
        pres.SaveAs(str(pdf), 32)  # ppSaveAsPDF
        if pptx is not None:
            pres.SaveAs(str(pptx), 24)  # ppSaveAsOpenXMLPresentation
    except Exception as exc:  # noqa: BLE001 - reported to the parent through stderr
        message = str(exc)
        if "password" in message.lower():
            message = "The presentation is password protected - ask for a version without a password"
        # The installed app has no console: the parent reads the reason from this file.
        Path(pdf).with_suffix(".error").write_text(message, encoding="utf-8")
        return 1
    finally:
        if pres is not None:
            pres.Close()
        if app is not None and app.Presentations.Count == 0:
            app.Quit()  # Only if the user doesn't have their own decks open in it.
        pythoncom.CoUninitialize()
    return 0


def _keynote(src: Path, pdf: Path, pptx: Path | None):
    export_pptx = f"    export theDoc to pptxOut as Microsoft PowerPoint\n" if pptx is not None else ""
    pptx_var = f"set pptxOut to POSIX file {system.quote(str(pptx))}\n" if pptx is not None else ""
    system.osascript(
        f"set src to POSIX file {system.quote(str(src))}\n"
        f"set pdfOut to POSIX file {system.quote(str(pdf))}\n"
        f"{pptx_var}"
        'tell application "Keynote"\n'
        "    set theDoc to open src\n"
        "    export theDoc to pdfOut as PDF\n"
        f"{export_pptx}"
        "    close theDoc saving no\n"
        "end tell",
        timeout=600,
    )


def _libreoffice(src: Path, pdf: Path, pptx: Path | None):
    for fmt, target in (("pdf", pdf), ("pptx", pptx)):
        if target is None:
            continue
        outdir = pdf.parent / f"lo-{fmt}"  # Inside the caller's temporary workdir: cleaned up with it.
        outdir.mkdir(exist_ok=True)
        try:
            result = system.run(
                [system.soffice_path(), f"-env:UserInstallation={_lo_profile()}", "--headless", "--norestore",
                 "--nolockcheck", "--convert-to", fmt, "--outdir", str(outdir), str(src)],
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise ConvertError("LibreOffice took more than 10 minutes") from None
        produced = outdir / f"{src.stem}.{fmt}"
        if not produced.exists():
            if fmt == "pdf":
                raise ConvertError(result.stderr.strip() or "LibreOffice could not convert the file")
            continue
        produced.replace(target)


def _lo_profile() -> str:
    """A private profile per worker thread: works while the user has LibreOffice open, lets
    conversions run side by side, and lives in the app's data folder so it's only created once."""
    from .libreoffice import data_dir

    slot = re.sub(r"[^A-Za-z0-9-]", "_", threading.current_thread().name)
    return (data_dir() / "lo-profiles" / slot).as_uri()
