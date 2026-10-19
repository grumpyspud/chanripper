"""Headless GUI smoke test: builds the real window, drives a real download.

Run:  QT_QPA_PLATFORM=offscreen .venv/bin/python tests/gui_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication                    # noqa: E402

from core.config import Settings                              # noqa: E402
from core.store import Store                                  # noqa: E402
from gui.main_window import MainWindow                        # noqa: E402
from gui.settings_dialog import SettingsDialog                # noqa: E402

URL = "https://boards.4chan.org/news/thread/1543881/air-force-commando-dies-after-falling-from"
fails: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not cond:
        fails.append(label)


def main() -> int:
    app = QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="chanripper-gui-"))
    settings = Settings(root=str(tmp / "dl"), board_dir=True, folder_mode="both",
                        name_mode="uploader", default_comments=True)
    settings.normalize()
    store = Store(tmp / "state.sqlite")

    win = MainWindow(settings, store)
    win.show()
    app.processEvents()

    check("window builds with 12 columns", win.table.columnCount() == 12,
          str(win.table.columnCount()))
    check("table starts empty", win.table.rowCount() == 0)

    # settings dialog constructs and round-trips
    dlg = SettingsDialog(settings, win)
    dlg.default_delay.setValue(5)
    out = dlg.result_settings()
    check("settings dialog round-trip", out.default_delay == 5.0,
          f"delay={out.default_delay}")
    dlg.deleteLater()

    # a pre-registered thread populates the table with live widgets
    store.add_thread("news", 1543881, url=URL, monitor=True, comments=True,
                     delay=10, subject="seeded")
    win._reload_table()
    check("row added", win.table.rowCount() == 1)
    spin = win.table.cellWidget(0, 8)
    check("per-thread delay widget", spin is not None and spin.value() == 10.0)
    mon = win.table.cellWidget(0, 9)
    check("monitor checkbox reflects state", mon is not None and mon.isChecked())

    # real end-to-end: paste a URL, hit the button, let the worker finish
    win.url_edit.setText(URL)
    win.chk_monitor.setChecked(False)
    win.chk_comments.setChecked(True)
    win.add_thread()

    rec = None
    deadline = time.time() + 90
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.3)
        rec = store.get_thread("news", 1543881)
        if rec and rec["status"] in ("ok", "dead", "error") and rec["post_count"]:
            break

    app.processEvents()
    check("engine reported ok", bool(rec) and rec["status"] == "ok",
          f"status={rec['status'] if rec else None} err={rec.get('error') if rec else ''}")
    check("posts recorded", bool(rec) and rec["post_count"] > 0,
          f"posts={rec['post_count'] if rec else 0}")
    check("media downloaded", bool(rec) and rec["media_count"] > 0,
          f"media={rec['media_count'] if rec else 0}")

    folder = Path(rec["folder"]) if rec and rec["folder"] else None
    check("uploader filename preserved on disk",
          bool(folder) and (folder / "file.png").exists(),
          str(folder))
    check("thread.json written", bool(folder) and (folder / "thread.json").exists())
    check("thread.txt written", bool(folder) and (folder / "thread.txt").exists())

    win._refresh_dynamic()
    check("status cell shows up to date",
          win.table.item(0, 11).text() == "up to date",
          win.table.item(0, 11).text())
    check("last-check cell populated", win.table.item(0, 6).text() != "—",
          win.table.item(0, 6).text())

    # monitor engine: flag the thread, start it, confirm it schedules a next pass
    store.update_thread("news", 1543881, monitor=1, delay=10)
    win._sync_ui_flags(("news", 1543881))
    win._ensure_monitor()
    win.monitor.poke()
    for _ in range(40):
        app.processEvents()
        time.sleep(0.25)
        if win.monitor.next_due("news", 1543881) is not None:
            break
    due = win.monitor.seconds_until("news", 1543881)
    check("monitor scheduled a next pass", due is not None, f"in {due}s")
    win.monitor.stop(wait=True)

    win.close()
    print(f"\nartifacts in {tmp}")
    print(f"{len(fails)} failure(s)" if fails else "\nall GUI checks passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
