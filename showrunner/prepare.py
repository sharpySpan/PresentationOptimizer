"""Preparing every presentation before the show, so switching speakers is instant.

For each file we produce, in a cache folder:
  - slides.pdf          what goes on the beamer
  - notes + media list  from the .pptx
  - clipN.m4a / .mkv    every audio/video clip, loudness-normalized to the same level
  - gifN.gif            animated GIFs (frozen in any PDF), played on top of the slide
  - thumb.png           preview for the set-up screen

The cache lives in '<folder>/.showrunner/' when the folder is writable, so a folder prepared
on one computer is ready on the next.
"""
from __future__ import annotations

import collections
import hashlib
import json
import shutil
import tempfile
import threading
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import convert, loudness, pptx_info, system
from .library import DECK_KINDS, Kind, Presentation, fresh_paired_pdf, paired_pdf, resolve_web_url

CACHE_VERSION = 3
CACHE_DIRNAME = ".showrunner"
SOURCE = "@source"  # Placeholder for "the original file", so caches survive moving the folder.


@dataclass
class Clip:
    file: str
    kind: str                                   # "audio" / "video" / "gif"
    rect: list[float] | None = None             # x, y, w, h as slide fractions (video, gif)
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


CLOUD_FOLDERS = ("onedrive", "dropbox", "google drive", "googledrive", "mobile documents", "icloud")


def _in_cloud_folder(path: Path) -> bool:
    """OneDrive/iCloud/Dropbox would upload hundreds of MB of prepared clips: keep the cache local there."""
    return any(part.casefold().startswith(CLOUD_FOLDERS) for part in path.parts)


def cache_dir(item: Presentation, fallback_root: Path) -> Path:
    key = cache_key(item)
    roots = [fallback_root] if _in_cloud_folder(item.path.parent) else [item.path.parent / CACHE_DIRNAME, fallback_root]
    for root in roots:
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
        elif item.kind in DECK_KINDS and fresh_paired_pdf(item.path):
            # The student also exported a PDF: it looks exactly like their own app, no converter needed.
            _use_paired_pdf(item, result, folder, fresh_paired_pdf(item.path), progress)
        elif item.kind in DECK_KINDS:
            progress("Converting slides…")
            with tempfile.TemporaryDirectory(prefix="showrunner-") as tmp:
                try:
                    pdf, pptx = convert.convert(item.path, Path(tmp))
                except convert.ConvertError:
                    stale = paired_pdf(item.path)
                    if stale is None:
                        raise
                    # The deck was edited after its PDF export and won't convert: an older PDF beats nothing.
                    _use_paired_pdf(item, result, folder, stale, progress)
                    result.warnings.append(f"{stale.name} is older than {item.path.name}: showing the older PDF.")
                    return result  # Not cached: converted properly once a converter works.
                _use_converted(item, result, folder, pdf, pptx, progress)
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
            except (loudness.MediaError, OSError) as exc:
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


def _use_paired_pdf(item: Presentation, result: Prepared, folder: Path, pdf: Path, progress):
    shutil.copyfile(pdf, folder / "slides.pdf")
    deck_src = item.path if item.kind is Kind.POWERPOINT and zipfile.is_zipfile(item.path) else None
    _prepare_pdf(item, result, folder / "slides.pdf", deck_src, progress)


def _use_converted(item: Presentation, result: Prepared, folder: Path, pdf: Path, pptx: Path | None, progress):
    shutil.copyfile(pdf, folder / "slides.pdf")
    deck_src = None
    if pptx is not None:
        deck_src = folder / "deck-source.pptx" if pptx != item.path else item.path
        if pptx != item.path:
            shutil.copyfile(pptx, deck_src)
    _prepare_pdf(item, result, folder / "slides.pdf", deck_src, progress)
    if deck_src is not None and deck_src != item.path:
        deck_src.unlink(missing_ok=True)


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
                try:
                    clip = _prepare_media(zf, media, item, result, folder, clip_no, progress)
                except Exception as exc:  # noqa: BLE001 - one broken clip must not sink the whole talk
                    result.warnings.append(f"Slide {index + 1}: a {media.kind} on this slide can't be played ({exc}).")
                    continue
                if clip is not None:
                    slide.clips.append(clip)


def _prepare_media(zf, media, item: Presentation, result: Prepared, folder: Path, n: int, progress) -> Clip | None:
    rect = list(media.rect) if media.rect else None
    if media.kind == "gif":
        gif = folder / f"gif{n}.gif"
        gif.write_bytes(zf.read(media.part))
        if is_animated(gif):
            return Clip(gif.name, "gif", rect, True)
        gif.unlink()  # A still GIF already looks right in the PDF.
        return None
    source = _media_source(zf, media, item.path.parent, folder, n, result)
    if source is None:
        return None
    progress(f"Levelling sound ({n})…")
    try:
        out = loudness.normalize(source, folder / f"clip{n}", media.kind)
        if source.parent == folder:
            source.unlink(missing_ok=True)  # Raw extract no longer needed.
    except loudness.MediaError as exc:
        result.warnings.append(f"A {media.kind} could not be levelled ({exc}); it plays as-is.")
        out = source
    return Clip(out.name if out.parent == folder else str(out), media.kind, rect, media.autoplay)


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


def is_animated(path: Path) -> bool:
    from PySide6.QtGui import QImageReader

    return QImageReader(str(path)).imageCount() > 1


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


def worker_count() -> int:
    """Parallel preparations: each LibreOffice/ffmpeg job mostly uses one core and some memory."""
    import os

    return max(2, min(4, (os.cpu_count() or 2) // 2))


class PrepareWorker(QObject):
    """Prepares items in background threads, several at once, in the order they were added.

    Plain Python threads (daemon), not QThreads: quitting mid-conversion can't crash the app.
    """

    progress = Signal(str, str)      # path, status text
    prepared = Signal(str, object)   # path, Prepared

    def __init__(self, fallback_root: Path, parent=None):
        super().__init__(parent)
        self.fallback_root = fallback_root
        self._queue: collections.deque[Presentation] = collections.deque()
        self._wake = threading.Condition()
        self._stopping = False
        self._locks: dict[str, threading.Lock] = {}

    def start(self):
        for n in range(worker_count()):
            threading.Thread(target=self._serve, name=f"prepare-{n}", daemon=True).start()

    def add(self, item: Presentation, urgent: bool = False):
        """urgent: the show is waiting for this one, so it goes to the front of the line."""
        with self._wake:
            (self._queue.appendleft if urgent else self._queue.append)(item)
            self._wake.notify()

    def stop(self):
        with self._wake:
            self._stopping = True
            self._queue.clear()
            self._wake.notify_all()
        system.kill_children()

    def _next(self) -> Presentation | None:
        with self._wake:
            while not self._queue and not self._stopping:
                self._wake.wait()
            return None if self._stopping else self._queue.popleft()

    def _serve(self):
        while (item := self._next()) is not None:
            key = str(item.path)
            # The same file queued twice (it changed again) never runs twice at once: same cache folder.
            with self._wake:
                lock = self._locks.setdefault(key, threading.Lock())
            with lock:
                if not item.path.exists():
                    result = Prepared(kind="slides", error="The file was removed from the folder")
                else:
                    self.progress.emit(key, "Preparing…")
                    try:
                        result = prepare(item, self.fallback_root, lambda text: self.progress.emit(key, text))
                    except Exception as exc:  # noqa: BLE001 - shown on that file's row
                        result = Prepared(kind="slides", error=str(exc) or exc.__class__.__name__)
            if not self._stopping:
                self.prepared.emit(key, result)
