"""One sound control for the whole show.

Every clip is loudness-normalized while preparing (see loudness.py), and every player gets its
QAudioOutput from this bus. That gives:
  - the same level across all talks,
  - one master slider from 0% to 200%,
  - one mute (Alt+S),
  - one output device (HDMI, USB speaker, ...).

To make 200% possible without distortion, 100% plays normalized audio at half amplitude
(-6 dB) and 200% plays it at full scale. Set the speakers/amp once so 100% sounds right.
"""
from __future__ import annotations

import math
import struct
import tempfile
import wave
import weakref
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer

MAX_GAIN = 2.0


class AudioBus(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gain = 1.0
        self.muted = False
        self.device: QAudioDevice = QMediaDevices.defaultAudioOutput()
        self._device_id: bytes | None = None   # None = follow the system default
        self._outputs: "weakref.WeakSet[QAudioOutput]" = weakref.WeakSet()
        self._test_player: QMediaPlayer | None = None
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._devices_changed)

    # --- outputs ---------------------------------------------------------

    def create_output(self, parent: QObject) -> QAudioOutput:
        output = QAudioOutput(self.device, parent)
        self._apply(output)
        self._outputs.add(output)
        return output

    def _apply(self, output: QAudioOutput):
        output.setDevice(self.device)
        output.setVolume(self.gain / MAX_GAIN)
        output.setMuted(self.muted)

    def _apply_all(self):
        for output in list(self._outputs):
            self._apply(output)
        self.changed.emit()

    # --- controls --------------------------------------------------------

    @property
    def percent(self) -> int:
        return round(self.gain * 100)

    def set_percent(self, percent: int):
        gain = max(0.0, min(MAX_GAIN, percent / 100))
        if abs(gain - self.gain) > 1e-6:
            self.gain = gain
            self._apply_all()

    def nudge(self, delta_percent: int):
        self.set_percent(self.percent + delta_percent)

    def set_muted(self, muted: bool):
        if muted != self.muted:
            self.muted = muted
            self._apply_all()

    def toggle_mute(self):
        self.set_muted(not self.muted)

    # --- devices ---------------------------------------------------------

    @staticmethod
    def devices() -> list[QAudioDevice]:
        return list(QMediaDevices.audioOutputs())

    def device_name(self) -> str:
        return "System default" if self._device_id is None else self.device.description()

    def set_device_by_name(self, name: str | None):
        """None/'' = follow the system default output."""
        match = next((d for d in self.devices() if d.description() == name), None) if name else None
        self._device_id = bytes(match.id()) if match else None
        self.device = match or QMediaDevices.defaultAudioOutput()
        self._apply_all()

    def _devices_changed(self):
        # HDMI plugged/unplugged: re-find our device, or fall back to the new default.
        match = next((d for d in self.devices() if bytes(d.id()) == self._device_id), None)
        self.device = match or QMediaDevices.defaultAudioOutput()
        self._apply_all()

    # --- test sound ------------------------------------------------------

    def play_test_tone(self):
        """Chime left then right, through the bus (so slider, mute and device all apply)."""
        if self._test_player is None:
            self._test_player = QMediaPlayer(self)
            self._test_player.setAudioOutput(self.create_output(self._test_player))
            self._test_player.setSource(QUrl.fromLocalFile(str(_test_tone())))
        self._test_player.setPosition(0)
        self._test_player.play()


def _test_tone() -> Path:
    path = Path(tempfile.gettempdir()) / "showrunner-test-tone-v2.wav"
    if path.exists():
        return path
    rate = 48000
    frames = bytearray()
    for freq, channel in ((660.0, 0), (880.0, 1)):
        n = int(rate * 0.6)
        for i in range(n):
            envelope = min(1.0, i / 800) * math.exp(-3.0 * i / n)
            # Peak around -1 dBFS, roughly the level of normalized clips.
            sample = int(0.89 * 32767 * envelope * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<hh", *((sample, 0) if channel == 0 else (0, sample)))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))
    return path
