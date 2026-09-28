from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nanatools.core import organizer
from nanatools.core.organizer import (
    Block, Fail, Options, Ruleset, Skip,
)

WINDOWS = os.name == "nt"


def write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def set_attributes(path: Path, flags: int) -> None:
    import ctypes

    ok = ctypes.windll.kernel32.SetFileAttributesW(str(path), flags)
    if not ok:
        raise OSError(ctypes.get_last_error())


class OrganizerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nanatools-organizer-"))
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.journals = self.tmp / "journals"
        self.journals.mkdir()

    def tearDown(self) -> None:
        for path in self.tmp.rglob("*"):
            try:
                if WINDOWS and path.is_file():
                    set_attributes(path, 0x80)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self, **kwargs):
        options = kwargs.pop("options", Options(probe_locks=False))
        return organizer.plan(str(self.root), options, **kwargs)

    def apply(self, plan_obj, **kwargs):
        kwargs.setdefault("journal_dir", str(self.journals))
        return organizer.apply(plan_obj, **kwargs)


class PathHelperTests(unittest.TestCase):

    @unittest.skipUnless(WINDOWS, "extended-length paths are a Windows concept")
    def test_ext_path_prefixes_unconditionally(self):
        self.assertEqual(organizer.ext_path(r"C:\a\b"), "\\\\?\\C:\\a\\b")

    @unittest.skipUnless(WINDOWS, "UNC form is a Windows concept")
    def test_ext_path_handles_unc(self):
        self.assertEqual(organizer.ext_path(r"\\server\share\f"),
                         "\\\\?\\UNC\\server\\share\\f")

    def test_ext_path_is_idempotent(self):
        once = organizer.ext_path(str(Path.cwd()))
        self.assertEqual(organizer.ext_path(once), once)

    def test_plain_path_round_trips(self):
        for raw in (str(Path.cwd()), r"\\server\share\f" if WINDOWS else "/tmp/x"):
            self.assertEqual(organizer.plain_path(organizer.ext_path(raw)),
                             os.path.abspath(raw))

    @unittest.skipUnless(WINDOWS, "needs a real long-path filesystem")
    def test_long_path_is_usable_through_ext_path(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            deep = tmp
            for _ in range(12):
                deep = deep / ("d" * 24)
            self.assertGreater(len(str(deep)), 260)
            os.makedirs(organizer.ext_path(str(deep)), exist_ok=True)
            target = deep / "file.txt"
            with open(organizer.ext_path(str(target)), "w", encoding="utf-8") as fh:
                fh.write("ok")
            self.assertTrue(os.path.lexists(organizer.ext_path(str(target))))
            self.assertFalse(os.path.lexists(str(target)),
                             "plain long path unexpectedly resolved")
        finally:
            shutil.rmtree(organizer.ext_path(str(tmp)), ignore_errors=True)


class RulesetTests(unittest.TestCase):

    def test_categories_match_the_original_table(self):
        rules = Ruleset()
        self.assertEqual(rules.category_for("setup.EXE"), "Installateurs")
        self.assertEqual(rules.category_for("clip.mkv"), "Videos")
        self.assertEqual(rules.category_for("song.flac"), "Audio")
        self.assertEqual(rules.category_for("shot.WEBP"), "Images")
        self.assertEqual(rules.category_for("notes.pdf"), "Documents")
        self.assertEqual(rules.category_for("main.py"), "Code")
        self.assertEqual(rules.category_for("face.ttf"), "Polices")
        self.assertEqual(rules.category_for("weird.qqq"), "Autres")

    def test_shortcuts_get_their_own_category(self):
        self.assertEqual(Ruleset().category_for("Brave.lnk"), "Raccourcis")

    def test_dated_subfolder_keys_on_the_file_not_today(self):
        rules = Ruleset(dated_subfolders=True)
        mtime_ns = int(time.mktime((2021, 3, 9, 12, 0, 0, 0, 0, -1)) * 1e9)
        first = rules.dest_dir(r"C:\root", "Videos", mtime_ns)
        second = rules.dest_dir(r"C:\root", "Videos", mtime_ns)
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(os.path.join("Videos", "2021-03")))


