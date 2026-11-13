"""Settings model + on-disk config for ChanRipper."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "ChanRipper"
SITE_DIR = "4chan"

DELAY_FLOOR = 2.0          # hard floor in seconds
DELAY_DEFAULT = 10.0       # matches 4chan's own thread refresh interval
DELAY_WARN_BELOW = 10.0

FOLDER_MODES = ("number", "name", "both")
NAME_MODES = ("uploader", "number")
DATE_FORMATS = ("iso", "us", "euro", "relative")


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def default_root() -> str:
    override = os.environ.get("CHANRIPPER_DOWNLOAD_DIR")
    if override:
        return override
    return str(_home() / "Downloads" / SITE_DIR)


def config_dir() -> Path:
    # Containers (and portable installs) point this at a mounted volume so
    # settings and the ledger survive an image update.
    override = os.environ.get("CHANRIPPER_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def clamp_delay(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DELAY_DEFAULT
    return max(DELAY_FLOOR, v)


@dataclass
class Settings:
    root: str = field(default_factory=default_root)
    board_dir: bool = True
    folder_mode: str = "both"          # number | name | both
    name_mode: str = "uploader"        # uploader | number
    date_format: str = "iso"           # iso | us | euro | relative
    default_delay: float = DELAY_DEFAULT
    default_comments: bool = False
    wayback_fallback: bool = False
    max_workers: int = 3
    media_workers: int = 4             # parallel media downloads per thread
    request_interval: float = 1.0      # global pacing between any two requests

    def normalize(self) -> "Settings":
        if self.folder_mode not in FOLDER_MODES:
            self.folder_mode = "both"
        if self.name_mode not in NAME_MODES:
            self.name_mode = "uploader"
        if self.date_format not in DATE_FORMATS:
            self.date_format = "iso"
        self.default_delay = clamp_delay(self.default_delay)
        self.max_workers = max(1, min(8, int(self.max_workers or 3)))
        self.media_workers = max(1, min(16, int(self.media_workers or 4)))
        self.request_interval = max(0.5, float(self.request_interval or 1.0))
        if not self.root:
            self.root = default_root()
        return self

    @classmethod
    def load(cls) -> "Settings":
        p = config_path()
        data: dict[str, Any] = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known}).normalize()

    def save(self) -> None:
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
