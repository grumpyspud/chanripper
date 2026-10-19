"""Windows-safe naming rules for thread folders and media files."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_STEM = 120


def sanitize(name: str | None, fallback: str = "unnamed") -> str:
    """Make a string safe as a Windows file/folder name component."""
    if not name:
        return fallback
    name = unicodedata.normalize("NFC", str(name))
    name = name.replace("\u200b", "")
    name = _ILLEGAL_RE.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name:
        return fallback
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name[:MAX_STEM]


def slug(text: str | None, fallback: str = "") -> str:
    if not text:
        return fallback
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:80] or fallback


def thread_label(no: int | str, subject: str = "", semantic_url: str = "",
                 mode: str = "both") -> str:
    """Build the thread folder name. mode: number | name | both."""
    if mode == "number":
        return str(no)
    name = sanitize(semantic_url, "") or slug(subject, "")
    if not name:
        return str(no)
    if mode == "name":
        return name
    return f"{no} - {name}"


def thread_folder(root: str | Path, site: str, board: str, no: int | str,
                  subject: str = "", semantic_url: str = "",
                  folder_mode: str = "both", board_dir: bool = True) -> Path:
    label = thread_label(no, subject, semantic_url, folder_mode)
    p = Path(root)
    if board_dir:
        p = p / site / board
    return p / label


def media_name(post: dict, name_mode: str = "uploader",
               used: set[str] | None = None, folder: Path | None = None) -> str | None:
    """Filename (no directory) to save a post's media under.

    uploader mode -> original uploader filename; on collision append the
    4chan numeric filename: 'file (1792348901932343).png'.
    """
    tim = post.get("tim")
    if tim is None:
        return None
    ext = post.get("ext") or ""
    if name_mode == "number":
        return f"{tim}{ext}"

    stem = sanitize(post.get("filename"), str(tim))
    base = f"{stem}{ext}"
    taken = used if used is not None else set()

    def exists(candidate: str) -> bool:
        if candidate.lower() in taken:
            return True
        if folder is not None:
            try:
                return (Path(folder) / candidate).exists()
            except OSError:
                return False
        return False

    if not exists(base):
        return base
    alt = f"{stem} ({tim}){ext}"
    if not exists(alt):
        return alt
    i = 2
    while exists(f"{stem} ({tim}) ({i}){ext}"):
        i += 1
    return f"{stem} ({tim}) ({i}){ext}"
