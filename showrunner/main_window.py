"""The set-up screen: pick the folder, see a preview of every file, put them in order, start."""
from __future__ import annotations

import random
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSettings, QSize, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox, QStyle,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import libreoffice, system
from .audio import AudioBus
from .library import Presentation, kind_for, load_order, natural_key, needs_converter, save_order
from .prepare import CACHE_DIRNAME, Prepared, PrepareWorker, is_current, load_cached
from .show import ShowController, ShowSettings

PATH_ROLE = Qt.UserRole
NUMBER_ROLE = Qt.UserRole + 1
THUMB = QSize(192, 108)


class OrderList(QListWidget):
    reordered = Signal()
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.placeholder = ""
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setIconSize(THUMB)
        self.setSpacing(3)
        self.setUniformItemSizes(True)

    def _external(self, event) -> bool:
        return event.mimeData().hasUrls() and event.source() is None

    def dragEnterEvent(self, event):
        if self._external(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._external(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self._external(event):  # Files from Finder/Explorer
            event.acceptProposedAction()
            self.files_dropped.emit([Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()])
            return
        super().dropEvent(event)
        self.reordered.emit()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count() == 0 and self.placeholder:
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#8a8f96"))
            painter.drawText(self.viewport().rect().adjusted(40, 0, -40, 0), Qt.AlignCenter | Qt.TextWordWrap,
                             self.placeholder)


class ConverterDownload(QThread):
    """Fetches LibreOffice in the background (see libreoffice.py)."""

    progress = Signal(int, int, str)  # kB done, kB total, text
    done = Signal(str)                # error message, empty on success

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel = threading.Event()

    def run(self):
        try:
            libreoffice.install(lambda done, total, text: self.progress.emit(done >> 10, total >> 10, text), self.cancel)
            self.done.emit("")
        except Exception as exc:
            self.done.emit(str(exc) or exc.__class__.__name__)


def _thumb_icon(path: Path | None, text: str = "") -> QIcon:
    """A 16:9 tile with the preview fitted in, so all rows line up."""
    tile = QPixmap(THUMB * 2)
    tile.setDevicePixelRatio(2)
    tile.fill(QColor("#22262b"))
    painter = QPainter(tile)
    source = QPixmap(str(path)) if path else QPixmap()
    if not source.isNull():
        scaled = source.scaled(THUMB * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(2)
        size = scaled.deviceIndependentSize()
        painter.drawPixmap(int((THUMB.width() - size.width()) / 2), int((THUMB.height() - size.height()) / 2), scaled)
    else:
        painter.setPen(QColor("#9aa0a6"))
        painter.drawText(tile.rect().adjusted(0, 0, -THUMB.width(), -THUMB.height()), Qt.AlignCenter, text)
    painter.end()
    return QIcon(tile)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Showrunner")
        self.resize(1240, 800)
        self.settings = QSettings()
        self.folder: Path | None = None
        self.items: list[Presentation] = []
        self.prepared: dict[str, Prepared] = {}
        self.status: dict[str, str] = {}
        self.controller: ShowController | None = None
        self.bus = AudioBus(self)
        self.waiting_for_converter: list[Presentation] = []
        self.download: ConverterDownload | None = None
        self.download_failures = 0
        self.download_retry = QTimer(self, singleShot=True)
        self.download_retry.timeout.connect(self._ensure_converter)

        cache_root = Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation)) / "prepared"
        self.cache_root = cache_root
        self.worker = PrepareWorker(cache_root, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.prepared.connect(self._on_prepared)
        self.worker.start()

        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _: self.reload_timer.start())
        # Debounced: copying 40 files in at once fires lots of change events.
        self.reload_timer = QTimer(self, singleShot=True, interval=1500)
        self.reload_timer.timeout.connect(self.reload_folder)

        self._build_ui()
        self._load_settings()
        self._ensure_converter()
        QShortcut(QKeySequence("Alt+S"), self, self.bus.toggle_mute)
        app = QGuiApplication.instance()
        app.screenAdded.connect(lambda _: self._fill_screens())
        app.screenRemoved.connect(lambda _: self._fill_screens())

    # --- UI ------------------------------------------------------------------

    def _build_ui(self):
        style = self.style()

        self.folder_label = QLabel("No folder chosen")
        self.folder_label.setStyleSheet("font-weight:600;")
        choose = QPushButton("Choose folder…")
        choose.clicked.connect(self.choose_folder)
        reveal = QPushButton("Open folder")
        reveal.clicked.connect(lambda: self.folder and QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        top = QHBoxLayout()
        top.addWidget(self.folder_label, 1)
        top.addWidget(reveal)
        top.addWidget(choose)

        self.list = OrderList()
        self.list.reordered.connect(self._on_reordered)
        self.list.files_dropped.connect(self._add_files)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.currentRowChanged.connect(self._update_details)
        self.list.itemDoubleClicked.connect(lambda _: self.start_show(True))
        self.list.setStyleSheet("QListWidget { font-size: 14px; } QListWidget::item { padding: 4px; }")

        up = QPushButton(style.standardIcon(QStyle.SP_ArrowUp), "")
        up.setToolTip("Move up")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton(style.standardIcon(QStyle.SP_ArrowDown), "")
        down.setToolTip("Move down")
        down.clicked.connect(lambda: self._move(1))
        self.sort_combo = QComboBox()
        for label, key in (("Sort by name A–Z", "az"), ("Sort by name Z–A", "za"),
                           ("Sort by date added", "date"), ("Random order", "random")):
            self.sort_combo.addItem(label, key)
        sort = QPushButton("Apply")
        sort.clicked.connect(self._sort)
        retry = QPushButton("Prepare again")
        retry.setToolTip("Re-convert the selected file (e.g. after installing LibreOffice)")
        retry.clicked.connect(self._reprepare_selected)
        list_buttons = QHBoxLayout()
        for widget in (up, down, self.sort_combo, sort):
            list_buttons.addWidget(widget)
        list_buttons.addStretch(1)
        list_buttons.addWidget(retry)

        self.summary = QLabel()
        self.summary.setStyleSheet("color:gray;")
        hint = QLabel("Drag to reorder · untick to skip someone · double-click to start from there")
        hint.setStyleSheet("color:gray;")

        left = QVBoxLayout()
        left.addLayout(top)
        left.addWidget(self.list, 1)
        left.addLayout(list_buttons)
        left.addWidget(hint)
        left.addWidget(self.summary)

        # Selected file preview
        self.preview = QLabel()
        self.preview.setFixedHeight(200)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#22262b; border-radius:6px;")
        self.detail = QTextBrowser()
        self.detail.setMaximumHeight(130)
        detail_layout = QVBoxLayout()
        detail_layout.addWidget(self.preview)
        detail_layout.addWidget(self.detail)
        detail = QGroupBox("Selected")
        detail.setLayout(detail_layout)

        # Sound
        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(lambda _: self.bus.set_device_by_name(self.device_combo.currentData()))
        self.volume_slider = QSlider(Qt.Horizontal, minimum=0, maximum=200, value=100)
        self.volume_slider.setPageStep(10)
        self.volume_slider.valueChanged.connect(self.bus.set_percent)
        self.volume_value = QLabel("100%")
        self.volume_value.setMinimumWidth(42)
        self.mute_button = QPushButton("Mute (Alt+S)")
        self.mute_button.setCheckable(True)
        self.mute_button.clicked.connect(self.bus.set_muted)
        test = QPushButton("Test sound")
        test.clicked.connect(self.bus.play_test_tone)
        self.bus.changed.connect(self._sync_sound)
        volume_row = QHBoxLayout()
        volume_row.addWidget(self.volume_slider, 1)
        volume_row.addWidget(self.volume_value)
        sound_buttons = QHBoxLayout()
        sound_buttons.addWidget(self.mute_button)
        sound_buttons.addWidget(test)
        sound_form = QFormLayout()
        sound_form.addRow("Output", self.device_combo)
        sound_form.addRow("Volume", volume_row)
        sound_form.addRow(sound_buttons)
        level_note = QLabel("All sound is levelled to the same loudness. 0–200%.")
        level_note.setStyleSheet("color:gray;")
        sound_form.addRow(level_note)
        sound = QGroupBox("Sound")
        sound.setLayout(sound_form)

        # Show
        self.beamer_combo = QComboBox()
        self.console_combo = QComboBox()
        self.windowed = QCheckBox("Test mode: show in windows, not full screen")
        self.talk_spin = QSpinBox(minimum=0, maximum=60, suffix=" min")
        self.talk_spin.setSpecialValueText("No limit")
        self.auto_spin = QSpinBox(minimum=0, maximum=120, suffix=" s")
        self.auto_spin.setSpecialValueText("Off - wait for →")
        show_form = QFormLayout()
        show_form.addRow("Beamer", self.beamer_combo)
        show_form.addRow("Console", self.console_combo)
        show_form.addRow(self.windowed)
        show_form.addRow("Talk length", self.talk_spin)
        show_form.addRow("Auto-start next", self.auto_spin)
        self.converter_label = QLabel()
        self.converter_label.setWordWrap(True)
        self.converter_label.setStyleSheet("color:gray;")
        self.converter_bar = QProgressBar()
        self.converter_bar.setTextVisible(False)
        self.converter_bar.setMaximumHeight(8)
        self.converter_bar.hide()
        show_form.addRow(self.converter_label)
        show_form.addRow(self.converter_bar)
        self._update_converter_label()
        show_box = QGroupBox("Show")
        show_box.setLayout(show_form)

        self.start_button = QPushButton("▶  Start the show")
        self.start_button.setMinimumHeight(52)
        self.start_button.setStyleSheet("font-size:18px; font-weight:700;")
        self.start_button.clicked.connect(lambda: self.start_show(False))
        self.start_selected = QPushButton("Start from selected")
        self.start_selected.clicked.connect(lambda: self.start_show(True))

        right = QVBoxLayout()
        right.addWidget(detail)
        right.addWidget(sound)
        right.addWidget(show_box)
        right.addStretch(1)
        right.addWidget(self.start_selected)
        right.addWidget(self.start_button)

        root = QHBoxLayout()
        root.addLayout(left, 1)
        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setFixedWidth(400)
        root.addWidget(right_widget)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

    def _sync_sound(self):
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(self.bus.percent)
        self.volume_slider.blockSignals(False)
        self.volume_value.setText(f"{self.bus.percent}%")
        self.mute_button.setChecked(self.bus.muted)
        self.mute_button.setText("MUTED (Alt+S)" if self.bus.muted else "Mute (Alt+S)")

    # --- settings ------------------------------------------------------------

    def _load_settings(self):
        s = self.settings
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(s.value("sort", "az"))))
        self.talk_spin.setValue(s.value("talk_minutes", 0, type=int))
        self.auto_spin.setValue(s.value("auto_start", 0, type=int))
        self.windowed.setChecked(s.value("windowed", False, type=bool))
        self.bus.set_percent(s.value("volume", 100, type=int))
        self._fill_devices(s.value("audio_device", ""))
        self._fill_screens()
        folder = Path(s.value("folder", "") or self._default_folder())
        if not folder.is_dir():
            folder = self._default_folder()
        self.open_folder(folder)
        self._update_summary()

    @staticmethod
    def _default_folder() -> Path:
        """Works out of the box: a folder in Documents, watched from the first launch."""
        documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home())
        folder = Path(documents) / "Showrunner Presentations"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _save_settings(self):
        s = self.settings
        s.setValue("sort", self.sort_combo.currentData())
        s.setValue("talk_minutes", self.talk_spin.value())
        s.setValue("auto_start", self.auto_spin.value())
        s.setValue("windowed", self.windowed.isChecked())
        s.setValue("volume", self.bus.percent)
        s.setValue("audio_device", self.device_combo.currentData() or "")
        s.setValue("beamer", self.beamer_combo.currentData() or "")
        s.setValue("console", self.console_combo.currentData() or "")
        if self.folder:
            s.setValue("folder", str(self.folder))

    def _fill_devices(self, wanted: str):
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("System default output", "")
        for device in self.bus.devices():
            self.device_combo.addItem(device.description(), device.description())
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(wanted)))
        self.device_combo.blockSignals(False)
        self.bus.set_device_by_name(self.device_combo.currentData())

    def _fill_screens(self):
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        for combo, key, prefer_primary in ((self.beamer_combo, "beamer", False), (self.console_combo, "console", True)):
            wanted = combo.currentData() if combo.count() else self.settings.value(key, "")
            combo.clear()
            for screen in screens:
                g = screen.geometry()
                label = f"{screen.name()}  ({g.width()}×{g.height()})" + ("  - main" if screen is primary else "")
                combo.addItem(label, screen.name())
            index = combo.findData(wanted)
            if index < 0:
                # Defaults: console on the main screen, beamer on the first other screen.
                index = next((i for i, s in enumerate(screens) if (s is primary) == prefer_primary), 0)
            combo.setCurrentIndex(index)
        if len(screens) > 1 and self.beamer_combo.currentIndex() == self.console_combo.currentIndex():
            # e.g. saved on a laptop alone, now a beamer is plugged in: use it.
            console = self.console_combo.currentIndex()
            self.beamer_combo.setCurrentIndex(next(i for i in range(len(screens)) if i != console))

    def closeEvent(self, event):
        self._save_settings()
        if self.download is not None:
            self.download.cancel.set()
            self.download.wait(5000)
        self.worker.stop()
        super().closeEvent(event)

    # --- folder & list -------------------------------------------------------

    def choose_folder(self):
        start = str(self.folder) if self.folder else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Folder with all presentations", start)
        if folder:
            self.open_folder(Path(folder))

    def open_folder(self, folder: Path):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        self.folder = folder
        self.folder_label.setText(str(folder))
        self.list.placeholder = (f"Put the presentations in\n{folder}\n\nor drag them here. "
                                 "Everything is prepared automatically.")
        self.watcher.addPath(str(folder))
        self.prepared.clear()
        self.status.clear()
        self.items = load_order(folder)
        self._queue_unprepared()
        self._refresh_list()
        self._save_settings()

    def reload_folder(self):
        """New/removed files: keep the current order, add newcomers at the end."""
        if not self.folder or not self.folder.is_dir():
            return
        save_order(self.folder, self.items)
        self.items = load_order(self.folder)
        self._queue_unprepared()
        self._refresh_list()

    def _queue_unprepared(self):
        for item in self.items:
            key = str(item.path)
            if not item.path.exists() or key in self.status:
                continue
            if key in self.prepared and is_current(item, self.prepared[key]):
                continue  # A replaced file gets a new cache key, so it is prepared again.
            self.prepared.pop(key, None)
            cached = load_cached(item, self.cache_root)
            if cached:
                self.prepared[key] = cached
            elif needs_converter(item) and not self._has_converter():
                self.status[key] = "Waiting for the slide converter…"
                self.waiting_for_converter.append(item)
                self._ensure_converter()
            else:
                self.status[key] = "Waiting…"
                self.worker.add(item)

    def _add_files(self, paths: list[Path]):
        """Files dragged onto the list are copied into the folder, which then picks them up."""
        if not self.folder:
            return
        for path in paths:
            if kind_for(path) is None or path.parent == self.folder:
                continue
            target = self.folder / path.name
            if path.is_dir():
                shutil.copytree(path, target, dirs_exist_ok=True)
            else:
                shutil.copy2(path, target)
        self.reload_folder()

    # --- slide converter -------------------------------------------------------

    @staticmethod
    def _has_converter() -> bool:
        return bool(system.converter_names())

    def _ensure_converter(self):
        """No PowerPoint/Keynote/LibreOffice? Fetch LibreOffice by itself, no questions asked."""
        if self._has_converter() or self.download is not None:
            return
        self.download = ConverterDownload(self)
        self.download.progress.connect(self._on_download_progress)
        self.download.done.connect(self._on_download_done)
        self.download.start()
        self._update_converter_label()

    def _on_download_progress(self, done: int, total: int, text: str):
        self.converter_bar.show()
        self.converter_bar.setRange(0, total)
        self.converter_bar.setValue(done)
        pct = f" {done * 100 // total}%" if total else ""
        self.converter_label.setText(f"Getting the slide converter (one time only): {text.rstrip('…')}{pct}")
        self.converter_label.setStyleSheet("color:gray;")

    def _on_download_done(self, error: str):
        self.download.deleteLater()
        self.download = None
        self.converter_bar.hide()
        system.soffice_path.cache_clear()
        if error and not self._has_converter():
            self.download_failures += 1
            minutes = min(10, 2 ** (self.download_failures - 1))
            self.download_retry.start(minutes * 60_000)
            self.converter_label.setText(f"Couldn't get the slide converter ({error}). Trying again in {minutes} min.")
            self.converter_label.setStyleSheet("color:#c98a00;")
            return
        self._update_converter_label()
        waiting, self.waiting_for_converter = self.waiting_for_converter, []
        for item in waiting:
            self.status[str(item.path)] = "Waiting…"
            self.worker.add(item)
        for item in self.items:  # Decks that failed before the converter arrived.
            key = str(item.path)
            prepared = self.prepared.get(key)
            if prepared is not None and prepared.error and needs_converter(item):
                self.prepared.pop(key)
                self.status[key] = "Waiting…"
                self.worker.add(item)
        self._refresh_list()

    def _update_converter_label(self):
        names = system.converter_names()
        if names:
            self.converter_label.setText("Slide converter: " + ", ".join(names))
            self.converter_label.setStyleSheet("color:gray;")
        elif self.download is not None:
            self.converter_label.setText("Getting the slide converter (one time only)…")

    def _on_progress(self, key: str, text: str):
        self.status[key] = text
        self._update_row(key)

    def _on_prepared(self, key: str, prepared: Prepared):
        self.prepared[key] = prepared
        self.status.pop(key, None)
        self._update_row(key)
        self._update_summary()
        if self._selected_path() == key:
            self._update_details()

    def _row_text(self, number: str, item: Presentation) -> str:
        key = str(item.path)
        prepared = self.prepared.get(key)
        if key in self.status:
            state = "⏳ " + self.status[key]
        elif prepared is None:
            state = ""
        elif prepared.error:
            state = "✖ " + prepared.error
        else:
            parts = []
            if prepared.kind in ("slides",):
                parts.append(f"{prepared.page_count} slides")
            clips = sum(len(s.clips) for s in prepared.slides)
            if clips:
                parts.append(f"{clips} audio/video")
            if any(s.notes for s in prepared.slides):
                parts.append("notes")
            state = ("⚠ " if prepared.warnings else "✓ ") + " · ".join(parts or ["ready"])
        return f"{number}   {item.name}\n       {item.label}  ·  {state}"

    def _update_row(self, key: str):
        for i in range(self.list.count()):
            row = self.list.item(i)
            if row.data(PATH_ROLE) == key:
                self.list.blockSignals(True)
                item = next(p for p in self.items if str(p.path) == key)
                row.setText(self._row_text(row.data(NUMBER_ROLE), item))
                row.setIcon(self._icon_for(item))
                self.list.blockSignals(False)

    def _icon_for(self, item: Presentation) -> QIcon:
        prepared = self.prepared.get(str(item.path))
        if prepared and prepared.thumb and not prepared.error:
            return _thumb_icon(prepared.path(prepared.thumb, item.path))
        return _thumb_icon(None, item.label)

    def _refresh_list(self):
        selected = self._selected_path()
        self.list.blockSignals(True)
        self.list.clear()
        number = 0
        for item in self.items:
            if item.enabled:
                number += 1
            label = f"{number:02d}" if item.enabled else " – "
            row = QListWidgetItem(self._row_text(label, item))
            row.setData(PATH_ROLE, str(item.path))
            row.setData(NUMBER_ROLE, label)
            row.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if item.enabled else Qt.Unchecked)
            row.setIcon(self._icon_for(item))
            row.setToolTip(str(item.path))
            if not item.enabled:
                row.setForeground(Qt.gray)
            self.list.addItem(row)
            if str(item.path) == selected:
                self.list.setCurrentItem(row)
        self.list.blockSignals(False)
        self._update_details()
        self._update_summary()

    def _selected_path(self) -> str | None:
        row = self.list.currentItem()
        return row.data(PATH_ROLE) if row else None

    def _commit_order(self):
        if self.folder:
            save_order(self.folder, self.items)
        self._refresh_list()

    def _on_reordered(self):
        by_path = {str(p.path): p for p in self.items}
        self.items = [by_path[self.list.item(i).data(PATH_ROLE)] for i in range(self.list.count())]
        self._commit_order()

    def _on_item_changed(self, row: QListWidgetItem):
        for item in self.items:
            if str(item.path) == row.data(PATH_ROLE):
                item.enabled = row.checkState() == Qt.Checked
        self.list.setCurrentItem(row)
        self._commit_order()

    def _move(self, delta: int):
        i = self.list.currentRow()
        j = i + delta
        if i < 0 or not 0 <= j < len(self.items):
            return
        self.items[i], self.items[j] = self.items[j], self.items[i]
        self._commit_order()

    def _sort(self):
        key = self.sort_combo.currentData()
        if key == "random":
            if QMessageBox.question(self, "Random order", "Put all presentations in a random order?") != QMessageBox.Yes:
                return
            random.shuffle(self.items)
        elif key == "date":
            self.items.sort(key=lambda p: p.path.stat().st_mtime if p.path.exists() else 0)
        else:
            self.items.sort(key=lambda p: natural_key(p.name), reverse=key == "za")
        self._save_settings()
        self._commit_order()

    def _reprepare_selected(self):
        key = self._selected_path()
        item = next((p for p in self.items if str(p.path) == key), None)
        if item is None:
            return
        prepared = self.prepared.pop(key, None)
        if prepared and prepared.dir and Path(prepared.dir).parent.name in (CACHE_DIRNAME, self.cache_root.name):
            shutil.rmtree(prepared.dir, ignore_errors=True)
        self.status[key] = "Waiting…"
        self.worker.add(item)
        self._update_row(key)

    def _update_details(self, *_):
        key = self._selected_path()
        item = next((p for p in self.items if str(p.path) == key), None)
        if item is None:
            self.preview.clear()
            self.detail.setHtml("")
            return
        prepared = self.prepared.get(key)
        pixmap = QPixmap()
        if prepared and prepared.thumb and not prepared.error:
            pixmap = QPixmap(str(prepared.path(prepared.thumb, item.path)))
        if pixmap.isNull():
            self.preview.setText(self.status.get(key, item.label))
        else:
            self.preview.setPixmap(pixmap.scaled(QSize(370, 200), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        html = [f"<b>{item.path.name}</b>"]
        if prepared and prepared.error:
            html.append(f"<p style='color:#d0342c'>{prepared.error}</p>")
        elif prepared:
            html += [f"<p style='color:#c98a00'>⚠ {w}</p>" for w in prepared.warnings]
            notes = next((s.notes for s in prepared.slides if s.notes), "")
            if notes:
                html.append(f"<p style='color:gray'>Notes: {notes[:200]}</p>")
        self.detail.setHtml("".join(html))

    def _update_summary(self):
        enabled = [p for p in self.items if p.enabled]
        ready = sum(1 for p in enabled if (pr := self.prepared.get(str(p.path))) and not pr.error)
        failed = sum(1 for p in enabled if (pr := self.prepared.get(str(p.path))) and pr.error)
        parts = [f"{len(enabled)} presentations", f"{ready} ready"]
        if failed:
            parts.append(f"{failed} can't be played")
        if len(self.items) - len(enabled):
            parts.append(f"{len(self.items) - len(enabled)} skipped")
        self.summary.setText("  ·  ".join(parts) if self.folder else "Choose the folder that holds everyone's presentation.")
        self.start_button.setEnabled(bool(enabled))
        self.start_selected.setEnabled(bool(enabled))

    # --- show ------------------------------------------------------------------

    def _screen(self, combo: QComboBox):
        return next((s for s in QGuiApplication.screens() if s.name() == combo.currentData()), QGuiApplication.primaryScreen())

    def start_show(self, from_selected: bool):
        playlist = [p for p in self.items if p.enabled]
        if not playlist or self.controller is not None:
            return
        start = 0
        if from_selected:
            key = self._selected_path()
            start = next((i for i, p in enumerate(playlist) if str(p.path) == key), 0)
        self._save_settings()
        settings = ShowSettings(
            audience_screen=self._screen(self.beamer_combo),
            console_screen=self._screen(self.console_combo),
            windowed=self.windowed.isChecked(),
            talk_minutes=self.talk_spin.value(),
            auto_start=self.auto_spin.value(),
            cache_root=self.cache_root,
        )
        self.controller = ShowController(playlist, self.prepared, self.bus, settings, self)
        self.controller.finished.connect(lambda index: self._on_show_finished(playlist, index))
        self.hide()
        self.controller.start(start)

    def _on_show_finished(self, playlist: list[Presentation], index: int):
        self.controller.deleteLater()
        self.controller = None
        self.show()
        if 0 <= index < len(playlist):
            target = str(playlist[index].path)
            for i in range(self.list.count()):
                if self.list.item(i).data(PATH_ROLE) == target:
                    self.list.setCurrentRow(i)
        self.raise_()
        self.activateWindow()
