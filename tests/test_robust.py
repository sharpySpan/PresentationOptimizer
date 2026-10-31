"""Things that go wrong on real machines: odd files, half-copied files, broken media, odd folders."""
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QStandardPaths  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from showrunner import convert, library, loudness, pptx_info, prepare, system  # noqa: E402
from showrunner.library import Kind, Presentation  # noqa: E402
from showrunner.pdf import fitz  # noqa: E402
from test_pipeline import ffmpeg, make_deck, media  # noqa: E402,F401
from test_show import _pdf, wait_for  # noqa: E402


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    application.setOrganizationName("ShowrunnerTest")
    application.setApplicationName("ShowrunnerRobust")
    QSettings().clear()
    yield application
    QSettings().clear()


def _no_converter(monkeypatch):
    def fail(src, workdir):
        raise convert.ConvertError("no converter in this test")

    monkeypatch.setattr(convert, "convert", fail)


def _fake_converter(monkeypatch, pages=2):
    def fake(src, workdir):
        _pdf(workdir / "slides.pdf", pages, "converted")
        return workdir / "slides.pdf", src

    monkeypatch.setattr(convert, "convert", fake)


def _rewrite_zip(path: Path, changes: dict[str, bytes | None]):
    """Copy a zip, replacing (or with None: dropping) some parts."""
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
        for info in src.infolist():
            if info.filename in changes:
                if changes[info.filename] is not None:
                    dst.writestr(info.filename, changes.pop(info.filename))
                continue
            dst.writestr(info, src.read(info.filename))
        for name, data in changes.items():
            if data is not None:
                dst.writestr(name, data)
    tmp.replace(path)


# --- bad files never crash anything ----------------------------------------------

def test_corrupt_deck_gives_a_clear_error(tmp_path, monkeypatch):
    _no_converter(monkeypatch)
    deck = tmp_path / "broken.pptx"
    deck.write_bytes(os.urandom(5000))
    result = prepare.prepare(Presentation(deck, Kind.POWERPOINT), tmp_path / "fb")
    assert result.error and "no converter" in result.error


@pytest.mark.parametrize("content", [b"", b"%PDF-1.7 truncated", os.urandom(3000)])
def test_broken_pdf_gives_an_error_not_a_crash(tmp_path, content):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(content)
    result = prepare.prepare(Presentation(pdf, Kind.PDF), tmp_path / "fb")
    assert result.error


def test_password_pdf_is_explained(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "secret")
    doc.save(tmp_path / "locked.pdf", encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    result = prepare.prepare(Presentation(tmp_path / "locked.pdf", Kind.PDF), tmp_path / "fb")
    assert "password" in result.error.lower()


def test_broken_image_and_video(tmp_path):
    (tmp_path / "pic.png").write_bytes(b"not a png")
    (tmp_path / "clip.mp4").write_bytes(os.urandom(2000))
    image = prepare.prepare(Presentation(tmp_path / "pic.png", Kind.IMAGE), tmp_path / "fb")
    assert image.error
    video = prepare.prepare(Presentation(tmp_path / "clip.mp4", Kind.VIDEO), tmp_path / "fb")
    assert video.error is None and video.warnings  # Still tries to play it as-is.


def test_one_broken_clip_does_not_sink_the_talk(tmp_path, media, monkeypatch):  # noqa: F811
    _fake_converter(monkeypatch)
    deck = tmp_path / "Alex.pptx"
    make_deck(deck, media)
    # Garbage instead of the video, and the audio part missing entirely (dangling relationship).
    _rewrite_zip(deck, {"ppt/media/media2.mp4": os.urandom(4000), "ppt/media/media1.mp3": None})
    result = prepare.prepare(Presentation(deck, Kind.POWERPOINT), tmp_path / "fb")
    assert result.error is None, result.error
    assert result.page_count == 2 and result.slides[0].notes.startswith("Say hello")
    assert any("can't be played" in w or "as-is" in w for w in result.warnings), result.warnings


def test_linked_media_with_spaces_is_found(tmp_path, media, monkeypatch):  # noqa: F811
    _fake_converter(monkeypatch)
    deck = tmp_path / "Sam.pptx"
    make_deck(deck, media)
    with zipfile.ZipFile(deck) as zf:
        rels = zf.read("ppt/slides/_rels/slide3.xml.rels").decode()
    rels = rels.replace("file:///C:/Users/kid/Music/intro.mp3", "file:///C:/Users/kid/Music/My%20Intro%20Song.mp3")
    _rewrite_zip(deck, {"ppt/slides/_rels/slide3.xml.rels": rels.encode()})
    shutil.copy(media / "quiet.mp3", tmp_path / "My Intro Song.mp3")
    result = prepare.prepare(Presentation(deck, Kind.POWERPOINT), tmp_path / "fb")
    assert [c.kind for c in result.slides[1].clips] == ["video", "audio"]
    assert not any("linked" in w for w in result.warnings)


def test_strict_open_xml_deck_still_gives_notes(tmp_path, media):  # noqa: F811
    deck = tmp_path / "strict.pptx"
    make_deck(deck, media)
    strict = {
        "http://schemas.openxmlformats.org/presentationml/2006/main": "http://purl.oclc.org/ooxml/presentationml/main",
        "http://schemas.openxmlformats.org/drawingml/2006/main": "http://purl.oclc.org/ooxml/drawingml/main",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships":
            "http://purl.oclc.org/ooxml/officeDocument/relationships",
    }
    changes = {}
    with zipfile.ZipFile(deck) as zf:
        for name in zf.namelist():
            if name.endswith(".xml"):
                text = zf.read(name).decode()
                for old, new in strict.items():
                    text = text.replace(old, new)
                changes[name] = text.encode()
    _rewrite_zip(deck, changes)
    info = pptx_info.read_deck(deck)
    assert info.slides[0].notes.startswith("Say hello") and info.slides[1].media


def test_hevc_phone_video_becomes_h264(tmp_path):
    src = tmp_path / "phone.mov"
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=2", "-f", "lavfi", "-i",
           "sine=frequency=300:duration=2", "-c:v", "libx265", "-c:a", "aac", "-shortest", str(src))
    result = prepare.prepare(Presentation(src, Kind.VIDEO), tmp_path / "fb")
    assert result.error is None and not result.warnings
    assert loudness.video_codec(result.path(result.video, src)) == "h264"


