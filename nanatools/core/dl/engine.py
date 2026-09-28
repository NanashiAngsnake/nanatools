from __future__ import annotations

import gc
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .errors import clean, humanize_error
from .ffmpeg import find_ffmpeg_dir
from .models import (
    ACTIVE,
    DownloadConfig,
    ErrorInfo,
    ErrorKind,
    JobState,
    QueueSummary,
    Status,
    summarize,
)
from .opts import build_ydl_opts

log = logging.getLogger(__name__)

_PROGRESS_INTERVAL = 0.12


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Event:
    kind: str
    job: Optional[JobState] = None
    text: str = ""
    level: str = "info"
    summary: Optional[QueueSummary] = None


EventCallback = Callable[[Event], None]


class _JobLogger:
    __slots__ = ("_emit", "_job")

    def __init__(self, emit: Callable[[str, str], None], job: JobState) -> None:
        self._emit = emit
        self._job = job

    def debug(self, message: str) -> None:
        text = clean(message)
        self._emit(text, "debug" if text.startswith("[debug] ") else "info")

    def info(self, message: str) -> None:
        self._emit(clean(message), "info")

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._emit(clean(message), "warning")

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._emit(clean(message), "error")


class DownloadEngine:
    def __init__(
        self,
        on_event: EventCallback,
        *,
        ydl_factory: Optional[Callable[[dict], Any]] = None,
        ffmpeg_dir: Optional[str] = None,
    ) -> None:
        self._on_event = on_event
        self._ydl_factory = ydl_factory
        self._ffmpeg_dir = ffmpeg_dir
        self._jobs: list[JobState] = []
        self._lock = threading.RLock()
        self._cancelled_ids: set[str] = set()
        self._running = False
        self._idle = threading.Event()
        self._idle.set()
        self._token: Any = None
        self._last_emit: dict[str, float] = {}
        self._part_snapshot: dict[str, dict[str, int]] = {}


    def add(self, url: str) -> Optional[JobState]:
        url = url.strip()
        if not url:
            return None
        with self._lock:
            for job in self._jobs:
                if job.url == url and job.status in ACTIVE:
                    return None
            job = JobState(url=url)
            self._jobs.append(job)
        self._emit(Event("job_added", job=job))
        return job

    def add_many(self, urls: Iterable[str]) -> list[JobState]:
        return [job for job in (self.add(url) for url in urls) if job is not None]

    def jobs(self) -> list[JobState]:
        with self._lock:
            return list(self._jobs)

    def pending(self) -> list[JobState]:
        with self._lock:
            return [job for job in self._jobs if job.status is Status.PENDING]

    def remove(self, job_id: str) -> bool:
        with self._lock:
            for index, job in enumerate(self._jobs):
                if job.job_id != job_id:
                    continue
                if job.status in (Status.RUNNING, Status.PROCESSING, Status.CANCELLING):
                    return False
                del self._jobs[index]
                return True
        return False

    def clear(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._jobs.clear()
            self._cancelled_ids.clear()
        return True

    def clear_finished(self) -> int:
        with self._lock:
            keep = [job for job in self._jobs if not job.is_terminal]
            removed = len(self._jobs) - len(keep)
            self._jobs = keep
        return removed

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running


    def cancel_job(self, job_id: str) -> None:
        with self._lock:
            self._cancelled_ids.add(job_id)
            job = next((j for j in self._jobs if j.job_id == job_id), None)
        if job is None:
            return
        if job.status is Status.PENDING:
            self._set_status(job, Status.CANCELLED)
        elif job.status is Status.PROCESSING:
            self._set_status(job, Status.CANCELLING)

    def _is_cancelled(self, job: JobState) -> bool:
        token = self._token
        if token is not None and getattr(token, "cancelled", False):
            return True
        with self._lock:
            return job.job_id in self._cancelled_ids

    def wait_idle(self, timeout: float = 5.0) -> bool:
        return self._idle.wait(timeout)


    def run(self, cfg: DownloadConfig, token: Any = None) -> QueueSummary:
        with self._lock:
            if self._running:
                raise RuntimeError("this engine is already running a queue")
            self._running = True
            self._token = token
            queue = [job for job in self._jobs if job.status is Status.PENDING]
        self._idle.clear()

        try:
            os.makedirs(str(cfg.dest), exist_ok=True)
            ffmpeg_dir = self._ffmpeg_dir if self._ffmpeg_dir is not None else find_ffmpeg_dir()
            base_opts = build_ydl_opts(cfg, ffmpeg_dir=ffmpeg_dir)

            cookie_problem = self.check_cookies(cfg)
            if cookie_problem is not None:
                for job in queue:
                    job.error = cookie_problem
                    self._set_status(job, Status.FAILED)
                self._emit(Event("log", text=cookie_problem.message, level="error"))
                return summarize(self.jobs())

            for job in queue:
                if self._is_cancelled(job):
                    self._set_status(job, Status.CANCELLED)
                    continue
                self._set_status(job, Status.RUNNING)
                try:
                    self._download_one(job, base_opts, cfg)
                except Cancelled:
                    self._finish_cancelled(job)
                except Exception as exc:
                    job.error = humanize_error(exc)
                    self._set_status(job, Status.FAILED)
                    self._emit(Event("log", job=job, level="error",
                                     text=f"{job.label}: {job.error.raw or job.error.message}"))
        finally:
            with self._lock:
                self._running = False
                self._token = None
            self._idle.set()

        result = summarize(self.jobs())
        self._emit(Event("finished", summary=result, text=result.as_text()))
        return result

    def probe(self, url: str, cfg: DownloadConfig) -> dict:
        opts = dict(build_ydl_opts(cfg, ffmpeg_dir=self._ffmpeg_dir))
        opts["quiet"] = True
        with self._make_ydl(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}

    def check_cookies(self, cfg: DownloadConfig) -> Optional[ErrorInfo]:
        browser = cfg.cookies_browser
        if not browser or browser == "None":
            return None
        try:
            from yt_dlp.cookies import extract_cookies_from_browser
        except Exception:
            return None
        try:
            extract_cookies_from_browser(browser)
        except Exception as exc:
            info = humanize_error(exc)
            return ErrorInfo(
                kind=ErrorKind.COOKIES,
                message=f"Could not read {browser}'s cookies. "
                        "Close the browser, or choose a different one.",
                raw=info.raw,
            )
        return None


    def _make_ydl(self, opts: dict) -> Any:
        if self._ydl_factory is not None:
            return self._ydl_factory(opts)
        from yt_dlp import YoutubeDL
        return YoutubeDL(opts)

    def _download_one(self, job: JobState, base_opts: dict, cfg: DownloadConfig) -> None:
        opts = dict(base_opts)
        opts["progress_hooks"] = [lambda payload: self._progress_hook(job, payload)]
        opts["postprocessor_hooks"] = [lambda payload: self._pp_hook(job, payload)]
        opts["logger"] = _JobLogger(
            lambda text, level: self._emit(Event("log", job=job, text=text, level=level)),
            job,
        )

        self._part_snapshot[job.job_id] = self._part_sizes(cfg.dest)

        try:
            with self._make_ydl(opts) as ydl:
                result = ydl.extract_info(job.url, download=True)
        finally:
            self._part_snapshot.pop(job.job_id, None)
            if self._is_cancelled(job):
                gc.collect()

        if self._is_cancelled(job):
            raise Cancelled()

        if result is None:
            raise RuntimeError("yt-dlp returned no information for this URL")

        if not job.saw_progress:
            self._set_status(job, Status.SKIPPED)
            return

        job.filepath = _final_path(result) or job.filepath
        job.frac = 1.0
        self._set_status(job, Status.DONE)
        self._emit(Event("log", job=job, text=f"Finished: {job.label}", level="info"))

    def _finish_cancelled(self, job: JobState) -> None:
        kept = job.status is Status.CANCELLING or job.stage
        self._set_status(job, Status.CANCELLED)
        if kept:
            self._emit(Event(
                "log", job=job, level="warning",
                text=f"Cancelled after processing had started: {job.label} "
                     "(the file was left finished rather than half-written).",
            ))
        else:
            self._emit(Event("log", job=job, level="warning",
                             text=f"Cancelled: {job.label}"))


    def _progress_hook(self, job: JobState, payload: dict) -> None:
        if self._is_cancelled(job):
            raise Cancelled()

        job.saw_progress = True
        info = payload.get("info_dict") or {}
        title = info.get("title")
        if title and not job.title:
            job.title = str(title)

        status = payload.get("status")
        if status == "downloading":
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
            downloaded = payload.get("downloaded_bytes") or 0
            frac = downloaded / total if total else 0.0
            index, count = payload.get("fragment_index"), payload.get("fragment_count")
            if not total and index and count:
                frac = index / count
            job.frac = min(max(frac, 0.0), 1.0)
            job.downloaded_bytes = downloaded
            job.total_bytes = total
            job.speed_bps = payload.get("speed") or None
            eta = payload.get("eta")
            job.eta_s = int(eta) if eta else None
            self._note_failed_resume(job, payload)
            self._emit_progress(job)
        elif status == "finished":
            job.frac = 1.0
            job.stage = "finishing"
            job.filepath = _as_path(payload.get("filename")) or job.filepath
            self._emit_progress(job, force=True)

    def _pp_hook(self, job: JobState, payload: dict) -> None:
        stage = str(payload.get("postprocessor") or "")
        if payload.get("status") == "started":
            job.stage = stage
            if self._is_cancelled(job):
                self._set_status(job, Status.CANCELLING)
            elif job.status is Status.RUNNING:
                self._set_status(job, Status.PROCESSING)
        elif payload.get("status") == "finished":
            if job.stage == stage:
                job.stage = ""
            self._emit_progress(job, force=True)


    def _set_status(self, job: JobState, status: Status) -> None:
        job.status = status
        self._emit(Event("status", job=job))

    def _emit_progress(self, job: JobState, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force:
            last = self._last_emit.get(job.job_id, 0.0)
            if now - last < _PROGRESS_INTERVAL:
                return
        self._last_emit[job.job_id] = now
        self._emit(Event("progress", job=job))

    def _emit(self, event: Event) -> None:
        try:
            self._on_event(event)
        except Exception:
            log.exception("download event listener raised")

    @staticmethod
    def _part_sizes(dest: Path) -> dict[str, int]:
        try:
            return {str(p): p.stat().st_size for p in Path(dest).glob("*.part")}
        except OSError:
            return {}

    def _note_failed_resume(self, job: JobState, payload: dict) -> None:
        sizes = self._part_snapshot.get(job.job_id)
        if not sizes:
            return
        tmp = payload.get("tmpfilename")
        previous = sizes.get(str(tmp)) if tmp else None
        self._part_snapshot[job.job_id] = {}
        if previous and previous > 1_000_000 and (payload.get("downloaded_bytes") or 0) < 65_536:
            self._emit(Event(
                "log", job=job, level="warning",
                text=f"{job.label}: the server does not support resuming, "
                     "so the download restarted from the beginning.",
            ))


def _as_path(value: Any) -> Optional[Path]:
    try:
        return Path(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _final_path(result: Any) -> Optional[Path]:
    if not isinstance(result, dict):
        return None
    requested = result.get("requested_downloads")
    if isinstance(requested, (list, tuple)) and requested:
        first = requested[0]
        if isinstance(first, dict):
            return _as_path(first.get("filepath") or first.get("filename"))
    return _as_path(result.get("filepath"))


__all__ = ["Cancelled", "DownloadEngine", "Event", "EventCallback"]
