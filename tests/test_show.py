"""Drives a real show (windowed test mode) through every kind of item with simulated keys."""
import faulthandler
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from showrunner import convert  # noqa: E402
from showrunner.pdf import fitz  # noqa: E402
from test_pipeline import make_deck, media  # noqa: E402,F401


def _pdf(path, pages, label):
    doc = fitz.open()
    for n in range(pages):
        doc.new_page(width=960, height=540).insert_text((60, 120), f"{label} {n + 1}", fontsize=48)
    doc.save(path)


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    application.setOrganizationName("ShowrunnerTest")
    application.setApplicationName("ShowrunnerTest")
    QSettings().clear()
    yield application
    QSettings().clear()


def wait_for(condition, ms=20000):
    # A real event loop, not QTest.qWait: qWait keeps the GIL and starves background threads.
    for _ in range(ms // 50):
        if condition():
            return True
        loop = QEventLoop()
        QTimer.singleShot(50, loop.quit)
        loop.exec()
    return False


def test_full_show(app, tmp_path, media, monkeypatch):  # noqa: F811
    faulthandler.dump_traceback_later(240, exit=True)  # A media deadlock must fail CI, not hang it.
    folder = tmp_path / "talks"
    folder.mkdir()
    _pdf(folder / "01_Jan.pdf", 3, "Jan")
    make_deck(folder / "02_Alex.pptx", media)
    shutil.copy(media / "clip.mp4", folder / "03_Sam.mp4")

    def fake_convert(src, workdir):
        _pdf(workdir / "slides.pdf", 2, "Alex")
        return workdir / "slides.pdf", src

    monkeypatch.setattr(convert, "convert", fake_convert)

    from showrunner.main_window import MainWindow

    window = MainWindow()
    window.open_folder(folder)
    assert wait_for(lambda: len(window.prepared) == 3)
    assert not [p.error for p in window.prepared.values() if p.error]
    assert window.list.item(0).icon().isNull() is False  # preview thumbnail

    window.windowed.setChecked(True)
    window.start_show(False)
    show = window.controller
    console, audience = show.console, show.audience
    assert console is not None

    QTest.keyClick(console, Qt.Key_Right)  # start Jan (PDF)
    assert show.mode.name == "TALK" and show.page == 0
    QTest.keyClick(audience, Qt.Key_Right)
    assert show.page == 1
    QTest.keyClick(audience, Qt.Key_Left)
    assert show.page == 0
    for _ in range(3):
        QTest.keyClick(console, Qt.Key_Right)
    assert show.mode.name == "UP_NEXT" and show.index == 1

    QTest.keyClick(console, Qt.Key_Right)  # start Alex: autoplay audio on slide 1, with notes
    assert [p.started for p in show.players] == [True]
    assert console.notes.toPlainText().startswith("Say hello")
    QTest.keyClick(console, Qt.Key_S, Qt.AltModifier)
    assert show.bus.muted
    QTest.keyClick(console, Qt.Key_S, Qt.AltModifier)
    assert not show.bus.muted
    console.volume.setValue(200)
    assert show.bus.percent == 200 and show.players[0].player.audioOutput().volume() == pytest.approx(1.0)
    console.volume.setValue(50)
    assert show.players[0].player.audioOutput().volume() == pytest.approx(0.25)

    QTest.keyClick(console, Qt.Key_Right)  # slide 2: video waits for a click
    assert show.page == 1 and len(show.pending_clicks) == 1
    QTest.keyClick(console, Qt.Key_Right)  # plays the video
    assert show.page == 1 and not show.pending_clicks
    QTest.keyClick(console, Qt.Key_Escape)  # end talk
    assert show.mode.name == "UP_NEXT" and show.index == 2

    QTest.keyClick(console, Qt.Key_Right)  # Sam's video; ends by itself where audio devices exist
    assert show.source.prepared.kind == "video"
    if not wait_for(lambda: show.mode.name != "TALK", 10000):
        QTest.keyClick(console, Qt.Key_Right)  # CI machines without sound devices may never advance
    assert show.mode.name == "END"

    show.stop()
    assert window.controller is None and window.isVisible()
    window.close()
    faulthandler.cancel_dump_traceback_later()
