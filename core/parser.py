"""Turn 4chan comment HTML into readable plain text."""
from __future__ import annotations

import html
import re

_BR = re.compile(r"<br\s*/?>", re.I)
_WBR = re.compile(r"<wbr\s*/?>", re.I)
_QUOTELINK = re.compile(r'<a[^>]*class="quotelink"[^>]*>(.*?)</a>', re.I | re.S)
_DEADLINK = re.compile(r'<span[^>]*class="deadlink"[^>]*>(.*?)</span>', re.I | re.S)
_GREEN = re.compile(r'<span[^>]*class="quote"[^>]*>(.*?)</span>', re.I | re.S)
_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.I | re.S)
_STRIKE = re.compile(r"<s>(.*?)</s>", re.I | re.S)
_ANCHOR = re.compile(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_TOKEN = re.compile(r"\x00(\d+)\x00")
_BLANKS = re.compile(r"\n{3,}")


def comment_to_text(com: str | None) -> str:
    """4chan post HTML -> plain text. Keeps greentext, >>refs and link URLs."""
    if not com:
        return ""
    t = _BR.sub("\n", com)
    t = _WBR.sub("", t)
    t = _QUOTELINK.sub(r"\1", t)
    t = _DEADLINK.sub(r"\1", t)
    t = _GREEN.sub(r"\1", t)
    t = _STRIKE.sub(r"~~\1~~", t)
    t = _PRE.sub(
        lambda m: "\n```\n" + _TAG.sub("", m.group(1)).strip("\n") + "\n```\n", t
    )

    # Links are stashed before the tag stripper runs, otherwise the '<url>'
    # we add would be eaten by _TAG.
    links: list[tuple[str, str]] = []

    def stash(m: re.Match) -> str:
        links.append((m.group(2), m.group(1)))
        return f"\x00{len(links) - 1}\x00"

    t = _ANCHOR.sub(stash, t)
    t = _TAG.sub("", t)
    t = html.unescape(t)

    def restore(m: re.Match) -> str:
        text, href = links[int(m.group(1))]
        text = html.unescape(_TAG.sub("", text)).strip()
        href = html.unescape(href)
        if not href or href.startswith("#") or href.startswith("javascript:"):
            return text
        return f"{text} <{href}>"

    t = _TOKEN.sub(restore, t)
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return _BLANKS.sub("\n\n", t).strip()


def post_author(post: dict) -> str:
    """'Anonymous', 'Anonymous#trip', 'Anonymous ## Mod' ..."""
    bits = [post.get("name") or "Anonymous"]
    if post.get("trip"):
        bits.append(post["trip"])
    if post.get("capcode"):
        bits.append(f"## {post['capcode'].capitalize()}")
    if post.get("country"):
        bits.append(f"[{post['country']}]")
    if post.get("id") and post["id"] != "Heaven":
        bits.append(f"(ID: {post['id']})")
    return " ".join(bits)


def post_header(post: dict) -> str:
    when = post.get("now") or ""
    return f"{post_author(post)} {when} No.{post['no']}".strip()


def media_line(post: dict) -> str | None:
    if post.get("tim"):
        fname = f"{post.get('filename', '')}{post.get('ext', '')}"
        kb = (post.get("fsize") or 0) / 1024.0
        dims = f", {post['w']}x{post['h']}" if post.get("w") else ""
        spoiler = " [SPOILER]" if post.get("spoiler") else ""
        return (f"File: {fname} ({kb:,.1f} KB{dims}) "
                f"[4chan: {post['tim']}{post.get('ext', '')}]{spoiler}")
    if post.get("filedeleted"):
        return "File: [deleted]"
    return None
