from __future__ import annotations

import enum
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Optional, Sequence

from .. import config

A_HIDDEN = 0x2
A_SYSTEM = 0x4
A_REPARSE = 0x400
A_OFFLINE = 0x1000
A_RECALL_ON_DATA_ACCESS = 0x400000

DEFAULT_CATEGORIES: Mapping[str, tuple[str, ...]] = {
    "Installateurs": (".exe", ".msi", ".msix", ".apk", ".appx", ".appxbundle",
                      ".appinstaller", ".deb", ".dmg"),
    "Archives": (".zip", ".rar", ".7z", ".gz", ".tar", ".tgz", ".bz2", ".xz",
                 ".iso", ".jar"),
    "Videos": (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v",
               ".ts"),
    "Audio": (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus", ".wma"),
    "Images": (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".ico",
               ".heic", ".avif", ".tiff", ".psd", ".ai"),
    "Documents": (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                  ".odt", ".ods", ".txt", ".rtf", ".csv", ".epub"),
    "Code": (".js", ".ts", ".py", ".ps1", ".bat", ".cmd", ".sh", ".json", ".xml",
             ".yml", ".yaml", ".html", ".css", ".lua", ".sql", ".md"),
    "Polices": (".ttf", ".otf", ".woff", ".woff2"),
    "Raccourcis": (".lnk", ".url"),
}

FALLBACK_CATEGORY = "Autres"

IN_PROGRESS_EXTENSIONS = frozenset({
    ".crdownload", ".part", ".partial", ".download", ".tmp", ".temp", ".!ut",
    ".opdownload", ".aria2",
})

PROTECTED_NAMES = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})

JOURNAL_SUFFIX = ".jsonl"
UNDO_SUFFIX = ".undo.jsonl"

FSYNC_EVERY = 25


class Skip(enum.StrEnum):
    HIDDEN = "hidden file"
    SYSTEM = "system file"
    REPARSE = "junction or symlink"
    CLOUD = "cloud placeholder, not downloaded locally"
    IN_PROGRESS = "download in progress"
    TOO_RECENT = "newer than the minimum age"
    LOCKED = "in use by another program"
    PROTECTED = "protected name"
    APP_FILE = "belongs to NanaTools"
    EXCLUDED_TYPE = "excluded file type"
    UNREADABLE = "could not be read"
    NOT_A_FILE = "not a file"


class Fail(enum.StrEnum):
    LOCKED = "in use by another program"
    VANISHED = "no longer there"
    CHANGED = "changed since the preview"
    CROSS_VOLUME = "on a different drive"
    DEST_NOT_A_DIR = "the category name is taken by a file"
    ERROR = "failed"


class Block(enum.StrEnum):
    CATEGORY_ROOT = "category_root"
    DEST_NOT_A_DIR = "dest_not_a_dir"
    APP_TREE = "app_tree"
    REPARSE_ROOT = "reparse_root"


def ext_path(path: str) -> str:
    if os.name != "nt":
        return os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def plain_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def canon(path: str) -> str:
    return os.path.normcase(os.path.abspath(plain_path(path)))


def is_locked(path: str) -> bool:
    target = ext_path(path)
    try:
        os.rename(target, target)
    except PermissionError:
        return True
    except OSError:
        return False
    return False


def _attributes(st: os.stat_result) -> int:
    return getattr(st, "st_file_attributes", 0)


def _birth_ns(st: os.stat_result) -> int:
    birth = getattr(st, "st_birthtime_ns", None)
    if birth is not None:
        return int(birth)
    birth = getattr(st, "st_birthtime", None)
    if birth is not None:
        return int(birth * 1_000_000_000)
    return int(st.st_ctime * 1_000_000_000)


def _identity(path: str) -> dict:
    st = os.lstat(ext_path(path))
    return {
        "dev": st.st_dev,
        "ino": st.st_ino,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }


def _same_identity(recorded: Mapping, actual: Mapping) -> bool:
    if recorded.get("size") != actual.get("size"):
        return False
    if recorded.get("mtime_ns") != actual.get("mtime_ns"):
        return False
    for key in ("dev", "ino"):
        left, right = recorded.get(key), actual.get(key)
        if left and right and left != right:
            return False
    return True


