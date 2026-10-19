# ChanRipper

A 4chan thread downloader and monitor — as a Windows desktop app and as a
Docker container with a web UI.

Paste a thread URL, hit **Download Thread**, and it pulls every media file in the
thread. Tick **Monitor** and it keeps checking in the background, grabbing only
what appeared since the last pass. Tick **Comments** and it also saves the posts.

Both front ends sit on the same headless engine, so they behave identically.

## What it does

- **Download a thread** — every image, video and audio file in the thread.
- **Monitor threads** — each monitored thread has its own check interval in
  seconds (default 10s, floor 2s). Only new media is downloaded on each pass.
- **Save comments** — per thread, opt-in. Writes both `thread.json` (lossless
  raw 4chan API data) and `thread.txt` (readable, replies indented under the
  post they answer, greentext and `>>` references preserved).
- **Always-visible thread list** — board, subject, post/media counts, size, last
  check, next check, editable delay, monitor/comments toggles, status.
- **File browser** (web UI) — browse what has been downloaded, with inline image
  thumbnails, plus a one-click zip of a whole archived thread.
- **Persistent** — the list and every thread's state survive a restart, and
  monitoring resumes automatically on startup.
- **Dead-thread detection** — a 404 marks the thread DEAD and stops monitoring it.

## How it keeps 4chan happy

| Behaviour | Detail |
| --- | --- |
| API pacing | One request per second against `a.4cdn.org`, globally, no matter how many threads are monitored (that is 4chan's documented client limit). |
| Conditional requests | Each thread remembers its `Last-Modified`. The next check sends `If-Modified-Since`, and an unchanged thread comes back **304 — zero bytes, no parsing**. |
| Media transfers | Images come from Cloudflare's `i.4cdn.org`, which has no 1 req/s rule, so media downloads use their own limiter and a parallel pool (4 at a time by default). A 222-file / 123 MB thread seeds in ~22s. |
| Backoff | `429` responses are honoured via `Retry-After`, with exponential backoff. |
| Dedupe | Every downloaded file is recorded by its 4chan `tim` ID in a SQLite ledger, so nothing is fetched twice — even across restarts. |

## Saving rules

Folder layout (root → site → board → thread):

```
Downloads\4chan\4chan\news\1543881 - air-force-commando-dies-after-falling-from\
    file.png                       <- uploader's filename
    file (1792348901932343).png    <- second upload also called "file.png"
    thread.json
    thread.txt
```

- **Thread folder name**: thread number, thread name, or both.
- **Board directory**: optional per-board folder.
- **Media filename**: the uploader's original name (`file.png`), or 4chan's
  numeric name (`1792348901932343.png`).
- **Collisions** (uploader mode): the second file gets the 4chan number
  appended — `file (1792348901932343).png`. A third gets `(… ) (2)`.
- All names are sanitized for Windows (illegal characters, reserved device
  names, trailing dots/spaces).
- A thread's folder is **sticky once created**: changing the naming settings
  affects new threads, and never relocates a thread that already has files.

## The desktop app

### The .exe (no Python needed)
Grab `ChanRipper.exe` from the releases page. It is a single self-contained file.

### From source
```bash
python -m venv .venv
.venv\Scripts\activate
pip install requests "PySide6>=6.6"
python main.py
```

### Building the exe yourself
```bash
pip install pyinstaller
pyinstaller --clean --noconfirm chanripper.spec
```

## The container (NAS / always-on host)

```bash
docker compose up -d
```

Then open `http://<host>:4848`.

`docker-compose.yml` mounts two volumes, and the split between them matters:

| Mount | Holds | Where it should live |
| --- | --- | --- |
| `/config` | `config.json` + `state.sqlite` — settings, thread list, dedupe ledger | **Local storage** (a NAS volume or a named Docker volume). SQLite over an SMB/CIFS mount corrupts under concurrent locking. |
| `/downloads` | the media itself | The share you actually want the files on. |

Updating is `docker compose pull && docker compose up -d` — both volumes stay
put, so settings, the thread list and the ledger all survive.

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `PUID` / `PGID` | `1000` | The uid/gid downloaded files are written as. **Set these to the owner of your share**, or files land as root and you cannot move or delete them from Windows. |
| `TZ` | `UTC` | Timestamps shown in the UI. |
| `CHANRIPPER_PORT` | `4848` | Port inside the container. |
| `CHANRIPPER_CONFIG_DIR` | `/config` | Where `config.json` and `state.sqlite` live. |
| `CHANRIPPER_DOWNLOAD_DIR` | `/downloads` | Default download root. |

The image is built for **linux/amd64 and linux/arm64**, so it runs on x86 NAS
boxes and ARM ones alike. There is no authentication — it assumes a trusted LAN,
so do not port-forward it to the internet.

### Building the image yourself
```bash
docker build -t chanripper .
docker run -d -p 4848:4848 -e PUID=1000 -e PGID=1000 \
  -v ./config:/config -v /your/share/4chan:/downloads chanripper
```

## Command line (the engine, no front end)

```bash
python cli.py download "https://boards.4chan.org/news/thread/1543881/" --comments
python cli.py download news/1543881 --monitor --interval 10
python cli.py watch --interval 10
python cli.py list
```

## Where state lives

| Context | Path |
| --- | --- |
| Desktop | `%APPDATA%\ChanRipper\config.json` and `state.sqlite` |
| Container | `/config/config.json` and `/config/state.sqlite` (the mounted volume) |

## Layout

```
core/        headless engine — no GUI or web imports, fully unit-tested
  api.py         board adapters, URL parsing, rate limiters, HTTP client
  parser.py      4chan comment HTML -> plain text
  naming.py      Windows-safe names and collision rules
  store.py       SQLite ledger
  writer.py      thread.json / thread.txt
  engine.py      download + incremental sync
  scheduler.py   monitor loop
  config.py      settings
gui/         PySide6 desktop window and settings dialog
web/         FastAPI + a single static page (no build step)
  app.py         REST API, SSE event stream, file browser, zip
  static/        index.html, app.js, style.css
cli.py       headless entry point
webmain.py   container entry point
tests/       unit tests (no network) + a headless GUI smoke test
```

The board layer is a pluggable `BoardAdapter`; only the 4chan adapter exists
today, so another imageboard can be added without touching the engine.

## Tests

```bash
python -m unittest discover -s tests -v              # 57 tests, no network
QT_QPA_PLATFORM=offscreen python tests/gui_smoke.py  # builds the window, does a real download
```
