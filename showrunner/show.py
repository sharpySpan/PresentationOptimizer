"""The running show.

Two windows:
  - AudienceWindow on the beamer: 'Up next' screens, slides, videos. Nothing else.
  - ConsoleWindow on the monitor: current + next slide, speaker notes, timer, the sound
    controls (0-200%, mute, output) and the running order.

Keys work in both windows (arrow keys, clickers, Alt+S for mute).
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPixmap, QScreen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGraphicsScene, QGraphicsView, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QSlider, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import system
from .audio import AudioBus
from .library import Presentation
from .prepare import Prepared, prepare

NEXT_KEYS = {Qt.Key_Right, Qt.Key_Down, Qt.Key_PageDown, Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_N}
PREV_KEYS = {Qt.Key_Left, Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Backspace, Qt.Key_P}
ACCENT = "#f5b841"
END_MARGIN_MS = 300


@dataclass
class ShowSettings:
    audience_screen: QScreen | None
    console_screen: QScreen | None
    windowed: bool          # test mode: both views as normal windows
    talk_minutes: int       # 0 = no time limit
    auto_start: int         # seconds before the next talk starts by itself, 0 = off
    cache_root: Path


class Mode(Enum):
    IDLE = auto()
    UP_NEXT = auto()
    TALK = auto()
    END = auto()


# --- rendering -----------------------------------------------------------------

class SlideSource:
    """Pixmaps for one prepared presentation, cached by page and size."""

    def __init__(self, item: Presentation, prepared: Prepared):
        self.item = item
        self.prepared = prepared
        self._doc = None
        self._image: QImage | None = None
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()
        if prepared.pdf:
            from .pdf import open_pdf

            self._doc = open_pdf(prepared.path(prepared.pdf, item.path))
        elif prepared.image:
            self._image = QImage(str(prepared.path(prepared.image, item.path)))
        elif prepared.thumb:
            self._image = QImage(str(prepared.path(prepared.thumb, item.path)))

    @property
    def aspect(self) -> float:
        if self._image is not None and not self._image.isNull() and self.prepared.kind == "image":
            return self._image.width() / max(1, self._image.height())
        return self.prepared.aspect

    def pixmap(self, page: int, width: int, height: int, dpr: float = 1.0) -> QPixmap:
        key = (page, width, height, dpr)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        pixmap = QPixmap()
        if self._doc is not None and 0 <= page < self._doc.page_count:
            from .pdf import render

            samples, w, h, stride = render(self._doc, page, int(width * dpr), int(height * dpr))
            pixmap = QPixmap.fromImage(QImage(samples, w, h, stride, QImage.Format_RGB888).copy())
        elif self._image is not None and not self._image.isNull():
            pixmap = QPixmap.fromImage(self._image.scaled(int(width * dpr), int(height * dpr), Qt.KeepAspectRatio,
                                                          Qt.SmoothTransformation))
        pixmap.setDevicePixelRatio(dpr)
        self._cache[key] = pixmap
        while len(self._cache) > 12:
            self._cache.popitem(last=False)
        return pixmap

    def close(self):
        if self._doc is not None:
            self._doc.close()
            self._doc = None


class PixmapBox(QWidget):
    """Draws a pixmap scaled to fit, on black."""

    def __init__(self, placeholder: str = ""):
        super().__init__()
        self.pixmap: QPixmap | None = None
        self.placeholder = placeholder
        self.setMinimumSize(160, 90)

    def set_pixmap(self, pixmap: QPixmap | None, placeholder: str = ""):
        self.pixmap = pixmap
        self.placeholder = placeholder
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000"))
        if self.pixmap is not None and not self.pixmap.isNull():
            size = self.pixmap.deviceIndependentSize().scaled(self.size().toSizeF(), Qt.KeepAspectRatio)
            x = (self.width() - size.width()) / 2
            y = (self.height() - size.height()) / 2
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(x, y, size.width(), size.height()), self.pixmap, QRectF(self.pixmap.rect()))
        elif self.placeholder:
            painter.setPen(QColor("#888"))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.placeholder)


# --- audience (beamer) -----------------------------------------------------------

class MessagePage(QWidget):
    def __init__(self):
        super().__init__()
        self.kicker, self.title, self.meta, self.error, self.hint = (QLabel() for _ in range(5))
        layout = QVBoxLayout(self)
        layout.addStretch(3)
        for label in (self.kicker, self.title, self.meta):
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            layout.addWidget(label)
        layout.addSpacing(30)
        self.error.setAlignment(Qt.AlignCenter)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        layout.addStretch(4)
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)
        layout.addSpacing(30)

    def resizeEvent(self, event):
        h = max(self.height(), 300)
        self.kicker.setStyleSheet(f"color:{ACCENT}; font-size:{int(h * 0.035)}px; font-weight:600; letter-spacing:4px;")
        self.title.setStyleSheet(f"color:white; font-size:{int(h * 0.09)}px; font-weight:700;")
        self.meta.setStyleSheet(f"color:#9a9a9a; font-size:{int(h * 0.03)}px;")
        self.error.setStyleSheet(f"color:#ff6b6b; font-size:{int(h * 0.028)}px;")
        self.hint.setStyleSheet(f"color:#555; font-size:{int(h * 0.022)}px;")
        super().resizeEvent(event)

    def set(self, kicker: str, title: str, meta: str = "", hint: str = "", error: str = ""):
        self.kicker.setText(kicker.upper())
        self.title.setText(title)
        self.meta.setText(meta)
        self.error.setText(error)
        self.error.setVisible(bool(error))
        self.hint.setText(hint)


class StageView(QGraphicsView):
    """A slide picture with live video items placed where the video sits on the slide."""

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor("black"))
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setFocusPolicy(Qt.NoFocus)
        self.pix_item = self.scene().addPixmap(QPixmap())
        self.pix_item.setTransformationMode(Qt.SmoothTransformation)
        self.aspect = 16 / 9
        self.videos: list[tuple[object, tuple[float, float, float, float]]] = []
        self.on_resize = None

    def slide_rect(self) -> QRectF:
        w, h = self.viewport().width(), self.viewport().height()
        if w / max(h, 1) > self.aspect:
            sw, sh = h * self.aspect, h
        else:
            sw, sh = w, w / self.aspect
        return QRectF((w - sw) / 2, (h - sh) / 2, sw, sh)

    def set_slide(self, pixmap: QPixmap | None, aspect: float):
        self.aspect = aspect
        self.pix_item.setPixmap(pixmap or QPixmap())
        self._layout()

    def add_video(self, rect: tuple[float, float, float, float]):
        from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

        item = QGraphicsVideoItem()
        self.scene().addItem(item)
        self.videos.append((item, rect))
        self._layout()
        return item

    def clear_videos(self):
        for item, _ in self.videos:
            self.scene().removeItem(item)
            item.deleteLater()
        self.videos.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout()
        if self.on_resize:
            self.on_resize()

    def _layout(self):
        self.scene().setSceneRect(0, 0, self.viewport().width(), self.viewport().height())
        r = self.slide_rect()
        pixmap = self.pix_item.pixmap()
        if not pixmap.isNull():
            self.pix_item.setPos(r.topLeft())
            self.pix_item.setScale(r.width() / max(1.0, pixmap.deviceIndependentSize().width()))
        for item, (x, y, w, h) in self.videos:
            item.setPos(r.x() + x * r.width(), r.y() + y * r.height())
            item.setSize(QSizeF(w * r.width(), h * r.height()))


class Toast(QLabel):
    """Volume/mute feedback on the beamer, only used when there is no console screen."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("background:rgba(0,0,0,190); color:white; font-size:26px; padding:14px 26px; border-radius:12px;")
        self.hide()
        self.timer = QTimer(self, singleShot=True, interval=1400)
        self.timer.timeout.connect(self.hide)

    def flash(self, text: str):
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 60)
        self.show()
        self.raise_()
        self.timer.start()


