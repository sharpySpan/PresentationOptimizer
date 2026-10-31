"""Loudness normalization with ffmpeg (EBU R128), so every clip in every talk plays equally loud.

ffmpeg comes from the imageio-ffmpeg wheel, which ships a static binary for macOS, Windows and Linux.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import system

TARGET_LUFS = -14.0
TRUE_PEAK = -1.5
LRA = 11.0


class MediaError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str], timeout: float = 1800) -> subprocess.CompletedProcess:
    try:
        return system.run([ffmpeg_exe(), "-hide_banner", "-nostdin", *args], timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"took longer than {timeout // 60:.0f} minutes") from exc
    except OSError as exc:
        raise MediaError(str(exc)) from exc


def video_codec(path: Path) -> str | None:
    """Codec name of the first video stream, e.g. 'h264', 'hevc', 'prores'."""
    result = _run(["-i", str(path)], timeout=60)
    match = re.search(r"Stream #\S+.*?: Video: (\w+)", result.stderr)
    return match.group(1).lower() if match else None


def measure(path: Path) -> dict | None:
    """First loudnorm pass. None when the file has no (audible) audio."""
    result = _run(["-i", str(path), "-vn", "-af",
                   f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA={LRA}:print_format=json", "-f", "null", "-"])
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr)
    if not match:
        return None
    data = json.loads(match.group(0))
    try:
        if float(data["input_i"]) < -70:  # -inf / digital silence
            return None
    except ValueError:
        return None
    return data


def _loudnorm_filter(m: dict) -> str:
    return (f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA={LRA}"
            f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def normalize(src: Path, dest_stem: Path, kind: str) -> Path:
    """Write a loudness-normalized copy. kind is 'audio' or 'video'. Returns the new file.

    H.264 video is copied untouched (fast). Anything else (iPhone HEVC, ProRes, WMV, ...) is
    re-encoded to H.264, the one codec every computer plays without extra codec packs.
    """
    m = measure(src)
    audio_args = ["-af", _loudnorm_filter(m), "-ar", "48000", "-c:a", "aac", "-b:a", "192k"] if m else ["-c:a", "aac", "-b:a", "192k"]
    if kind == "audio":
        dest = dest_stem.with_suffix(".m4a")
        result = _run(["-y", "-i", str(src), "-vn", "-map", "0:a:0", *audio_args, str(dest)])
        if result.returncode != 0:
            raise MediaError(_tail(result.stderr))
        return dest

    dest = dest_stem.with_suffix(".mkv")
    maps = ["-map", "0:v:0", "-map", "0:a:0?"]
    result = None
    if video_codec(src) == "h264":
        result = _run(["-y", "-i", str(src), *maps, "-c:v", "copy", *audio_args, str(dest)])
    if result is None or result.returncode != 0:
        # Max 1080p: a beamer never shows more, and 4K phone video would take ages to re-encode.
        result = _run(["-y", "-i", str(src), *maps, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                       "-vf", "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease:"
                              "force_divisible_by=2",
                       "-pix_fmt", "yuv420p", *audio_args, str(dest)])
        if result.returncode != 0:
            raise MediaError(_tail(result.stderr))
    return dest


def video_frame(src: Path, dest: Path, at_seconds: float = 1.0) -> bool:
    """Grab one frame as a preview image."""
    try:
        return _video_frame(src, dest, at_seconds)
    except MediaError:
        return False


def _video_frame(src: Path, dest: Path, at_seconds: float) -> bool:
    result = _run(["-y", "-ss", str(at_seconds), "-i", str(src), "-frames:v", "1", "-vf", "scale=640:-2", str(dest)], timeout=60)
    if result.returncode != 0 or not dest.exists():
        result = _run(["-y", "-i", str(src), "-frames:v", "1", "-vf", "scale=640:-2", str(dest)], timeout=60)
    return dest.exists()


def _tail(stderr: str) -> str:
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "ffmpeg failed"
