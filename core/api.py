"""Board adapters, URL parsing and a globally paced HTTP client.

The board layer is pluggable on purpose: everything downstream works against
BoardAdapter, so another imageboard can be added without touching the engine.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

THREAD_URL_RE = re.compile(
    r"(?:boards\.4chan(?:nel)?\.org|4chan\.org)/(?P<board>[a-z0-9]{1,10})/thread/(?P<no>\d+)",
    re.I)
THREAD_API_RE = re.compile(
    r"a\.4cdn\.org/(?P<board>[a-z0-9]{1,10})/thread/(?P<no>\d+)\.json", re.I)
SHORT_RE = re.compile(r"^(?P<board>[a-z0-9]{1,10})[/ :#](?P<no>\d+)$", re.I)


def parse_thread_url(text: str) -> tuple[str, int] | None:
    """Accept a boards URL, an a.4cdn.org API URL, or 'news/1543881'."""
    text = (text or "").strip()
    if not text:
        return None
    for rx in (THREAD_URL_RE, THREAD_API_RE, SHORT_RE):
        m = rx.search(text)
        if m:
            return m.group("board").lower(), int(m.group("no"))
    return None


class RateLimiter:
    """Global pacer: at most one outbound request per `interval` seconds."""

    def __init__(self, interval: float = 1.0):
        self.interval = float(interval)
        self._lock = threading.Lock()
        self._next = 0.0
        self._penalty = 0.0

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                ready = max(self._next, now + self._penalty)
                wait = ready - now
                if wait <= 0:
                    self._next = now + self.interval
                    self._penalty = 0.0
                    return
            time.sleep(min(wait, 0.25))

    def penalize(self, seconds: float) -> None:
        with self._lock:
            self._penalty = max(self._penalty, float(seconds))


@dataclass
class ThreadFetch:
    status: int          # 200 | 304 | 404 | 429 | 0 (network error)
    posts: list
    last_modified: str = ""
    error: str = ""

    @property
    def changed(self) -> bool:
        return self.status == 200


class BoardAdapter:
    site = "?"
    #: Where the board list (and therefore each board's media cap) lives.
    boards_url: str | None = None

    @staticmethod
    def parse_board_limits(payload: dict) -> dict[str, int]:
        """`board -> image_limit` out of a /boards.json payload."""
        out: dict[str, int] = {}
        for b in (payload or {}).get("boards", []):
            limit = b.get("image_limit")
            if b.get("board") and isinstance(limit, int) and limit > 0:
                out[str(b["board"]).lower()] = limit
        return out

    def thread_url(self, board: str, no: int) -> str:
        raise NotImplementedError

    def thread_api(self, board: str, no: int) -> str:
        raise NotImplementedError

    def media_url(self, board: str, tim: int, ext: str) -> str:
        raise NotImplementedError


class Chan4(BoardAdapter):
    site = "4chan"
    API = "https://a.4cdn.org"
    CDN = "https://i.4cdn.org"
    WWW = "https://boards.4chan.org"
    boards_url = f"{API}/boards.json"

    def thread_url(self, board: str, no: int) -> str:
        return f"{self.WWW}/{board}/thread/{no}/"

    def thread_api(self, board: str, no: int) -> str:
        return f"{self.API}/{board}/thread/{no}.json"

    def media_url(self, board: str, tim: int, ext: str) -> str:
        return f"{self.CDN}/{board}/{tim}{ext}"


ADAPTERS: dict[str, type[BoardAdapter]] = {"4chan": Chan4}


class Client:
    def __init__(self, adapter: BoardAdapter | None = None,
                 interval: float = 1.0, timeout: float = 30.0,
                 media_interval: float = 0.1):
        self.adapter = adapter or Chan4()
        # API limiter: 4chan asks for <=1 req/s against a.4cdn.org
        self.limiter = RateLimiter(interval)
        # Media limiter: images come from Cloudflare's i.4cdn.org, which has no
        # such limit. Sharing the API limiter made a 200-file thread take
        # minutes; this one is only here to stop us opening 500 sockets.
        self.media_limiter = RateLimiter(media_interval)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept": "*/*"})
        # board -> image_limit, fetched once from /boards.json. Caps differ per
        # board (150 on /g/, 300 on /v/ and /a/), so this can't be a constant.
        self._board_limits: dict[str, int] | None = None
        self._limits_lock = threading.Lock()

    def image_limit(self, board: str) -> int | None:
        """4chan's attachment cap for `board`, or None if it can't be learned.

        Failure is not cached: the next sync tries again, so one bad request at
        startup doesn't disable the media-full check for the process lifetime.
        """
        board = (board or "").lower()
        with self._limits_lock:
            if self._board_limits is None:
                url = getattr(self.adapter, "boards_url", None)
                if not url:
                    return None
                try:
                    r = self._get(url)
                except requests.RequestException:
                    return None
                if r.status_code != 200:
                    return None
                try:
                    self._board_limits = self.adapter.parse_board_limits(r.json())
                except ValueError:
                    return None
            return self._board_limits.get(board)

    def _get(self, url: str, headers: dict | None = None, stream: bool = False,
             limiter: RateLimiter | None = None):
        (limiter or self.limiter).acquire()
        r = self.session.get(url, headers=headers or {}, timeout=self.timeout,
                             stream=stream)
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After", "5"))
            except (TypeError, ValueError):
                wait = 5.0
            (limiter or self.limiter).penalize(max(wait, 1.0))
        return r

    def fetch_thread(self, board: str, no: int,
                     last_modified: str | None = None) -> ThreadFetch:
        headers = {"If-Modified-Since": last_modified} if last_modified else {}
        try:
            r = self._get(self.adapter.thread_api(board, no), headers=headers)
        except requests.RequestException as exc:
            return ThreadFetch(0, [], last_modified or "", str(exc))
        lm = r.headers.get("Last-Modified") or (last_modified or "")
        if r.status_code == 304:
            return ThreadFetch(304, [], lm)
        if r.status_code == 404:
            return ThreadFetch(404, [], lm, "thread not found (expired or pruned)")
        if r.status_code != 200:
            return ThreadFetch(r.status_code, [], lm, f"HTTP {r.status_code}")
        try:
            posts = r.json().get("posts", [])
        except ValueError:
            return ThreadFetch(200, [], lm, "invalid JSON")
        return ThreadFetch(200, posts, lm)

    def download(self, url: str, dest, expected_size: int | None = None):
        """Stream to <dest>.part then atomically rename. Returns (ok, error)."""
        from pathlib import Path
        dest = Path(dest)
        try:
            r = self._get(url, stream=True, limiter=self.media_limiter)
        except requests.RequestException as exc:
            return False, str(exc)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        tmp = dest.with_name(dest.name + ".part")
        size = 0
        try:
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(65536):
                    if chunk:
                        fh.write(chunk)
                        size += len(chunk)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            return False, str(exc)
        if expected_size and size != expected_size:
            tmp.unlink(missing_ok=True)
            return False, f"size mismatch (got {size}, expected {expected_size})"
        tmp.replace(dest)
        return True, ""