class AudienceWindow(QWidget):
    def __init__(self, controller: "ShowController"):
        super().__init__(None, Qt.Window if controller.settings.windowed else Qt.Window | Qt.FramelessWindowHint)
        self.controller = controller
        self.setWindowTitle("Showrunner - beamer")
        self.setStyleSheet("background:black;")
        self.stack = QStackedWidget(self)
        self.message = MessagePage()
        self.stage = StageView()
        self.black = QWidget()
        for page in (self.message, self.stage, self.black):
            self.stack.addWidget(page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)
        self.toast = Toast(self)
        if not controller.settings.windowed:
            self.setCursor(Qt.BlankCursor)

    def mousePressEvent(self, event):
        self.controller.handle_key(Qt.Key_Left if event.button() == Qt.RightButton else Qt.Key_Right)

    def closeEvent(self, event):
        self.controller.stop()
        super().closeEvent(event)


# --- console (monitor) -------------------------------------------------------------

def _button(text: str, tip: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setFocusPolicy(Qt.NoFocus)  # Keys must always reach the show, never a button.
    button.setToolTip(tip)
    return button


class ConsoleWindow(QWidget):
    def __init__(self, controller: "ShowController", bus: AudioBus):
        super().__init__(None, Qt.Window)
        self.controller = controller
        self.bus = bus
        self.setWindowTitle("Showrunner - console")
        self.setStyleSheet("""
            QWidget { background:#15171a; color:#e8e8e8; font-size:14px; }
            QPushButton { background:#2a2e33; border:1px solid #3a3f45; border-radius:6px; padding:7px 12px; }
            QPushButton:hover { background:#353a40; }
            QPushButton:checked { background:#c0392b; border-color:#e74c3c; color:white; }
            QListWidget, QTextBrowser { background:#1d2024; border:1px solid #2c3035; border-radius:6px; }
            QListWidget::item:selected { background:#3b4048; }
            QComboBox { background:#2a2e33; border:1px solid #3a3f45; border-radius:6px; padding:5px; }
            QSlider::groove:horizontal { height:8px; background:#2c3035; border-radius:4px; }
            QSlider::sub-page:horizontal { background:%s; border-radius:4px; }
            QSlider::handle:horizontal { background:white; width:22px; margin:-8px 0; border-radius:11px; }
            QLabel#dim { color:#8a8f96; }
        """ % ACCENT)

        # Header
        self.now_label = QLabel()
        self.now_label.setStyleSheet("font-size:22px; font-weight:700;")
        self.position_label = QLabel()
        self.position_label.setObjectName("dim")
        self.timer_label = QLabel("00:00")
        self.timer_label.setStyleSheet("font-size:30px; font-weight:700; font-family:monospace;")
        self.clock_label = QLabel()
        self.clock_label.setObjectName("dim")
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.addWidget(self.now_label)
        title_col.addWidget(self.position_label)
        header.addLayout(title_col, 1)
        header.addWidget(self.clock_label)
        header.addSpacing(20)
        header.addWidget(self.timer_label)

        # Slides + notes
        self.current = PixmapBox()
        self.next_box = PixmapBox()
        self.next_caption = QLabel("Next")
        self.next_caption.setObjectName("dim")
        self.notes = QTextBrowser()
        self.notes.setFocusPolicy(Qt.NoFocus)
        self.notes_size = 20
        smaller = _button("A−", "Smaller notes")
        smaller.clicked.connect(lambda: self._notes_font(-2))
        bigger = _button("A+", "Bigger notes")
        bigger.clicked.connect(lambda: self._notes_font(2))
        notes_head = QHBoxLayout()
        notes_label = QLabel("Speaker notes")
        notes_label.setObjectName("dim")
        notes_head.addWidget(notes_label, 1)
        notes_head.addWidget(smaller)
        notes_head.addWidget(bigger)

        self.clips_row = QHBoxLayout()
        self.clips_label = QLabel("")
        self.clips_label.setObjectName("dim")
        clips_wrap = QHBoxLayout()
        clips_wrap.addWidget(self.clips_label)
        clips_wrap.addLayout(self.clips_row, 1)

        left = QVBoxLayout()
        left.addWidget(self.current, 5)
        left.addLayout(clips_wrap)
        left.addLayout(notes_head)
        left.addWidget(self.notes, 3)

        # Sound
        self.volume = QSlider(Qt.Horizontal, minimum=0, maximum=200, value=bus.percent)
        self.volume.setFocusPolicy(Qt.NoFocus)
        self.volume.setPageStep(10)
        self.volume.valueChanged.connect(bus.set_percent)
        self.volume_label = QLabel()
        self.volume_label.setMinimumWidth(64)
        self.volume_label.setStyleSheet("font-size:22px; font-weight:700;")
        self.mute = _button("Mute  (Alt+S)")
        self.mute.setCheckable(True)
        self.mute.clicked.connect(bus.set_muted)
        reset = _button("100%")
        reset.clicked.connect(lambda: bus.set_percent(100))
        test = _button("Test sound")
        test.clicked.connect(bus.play_test_tone)
        self.device = QComboBox()
        self.device.setFocusPolicy(Qt.NoFocus)
        self.device.addItem("System default output", "")
        for device in bus.devices():
            self.device.addItem(device.description(), device.description())
        self.device.setCurrentIndex(max(0, self.device.findData(bus.device_name() if bus.device_name() != "System default" else "")))
        self.device.currentIndexChanged.connect(lambda _: bus.set_device_by_name(self.device.currentData()))
        sound = QGridLayout()
        sound_title = QLabel("Sound - all talks levelled to the same loudness")
        sound_title.setObjectName("dim")
        sound.addWidget(sound_title, 0, 0, 1, 3)
        sound.addWidget(self.volume, 1, 0, 1, 2)
        sound.addWidget(self.volume_label, 1, 2)
        sound.addWidget(self.mute, 2, 0)
        sound.addWidget(reset, 2, 1)
        sound.addWidget(test, 2, 2)
        sound.addWidget(self.device, 3, 0, 1, 3)
        bus.changed.connect(self.sync_sound)
        self.sync_sound()

        # Running order
        self.queue = QListWidget()
        self.queue.setFocusPolicy(Qt.NoFocus)
        self.queue.itemDoubleClicked.connect(lambda item: controller.jump_to(self.queue.row(item)))
        queue_label = QLabel("Running order (double-click to jump)")
        queue_label.setObjectName("dim")

        # Controls
        prev_b = _button("◀  Back")
        prev_b.clicked.connect(controller.previous)
        next_b = _button("Next  ▶")
        next_b.setStyleSheet(f"background:{ACCENT}; color:black; font-weight:700;")
        next_b.clicked.connect(controller.next)
        self.black_b = _button("Black (B)")
        self.black_b.setCheckable(True)
        self.black_b.clicked.connect(controller.toggle_black)
        end_b = _button("End talk (Esc)")
        end_b.clicked.connect(controller.end_talk)
        exit_b = _button("Exit show")
        exit_b.clicked.connect(controller.stop)
        controls = QGridLayout()
        controls.addWidget(prev_b, 0, 0)
        controls.addWidget(next_b, 0, 1)
        controls.addWidget(self.black_b, 1, 0)
        controls.addWidget(end_b, 1, 1)
        controls.addWidget(exit_b, 2, 0, 1, 2)

        right = QVBoxLayout()
        right.addWidget(self.next_caption)
        right.addWidget(self.next_box, 2)
        right.addLayout(sound)
        right.addSpacing(8)
        right.addWidget(queue_label)
        right.addWidget(self.queue, 3)
        right.addLayout(controls)
        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setFixedWidth(400)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addWidget(right_widget)
        hint = QLabel("→ / Space / clicker next  ·  ← back  ·  Alt+S mute  ·  Alt+↑ / Alt+↓ volume  ·  B black  ·  Esc end talk")
        hint.setObjectName("dim")
        root = QVBoxLayout(self)
        root.addLayout(header)
        root.addLayout(body, 1)
        root.addWidget(hint)
        self._notes_font(0)

    def _notes_font(self, delta: int):
        self.notes_size = max(12, min(48, self.notes_size + delta))
        self.notes.setStyleSheet(f"font-size:{self.notes_size}px;")

    def sync_sound(self):
        self.volume.blockSignals(True)
        self.volume.setValue(self.bus.percent)
        self.volume.blockSignals(False)
        self.volume_label.setText(f"{self.bus.percent}%")
        self.mute.setChecked(self.bus.muted)
        self.mute.setText("MUTED  (Alt+S)" if self.bus.muted else "Mute  (Alt+S)")

    def set_clips(self, clips: list, players: list):
        while self.clips_row.count():
            widget = self.clips_row.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        self.clips_label.setText("Media on this slide:" if clips else "")
        for n, (clip, player) in enumerate(zip(clips, players), 1):
            icon = "🎬" if clip.kind == "video" else "🔊"
            button = _button(f"{icon} {n} ▶/❚❚", "Play / pause")
            button.clicked.connect(player.toggle)
            self.clips_row.addWidget(button)
        self.clips_row.addStretch(1)

    def closeEvent(self, event):
        self.controller.stop()
        super().closeEvent(event)


# --- media ----------------------------------------------------------------------

class ClipPlayer(QObject):
    ended = Signal()

    def __init__(self, path: Path, bus: AudioBus, video_item=None, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QMediaPlayer

        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(bus.create_output(self.player))
        if video_item is not None:
            self.player.setVideoOutput(video_item)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.positionChanged.connect(self._position)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.started = False
        self._ended = False

    def play(self):
        if self._ended:
            self.player.setPosition(0)
            self._ended = False
        self.started = True
        self.player.play()

    def toggle(self):
        from PySide6.QtMultimedia import QMediaPlayer

        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.play()

    def restart(self):
        self.player.setPosition(0)
        self._ended = False
        self.play()

    def _position(self, position: int):
        # Pause just before the end: Qt 6.12's FFmpeg backend can lock up the whole app when a
        # stream plays into end-of-media. Losing the last fraction of a second is inaudible.
        duration = self.player.duration()
        if not self._ended and duration > 0 and position >= duration - END_MARGIN_MS:
            self._ended = True
            self.player.pause()
            self.ended.emit()

    def _status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.EndOfMedia and not self._ended:
            self._ended = True
            self.ended.emit()

    def close(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.deleteLater()


# --- controller -------------------------------------------------------------------

class ShowController(QObject):
    finished = Signal(int)  # index of the presentation we stopped at

    def __init__(self, items: list[Presentation], prepared: dict[str, Prepared], bus: AudioBus,
                 settings: ShowSettings, parent=None):
        super().__init__(parent)
        self.items = items
        self.prepared = prepared
        self.bus = bus
        self.settings = settings
        self.mode = Mode.IDLE
        self.index = 0
        self.page = 0
        self.error = ""
        self.source: SlideSource | None = None
        self.players: list[ClipPlayer] = []
        self.pending_clicks: list[ClipPlayer] = []
        self.talk_started = 0.0
        self.awake: system.KeepAwake | None = None

        self.audience = AudienceWindow(self)
        self.audience.stage.on_resize = self._refresh_audience_slide
        same_screen = settings.audience_screen is settings.console_screen
        self.console = ConsoleWindow(self, bus) if (settings.windowed or not same_screen) else None
        self.bus.changed.connect(self._sound_feedback)

        self.ticker = QTimer(self, interval=500)
        self.ticker.timeout.connect(self._tick)
        self.countdown = 0
        self.countdown_timer = QTimer(self, interval=1000)
        self.countdown_timer.timeout.connect(self._countdown_tick)

    # --- lifecycle ---------------------------------------------------------

    def start(self, index: int = 0):
        self.index = max(0, min(index, len(self.items) - 1))
        self._place(self.audience, self.settings.audience_screen, fullscreen=not self.settings.windowed)
        if self.console:
            self._place(self.console, self.settings.console_screen, fullscreen=False)
        QApplication.instance().installEventFilter(self)
        self.awake = system.KeepAwake()
        self.ticker.start()
        self.show_up_next()

    def _place(self, window: QWidget, screen: QScreen | None, fullscreen: bool):
        if screen is not None:
            window.setScreen(screen)
            geo = screen.availableGeometry() if not fullscreen else screen.geometry()
        else:
            geo = QApplication.primaryScreen().availableGeometry()
        if fullscreen:
            window.setGeometry(geo)
            window.show()
        elif window is self.audience:
            window.resize(960, 540)
            window.move(geo.x() + 40, geo.y() + 40)
            window.show()
        else:
            window.setGeometry(geo)
            window.showMaximized()
        window.raise_()
        window.activateWindow()

    def stop(self):
        if self.mode is Mode.IDLE:
            return
        self.mode = Mode.IDLE
        QApplication.instance().removeEventFilter(self)
        self.ticker.stop()
        self.countdown_timer.stop()
        self._close_talk()
        system.set_kiosk(False)
        if self.awake:
            self.awake.release()
        self.bus.changed.disconnect(self._sound_feedback)
        for window in (self.audience, self.console):
            if window is not None:
                window.hide()
                window.deleteLater()
        self.finished.emit(min(self.index, len(self.items) - 1))

    # --- the running order ------------------------------------------------

    def show_up_next(self):
        self._close_talk()
        self.mode = Mode.END if self.index >= len(self.items) else Mode.UP_NEXT
        msg = self.audience.message
        if self.mode is Mode.END:
            msg.set("That's a wrap", f"All {len(self.items)} presentations done", hint="← back to the last one")
        else:
            item = self.items[self.index]
            msg.set("Up first" if self.index == 0 else "Up next", item.name,
                    f"{self.index + 1} of {len(self.items)}",
                    "→ to start" if not self.error else "→ try again  ·  S skip",
                    f"Couldn't play this: {self.error}" if self.error else "")
        self.audience.stack.setCurrentWidget(self.audience.message)
        if not self.settings.windowed:
            system.set_kiosk(True)
        self._update_console()

    def start_talk(self):
        self.countdown_timer.stop()
        item = self.items[self.index]
        prepared = self.prepared.get(str(item.path))
        if prepared is None or prepared.error:
            self.audience.message.set("One moment", item.name, "Preparing…")
            QApplication.processEvents()
            prepared = prepare(item, self.settings.cache_root)
            self.prepared[str(item.path)] = prepared
        if prepared.error:
            self.error = prepared.error
            self.show_up_next()
            return
        self.error = ""
        self.source = SlideSource(item, prepared)
        self.mode = Mode.TALK
        self.talk_started = time.monotonic()

        if prepared.kind == "web":
            self.audience.message.set("In the browser", item.name, prepared.url or "",
                                      "When finished, come back and press →")
            self.audience.stack.setCurrentWidget(self.audience.message)
            system.set_kiosk(False)
            QDesktopServices.openUrl(QUrl(prepared.url))
            self._update_console()
        elif prepared.kind == "video":
            self.page = 0
            self.audience.stage.set_slide(None, 16 / 9)
            self.audience.stack.setCurrentWidget(self.audience.stage)
            video_item = self.audience.stage.add_video((0, 0, 1, 1))
            player = ClipPlayer(prepared.path(prepared.video, item.path), self.bus, video_item, self)
            player.ended.connect(lambda: QTimer.singleShot(0, self.end_talk))
            self.players = [player]
            player.play()
            self._update_console()
        else:
            self.audience.stack.setCurrentWidget(self.audience.stage)
            self._enter_slide(0)

    def end_talk(self):
        if self.mode is not Mode.TALK:
            return
        self._close_talk()
        self.index += 1
        self.show_up_next()
        if self.settings.auto_start > 0 and self.mode is Mode.UP_NEXT:
            self.countdown = self.settings.auto_start
            self.countdown_timer.start()
            self._countdown_text()

    def jump_to(self, index: int):
        self._close_talk()
        self.error = ""
        self.index = index
        self.show_up_next()

    def _close_talk(self):
        self._stop_clips()
        self.audience.stage.set_slide(None, 16 / 9)
        if self.source is not None:
            self.source.close()
            self.source = None
        if self.audience.stack.currentWidget() is self.audience.black and self.console:
            self.console.black_b.setChecked(False)

    # --- slides --------------------------------------------------------------

    def _enter_slide(self, page: int):
        self._stop_clips()
        self.page = page
        self._refresh_audience_slide()
        prepared = self.source.prepared
        clips = prepared.slides[page].clips if page < len(prepared.slides) else []
        for clip in clips:
            video_item = self.audience.stage.add_video(tuple(clip.rect or (0, 0, 1, 1))) if clip.kind == "video" else None
            player = ClipPlayer(prepared.path(clip.file, self.source.item.path), self.bus, video_item, self)
            self.players.append(player)
            if clip.autoplay:
                player.play()
            else:
                self.pending_clicks.append(player)
        self._update_console()

    def _refresh_audience_slide(self):
        if self.source is None or self.source.prepared.kind == "video":
            return
        stage = self.audience.stage
        rect = stage.slide_rect()
        pixmap = self.source.pixmap(self.page, int(rect.width()), int(rect.height()), stage.devicePixelRatioF())
        stage.set_slide(pixmap, self.source.aspect)

    def _stop_clips(self):
        for player in self.players:
            player.close()
        self.players = []
        self.pending_clicks = []
        self.audience.stage.clear_videos()

    # --- navigation ----------------------------------------------------------

    def next(self):
        if self.countdown_timer.isActive():
            self.countdown_timer.stop()
            self.start_talk()
        elif self.mode is Mode.UP_NEXT:
            self.start_talk()
        elif self.mode is Mode.TALK:
            kind = self.source.prepared.kind
            if kind in ("video", "web", "image"):
                self.end_talk()
            elif self.pending_clicks:
                self.pending_clicks.pop(0).play()  # Click-to-play media, like PowerPoint.
            elif self.page < self.source.prepared.page_count - 1:
                self._enter_slide(self.page + 1)
            else:
                self.end_talk()

    def previous(self):
        self.countdown_timer.stop()
        if self.mode is Mode.UP_NEXT and self.index > 0:
            self.jump_to(self.index - 1)
        elif self.mode is Mode.END:
            self.jump_to(len(self.items) - 1)
        elif self.mode is Mode.TALK:
            if self.source.prepared.kind == "video" and self.players:
                self.players[0].restart()
            elif self.page > 0:
                self._enter_slide(self.page - 1)

    def toggle_black(self):
        if self.mode is not Mode.TALK:
            return
        stack = self.audience.stack
        black = stack.currentWidget() is not self.audience.black
        stack.setCurrentWidget(self.audience.black if black else self.audience.stage)
        if self.console:
            self.console.black_b.setChecked(black)

    def handle_key(self, key: int, modifiers=Qt.NoModifier):
        alt = bool(modifiers & Qt.AltModifier)
        if (alt and key == Qt.Key_S) or key == Qt.Key_M:
            self.bus.toggle_mute()
        elif (alt and key == Qt.Key_Up) or key in (Qt.Key_Plus, Qt.Key_Equal):
            self.bus.nudge(10)
        elif (alt and key == Qt.Key_Down) or key in (Qt.Key_Minus, Qt.Key_Underscore):
            self.bus.nudge(-10)
        elif key in (Qt.Key_B, Qt.Key_Period):
            self.toggle_black()
        elif key == Qt.Key_Space and self.mode is Mode.TALK and self.source and self.source.prepared.kind == "video":
            self.players[0].toggle()
        elif key == Qt.Key_Escape:
            if self.countdown_timer.isActive():
                self.countdown_timer.stop()
                self.show_up_next()
            elif self.mode is Mode.TALK:
                self.end_talk()
            elif not self.settings.windowed and self.console is None:
                self.stop()  # No console to click 'Exit show' on.
        elif key == Qt.Key_S and self.mode is Mode.UP_NEXT:
            self.jump_to(self.index + 1)
        elif key == Qt.Key_Home and self.mode is Mode.TALK and self.source.prepared.page_count > 1:
            self._enter_slide(0)
        elif key in NEXT_KEYS:
            self.next()
        elif key in PREV_KEYS:
            self.previous()
        else:
            return False
        return True

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and isinstance(obj, QWidget):
            window = obj.window()
            if window is self.audience or window is self.console:
                return self.handle_key(event.key(), event.modifiers())
        return False

    # --- feedback ------------------------------------------------------------

    def _sound_feedback(self):
        if self.console is None:
            self.audience.toast.flash("🔇 Muted" if self.bus.muted else f"🔊 {self.bus.percent}%")

    def _countdown_tick(self):
        self.countdown -= 1
        if self.countdown <= 0:
            self.countdown_timer.stop()
            self.start_talk()
        else:
            self._countdown_text()

    def _countdown_text(self):
        self.audience.message.hint.setText(f"Starting in {self.countdown}…")

    def _tick(self):
        if not self.console:
            return
        self.console.clock_label.setText(time.strftime("%H:%M"))
        base = "font-size:30px; font-weight:700; font-family:monospace;"
        if self.mode is not Mode.TALK:
            self.console.timer_label.setText("--:--")
            self.console.timer_label.setStyleSheet(base)
            return
        elapsed = int(time.monotonic() - self.talk_started)
        limit = self.settings.talk_minutes * 60
        color = ""
        if limit:  # Orange in the last minute, red when over time.
            color = "#e74c3c" if elapsed >= limit else ACCENT if elapsed >= limit - 60 else ""
        self.console.timer_label.setText(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        self.console.timer_label.setStyleSheet(base + (f"color:{color};" if color else ""))

    def _update_console(self):
        c = self.console
        if c is None:
            return
        # Running order
        c.queue.clear()
        for n, item in enumerate(self.items):
            row = QListWidgetItem(f"{n + 1:02d}  {item.name}")
            if n < self.index:
                row.setForeground(QColor("#666"))
            if n == self.index:
                font = row.font()
                font.setBold(True)
                row.setFont(font)
            c.queue.addItem(row)
        if 0 <= self.index < len(self.items):
            c.queue.setCurrentRow(self.index)
            c.queue.scrollToItem(c.queue.item(self.index), QListWidget.PositionAtCenter)

        if self.mode is Mode.END:
            c.now_label.setText("All done")
            c.position_label.setText("")
            c.current.set_pixmap(None, "That's a wrap")
            c.next_box.set_pixmap(None)
            c.notes.setPlainText("")
            c.set_clips([], [])
            return

        item = self.items[self.index]
        prepared = self.prepared.get(str(item.path))
        if self.mode is Mode.UP_NEXT:
            c.now_label.setText(f"Up next: {item.name}")
            c.position_label.setText(f"Talk {self.index + 1} of {len(self.items)}  ·  press → to start")
            preview = self._preview(item, prepared, 0)
            c.current.set_pixmap(preview, "Waiting to start")
            c.next_caption.setText("After this")
            following = self.items[self.index + 1] if self.index + 1 < len(self.items) else None
            c.next_box.set_pixmap(self._preview(following, self.prepared.get(str(following.path)) if following else None, 0)
                                  if following else None, following.name if following else "Last one")
            notes = prepared.slides[0].notes if prepared and prepared.slides else ""
            warnings = "\n".join("⚠ " + w for w in prepared.warnings) if prepared else ""
            c.notes.setPlainText("\n\n".join(x for x in (notes, warnings) if x))
            c.set_clips([], [])
            return

        prepared = self.source.prepared
        count = prepared.page_count
        c.now_label.setText(item.name)
        if prepared.kind in ("slides", "image"):
            c.position_label.setText(f"Talk {self.index + 1} of {len(self.items)}  ·  slide {self.page + 1} / {count}")
            c.current.set_pixmap(self.source.pixmap(self.page, 960, 540))
            if self.page + 1 < count:
                c.next_caption.setText("Next slide")
                c.next_box.set_pixmap(self.source.pixmap(self.page + 1, 480, 270))
            else:
                following = self.items[self.index + 1] if self.index + 1 < len(self.items) else None
                c.next_caption.setText("Next: end of talk")
                c.next_box.set_pixmap(None, f"Then: {following.name}" if following else "Then: end of show")
            slide = prepared.slides[self.page]
            c.notes.setPlainText(slide.notes or "(no notes)")
            c.set_clips(slide.clips, self.players)
        else:
            label = "Video playing" if prepared.kind == "video" else "Presenting in the browser"
            c.position_label.setText(f"Talk {self.index + 1} of {len(self.items)}  ·  {label}")
            c.current.set_pixmap(self.source.pixmap(0, 960, 540), label)
            c.next_caption.setText("Next")
            c.next_box.set_pixmap(None, "→ ends this talk")
            c.notes.setPlainText("Space = pause / play, ← = restart" if prepared.kind == "video" else "")
            c.set_clips([], [])

    def _preview(self, item: Presentation | None, prepared: Prepared | None, page: int) -> QPixmap | None:
        if item is None or prepared is None or prepared.error or not prepared.thumb:
            return None
        return QPixmap(str(prepared.path(prepared.thumb, item.path)))
