# ChanRipper

A 4chan thread downloader and monitor for Windows, with a real GUI.

Paste a thread URL, hit **Download Thread**, and it pulls every media file in the
thread. Tick **Monitor** and it keeps checking in the background, grabbing only
what appeared since the last pass. Tick **Comments** and it also saves the posts.

## What it does

- **Download a thread** — every image, video and audio file in the thread.
- **Monitor threads** — each monitored thread has its own check interval in
  seconds (default 10s, floor 2s). Only new media is downloaded on each pass.
- **Save comments** — per thread, opt-in. Writes both `thread.json` (lossless
  raw 4chan API data) and `thread.txt` (readable, replies indented under the
  post they answer, greentext and `>>` references preserved).
- **Always-visible thread list** — board, subject, post/media counts, size, last
  check, next check, editable delay, monitor/comments toggles, status.
- **Persistent** — the list and every thread's state survive a restart, and
  monitoring resumes automatically on launch.
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
%USERPROFILE%\Downloads\4chan\4chan\news\1543881 - air-force-commando-dies-after-falling-from\
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

## Running it

### The .exe (no Python needed)
Grab `ChanRipper.exe` from the GitHub Actions build artifact or the releases
page. It is a single self-contained file.

### From source
```bash
python -m venv .venv
.venv\Scripts\activate
pip install requests "PySide6>=6.6"
python main.py
```

### Command line (the engine, no GUI)
```bash
python cli.py download "https://boards.4chan.org/news/thread/1543881/" --comments
python cli.py download news/1543881 --monitor --interval 10
python cli.py watch --interval 10
python cli.py list
```

### Building the exe yourself
```bash
pip install pyinstaller
pyinstaller --clean --noconfirm chanripper.spec
```

## Where state lives

| File | Contents |
| --- | --- |
| `%APPDATA%\ChanRipper\config.json` | Your settings |
| `%APPDATA%\ChanRipper\state.sqlite` | Thread list, per-thread state, media ledger |

## Layout

```
core/        headless engine — no GUI imports, fully unit-tested
  api.py         board adapters, URL parsing, rate limiters, HTTP client
  parser.py      4chan comment HTML -> plain text
  naming.py      Windows-safe names and collision rules
  store.py       SQLite ledger
  writer.py      thread.json / thread.txt
  engine.py      download + incremental sync
  scheduler.py   monitor loop
  config.py      settings
gui/         PySide6 window and settings dialog
cli.py       headless entry point
tests/       unit tests (no network) + a headless GUI smoke test
```

The board layer is a pluggable `BoardAdapter`; only the 4chan adapter exists
today, so another imageboard can be added without touching the engine.

## Tests

```bash
python -m unittest discover -s tests -v            # 32 unit tests, no network
QT_QPA_PLATFORM=offscreen python tests/gui_smoke.py  # builds the window, does a real download
```
