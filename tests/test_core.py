"""Core tests — no network. Run: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import naming, parser, writer                      # noqa: E402
from core.api import BoardAdapter, ThreadFetch, parse_thread_url  # noqa: E402
from core.config import Settings, clamp_delay                # noqa: E402
from core.engine import Engine                               # noqa: E402
from core.store import Store                                 # noqa: E402


# --------------------------------------------------------------------------
class TestUrls(unittest.TestCase):
    def test_board_url(self):
        self.assertEqual(
            parse_thread_url("https://boards.4chan.org/news/thread/1543881/"
                             "air-force-commando-dies-after-falling-from"),
            ("news", 1543881))

    def test_api_url(self):
        self.assertEqual(
            parse_thread_url("https://a.4cdn.org/news/thread/1543881.json"),
            ("news", 1543881))

    def test_short_form(self):
        self.assertEqual(parse_thread_url("news/1543881"), ("news", 1543881))
        self.assertEqual(parse_thread_url("news:1543881"), ("news", 1543881))

    def test_junk(self):
        self.assertIsNone(parse_thread_url("https://example.com/foo"))
        self.assertIsNone(parse_thread_url(""))


class TestNaming(unittest.TestCase):
    def test_illegal_chars(self):
        self.assertEqual(naming.sanitize('a<b>c:d"e/f\\g|h?i*j'),
                         "a_b_c_d_e_f_g_h_i_j")

    def test_trailing_dots_spaces(self):
        self.assertEqual(naming.sanitize("report... "), "report")

    def test_reserved(self):
        self.assertTrue(naming.sanitize("CON.txt").startswith("_"))
        self.assertTrue(naming.sanitize("lpt1").startswith("_"))

    def test_empty_fallback(self):
        self.assertEqual(naming.sanitize("", "unnamed"), "unnamed")

    def test_thread_label_modes(self):
        args = (1543881, "Air Force commando dies", "air-force-commando-dies")
        self.assertEqual(naming.thread_label(*args, mode="number"), "1543881")
        self.assertEqual(naming.thread_label(*args, mode="name"),
                         "air-force-commando-dies")
        self.assertEqual(naming.thread_label(*args, mode="both"),
                         "1543881 - air-force-commando-dies")

    def test_thread_label_no_subject(self):
        self.assertEqual(naming.thread_label(99, "", "", mode="name"), "99")

    def test_media_uploader_mode(self):
        post = {"tim": 1792348901932343, "ext": ".png", "filename": "file"}
        self.assertEqual(naming.media_name(post, "uploader"), "file.png")

    def test_media_number_mode(self):
        post = {"tim": 1792348901932343, "ext": ".png", "filename": "file"}
        self.assertEqual(naming.media_name(post, "number"), "1792348901932343.png")

    def test_media_collision_uses_tim(self):
        a = {"tim": 111, "ext": ".png", "filename": "file"}
        b = {"tim": 1792348901932343, "ext": ".png", "filename": "file"}
        used = {naming.media_name(a, "uploader").lower()}
        self.assertEqual(naming.media_name(b, "uploader", used),
                         "file (1792348901932343).png")

    def test_media_collision_twice(self):
        used = {"file.png", "file (222).png"}
        p = {"tim": 222, "ext": ".png", "filename": "file"}
        self.assertEqual(naming.media_name(p, "uploader", used),
                         "file (222) (2).png")

    def test_media_missing_tim(self):
        self.assertIsNone(naming.media_name({"ext": ".png"}, "uploader"))


class TestParser(unittest.TestCase):
    def test_br_and_wbr(self):
        self.assertEqual(parser.comment_to_text("a<br>b<wbr>c"), "a\nbc")

    def test_greentext_and_quotes(self):
        html = '<span class="quote">&gt;hello</span><br><a href="#p1" class="quotelink">&gt;&gt;1</a>'
        self.assertEqual(parser.comment_to_text(html), ">hello\n>>1")

    def test_external_link_keeps_url(self):
        html = '<a href="https://x.test/a" target="_blank">x.test/a</a>'
        self.assertEqual(parser.comment_to_text(html), "x.test/a <https://x.test/a>")

    def test_entities_and_tags(self):
        self.assertEqual(parser.comment_to_text("<b>a</b>&amp;b&lt;c"),
                         "a&b<c")

    def test_code_block(self):
        out = parser.comment_to_text('<pre class="prettyprint">x &gt; y</pre>')
        self.assertIn("```", out)
        self.assertIn("x > y", out)

    def test_none(self):
        self.assertEqual(parser.comment_to_text(None), "")


class TestTree(unittest.TestCase):
    def test_nesting(self):
        posts = [
            {"no": 1, "resto": 0, "com": "op"},
            {"no": 2, "resto": 1, "com": "reply"},
            {"no": 3, "resto": 2, "com": "reply to reply"},
        ]
        text = writer.render_text("news", 1, posts, "u")
        lines = text.splitlines()
        i1 = next(i for i, l in enumerate(lines) if "No.1" in l)
        i2 = next(i for i, l in enumerate(lines) if "No.2" in l)
        i3 = next(i for i, l in enumerate(lines) if "No.3" in l)
        ind = lambda s: len(s) - len(s.lstrip())
        self.assertLess(ind(lines[i1]), ind(lines[i2]))
        self.assertLess(ind(lines[i2]), ind(lines[i3]))

    def test_orphan_reply_becomes_top_level(self):
        posts = [{"no": 1, "resto": 0}, {"no": 9, "resto": 12345}]
        text = writer.render_text("news", 1, posts, "u")
        self.assertIn("No.9", text)


# --------------------------------------------------------------------------
class FakeAdapter(BoardAdapter):
    site = "4chan"

    def thread_url(self, board, no):
        return f"https://boards.4chan.org/{board}/thread/{no}/"

    def thread_api(self, board, no):
        return f"https://a.4cdn.org/{board}/thread/{no}.json"

    def media_url(self, board, tim, ext):
        return f"media://{board}/{tim}{ext}"


class FakeClient:
    """Serves a scripted sequence of thread states."""

    def __init__(self, states, not_modified_when_same=True, limit=None):
        self.states = list(states)
        self.calls = 0
        self.downloads: list[str] = []
        self.not_modified_when_same = not_modified_when_same
        self._limit = limit

    def image_limit(self, board):
        return self._limit

    def fetch_thread(self, board, no, last_modified=None):
        state = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        if isinstance(state, int):                       # 404 / 429 etc.
            return ThreadFetch(state, [], "", f"HTTP {state}")
        posts, lm = state
        if last_modified and last_modified == lm and self.not_modified_when_same:
            return ThreadFetch(304, [], lm)
        return ThreadFetch(200, posts, lm)

    def download(self, url, dest, expected_size=None):
        self.downloads.append(url)
        Path(dest).write_bytes(b"x" * (expected_size or 10))
        return True, ""


def post(no, resto=0, tim=None, fname=None, ext=".png", size=1000, com=None):
    p = {"no": no, "resto": resto, "time": 1700000000 + no, "name": "Anonymous"}
    if com:
        p["com"] = com
    if tim:
        p.update({"tim": tim, "filename": fname or "file", "ext": ext,
                  "fsize": size, "md5": "AAA=", "w": 10, "h": 10})
    return p


class TestEngineIncremental(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = Settings(root=str(root / "dl"), board_dir=True,
                                 folder_mode="both", name_mode="uploader")
        self.settings.normalize()
        self.store = Store(root / "state.sqlite")
        self.adapter = FakeAdapter()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def make(self, states):
        client = FakeClient(states)
        return Engine(self.settings, self.store, self.adapter, client), client

    def test_first_pass_downloads_everything(self):
        posts = [post(1, tim=111, fname="a", com="op"),
                 post(2, resto=1, com="hi"),
                 post(3, resto=1, tim=222, fname="b")]
        engine, client = self.make([(posts, "LM1")])
        res = engine.add("news/1", comments=True)
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.new_posts, 3)
        self.assertEqual(res.new_media, 2)
        self.assertEqual(len(client.downloads), 2)
        folder = Path(self.settings.root) / "4chan" / "news" / "1"
        self.assertTrue((folder / "a.png").exists())
        self.assertTrue((folder / "b.png").exists())
        self.assertTrue((folder / "thread.json").exists())
        self.assertTrue((folder / "thread.txt").exists())

    def test_second_pass_only_gets_new_media(self):
        first = [post(1, tim=111, fname="a"), post(2, resto=1)]
        second = first + [post(3, resto=1, tim=222, fname="b"),
                          post(4, resto=1, com="text only")]
        engine, client = self.make([(first, "LM1"), (second, "LM2")])
        engine.add("news/1", comments=True)
        self.assertEqual(len(client.downloads), 1)

        res = engine.sync("news", 1)
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.new_posts, 2)
        self.assertEqual(res.new_media, 1)
        self.assertEqual(len(client.downloads), 2)      # only the new one
        folder = Path(self.settings.root) / "4chan" / "news" / "1"
        self.assertTrue((folder / "b.png").exists())

    def test_unchanged_thread_is_a_304_no_op(self):
        posts = [post(1, tim=111, fname="a")]
        engine, client = self.make([(posts, "LM1")])
        engine.add("news/1")
        before = len(client.downloads)
        res = engine.sync("news", 1)
        self.assertEqual(res.status, "unchanged")
        self.assertEqual(len(client.downloads), before)

    def test_404_marks_dead_and_stops_monitoring(self):
        posts = [post(1, tim=111, fname="a")]
        engine, client = self.make([(posts, "LM1"), 404])
        engine.add("news/1", monitor=True)
        self.assertEqual(self.store.get_thread("news", 1)["monitor"], 1)
        res = engine.sync("news", 1)
        self.assertEqual(res.status, "dead")
        rec = self.store.get_thread("news", 1)
        self.assertEqual(rec["status"], "dead")
        self.assertEqual(rec["monitor"], 0)

    def test_collision_between_two_posts(self):
        posts = [post(1, tim=111, fname="file"),
                 post(2, resto=1, tim=222, fname="file")]
        engine, client = self.make([(posts, "LM1")])
        engine.add("news/1")
        folder = Path(self.settings.root) / "4chan" / "news" / "1"
        names = sorted(p.name for p in folder.iterdir() if p.suffix == ".png")
        self.assertEqual(names, ["file (222).png", "file.png"])

    def test_comment_backfill_on_existing_thread(self):
        posts = [post(1, tim=111, fname="a", com="op text")]
        engine, client = self.make([(posts, "LM1")])
        engine.add("news/1", comments=False)
        folder = Path(self.settings.root) / "4chan" / "news" / "1"
        self.assertFalse((folder / "thread.txt").exists())

        # now turn comments on and force a refresh
        self.store.update_thread("news", 1, comments=1, last_modified="")
        engine.sync("news", 1, force=True)
        self.assertTrue((folder / "thread.txt").exists())
        self.assertIn("op text", (folder / "thread.txt").read_text(encoding="utf-8"))

    def test_number_naming_mode(self):
        self.settings.name_mode = "number"
        posts = [post(1, tim=111, fname="whatever", ext=".webm")]
        engine, client = self.make([(posts, "LM1")])
        engine.add("news/1")
        folder = Path(self.settings.root) / "4chan" / "news" / "1"
        self.assertTrue((folder / "111.webm").exists())

    def test_remove_thread_clears_all_three_tables(self):
        # regression: posts/media key on `thread`, threads keys on `no`
        posts = [post(1, tim=111, fname="a"), post(2, resto=1, tim=222, fname="b")]
        engine, client = self.make([(posts, "LM1")])
        engine.add("news/1", comments=True)
        self.assertIsNotNone(self.store.get_thread("news", 1))
        self.assertTrue(self.store.known_media("news", 1))
        self.assertTrue(self.store.known_posts("news", 1))

        self.store.remove_thread("news", 1)

        self.assertIsNone(self.store.get_thread("news", 1))
        self.assertEqual(self.store.known_media("news", 1), set())
        self.assertEqual(self.store.known_posts("news", 1), set())

    def test_removed_thread_can_be_re_added(self):
        posts = [post(1, tim=111, fname="a")]
        engine, client = self.make([(posts, "LM1"), (posts, "LM2")])
        engine.add("news/1")
        self.store.remove_thread("news", 1)
        res = engine.add("news/1")
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.new_media, 1)

    def test_folder_is_sticky_when_naming_settings_change(self):
        # regression: flipping folder_mode must not relocate a thread and
        # orphan the files already downloaded into its original folder.
        posts = [post(1, tim=111, fname="a")]
        engine, client = self.make([(posts, "LM1"), (posts, "LM2")])
        engine.add("news/1")
        original = Path(self.settings.root) / "4chan" / "news" / "1"
        self.assertTrue((original / "a.png").exists())

        self.settings.folder_mode = "number"
        self.settings.board_dir = False
        engine.apply_settings(self.settings)
        res = engine.sync("news", 1, force=True)

        self.assertEqual(Path(res.folder), original)
        self.assertTrue((original / "a.png").exists())
        self.assertFalse((Path(self.settings.root) / "1").exists())
        self.assertEqual(engine.store.get_thread("news", 1)["folder"], str(original))


class TestMediaLimit(unittest.TestCase):
    """A media-full thread with comments off has nothing left to watch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = Settings(root=str(root / "dl"), board_dir=True,
                                 folder_mode="both", name_mode="uploader")
        self.settings.normalize()
        self.store = Store(root / "state.sqlite")
        self.adapter = FakeAdapter()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @staticmethod
    def thread(files: int) -> list[dict]:
        posts = [post(1, tim=1000, fname="op")]
        posts += [post(n, resto=1, tim=1000 + n, fname=f"f{n}")
                  for n in range(2, files + 1)]
        return posts

    def run_sync(self, files: int, *, limit, comments=False, monitor=True):
        # start clean so a second call in the same test isn't a 304 no-op
        self.store.remove_thread("news", 1)
        client = FakeClient([(self.thread(files), "LM1")], limit=limit)
        engine = Engine(self.settings, self.store, self.adapter, client)
        engine.add("news/1", monitor=monitor, comments=comments)
        return self.store.get_thread("news", 1)

    def test_stops_when_full_and_comments_off(self):
        rec = self.run_sync(4, limit=3)
        self.assertEqual(rec["monitor"], 0)
        self.assertEqual(rec["status"], "full")

    def test_keeps_monitoring_when_comments_on(self):
        rec = self.run_sync(4, limit=3, comments=True)
        self.assertEqual(rec["monitor"], 1)
        self.assertEqual(rec["status"], "ok")

    def test_still_monitors_below_the_limit(self):
        rec = self.run_sync(3, limit=3)
        self.assertEqual(rec["monitor"], 1)
        self.assertEqual(rec["status"], "ok")

    def test_boundary_is_limit_plus_one(self):
        # 4chan doesn't count the OP's file, so a 150-cap board is full at 151.
        self.assertEqual(self.run_sync(150, limit=150)["monitor"], 1)
        self.assertEqual(self.run_sync(151, limit=150)["monitor"], 0)

    def test_unknown_limit_never_stops_monitoring(self):
        rec = self.run_sync(50, limit=None)
        self.assertEqual(rec["monitor"], 1)
        self.assertEqual(rec["status"], "ok")