@dataclass(frozen=True)
class Ruleset:
    categories: Mapping[str, Sequence[str]] = field(
        default_factory=lambda: dict(DEFAULT_CATEGORIES))
    fallback: str = FALLBACK_CATEGORY
    skip_extensions: frozenset[str] = frozenset()
    dated_subfolders: bool = False

    def category_for(self, name: str) -> str:
        ext = os.path.splitext(name)[1].lower()
        for category, extensions in self.categories.items():
            if ext in extensions:
                return category
        return self.fallback

    def folder_names(self) -> frozenset[str]:
        return frozenset(
            os.path.normcase(n) for n in (*self.categories, self.fallback))

    def dest_dir(self, root: str, category: str, mtime_ns: int) -> str:
        base = os.path.join(root, category)
        if not self.dated_subfolders:
            return base
        stamp = datetime.fromtimestamp(mtime_ns / 1e9).strftime("%Y-%m")
        return os.path.join(base, stamp)


DESKTOP_RULESET = Ruleset(skip_extensions=frozenset({".lnk", ".url"}))


@dataclass(frozen=True)
class Options:
    min_age_days: float = 0.0
    include_hidden: bool = False
    include_system: bool = False
    probe_locks: bool = True
    allow_category_root: bool = False
    ruleset: Ruleset = field(default_factory=Ruleset)

    def as_json(self) -> dict:
        return {
            "min_age_days": self.min_age_days,
            "include_hidden": self.include_hidden,
            "include_system": self.include_system,
            "dated_subfolders": self.ruleset.dated_subfolders,
            "fallback": self.ruleset.fallback,
        }


@dataclass(frozen=True)
class PlanItem:
    src: str
    name: str
    category: str
    dest_dir: str
    dest_name: str
    size: int
    mtime_ns: int
    age_days: float

    @property
    def dest(self) -> str:
        return os.path.join(self.dest_dir, self.dest_name)

    @property
    def key(self) -> str:
        return canon(self.src)

    @property
    def renamed(self) -> bool:
        return self.dest_name != self.name

    @property
    def long_path(self) -> bool:
        return len(self.dest) >= 250


@dataclass(frozen=True)
class SkippedItem:
    name: str
    src: str
    reason: Skip
    detail: str = ""


@dataclass(frozen=True)
class Plan:
    root: str
    created_at: float
    options: Options
    items: tuple[PlanItem, ...]
    skipped: tuple[SkippedItem, ...]
    subdirs: tuple[str, ...]
    blocking: tuple[tuple[Block, str], ...] = ()

    def by_category(self) -> dict[str, list[PlanItem]]:
        groups: dict[str, list[PlanItem]] = {}
        for item in self.items:
            groups.setdefault(item.category, []).append(item)
        return dict(sorted(groups.items(),
                           key=lambda kv: (-len(kv[1]), kv[0])))

    def totals(self, selection: Optional[frozenset[str]] = None) -> tuple[int, int]:
        chosen = [i for i in self.items
                  if selection is None or i.key in selection]
        return len(chosen), sum(i.size for i in chosen)

    @property
    def blocked(self) -> bool:
        return bool(self.blocking)


@dataclass(frozen=True)
class MoveResult:
    item: PlanItem
    ok: bool
    dest: Optional[str] = None
    reason: Optional[Fail] = None
    detail: str = ""


@dataclass(frozen=True)
class ApplyResult:
    run_id: str
    journal_path: str
    results: tuple[MoveResult, ...]
    created_dirs: tuple[str, ...]
    cancelled: bool = False

    @property
    def moved(self) -> tuple[MoveResult, ...]:
        return tuple(r for r in self.results if r.ok)

    @property
    def failed(self) -> tuple[MoveResult, ...]:
        return tuple(r for r in self.results if not r.ok)


@dataclass(frozen=True)
class JournalInfo:
    path: str
    run_id: str
    root: str
    started: float
    moved: int
    complete: bool
    undone: bool
    undone_at: Optional[float] = None

    @property
    def started_text(self) -> str:
        return datetime.fromtimestamp(self.started).strftime("%d/%m/%Y %H:%M")


@dataclass(frozen=True)
class RestoreResult:
    src: str
    dest: str
    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class UndoResult:
    journal_path: str
    restored: tuple[RestoreResult, ...]
    blocked: tuple[RestoreResult, ...]
    removed_dirs: tuple[str, ...]
    already_undone: bool = False

    @property
    def restored_count(self) -> int:
        return len(self.restored)