def test_unicode_and_spaces_everywhere(tmp_path, media, monkeypatch):  # noqa: F811
    _fake_converter(monkeypatch)
    folder = tmp_path / "Klas 4B – presentaties ü"
    folder.mkdir()
    _pdf(folder / "Zoë Ω 'quotes' & more.pdf", 2, "Zoe")
    make_deck(folder / "Jürgen van der Bergh.pptx", media)
    for item in library.load_order(folder):
        result = prepare.prepare(item, tmp_path / "fb")
        assert result.error is None, (item.path.name, result.error)


# --- paired PDFs ---------------------------------------------------------------------

def _age(path: Path, seconds: float):
    stat = path.stat()
    os.utime(path, (stat.st_atime - seconds, stat.st_mtime - seconds))


def test_outdated_paired_pdf_is_not_shown(tmp_path, media, monkeypatch):  # noqa: F811
    deck = tmp_path / "Sam.pptx"
    make_deck(deck, media)
    _pdf(tmp_path / "Sam.pdf", 5, "old export")
    _age(tmp_path / "Sam.pdf", 3600)  # Exported an hour before the deck was last saved.
    item = Presentation(deck, Kind.POWERPOINT)
    assert library.needs_converter(item)

    _fake_converter(monkeypatch, pages=2)
    assert prepare.prepare(item, tmp_path / "fb").page_count == 2  # The converted, current deck.


def test_outdated_paired_pdf_is_the_fallback_without_converter(tmp_path, media, monkeypatch):  # noqa: F811
    deck = tmp_path / "Sam.pptx"
    make_deck(deck, media)
    _pdf(tmp_path / "Sam.pdf", 2, "old export")
    _age(tmp_path / "Sam.pdf", 3600)
    _no_converter(monkeypatch)
    result = prepare.prepare(Presentation(deck, Kind.POWERPOINT), tmp_path / "fb")
    assert result.error is None and any("older" in w for w in result.warnings)
    assert not (Path(result.dir) / "manifest.json").exists()  # Not cached: converted for real later.


# --- caches in odd places ----------------------------------------------------------------

def test_cloud_folders_keep_the_cache_local(tmp_path):
    folder = tmp_path / "OneDrive - School" / "Documents" / "Showrunner Presentations"
    folder.mkdir(parents=True)
    _pdf(folder / "a.pdf", 1, "a")
    result = prepare.prepare(Presentation(folder / "a.pdf", Kind.PDF), tmp_path / "local-cache")
    assert Path(result.dir).parent == tmp_path / "local-cache"
    assert not (folder / prepare.CACHE_DIRNAME).exists()


def test_read_only_folder_uses_the_local_cache(tmp_path, monkeypatch):
    folder = tmp_path / "usb"
    folder.mkdir()
    _pdf(folder / "a.pdf", 1, "a")
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if prepare.CACHE_DIRNAME in self.parts:
            raise PermissionError("read-only")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    result = prepare.prepare(Presentation(folder / "a.pdf", Kind.PDF), tmp_path / "local-cache")
    assert result.error is None and Path(result.dir).parent == tmp_path / "local-cache"


# --- background preparation ----------------------------------------------------------------

