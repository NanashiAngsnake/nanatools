from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from tkinter import filedialog
from typing import Any

import customtkinter as ctk

from ... import registry, theme
from ...core.dl import (
    BROWSERS,
    NAMING,
    QUALITIES,
    DownloadConfig,
    DownloadEngine,
    Event,
    JobState,
    QueueSummary,
    Status,
    ffmpeg_available,
    ytdlp_available,
)
from ...core.dl.ffmpeg import FFMPEG_HINT
from ...core.dl.models import default_dest, human_eta, human_size, human_speed
from ...core.dl.ytdlp import version_note
from ..base import ToolPanel

_STATUS_LOOK: dict[Status, tuple[str, str]] = {
    Status.PENDING: ("Waiting", theme.MUTED),
    Status.RUNNING: ("Downloading", theme.ACCENT),
    Status.PROCESSING: ("Processing", theme.ACCENT),
    Status.CANCELLING: ("Cancelling after processing", theme.WARNING),
    Status.DONE: ("Done", theme.SUCCESS),
    Status.SKIPPED: ("Already had it", theme.TEXT_DIM),
    Status.FAILED: ("Failed", theme.DANGER),
    Status.CANCELLED: ("Cancelled", theme.WARNING),
}

_LOG_COLOURS = {
    "debug": theme.MUTED,
    "info": theme.TEXT_DIM,
    "warning": theme.WARNING,
    "error": theme.DANGER,
}

_LOG_MAX_LINES = 600


