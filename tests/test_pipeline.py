import subprocess
import zipfile
from pathlib import Path

import pytest

from showrunner import convert, loudness, pptx_info, prepare
from showrunner.library import Kind, Presentation
from showrunner.pdf import fitz

P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" ' \
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ' \
    'xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main"'
REL = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def ffmpeg(*args):
    subprocess.run([loudness.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-af", "volume=-30dB", str(d / "quiet.mp3"))
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=660:duration=4", "-af", "volume=-3dB", str(d / "loud.wav"))
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3", "-f", "lavfi", "-i",
           "sine=frequency=300:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
           str(d / "clip.mp4"))
    return d


def _pic(shape_id, kind, rid_embed, rid_link, x, y, w, h):
    tag = "a:videoFile" if kind == "video" else "a:audioFile"
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="m"/><p:cNvPicPr/><p:nvPr><{tag} r:link="{rid_link}"/>'
            f'<p:extLst><p:ext uri="x"><p14:media r:embed="{rid_embed}"/></p:ext></p:extLst></p:nvPr></p:nvPicPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm></p:spPr></p:pic>')


def _timing(shape_id, node_type):
    return (f'<p:timing><p:tnLst><p:par><p:cTn id="1"><p:childTnLst><p:par><p:cTn id="2" presetClass="mediacall" '
            f'nodeType="{node_type}"><p:childTnLst><p:cmd type="call" cmd="playFrom(0.0)"><p:cBhvr><p:cTn id="3"/>'
            f'<p:tgtEl><p:spTgt spid="{shape_id}"/></p:tgtEl></p:cBhvr></p:cmd></p:childTnLst></p:cTn></p:par>'
            f'</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>')


def make_deck(path: Path, media: Path):
    """3 slides: 1 has notes + autoplay audio, 2 is hidden, 3 has a click-to-play video and a linked file."""
    cx, cy = 12192000, 6858000
    slides = {
        1: (f'<p:sld {P}><p:cSld><p:spTree>{_pic(4, "audio", "rId3", "rId3", -500000, 0, 100, 100)}</p:spTree></p:cSld>'
            f'{_timing(4, "afterEffect")}</p:sld>',
            [("rId2", "../notesSlides/notesSlide1.xml", ""), ("rId3", "../media/media1.mp3", "")]),
        2: (f'<p:sld {P} show="0"><p:cSld><p:spTree/></p:cSld></p:sld>', []),
        3: (f'<p:sld {P}><p:cSld><p:spTree>{_pic(5, "video", "rId4", "rId4", cx // 4, cy // 4, cx // 2, cy // 2)}'
            f'{_pic(6, "audio", "rId9", "rId5", 0, 0, 10, 10)}</p:spTree></p:cSld>{_timing(5, "clickEffect")}</p:sld>',
            [("rId4", "../media/media2.mp4", ""), ("rId5", "file:///C:/Users/kid/Music/intro.mp3", "External")]),
    }
    with zipfile.ZipFile(path, "w") as zf:
        ids = "".join(f'<p:sldId id="{255 + n}" r:id="rId{n}"/>' for n in slides)
        zf.writestr("ppt/presentation.xml", f'<p:presentation {P}><p:sldIdLst>{ids}</p:sldIdLst>'
                                            f'<p:sldSz cx="{cx}" cy="{cy}"/></p:presentation>')
        rels = "".join(f'<Relationship Id="rId{n}" Type="slide" Target="slides/slide{n}.xml"/>' for n in slides)
        zf.writestr("ppt/_rels/presentation.xml.rels", f"<Relationships {REL}>{rels}</Relationships>")
        for n, (xml, slide_rels) in slides.items():
            zf.writestr(f"ppt/slides/slide{n}.xml", xml)
            body = "".join(f'<Relationship Id="{i}" Type="t" Target="{t}"' + (f' TargetMode="{m}"' if m else "") + "/>"
                           for i, t, m in slide_rels)
            zf.writestr(f"ppt/slides/_rels/slide{n}.xml.rels", f"<Relationships {REL}>{body}</Relationships>")
        zf.writestr("ppt/notesSlides/notesSlide1.xml",
                    f'<p:notes {P}><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="n"/><p:cNvSpPr/><p:nvPr>'
                    f'<p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Say hello</a:t></a:r></a:p>'
                    f'<a:p><a:r><a:t>Then the </a:t></a:r><a:r><a:t>song</a:t></a:r></a:p></p:txBody></p:sp>'
                    f'</p:spTree></p:cSld></p:notes>')
        zf.write(media / "quiet.mp3", "ppt/media/media1.mp3")
        zf.write(media / "clip.mp4", "ppt/media/media2.mp4")


