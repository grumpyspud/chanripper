"""ChanRipper — 4chan thread downloader and monitor.

Entry point for both `python main.py` and the PyInstaller build.
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from core.config import Settings, config_dir
from core.store import Store
from gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ChanRipper")
    app.setOrganizationName("ChanRipper")

    settings = Settings.load()
    store = Store(config_dir() / "state.sqlite")

    window = MainWindow(settings, store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
