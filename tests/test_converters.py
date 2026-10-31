"""Real conversions with the real converters, every deck format. Skipped where no converter is
installed; CI runs them on every platform right after downloading LibreOffice like the app does."""
import os
import shutil
import subprocess
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from showrunner import convert, prepare, system  # noqa: E402
from showrunner.library import Kind, Presentation, kind_for  # noqa: E402

pptx = pytest.importorskip("pptx")
needs_libreoffice = pytest.mark.skipif(not system.soffice_path(), reason="LibreOffice not installed")


def make_pptx(path, slides=3):
    from pptx.util import Inches

    deck = pptx.Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    for n in range(slides):
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = f"Slide {n + 1} – Zoë"
        slide.placeholders[1].text = "Bullet one\nBullet two"
        slide.notes_slide.notes_text_frame.text = f"Note {n + 1}"
    deck.save(path)
    return path


def lo_convert(src, fmt, outdir):
    """Make .odp / .ppt test files with LibreOffice itself."""
    subprocess.run([system.soffice_path(), f"-env:UserInstallation={convert._lo_profile()}", "--headless",
                    "--convert-to", fmt, "--outdir", str(outdir), str(src)],
                   check=True, capture_output=True, timeout=300, creationflags=system.NO_WINDOW)
    return outdir / f"{src.stem}.{fmt}"


def check(result, slides=3):
    assert result.error is None, result.error
    assert result.page_count == slides
    assert [s.notes for s in result.slides] == [f"Note {n + 1}" for n in range(slides)]


@needs_libreoffice
@pytest.mark.parametrize("suffix", [".pptx", ".ppsx", ".pptm"])
def test_powerpoint_formats(tmp_path, suffix, monkeypatch):
    monkeypatch.setattr(system, "has_powerpoint", lambda: False)  # Force LibreOffice.
    src = make_pptx(tmp_path / f"Émile van Dijk – talk{suffix}")
    check(prepare.prepare(Presentation(src, kind_for(src)), tmp_path / "fb"))


@needs_libreoffice
@pytest.mark.parametrize("fmt", ["odp", "ppt"])
def test_other_deck_formats(tmp_path, fmt, monkeypatch):
    monkeypatch.setattr(system, "has_powerpoint", lambda: False)
    made = tmp_path / "made"
    made.mkdir()
    src = lo_convert(make_pptx(made / "deck.pptx"), fmt, tmp_path)
    check(prepare.prepare(Presentation(src, kind_for(src)), tmp_path / "fb"))


@needs_libreoffice
def test_many_decks_convert_in_parallel(app_qt, tmp_path, monkeypatch):
    from test_show import wait_for

    monkeypatch.setattr(system, "has_powerpoint", lambda: False)
    for n in range(6):
        make_pptx(tmp_path / f"{n:02d} Student.pptx")
    worker = prepare.PrepareWorker(tmp_path / "fb")
    results = {}
    worker.prepared.connect(lambda key, result: results.__setitem__(key, result))
    worker.start()
    start = time.monotonic()
    for path in sorted(tmp_path.glob("*.pptx")):
        worker.add(Presentation(path, Kind.POWERPOINT))
    assert wait_for(lambda: len(results) == 6, 600000)
    worker.stop()
    print(f"6 decks in {time.monotonic() - start:.1f}s with {prepare.worker_count()} workers")
    for result in results.values():
        check(result)


@pytest.mark.skipif(not system.has_powerpoint(), reason="PowerPoint not installed")
def test_real_powerpoint(tmp_path):
    src = make_pptx(tmp_path / "With PowerPoint.pptx")
    pdf, deck = convert.convert(src, tmp_path)
    assert pdf.stat().st_size > 0 and deck == src


@pytest.fixture
def app_qt():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@needs_libreoffice
def test_libreoffice_runs_while_the_profile_is_fresh(tmp_path, monkeypatch):
    """A brand-new profile folder (first run on a computer) must convert fine too."""
    monkeypatch.setattr(system, "has_powerpoint", lambda: False)
    from showrunner import libreoffice

    monkeypatch.setattr(libreoffice, "data_dir", lambda: tmp_path / "data")
    src = make_pptx(tmp_path / "fresh.pptx", slides=1)
    pdf, _ = convert.convert(src, tmp_path)
    assert pdf.stat().st_size > 0
    shutil.rmtree(tmp_path / "data", ignore_errors=True)
