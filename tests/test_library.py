import json
import plistlib

from showrunner.library import (
    ORDER_FILE, Kind, display_name, kind_for, load_order, resolve_web_url, save_order, scan_folder,
)


def touch(folder, *names):
    for name in names:
        (folder / name).write_bytes(b"x")


def test_kind_detection(tmp_path):
    assert kind_for(tmp_path / "a.PPTX") is Kind.POWERPOINT
    assert kind_for(tmp_path / "a.key") is Kind.KEYNOTE
    assert kind_for(tmp_path / "a.pdf") is Kind.PDF
    assert kind_for(tmp_path / "a.mov") is Kind.VIDEO
    assert kind_for(tmp_path / "a.docx") is None
    assert kind_for(tmp_path / "~$a.pptx") is None
    assert kind_for(tmp_path / "._a.pptx") is None


def test_scan_sorts_naturally_and_includes_keynote_packages(tmp_path):
    touch(tmp_path, "student 10.pdf", "student 2.pptx", "notes.txt", ".hidden.pdf")
    (tmp_path / "old.key").mkdir()
    assert [p.name for p in scan_folder(tmp_path)] == ["old.key", "student 2.pptx", "student 10.pdf"]


def test_order_is_remembered_and_new_files_are_appended(tmp_path):
    touch(tmp_path, "a.pdf", "b.pdf", "c.pdf")
    items = load_order(tmp_path)
    items.reverse()
    items[0].enabled = False
    save_order(tmp_path, items)

    touch(tmp_path, "d.pdf")
    (tmp_path / "b.pdf").unlink()
    reloaded = load_order(tmp_path)
    assert [p.path.name for p in reloaded] == ["c.pdf", "a.pdf", "d.pdf"]
    assert [p.enabled for p in reloaded] == [False, True, True]


def test_corrupt_order_file_is_ignored(tmp_path):
    touch(tmp_path, "b.pdf", "a.pdf")
    (tmp_path / ORDER_FILE).write_text("{not json")
    assert [p.path.name for p in load_order(tmp_path)] == ["a.pdf", "b.pdf"]


def test_display_name(tmp_path):
    assert display_name(tmp_path / "03_Jan__de Vries.pptx") == "Jan de Vries"
    assert display_name(tmp_path / "12 - Sam.key") == "Sam"
    assert display_name(tmp_path / "2024 Project.pdf") == "Project"
    assert display_name(tmp_path / "42.pdf") == "42"


def test_web_shortcuts(tmp_path):
    webloc = tmp_path / "a.webloc"
    webloc.write_bytes(plistlib.dumps({"URL": "https://docs.google.com/presentation/d/abc123/edit#slide=id.p"}))
    assert resolve_web_url(webloc) == "https://docs.google.com/presentation/d/abc123/present"

    url = tmp_path / "b.url"
    url.write_text("[InternetShortcut]\nURL=https://example.com/deck\n")
    assert resolve_web_url(url) == "https://example.com/deck"

    gslides = tmp_path / "c.gslides"
    gslides.write_text(json.dumps({"doc_id": "xyz"}))
    assert resolve_web_url(gslides) == "https://docs.google.com/presentation/d/xyz/present"
