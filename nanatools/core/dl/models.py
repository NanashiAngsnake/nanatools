from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


QUALITIES: tuple[str, ...] = ("Best", "1080p", "720p", "480p", "360p")

BROWSERS: tuple[str, ...] = ("None", "brave", "chrome", "firefox", "edge")

NAMING: dict[str, str] = {
    "Simple title": "%(title).150B.%(ext)s",
    "Playlist / series": (
        "%(playlist_title,uploader|Videos)s/"
        "%(playlist_index,autonumber)03d - %(title).120B.%(ext)s"
    ),
}

ARCHIVE_NAME = ".downloaded.txt"

SUBTITLE_LANGS: tuple[str, ...] = ("fr", "en", "fr-FR", "en-US")


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PROCESSING = "processing"
    CANCELLING = "cancelling"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL: frozenset[Status] = frozenset(
    {Status.DONE, Status.SKIPPED, Status.FAILED, Status.CANCELLED}
)

ACTIVE: frozenset[Status] = frozenset(
    {Status.PENDING, Status.RUNNING, Status.PROCESSING, Status.CANCELLING}
)


class ErrorKind(str, Enum):
    NETWORK = "network"
    NOT_FOUND = "not_found"
    BAD_URL = "bad_url"
    DRM = "drm"
    COOKIES = "cookies"
    FFMPEG_MISSING = "ffmpeg_missing"
    EXTRACTOR_STALE = "extractor_stale"
    DISK = "disk"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorInfo:
    kind: ErrorKind
    message: str
    raw: str = ""
    hint: Optional[str] = None


@dataclass(frozen=True)
class DownloadConfig:
    dest: Path
    quality: str = "Best"
    naming: str = "Simple title"
    cookies_browser: Optional[str] = None
    audio_only: bool = False
    subtitles: bool = False
    subtitle_langs: Sequence[str] = SUBTITLE_LANGS
    playlist: bool = True
    use_archive: bool = True
    rate_limit: Optional[int] = None

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not str(self.dest).strip():
            problems.append("Choose a destination folder.")
        elif self.dest.exists() and not self.dest.is_dir():
            problems.append(f"{self.dest} exists but is not a folder.")
        if self.quality not in QUALITIES:
            problems.append(f"Unknown quality: {self.quality}.")
        if self.naming not in NAMING:
            problems.append(f"Unknown naming template: {self.naming}.")
        if self.cookies_browser is not None and self.cookies_browser not in BROWSERS:
            problems.append(f"Unknown browser: {self.cookies_browser}.")
        if self.rate_limit is not None and self.rate_limit <= 0:
            problems.append("The speed limit must be greater than zero.")
        return problems


    def as_mapping(self) -> dict[str, Any]:
        return {
            "dest": str(self.dest),
            "quality": self.quality,
            "naming": self.naming,
            "cookies_browser": self.cookies_browser,
            "audio_only": self.audio_only,
            "subtitles": self.subtitles,
            "playlist": self.playlist,
            "use_archive": self.use_archive,
            "rate_limit": self.rate_limit,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, dest: Optional[Path] = None) -> "DownloadConfig":
        raw_dest = dest if dest is not None else Path(str(data.get("dest", "")))
        browser = data.get("cookies_browser")
        if browser in (None, "", "None"):
            browser = None
        return cls(
            dest=Path(raw_dest),
            quality=str(data.get("quality", "Best")),
            naming=str(data.get("naming", "Simple title")),
            cookies_browser=browser,
            audio_only=bool(data.get("audio_only", False)),
            subtitles=bool(data.get("subtitles", False)),
            playlist=bool(data.get("playlist", True)),
            use_archive=bool(data.get("use_archive", True)),
            rate_limit=data.get("rate_limit") or None,
        )


@dataclass
class JobState:
    url: str
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: Status = Status.PENDING
    title: str = ""
    frac: float = 0.0
    speed_bps: Optional[float] = None
    eta_s: Optional[int] = None
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    filepath: Optional[Path] = None
    stage: str = ""
    error: Optional[ErrorInfo] = None
    saw_progress: bool = False

    @property
    def label(self) -> str:
        return self.title or self.url

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def reset(self) -> None:
        self.status = Status.PENDING
        self.frac = 0.0
        self.speed_bps = None
        self.eta_s = None
        self.downloaded_bytes = 0
        self.total_bytes = None
        self.stage = ""
        self.error = None
        self.saw_progress = False


@dataclass(frozen=True)
class QueueSummary:
    done: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: int = 0

    @property
    def total(self) -> int:
        return self.done + self.skipped + self.failed + self.cancelled

    @property
    def tone(self) -> str:
        if self.failed:
            return "danger"
        if self.cancelled:
            return "warning"
        return "success"

    def as_text(self) -> str:
        if not self.total:
            return "Nothing to do."
        parts = [f"{self.done} finished"]
        if self.skipped:
            parts.append(f"{self.skipped} already had")
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.cancelled:
            parts.append(f"{self.cancelled} cancelled")
        return " - ".join(parts)


def summarize(jobs: Sequence[JobState]) -> QueueSummary:
    counts = {status: 0 for status in TERMINAL}
    for job in jobs:
        if job.status in counts:
            counts[job.status] += 1
    return QueueSummary(
        done=counts[Status.DONE],
        skipped=counts[Status.SKIPPED],
        failed=counts[Status.FAILED],
        cancelled=counts[Status.CANCELLED],
    )


def human_size(num_bytes: Optional[float]) -> str:
    if not num_bytes:
        return ""
    step = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.0f} {unit}" if unit == "B" else f"{step:.1f} {unit}"
        step /= 1024
    return ""


def human_speed(bps: Optional[float]) -> str:
    return f"{human_size(bps)}/s" if bps else ""


def human_eta(seconds: Optional[float]) -> str:
    if not seconds or seconds < 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def default_dest() -> Path:
    downloads = Path(os.path.expanduser("~")) / "Downloads"
    return downloads if downloads.is_dir() else Path(os.path.expanduser("~"))


__all__ = [
    "ACTIVE", "ARCHIVE_NAME", "BROWSERS", "NAMING", "QUALITIES", "SUBTITLE_LANGS",
    "TERMINAL", "DownloadConfig", "ErrorInfo", "ErrorKind", "JobState",
    "QueueSummary", "Status", "default_dest", "human_eta", "human_size",
    "human_speed", "summarize",
]