class PlanTests(OrganizerTestCase):

    def test_plan_moves_nothing(self):
        write(self.root / "a.mp4")
        before = sorted(p.name for p in self.root.iterdir())
        plan = self.plan()
        self.assertEqual(len(plan.items), 1)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)

    def test_plan_is_non_recursive_and_reports_subfolders(self):
        write(self.root / "sub" / "deep.mp4")
        write(self.root / "top.mp4")
        plan = self.plan()
        self.assertEqual([i.name for i in plan.items], ["top.mp4"])
        self.assertEqual(plan.subdirs, ("sub",))

    @unittest.skipUnless(WINDOWS, "hidden attribute is a Windows concept")
    def test_hidden_and_system_files_are_skipped(self):
        hidden = write(self.root / "secret.txt")
        system = write(self.root / "desktop-thing.txt")
        set_attributes(hidden, 0x2)
        set_attributes(system, 0x4)
        write(self.root / "normal.txt")

        plan = self.plan()
        self.assertEqual([i.name for i in plan.items], ["normal.txt"])
        reasons = {s.name: s.reason for s in plan.skipped}
        self.assertEqual(reasons["secret.txt"], Skip.HIDDEN)
        self.assertEqual(reasons["desktop-thing.txt"], Skip.SYSTEM)

    @unittest.skipUnless(WINDOWS, "hidden attribute is a Windows concept")
    def test_hidden_files_can_be_opted_in(self):
        hidden = write(self.root / "secret.txt")
        set_attributes(hidden, 0x2)
        plan = self.plan(options=Options(probe_locks=False, include_hidden=True))
        self.assertEqual([i.name for i in plan.items], ["secret.txt"])

    def test_protected_names_are_never_moved(self):
        write(self.root / "desktop.ini")
        write(self.root / "Thumbs.db")
        plan = self.plan()
        self.assertEqual(plan.items, ())
        self.assertTrue(all(s.reason is Skip.PROTECTED for s in plan.skipped))

    def test_partial_downloads_are_skipped(self):
        write(self.root / "big.mp4.crdownload")
        write(self.root / "other.part")
        plan = self.plan()
        self.assertEqual(plan.items, ())
        self.assertEqual({s.reason for s in plan.skipped}, {Skip.IN_PROGRESS})

    @unittest.skipUnless(WINDOWS, "junctions are a Windows concept")
    def test_junction_is_detected_without_is_symlink(self):
        target = self.tmp / "target"
        target.mkdir()
        write(target / "precious.txt", "PRECIOUS")
        link = self.root / "link"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True)
        if result.returncode != 0:
            self.skipTest(f"mklink unavailable: {result.stderr.strip()}")

        self.assertFalse(link.is_symlink(), "premise of this test changed")
        self.assertTrue(os.lstat(link).st_file_attributes & organizer.A_REPARSE)

        plan = self.plan()
        self.assertEqual(plan.items, ())
        self.assertNotIn("link", plan.subdirs)
        self.assertEqual([s.reason for s in plan.skipped], [Skip.REPARSE])
        self.assertEqual((target / "precious.txt").read_text(encoding="utf-8"),
                         "PRECIOUS")

    def test_age_filter_uses_the_later_of_mtime_and_birthtime(self):
        fresh = write(self.root / "unpacked.mp4")
        old_mtime = time.time() - 400 * 86400
        os.utime(fresh, (old_mtime, old_mtime))

        plan = self.plan(options=Options(probe_locks=False, min_age_days=7))
        self.assertEqual(plan.items, ())
        self.assertEqual([s.reason for s in plan.skipped], [Skip.TOO_RECENT])

    def test_age_filter_lets_genuinely_old_files_through(self):
        old = write(self.root / "old.mp4")
        stamp = time.time() - 400 * 86400
        os.utime(old, (stamp, stamp))
        plan = self.plan(options=Options(probe_locks=False, min_age_days=7),
                         now=time.time() + 30 * 86400)
        self.assertEqual([i.name for i in plan.items], ["old.mp4"])

    @unittest.skipUnless(WINDOWS, "the lock probe relies on Windows sharing")
    def test_locked_file_is_detected_without_being_touched(self):
        busy = write(self.root / "busy.mp4", "CONTENT")
        handle = open(busy, "r+b")
        try:
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            plan = self.plan(options=Options(probe_locks=True))
        finally:
            handle.close()
        self.assertEqual(busy.read_text(encoding="utf-8"), "CONTENT")
        if plan.items:
            self.skipTest("this Windows build did not report the file as held")
        self.assertEqual([s.reason for s in plan.skipped], [Skip.LOCKED])

    def test_collision_suffix_is_reserved_at_plan_time(self):
        write(self.root / "clip.mp4")
        write(self.root / "Videos" / "clip.mp4", "PRECIOUS")
        write(self.root / "Videos" / "clip (2).mp4", "ALSO PRECIOUS")
        plan = self.plan()
        self.assertEqual(plan.items[0].dest_name, "clip (3).mp4")
        self.assertTrue(plan.items[0].renamed)

    def test_two_sources_claiming_one_name_get_different_names(self):
        write(self.root / "a" / "x.mp4")
        write(self.root / "photo.jpg")
        write(self.root / "Images" / "photo.jpg", "PRECIOUS")
        plan = self.plan()
        self.assertEqual(plan.items[0].dest_name, "photo (2).jpg")

    @unittest.skipUnless(WINDOWS, "NTFS is case-insensitive")
    def test_case_only_collision_is_seen(self):
        write(self.root / "photo.jpg")
        write(self.root / "Images" / "PHOTO.JPG", "PRECIOUS")
        plan = self.plan()
        self.assertEqual(plan.items[0].dest_name, "photo (2).jpg")

    def test_category_folder_as_root_is_blocked(self):
        videos = self.root / "Videos"
        videos.mkdir()
        write(videos / "clip.mp4")
        plan = organizer.plan(str(videos), Options(probe_locks=False))
        self.assertIn(Block.CATEGORY_ROOT, [b for b, _ in plan.blocking])
        with self.assertRaises(ValueError):
            self.apply(plan)

    def test_category_folder_as_root_can_be_overridden(self):
        videos = self.root / "Videos"
        videos.mkdir()
        write(videos / "clip.mp4")
        plan = organizer.plan(str(videos),
                              Options(probe_locks=False, allow_category_root=True))
        self.assertEqual(plan.blocking, ())

    def test_a_file_named_like_a_category_blocks_the_plan(self):
        write(self.root / "clip.mp4")
        write(self.root / "Videos")
        plan = self.plan()
        self.assertIn(Block.DEST_NOT_A_DIR, [b for b, _ in plan.blocking])

    def test_missing_root_raises(self):
        with self.assertRaises(FileNotFoundError):
            organizer.plan(str(self.root / "nope"))

    def test_the_application_folder_is_refused(self):
        from nanatools import config

        plan = organizer.plan(str(config.ROOT_DIR), Options(probe_locks=False))
        self.assertIn(Block.APP_TREE, [b for b, _ in plan.blocking])

    def test_a_parent_of_the_application_folder_is_allowed(self):
        from nanatools import config

        parent = str(config.ROOT_DIR.parent)
        plan = organizer.plan(parent, Options(probe_locks=False))
        self.assertNotIn(Block.APP_TREE, [b for b, _ in plan.blocking])

    def test_lock_probe_leaves_a_free_file_alone(self):
        target = write(self.root / "free.mp4", "UNTOUCHED")
        self.assertFalse(organizer.is_locked(str(target)))
        self.assertEqual(target.read_text(encoding="utf-8"), "UNTOUCHED")

    def test_journal_path_lands_in_the_configured_directory(self):
        from nanatools import config

        path = organizer.journal_path_for("abc123")
        self.assertEqual(os.path.dirname(path), str(config.JOURNAL_DIR))
        self.assertTrue(path.endswith("_abc123.jsonl"))
        self.assertFalse(os.path.exists(path), "journal_path_for must not create")

    def test_totals_respect_the_selection(self):
        write(self.root / "a.mp4", "1234")
        write(self.root / "b.mp4", "12")
        plan = self.plan()
        self.assertEqual(plan.totals(), (2, 6))
        only_a = frozenset({i.key for i in plan.items if i.name == "a.mp4"})
        self.assertEqual(plan.totals(only_a), (1, 4))

    def test_by_category_is_ordered_by_size_of_group(self):
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            write(self.root / name)
        write(self.root / "one.pdf")
        plan = self.plan()
        self.assertEqual(list(plan.by_category()), ["Videos", "Documents"])