def test_worker_prepares_many_at_once_and_reports_removed_files(app, tmp_path, media):  # noqa: F811
    for n in range(6):
        _pdf(tmp_path / f"{n}.pdf", 3, str(n))
        shutil.copy(media / "clip.mp4", tmp_path / f"{n}.mp4")
    gone = tmp_path / "gone.pdf"
    worker = prepare.PrepareWorker(tmp_path / "fb")
    results = {}
    worker.prepared.connect(lambda key, result: results.__setitem__(key, result))
    worker.start()
    items = [Presentation(p, library.kind_for(p)) for p in sorted(tmp_path.iterdir()) if p.is_file()]
    items.append(Presentation(gone, Kind.PDF))
    for item in items:
        worker.add(item)
    assert wait_for(lambda: len(results) == len(items), 60000)
    worker.stop()
    assert "removed" in results[str(gone)].error
    assert all(r.error is None for k, r in results.items() if k != str(gone))


def test_urgent_items_jump_the_queue(app, tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(prepare, "worker_count", lambda: 1)
    real = prepare.prepare

    def slow(item, root, progress=lambda text: None):
        order.append(item.path.name)
        time.sleep(0.05)
        return real(item, root, progress)

    monkeypatch.setattr(prepare, "prepare", slow)
    for n in range(5):
        _pdf(tmp_path / f"{n}.pdf", 1, str(n))
    worker = prepare.PrepareWorker(tmp_path / "fb")
    done = []
    worker.prepared.connect(lambda key, result: done.append(key))
    for n in range(4):
        worker.add(Presentation(tmp_path / f"{n}.pdf", Kind.PDF))
    worker.add(Presentation(tmp_path / "4.pdf", Kind.PDF), urgent=True)
    worker.start()
    assert wait_for(lambda: len(done) == 5, 20000)
    worker.stop()
    assert order[0] == "4.pdf"


# --- the set-up window ---------------------------------------------------------------------

@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(system, "converter_names", lambda: ["LibreOffice"])
    documents = tmp_path / "Documents"
    monkeypatch.setattr(QStandardPaths, "writableLocation",
                        lambda loc, real=QStandardPaths.writableLocation: str(documents)
                        if loc == QStandardPaths.DocumentsLocation else real(loc))
    from showrunner.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()


def test_file_still_being_copied_waits_until_done(window):
    folder = window.folder
    _pdf(folder / "late.pdf", 2, "late")
    window.reload_folder()
    key = str(folder / "late.pdf")
    assert window.status.get(key) == "Copying in…"
    assert wait_for(lambda: key in window.prepared and key not in window.status, 20000)
    assert window.prepared[key].page_count == 2


def test_file_replaced_while_preparing_is_prepared_again(window):
    folder = window.folder
    path = folder / "talk.pdf"
    _pdf(path, 2, "v1")
    _age(path, 60)
    window.reload_folder()
    assert wait_for(lambda: str(path) in window.prepared, 20000)
    old = window.prepared.pop(str(path))
    _pdf(path, 4, "v2")  # Replaced; then the old result arrives late.
    _age(path, 60)
    window._on_prepared(str(path), old)
    assert wait_for(lambda: str(path) in window.prepared and str(path) not in window.status, 20000)
    assert window.prepared[str(path)].page_count == 4


def test_documents_not_allowed_falls_back(app, tmp_path, monkeypatch):
    blocked = tmp_path / "Documents"
    blocked.write_text("a file, so nothing can be created inside it")
    monkeypatch.setattr(QStandardPaths, "writableLocation",
                        lambda loc, real=QStandardPaths.writableLocation: str(blocked)
                        if loc == QStandardPaths.DocumentsLocation else real(loc))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    from showrunner.main_window import MainWindow

    folder = MainWindow._default_folder()
    assert folder == tmp_path / "home" / "Showrunner Presentations" and folder.is_dir()


def test_unreadable_folder_shows_a_message(window, tmp_path, monkeypatch):
    def denied(folder):
        raise PermissionError(13, "Operation not permitted")

    from showrunner import main_window

    monkeypatch.setattr(main_window, "load_order", denied)
    window.open_folder(tmp_path)
    assert window.items == [] and "isn't allowed" in window.list.placeholder


def test_show_waits_for_preparation_without_freezing(window, tmp_path):
    folder = window.folder
    _pdf(folder / "a.pdf", 2, "a")
    _age(folder / "a.pdf", 60)
    window.reload_folder()
    window.prepared.clear()  # As if it wasn't ready yet when the show reached it.
    window.status.clear()
    window.windowed.setChecked(True)
    window.start_show(False)
    show = window.controller
    show.next()
    assert show.mode.name == "UP_NEXT" and show.waiting_for  # Not blocking: the event loop keeps running.
    assert wait_for(lambda: show.mode.name == "TALK", 20000)
    assert show.source.prepared.page_count == 2
    show.stop()


def test_crash_handler_writes_a_log(app, tmp_path, monkeypatch):
    import sys

    from showrunner import app as app_module, libreoffice
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(libreoffice, "data_dir", lambda: tmp_path)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    app_module._install_crash_handler()
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    assert "boom" in (tmp_path / "showrunner.log").read_text(encoding="utf-8")
    assert shown and "boom" in shown[0]
