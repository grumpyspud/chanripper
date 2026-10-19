"""Headless CLI — the engine's test harness and a usable tool in its own right."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from core import naming
from core.api import parse_thread_url
from core.config import (DELAY_DEFAULT, DELAY_FLOOR, DELAY_WARN_BELOW, Settings,
                         clamp_delay, config_dir)
from core.engine import Engine
from core.scheduler import MonitorEngine
from core.store import Store


def build(args) -> tuple[Engine, Store]:
    s = Settings.load()
    if args.root:
        s.root = args.root
    if args.board_dir is not None:
        s.board_dir = args.board_dir
    if args.folder_mode:
        s.folder_mode = args.folder_mode
    if args.name_mode:
        s.name_mode = args.name_mode
    s.normalize()
    db = Path(args.db) if args.db else config_dir() / "state.sqlite"
    store = Store(db)
    return Engine(s, store), store


def cmd_download(args) -> int:
    engine, store = build(args)
    comments = args.comments if args.comments is not None else engine.settings.default_comments
    res = engine.add(args.url, monitor=args.monitor, comments=comments,
                     delay=args.interval)
    if res.status == "error":
        print(f"FAILED: {res.error}", file=sys.stderr)
        return 1
    print(f"{res.status}: /{res.board}/{res.no}")
    print(f"  folder      : {res.folder}")
    print(f"  new posts   : {res.new_posts}")
    print(f"  new media   : {res.new_media}  ({res.bytes_downloaded/1048576:.2f} MB)")
    for m in res.messages:
        print(f"  note        : {m}")
    return 0


def cmd_watch(args) -> int:
    engine, store = build(args)
    if args.interval:
        d = clamp_delay(args.interval)
        for rec in store.list_threads():
            if rec["monitor"]:
                store.update_thread(rec["board"], rec["no"], delay=d)

    def on_event(res):
        stamp = time.strftime("%H:%M:%S")
        if res.status == "unchanged":
            print(f"[{stamp}] /{res.board}/{res.no}  no change")
        elif res.status == "ok":
            print(f"[{stamp}] /{res.board}/{res.no}  +{res.new_posts} posts, "
                  f"+{res.new_media} media")
        else:
            print(f"[{stamp}] /{res.board}/{res.no}  {res.status}: {res.error}")

    mon = MonitorEngine(engine, on_event=on_event,
                        max_workers=engine.settings.max_workers)
    print("Monitoring. Ctrl-C to stop.")
    mon.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        mon.stop(wait=True)
        store.close()
    return 0


def cmd_list(args) -> int:
    engine, store = build(args)
    rows = store.list_threads()
    if not rows:
        print("(no threads)")
        return 0
    for r in rows:
        print(f"/{r['board']}/{r['no']:<12} mon={'Y' if r['monitor'] else 'n'} "
              f"com={'Y' if r['comments'] else 'n'} {r['delay']:>5.0f}s "
              f"posts={r['post_count']:<4} media={r['media_count']:<4} "
              f"{r['status']:<9} {r['subject'][:40]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="chanripper", description="ChanRipper engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--root", help="download root folder")
        sp.add_argument("--db", help="state database path")
        sp.add_argument("--board-dir", dest="board_dir", action="store_true",
                        default=None)
        sp.add_argument("--no-board-dir", dest="board_dir", action="store_false")
        sp.add_argument("--folder-mode", choices=["number", "name", "both"])
        sp.add_argument("--name-mode", choices=["uploader", "number"])

    d = sub.add_parser("download", help="download a thread now")
    d.add_argument("url")
    d.add_argument("--monitor", action="store_true")
    d.add_argument("--comments", dest="comments", action="store_true", default=None)
    d.add_argument("--no-comments", dest="comments", action="store_false")
    d.add_argument("--interval", type=float, default=None)
    common(d)
    d.set_defaults(func=cmd_download)

    w = sub.add_parser("watch", help="run the monitor loop")
    w.add_argument("--interval", type=float, default=None)
    common(w)
    w.set_defaults(func=cmd_watch)

    ls = sub.add_parser("list", help="list registered threads")
    common(ls)
    ls.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    if getattr(args, "interval", None) is not None and args.interval < DELAY_WARN_BELOW:
        eff = clamp_delay(args.interval)
        print(f"note: interval below {DELAY_WARN_BELOW:.0f}s "
              f"(4chan's own refresh); using {eff:.0f}s floor", file=sys.stderr)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
