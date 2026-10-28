"""The running show: an 'up next' screen between talks, and playback of each presentation.

PDFs, images and videos are shown in our own full-screen window. Keynote/PowerPoint decks are
handed to those apps; when their slideshow ends we take the screen back.
"""
from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from enum import Enum, auto

from PySide6.QtCore import QObject, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPixmap, QScreen
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

from . import audio, macos, runners
from .library import Kind, Presentation, resolve_web_url

NEXT_KEYS = {Qt.Key_Right, Qt.Key_Down, Qt.Key_PageDown, Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_N, Qt.Key_F5}
PREV_KEYS = {Qt.Key_Left, Qt.Key_Up, Qt.Key_PageUp, Qt.Key_Backspace, Qt.Key_P}

ACCENT = "#f5b841"


@dataclass
class ShowSettings:
    screen: QScreen | None
    audio_device: str | None  # None = leave the system output alone
    volume: int | None        # None = leave the volume alone
    slide_app: str            # "keynote" or "powerpoint"
    auto_start: int           # seconds before the next talk starts by itself, 0 = wait for a key


class Mode(Enum):
    IDLE = auto()
    UP_NEXT = auto()
    SLIDES = auto()
    VIDEO = auto()
    EXTERNAL = auto()
    WEB = auto()
    END = auto()


class MessagePage(QWidget):
    def __init__(self):
        super().__init__()
        self.kicker = QLabel()
        self.title = QLabel()
        self.meta = QLabel()
        self.error = QLabel()
        self.hint = QLabel()
        for label in (self.kicker, self.title, self.meta, self.error, self.hint):
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addStretch(3)
        layout.addWidget(self.kicker)
        layout.addWidget(self.title)
        layout.addWidget(self.meta)
        layout.addSpacing(30)
        layout.addWidget(self.error)
        layout.addStretch(4)
        layout.addWidget(self.hint)
        layout.addSpacing(30)

    def resizeEvent(self, event):
        h = max(self.height(), 400)
        self.kicker.setStyleSheet(f"color:{ACCENT}; font-size:{int(h * 0.035)}px; font-weight:600; letter-spacing:4px;")
        self.title.setStyleSheet(f"color:white; font-size:{int(h * 0.09)}px; font-weight:700;")
        self.meta.setStyleSheet(f"color:#9a9a9a; font-size:{int(h * 0.03)}px;")
        self.error.setStyleSheet(f"color:#ff6b6b; font-size:{int(h * 0.028)}px;")
        self.hint.setStyleSheet(f"color:#666; font-size:{int(h * 0.022)}px;")
        super().resizeEvent(event)

    def set(self, kicker: str, title: str, meta: str = "", hint: str = "", error: str = ""):
        self.kicker.setText(kicker.upper())
        self.title.setText(title)
        self.meta.setText(meta)
        self.error.setText(error)
        self.error.setVisible(bool(error))
        self.hint.setText(hint)


class ImagePage(QWidget):
    def __init__(self):
        super().__init__()
        self.pixmap: QPixmap | None = None

    def set_pixmap(self, pixmap: QPixmap | None):
        self.pixmap = pixmap
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        if self.pixmap and not self.pixmap.isNull():
            size = self.pixmap.deviceIndependentSize().scaled(self.size().toSizeF(), Qt.KeepAspectRatio)
            x = (self.width() - size.width()) / 2
            y = (self.height() - size.height()) / 2
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(x, y, size.width(), size.height()), self.pixmap, QRectF(self.pixmap.rect()))


