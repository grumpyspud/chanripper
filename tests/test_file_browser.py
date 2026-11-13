"""Desktop file browser — offscreen, no window server.

Skips itself when PySide6 isn't installed, so the CI test job (which installs
only requirements-test.txt) stays green.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    from gui.file_browser import FileBrowser, FilePreview, format_ts
    HAVE_QT = True
except Exception:                                   # pragma: no cover
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TestFileBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / "4chan" / "g" / "1"
        (self.folder / "sub").mkdir(parents=True)
        for name in ("a.png", "b.png", "c.webm"):
            (self.folder / name).write_bytes(b"x" * 100)
        self.dlg = FileBrowser(self.root, start=str(self.folder), title="/g/1/")

    def tearDown(self):
        self.dlg.reject()
        self.tmp.cleanup()

    def items(self):
        return [self.dlg.list.item(i) for i in range(self.dlg.list.count())]

    def file_items(self):
        return [i for i in self.items() if not i.data(Qt.UserRole + 1)]

    def by_name(self, name):
        return next(i for i in self.items() if i.text().split("\n")[0] == name)

    def test_lists_the_thread_folder(self):
        names = {i.text().split("\n")[0] for i in self.items()}
        self.assertEqual(names, {"sub", "a.png", "b.png", "c.webm"})

    def test_folders_cannot_be_marked(self):
        self.dlg.toggle_mark(self.by_name("sub"))
        self.assertEqual(self.dlg.marked, set())
        self.assertFalse(self.dlg.delete_btn.isEnabled())

    def test_marking_enables_the_delete_button(self):
        item = self.file_items()[0]
        self.assertFalse(self.dlg.delete_btn.isEnabled())
        self.dlg.toggle_mark(item)
        self.assertEqual(len(self.dlg.marked), 1)
        self.assertTrue(self.dlg.delete_btn.isEnabled())
        self.assertIn("(1)", self.dlg.delete_btn.text())

        self.dlg.toggle_mark(item)                       # clicking again unmarks
        self.assertEqual(self.dlg.marked, set())
        self.assertFalse(self.dlg.delete_btn.isEnabled())

    def test_mark_all_then_delete_only_files(self):
        self.dlg._toggle_all()
        self.assertEqual(len(self.dlg.marked), 3)

        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        self.dlg._delete_marked()

        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["sub"])
        self.assertEqual(self.dlg.marked, set())
        self.assertEqual(len(self.file_items()), 0)
        self.assertFalse(self.dlg.delete_btn.isEnabled())

    def test_cancel_keeps_the_files(self):
        self.dlg._toggle_all()
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        self.dlg._delete_marked()
        self.assertEqual(len(list(self.folder.iterdir())), 4)

    def test_cannot_navigate_outside_the_root(self):
        self.dlg.browse("../../../../etc")
        self.assertEqual(self.dlg.path, "4chan/g/1")

    def test_preview_builds_and_navigates(self):
        p = FilePreview(self.dlg, 0)
        try:
            self.assertEqual(p.files, ["4chan/g/1/a.png", "4chan/g/1/b.png",
                                       "4chan/g/1/c.webm"])
            self.assertEqual(p.name_label.text(), "a.png")
            self.assertFalse(p.prev_btn.isEnabled())       # first item
            self.assertTrue(p.next_btn.isEnabled())
            # marking through the preview checkbox updates the parent's set
            p.mark_box.setChecked(True)
            self.assertIn("4chan/g/1/a.png", self.dlg.marked)
            # next/prev navigation
            p._nav(1)
            self.assertEqual(p.name_label.text(), "b.png")
            self.assertEqual(p.count_label.text(), "2 / 3")
            p._nav(-1)
            self.assertEqual(p.name_label.text(), "a.png")
        finally:
            p.reject()

    def test_preview_zoom_stays_in_bounds(self):
        p = FilePreview(self.dlg, 0)
        try:
            p._set_zoom(100.0)
            self.assertLessEqual(p.scale, 6.0)
            p._set_zoom(0.0001)
            self.assertGreaterEqual(p.scale, 0.25)
        finally:
            p.reject()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TestFormatTs(unittest.TestCase):
    def test_iso_us_euro_shapes(self):
        ts = 1700000000
        self.assertRegex(format_ts(ts, "iso"), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertRegex(format_ts(ts, "us"),
                         r"^\d{2}/\d{2}/\d{4} \d{1,2}:\d{2} (AM|PM)$")
        self.assertRegex(format_ts(ts, "euro"), r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$")

    def test_relative(self):
        self.assertEqual(format_ts(time.time(), "relative"), "just now")
        self.assertEqual(format_ts(time.time() - 7200, "relative"), "2h ago")

    def test_missing_ts_returns_empty(self):
        self.assertEqual(format_ts(0, "iso"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
