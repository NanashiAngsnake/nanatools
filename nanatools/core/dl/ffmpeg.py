from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional


def find_ffmpeg_dir() -> Optional[str]:
    found = shutil.which("ffmpeg")
    if found:
        return str(Path(found).parent)

    local = os.environ.get("LOCALAPPDATA")
    if local:
        root = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if root.is_dir():
            for exe in root.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
                return str(exe.parent)
    return None


def ffmpeg_available() -> bool:
    return find_ffmpeg_dir() is not None


FFMPEG_HINT = (
    "ffmpeg was not found. Merging video with audio, extracting MP3 and "
    "embedding subtitles all need it. Install it with:  winget install Gyan.FFmpeg"
)


__all__ = ["find_ffmpeg_dir", "ffmpeg_available", "FFMPEG_HINT"]