class TestBoardLimits(unittest.TestCase):
    def test_parse_board_limits(self):
        payload = {"boards": [
            {"board": "g", "image_limit": 150},
            {"board": "v", "image_limit": 300},
            {"board": "x"},                       # no limit reported
            {"board": "y", "image_limit": 0},
        ]}
        self.assertEqual(BoardAdapter.parse_board_limits(payload),
                         {"g": 150, "v": 300})

    def test_parse_junk(self):
        self.assertEqual(BoardAdapter.parse_board_limits({}), {})
        self.assertEqual(BoardAdapter.parse_board_limits(None), {})


class TestConfig(unittest.TestCase):
    def test_delay_floor(self):
        self.assertEqual(clamp_delay(1), 2.0)
        self.assertEqual(clamp_delay(10), 10.0)
        self.assertEqual(clamp_delay("junk"), 10.0)

    def test_bad_enum_falls_back(self):
        s = Settings(folder_mode="nope", name_mode="nope").normalize()
        self.assertEqual(s.folder_mode, "both")
        self.assertEqual(s.name_mode, "uploader")

    def test_date_format_falls_back(self):
        s = Settings(date_format="nope").normalize()
        self.assertEqual(s.date_format, "iso")

    def test_date_format_roundtrip(self):
        for fmt in ("iso", "us", "euro", "relative"):
            s = Settings(date_format=fmt).normalize()
            self.assertEqual(s.date_format, fmt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
