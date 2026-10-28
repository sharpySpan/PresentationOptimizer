"""Making sure sound comes out of the right speakers.

On macOS this talks to CoreAudio directly (via ctypes) to pick the system output device,
because Keynote and PowerPoint always play through the system default output. Volume and
mute go through AppleScript.
"""
from __future__ import annotations

import ctypes
import math
import struct
import subprocess
import tempfile
import wave
from ctypes import POINTER, byref, c_uint32, c_void_p, sizeof
from pathlib import Path

from . import macos


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


_SYSTEM_OBJECT = 1
_DEVICES = _fourcc("dev#")
_DEFAULT_OUTPUT = _fourcc("dOut")
_DEFAULT_SYSTEM_OUTPUT = _fourcc("sOut")
_NAME = _fourcc("lnam")
_STREAMS = _fourcc("stm#")
_SCOPE_GLOBAL = _fourcc("glob")
_SCOPE_OUTPUT = _fourcc("outp")
_ELEMENT_MAIN = 0
_UTF8 = 0x08000100


class _Address(ctypes.Structure):
    _fields_ = [("selector", c_uint32), ("scope", c_uint32), ("element", c_uint32)]


_libs: tuple | None = None


def _load():
    global _libs
    if _libs is None:
        ca = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ca.AudioObjectGetPropertyDataSize.argtypes = [c_uint32, POINTER(_Address), c_uint32, c_void_p, POINTER(c_uint32)]
        ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        ca.AudioObjectGetPropertyData.argtypes = [c_uint32, POINTER(_Address), c_uint32, c_void_p, POINTER(c_uint32), c_void_p]
        ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        ca.AudioObjectSetPropertyData.argtypes = [c_uint32, POINTER(_Address), c_uint32, c_void_p, c_uint32, c_void_p]
        ca.AudioObjectSetPropertyData.restype = ctypes.c_int32
        cf.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_long, c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [c_void_p]
        _libs = (ca, cf)
    return _libs


def _size(obj: int, selector: int, scope: int = _SCOPE_GLOBAL) -> int:
    ca, _ = _load()
    size = c_uint32(0)
    addr = _Address(selector, scope, _ELEMENT_MAIN)
    if ca.AudioObjectGetPropertyDataSize(obj, byref(addr), 0, None, byref(size)) != 0:
        return 0
    return size.value


def _device_ids() -> list[int]:
    ca, _ = _load()
    size = _size(_SYSTEM_OBJECT, _DEVICES)
    count = size // sizeof(c_uint32)
    if not count:
        return []
    ids = (c_uint32 * count)()
    size_ref = c_uint32(size)
    addr = _Address(_DEVICES, _SCOPE_GLOBAL, _ELEMENT_MAIN)
    if ca.AudioObjectGetPropertyData(_SYSTEM_OBJECT, byref(addr), 0, None, byref(size_ref), ids) != 0:
        return []
    return list(ids)


def _device_name(device: int) -> str:
    ca, cf = _load()
    ref = c_void_p()
    size = c_uint32(sizeof(ref))
    addr = _Address(_NAME, _SCOPE_GLOBAL, _ELEMENT_MAIN)
    if ca.AudioObjectGetPropertyData(device, byref(addr), 0, None, byref(size), byref(ref)) != 0 or not ref.value:
        return f"Device {device}"
    buf = ctypes.create_string_buffer(512)
    ok = cf.CFStringGetCString(ref, buf, len(buf), _UTF8)
    cf.CFRelease(ref)
    return buf.value.decode("utf-8", "replace") if ok else f"Device {device}"


def _default_output() -> int:
    ca, _ = _load()
    device = c_uint32(0)
    size = c_uint32(sizeof(device))
    addr = _Address(_DEFAULT_OUTPUT, _SCOPE_GLOBAL, _ELEMENT_MAIN)
    ca.AudioObjectGetPropertyData(_SYSTEM_OBJECT, byref(addr), 0, None, byref(size), byref(device))
    return device.value


def list_output_devices() -> list[str]:
    """Names of every device that can play sound (speakers, HDMI, AirPlay, USB...)."""
    if not macos.IS_MAC:
        return []
    try:
        return [_device_name(d) for d in _device_ids() if _size(d, _STREAMS, _SCOPE_OUTPUT) > 0]
    except OSError:
        return []


def current_output_device() -> str | None:
    if not macos.IS_MAC:
        return None
    try:
        return _device_name(_default_output())
    except OSError:
        return None


def set_output_device(name: str) -> bool:
    if not macos.IS_MAC:
        return False
    try:
        ca, _ = _load()
        for device in _device_ids():
            if _size(device, _STREAMS, _SCOPE_OUTPUT) > 0 and _device_name(device) == name:
                value = c_uint32(device)
                ok = True
                for selector in (_DEFAULT_OUTPUT, _DEFAULT_SYSTEM_OUTPUT):
                    addr = _Address(selector, _SCOPE_GLOBAL, _ELEMENT_MAIN)
                    ok &= ca.AudioObjectSetPropertyData(_SYSTEM_OBJECT, byref(addr), 0, None, sizeof(value), byref(value)) == 0
                return ok
    except OSError:
        pass
    return False


def set_volume(percent: int) -> bool:
    """Set the system output volume and make sure it is not muted."""
    if not macos.IS_MAC:
        return False
    percent = max(0, min(100, int(percent)))
    try:
        macos.osascript(f"set volume output volume {percent}\nset volume output muted false", timeout=5)
        return True
    except macos.AppleScriptError:
        return False


def apply(device: str | None, volume: int | None) -> None:
    """Called before the show and before each presentation; cheap and idempotent.

    Re-applying each time matters: plugging/unplugging HDMI makes macOS switch outputs.
    """
    if device:
        set_output_device(device)
    if volume is not None:
        set_volume(volume)


def _test_tone() -> Path:
    """A two-note chime: first in the left speaker, then in the right."""
    path = Path(tempfile.gettempdir()) / "showrunner-test-tone.wav"
    if path.exists():
        return path
    rate = 44100
    frames = bytearray()
    for freq, channel in ((660.0, 0), (880.0, 1)):
        n = int(rate * 0.6)
        for i in range(n):
            envelope = min(1.0, i / 800) * math.exp(-3.0 * i / n)
            sample = int(0.5 * 32767 * envelope * math.sin(2 * math.pi * freq * i / rate))
            left, right = (sample, 0) if channel == 0 else (0, sample)
            frames += struct.pack("<hh", left, right)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))
    return path


def play_test_tone() -> bool:
    """Play through the system default output - the same route Keynote/PowerPoint use."""
    path = _test_tone()
    if macos.IS_MAC:
        subprocess.Popen(["afplay", str(path)])
        return True
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except ImportError:
        return False
