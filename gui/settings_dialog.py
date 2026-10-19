"""Settings dialog — every default from the spec lives here."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QVBoxLayout)

from core.config import (DELAY_FLOOR, DELAY_WARN_BELOW, Settings, clamp_delay)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)

        # ---- storage -------------------------------------------------------
        storage = QGroupBox("Saving")
        form = QFormLayout(storage)

        row = QHBoxLayout()
        self.root_edit = QLineEdit(settings.root)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_root)
        row.addWidget(self.root_edit)
        row.addWidget(browse)
        form.addRow("Default root folder", row)

        self.board_dir = QCheckBox("Create a directory for each message board")
        self.board_dir.setChecked(settings.board_dir)
        form.addRow("", self.board_dir)

        self.folder_mode = QComboBox()
        self.folder_mode.addItem("Thread number", "number")
        self.folder_mode.addItem("Thread name", "name")
        self.folder_mode.addItem("Number and name", "both")
        idx = self.folder_mode.findData(settings.folder_mode)
        self.folder_mode.setCurrentIndex(max(0, idx))
        form.addRow("Thread folder is named", self.folder_mode)

        self.name_mode = QComboBox()
        self.name_mode.addItem("Uploader's filename", "uploader")
        self.name_mode.addItem("4chan file number", "number")
        idx = self.name_mode.findData(settings.name_mode)
        self.name_mode.setCurrentIndex(max(0, idx))
        form.addRow("Media files are saved as", self.name_mode)

        hint = QLabel(
            "Uploader mode saves the original name (e.g. <b>file.png</b>). "
            "If two uploads share a name, the second becomes "
            "<b>file (1792348901932343).png</b>.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        form.addRow("", hint)

        # ---- monitoring ----------------------------------------------------
        monitoring = QGroupBox("Monitoring")
        mform = QFormLayout(monitoring)

        self.default_delay = QDoubleSpinBox()
        self.default_delay.setRange(DELAY_FLOOR, 3600)
        self.default_delay.setSingleStep(1)
        self.default_delay.setDecimals(0)
        self.default_delay.setSuffix(" s")
        self.default_delay.setValue(clamp_delay(settings.default_delay))
        mform.addRow("Default delay between checks", self.default_delay)

        self.delay_note = QLabel("")
        self.delay_note.setStyleSheet("color:#c08000;")
        mform.addRow("", self.delay_note)

        self.default_comments = QCheckBox(
            "Download comments by default for new threads")
        self.default_comments.setChecked(settings.default_comments)
        mform.addRow("", self.default_comments)

        self.workers = QSpinBox()
        self.workers.setRange(1, 8)
        self.workers.setValue(settings.max_workers)
        mform.addRow("Simultaneous threads checked", self.workers)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.5, 10.0)
        self.interval.setSingleStep(0.5)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" s")
        self.interval.setValue(settings.request_interval)
        mform.addRow("Minimum gap between any two requests", self.interval)

        rate_note = QLabel("4chan asks for no more than one request per second "
                           "per client. Leave this at 1.0 unless you know why.")
        rate_note.setWordWrap(True)
        rate_note.setStyleSheet("color:#888;")
        mform.addRow("", rate_note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(storage)
        lay.addWidget(monitoring)
        lay.addWidget(buttons)

        self.default_delay.valueChanged.connect(self._check_delay)
        self._check_delay(self.default_delay.value())

    def _check_delay(self, value: float) -> None:
        self.delay_note.setText(
            f"Below {DELAY_WARN_BELOW:.0f}s you are polling faster than 4chan's "
            f"own thread refresh. Floor is {DELAY_FLOOR:.0f}s."
            if value < DELAY_WARN_BELOW else "")

    def _pick_root(self) -> None:
        start = self.root_edit.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose download root", start)
        if chosen:
            self.root_edit.setText(chosen)

    def result_settings(self) -> Settings:
        s = self.settings
        s.root = self.root_edit.text().strip() or s.root
        s.board_dir = self.board_dir.isChecked()
        s.folder_mode = self.folder_mode.currentData()
        s.name_mode = self.name_mode.currentData()
        s.default_delay = clamp_delay(self.default_delay.value())
        s.default_comments = self.default_comments.isChecked()
        s.max_workers = self.workers.value()
        s.request_interval = self.interval.value()
        return s.normalize()
