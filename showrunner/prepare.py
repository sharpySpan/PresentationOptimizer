"""Preparing every presentation before the show, so switching speakers is instant.

For each file we produce, in a cache folder:
  - slides.pdf          what goes on the beamer
  - notes + media list  from the .pptx
  - clipN.m4a / .mkv    every audio/video clip, loudness-normalized to the same level
  - thumb.png           preview for the set-up screen

The cache lives in '<folder>/.showrunner/' when the folder is writable, so a folder prepared
on one computer is ready on the next.
"""
from __future__ import annotations

import hashlib
import json
import queue
import shutil
import tempfile
import threading
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import convert, loudness, pptx_info
from .library import DECK_KINDS, Kind, Presentation, paired_pdf, resolve_web_url

CACHE_VERSION = 2
CACHE_DIRNAME = ".showrunner"
SOURCE = "@source"  # Placeholder for "the original file", so caches survive moving the folder.


@dataclass
class Clip:
    file: str
    kind: str                                   # "audio" / "video"
    rect: list[float] | None = None             # x, y, w, h as slide fractions (video)
    autoplay: bool = False


@dataclass
class Slide:
    notes: str = ""
    clips: list[Clip] = field(default_factory=list)


@dataclass
class Prepared:
    kind: str                                   # "slides" / "image" / "video" / "web"
    dir: str = ""
    pdf: str | None = None
    image: str | None = None
    video: str | None = None
    url: str | None = None
    thumb: str | None = None
    aspect: float = 16 / 9
    slides: list[Slide] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def path(self, name: str | None, source: Path) -> Path | None:
        if not name:
            return None
        return source if name == SOURCE else Path(self.dir) / name

    @property
    def page_count(self) -> int:
        return len(self.slides)


def cache_key(item: Presentation) -> str:
    """Changes whenever the file (or its paired PDF) is replaced, so new versions are re-prepared."""
    parts = [item.path.name]
    for path in (item.path, paired_pdf(item.path) if item.kind in DECK_KINDS else None):
        if path is not None:
            stat = path.stat()
            parts += [str(stat.st_size), str(stat.st_mtime_ns)]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def is_current(item: Presentation, prepared: Prepared) -> bool:
    try:
        return bool(prepared.dir) and Path(prepared.dir).name == cache_key(item)
    except OSError:
        return False


def cache_dir(item: Presentation, fallback_root: Path) -> Path:
    key = cache_key(item)
    for root in (item.path.parent / CACHE_DIRNAME, fallback_root):
        try:
            target = root / key
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write-test"
            probe.write_text("")
            probe.unlink()
            return target
        except OSError:
            continue
    raise OSError("No writable cache folder")


def load_cached(item: Presentation, fallback_root: Path) -> Prepared | None:
    try:
        folder = cache_dir(item, fallback_root)
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.pop("version", None) != CACHE_VERSION:
        return None
    slides = [Slide(s["notes"], [Clip(**c) for c in s["clips"]]) for s in data.pop("slides", [])]
    prepared = Prepared(**data, slides=slides)
    prepared.dir = str(folder)
    return prepared


