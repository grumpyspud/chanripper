"""Main window: URL bar, always-visible thread list, monitor wiring."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDoubleSpinBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QToolBar,
                               QVBoxLayout, QWidget)

from core.api import parse_thread_url
from core.config import DELAY_FLOOR, Settings, clamp_delay
from core.engine import Engine, SyncResult
from core.scheduler import MonitorEngine
from core.store import Store

from .settings_dialog import SettingsDialog

COLUMNS = ["Board", "Thread", "Subject", "Posts", "Media", "Size",
           "Last check", "Next check", "Delay (s)", "Monitor", "Comments",
           "Status"]
C_BOARD, C_THREAD, C_SUBJECT, C_POSTS, C_MEDIA, C_SIZE = 0, 1, 2, 3, 4, 5
C_LAST, C_NEXT, C_DELAY, C_MON, C_COM, C_STATUS = 6, 7, 8, 9, 10, 11


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _open_folder(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))            # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


class Bridge(QObject):
    """Marshals engine callbacks from worker threads onto the GUI thread."""
    sync_done = Signal(object)
    added = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, store: Store):
        super().__init__()
        self.settings = settings
        self.store = store
        self.engine = Engine(settings, store)
        self.bridge = Bridge()
        self.bridge.sync_done.connect(self._on_sync_done)
        self.bridge.added.connect(self._on_added)

        self.monitor: MonitorEngine | None = None
        self._rows: dict[tuple[str, int], int] = {}
        self._spins: dict[tuple[str, int], QDoubleSpinBox] = {}

        self.setWindowTitle("ChanRipper")
        self.resize(1180, 620)
        self._build_ui()

        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_dynamic)
        self._tick.start()

        self._reload_table()
        QTimer.singleShot(300, self._autostart)

    # -- ui ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 10, 10, 6)

        top = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "Paste a thread URL, e.g. "
            "https://boards.4chan.org/news/thread/1543881/…  (or news/1543881)")
        self.url_edit.returnPressed.connect(self.add_thread)
        top.addWidget(self.url_edit, 1)

        self.chk_monitor = QCheckBox("Monitor")
        self.chk_comments = QCheckBox("Comments")
        self.chk_comments.setChecked(self.settings.default_comments)
        top.addWidget(self.chk_monitor)
        top.addWidget(self.chk_comments)

        add_btn = QPushButton("Download Thread")
        add_btn.setDefault(True)
        add_btn.clicked.connect(self.add_thread)
        top.addWidget(add_btn)
        lay.addLayout(top)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        hdr = self.table.horizontalHeader()
        for col, width in ((C_SUBJECT, 320), (C_BOARD, 70), (C_THREAD, 100)):
            hdr.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, width)
        hdr.setSectionResizeMode(C_SUBJECT, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.setCentralWidget(central)

        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)
        for label, slot in (("Add", self.add_thread),
                            ("Remove", self.remove_selected),
                            ("Re-check now", self.recheck_selected),
                            ("Open folder", self.open_selected_folder),
                            ("Start all", self.start_all),
                            ("Stop all", self.stop_all),
                            ("Open root", self.open_root),
                            ("Settings", self.open_settings)):
            act = QAction(label, self)
            act.triggered.connect(slot)
            bar.addAction(act)

        self.status_label = QLabel("idle")
        self.statusBar().addPermanentWidget(self.status_label)

    # -- table ---------------------------------------------------------------
    def _reload_table(self) -> None:
        self.table.setRowCount(0)
        self._rows.clear()
        self._spins.clear()
        for rec in self.store.list_threads():
            self._add_row(rec)

    def _add_row(self, rec: dict) -> None:
        key = (rec["board"], rec["no"])
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[key] = row

        for col, text in ((C_BOARD, f"/{rec['board']}/"),
                          (C_THREAD, str(rec["no"])),
                          (C_SUBJECT, rec.get("subject") or "")):
            item = QTableWidgetItem(text)
            item.setData(Qt.UserRole, key)
            self.table.setItem(row, col, item)

        for col in (C_POSTS, C_MEDIA, C_SIZE, C_LAST, C_NEXT, C_STATUS):
            self.table.setItem(row, col, QTableWidgetItem(""))

        spin = QDoubleSpinBox()
        spin.setRange(DELAY_FLOOR, 3600)
        spin.setDecimals(0)
        spin.setSingleStep(1)
        spin.setSuffix(" s")
        spin.setValue(clamp_delay(rec.get("delay") or self.settings.default_delay))
        spin.valueChanged.connect(
            lambda value, k=key: self._delay_changed(k, value))
        self.table.setCellWidget(row, C_DELAY, spin)
        self._spins[key] = spin

        for col, field in ((C_MON, "monitor"), (C_COM, "comments")):
            cb = QCheckBox()
            cb.setChecked(bool(rec.get(field)))
            cb.setStyleSheet("margin-left:8px;")
            cb.stateChanged.connect(
                lambda _s, k=key, f=field, c=cb: self._flag_changed(k, f, c.isChecked()))
            self.table.setCellWidget(row, col, cb)

        self._refresh_row(rec)

    def _refresh_row(self, rec: dict) -> None:
        key = (rec["board"], rec["no"])
        row = self._rows.get(key)
        if row is None:
            return
        self.table.item(row, C_POSTS).setText(str(rec.get("post_count") or 0))
        self.table.item(row, C_MEDIA).setText(str(rec.get("media_count") or 0))
        self.table.item(row, C_SIZE).setText(_human(rec.get("media_bytes") or 0))
        self.table.item(row, C_SUBJECT).setText(rec.get("subject") or "")
        self._set_status(row, rec)

    def _set_status(self, row: int, rec: dict) -> None:
        status = rec.get("status") or "idle"
        label = {"ok": "up to date", "unchanged": "up to date",
                 "dead": "DEAD (404)", "error": "error", "idle": "idle",
                 "downloading": "downloading…"}.get(status, status)
        if status == "error" and rec.get("error"):
            label = f"error: {rec['error'][:40]}"
        item = self.table.item(row, C_STATUS)
        item.setText(label)
        colour = {"DEAD (404)": "#c0392b", "up to date": "#2d8a4e"}.get(label, "")
        item.setForeground(Qt.red if "DEAD" in label else
                           (Qt.darkGreen if label == "up to date" else Qt.black))

    def _refresh_dynamic(self) -> None:
        now = time.strftime("%H:%M:%S")
        for (board, no), row in list(self._rows.items()):
            rec = self.store.get_thread(board, no)
            if not rec:
                continue
            last = rec.get("last_check")
            self.table.item(row, C_LAST).setText(
                time.strftime("%H:%M:%S", time.localtime(last)) if last else "—")
            if self.monitor and rec.get("monitor") and rec.get("status") != "dead":
                if self.monitor.inflight(board, no):
                    nxt = "checking…"
                else:
                    secs = self.monitor.seconds_until(board, no)
                    nxt = "—" if secs is None else f"{secs:.0f}s"
            else:
                nxt = "—"
            self.table.item(row, C_NEXT).setText(nxt)

        monitored = sum(1 for r in self.store.list_threads() if r.get("monitor"))
        running = "monitoring" if self.monitor else "stopped"
        self.status_label.setText(
            f"{running} · {monitored} thread(s) · queue paced at "
            f"{self.settings.request_interval:.1f} req/s max · {now}")

    # -- actions -------------------------------------------------------------
    def add_thread(self) -> None:
        url = self.url_edit.text().strip()
        parsed = parse_thread_url(url)
        if not parsed:
            QMessageBox.warning(self, "Unrecognised URL",
                                "Paste a 4chan thread URL, e.g.\n"
                                "https://boards.4chan.org/news/thread/1543881/")
            return
        board, no = parsed
        key = (board, no)
        monitor = self.chk_monitor.isChecked()
        comments = self.chk_comments.isChecked()
        if key in self._rows:
            self.store.update_thread(board, no, monitor=int(monitor),
                                     comments=int(comments))
            self._sync_ui_flags(key)
        self._set_busy(True, f"fetching /{board}/{no}…")

        def work() -> None:
            try:
                res = self.engine.add(url, monitor=monitor, comments=comments)
            except Exception as exc:                       # pragma: no cover
                res = SyncResult(board, no, "error", error=str(exc))
            self.bridge.added.emit(res)

        threading.Thread(target=work, daemon=True).start()

    def _on_added(self, res: SyncResult) -> None:
        self._set_busy(False)
        if res.status == "error" and not res.folder:
            QMessageBox.warning(self, "Download failed", res.error or "unknown error")
            return
        rec = self.store.get_thread(res.board, res.no)
        if rec:
            key = (res.board, res.no)
            if key not in self._rows:
                self._add_row(rec)
            else:
                self._sync_ui_flags(key)
                self._refresh_row(rec)
        self.url_edit.clear()
        if self.monitor:
            self.monitor.poke()

    def _on_sync_done(self, res: SyncResult) -> None:
        rec = self.store.get_thread(res.board, res.no)
        if rec:
            key = (res.board, res.no)
            if key not in self._rows:
                self._add_row(rec)
            else:
                self._refresh_row(rec)

    def remove_selected(self) -> None:
        for key in self._selected_keys():
            board, no = key
            rec = self.store.get_thread(board, no)
            if QMessageBox.question(
                    self, "Remove thread",
                    f"Remove /{board}/{no} from the list?\n\n"
                    "Files already downloaded stay on disk.") != QMessageBox.Yes:
                continue
            if self.monitor:
                self.monitor.poke()
            self.store.remove_thread(board, no)
            row = self._rows.pop(key, None)
            if row is not None:
                self.table.removeRow(row)
            self._rows.clear()
            self._spins.clear()
            self._reload_table()

    def recheck_selected(self) -> None:
        for board, no in self._selected_keys():
            threading.Thread(
                target=lambda b=board, n=no: self.bridge.sync_done.emit(
                    self.engine.sync(b, n, force=True)), daemon=True).start()

    def open_selected_folder(self) -> None:
        for board, no in self._selected_keys():
            rec = self.store.get_thread(board, no)
            if rec and rec.get("folder"):
                _open_folder(Path(rec["folder"]))

    def start_all(self) -> None:
        for rec in self.store.list_threads():
            self.store.update_thread(rec["board"], rec["no"], monitor=1)
            self._sync_ui_flags((rec["board"], rec["no"]))
        self._ensure_monitor()
        self.monitor.poke() if self.monitor else None

    def stop_all(self) -> None:
        for rec in self.store.list_threads():
            self.store.update_thread(rec["board"], rec["no"], monitor=0)
            self._sync_ui_flags((rec["board"], rec["no"]))

    def open_root(self) -> None:
        root = Path(self.settings.root)
        root.mkdir(parents=True, exist_ok=True)
        _open_folder(root)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self.settings = dlg.result_settings()
            self.settings.save()
            self.engine.apply_settings(self.settings)
            self._reload_table()

    def _context_menu(self, pos) -> None:
        keys = self._selected_keys()
        if not keys:
            return
        menu = QMenu(self)
        acts = {
            "Open folder": self.open_selected_folder,
            "Re-check now": self.recheck_selected,
            "Copy thread URL": self._copy_url,
            "Remove": self.remove_selected,
        }
        for label, slot in acts.items():
            a = menu.addAction(label)
            a.triggered.connect(slot)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_url(self) -> None:
        keys = self._selected_keys()
        if keys:
            board, no = keys[0]
            QGuiApplication.clipboard().setText(
                f"https://boards.4chan.org/{board}/thread/{no}/")

    # -- plumbing ------------------------------------------------------------
    def _selected_keys(self) -> list[tuple[str, int]]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        out = []
        for row in rows:
            item = self.table.item(row, C_BOARD)
            if item:
                out.append(item.data(Qt.UserRole))
        return out

    def _delay_changed(self, key: tuple[str, int], value: float) -> None:
        board, no = key
        self.store.update_thread(board, no, delay=clamp_delay(value))

    def _flag_changed(self, key: tuple[str, int], field: str, value: bool) -> None:
        board, no = key
        self.store.update_thread(board, no, **{field: int(value)})
        if field == "monitor" and value:
            self._ensure_monitor()
        if self.monitor:
            self.monitor.poke()

    def _sync_ui_flags(self, key: tuple[str, int]) -> None:
        rec = self.store.get_thread(*key)
        row = self._rows.get(key)
        if not rec or row is None:
            return
        for col, field in ((C_MON, "monitor"), (C_COM, "comments")):
            cb = self.table.cellWidget(row, col)
            if isinstance(cb, QCheckBox):
                cb.blockSignals(True)
                cb.setChecked(bool(rec.get(field)))
                cb.blockSignals(False)
        spin = self._spins.get(key)
        if spin:
            spin.blockSignals(True)
            spin.setValue(clamp_delay(rec.get("delay") or self.settings.default_delay))
            spin.blockSignals(False)

    def _ensure_monitor(self) -> None:
        if self.monitor is None:
            self.monitor = MonitorEngine(
                self.engine,
                on_event=lambda res: self.bridge.sync_done.emit(res),
                max_workers=self.settings.max_workers)
        self.monitor.start()

    def _autostart(self) -> None:
        if any(r.get("monitor") and r.get("status") != "dead"
               for r in self.store.list_threads()):
            self._ensure_monitor()
            self.statusBar().showMessage("resumed monitoring saved threads", 5000)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.statusBar().showMessage(text, 0 if busy else 3000)

    def closeEvent(self, event) -> None:
        self.settings.save()
        if self.monitor:
            self.monitor.stop(wait=False)
        self.store.close()
        super().closeEvent(event)
