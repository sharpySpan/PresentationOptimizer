"""The hands-off path: default folder, converter fetched by itself, PDF pairing, replaced files."""
import io
import os
import tarfile
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QStandardPaths  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from showrunner import convert, libreoffice, prepare, system  # noqa: E402
from showrunner.library import Kind, Presentation, needs_converter, scan_folder  # noqa: E402
from test_pipeline import make_deck, media  # noqa: E402,F401
from test_show import _pdf, wait_for  # noqa: E402


# --- PDF pairing ----------------------------------------------------------------

def test_paired_pdf_is_part_of_the_deck(tmp_path, media):  # noqa: F811
    make_deck(tmp_path / "Sam.pptx", media)
    _pdf(tmp_path / "sam.PDF", 2, "Sam")       # any capitalisation
    _pdf(tmp_path / "Other.pdf", 1, "Other")
    assert [p.name for p in scan_folder(tmp_path)] == ["Other.pdf", "Sam.pptx"]

    deck = Presentation(tmp_path / "Sam.pptx", Kind.POWERPOINT)
    assert not needs_converter(deck)


def test_paired_pdf_needs_no_converter_and_keeps_notes(tmp_path, media, monkeypatch):  # noqa: F811
    def no_converter(*_):
        raise AssertionError("converter must not be used")

    monkeypatch.setattr(convert, "convert", no_converter)
    make_deck(tmp_path / "Sam.pptx", media)
    _pdf(tmp_path / "Sam.pdf", 2, "Sam")
    item = Presentation(tmp_path / "Sam.pptx", Kind.POWERPOINT)
    result = prepare.prepare(item, tmp_path / "fb")
    assert result.error is None and result.page_count == 2
    assert result.slides[0].notes.startswith("Say hello")
    assert result.slides[1].clips  # media still comes from the pptx

    # Student replaces the PDF: the cache key changes, so it gets prepared again.
    assert prepare.is_current(item, result)
    time.sleep(0.05)
    _pdf(tmp_path / "Sam.pdf", 3, "Sam v2")
    assert not prepare.is_current(item, result)
    assert prepare.prepare(item, tmp_path / "fb").page_count == 3


# --- LibreOffice download ---------------------------------------------------------

def test_latest_version_and_package_paths():
    html = '<a href="25.8.7/">x</a><a href="26.8.10/">x</a><a href="26.8.2/">x</a><a href="../">up</a>'
    assert libreoffice.latest_version(html) == "26.8.10"
    v = "26.8.2"
    assert libreoffice.package_path(v, "win32", "AMD64") == f"{v}/win/x86_64/LibreOffice_{v}_Win_x86-64.msi"
    assert libreoffice.package_path(v, "darwin", "arm64") == f"{v}/mac/aarch64/LibreOffice_{v}_MacOS_aarch64.dmg"
    assert libreoffice.package_path(v, "darwin", "x86_64") == f"{v}/mac/x86_64/LibreOffice_{v}_MacOS_x86-64.dmg"
    assert libreoffice.package_path(v, "linux", "x86_64") == f"{v}/deb/x86_64/LibreOffice_{v}_Linux_x86-64_deb.tar.gz"


def _ar_member(name: str, data: bytes) -> bytes:
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
    return header + data + (b"\n" if len(data) % 2 else b"")


def test_extract_deb(tmp_path):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:xz") as tar:
        body = b"#!/bin/sh\necho soffice\n"
        info = tarfile.TarInfo("./opt/libreoffice26.8/program/soffice")
        info.size, info.mode = len(body), 0o755
        tar.addfile(info, io.BytesIO(body))
    deb = b"!<arch>\n" + _ar_member("debian-binary", b"2.0\n") + _ar_member("control.tar.xz", b"x") \
        + _ar_member("data.tar.xz", payload.getvalue())
    libreoffice.extract_deb(deb, tmp_path)
    soffice = tmp_path / "opt" / "libreoffice26.8" / "program" / "soffice"
    assert soffice.read_bytes().startswith(b"#!/bin/sh")


# --- the whole thing without touching anything ------------------------------------

@pytest.fixture
def app():
    QStandardPaths.setTestModeEnabled(True)  # Default folder goes to a test location, not real Documents.
    application = QApplication.instance() or QApplication([])
    application.setOrganizationName("ShowrunnerTest")
    application.setApplicationName("ShowrunnerZero")
    QSettings().clear()
    yield application
    QSettings().clear()


def test_hands_off(app, tmp_path, media, monkeypatch):  # noqa: F811
    from showrunner import main_window

    state = {"installed": False, "downloads": 0}
    monkeypatch.setattr(system, "converter_names", lambda: ["LibreOffice"] if state["installed"] else [])

    def fake_run(self):
        state["downloads"] += 1
        self.progress.emit(50, 100, "Downloading LibreOffice…")
        time.sleep(0.3)  # worker thread: no Qt event loop here
        state["installed"] = True
        self.done.emit("")

    monkeypatch.setattr(main_window.ConverterDownload, "run", fake_run)

    def fake_convert(src, workdir):
        _pdf(workdir / "slides.pdf", 2, "converted")
        return workdir / "slides.pdf", src

    monkeypatch.setattr(convert, "convert", fake_convert)
    documents = tmp_path / "Documents"  # never the real Documents folder
    monkeypatch.setattr(QStandardPaths, "writableLocation",
                        lambda loc, real=QStandardPaths.writableLocation: str(documents)
                        if loc == QStandardPaths.DocumentsLocation else real(loc))

    window = main_window.MainWindow()
    folder = window.folder
    assert folder == documents / "Showrunner Presentations" and folder.is_dir()
    assert wait_for(lambda: state["downloads"] == 1, 5000)  # started by itself at launch

    # Just drop files in the folder.
    make_deck(folder / "Alex.pptx", media)
    _pdf(folder / "Jan.pdf", 3, "Jan")
    window.reload_folder()
    assert wait_for(lambda: len(window.prepared) == 2 and not window.status, 20000)
    assert all(p.error is None for p in window.prepared.values())
    assert window.prepared[str(folder / "Alex.pptx")].page_count == 2
    assert state["downloads"] == 1

    window.close()
