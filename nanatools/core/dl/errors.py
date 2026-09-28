from __future__ import annotations

import re

from .models import ErrorInfo, ErrorKind

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_PREFIX_RE = re.compile(r"^\s*ERROR:\s*", re.IGNORECASE)
_TAG_RE = re.compile(r"^\[[^\]]+\]\s*")
_CAUSED_BY_RE = re.compile(r"\s*\(caused by .*\)\s*$", re.IGNORECASE | re.DOTALL)


def clean(text: object) -> str:
    return ANSI_RE.sub("", str(text))


_RULES: tuple[tuple[ErrorKind, tuple[str, ...], str], ...] = (
    (ErrorKind.DRM, ("drm", "protected by a digital rights"),
     "This content is DRM-protected and cannot be downloaded."),
    (ErrorKind.BAD_URL, ("is not a valid url", "unsupported url"),
     "That does not look like a URL yt-dlp can open."),
    (ErrorKind.COOKIES, ("dpapi", "could not copy", "cookie", "unsupported platform"),
     "The browser's cookies could not be read. Close the browser, or choose another one."),
    (ErrorKind.NOT_FOUND, ("http error 404", "http error 410", "not found",
                           "video unavailable", "removed by the uploader"),
     "The page or file is gone (404)."),
    (ErrorKind.FFMPEG_MISSING, ("ffmpeg not found", "ffmpeg is not installed",
                                "you have requested merging", "ffprobe"),
     "ffmpeg is needed for this and was not found."),
    (ErrorKind.DISK, ("no space left", "errno 28", "permission denied",
                      "winerror 32", "winerror 5", "access is denied"),
     "The file could not be written. Check the folder and free space."),
    (ErrorKind.EXTRACTOR_STALE, ("unable to extract", "signature extraction failed",
                                 "please report this issue", "requested format is not available"),
     "yt-dlp could not read this page's player."),
    (ErrorKind.NETWORK, ("failed to establish a new connection", "getaddrinfo",
                         "timed out", "temporary failure in name resolution",
                         "connection refused", "winerror 10061", "winerror 10054",
                         "unable to download webpage", "http error 4", "http error 5"),
     "The site could not be reached."),
)

_STALE_HINT = (
    "Sites change often and yt-dlp has to keep up. "
    "Updating it (pip install -U yt-dlp) fixes most of these."
)


def humanize_error(exc: BaseException) -> ErrorInfo:
    raw = clean(exc).strip()

    message = raw
    while _PREFIX_RE.match(message):
        message = _PREFIX_RE.sub("", message, count=1)
    message = _TAG_RE.sub("", message)
    message = _CAUSED_BY_RE.sub("", message).strip()
    message = message.splitlines()[0] if message else raw or exc.__class__.__name__

    lowered = message.lower()
    for kind, needles, wording in _RULES:
        if any(needle in lowered for needle in needles):
            hint = _STALE_HINT if kind is ErrorKind.EXTRACTOR_STALE else None
            return ErrorInfo(kind=kind, message=wording, raw=raw, hint=hint)

    return ErrorInfo(kind=ErrorKind.UNKNOWN, message=message or "Download failed.", raw=raw)


__all__ = ["ANSI_RE", "clean", "humanize_error"]
