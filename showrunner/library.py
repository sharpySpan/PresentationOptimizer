"""Finding presentations in a folder and remembering the running order.

Pure Python (no Qt) so it can be unit-tested anywhere.
"""
from __future__ import annotations

import configparser
import json
import plistlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

ORDER_FILE = ".showrunner-order.json"


class Kind(str, Enum):
    KEYNOTE = "keynote"
    POWERPOINT = "powerpoint"
    OPENDOCUMENT = "opendocument"
    PDF = "pdf"
    IMAGE = "image"
    VIDEO = "video"
    WEB = "web"


EXTENSIONS: dict[str, Kind] = {
    ".key": Kind.KEYNOTE,
    ".ppt": Kind.POWERPOINT,
    ".pptx": Kind.POWERPOINT,
    ".pptm": Kind.POWERPOINT,
    ".pps": Kind.POWERPOINT,
    ".ppsx": Kind.POWERPOINT,
    ".ppsm": Kind.POWERPOINT,
    ".pot": Kind.POWERPOINT,
    ".potx": Kind.POWERPOINT,
    ".odp": Kind.OPENDOCUMENT,
    ".otp": Kind.OPENDOCUMENT,
    ".pdf": Kind.PDF,
    ".png": Kind.IMAGE,
    ".jpg": Kind.IMAGE,
    ".jpeg": Kind.IMAGE,
    ".gif": Kind.IMAGE,
    ".bmp": Kind.IMAGE,
    ".tif": Kind.IMAGE,
    ".tiff": Kind.IMAGE,
    ".webp": Kind.IMAGE,
    ".heic": Kind.IMAGE,
    ".mp4": Kind.VIDEO,
    ".m4v": Kind.VIDEO,
    ".mov": Kind.VIDEO,
    ".avi": Kind.VIDEO,
    ".mkv": Kind.VIDEO,
    ".webm": Kind.VIDEO,
    ".webloc": Kind.WEB,
    ".url": Kind.WEB,
    ".gslides": Kind.WEB,
}

LABELS: dict[Kind, str] = {
    Kind.KEYNOTE: "Keynote",
    Kind.POWERPOINT: "PowerPoint",
    Kind.OPENDOCUMENT: "OpenDocument",
    Kind.PDF: "PDF",
    Kind.IMAGE: "Image",
    Kind.VIDEO: "Video",
    Kind.WEB: "Web link",
}


@dataclass
class Presentation:
    path: Path
    kind: Kind
    enabled: bool = True

    @property
    def name(self) -> str:
        return display_name(self.path)

    @property
    def label(self) -> str:
        return LABELS[self.kind]


def kind_for(path: Path) -> Kind | None:
    name = path.name
    # Hidden files, Office lock files ("~$deck.pptx") and AppleDouble files ("._deck.pptx").
    if name.startswith((".", "~$")):
        return None
    return EXTENSIONS.get(path.suffix.lower())


def display_name(path: Path) -> str:
    """'03_jan-de-vries_final.pptx' -> 'jan-de-vries final'.

    A leading number is dropped: the running order supplies the number.
    """
    name = re.sub(r"_+", " ", path.stem)
    name = re.sub(r"\s+", " ", name).strip()
    without_number = re.sub(r"^\d+\s*[-.)]?\s+", "", name)
    return without_number or name or path.name


def natural_key(text: str) -> list:
    """Sort 'student 2' before 'student 10'."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


DECK_KINDS = {Kind.KEYNOTE, Kind.POWERPOINT, Kind.OPENDOCUMENT}


def scan_folder(folder: Path) -> list[Path]:
    """Every playable file directly inside the folder.

    Old-style Keynote documents are package directories, so '.key' directories count too.
    A PDF with the same name as a deck ('Sam.pptx' + 'Sam.pdf') belongs to that deck, so it
    isn't listed separately.
    """
    found = []
    for entry in folder.iterdir():
        kind = kind_for(entry)
        if kind is None:
            continue
        if entry.is_file() or (kind is Kind.KEYNOTE and entry.is_dir()):
            found.append(entry)
    deck_stems = {p.stem.casefold() for p in found if kind_for(p) in DECK_KINDS}
    found = [p for p in found if not (kind_for(p) is Kind.PDF and p.stem.casefold() in deck_stems)]
    return sorted(found, key=lambda p: natural_key(p.name))


def paired_pdf(path: Path) -> Path | None:
    """'Sam.pptx' -> 'Sam.pdf' next to it (any capitalisation), exported by the student's own app."""
    stem = path.stem.casefold()
    try:
        for sibling in path.parent.iterdir():
            if sibling.is_file() and sibling.suffix.lower() == ".pdf" and sibling.stem.casefold() == stem:
                return sibling
    except OSError:
        pass
    return None


STALE_PDF_SECONDS = 120


def fresh_paired_pdf(path: Path) -> Path | None:
    """The paired PDF, unless the deck was saved again after it was exported (then it's outdated)."""
    pdf = paired_pdf(path)
    try:
        if pdf is not None and pdf.stat().st_mtime + STALE_PDF_SECONDS < path.stat().st_mtime:
            return None
    except OSError:
        return None
    return pdf


def needs_converter(item: "Presentation") -> bool:
    return item.kind in DECK_KINDS and fresh_paired_pdf(item.path) is None


def merge_order(saved: list[str], disabled: set[str], files: list[Path]) -> list[Presentation]:
    """Keep the saved order for files that still exist, append new files at the end."""
    by_name = {p.name: p for p in files}
    ordered = [by_name.pop(name) for name in saved if name in by_name]
    ordered += sorted(by_name.values(), key=lambda p: natural_key(p.name))
    return [Presentation(p, kind_for(p), enabled=p.name not in disabled) for p in ordered]


def load_order(folder: Path) -> list[Presentation]:
    saved, disabled = [], set()
    order_path = folder / ORDER_FILE
    if order_path.exists():
        try:
            data = json.loads(order_path.read_text(encoding="utf-8"))
            saved = [str(n) for n in data.get("order", [])]
            disabled = {str(n) for n in data.get("disabled", [])}
        except (OSError, ValueError, AttributeError):
            pass
    return merge_order(saved, disabled, scan_folder(folder))


def save_order(folder: Path, items: list[Presentation]) -> None:
    data = {
        "order": [p.path.name for p in items],
        "disabled": [p.path.name for p in items if not p.enabled],
    }
    try:
        (folder / ORDER_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # Read-only folder (e.g. a shared drive): order just isn't remembered.


def resolve_web_url(path: Path) -> str | None:
    """Read the URL out of a .webloc (macOS), .url (Windows) or .gslides (Google Drive) shortcut."""
    suffix = path.suffix.lower()
    url = None
    try:
        if suffix == ".webloc":
            with path.open("rb") as fh:
                url = plistlib.load(fh).get("URL")
        elif suffix == ".url":
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(path, encoding="utf-8")
            url = parser.get("InternetShortcut", "URL", fallback=None)
        elif suffix == ".gslides":
            data = json.loads(path.read_text(encoding="utf-8"))
            url = data.get("url")
            if not url and data.get("doc_id"):
                url = f"https://docs.google.com/presentation/d/{data['doc_id']}/edit"
    except (OSError, ValueError, plistlib.InvalidFileException, configparser.Error):
        return None
    if not url:
        return None
    # Google Slides: jump straight into presenter mode.
    return re.sub(r"(docs\.google\.com/presentation/d/[^/?#]+).*$", r"\1/present", url)
