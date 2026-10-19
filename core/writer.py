"""Write the lossless thread.json and the readable thread.txt (threaded tree)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .parser import comment_to_text, media_line, post_header

RULE = "=" * 78


def _tree(posts: list[dict]) -> dict[int, list[dict]]:
    known = {p.get("no") for p in posts}
    children: dict[int, list[dict]] = {}
    for p in posts:
        resto = p.get("resto") or 0
        if resto not in known:
            resto = 0            # orphan reply -> treat as top level
        children.setdefault(resto, []).append(p)
    for kids in children.values():
        kids.sort(key=lambda x: x.get("no") or 0)
    return children


def render_text(board: str, no: int, posts: list[dict], thread_url: str = "",
                fetched_at: float | None = None) -> str:
    children = _tree(posts)
    op = (children.get(0) or [{}])[0]
    media_count = sum(1 for p in posts if p.get("tim"))
    stamp = datetime.fromtimestamp(fetched_at).strftime("%Y-%m-%d %H:%M:%S") \
        if fetched_at else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    out: list[str] = [
        RULE,
        f" /{board}/ thread {no}",
        f" Subject: {op.get('sub', '') or '(none)'}",
        f" URL: {thread_url}",
        f" Posts: {len(posts)}   Media: {media_count}   Captured: {stamp}",
        RULE,
        "",
    ]

    def emit(post: dict, depth: int) -> None:
        ind = "    " * depth
        body = ind + "  "
        out.append(f"{ind}{post_header(post)}")
        ml = media_line(post)
        if ml:
            out.append(f"{body}{ml}")
        com = comment_to_text(post.get("com"))
        if com:
            for line in com.split("\n"):
                out.append(f"{body}{line}" if line else "")
        for child in children.get(post.get("no"), []):
            emit(child, depth + 1)

    for top in children.get(0, []):
        emit(top, 0)
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def write_thread_json(path: Path, posts: list[dict]) -> None:
    payload = {"posts": posts}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def write_thread_text(path: Path, board: str, no: int, posts: list[dict],
                      thread_url: str = "", fetched_at: float | None = None) -> None:
    text = render_text(board, no, posts, thread_url, fetched_at)
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(text)
