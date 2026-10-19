"""Background monitor: watches every flagged thread on its own interval.

All network work goes through the shared Client, so the global 1 req/s pacer
applies no matter how many threads are being watched.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .engine import Engine, SyncResult


class MonitorEngine:
    def __init__(self, engine: Engine, on_event=None, max_workers: int = 3):
        self.engine = engine
        self.on_event = on_event or (lambda r: None)
        self.pool = ThreadPoolExecutor(max_workers=max_workers,
                                       thread_name_prefix="monitor")
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._next: dict[tuple[str, int], float] = {}
        self._inflight: set[tuple[str, int]] = set()
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5 if wait else 0.1)
        self.pool.shutdown(wait=wait, cancel_futures=True)

    def poke(self) -> None:
        """Force an immediate re-scan (e.g. after adding a thread)."""
        self._wake.set()

    def next_due(self, board: str, no: int) -> float | None:
        """Monotonic timestamp of the next scheduled pass, or None."""
        with self._lock:
            return self._next.get((board, no))

    def seconds_until(self, board: str, no: int) -> float | None:
        due = self.next_due(board, no)
        return None if due is None else max(0.0, due - time.monotonic())

    def inflight(self, board: str, no: int) -> bool:
        with self._lock:
            return (board, no) in self._inflight

    # -- scheduling ----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            for rec in self.engine.store.list_threads():
                if not rec.get("monitor") or rec.get("status") == "dead":
                    continue
                key = (rec["board"], rec["no"])
                with self._lock:
                    due = self._next.get(key)
                    if due is None:
                        self._next[key] = now + float(rec.get("delay") or 10.0)
                        continue
                    if due > now or key in self._inflight:
                        continue
                    self._inflight.add(key)
                self.pool.submit(self._run_one, rec)
            self._wake.wait(timeout=0.5)
            self._wake.clear()

    def _run_one(self, rec: dict) -> None:
        key = (rec["board"], rec["no"])
        try:
            result = self.engine.sync(rec["board"], rec["no"])
        except Exception as exc:                      # keep the loop alive
            result = SyncResult(rec["board"], rec["no"], "error", error=str(exc))
        finally:
            delay = float(rec.get("delay") or 10.0)
            with self._lock:
                self._next[key] = time.monotonic() + delay
                self._inflight.discard(key)
        try:
            self.on_event(result)
        except Exception:
            pass