class ShowWindow(QWidget):
    def __init__(self, controller: "ShowController"):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.controller = controller
        self.setWindowTitle("Showrunner")
        self.setStyleSheet("background:black;")
        self.setCursor(Qt.BlankCursor)
        self.stack = QStackedWidget(self)
        self.message = MessagePage()
        self.image = ImagePage()
        self.video_holder = QWidget()
        QVBoxLayout(self.video_holder).setContentsMargins(0, 0, 0, 0)
        self.video_widget = None  # Created on first video; QtMultimedia is slow to load.
        for page in (self.message, self.image, self.video_holder):
            self.stack.addWidget(page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

    def ensure_video_widget(self):
        if self.video_widget is None:
            from PySide6.QtMultimediaWidgets import QVideoWidget

            self.video_widget = QVideoWidget()
            self.video_widget.setStyleSheet("background:black;")
            self.video_holder.layout().addWidget(self.video_widget)
        return self.video_widget

    def keyPressEvent(self, event):
        self.controller.handle_key(event.key())

    def mousePressEvent(self, event):
        self.controller.handle_key(Qt.Key_Left if event.button() == Qt.RightButton else Qt.Key_Right)

    def closeEvent(self, event):
        if self.controller.mode is not Mode.IDLE:
            self.controller.stop()
        super().closeEvent(event)


class ExternalRunner(QThread):
    done = Signal(str)  # empty string = finished normally

    def __init__(self, item: Presentation, app: str):
        super().__init__()
        self.item = item
        self.app = app
        self.stop_event = threading.Event()

    def run(self):
        try:
            runners.play(self.item.path, self.item.kind, self.app, self.stop_event)
            self.done.emit("")
        except Exception as exc:
            self.done.emit(str(exc) or exc.__class__.__name__)

    def stop(self):
        self.stop_event.set()


class ShowController(QObject):
    finished = Signal(int)  # index of the presentation we stopped at

    def __init__(self, items: list[Presentation], settings: ShowSettings, parent=None):
        super().__init__(parent)
        self.items = items
        self.settings = settings
        self.index = 0
        self.mode = Mode.IDLE
        self.error = ""
        self.window = ShowWindow(self)
        self.doc = None
        self.page = 0
        self.player = None
        self.audio_output = None
        self.runner: ExternalRunner | None = None
        self._orphans: list[ExternalRunner] = []
        self.awake: subprocess.Popen | None = None
        self.countdown = 0
        self.countdown_timer = QTimer(self, interval=1000)
        self.countdown_timer.timeout.connect(self._tick)

    # --- lifecycle -------------------------------------------------------

    def start(self, index: int = 0):
        self.index = max(0, min(index, len(self.items) - 1))
        screen = self.settings.screen
        if screen is not None:
            self.window.setScreen(screen)
            self.window.setGeometry(screen.geometry())
        self.window.show()
        self._take_focus()
        self.awake = macos.keep_awake()
        audio.apply(self.settings.audio_device, self.settings.volume)
        self.show_up_next()

    def stop(self):
        if self.mode is Mode.IDLE:
            return
        self.countdown_timer.stop()
        self._cleanup()
        self.mode = Mode.IDLE
        macos.set_kiosk(False)
        if self.awake:
            self.awake.terminate()
            self.awake = None
        self.window.close()
        self.finished.emit(min(self.index, len(self.items) - 1))

    def _take_focus(self):
        macos.activate_self()
        macos.set_kiosk(True)
        self.window.raise_()
        self.window.activateWindow()
        self.window.setFocus()

    # --- screens ---------------------------------------------------------

    def show_up_next(self):
        self._cleanup()
        total = len(self.items)
        if self.index >= total:
            self.mode = Mode.END
            self.window.message.set("That's a wrap", f"All {total} presentations done",
                                    hint="← back to the last one  ·  Esc to exit")
            self.window.stack.setCurrentWidget(self.window.message)
            return
        self.mode = Mode.UP_NEXT
        item = self.items[self.index]
        hint = "→ / Space / click to start  ·  ← previous  ·  S skip  ·  Esc exit"
        if self.error:
            hint = "→ to try again  ·  S skip to the next one  ·  Esc exit"
        self.window.message.set(
            "Up first" if self.index == 0 else "Up next",
            item.name,
            f"{self.index + 1} of {total}  ·  {item.label}",
            hint,
            f"Couldn't play this: {self.error}" if self.error else "",
        )
        self.window.stack.setCurrentWidget(self.window.message)

    def start_current(self):
        self.countdown_timer.stop()
        item = self.items[self.index]
        self.error = ""
        audio.apply(self.settings.audio_device, self.settings.volume)
        try:
            if item.kind is Kind.PDF:
                self._start_pdf(item)
            elif item.kind is Kind.IMAGE:
                self._start_image(item)
            elif item.kind is Kind.VIDEO:
                self._start_video(item)
            elif item.kind is Kind.WEB:
                self._start_web(item)
            else:
                self._start_external(item)
        except Exception as exc:
            self._fail(str(exc) or exc.__class__.__name__)

    def finish_current(self):
        self._cleanup()
        self.index += 1
        self.show_up_next()
        if self.settings.auto_start > 0 and self.mode is Mode.UP_NEXT:
            self.countdown = self.settings.auto_start
            self._update_countdown()
            self.countdown_timer.start()

    def _fail(self, message: str):
        self._cleanup()
        self.error = message
        self.show_up_next()

    def _tick(self):
        self.countdown -= 1
        if self.countdown <= 0:
            self.countdown_timer.stop()
            self.start_current()
        else:
            self._update_countdown()

    def _update_countdown(self):
        self.window.message.hint.setText(f"Starting in {self.countdown}…  ·  any key to start now  ·  Esc exit")

    # --- built-in players ------------------------------------------------

    def _start_pdf(self, item: Presentation):
        from .pdf import open_pdf

        self.doc = open_pdf(item.path)
        if self.doc.needs_pass:
            raise RuntimeError("PDF is password protected")
        self.page = 0
        self.mode = Mode.SLIDES
        self._render_pdf_page()
        self.window.stack.setCurrentWidget(self.window.image)

    def _render_pdf_page(self):
        from .pdf import render

        dpr = self.window.devicePixelRatioF()
        samples, w, h, stride = render(self.doc, self.page, int(self.window.width() * dpr), int(self.window.height() * dpr))
        image = QImage(samples, w, h, stride, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(dpr)
        self.window.image.set_pixmap(pixmap)

    def _start_image(self, item: Presentation):
        pixmap = QPixmap(str(item.path))
        if pixmap.isNull():
            raise RuntimeError("Image can't be shown")
        self.mode = Mode.SLIDES
        self.doc = None
        self.window.image.set_pixmap(pixmap)
        self.window.stack.setCurrentWidget(self.window.image)

    def _start_video(self, item: Presentation):
        from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

        self.player = QMediaPlayer(self)
        # Picked fresh each time, so it follows the output device we just selected.
        self.audio_output = QAudioOutput(QMediaDevices.defaultAudioOutput(), self)
        self.audio_output.setVolume(1.0)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.window.ensure_video_widget())
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.errorOccurred.connect(lambda _err, msg: QTimer.singleShot(0, lambda: self._fail(msg or "Video error")))
        self.player.setSource(QUrl.fromLocalFile(str(item.path)))
        self.mode = Mode.VIDEO
        self.window.stack.setCurrentWidget(self.window.video_holder)
        self.player.play()

    def _on_media_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.EndOfMedia and self.mode is Mode.VIDEO:
            QTimer.singleShot(0, self.finish_current)

    def _start_web(self, item: Presentation):
        url = resolve_web_url(item.path)
        if not url:
            raise RuntimeError("Shortcut contains no link")
        self.mode = Mode.WEB
        self.window.message.set("In the browser", item.name, url,
                                "When finished: come back here (⌘-Tab) and press → for the next presenter")
        self.window.stack.setCurrentWidget(self.window.message)
        macos.set_kiosk(False)
        QDesktopServices.openUrl(QUrl(url))

    # --- Keynote / PowerPoint --------------------------------------------

    def _start_external(self, item: Presentation):
        if not macos.IS_MAC:
            raise RuntimeError(f"{item.label} files can only be played on macOS")
        app = runners.pick_app(item.kind, self.settings.slide_app)
        if app is None:
            raise RuntimeError("No app installed that can play this file")
        app_name = "PowerPoint" if app == runners.POWERPOINT else "Keynote"
        self.mode = Mode.EXTERNAL
        self.window.message.set(f"Opening in {app_name}", item.name, "",
                                "The show continues here automatically when the slideshow ends")
        self.window.stack.setCurrentWidget(self.window.message)
        macos.set_kiosk(False)
        self.runner = ExternalRunner(item, app)
        self.runner.done.connect(self._on_external_done)
        self.runner.start()

    def _on_external_done(self, error: str):
        runner, self.runner = self.runner, None
        if runner:
            runner.wait(2000)
            runner.deleteLater()
        if self.mode is not Mode.EXTERNAL:
            return
        self._take_focus()
        if error:
            self._fail(error)
        else:
            self.finish_current()

    # --- input -----------------------------------------------------------

    def handle_key(self, key: int):
        is_next = key in NEXT_KEYS
        is_prev = key in PREV_KEYS
        if self.countdown_timer.isActive():
            self.countdown_timer.stop()
            if key == Qt.Key_Escape:
                self.stop()
            else:
                self.start_current()
            return

        if self.mode is Mode.UP_NEXT:
            if key == Qt.Key_Escape:
                self.stop()
            elif is_next:
                self.start_current()
            elif is_prev and self.index > 0:
                self.error = ""
                self.index -= 1
                self.show_up_next()
            elif key == Qt.Key_S:
                self.error = ""
                self.index += 1
                self.show_up_next()
        elif self.mode is Mode.END:
            if key == Qt.Key_Escape:
                self.stop()
            elif is_prev:
                self.index = len(self.items) - 1
                self.show_up_next()
        elif self.mode is Mode.SLIDES:
            if key == Qt.Key_Escape:
                self.finish_current()
            elif is_next:
                if self.doc is not None and self.page < self.doc.page_count - 1:
                    self.page += 1
                    self._render_pdf_page()
                else:
                    self.finish_current()
            elif is_prev and self.doc is not None and self.page > 0:
                self.page -= 1
                self._render_pdf_page()
        elif self.mode is Mode.VIDEO:
            from PySide6.QtMultimedia import QMediaPlayer

            if key == Qt.Key_Space:
                if self.player.playbackState() == QMediaPlayer.PlayingState:
                    self.player.pause()
                else:
                    self.player.play()
            elif key == Qt.Key_Escape or is_next:
                self.finish_current()
            elif is_prev:
                self.player.setPosition(0)
                self.player.play()
        elif self.mode is Mode.WEB:
            macos.set_kiosk(True)
            if key == Qt.Key_Escape or is_next:
                self.finish_current()
        elif self.mode is Mode.EXTERNAL:
            if key == Qt.Key_Escape and self.runner:
                self.runner.stop()  # Ends that slideshow; _on_external_done moves on.

    # --- cleanup ---------------------------------------------------------

    def _cleanup(self):
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.deleteLater()
            self.player = None
        if self.audio_output is not None:
            self.audio_output.deleteLater()
            self.audio_output = None
        if self.doc is not None:
            self.doc.close()
            self.doc = None
        if self.runner is not None:
            self.runner.done.disconnect()
            self.runner.stop()
            if not self.runner.wait(5000):
                self._orphans.append(self.runner)  # Destroying a running QThread crashes.
            self.runner = None
        self.window.image.set_pixmap(None)