def prepare(item: Presentation, fallback_root: Path, progress=lambda text: None) -> Prepared:
    cached = load_cached(item, fallback_root)
    if cached:
        return cached
    folder = cache_dir(item, fallback_root)
    result = Prepared(kind="slides", dir=str(folder))
    try:
        if item.kind is Kind.PDF:
            _prepare_pdf(item, result, item.path, None, progress)
        elif item.kind in DECK_KINDS and paired_pdf(item.path):
            # The student also exported a PDF: it looks exactly like their own app, no converter needed.
            shutil.copyfile(paired_pdf(item.path), folder / "slides.pdf")
            deck_src = item.path if zipfile.is_zipfile(item.path) and item.kind is Kind.POWERPOINT else None
            _prepare_pdf(item, result, folder / "slides.pdf", deck_src, progress)
        elif item.kind in DECK_KINDS:
            progress("Converting slides…")
            with tempfile.TemporaryDirectory(prefix="showrunner-") as tmp:
                pdf, pptx = convert.convert(item.path, Path(tmp))
                shutil.copyfile(pdf, folder / "slides.pdf")
                deck_src = None
                if pptx is not None:
                    deck_src = folder / "deck-source.pptx" if pptx != item.path else item.path
                    if pptx != item.path:
                        shutil.copyfile(pptx, deck_src)
                _prepare_pdf(item, result, folder / "slides.pdf", deck_src, progress)
                if deck_src is not None and deck_src != item.path:
                    deck_src.unlink(missing_ok=True)
        elif item.kind is Kind.IMAGE:
            result.kind = "image"
            result.image = SOURCE
            result.slides = [Slide()]
            _image_thumb(item.path, folder / "thumb.png")
            result.thumb = "thumb.png"
        elif item.kind is Kind.VIDEO:
            result.kind = "video"
            result.slides = [Slide()]
            progress("Levelling sound…")
            try:
                result.video = loudness.normalize(item.path, folder / "video", "video").name
            except loudness.MediaError as exc:
                result.video = SOURCE
                result.warnings.append(f"Sound could not be levelled ({exc}); plays as-is.")
            if loudness.video_frame(item.path, folder / "thumb.png"):
                result.thumb = "thumb.png"
        elif item.kind is Kind.WEB:
            result.kind = "web"
            result.url = resolve_web_url(item.path)
            result.slides = [Slide()]
            if not result.url:
                raise RuntimeError("Shortcut contains no link")
            result.warnings.append("Opens in the browser: sound is not controlled by Showrunner.")
    except Exception as exc:
        result.error = str(exc) or exc.__class__.__name__
        return result  # Not cached, so it's retried next time.

    data = asdict(result)
    data.pop("dir")
    data["version"] = CACHE_VERSION
    (folder / "manifest.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    return result


def _prepare_pdf(item: Presentation, result: Prepared, pdf_path: Path, deck_path: Path | None, progress):
    from .pdf import open_pdf

    folder = Path(result.dir)
    with open_pdf(pdf_path) as doc:
        if doc.needs_pass:
            raise RuntimeError("PDF is password protected")
        if doc.page_count == 0:
            raise RuntimeError("PDF has no pages")
        pages = doc.page_count
        rect = doc[0].rect
        result.aspect = rect.width / rect.height if rect.height else 16 / 9
        pix = doc[0].get_pixmap(matrix=_fit(rect, 480), alpha=False)
        pix.save(str(folder / "thumb.png"))
    result.thumb = "thumb.png"
    result.pdf = SOURCE if pdf_path == item.path else pdf_path.name
    result.slides = [Slide() for _ in range(pages)]

    if deck_path is None or not zipfile.is_zipfile(deck_path):
        return
    try:
        deck = pptx_info.read_deck(deck_path)
    except Exception as exc:
        result.warnings.append(f"Could not read speaker notes ({exc}).")
        return
    if len(deck.slides) != pages:
        result.warnings.append(f"Slide count differs ({len(deck.slides)} in deck, {pages} in PDF): notes may be off.")

    clip_no = 0
    with zipfile.ZipFile(deck_path) as zf:
        for index, info in enumerate(deck.slides[:pages]):
            slide = result.slides[index]
            slide.notes = info.notes
            for media in info.media:
                clip_no += 1
                source = _media_source(zf, media, item.path.parent, folder, clip_no, result)
                if source is None:
                    continue
                progress(f"Levelling sound ({clip_no})…")
                try:
                    out = loudness.normalize(source, folder / f"clip{clip_no}", media.kind)
                    if source.parent == folder:
                        source.unlink(missing_ok=True)  # Raw extract no longer needed.
                except loudness.MediaError as exc:
                    result.warnings.append(f"Slide {index + 1}: sound could not be levelled ({exc}).")
                    out = source
                slide.clips.append(Clip(out.name if out.parent == folder else str(out), media.kind,
                                        list(media.rect) if media.rect else None, media.autoplay))


def _media_source(zf, media, source_folder: Path, folder: Path, n: int, result: Prepared) -> Path | None:
    if media.part:
        target = folder / f"raw{n}{Path(media.part).suffix}"
        target.write_bytes(zf.read(media.part))
        return target
    # Only linked: use it if the student put that file in the folder too.
    candidate = source_folder / media.external
    if candidate.exists():
        return candidate
    result.warnings.append(f"'{media.external}' is linked, not embedded: that sound/video is missing.")
    return None


def _fit(rect, width: int):
    from .pdf import fitz

    zoom = width / rect.width if rect.width else 1
    return fitz.Matrix(zoom, zoom)


def _image_thumb(src: Path, dest: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    image = QImage(str(src))
    if image.isNull():
        raise RuntimeError("Image format can't be shown")
    image.scaledToWidth(480, Qt.SmoothTransformation).save(str(dest))


class PrepareWorker(QThread):
    """Prepares items one by one in the background, newest requests first in line."""

    progress = Signal(str, str)      # path, status text
    prepared = Signal(str, object)   # path, Prepared

    def __init__(self, fallback_root: Path, parent=None):
        super().__init__(parent)
        self.fallback_root = fallback_root
        self.queue: queue.Queue[Presentation | None] = queue.Queue()
        self._stopping = threading.Event()

    def add(self, item: Presentation):
        self.queue.put(item)

    def stop(self):
        self._stopping.set()
        self.queue.put(None)
        self.wait(3000)

    def run(self):
        while not self._stopping.is_set():
            item = self.queue.get()
            if item is None:
                break
            if not item.path.exists():
                continue
            key = str(item.path)
            self.progress.emit(key, "Preparing…")
            try:
                result = prepare(item, self.fallback_root, lambda text: self.progress.emit(key, text))
            except Exception as exc:
                result = Prepared(kind="slides", error=str(exc))
            self.prepared.emit(key, result)
