"""Web API tests — no network. Run: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

# must be set before web.app is imported: STATE is built at import time
os.environ["CHANRIPPER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="cr-web-cfg-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient                       # noqa: E402
from test_core import FakeAdapter, FakeClient, post             # noqa: E402

from core.config import Settings                                # noqa: E402
from core.engine import Engine                                  # noqa: E402
from core.store import Store                                    # noqa: E402
from web import app as webapp                                   # noqa: E402

THREAD = [
    post(1, tim=111, fname="file", com="op line<br>second"),
    post(2, resto=1, tim=222, fname="file", com="reply"),
    post(3, resto=1, com="text only, no media"),
]


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        s = Settings(root=str(base / "dl"), board_dir=True, folder_mode="both",
                     name_mode="uploader", default_comments=True)
        s.normalize()
        self.store = Store(base / "state.sqlite")
        self.fake = FakeClient([(THREAD, "LM1")])
        self.engine = Engine(s, self.store, FakeAdapter(), self.fake)

        webapp.STATE.settings = s
        webapp.STATE.store = self.store
        webapp.STATE.engine = self.engine
        webapp.STATE.monitor = None
        webapp.STATE.ensure_monitor = lambda: None       # keep tests single-threaded

        self.tc = TestClient(webapp.app)
        self.tc.__enter__()

    def tearDown(self) -> None:
        self.tc.__exit__(None, None, None)
        self.store.close()
        self.tmp.cleanup()

    # -- meta ---------------------------------------------------------------
    def test_healthz(self):
        r = self.tc.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text.strip(), "ok")

    def test_state_shape(self):
        body = self.tc.get("/api/state").json()
        for key in ("version", "threads", "settings", "monitoring"):
            self.assertIn(key, body)
        self.assertEqual(body["threads"], [])

    def test_index_served(self):
        r = self.tc.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("ChanRipper", r.text)
        self.assertIn("Download Thread", r.text)

    # -- adding -------------------------------------------------------------
    def test_add_rejects_bad_url(self):
        r = self.tc.post("/api/threads", json={"url": "https://example.com/x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("4chan", r.json()["detail"])

    def test_add_downloads_and_writes_files(self):
        r = self.tc.post("/api/threads",
                         json={"url": "https://boards.4chan.org/news/thread/1/",
                               "monitor": False, "comments": True})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["new_posts"], 3)
        self.assertEqual(body["new_media"], 2)

        folder = Path(self.engine.settings.root) / "4chan" / "news" / "1"
        self.assertTrue((folder / "file.png").exists())
        self.assertTrue((folder / "file (222).png").exists())
        self.assertTrue((folder / "thread.json").exists())
        self.assertTrue((folder / "thread.txt").exists())

    def test_add_then_state_lists_it(self):
        self.tc.post("/api/threads", json={"url": "news/1", "comments": True})
        threads = self.tc.get("/api/state").json()["threads"]
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["board"], "news")
        self.assertEqual(threads[0]["post_count"], 3)
        self.assertFalse(threads[0]["monitor"])

    def test_add_short_form_url(self):
        r = self.tc.post("/api/threads", json={"url": "news/1"})
        self.assertEqual(r.status_code, 200)

    # -- editing ------------------------------------------------------------
    def test_patch_monitor_and_delay(self):
        self.tc.post("/api/threads", json={"url": "news/1"})
        r = self.tc.patch("/api/threads/news/1",
                          json={"monitor": True, "delay": 5, "comments": True})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["monitor"])
        self.assertTrue(body["comments"])
        self.assertEqual(body["delay"], 5.0)

    def test_delay_floor_enforced(self):
        self.tc.post("/api/threads", json={"url": "news/1"})
        body = self.tc.patch("/api/threads/news/1", json={"delay": 0.1}).json()
        self.assertEqual(body["delay"], 2.0)

    def test_patch_unknown_thread_404(self):
        r = self.tc.patch("/api/threads/news/999", json={"monitor": True})
        self.assertEqual(r.status_code, 404)

    def test_check_endpoint(self):
        self.tc.post("/api/threads", json={"url": "news/1"})
        r = self.tc.post("/api/threads/news/1/check")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")   # force=True re-fetches

    def test_delete(self):
        self.tc.post("/api/threads", json={"url": "news/1"})
        r = self.tc.delete("/api/threads/news/1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.tc.get("/api/state").json()["threads"], [])

    # -- settings -----------------------------------------------------------
    def test_settings_roundtrip(self):
        r = self.tc.put("/api/settings", json={"default_delay": 4, "name_mode": "number",
                                               "folder_mode": "number",
                                               "board_dir": False})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["default_delay"], 4.0)
        self.assertEqual(body["name_mode"], "number")
        self.assertFalse(body["board_dir"])
        self.assertEqual(self.tc.get("/api/settings").json()["name_mode"], "number")

    def test_settings_rejects_bad_enum(self):
        body = self.tc.put("/api/settings", json={"folder_mode": "nonsense"}).json()
        self.assertEqual(body["folder_mode"], "both")          # normalized

    # -- file browser -------------------------------------------------------
    def _seed(self):
        self.tc.post("/api/threads", json={"url": "news/1", "comments": True})

    def test_files_root_listing(self):
        self._seed()
        body = self.tc.get("/api/files").json()
        self.assertEqual(body["path"], "")
        self.assertIsNone(body["parent"])
        self.assertTrue(any(e["name"] == "4chan" and e["dir"] for e in body["entries"]))

    def test_files_nested_listing(self):
        self._seed()
        body = self.tc.get("/api/files", params={"path": "4chan/news/1"}).json()
        names = {e["name"] for e in body["entries"]}
        self.assertIn("file.png", names)
        self.assertIn("thread.json", names)
        self.assertEqual(body["parent"], "4chan/news")
        kinds = {e["name"]: e["kind"] for e in body["entries"]}
        self.assertEqual(kinds["file.png"], "image")
        self.assertEqual(kinds["thread.json"], "file")

    def test_files_path_traversal_blocked(self):
        for bad in ("../../etc", "/etc/passwd", "..\\..\\windows"):
            r = self.tc.get("/api/files", params={"path": bad})
            self.assertIn(r.status_code, (400, 404), f"{bad} -> {r.status_code}")

    def test_raw_file_served(self):
        self._seed()
        r = self.tc.get("/api/files/raw", params={"path": "4chan/news/1/file.png"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"x" * 1000)

    def test_raw_file_traversal_blocked(self):
        r = self.tc.get("/api/files/raw", params={"path": "../../etc/passwd"})
        self.assertEqual(r.status_code, 400)

    def test_missing_file_404(self):
        r = self.tc.get("/api/files/raw", params={"path": "nope.png"})
        self.assertEqual(r.status_code, 404)

    # -- zip ----------------------------------------------------------------
    def test_zip_download(self):
        self._seed()
        r = self.tc.get("/api/threads/news/1/zip")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = set(zf.namelist())
        self.assertIn("file.png", names)
        self.assertIn("thread.json", names)
        self.assertIn("thread.txt", names)

    def test_zip_unknown_thread_404(self):
        r = self.tc.get("/api/threads/news/404/zip")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
