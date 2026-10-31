"""`Showrunner --self-test <report>`: runs the real pipeline inside the built app.

CI runs this on every installer before a release, so a library or data file that PyInstaller
left out fails the build instead of a student's talk. Writes "OK" or the traceback to <report>.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


def _check(tmp: Path) -> None:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtWidgets import QApplication

    from . import main_window  # noqa: F401  (imports every module the app uses)
    from . import libreoffice, loudness, prepare
    from .library import Kind, Presentation
    from .pdf import fitz, open_pdf, render
    from .system import NO_WINDOW

    app = QApplication.instance() or QApplication([])
    QMediaPlayer().setAudioOutput(QAudioOutput())

    pdf = tmp / "talk.pdf"
    doc = fitz.open()
    doc.new_page(width=960, height=540).insert_text((60, 120), "Self test", fontsize=48)
    doc.save(pdf)
    with open_pdf(pdf) as doc:
        render(doc, 0, 640, 360)
    result = prepare.prepare(Presentation(pdf, Kind.PDF), tmp / "cache")
    assert result.error is None and result.page_count == 1, result.error

    clip = tmp / "clip.mp4"
    subprocess.run([loudness.ffmpeg_exe(), "-hide_banner", "-nostdin", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)],
                   check=True, capture_output=True, creationflags=NO_WINDOW)
    result = prepare.prepare(Presentation(clip, Kind.VIDEO), tmp / "cache")
    assert result.error is None, result.error

    gif = tmp / "moving.gif"
    subprocess.run([loudness.ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=64x48:rate=5:duration=1", str(gif)],
                   check=True, capture_output=True, creationflags=NO_WINDOW)
    assert prepare.is_animated(gif), "Qt's GIF plugin is missing"

    # HTTPS as the converter download does it (certificates inside the bundle).
    with libreoffice._open(libreoffice.BASE) as response:
        assert response.status == 200
    app.processEvents()


def run(report: str) -> int:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _check(Path(tmp))
        text, code = "OK\n", 0
    except BaseException:  # noqa: BLE001 - every failure belongs in the report
        text, code = traceback.format_exc(), 1
    Path(report).write_text(text, encoding="utf-8")
    sys.stdout and sys.stdout.write(text)
    return code
