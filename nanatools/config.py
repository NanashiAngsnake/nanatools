from __future__ import annotations

import os
from pathlib import Path

from .version import APP_NAME

ROOT_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"
ASSETS_DIR = ROOT_DIR / "assets"

SETTINGS_FILE = DATA_DIR / "settings.json"
LOG_FILE = LOG_DIR / "nanatools.log"

JOURNAL_DIR = DATA_DIR / "journals"

DOWNLOADS_DIR = Path(os.path.expandvars(r"%USERPROFILE%")) / "Downloads"
DESKTOP_DIR = Path(os.path.expandvars(r"%USERPROFILE%")) / "Desktop"


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, JOURNAL_DIR):
        d.mkdir(parents=True, exist_ok=True)


__all__ = [
    "APP_NAME", "ROOT_DIR", "DATA_DIR", "LOG_DIR", "ASSETS_DIR",
    "SETTINGS_FILE", "LOG_FILE", "JOURNAL_DIR",
    "DOWNLOADS_DIR", "DESKTOP_DIR", "ensure_dirs",
]
