"""Pre-flight checks: find broken files and sound problems before the class starts."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from . import macos, runners
from .library import Kind, Presentation, resolve_web_url

OK, WARN, ERROR = "ok", "warn", "error"

MEDIA_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".caf", ".mp4", ".m4v", ".mov",
    ".wma", ".wmv", ".avi", ".flv", ".mkv", ".ogg", ".webm", ".mpg", ".mpeg", ".asf", ".mid", ".midi",
}
# Media that Keynote/PowerPoint for Mac usually cannot play.
UNPLAYABLE_ON_MAC = {".wma", ".wmv", ".avi", ".flv", ".mkv", ".ogg", ".webm", ".asf", ".mid", ".midi"}


@dataclass
class CheckResult:
    level: str
    message: str


def check(item: Presentation, prefer_app: str) -> CheckResult:
    path = item.path
    if not path.exists():
        return CheckResult(ERROR, "File is missing")
    if path.is_file() and path.stat().st_size == 0:
        return CheckResult(ERROR, "File is empty (still uploading?)")
    try:
        return _CHECKS[item.kind](item, prefer_app)
    except Exception as exc:  # A check must never crash the app.
        return CheckResult(ERROR, f"Could not read file: {exc}")


def _check_pdf(item: Presentation, _prefer: str) -> CheckResult:
    from .pdf import open_pdf

    with open_pdf(item.path) as doc:
        if doc.needs_pass:
            return CheckResult(ERROR, "PDF is password protected")
        if doc.page_count == 0:
            return CheckResult(ERROR, "PDF has no pages")
        return CheckResult(OK, f"{doc.page_count} pages")


def _check_image(item: Presentation, _prefer: str) -> CheckResult:
    from PySide6.QtGui import QImageReader

    reader = QImageReader(str(item.path))
    if not reader.canRead():
        return CheckResult(ERROR, "Image format can't be shown")
    return CheckResult(OK, "Single image")


def _check_video(item: Presentation, _prefer: str) -> CheckResult:
    if item.path.suffix.lower() in {".avi", ".mkv", ".webm"}:
        return CheckResult(WARN, "Video may not play on macOS - .mp4 or .mov is safest")
    return CheckResult(OK, "Plays full screen with sound")


def _check_web(item: Presentation, _prefer: str) -> CheckResult:
    url = resolve_web_url(item.path)
    if not url:
        return CheckResult(ERROR, "Shortcut contains no link")
    return CheckResult(WARN, f"Opens in browser, needs internet: {url}")


def _check_slides(item: Presentation, prefer: str) -> CheckResult:
    if not macos.IS_MAC:
        return CheckResult(WARN, "Slide decks can only be played on macOS")
    app = runners.pick_app(item.kind, prefer)
    if app is None:
        if item.kind is Kind.OPENDOCUMENT:
            return CheckResult(ERROR, "Needs LibreOffice plus Keynote or PowerPoint")
        need = "Keynote" if item.kind is Kind.KEYNOTE else "Keynote or PowerPoint"
        return CheckResult(ERROR, f"{need} is not installed")

    media, warnings = _embedded_media(item)
    app_name = "PowerPoint" if app == runners.POWERPOINT else "Keynote"
    if warnings:
        return CheckResult(WARN, f"Plays in {app_name}. " + " ".join(warnings))
    sound = f", {media} audio/video clip{'s' if media != 1 else ''}" if media else ""
    return CheckResult(OK, f"Plays in {app_name}{sound}")


def _embedded_media(item: Presentation) -> tuple[int, list[str]]:
    """Count embedded media and spot the classic 'no sound on the day' problems."""
    path = item.path
    if path.is_dir():  # Old Keynote package
        names = [str(p.relative_to(path)) for p in path.rglob("*") if p.is_file()]
        rels: dict[str, bytes] = {}
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            rels = {n: zf.read(n) for n in names if n.endswith(".rels") and "/slides/" in n}
    else:
        return 0, []  # Legacy binary .ppt: can't look inside cheaply.

    media = [n for n in names if PurePosixPath(n).suffix.lower() in MEDIA_EXTENSIONS]
    warnings = []
    bad = sorted({PurePosixPath(n).suffix.lower() for n in media} & UNPLAYABLE_ON_MAC)
    if bad:
        warnings.append(f"Contains {', '.join(bad)} media that may not play on a Mac.")

    linked = []
    for data in rels.values():
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            continue
        for rel in root:
            target = rel.get("Target", "")
            if rel.get("TargetMode") == "External" and not re.match(r"https?:", target):
                if PurePosixPath(target.replace("\\", "/")).suffix.lower() in MEDIA_EXTENSIONS:
                    linked.append(PurePosixPath(target.replace("\\", "/")).name)
    if linked:
        warnings.append(
            f"Links to {', '.join(sorted(set(linked)))} instead of embedding it - that sound/video will be missing."
        )
    return len(media), warnings


_CHECKS = {
    Kind.PDF: _check_pdf,
    Kind.IMAGE: _check_image,
    Kind.VIDEO: _check_video,
    Kind.WEB: _check_web,
    Kind.KEYNOTE: _check_slides,
    Kind.POWERPOINT: _check_slides,
    Kind.OPENDOCUMENT: _check_slides,
}
