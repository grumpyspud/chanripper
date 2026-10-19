"""SQLite state: monitored threads, seen posts and the media ledger."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
  board         TEXT NOT NULL,
  no            INTEGER NOT NULL,
  subject       TEXT DEFAULT '',
  semantic_url  TEXT DEFAULT '',
  url           TEXT DEFAULT '',
  folder        TEXT DEFAULT '',
  monitor       INTEGER DEFAULT 0,
  comments      INTEGER DEFAULT 0,
  delay         REAL DEFAULT 10.0,
  status        TEXT DEFAULT 'idle',
  last_modified TEXT DEFAULT '',
  last_check    REAL DEFAULT 0,
  added         REAL DEFAULT 0,
  post_count    INTEGER DEFAULT 0,
  media_count   INTEGER DEFAULT 0,
  media_bytes   INTEGER DEFAULT 0,
  error         TEXT DEFAULT '',
  PRIMARY KEY (board, no)
);
CREATE TABLE IF NOT EXISTS posts (
  board    TEXT NOT NULL,
  thread   INTEGER NOT NULL,
  post     INTEGER NOT NULL,
  resto    INTEGER DEFAULT 0,
  ts       INTEGER DEFAULT 0,
  name     TEXT DEFAULT '',
  has_media INTEGER DEFAULT 0,
  PRIMARY KEY (board, thread, post)
);
CREATE TABLE IF NOT EXISTS media (
  board    TEXT NOT NULL,
  thread   INTEGER NOT NULL,
  tim      INTEGER NOT NULL,
  post     INTEGER DEFAULT 0,
  saved_as TEXT DEFAULT '',
  fsize    INTEGER DEFAULT 0,
  md5      TEXT DEFAULT '',
  ok       INTEGER DEFAULT 0,
  PRIMARY KEY (board, thread, tim)
);
"""

THREAD_FIELDS = (
    "subject", "semantic_url", "url", "folder", "monitor", "comments", "delay",
    "status", "last_modified", "last_check", "added", "post_count",
    "media_count", "media_bytes", "error",
)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- threads -----------------------------------------------------------
    def get_thread(self, board: str, no: int) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM threads WHERE board=? AND no=?", (board, no)).fetchone()
        return dict(row) if row else None

    def list_threads(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM threads ORDER BY added").fetchall()
        return [dict(r) for r in rows]

    def add_thread(self, board: str, no: int, *, url: str = "", folder: str = "",
                   monitor: bool = False, comments: bool = False,
                   delay: float = 10.0, subject: str = "",
                   semantic_url: str = "") -> dict:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO threads"
                "(board,no,url,folder,monitor,comments,delay,subject,semantic_url,added)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (board, no, url, folder, int(monitor), int(comments), float(delay),
                 subject, semantic_url, time.time()))
            self._db.commit()
        return self.get_thread(board, no)  # type: ignore[return-value]

    def update_thread(self, board: str, no: int, **fields) -> None:
        sets = {k: v for k, v in fields.items() if k in THREAD_FIELDS}
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        with self._lock:
            self._db.execute(f"UPDATE threads SET {cols} WHERE board=? AND no=?",
                             (*sets.values(), board, no))
            self._db.commit()

    def remove_thread(self, board: str, no: int) -> None:
        with self._lock:
            for t in ("threads", "posts", "media"):
                self._db.execute(f"DELETE FROM {t} WHERE board=? AND no=?",
                                 (board, no))
            self._db.commit()

    # ---- posts -------------------------------------------------------------
    def known_posts(self, board: str, no: int) -> set[int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT post FROM posts WHERE board=? AND thread=?", (board, no))
            return {r["post"] for r in rows}

    def record_posts(self, board: str, no: int, posts: list[dict]) -> int:
        new = 0
        with self._lock:
            for p in posts:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO posts"
                    "(board,thread,post,resto,ts,name,has_media) VALUES(?,?,?,?,?,?,?)",
                    (board, no, p.get("no"), p.get("resto") or 0, p.get("time") or 0,
                     p.get("name") or "", 1 if p.get("tim") else 0))
                new += cur.rowcount if cur.rowcount > 0 else 0
            self._db.commit()
        return new

    # ---- media ledger ------------------------------------------------------
    def known_media(self, board: str, no: int) -> set[int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT tim FROM media WHERE board=? AND thread=?", (board, no))
            return {r["tim"] for r in rows}

    def record_media(self, board: str, no: int, tim: int, *, post: int = 0,
                     saved_as: str = "", fsize: int = 0, md5: str = "",
                     ok: bool = True) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO media"
                "(board,thread,tim,post,saved_as,fsize,md5,ok) VALUES(?,?,?,?,?,?,?,?)",
                (board, no, tim, post, saved_as, fsize, md5, int(ok)))
            self._db.commit()

    def media_saved_names(self, board: str, no: int) -> set[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT saved_as FROM media WHERE board=? AND thread=?", (board, no))
            return {r["saved_as"].lower() for r in rows if r["saved_as"]}
