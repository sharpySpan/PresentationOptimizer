"""CI check: download LibreOffice the way the app does, then convert a real .pptx with it.

Proves the download, checksum and unpacking (MSI / DMG / DEB) work on every platform.
Needs python-pptx (only for making the test deck).
"""
import sys
import tempfile
import time
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from showrunner import convert, libreoffice, pptx_info, system

start = time.time()
soffice = libreoffice.install(lambda done, total, text: None)
print(f"LibreOffice unpacked in {time.time() - start:.0f}s: {soffice}")
system.soffice_path.cache_clear()
assert system.soffice_path() is not None, "app doesn't find the downloaded LibreOffice"

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    for n in range(3):
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = f"Slide {n + 1}"
        slide.notes_slide.notes_text_frame.text = f"Note {n + 1}"
    deck.save(tmp / "check.pptx")

    convert_start = time.time()
    pdf, pptx = convert.convert(tmp / "check.pptx", tmp)
    from showrunner.pdf import open_pdf

    with open_pdf(pdf) as doc:
        pages = doc.page_count
    notes = [s.notes for s in pptx_info.read_deck(pptx).slides]
    print(f"Converted in {time.time() - convert_start:.0f}s: {pages} pages, notes {notes}")
    if pages != 3 or notes != ["Note 1", "Note 2", "Note 3"]:
        sys.exit("conversion result is wrong")
print("converter check OK")
