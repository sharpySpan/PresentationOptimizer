"""Reads what we need out of a .pptx: speaker notes and the audio/video on each slide.

Plain zipfile + ElementTree, so it works the same on every platform.
"""
from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
R_ID = f"{{{NS['r']}}}id"
R_EMBED = f"{{{NS['r']}}}embed"
R_LINK = f"{{{NS['r']}}}link"

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".caf", ".wma", ".ogg", ".flac", ".mid", ".midi"}
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".wmv", ".avi", ".mkv", ".webm", ".mpg", ".mpeg", ".asf", ".flv"}


@dataclass
class MediaRef:
    kind: str                 # "audio" or "video"
    part: str | None          # path inside the zip when embedded
    external: str | None      # file name when only linked (the classic "no sound on the day")
    rect: tuple[float, float, float, float] | None  # x, y, w, h as fractions of the slide
    autoplay: bool


@dataclass
class SlideInfo:
    notes: str = ""
    media: list[MediaRef] = field(default_factory=list)


@dataclass
class DeckInfo:
    slides: list[SlideInfo]      # visible slides only, in show order
    aspect: float                # width / height


def _xml(zf: zipfile.ZipFile, part: str):
    return ElementTree.fromstring(zf.read(part))


def _rels(zf: zipfile.ZipFile, part: str) -> dict[str, tuple[str, bool]]:
    """rId -> (resolved target, is_external)."""
    folder, name = posixpath.split(part)
    rels_part = posixpath.join(folder, "_rels", name + ".rels")
    if rels_part not in zf.namelist():
        return {}
    result = {}
    for rel in _xml(zf, rels_part).findall("rel:Relationship", NS):
        target = rel.get("Target", "")
        external = rel.get("TargetMode") == "External"
        if not external:
            target = posixpath.normpath(posixpath.join(folder, target)) if not target.startswith("/") else target[1:]
        result[rel.get("Id")] = (target, external)
    return result


def _notes_text(zf: zipfile.ZipFile, part: str) -> str:
    root = _xml(zf, part)
    for shape in root.iter(f"{{{NS['p']}}}sp"):
        ph = shape.find("p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is None or ph.get("type") != "body":
            continue
        paragraphs = []
        for para in shape.iterfind(".//a:p", NS):
            paragraphs.append("".join(t.text or "" for t in para.iterfind(".//a:t", NS)))
        return "\n".join(paragraphs).strip()
    return ""


def _autoplay_shape_ids(slide) -> set[str]:
    """Shape ids whose media starts by itself (With/After Previous) instead of on click."""
    ids = set()
    for ctn in slide.iterfind(".//p:timing//p:cTn", NS):
        if ctn.get("presetClass") != "mediacall" or ctn.get("nodeType") not in ("withEffect", "afterEffect"):
            continue
        for target in ctn.iterfind(".//p:spTgt", NS):
            ids.add(target.get("spid"))
    return ids


def _kind_for(name: str, tag_kind: str | None) -> str:
    ext = posixpath.splitext(name.lower())[1]
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return tag_kind or "video"


def read_deck(path) -> DeckInfo:
    with zipfile.ZipFile(path) as zf:
        pres = _xml(zf, "ppt/presentation.xml")
        size = pres.find("p:sldSz", NS)
        cx = int(size.get("cx", 12192000)) if size is not None else 12192000
        cy = int(size.get("cy", 6858000)) if size is not None else 6858000
        pres_rels = _rels(zf, "ppt/presentation.xml")

        slides = []
        for sld_id in pres.iterfind("p:sldIdLst/p:sldId", NS):
            part, _ = pres_rels.get(sld_id.get(R_ID), (None, False))
            if not part or part not in zf.namelist():
                continue
            slide = _xml(zf, part)
            if slide.get("show") == "0":
                continue  # Hidden slide: not exported to PDF either.
            rels = _rels(zf, part)
            info = SlideInfo()
            for target, external in rels.values():
                if target.endswith(".xml") and "/notesSlides/" in target and target in zf.namelist():
                    info.notes = _notes_text(zf, target)
            autoplay = _autoplay_shape_ids(slide)
            for pic in slide.iter(f"{{{NS['p']}}}pic"):
                media = _media_for_pic(pic, rels, cx, cy, autoplay)
                if media:
                    info.media.append(media)
            slides.append(info)
        return DeckInfo(slides, cx / cy if cy else 16 / 9)


def _media_for_pic(pic, rels, cx, cy, autoplay_ids) -> MediaRef | None:
    nvpr = pic.find("p:nvPicPr/p:nvPr", NS)
    if nvpr is None:
        return None
    tag = nvpr.find("a:videoFile", NS)
    tag_kind = "video"
    if tag is None:
        tag = nvpr.find("a:audioFile", NS)
        tag_kind = "audio"
    p14 = nvpr.find(".//p14:media", NS)
    if tag is None and p14 is None:
        return None

    part = external = None
    # The p14:media element points at the embedded copy; a:videoFile may only be a link.
    for rid in ((p14.get(R_EMBED) if p14 is not None else None), (tag.get(R_LINK) if tag is not None else None)):
        if rid and rid in rels:
            target, is_external = rels[rid]
            if is_external:
                external = external or posixpath.basename(target.replace("\\", "/"))
            else:
                part = target
                break
    if part is None and external is None:
        return None

    rect = None
    off = pic.find("p:spPr/a:xfrm/a:off", NS)
    ext = pic.find("p:spPr/a:xfrm/a:ext", NS)
    if off is not None and ext is not None and cx and cy:
        rect = (int(off.get("x", 0)) / cx, int(off.get("y", 0)) / cy, int(ext.get("cx", 0)) / cx, int(ext.get("cy", 0)) / cy)

    cnv = pic.find("p:nvPicPr/p:cNvPr", NS)
    shape_id = cnv.get("id") if cnv is not None else None
    kind = _kind_for(part or external, tag_kind)
    return MediaRef(kind, part, None if part else external, rect, shape_id in autoplay_ids)