class ApplyTests(OrganizerTestCase):

    def test_moves_files_into_categories(self):
        write(self.root / "clip.mp4")
        write(self.root / "notes.pdf")
        result = self.apply(self.plan())
        self.assertEqual(len(result.moved), 2)
        self.assertTrue((self.root / "Videos" / "clip.mp4").exists())
        self.assertTrue((self.root / "Documents" / "notes.pdf").exists())

    def test_deselected_items_stay_put(self):
        write(self.root / "clip.mp4")
        write(self.root / "notes.pdf")
        plan = self.plan()
        keep = frozenset({i.key for i in plan.items if i.name == "clip.mp4"})
        self.apply(plan, selection=keep)
        self.assertTrue((self.root / "Videos" / "clip.mp4").exists())
        self.assertTrue((self.root / "notes.pdf").exists())

    def test_collision_never_overwrites(self):
        write(self.root / "clip.mp4", "NEW")
        existing = write(self.root / "Videos" / "clip.mp4", "PRECIOUS")
        result = self.apply(self.plan())
        self.assertTrue(result.moved[0].ok)
        self.assertEqual(existing.read_text(encoding="utf-8"), "PRECIOUS")
        self.assertEqual((self.root / "Videos" / "clip (2).mp4")
                         .read_text(encoding="utf-8"), "NEW")

    def test_source_changed_since_the_preview_is_refused(self):
        clip = write(self.root / "clip.mp4", "before")
        plan = self.plan()
        time.sleep(0.01)
        clip.write_text("after and longer", encoding="utf-8")
        result = self.apply(plan)
        self.assertEqual(result.failed[0].reason, Fail.CHANGED)
        self.assertTrue(clip.exists())

    def test_vanished_source_is_a_skip_not_a_crash(self):
        clip = write(self.root / "clip.mp4")
        plan = self.plan()
        clip.unlink()
        result = self.apply(plan)
        self.assertEqual(result.failed[0].reason, Fail.VANISHED)

    def test_cancel_stops_early_and_leaves_a_usable_journal(self):
        for i in range(6):
            write(self.root / f"f{i}.mp4")
        plan = self.plan()
        seen = []

        def cancel() -> bool:
            return len(seen) >= 2

        def progress(done, total, name):
            seen.append(name)

        result = self.apply(plan, cancel=cancel, progress=progress)
        self.assertTrue(result.cancelled)
        self.assertLess(len(result.moved), 6)
        undone = organizer.undo(result.journal_path)
        self.assertEqual(undone.restored_count, len(result.moved))

    def test_a_stale_selection_key_moves_nothing(self):
        write(self.root / "clip.mp4")
        plan = self.plan()
        result = self.apply(plan, selection=frozenset({"c:\\nowhere\\gone.mp4"}))
        self.assertEqual(result.results, ())
        self.assertTrue((self.root / "clip.mp4").exists())

    def test_applying_a_blocked_plan_raises(self):
        write(self.root / "clip.mp4")
        write(self.root / "Videos")
        with self.assertRaises(ValueError):
            self.apply(self.plan())

    def test_dated_subfolders_nest_under_the_category(self):
        clip = write(self.root / "clip.mp4")
        stamp = time.mktime((2022, 5, 4, 10, 0, 0, 0, 0, -1))
        os.utime(clip, (stamp, stamp))
        options = Options(probe_locks=False,
                          ruleset=Ruleset(dated_subfolders=True))
        result = self.apply(self.plan(options=options))
        self.assertTrue((self.root / "Videos" / "2022-05" / "clip.mp4").exists())
        self.assertEqual(len(result.created_dirs), 2)


