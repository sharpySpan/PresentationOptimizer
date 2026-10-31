"""Animated GIFs: every PDF export freezes them, so Showrunner plays them on top of the slide."""
import os
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from showrunner import convert, pptx_info, prepare  # noqa: E402
from showrunner.library import Kind, Presentation  # noqa: E402
from showrunner.pdf import fitz  # noqa: E402
from test_pipeline import P, REL, ffmpeg  # noqa: E402

CX, CY = 12192000, 6858000


@pytest.fixture(scope="module")
def gifs(tmp_path_factory):
    d = tmp_path_factory.mktemp("gifs")
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1", str(d / "moving.gif"))
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1", "-frames:v", "1", str(d / "still.gif"))
    return d


def _pic(shape_id, rid, x, y, w, h):
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="g"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rid}"/></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm></p:spPr></p:pic>')


def make_gif_deck(path, gifs):
    """Slide 1: an animated GIF inside a group, plus a still GIF and a PNG-less blip that must be ignored."""
    # Group at (CX/2, 0) sized CX/2 x CY/2, child space 0..CX x 0..CY: halves everything inside.
    group = (f'<p:grpSp><p:nvGrpSpPr><p:cNvPr id="2" name="grp"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
             f'<p:grpSpPr><a:xfrm><a:off x="{CX // 2}" y="0"/><a:ext cx="{CX // 2}" cy="{CY // 2}"/>'
             f'<a:chOff x="0" y="0"/><a:chExt cx="{CX}" cy="{CY}"/></a:xfrm></p:grpSpPr>'
             f'{_pic(3, "rId2", 0, 0, CX, CY)}</p:grpSp>')
    slide = (f'<p:sld {P}><p:cSld><p:spTree>{group}{_pic(4, "rId3", 0, CY // 2, CX // 4, CY // 4)}'
             f'</p:spTree></p:cSld></p:sld>')
    rels = (f'<Relationships {REL}><Relationship Id="rId2" Type="image" Target="../media/image1.gif"/>'
            f'<Relationship Id="rId3" Type="image" Target="../media/image2.gif"/></Relationships>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ppt/presentation.xml", f'<p:presentation {P}><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
                                            f'</p:sldIdLst><p:sldSz cx="{CX}" cy="{CY}"/></p:presentation>')
        zf.writestr("ppt/_rels/presentation.xml.rels",
                    f'<Relationships {REL}><Relationship Id="rId1" Type="slide" Target="slides/slide1.xml"/>'
                    f'</Relationships>')
        zf.writestr("ppt/slides/slide1.xml", slide)
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", rels)
        zf.write(gifs / "moving.gif", "ppt/media/image1.gif")
        zf.write(gifs / "still.gif", "ppt/media/image2.gif")


def test_read_deck_finds_gifs_with_group_positions(tmp_path, gifs):
    make_gif_deck(tmp_path / "g.pptx", gifs)
    (slide,) = pptx_info.read_deck(tmp_path / "g.pptx").slides
    grouped, still = slide.media
    assert (grouped.kind, grouped.part, grouped.autoplay) == ("gif", "ppt/media/image1.gif", True)
    assert grouped.rect == pytest.approx((0.5, 0.0, 0.5, 0.5))
    assert still.rect == pytest.approx((0.0, 0.5, 0.25, 0.25))


def test_only_animated_gifs_are_kept_and_played(tmp_path, gifs, monkeypatch):
    app = QApplication.instance() or QApplication([])
    deck = tmp_path / "Gif.pptx"
    make_gif_deck(deck, gifs)

    def fake_convert(src, workdir):
        doc = fitz.open()
        doc.new_page(width=960, height=540)
        doc.save(workdir / "slides.pdf")
        return workdir / "slides.pdf", src

    monkeypatch.setattr(convert, "convert", fake_convert)
    result = prepare.prepare(Presentation(deck, Kind.POWERPOINT), tmp_path / "fb")
    assert result.error is None, result.error
    (clip,) = result.slides[0].clips
    assert clip.kind == "gif" and result.path(clip.file, deck).exists()

    from showrunner.show import StageView

    stage = StageView()
    stage.resize(960, 540)
    stage.show()
    movie = stage.add_gif(result.path(clip.file, deck), tuple(clip.rect))
    seen = set()
    for _ in range(60):
        app.processEvents()
        seen.add(movie.currentFrameNumber())
        movie.jumpToNextFrame()
    assert len(seen) > 1  # it really animates
    item = stage.gifs[0][0]
    box = item.sceneBoundingRect()
    assert box.x() == pytest.approx(480, abs=2) and box.width() == pytest.approx(480, abs=2)
    stage.clear_videos()
    assert not stage.gifs
    stage.close()
    stage.deleteLater()
    app.processEvents()
