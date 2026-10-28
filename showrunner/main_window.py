"""The set-up screen: pick the folder, drag presentations into order, check sound, start."""
from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSlider, QSpinBox, QStyle, QVBoxLayout, QWidget,
)

from . import audio, checks, macos, runners
from .library import Presentation, load_order, natural_key, save_order
from .show import ShowController, ShowSettings

PATH_ROLE = Qt.UserRole
KEEP_SYSTEM_DEVICE = "Keep current system output"


class OrderList(QListWidget):
    reordered = Signal()

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.reordered.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Showrunner")
        self.resize(1120, 740)
        self.settings = QSettings()
        self.folder: Path | None = None
        self.items: list[Presentation] = []
        self.results: dict[Path, checks.CheckResult] = {}
        self.controller: ShowController | None = None

        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _: self.reload_timer.start())
        # Debounced: students copying 40 files in at once fire lots of change events.
        self.reload_timer = QTimer(self, singleShot=True, interval=800)
        self.reload_timer.timeout.connect(self.reload_folder)

        self._build_ui()
        self._load_settings()
        app = QGuiApplication.instance()
        app.screenAdded.connect(lambda _: self._fill_screens())
        app.screenRemoved.connect(lambda _: self._fill_screens())

    # --- UI --------------------------------------------------------------

    def _build_ui(self):
        style = self.style()

        self.folder_label = QLabel("No folder chosen")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.folder_label.setStyleSheet("font-weight:600;")
        choose = QPushButton("Choose folder…")
        choose.clicked.connect(self.choose_folder)
        reveal = QPushButton("Show in Finder" if macos.IS_MAC else "Open folder")
        reveal.clicked.connect(lambda: self.folder and QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        top = QHBoxLayout()
        top.addWidget(self.folder_label, 1)
        top.addWidget(reveal)
        top.addWidget(choose)

        self.list = OrderList()
        self.list.reordered.connect(self._on_reordered)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.currentRowChanged.connect(self._update_details)
        self.list.itemDoubleClicked.connect(lambda item: self.start_show(from_selected=True))
        self.list.setStyleSheet("QListWidget { font-size: 15px; } QListWidget::item { padding: 7px 4px; }")

        up = QPushButton(style.standardIcon(QStyle.SP_ArrowUp), "")
        up.setToolTip("Move up")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton(style.standardIcon(QStyle.SP_ArrowDown), "")
        down.setToolTip("Move down")
        down.clicked.connect(lambda: self._move(1))
        sort = QPushButton("Sort A–Z")
        sort.clicked.connect(self._sort)
        shuffle = QPushButton("Shuffle")
        shuffle.setToolTip("Random order")
        shuffle.clicked.connect(self._shuffle)
        check = QPushButton("Check all files")
        check.clicked.connect(self.run_checks)
        list_buttons = QHBoxLayout()
        for widget in (up, down, sort, shuffle):
            list_buttons.addWidget(widget)
        list_buttons.addStretch(1)
        list_buttons.addWidget(check)

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

        # Sound
        self.device_combo = QComboBox()
        refresh = QPushButton(style.standardIcon(QStyle.SP_BrowserReload), "")
        refresh.setToolTip("Look for new speakers / HDMI")
        refresh.clicked.connect(self._fill_devices)
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(refresh)
        self.volume_check = QCheckBox("Set volume and unmute before every talk")
        self.volume_slider = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=80)
        self.volume_value = QLabel("80%")
        self.volume_slider.valueChanged.connect(lambda v: self.volume_value.setText(f"{v}%"))
        self.volume_check.toggled.connect(self.volume_slider.setEnabled)
        volume_row = QHBoxLayout()
        volume_row.addWidget(self.volume_slider, 1)
        volume_row.addWidget(self.volume_value)
        test = QPushButton("Play test sound (left, then right)")
        test.clicked.connect(self.test_sound)
        sound_form = QFormLayout()
        sound_form.addRow("Output", device_row)
        sound_form.addRow(self.volume_check)
        sound_form.addRow("Volume", volume_row)
        sound_form.addRow(test)
        sound = QGroupBox("Sound")
        sound.setLayout(sound_form)

        # Show
        self.screen_combo = QComboBox()
        self.app_combo = QComboBox()
        self.app_combo.addItem("Keynote", "keynote")
        self.app_combo.addItem("PowerPoint", "powerpoint")
        self.auto_spin = QSpinBox(minimum=0, maximum=120, suffix=" s")
        self.auto_spin.setSpecialValueText("Off - wait for a key")
        show_form = QFormLayout()
        show_form.addRow("Show on", self.screen_combo)
        show_form.addRow("Play .pptx with", self.app_combo)
        show_form.addRow("Auto-start next", self.auto_spin)
        apps = QLabel(self._apps_text())
        apps.setWordWrap(True)
        apps.setStyleSheet("color:gray;")
        show_form.addRow(apps)
        show_box = QGroupBox("Show")
        show_box.setLayout(show_form)

        # Selected
        self.detail_name = QLabel("-")
        self.detail_name.setWordWrap(True)
        self.detail_name.setStyleSheet("font-weight:600;")
        self.detail_info = QLabel()
        self.detail_info.setWordWrap(True)
        self.detail_info.setTextFormat(Qt.RichText)
        detail_layout = QVBoxLayout()
        detail_layout.addWidget(self.detail_name)
        detail_layout.addWidget(self.detail_info)
        detail = QGroupBox("Selected")
        detail.setLayout(detail_layout)

        self.start_button = QPushButton("▶  Start the show")
        self.start_button.setMinimumHeight(54)
        self.start_button.setStyleSheet("font-size:18px; font-weight:700;")
        self.start_button.clicked.connect(lambda: self.start_show(False))
        self.start_selected = QPushButton("Start from selected")
        self.start_selected.clicked.connect(lambda: self.start_show(True))
        keys = QLabel("During the show: → / Space / clicker = next · ← = back · S = skip · Esc = end talk / exit")
        keys.setWordWrap(True)
        keys.setStyleSheet("color:gray;")

        right = QVBoxLayout()
        right.addWidget(sound)
        right.addWidget(show_box)
        right.addWidget(detail)
        right.addStretch(1)
        right.addWidget(keys)
        right.addWidget(self.start_selected)
        right.addWidget(self.start_button)

        root = QHBoxLayout()
        root.addLayout(left, 3)
        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setFixedWidth(380)
        root.addWidget(right_widget)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

    def _apps_text(self) -> str:
        if not macos.IS_MAC:
            return "Not on macOS: only PDFs, images and videos can be played here."
        found = [name for name, ok in (
            ("Keynote", macos.app_installed(runners.KEYNOTE)),
            ("PowerPoint", macos.app_installed(runners.POWERPOINT)),
            ("LibreOffice", bool(macos.soffice_path())),
        ) if ok]
        return "Installed: " + (", ".join(found) if found else "no slide apps - install Keynote (free)")

    # --- settings --------------------------------------------------------

    def _load_settings(self):
        s = self.settings
        self.volume_check.setChecked(s.value("volume_enabled", True, type=bool))
        self.volume_slider.setValue(s.value("volume", 80, type=int))
        self.volume_slider.setEnabled(self.volume_check.isChecked())
        index = self.app_combo.findData(s.value("slide_app", "keynote"))
        self.app_combo.setCurrentIndex(max(index, 0))
        self.auto_spin.setValue(s.value("auto_start", 0, type=int))
        self._fill_devices()
        self._fill_screens()
        folder = s.value("folder", "")
        if folder and Path(folder).is_dir():
            self.open_folder(Path(folder))
        self._update_summary()

    def _save_settings(self):
        s = self.settings
        s.setValue("volume_enabled", self.volume_check.isChecked())
        s.setValue("volume", self.volume_slider.value())
        s.setValue("slide_app", self.app_combo.currentData())
        s.setValue("auto_start", self.auto_spin.value())
        s.setValue("audio_device", self.device_combo.currentData() or "")
        s.setValue("screen", self.screen_combo.currentData() or "")
        if self.folder:
            s.setValue("folder", str(self.folder))

    def _fill_devices(self):
        wanted = self.device_combo.currentData() if self.device_combo.count() else self.settings.value("audio_device", "")
        devices = audio.list_output_devices()
        current = audio.current_output_device()
        self.device_combo.clear()
        self.device_combo.addItem(KEEP_SYSTEM_DEVICE, "")
        for name in devices:
            self.device_combo.addItem(f"{name}  (current)" if name == current else name, name)
        self.device_combo.setEnabled(bool(devices))
        index = self.device_combo.findData(wanted)
        self.device_combo.setCurrentIndex(max(index, 0))

    def _fill_screens(self):
        wanted = self.screen_combo.currentData() if self.screen_combo.count() else self.settings.value("screen", "")
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        self.screen_combo.clear()
        for screen in screens:
            g = screen.geometry()
            label = f"{screen.name()}  ({g.width()}×{g.height()})" + ("  - main" if screen is primary else "")
            self.screen_combo.addItem(label, screen.name())
        index = self.screen_combo.findData(wanted)
        if index < 0:
            # Default to the projector: the first screen that isn't the laptop's own.
            index = next((i for i, s in enumerate(screens) if s is not primary), 0)
        self.screen_combo.setCurrentIndex(index)

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)

    # --- folder & list ---------------------------------------------------

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
        self.watcher.addPath(str(folder))
        self.results.clear()
        self.items = load_order(folder)
        self._refresh_list()
        self._save_settings()
        self.run_checks()

    def reload_folder(self):
        """New/removed files: keep the current order, add newcomers at the end."""
        if not self.folder or not self.folder.is_dir():
            return
        save_order(self.folder, self.items)
        known = {p.path for p in self.items}
        self.items = load_order(self.folder)
        for item in self.items:
            if item.path not in known:
                self.results[item.path] = checks.check(item, self.app_combo.currentData())
        self._refresh_list()

    def _refresh_list(self):
        selected = self._selected_path()
        style = self.style()
        icons = {
            checks.OK: style.standardIcon(QStyle.SP_DialogApplyButton),
            checks.WARN: style.standardIcon(QStyle.SP_MessageBoxWarning),
            checks.ERROR: style.standardIcon(QStyle.SP_MessageBoxCritical),
        }
        self.list.blockSignals(True)
        self.list.clear()
        number = 0
        for item in self.items:
            if item.enabled:
                number += 1
            prefix = f"{number:02d}" if item.enabled else "  –"
            row = QListWidgetItem(f"{prefix}    {item.name}      ·  {item.label}")
            row.setData(PATH_ROLE, str(item.path))
            row.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked if item.enabled else Qt.Unchecked)
            result = self.results.get(item.path)
            if result:
                row.setIcon(icons[result.level])
                row.setToolTip(result.message)
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
        self.items.sort(key=lambda p: natural_key(p.path.name))
        self._commit_order()

    def _shuffle(self):
        if QMessageBox.question(self, "Shuffle", "Put all presentations in a random order?") == QMessageBox.Yes:
            random.shuffle(self.items)
            self._commit_order()

    def _update_details(self, *_):
        path = self._selected_path()
        item = next((p for p in self.items if str(p.path) == path), None)
        if item is None:
            self.detail_name.setText("-")
            self.detail_info.setText("")
            return
        result = self.results.get(item.path)
        self.detail_name.setText(item.path.name)
        color = {checks.OK: "#2e9e4f", checks.WARN: "#c98a00", checks.ERROR: "#d0342c"}.get(result.level if result else "", "gray")
        message = result.message if result else "Not checked yet"
        self.detail_info.setText(f"{item.label}<br><span style='color:{color}'>{message}</span>")

    def _update_summary(self):
        enabled = sum(p.enabled for p in self.items)
        skipped = len(self.items) - enabled
        warn = sum(1 for p in self.items if p.enabled and self.results.get(p.path, None) and self.results[p.path].level == checks.WARN)
        err = sum(1 for p in self.items if p.enabled and self.results.get(p.path, None) and self.results[p.path].level == checks.ERROR)
        parts = [f"{enabled} presentations"]
        if skipped:
            parts.append(f"{skipped} skipped")
        if warn:
            parts.append(f"{warn} with warnings")
        if err:
            parts.append(f"{err} won't play")
        self.summary.setText("  ·  ".join(parts) if self.folder else "Choose the folder that holds everyone's presentation.")
        self.start_button.setEnabled(enabled > 0)
        self.start_selected.setEnabled(enabled > 0)

    # --- actions ---------------------------------------------------------

    def run_checks(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            prefer = self.app_combo.currentData()
            for item in self.items:
                self.results[item.path] = checks.check(item, prefer)
                QApplication.processEvents()
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_list()

    def test_sound(self):
        audio.apply(self.device_combo.currentData() or None,
                    self.volume_slider.value() if self.volume_check.isChecked() else None)
        self._fill_devices()
        if not audio.play_test_tone():
            QMessageBox.information(self, "Test sound", "Couldn't play a test sound on this system.")

    def _show_settings(self) -> ShowSettings:
        wanted = self.screen_combo.currentData()
        screen = next((s for s in QGuiApplication.screens() if s.name() == wanted), QGuiApplication.primaryScreen())
        return ShowSettings(
            screen=screen,
            audio_device=self.device_combo.currentData() or None,
            volume=self.volume_slider.value() if self.volume_check.isChecked() else None,
            slide_app=self.app_combo.currentData(),
            auto_start=self.auto_spin.value(),
        )

    def start_show(self, from_selected: bool):
        playlist = [p for p in self.items if p.enabled]
        if not playlist or self.controller is not None:
            return
        start = 0
        if from_selected:
            path = self._selected_path()
            start = next((i for i, p in enumerate(playlist) if str(p.path) == path), 0)
        self._save_settings()
        self.controller = ShowController(playlist, self._show_settings(), self)
        self.controller.finished.connect(lambda index: self._on_show_finished(playlist, index))
        self.controller.start(start)

    def _on_show_finished(self, playlist: list[Presentation], index: int):
        self.controller.deleteLater()
        self.controller = None
        if 0 <= index < len(playlist):
            target = str(playlist[index].path)
            for i in range(self.list.count()):
                if self.list.item(i).data(PATH_ROLE) == target:
                    self.list.setCurrentRow(i)
        self.raise_()
        self.activateWindow()