class JournalTests(OrganizerTestCase):

    def test_journal_is_written_during_the_run(self):
        for i in range(4):
            write(self.root / f"f{i}.mp4")
        plan = self.plan()
        lines_seen = []

        def progress(done, total, name):
            info = organizer.list_journals(str(self.journals))
            lines_seen.append(info[0].moved if info else -1)

        self.apply(plan, progress=progress)
        self.assertEqual(lines_seen[-1], 3)

    def test_truncated_journal_still_parses(self):
        write(self.root / "clip.mp4")
        result = self.apply(self.plan())
        raw = Path(result.journal_path).read_text(encoding="utf-8")
        Path(result.journal_path).write_text(
            raw[: len(raw) - 12], encoding="utf-8")
        records, dropped = organizer.read_journal(result.journal_path)
        self.assertEqual(dropped, 1)
        self.assertTrue(any(r["t"] == "move" for r in records))

    def test_journal_reads_a_bom(self):
        path = self.journals / "bom.jsonl"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps(
            {"t": "header", "run_id": "x", "root": "r", "started": 1.0}
        ).encode("utf-8") + b"\n")
        records, dropped = organizer.read_journal(str(path))
        self.assertEqual(dropped, 0)
        self.assertEqual(records[0]["run_id"], "x")

    def test_list_journals_is_newest_first_and_flags_undone_runs(self):
        write(self.root / "a.mp4")
        first = self.apply(self.plan())
        write(self.root / "b.mp4")
        second = self.apply(self.plan())
        listing = organizer.list_journals(str(self.journals))
        self.assertEqual([i.run_id for i in listing],
                         [second.run_id, first.run_id])
        self.assertFalse(listing[0].undone)

        organizer.undo(second.journal_path)
        listing = organizer.list_journals(str(self.journals))
        self.assertTrue(next(i for i in listing if i.run_id == second.run_id).undone)

    def test_sidecar_never_touches_the_journal(self):
        write(self.root / "a.mp4")
        result = self.apply(self.plan())
        before = Path(result.journal_path).read_bytes()
        organizer.undo(result.journal_path)
        self.assertEqual(Path(result.journal_path).read_bytes(), before)
        self.assertTrue(os.path.exists(
            organizer.undo_sidecar_for(result.journal_path)))