Progress = Callable[[int, int, str], None]
Cancel = Callable[[], bool]


def _free_name(name: str, index: int) -> str:
    if index <= 1:
        return name
    stem, ext = os.path.splitext(name)
    return f"{stem} ({index}){ext}"


def _reserve(dest_dir: str, name: str, taken: set[str]) -> str:
    index = 1
    while True:
        candidate = _free_name(name, index)
        key = canon(os.path.join(dest_dir, candidate))
        if key not in taken and not os.path.lexists(ext_path(os.path.join(dest_dir, candidate))):
            taken.add(key)
            return candidate
        index += 1


def plan(
    root: str,
    options: Optional[Options] = None,
    *,
    now: Optional[float] = None,
    self_paths: Iterable[str] = (),
    cancel: Optional[Cancel] = None,
    progress: Optional[Progress] = None,
) -> Plan:
    options = options or Options()
    now = time.time() if now is None else now
    rules = options.ruleset

    root_abs = os.path.abspath(root)
    try:
        root_st = os.lstat(ext_path(root_abs))
    except FileNotFoundError:
        raise FileNotFoundError(f"No such folder: {root_abs}") from None
    if not os.path.isdir(ext_path(root_abs)):
        raise NotADirectoryError(f"Not a folder: {root_abs}")

    blocking: list[tuple[Block, str]] = []
    if _attributes(root_st) & A_REPARSE:
        blocking.append((Block.REPARSE_ROOT,
                         "This folder is a junction or symlink."))
    if _inside(root_abs, str(config.ROOT_DIR)):
        blocking.append((Block.APP_TREE,
                         "This folder is part of the NanaTools application "
                         "itself, including the undo journals."))
    if (not options.allow_category_root
            and os.path.normcase(os.path.basename(root_abs)) in rules.folder_names()):
        blocking.append((
            Block.CATEGORY_ROOT,
            f"'{os.path.basename(root_abs)}' is itself a category folder -- "
            "organizing it would sort already-sorted files."))

    protected = set(PROTECTED_NAMES)
    own = {canon(p) for p in self_paths}
    own.add(canon(str(config.JOURNAL_DIR)))
    folder_names = rules.folder_names()

    with os.scandir(ext_path(root_abs)) as scan:
        entries = sorted(scan, key=lambda e: e.name.lower())
    total = len(entries)

    items: list[PlanItem] = []
    skipped: list[SkippedItem] = []
    subdirs: list[str] = []
    taken: set[str] = set()

    for index, entry in enumerate(entries, start=1):
        if cancel is not None and cancel():
            break
        if progress is not None:
            progress(index, total, entry.name)

        src = plain_path(entry.path)
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            skipped.append(SkippedItem(entry.name, src, Skip.UNREADABLE, str(exc)))
            continue

        attrs = _attributes(st)
        if attrs & A_REPARSE:
            skipped.append(SkippedItem(entry.name, src, Skip.REPARSE))
            continue
        if entry.is_dir(follow_symlinks=False):
            subdirs.append(entry.name)
            continue
        if not entry.is_file(follow_symlinks=False):
            skipped.append(SkippedItem(entry.name, src, Skip.NOT_A_FILE))
            continue

        if attrs & A_HIDDEN and not options.include_hidden:
            skipped.append(SkippedItem(entry.name, src, Skip.HIDDEN))
            continue
        if attrs & A_SYSTEM and not options.include_system:
            skipped.append(SkippedItem(entry.name, src, Skip.SYSTEM))
            continue
        if attrs & (A_OFFLINE | A_RECALL_ON_DATA_ACCESS):
            skipped.append(SkippedItem(entry.name, src, Skip.CLOUD))
            continue
        if os.path.normcase(entry.name) in protected:
            skipped.append(SkippedItem(entry.name, src, Skip.PROTECTED))
            continue
        if os.path.normcase(entry.name) in folder_names:
            skipped.append(SkippedItem(entry.name, src, Skip.PROTECTED,
                                       "shares a category folder's name"))
            continue
        if canon(src) in own:
            skipped.append(SkippedItem(entry.name, src, Skip.APP_FILE))
            continue

        ext = os.path.splitext(entry.name)[1].lower()
        if ext in IN_PROGRESS_EXTENSIONS:
            skipped.append(SkippedItem(entry.name, src, Skip.IN_PROGRESS))
            continue
        if ext in rules.skip_extensions:
            skipped.append(SkippedItem(entry.name, src, Skip.EXCLUDED_TYPE))
            continue

        age_ns = now * 1e9 - max(st.st_mtime_ns, _birth_ns(st))
        age_days = max(0.0, age_ns / 86_400e9)
        if age_days < options.min_age_days:
            skipped.append(SkippedItem(
                entry.name, src, Skip.TOO_RECENT, f"{age_days:.1f} days old"))
            continue

        if options.probe_locks and is_locked(src):
            skipped.append(SkippedItem(entry.name, src, Skip.LOCKED))
            continue

        category = rules.category_for(entry.name)
        dest_dir = rules.dest_dir(root_abs, category, st.st_mtime_ns)
        dest_name = _reserve(dest_dir, entry.name, taken)
        items.append(PlanItem(
            src=src, name=entry.name, category=category,
            dest_dir=dest_dir, dest_name=dest_name,
            size=st.st_size, mtime_ns=st.st_mtime_ns, age_days=age_days,
        ))

    for dest_dir in {i.dest_dir for i in items}:
        target = ext_path(dest_dir)
        if os.path.lexists(target) and not os.path.isdir(target):
            blocking.append((
                Block.DEST_NOT_A_DIR,
                f"'{os.path.basename(dest_dir)}' already exists as a file, "
                "so the category folder cannot be created."))

    return Plan(
        root=root_abs,
        created_at=now,
        options=options,
        items=tuple(items),
        skipped=tuple(skipped),
        subdirs=tuple(sorted(subdirs, key=str.lower)),
        blocking=tuple(blocking),
    )