def test_read_deck_notes_media_and_hidden_slides(tmp_path, media):
    deck_path = tmp_path / "deck.pptx"
    make_deck(deck_path, media)
    deck = pptx_info.read_deck(deck_path)
    assert len(deck.slides) == 2  # hidden slide skipped
    first, third = deck.slides
    assert first.notes == "Say hello\nThen the song"
    assert [(m.kind, m.part, m.autoplay) for m in first.media] == [("audio", "ppt/media/media1.mp3", True)]
    video, linked = third.media
    assert (video.kind, video.part, video.autoplay) == ("video", "ppt/media/media2.mp4", False)
    assert video.rect == pytest.approx((0.25, 0.25, 0.5, 0.5))
    assert (linked.part, linked.external) == (None, "intro.mp3")


def test_quiet_and_loud_end_up_equally_loud(tmp_path, media):
    quiet = loudness.normalize(media / "quiet.mp3", tmp_path / "q", "audio")
    loud = loudness.normalize(media / "loud.wav", tmp_path / "l", "audio")
    levels = [float(loudness.measure(p)["input_i"]) for p in (quiet, loud)]
    assert all(abs(level - loudness.TARGET_LUFS) < 1.5 for level in levels), levels


def test_video_normalized_keeps_picture(tmp_path, media):
    out = loudness.normalize(media / "clip.mp4", tmp_path / "v", "video")
    assert out.suffix == ".mkv" and out.stat().st_size > 0
    assert loudness.video_frame(out, tmp_path / "frame.png")


def test_prepare_deck_end_to_end(tmp_path, media, monkeypatch):
    folder = tmp_path / "talks"
    folder.mkdir()
    deck_path = folder / "Alex.pptx"
    make_deck(deck_path, media)
    (folder / "intro.mp3").write_bytes((media / "quiet.mp3").read_bytes())  # the linked file, put in the folder

    def fake_convert(src, workdir):
        doc = fitz.open()
        for n in range(2):
            doc.new_page(width=960, height=540).insert_text((50, 80), f"Slide {n + 1}")
        doc.save(workdir / "slides.pdf")
        return workdir / "slides.pdf", src

    monkeypatch.setattr(convert, "convert", fake_convert)
    item = Presentation(deck_path, Kind.POWERPOINT)
    result = prepare.prepare(item, tmp_path / "fallback")
    assert result.error is None, result.error
    assert result.page_count == 2
    assert result.slides[0].notes.startswith("Say hello")
    assert [c.autoplay for c in result.slides[0].clips] == [True]
    assert [c.kind for c in result.slides[1].clips] == ["video", "audio"]  # linked file found in folder
    for slide in result.slides:
        for clip in slide.clips:
            assert result.path(clip.file, deck_path).exists()
    assert (Path(result.dir) / "thumb.png").exists()
    assert Path(result.dir).parent == folder / prepare.CACHE_DIRNAME

    cached = prepare.load_cached(item, tmp_path / "fallback")
    assert cached is not None and cached.slides[0].notes == result.slides[0].notes


def test_prepare_video_and_missing_converter(tmp_path, media, monkeypatch):
    video = tmp_path / "Sam.mp4"
    video.write_bytes((media / "clip.mp4").read_bytes())
    result = prepare.prepare(Presentation(video, Kind.VIDEO), tmp_path / "fb")
    assert result.kind == "video" and result.error is None
    assert result.path(result.video, video).suffix == ".mkv"
    assert result.thumb

    monkeypatch.setattr(convert, "_backends", lambda suffix: iter(()))
    deck = tmp_path / "x.key"
    deck.write_bytes(b"PK")
    failed = prepare.prepare(Presentation(deck, Kind.KEYNOTE), tmp_path / "fb")
    assert "Mac" in failed.error
    assert prepare.load_cached(Presentation(deck, Kind.KEYNOTE), tmp_path / "fb") is None  # errors aren't cached