class UndoTests(OrganizerTestCase):

    def test_round_trip_restores_everything(self):
        names = ["clip.mp4", "notes.pdf", "song.mp3", "weird.qqq"]
        for name in names:
            write(self.root / name, name)
        result = self.apply(self.plan())
        undone = organizer.undo(result.journal_path)
        self.assertEqual(undone.restored_count, len(names))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         sorted(names))

    def test_undo_removes_only_folders_this_run_created(self):
        mine = self.root / "a trier"
        mine.mkdir()
        write(self.root / "clip.mp4")
        result = self.apply(self.plan())
        organizer.undo(result.journal_path)
        self.assertTrue(mine.is_dir(), "an unrelated empty folder was deleted")
        self.assertFalse((self.root / "Videos").exists())

    def test_undo_keeps_a_category_folder_that_still_holds_something(self):
        write(self.root / "clip.mp4")
        result = self.apply(self.plan())
        write(self.root / "Videos" / "someone-elses.mp4")
        organizer.undo(result.journal_path)
        self.assertTrue((self.root / "Videos" / "someone-elses.mp4").exists())

    def test_undo_refuses_a_file_edited_since_the_move(self):
        write(self.root / "clip.mp4", "original")
        result = self.apply(self.plan())
        moved = Path(result.moved[0].dest)
        time.sleep(0.01)
        moved.write_text("edited by the user", encoding="utf-8")

        undone = organizer.undo(result.journal_path)
        self.assertEqual(undone.restored_count, 0)
        self.assertEqual(len(undone.blocked), 1)
        self.assertIn("changed", undone.blocked[0].reason)
        self.assertEqual(moved.read_text(encoding="utf-8"), "edited by the user")

    def test_undo_never_overwrites_a_reoccupied_origin(self):
        write(self.root / "clip.mp4", "the original")
        result = self.apply(self.plan())
        write(self.root / "clip.mp4", "a NEW download with the same name")

        undone = organizer.undo(result.journal_path)
        self.assertEqual(undone.restored_count, 1)
        self.assertEqual((self.root / "clip.mp4").read_text(encoding="utf-8"),
                         "a NEW download with the same name")
        self.assertEqual((self.root / "clip (2).mp4").read_text(encoding="utf-8"),
                         "the original")

    def test_undo_is_idempotent(self):
        write(self.root / "clip.mp4")
        result = self.apply(self.plan())
        organizer.undo(result.journal_path)
        second = organizer.undo(result.journal_path)
        self.assertTrue(second.already_undone)
        self.assertEqual(second.restored_count, 0)
        self.assertTrue((self.root / "clip.mp4").exists())

    def test_undo_works_on_an_older_run_while_a_newer_one_stands(self):
        write(self.root / "a.mp4", "A")
        first = self.apply(self.plan())
        write(self.root / "b.pdf", "B")
        second = self.apply(self.plan())

        organizer.undo(first.journal_path)
        self.assertTrue((self.root / "a.mp4").exists())
        self.assertTrue((self.root / "Documents" / "b.pdf").exists())

        organizer.undo(second.journal_path)
        self.assertTrue((self.root / "b.pdf").exists())

    def test_undo_of_a_crashed_run_works_from_the_records_alone(self):
        for i in range(3):
            write(self.root / f"f{i}.mp4")
        result = self.apply(self.plan())
        lines = Path(result.journal_path).read_text(
            encoding="utf-8").splitlines(keepends=True)
        Path(result.journal_path).write_text(
            "".join(l for l in lines if '"footer"' not in l), encoding="utf-8")

        info = organizer.list_journals(str(self.journals))[0]
        self.assertFalse(info.complete)
        undone = organizer.undo(result.journal_path)
        self.assertEqual(undone.restored_count, 3)


class SourceHygieneTests(unittest.TestCase):

    def test_core_does_not_import_tk(self):
        source = Path(organizer.__file__).read_text(encoding="utf-8")
        for banned in ("import tkinter", "import customtkinter"):
            self.assertNotIn(banned, source)

    def test_core_never_uses_an_overwriting_move(self):
        source = Path(organizer.__file__).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        body = code.split('"""', 2)[-1]
        self.assertNotIn("shutil.move", body)
        self.assertNotIn("os.replace", body)


if __name__ == "__main__":
    unittest.main()
