from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class Namespace:
    __slots__ = ("_store", "_name")

    def __init__(self, store: "SettingsStore", name: str) -> None:
        self._store = store
        self._name = name

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(self._name, key, default)

    def set(self, key: str, value: Any) -> None:
        self._store.set(self._name, key, value)

    def update(self, values: dict) -> None:
        for key, value in values.items():
            self._store.set(self._name, key, value)

    def all(self) -> dict:
        return dict(self._store.section(self._name))


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._data: dict = {}
        self._dirty = False
        self.load()


    def load(self) -> None:
        with self._lock:
            try:
                raw = self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._data = {}
                return
            except OSError as exc:
                log.warning("settings unreadable (%s); starting from defaults", exc)
                self._data = {}
                return
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                backup = self._path.with_suffix(".corrupt.json")
                try:
                    self._path.replace(backup)
                    log.warning("settings corrupt (%s); moved to %s", exc, backup)
                except OSError:
                    log.warning("settings corrupt (%s) and could not be set aside", exc)
                self._data = {}
                return
            self._data = parsed if isinstance(parsed, dict) else {}

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            payload = json.dumps(self._data, indent=2, ensure_ascii=False)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self._path)
                self._dirty = False
            except OSError as exc:
                log.error("could not save settings: %s", exc)
                try:
                    tmp.unlink()
                except OSError:
                    pass


    def namespace(self, name: str) -> Namespace:
        return Namespace(self, name)

    def section(self, name: str) -> dict:
        with self._lock:
            section = self._data.get(name)
            return section if isinstance(section, dict) else {}

    def get(self, section: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self.section(section).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        with self._lock:
            bucket = self._data.setdefault(section, {})
            if not isinstance(bucket, dict):
                bucket = self._data[section] = {}
            if bucket.get(key, object()) != value:
                bucket[key] = value
                self._dirty = True


__all__ = ["SettingsStore", "Namespace"]
