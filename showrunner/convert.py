"""Turning slide decks into a PDF (what we show) plus a .pptx (where notes and media come from).

Best available converter per platform:
  Windows: PowerPoint (COM)  -> LibreOffice
  macOS:   Keynote           -> LibreOffice
  Linux:   LibreOffice
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import system

PPTX_LIKE = {".pptx", ".pptm", ".ppsx", ".ppsm", ".potx"}


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


def _powerpoint(src: Path, pdf: Path, pptx: Path | None):
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = pres = None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        # ReadOnly, Untitled, WithWindow=False
        pres = app.Presentations.Open(str(src), True, False, False)
        pres.SaveAs(str(pdf), 32)  # ppSaveAsPDF
        if pptx is not None:
            pres.SaveAs(str(pptx), 24)  # ppSaveAsOpenXMLPresentation
    finally:
        if pres is not None:
            pres.Close()
        if app is not None and app.Presentations.Count == 0:
            app.Quit()  # Only if the user doesn't have their own decks open in it.
        pythoncom.CoUninitialize()


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
        outdir = Path(tempfile.mkdtemp(prefix="showrunner-lo-"))
        result = subprocess.run(
            [system.soffice_path(), f"-env:UserInstallation={_lo_profile()}", "--headless", "--norestore",
             "--convert-to", fmt, "--outdir", str(outdir), str(src)],
            capture_output=True, text=True, errors="replace", timeout=600, creationflags=system.NO_WINDOW,
        )
        produced = outdir / f"{src.stem}.{fmt}"
        if not produced.exists():
            if fmt == "pdf":
                raise ConvertError(result.stderr.strip() or "LibreOffice could not convert the file")
            continue
        produced.replace(target)


def _lo_profile() -> str:
    # A private profile, so conversion works even while the user has LibreOffice open.
    return (Path(tempfile.gettempdir()) / "showrunner-lo-profile").as_uri()