class DownloaderPanel(ToolPanel):
    spec = registry.by_id("downloader")


    def build(self) -> None:
        self._engine = DownloadEngine(self._dispatch_event)
        self._rows: dict[str, dict[str, Any]] = {}
        self._ctx: Any = None
        self._handle: Any = None
        self._cancelling = False

        self.grid_columnconfigure(0, weight=1)

        if not ytdlp_available():
            self._build_unavailable()
            return

        self._build_header()
        self._build_urls()
        self._build_options()
        self._build_destination()
        self._build_actions()
        self._build_queue()
        self._build_log()
        self._build_footer()
        self._sync_audio_only()

    def _build_unavailable(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            self, text="Video downloader", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 6))
        ctk.CTkLabel(
            self, justify="left", anchor="nw", wraplength=620,
            text=("This tool needs yt-dlp, which is not installed.\n\n"
                  "Install it with:\n    pip install --user yt-dlp\n\n"
                  "ffmpeg is needed too, for merging video with audio:\n"
                  "    winget install Gyan.FFmpeg"),
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=13),
            text_color=theme.TEXT_DIM,
        ).grid(row=1, column=0, sticky="new", padx=24, pady=(0, 24))

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 2))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="Video downloader", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self._version_label = ctk.CTkLabel(
            header, text=version_note(), anchor="e",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            text_color=theme.MUTED,
        )
        self._version_label.grid(row=0, column=1, sticky="e")

    def _build_urls(self) -> None:
        self._url_box = ctk.CTkTextbox(
            self, height=84, font=ctk.CTkFont(family=theme.FONT_FAMILY, size=13),
        )
        self._url_box.grid(row=1, column=0, sticky="ew", padx=20, pady=(6, 2))
        ctk.CTkLabel(
            self, anchor="w", text=(
                "One URL per line - a video page, a playlist, or a direct "
                ".m3u8 / .mp4 link you are allowed to download."
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            text_color=theme.MUTED,
        ).grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 6))

    def _build_options(self) -> None:
        card = ctk.CTkFrame(self, fg_color=theme.CARD)
        card.grid(row=3, column=0, sticky="ew", padx=20, pady=4)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))

        self._quality = ctk.StringVar(value=self.settings.get("quality", "Best"))
        self._naming = ctk.StringVar(value=self.settings.get("naming", "Simple title"))
        self._cookies = ctk.StringVar(value=self.settings.get("cookies_browser") or "None")

        ctk.CTkLabel(top, text="Quality").pack(side="left", padx=(0, 6))
        self._quality_menu = ctk.CTkOptionMenu(
            top, variable=self._quality, values=list(QUALITIES), width=104)
        self._quality_menu.pack(side="left", padx=(0, 18))

        ctk.CTkLabel(top, text="Naming").pack(side="left", padx=(0, 6))
        ctk.CTkOptionMenu(
            top, variable=self._naming, values=list(NAMING), width=160,
        ).pack(side="left", padx=(0, 18))

        ctk.CTkLabel(top, text="Cookies from").pack(side="left", padx=(0, 6))
        ctk.CTkOptionMenu(
            top, variable=self._cookies, values=list(BROWSERS), width=104,
        ).pack(side="left")

        bottom = ctk.CTkFrame(card, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(4, 12))

        self._audio_only = ctk.BooleanVar(value=self.settings.get("audio_only", False))
        self._subtitles = ctk.BooleanVar(value=self.settings.get("subtitles", False))
        self._playlist = ctk.BooleanVar(value=self.settings.get("playlist", True))
        self._archive = ctk.BooleanVar(value=self.settings.get("use_archive", True))

        ctk.CTkCheckBox(
            bottom, text="Audio only (MP3)", variable=self._audio_only,
            command=self._sync_audio_only,
        ).pack(side="left", padx=(0, 18))
        self._subs_check = ctk.CTkCheckBox(
            bottom, text="Embed FR/EN subtitles", variable=self._subtitles)
        self._subs_check.pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(
            bottom, text="Whole playlist", variable=self._playlist,
        ).pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(
            bottom, text="Skip what I already have", variable=self._archive,
        ).pack(side="left")

    def _build_destination(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=4, column=0, sticky="ew", padx=20, pady=4)
        row.grid_columnconfigure(0, weight=1)

        self._dest = ctk.StringVar(
            value=self.settings.get("dest") or str(default_dest()))
        ctk.CTkEntry(row, textvariable=self._dest, height=34).grid(
            row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(row, text="Browse", width=88, height=34,
                      fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
                      command=self._choose_folder).grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(row, text="Open folder", width=104, height=34,
                      fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
                      command=self._open_folder).grid(row=0, column=2)

    def _build_actions(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=5, column=0, sticky="ew", padx=20, pady=(6, 4))
        row.grid_columnconfigure(0, weight=1)

        self._download_btn = ctk.CTkButton(
            row, text="Download", height=40,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=15, weight="bold"),
            command=self._start)
        self._download_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._cancel_btn = ctk.CTkButton(
            row, text="Cancel", width=104, height=40, state="disabled",
            fg_color=theme.DANGER, hover_color="#e05555", command=self._cancel)
        self._cancel_btn.grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            row, text="Clear queue", width=112, height=40,
            fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
            command=self._clear_queue).grid(row=0, column=2)

    def _build_queue(self) -> None:
        self.grid_rowconfigure(7, weight=1)
        ctk.CTkLabel(
            self, text="Queue", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=6, column=0, sticky="ew", padx=22, pady=(8, 0))
        self._queue_frame = ctk.CTkScrollableFrame(self, fg_color=theme.CARD, height=150)
        self._queue_frame.grid(row=7, column=0, sticky="nsew", padx=20, pady=(4, 4))
        self._queue_frame.grid_columnconfigure(0, weight=1)
        self._empty_label = ctk.CTkLabel(
            self._queue_frame, text="Nothing queued yet.", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
            text_color=theme.MUTED)
        self._empty_label.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

    def _build_log(self) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=8, column=0, sticky="ew", padx=22, pady=(4, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="Log", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self._verbose = ctk.BooleanVar(value=self.settings.get("verbose_log", False))
        ctk.CTkCheckBox(
            head, text="yt-dlp details", variable=self._verbose, checkbox_width=16,
            checkbox_height=16, font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
        ).grid(row=0, column=1, sticky="e")

        self._log_box = ctk.CTkTextbox(
            self, height=112, wrap="none",
            font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11))
        self._log_box.grid(row=9, column=0, sticky="ew", padx=20, pady=(4, 4))
        for level, colour in _LOG_COLOURS.items():
            self._log_box.tag_config(level, foreground=colour)
        self._log_box.configure(state="disabled")

    def _build_footer(self) -> None:
        self._footer = ctk.CTkLabel(
            self, anchor="w", wraplength=900, justify="left",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            text_color=theme.WARNING if not ffmpeg_available() else theme.MUTED,
            text="" if ffmpeg_available() else FFMPEG_HINT)
        self._footer.grid(row=10, column=0, sticky="ew", padx=22, pady=(0, 14))


    def _sync_audio_only(self) -> None:
        state = "disabled" if self._audio_only.get() else "normal"
        self._quality_menu.configure(state=state)
        self._subs_check.configure(state=state)

    def _choose_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self._dest.get() or str(default_dest()))
        if chosen:
            self._dest.set(chosen)
            self._save_settings()

    def _open_folder(self) -> None:
        self._reveal(Path(self._dest.get().strip() or str(default_dest())))

    def _reveal(self, target: Path) -> None:
        try:
            if target.is_file():
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", "/select,", str(target)])
                    return
                target = target.parent
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
        except Exception as exc:
            self._log(f"Could not open {target}: {exc}", "error")

    def _read_config(self) -> DownloadConfig:
        browser = self._cookies.get()
        return DownloadConfig(
            dest=Path(self._dest.get().strip() or str(default_dest())),
            quality=self._quality.get(),
            naming=self._naming.get(),
            cookies_browser=None if browser == "None" else browser,
            audio_only=self._audio_only.get(),
            subtitles=self._subtitles.get(),
            playlist=self._playlist.get(),
            use_archive=self._archive.get(),
        )

    def _save_settings(self) -> None:
        cfg = self._read_config()
        values = cfg.as_mapping()
        values["verbose_log"] = self._verbose.get()
        self.settings.update(values)


    def _start(self) -> None:
        if self._engine.is_running:
            return

        raw = self._url_box.get("1.0", "end")
        added = self._engine.add_many(line for line in raw.splitlines())
        if added:
            self._url_box.delete("1.0", "end")

        cfg = self._read_config()
        problems = cfg.validate()
        if problems:
            self.status(problems[0], tone="warning")
            self._log(problems[0], "warning")
            return
        if not self._engine.pending():
            self.status("Paste at least one URL.", tone="warning")
            return

        self._save_settings()
        self._cancelling = False
        self._download_btn.configure(state="disabled", text="Downloading...")
        self._cancel_btn.configure(state="normal", text="Cancel")
        pending = len(self._engine.pending())
        self.status(f"{pending} item(s) queued.", tone="info")

        engine = self._engine

        def work(ctx: Any) -> QueueSummary:
            self._ctx = ctx
            try:
                return engine.run(cfg, ctx.token)
            finally:
                self._ctx = None

        self._handle = self.run(
            work,
            name="downloader-queue",
            on_progress=self._on_progress,
            on_done=self._on_done,
            on_error=self._on_error,
            on_cancelled=self._on_cancelled,
        )

    def _cancel(self) -> None:
        if self._handle is None:
            return
        self._cancelling = True
        self._handle.cancel()
        self._cancel_btn.configure(state="disabled", text="Cancelling...")
        self.status("Cancelling. A file already being merged is left to finish.",
                    tone="warning")
        for job in self._engine.jobs():
            if job.status in (Status.RUNNING, Status.PROCESSING):
                self._refresh_row(job)

    def _clear_queue(self) -> None:
        if not self._engine.clear():
            self.status("A download is running - cancel it first.", tone="warning")
            return
        for widgets in self._rows.values():
            widgets["frame"].destroy()
        self._rows.clear()
        self._empty_label.grid()
        self.status("Queue cleared.")


    def _dispatch_event(self, event: Event) -> None:
        ctx = self._ctx
        if ctx is None:
            self._apply(event)
        else:
            ctx.progress(event=event)

    def _on_progress(self, payload: dict) -> None:
        event = payload.get("event")
        if event is not None:
            self._apply(event)

    def _apply(self, event: Event) -> None:
        if event.kind == "job_added" and event.job is not None:
            self._add_row(event.job)
        elif event.kind in ("status", "progress") and event.job is not None:
            self._refresh_row(event.job)
            if event.kind == "status" and event.job.status is Status.FAILED:
                error = event.job.error
                if error is not None:
                    self._log(f"{event.job.label}: {error.message}", "error")
                    if error.hint:
                        self._log(error.hint, "warning")
        elif event.kind == "log":
            self._log(event.text, event.level)
        elif event.kind == "finished" and event.summary is not None:
            self.status(event.summary.as_text(), tone=event.summary.tone)

    def _on_done(self, summary: QueueSummary) -> None:
        self._finish()
        self.status(summary.as_text(), tone=summary.tone)

    def _on_cancelled(self) -> None:
        self._finish()
        self.status("Cancelled. " + self._engine_summary_text(), tone="warning")

    def _on_error(self, exc: BaseException) -> None:
        self._finish()
        self._log(f"The queue stopped: {exc}", "error")
        self.status(f"The queue stopped: {exc}", tone="danger")

    def _engine_summary_text(self) -> str:
        from ...core.dl import summarize

        return summarize(self._engine.jobs()).as_text()

    def _finish(self) -> None:
        self._handle = None
        self._cancelling = False
        self._download_btn.configure(state="normal", text="Download")
        self._cancel_btn.configure(state="disabled", text="Cancel")
        for job in self._engine.jobs():
            self._refresh_row(job)


    def _add_row(self, job: JobState) -> None:
        self._empty_label.grid_remove()
        row = ctk.CTkFrame(self._queue_frame, fg_color=theme.CARD_ALT, corner_radius=6)
        row.grid(row=len(self._rows), column=0, sticky="ew", padx=2, pady=2)
        row.grid_columnconfigure(0, weight=1)

        name = ctk.CTkLabel(
            row, text=job.label, anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12))
        name.grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=(6, 2))

        status = ctk.CTkLabel(
            row, text=_STATUS_LOOK[job.status][0], anchor="e", width=180,
            text_color=_STATUS_LOOK[job.status][1],
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11))
        status.grid(row=0, column=1, sticky="e", padx=(0, 6), pady=(6, 2))

        action = ctk.CTkButton(
            row, text="x", width=26, height=24, corner_radius=4,
            fg_color="transparent", hover_color=theme.HOVER, text_color=theme.MUTED,
            command=lambda job_id=job.job_id: self._row_action(job_id))
        action.grid(row=0, column=2, rowspan=2, padx=(0, 8))

        bar = ctk.CTkProgressBar(row, height=6)
        bar.set(0)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(10, 6), pady=(0, 8))

        self._rows[job.job_id] = {
            "frame": row, "name": name, "status": status, "bar": bar, "action": action,
        }

    def _refresh_row(self, job: JobState) -> None:
        widgets = self._rows.get(job.job_id)
        if widgets is None:
            return
        try:
            text, colour = _STATUS_LOOK[job.status]
            if self._cancelling and job.status in (Status.RUNNING, Status.PROCESSING):
                text, colour = "Cancelling", theme.WARNING
            elif job.status is Status.RUNNING:
                text = f"{int(job.frac * 100)}%"
                details = [human_speed(job.speed_bps), human_eta(job.eta_s)]
                if not job.speed_bps and job.total_bytes:
                    details = [human_size(job.downloaded_bytes)]
                extra = "  ".join(part for part in details if part)
                text = f"{text}  {extra}".strip()
            elif job.status is Status.PROCESSING and job.stage:
                text = f"Processing ({job.stage})"
            elif job.status is Status.FAILED and job.error is not None:
                text = job.error.message

            widgets["name"].configure(text=job.label)
            widgets["status"].configure(text=text, text_color=colour)
            widgets["bar"].set(job.frac)

            if job.status is Status.DONE and job.filepath is not None:
                widgets["action"].configure(text="Open", width=52,
                                            text_color=theme.TEXT_DIM)
            elif job.is_terminal:
                widgets["action"].configure(text="x", width=26, text_color=theme.MUTED)
        except Exception:
            self._rows.pop(job.job_id, None)

    def _row_action(self, job_id: str) -> None:
        job = next((j for j in self._engine.jobs() if j.job_id == job_id), None)
        if job is None:
            return
        if job.status is Status.DONE and job.filepath is not None:
            self._reveal(job.filepath)
            return
        if job.is_terminal or job.status is Status.PENDING:
            if self._engine.remove(job_id):
                widgets = self._rows.pop(job_id, None)
                if widgets is not None:
                    widgets["frame"].destroy()
                if not self._rows:
                    self._empty_label.grid()
            return
        self._engine.cancel_job(job_id)
        self._refresh_row(job)


    def _log(self, text: str, level: str = "info") -> None:
        if not text:
            return
        if level == "debug" and not self._verbose.get():
            return
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text.rstrip() + "\n", level)
        lines = int(self._log_box.index("end-1c").split(".")[0])
        if lines > _LOG_MAX_LINES:
            self._log_box.delete("1.0", f"{lines - _LOG_MAX_LINES}.0")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")


    def on_hide(self) -> None:
        if hasattr(self, "_dest"):
            self._save_settings()

    def on_close(self) -> None:
        if not hasattr(self, "_engine"):
            return
        self._save_settings()
        if self._handle is not None:
            self._handle.cancel()
        if self._engine.is_running and not self._engine.wait_idle(5.0):
            self.log.warning("closed while post-processing was still running")


__all__ = ["DownloaderPanel"]
