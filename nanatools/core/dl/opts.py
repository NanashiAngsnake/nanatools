from __future__ import annotations

import os
from typing import Any, Optional

from .models import ARCHIVE_NAME, NAMING, DownloadConfig


def build_ydl_opts(
    cfg: DownloadConfig,
    *,
    ffmpeg_dir: Optional[str] = None,
    logger: Any = None,
) -> dict:
    dest = str(cfg.dest)
    template = NAMING.get(cfg.naming, NAMING["Simple title"])

    opts: dict[str, Any] = {
        "outtmpl": os.path.join(dest, template),
        "noplaylist": not cfg.playlist,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "noprogress": True,
        "continuedl": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 5,
        "windowsfilenames": True,
        "overwrites": False,
    }

    if logger is not None:
        opts["logger"] = logger

    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir

    if cfg.use_archive:
        opts["download_archive"] = os.path.join(dest, ARCHIVE_NAME)

    if cfg.cookies_browser and cfg.cookies_browser != "None":
        opts["cookiesfrombrowser"] = (cfg.cookies_browser,)

    if cfg.rate_limit:
        opts["ratelimit"] = cfg.rate_limit

    if cfg.audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
        return opts

    if cfg.quality == "Best":
        opts["format"] = "bestvideo+bestaudio/best"
    else:
        height = cfg.quality.replace("p", "")
        opts["format"] = (
            f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
        )
    opts["merge_output_format"] = "mp4"

    if cfg.subtitles:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = False
        opts["subtitleslangs"] = list(cfg.subtitle_langs)
        opts["postprocessors"] = [
            {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False}
        ]

    return opts


__all__ = ["build_ydl_opts"]
