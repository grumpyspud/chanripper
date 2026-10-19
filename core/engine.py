"""The engine: turn a thread URL into files on disk, and keep them in sync."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import naming, writer
from .api import BoardAdapter, Chan4, Client, parse_thread_url
from .config import Settings, clamp_delay
from .store import Store


@dataclass
class SyncResult:
    board: str
    no: int
    status: str = "idle"          # ok | unchanged | dead | error
    new_posts: int = 0
    new_media: int = 0
    bytes_downloaded: int = 0
    folder: Path | None = None
    error: str = ""
    messages: list[str] = field(default_factory=list)


class Engine:
    """Headless. No GUI imports anywhere below this line."""

    def __init__(self, settings: Settings, store: Store,
                 adapter: BoardAdapter | None = None, client: Client | None = None):
        self.settings = settings
        self.store = store
        self.adapter = adapter or Chan4()
        self.client = client or Client(self.adapter,
                                       interval=settings.request_interval)

    # -- helpers -------------------------------------------------------------
    def apply_settings(self, settings: Settings) -> None:
        """Swap in new settings and propagate what the client caches."""
        self.settings = settings
        limiter = getattr(self.client, "limiter", None)
        if limiter is not None:
            limiter.interval = settings.request_interval

    def thread_dir(self, board: str, no: int, subject: str = "",
                   semantic_url: str = "") -> Path:
        s = self.settings
        return naming.thread_folder(
            s.root, self.adapter.site, board, no, subject, semantic_url,
            folder_mode=s.folder_mode, board_dir=s.board_dir)

    def add(self, url: str, *, monitor: bool | None = None,
            comments: bool | None = None, delay: float | None = None) -> SyncResult:
        """Register a thread and immediately do the first download pass."""
        parsed = parse_thread_url(url)
        if not parsed:
            return SyncResult("", 0, "error", error=f"unrecognised URL: {url!r}")
        board, no = parsed
        s = self.settings
        self.store.add_thread(
            board, no, url=self.adapter.thread_url(board, no),
            monitor=bool(monitor),
            comments=s.default_comments if comments is None else bool(comments),
            delay=clamp_delay(s.default_delay if delay is None else delay))
        return self.sync(board, no)

    def sync(self, board: str, no: int, *, force: bool = False) -> SyncResult:
        rec = self.store.get_thread(board, no)
        if rec is None:
            return SyncResult(board, no, "error", error="thread not registered")

        result = SyncResult(board, no)
        lm = None if force else (rec.get("last_modified") or None)
        fetch = self.client.fetch_thread(board, no, lm)

        self.store.update_thread(board, no, last_check=time.time())

        if fetch.status == 304:
            result.status = "unchanged"
            result.messages.append("no new posts (304)")
            return result
        if fetch.status == 404:
            self.store.update_thread(board, no, status="dead", monitor=0,
                                     error=fetch.error)
            result.status = "dead"
            result.error = fetch.error
            return result
        if fetch.status != 200:
            self.store.update_thread(board, no, status="error", error=fetch.error)
            result.status = "error"
            result.error = fetch.error
            return result

        posts = fetch.posts
        if not posts:
            result.status = "error"
            result.error = "empty thread"
            return result

        op = posts[0]
        # A thread's folder is sticky once created: changing the naming
        # settings must not relocate a thread and orphan the files already in
        # it. New threads pick up whatever the current settings say.
        existing = rec.get("folder")
        if existing and Path(existing).is_dir():
            folder = Path(existing)
        else:
            folder = self.thread_dir(board, no, op.get("sub", ""),
                                     op.get("semantic_url", ""))
        folder.mkdir(parents=True, exist_ok=True)
        result.folder = folder

        known_posts = self.store.known_posts(board, no)
        new_posts = [p for p in posts if p.get("no") not in known_posts]

        # --- media ----------------------------------------------------------
        # Filenames are assigned serially so collision resolution stays
        # deterministic; the transfers themselves run in parallel.
        known_media = self.store.known_media(board, no)
        used = self.store.media_saved_names(board, no)
        pending: list[tuple[dict, int, str]] = []
        for post in posts:
            tim = post.get("tim")
            if not tim or tim in known_media:
                continue
            fname = naming.media_name(post, self.settings.name_mode, used, folder)
            if not fname:
                continue
            used.add(fname.lower())
            pending.append((post, tim, fname))

        def fetch_one(item):
            post, tim, fname = item
            dest = folder / fname
            if dest.exists() and dest.stat().st_size == (post.get("fsize") or -1):
                return item, True, ""
            url = self.adapter.media_url(board, tim, post.get("ext", ""))
            ok, err = self.client.download(url, dest, post.get("fsize"))
            return item, ok, err

        if pending:
            workers = max(1, min(self.settings.media_workers, len(pending)))
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="media") as pool:
                for (post, tim, fname), ok, err in pool.map(fetch_one, pending):
                    self.store.record_media(
                        board, no, tim, post=post.get("no") or 0,
                        saved_as=fname if ok else "",
                        fsize=post.get("fsize") or 0,
                        md5=post.get("md5") or "", ok=ok)
                    if ok:
                        result.new_media += 1
                        result.bytes_downloaded += post.get("fsize") or 0
                    else:
                        result.messages.append(f"media {tim}: {err}")

        # --- posts + comments ----------------------------------------------
        result.new_posts = self.store.record_posts(board, no, posts)

        if rec.get("comments"):
            writer.write_thread_json(folder / "thread.json", posts)
            writer.write_thread_text(folder / "thread.txt", board, no, posts,
                                     self.adapter.thread_url(board, no))

        media_rows = [p for p in posts if p.get("tim")]
        self.store.update_thread(
            board, no,
            subject=op.get("sub", "") or "",
            semantic_url=op.get("semantic_url", "") or "",
            folder=str(folder),
            url=self.adapter.thread_url(board, no),
            last_modified=fetch.last_modified,
            post_count=len(posts),
            media_count=len(media_rows),
            media_bytes=sum(p.get("fsize") or 0 for p in media_rows),
            status="ok", error="")

        result.status = "ok"
        return result