def _inside(child: str, parent: str) -> bool:
    child_c, parent_c = canon(child), canon(parent)
    if child_c == parent_c:
        return True
    return child_c.startswith(parent_c.rstrip("\\/") + os.sep)


class _Journal:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(ext_path(os.path.dirname(path)), exist_ok=True)
        self._fh = open(ext_path(path), "a", encoding="utf-8", newline="\n")
        self._since_sync = 0

    def write(self, record: dict, *, sync: bool = False) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()
        self._since_sync += 1
        if sync or self._since_sync >= FSYNC_EVERY:
            os.fsync(self._fh.fileno())
            self._since_sync = 0

    def close(self) -> None:
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass
        self._fh.close()

    def __enter__(self) -> "_Journal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _move_no_clobber(src: str, dest_dir: str, name: str) -> str:
    index = 1
    while index < 10_000:
        candidate = os.path.join(dest_dir, _free_name(name, index))
        try:
            os.rename(ext_path(src), ext_path(candidate))
        except FileExistsError:
            index += 1
            continue
        return candidate
    raise FileExistsError(f"could not find a free name for {name} in {dest_dir}")


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def journal_path_for(run_id: str, journal_dir: Optional[str] = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = journal_dir or str(config.JOURNAL_DIR)
    return os.path.join(base, f"{stamp}_{run_id}{JOURNAL_SUFFIX}")


def apply(
    plan_obj: Plan,
    *,
    selection: Optional[frozenset[str]] = None,
    journal_dir: Optional[str] = None,
    cancel: Optional[Cancel] = None,
    progress: Optional[Progress] = None,
) -> ApplyResult:
    if plan_obj.blocked:
        raise ValueError("this plan has blocking errors and must not be applied")

    chosen = [i for i in plan_obj.items
              if selection is None or i.key in selection]
    total = len(chosen)
    run_id = _new_run_id()
    path = journal_path_for(run_id, journal_dir)

    results: list[MoveResult] = []
    created: list[str] = []
    cancelled = False

    with _Journal(path) as journal:
        journal.write({
            "t": "header", "v": 1, "tool": "nanatools-organizer",
            "run_id": run_id, "started": time.time(),
            "root": plan_obj.root, "planned": total,
            "options": plan_obj.options.as_json(),
        }, sync=True)

        for index, item in enumerate(chosen, start=1):
            if cancel is not None and cancel():
                cancelled = True
                break
            if progress is not None:
                progress(index, total, item.name)

            result = _move_one(item, plan_obj.root, journal, created)
            results.append(result)

        journal.write({
            "t": "footer", "finished": time.time(),
            "moved": sum(1 for r in results if r.ok),
            "failed": sum(1 for r in results if not r.ok),
            "status": "cancelled" if cancelled else "completed",
        }, sync=True)

    return ApplyResult(
        run_id=run_id, journal_path=path, results=tuple(results),
        created_dirs=tuple(created), cancelled=cancelled,
    )


def _move_one(item: PlanItem, root: str, journal: _Journal,
              created: list[str]) -> MoveResult:
    if not _inside(item.dest_dir, root):
        return MoveResult(item, False, reason=Fail.ERROR,
                          detail="destination outside the folder being organized")

    try:
        current = os.lstat(ext_path(item.src))
    except FileNotFoundError:
        return MoveResult(item, False, reason=Fail.VANISHED)
    except OSError as exc:
        return MoveResult(item, False, reason=Fail.ERROR, detail=str(exc))

    if (current.st_size, current.st_mtime_ns) != (item.size, item.mtime_ns):
        return MoveResult(item, False, reason=Fail.CHANGED)

    for parent in _missing_parents(item.dest_dir, root):
        try:
            os.mkdir(ext_path(parent))
        except FileExistsError:
            if not os.path.isdir(ext_path(parent)):
                return MoveResult(item, False, reason=Fail.DEST_NOT_A_DIR)
            continue
        except OSError as exc:
            return MoveResult(item, False, reason=Fail.ERROR, detail=str(exc))
        created.append(parent)
        journal.write({"t": "mkdir", "path": parent})

    try:
        dest_st = os.stat(ext_path(item.dest_dir))
    except OSError as exc:
        return MoveResult(item, False, reason=Fail.ERROR, detail=str(exc))
    if dest_st.st_dev and current.st_dev and dest_st.st_dev != current.st_dev:
        return MoveResult(item, False, reason=Fail.CROSS_VOLUME)

    try:
        dest = _move_no_clobber(item.src, item.dest_dir, item.name)
    except PermissionError as exc:
        journal.write({"t": "skip", "from": item.src,
                       "reason": "locked", "detail": str(exc)})
        return MoveResult(item, False, reason=Fail.LOCKED, detail=str(exc))
    except FileNotFoundError as exc:
        if os.path.lexists(ext_path(item.src)):
            journal.write({"t": "skip", "from": item.src,
                           "reason": "path", "detail": str(exc)})
            return MoveResult(item, False, reason=Fail.ERROR,
                              detail=f"path problem, not a missing file: {exc}")
        return MoveResult(item, False, reason=Fail.VANISHED)
    except OSError as exc:
        journal.write({"t": "skip", "from": item.src,
                       "reason": "error", "detail": str(exc)})
        return MoveResult(item, False, reason=Fail.ERROR, detail=str(exc))

    journal.write({
        "t": "move", "from": item.src, "to": dest,
        "id": _identity(dest),
    })
    return MoveResult(item, True, dest=dest)


def _missing_parents(dest_dir: str, root: str) -> list[str]:
    missing: list[str] = []
    current = dest_dir
    while not _inside(root, current) and _inside(current, root):
        if os.path.isdir(ext_path(current)):
            break
        missing.append(current)
        current = os.path.dirname(current)
    return list(reversed(missing))


def read_journal(path: str) -> tuple[list[dict], int]:
    records: list[dict] = []
    dropped = 0
    try:
        with open(ext_path(path), "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    dropped += 1
                    continue
                if isinstance(record, dict):
                    records.append(record)
                else:
                    dropped += 1
    except FileNotFoundError:
        return [], 0
    return records, dropped


def undo_sidecar_for(journal_path: str) -> str:
    if journal_path.endswith(JOURNAL_SUFFIX):
        return journal_path[: -len(JOURNAL_SUFFIX)] + UNDO_SUFFIX
    return journal_path + UNDO_SUFFIX


def _journal_info(path: str) -> Optional[JournalInfo]:
    records, _ = read_journal(path)
    if not records or records[0].get("t") != "header":
        return None
    header = records[0]
    footer = records[-1] if records[-1].get("t") == "footer" else None
    undone_at = None
    sidecar, _ = read_journal(undo_sidecar_for(path))
    for record in sidecar:
        if record.get("t") == "undo_footer":
            undone_at = record.get("finished")
    return JournalInfo(
        path=path,
        run_id=header.get("run_id", ""),
        root=header.get("root", ""),
        started=float(header.get("started", 0.0)),
        moved=sum(1 for r in records if r.get("t") == "move"),
        complete=footer is not None,
        undone=undone_at is not None,
        undone_at=undone_at,
    )


def list_journals(journal_dir: Optional[str] = None) -> list[JournalInfo]:
    base = journal_dir or str(config.JOURNAL_DIR)
    try:
        names = os.listdir(ext_path(base))
    except OSError:
        return []
    infos = []
    for name in names:
        if not name.endswith(JOURNAL_SUFFIX) or name.endswith(UNDO_SUFFIX):
            continue
        info = _journal_info(os.path.join(base, name))
        if info is not None:
            infos.append(info)
    return sorted(infos, key=lambda i: i.started, reverse=True)


def undo(
    journal_path: str,
    *,
    cancel: Optional[Cancel] = None,
    progress: Optional[Progress] = None,
) -> UndoResult:
    records, _ = read_journal(journal_path)
    sidecar_path = undo_sidecar_for(journal_path)
    previous, _ = read_journal(sidecar_path)
    if any(r.get("t") == "undo_footer" for r in previous):
        return UndoResult(journal_path, (), (), (), already_undone=True)

    moves = [r for r in records if r.get("t") == "move"]
    mkdirs = [r.get("path", "") for r in records if r.get("t") == "mkdir"]
    total = len(moves)

    restored: list[RestoreResult] = []
    blocked: list[RestoreResult] = []
    removed: list[str] = []

    with _Journal(sidecar_path) as sidecar:
        sidecar.write({"t": "undo_header", "started": time.time(),
                       "journal": journal_path}, sync=True)

        for index, record in enumerate(reversed(moves), start=1):
            if cancel is not None and cancel():
                break
            src, dest = record.get("to", ""), record.get("from", "")
            if progress is not None:
                progress(index, total, os.path.basename(src))

            outcome = _restore_one(src, dest, record.get("id") or {})
            (restored if outcome.ok else blocked).append(outcome)
            sidecar.write({"t": "restore" if outcome.ok else "blocked",
                           "from": src, "to": outcome.dest,
                           "reason": outcome.reason})

        for directory in sorted(set(mkdirs), key=lambda p: p.count(os.sep),
                                reverse=True):
            try:
                os.rmdir(ext_path(directory))
            except OSError:
                continue
            removed.append(directory)
            sidecar.write({"t": "rmdir", "path": directory})

        sidecar.write({
            "t": "undo_footer", "finished": time.time(),
            "restored": len(restored), "blocked": len(blocked),
        }, sync=True)

    return UndoResult(journal_path, tuple(restored), tuple(blocked),
                      tuple(removed))


def _restore_one(src: str, dest: str, recorded: Mapping) -> RestoreResult:
    try:
        actual = _identity(src)
    except FileNotFoundError:
        return RestoreResult(src, dest, False, "already gone")
    except OSError as exc:
        return RestoreResult(src, dest, False, str(exc))

    if recorded and not _same_identity(recorded, actual):
        return RestoreResult(src, dest, False, "changed since it was moved")

    parent = os.path.dirname(dest)
    try:
        os.makedirs(ext_path(parent), exist_ok=True)
    except OSError as exc:
        return RestoreResult(src, dest, False, str(exc))

    name = os.path.basename(dest)
    try:
        final = _move_no_clobber(src, parent, name)
    except PermissionError:
        return RestoreResult(src, dest, False, "in use by another program")
    except OSError as exc:
        return RestoreResult(src, dest, False, str(exc))
    return RestoreResult(src, final, True)


__all__ = [
    "DEFAULT_CATEGORIES", "FALLBACK_CATEGORY", "DESKTOP_RULESET",
    "IN_PROGRESS_EXTENSIONS", "PROTECTED_NAMES",
    "Skip", "Fail", "Block", "Ruleset", "Options",
    "PlanItem", "SkippedItem", "Plan", "MoveResult", "ApplyResult",
    "JournalInfo", "RestoreResult", "UndoResult",
    "ext_path", "plain_path", "canon", "is_locked",
    "plan", "apply", "undo", "list_journals", "read_journal",
    "journal_path_for", "undo_sidecar_for",
]
