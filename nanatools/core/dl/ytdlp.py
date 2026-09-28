from __future__ import annotations

import datetime as _dt
import re
import site
import sys
from typing import Optional

_DATE_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})")


def ytdlp_version() -> Optional[str]:
    try:
        from yt_dlp.version import __version__ as version
        return str(version)
    except Exception:
        pass
    try:
        from importlib.metadata import version as metadata_version
        return metadata_version("yt-dlp")
    except Exception:
        return None


def ytdlp_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("yt_dlp") is not None
    except Exception:
        return False


def ytdlp_release_date(version: Optional[str] = None) -> Optional[_dt.date]:
    text = version if version is not None else ytdlp_version()
    if not text:
        return None
    match = _DATE_RE.match(text.strip())
    if not match:
        return None
    try:
        return _dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def ytdlp_age_days(version: Optional[str] = None,
                   today: Optional[_dt.date] = None) -> Optional[int]:
    released = ytdlp_release_date(version)
    if released is None:
        return None
    return ((today or _dt.date.today()) - released).days


def ytdlp_update_command() -> list[str]:
    command = [sys.executable, "-m", "pip", "install", "-U"]
    if _installed_in_user_site():
        command.append("--user")
    command.append("yt-dlp")
    return command


def _installed_in_user_site() -> bool:
    try:
        import yt_dlp
        package_dir = str(getattr(yt_dlp, "__file__", "") or "")
        user_site = site.getusersitepackages()
    except Exception:
        return False
    if isinstance(user_site, (list, tuple)):
        return any(package_dir.lower().startswith(str(p).lower()) for p in user_site)
    return bool(user_site) and package_dir.lower().startswith(str(user_site).lower())


def version_note(stale_after_days: int = 60,
                 today: Optional[_dt.date] = None) -> str:
    version = ytdlp_version()
    if not version:
        return "yt-dlp is not installed."
    age = ytdlp_age_days(version, today)
    if age is None:
        return f"yt-dlp {version}"
    if age >= stale_after_days:
        return (f"yt-dlp {version} - {age} days old. "
                f"Sites change; consider {' '.join(ytdlp_update_command()[2:])}")
    return f"yt-dlp {version} - {age} days old"


__all__ = [
    "version_note", "ytdlp_age_days", "ytdlp_available", "ytdlp_release_date",
    "ytdlp_update_command", "ytdlp_version",
]
