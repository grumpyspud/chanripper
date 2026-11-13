"""ChanRipper web UI — FastAPI front end over the same headless engine.

Nothing in core/ is modified for this: the engine already ran headless, the
web layer just exposes it over HTTP and serves a single-page UI.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from core import __version__
from core.api import parse_thread_url
from core.config import DELAY_FLOOR, DELAY_WARN_BELOW, Settings, clamp_delay
from core.engine import Engine, SyncResult
from core.scheduler import MonitorEngine
from core.store import Store

STATIC_DIR = Path(__file__).parent / "static"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXT = {".webm", ".mp4", ".mov", ".mkv"}
AUDIO_EXT = {".mp3", ".ogg", ".m4a", ".wav"}


# --------------------------------------------------------------------------
class Broadcaster:
    """Fan out engine events to every connected SSE client.

    MonitorEngine calls its on_event from worker threads, so publishing hops
    back onto the event loop before touching the queues.
    """

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._queues.discard(q)

    def publish(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._fanout, payload)

    def _fanout(self, payload: dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._queues)
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass


class State:
    def __init__(self) -> None:
        self.settings = Settings.load()
        self.store = Store(self._db_path())
        self.engine = Engine(self.settings, self.store)
        self.broadcaster = Broadcaster()
        self.monitor: MonitorEngine | None = None
        self.started_at = time.time()

    def _db_path(self) -> Path:
        from core.config import config_dir
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / "state.sqlite"

    def ensure_monitor(self) -> MonitorEngine:
        if self.monitor is None:
            self.monitor = MonitorEngine(
                self.engine,
                on_event=lambda res: self.broadcaster.publish(event_payload(res)),
                max_workers=self.settings.max_workers)
        self.monitor.start()
        return self.monitor

    def stop(self) -> None:
        if self.monitor:
            self.monitor.stop(wait=False)
        self.store.close()


STATE = State()


# --------------------------------------------------------------------------
def event_payload(res: SyncResult) -> dict[str, Any]:
    rec = STATE.store.get_thread(res.board, res.no) or {}
    return {
        "type": "sync",
        "board": res.board,
        "no": res.no,
        "status": res.status,
        "new_posts": res.new_posts,
        "new_media": res.new_media,
        "bytes": res.bytes_downloaded,
        "error": res.error,
        "thread": rec,
        "at": time.time(),
    }


def thread_payload(rec: dict) -> dict:
    out = dict(rec)
    out["monitor"] = bool(rec.get("monitor"))
    out["comments"] = bool(rec.get("comments"))
    if STATE.monitor and rec.get("monitor") and rec.get("status") != "dead":
        secs = STATE.monitor.seconds_until(rec["board"], rec["no"])
        out["next_in"] = None if secs is None else round(secs, 1)
        out["checking"] = STATE.monitor.inflight(rec["board"], rec["no"])
    else:
        out["next_in"] = None
        out["checking"] = False
    return out


def safe_path(rel: str) -> Path:
    """Resolve a client path inside the download root, or refuse."""
    root = Path(STATE.settings.root).resolve()
    rel = (rel or "").strip().lstrip("/\\")
    target = (root / rel).resolve() if rel else root
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="path escapes the download root")
    return target


def _kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "file"


# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE.broadcaster.bind(asyncio.get_running_loop())
    Path(STATE.settings.root).mkdir(parents=True, exist_ok=True)
    if any(r.get("monitor") and r.get("status") != "dead"
           for r in STATE.store.list_threads()):
        STATE.ensure_monitor()
    yield
    STATE.stop()


app = FastAPI(title="ChanRipper", version=__version__, lifespan=lifespan)


# ---- models ---------------------------------------------------------------
class NewThread(BaseModel):
    url: str
    monitor: bool = False
    comments: bool | None = None
    delay: float | None = None


class ThreadPatch(BaseModel):
    monitor: bool | None = None
    comments: bool | None = None
    delay: float | None = None


class DeleteFiles(BaseModel):
    paths: list[str] = Field(default_factory=list)


class SettingsPatch(BaseModel):
    root: str | None = None
    board_dir: bool | None = None
    folder_mode: str | None = None
    name_mode: str | None = None
    default_delay: float | None = None
    default_comments: bool | None = None
    max_workers: int | None = None
    media_workers: int | None = None
    request_interval: float | None = None


# ---- meta -----------------------------------------------------------------
@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


@app.get("/api/state")
async def api_state() -> dict:
    threads = [thread_payload(r) for r in STATE.store.list_threads()]
    return {
        "version": __version__,
        "threads": threads,
        "settings": STATE.settings.to_dict(),
        "monitoring": STATE.monitor is not None,
        "delay_floor": DELAY_FLOOR,
        "delay_warn_below": DELAY_WARN_BELOW,
        "uptime": round(time.time() - STATE.started_at, 1),
    }


# ---- threads --------------------------------------------------------------
@app.post("/api/threads")
async def add_thread(body: NewThread) -> dict:
    parsed = parse_thread_url(body.url)
    if not parsed:
        raise HTTPException(status_code=400,
                            detail="Paste a 4chan thread URL, e.g. "
                                   "https://boards.4chan.org/news/thread/1543881/")
    board, no = parsed
    res = await run_in_threadpool(
        STATE.engine.add, body.url,
        monitor=body.monitor, comments=body.comments, delay=body.delay)
    if body.monitor:
        await run_in_threadpool(STATE.ensure_monitor)
        STATE.monitor.poke() if STATE.monitor else None
    payload = event_payload(res)
    STATE.broadcaster.publish(payload)
    if res.status == "error" and not res.folder:
        raise HTTPException(status_code=502, detail=res.error or "download failed")
    return payload


@app.patch("/api/threads/{board}/{no}")
async def patch_thread(board: str, no: int, body: ThreadPatch) -> dict:
    rec = STATE.store.get_thread(board, no)
    if not rec:
        raise HTTPException(status_code=404, detail="thread not tracked")
    fields: dict[str, Any] = {}
    if body.monitor is not None:
        fields["monitor"] = int(body.monitor)
    if body.comments is not None:
        fields["comments"] = int(body.comments)
    if body.delay is not None:
        fields["delay"] = clamp_delay(body.delay)
    if fields:
        await run_in_threadpool(STATE.store.update_thread, board, no, **fields)
    if fields.get("monitor"):
        await run_in_threadpool(STATE.ensure_monitor)
    if STATE.monitor:
        STATE.monitor.poke()
    rec = STATE.store.get_thread(board, no)
    if rec is None:
        raise HTTPException(status_code=404, detail="thread vanished")
    return thread_payload(rec)


@app.delete("/api/threads/{board}/{no}")
async def delete_thread(board: str, no: int) -> dict:
    if not STATE.store.get_thread(board, no):
        raise HTTPException(status_code=404, detail="thread not tracked")
    await run_in_threadpool(STATE.store.remove_thread, board, no)
    return {"removed": f"/{board}/{no}/"}


@app.post("/api/threads/{board}/{no}/check")
async def check_thread(board: str, no: int) -> dict:
    if not STATE.store.get_thread(board, no):
        raise HTTPException(status_code=404, detail="thread not tracked")
    res = await run_in_threadpool(STATE.engine.sync, board, no, force=True)
    payload = event_payload(res)
    STATE.broadcaster.publish(payload)
    return payload


@app.post("/api/monitor/start")
async def monitor_start() -> dict:
    for rec in STATE.store.list_threads():
        if rec.get("status") != "dead":
            STATE.store.update_thread(rec["board"], rec["no"], monitor=1)
    STATE.ensure_monitor()
    STATE.monitor.poke() if STATE.monitor else None
    return {"monitoring": True}


@app.post("/api/monitor/stop")
async def monitor_stop() -> dict:
    for rec in STATE.store.list_threads():
        STATE.store.update_thread(rec["board"], rec["no"], monitor=0)
    return {"monitoring": bool(STATE.monitor)}


# ---- settings -------------------------------------------------------------
@app.get("/api/settings")
async def get_settings() -> dict:
    return STATE.settings.to_dict()


@app.put("/api/settings")
async def put_settings(body: SettingsPatch) -> dict:
    s = STATE.settings
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(s, key, value)
    s.normalize()
    s.save()
    STATE.engine.apply_settings(s)
    Path(s.root).mkdir(parents=True, exist_ok=True)
    return s.to_dict()


# ---- live events ----------------------------------------------------------
@app.get("/api/events")
async def api_events(request: Request):
    q = STATE.broadcaster.subscribe()

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            STATE.broadcaster.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


# ---- file browser ---------------------------------------------------------
@app.get("/api/files")
async def api_files(path: str = Query("")) -> dict:
    target = safe_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="no such path")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")

    root = Path(STATE.settings.root).resolve()
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        try:
            stat = child.stat()
        except OSError:
            continue
        rel = str(child.relative_to(root)).replace(os.sep, "/")
        entries.append({
            "name": child.name,
            "path": rel,
            "dir": child.is_dir(),
            "size": stat.st_size if child.is_file() else 0,
            "mtime": stat.st_mtime,
            "kind": _kind(child.name) if child.is_file() else "dir",
        })

    parent = None
    if target != root:
        parent = str(target.parent.relative_to(root)).replace(os.sep, "/")
        if parent == ".":
            parent = ""
    return {"path": path.strip("/"), "parent": parent, "entries": entries}


@app.get("/api/files/raw")
async def api_file_raw(path: str = Query(...), download: bool = False):
    target = safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    return FileResponse(
        target, filename=target.name,
        content_disposition_type="attachment" if download else "inline")


@app.post("/api/files/delete")
async def delete_files(body: DeleteFiles) -> dict:
    """Delete marked files from disk.

    The media ledger is deliberately left alone: the monitor decides what to
    download from it, so a deleted file is never fetched again on the next
    check. The thread's media count therefore keeps describing the thread, not
    what happens to still be on disk.
    """
    if not body.paths:
        raise HTTPException(status_code=400, detail="no paths given")

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    freed = 0
    for rel in body.paths:
        try:
            target = safe_path(rel)
        except HTTPException as exc:
            failed.append({"path": rel, "error": str(exc.detail)})
            continue
        if not target.exists():
            failed.append({"path": rel, "error": "no such file"})
            continue
        if target.is_dir():
            failed.append({"path": rel, "error": "directories are not deletable"})
            continue
        try:
            size = target.stat().st_size
            target.unlink()
        except OSError as exc:
            failed.append({"path": rel, "error": str(exc)})
            continue
        deleted.append(rel)
        freed += size
    return {"deleted": deleted, "failed": failed, "bytes": freed}


@app.post("/api/files/zip")
async def zip_files(body: DeleteFiles, background: BackgroundTasks):
    """Zip the selected files (flat archive, one file per entry).

    All paths are re-validated against the download root and must be regular
    files; directories and missing paths are refused up front so the archive
    is never built from a half-valid list.
    """
    if not body.paths:
        raise HTTPException(status_code=400, detail="no paths given")

    targets: list[Path] = []
    for rel in body.paths:
        try:
            target = safe_path(rel)
        except HTTPException as exc:
            raise HTTPException(status_code=400, detail=exc.detail)
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"not a file: {rel}")
        targets.append(target)

    tmpdir = Path(os.environ.get("CHANRIPPER_TMP", "/tmp"))
    tmpdir.mkdir(parents=True, exist_ok=True)
    handle = tempfile_path(tmpdir, "files")

    def build() -> Path:
        seen: set[str] = set()
        with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            for target in targets:
                arcname = target.name
                while arcname in seen:            # two same-named files: dedupe
                    stem, dot, ext = target.name.rpartition(".")
                    arcname = f"{stem} ({len(seen)}){dot + ext if dot else ''}"
                seen.add(arcname)
                zf.write(target, arcname)
        return handle

    await run_in_threadpool(build)
    background.add_task(lambda: handle.unlink(missing_ok=True))
    return FileResponse(handle, filename="chanripper-files.zip",
                        media_type="application/zip")


@app.get("/api/threads/{board}/{no}/zip")
async def zip_thread(board: str, no: int, background: BackgroundTasks):
    rec = STATE.store.get_thread(board, no)
    if not rec:
        raise HTTPException(status_code=404, detail="thread not tracked")
    folder = Path(rec.get("folder") or "")
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="thread folder not found on disk")

    tmpdir = Path(os.environ.get("CHANRIPPER_TMP", "/tmp"))
    tmpdir.mkdir(parents=True, exist_ok=True)
    handle = tempfile_path(tmpdir, f"{board}-{no}")

    def build() -> Path:
        with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            for item in sorted(folder.rglob("*")):
                if item.is_file() and not item.name.endswith(".part"):
                    zf.write(item, item.relative_to(folder))
        return handle

    await run_in_threadpool(build)
    background.add_task(lambda: handle.unlink(missing_ok=True))
    return FileResponse(handle, filename=f"{folder.name}.zip",
                        media_type="application/zip")


def tempfile_path(tmpdir: Path, stem: str) -> Path:
    import uuid
    return tmpdir / f"chanripper-{stem}-{uuid.uuid4().hex[:8]}.zip"


# ---- static UI ------------------------------------------------------------
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
