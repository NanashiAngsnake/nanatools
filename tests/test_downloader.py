from __future__ import annotations

import datetime as dt
import http.server
import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nanatools.core.dl import (
    ARCHIVE_NAME,
    Cancelled,
    DownloadConfig,
    DownloadEngine,
    ErrorKind,
    JobState,
    QueueSummary,
    Status,
    build_ydl_opts,
    humanize_error,
    summarize,
)
import nanatools.core.dl.ytdlp as ver
from nanatools.core.dl.errors import clean
from nanatools.core.dl.models import (
    human_eta,
    human_size,
    human_speed,
)
from nanatools.jobs import CancelToken


class ScriptedYDL:
    def __init__(self, opts: dict, plan) -> None:
        self.opts = opts
        self.plan = plan
        self.closed = False

    def __enter__(self) -> "ScriptedYDL":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.closed = True
        return False


    def progress(self, **payload) -> None:
        for hook in self.opts.get("progress_hooks", ()):
            hook(payload)

    def post_process(self, name: str, status: str) -> None:
        for hook in self.opts.get("postprocessor_hooks", ()):
            hook({"status": status, "postprocessor": name, "info_dict": {}})

    def extract_info(self, url: str, download: bool = True):
        return self.plan(self, url)


def factory(plan):
    made: list[ScriptedYDL] = []

    def make(opts: dict) -> ScriptedYDL:
        ydl = ScriptedYDL(opts, plan)
        made.append(ydl)
        return ydl

    make.made = made
    return make


def plain_download(info: dict | None = None, *, chunks: int = 3):
    def plan(ydl: ScriptedYDL, url: str):
        for step in range(1, chunks + 1):
            ydl.progress(
                status="downloading", downloaded_bytes=step * 1000,
                total_bytes=chunks * 1000, speed=500_000.0, eta=2,
                filename="video.mp4", tmpfilename="video.mp4.part",
                info_dict={"title": "A video"},
            )
        ydl.progress(status="finished", filename="video.mp4",
                     downloaded_bytes=chunks * 1000, info_dict={"title": "A video"})
        ydl.post_process("MoveFiles", "started")
        ydl.post_process("MoveFiles", "finished")
        return info if info is not None else {
            "title": "A video",
            "requested_downloads": [{"filepath": "C:/out/video.mp4"}],
        }

    return plan


def collector() -> tuple[list, callable]:
    events: list = []
    lock = threading.Lock()

    def on_event(event) -> None:
        with lock:
            events.append(event)

    return events, on_event


def config(dest: Path, **kwargs) -> DownloadConfig:
    return DownloadConfig(dest=dest, **kwargs)


class EngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.dest = Path(self._tmp.name)
        self.events, self.on_event = collector()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def engine(self, plan) -> DownloadEngine:
        return DownloadEngine(self.on_event, ydl_factory=factory(plan), ffmpeg_dir="")

    def kinds(self, kind: str) -> list:
        return [event for event in self.events if event.kind == kind]


class HeadlessImportTests(unittest.TestCase):
    def test_core_imports_with_tkinter_blocked(self) -> None:
        script = textwrap.dedent(
            """
            import sys
            root = sys.argv[1]
            sys.path.insert(0, root)

            class Blocker:
                def find_module(self, name, path=None):
                    return self.find_spec(name, path)
                def find_spec(self, name, path=None, target=None):
                    top = name.split('.')[0]
                    if top in ('tkinter', 'customtkinter', '_tkinter'):
                        raise ImportError('blocked for the test: ' + name)
                    return None

            sys.meta_path.insert(0, Blocker())
            import nanatools.core.dl as dl
            dl.DownloadEngine(lambda event: None)
            dl.build_ydl_opts(dl.DownloadConfig(dest=__import__('pathlib').Path('.')))
            assert 'tkinter' not in sys.modules, 'the core dragged tkinter in'
            assert 'yt_dlp' not in sys.modules, 'the core imported yt-dlp eagerly'
            print('ok')
            """
        )
        root = str(Path(__file__).resolve().parent.parent)
        result = subprocess.run(
            [sys.executable, "-c", script, root],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0,
                         f"headless import failed:\n{result.stdout}\n{result.stderr}")
        self.assertIn("ok", result.stdout)


class OptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dest = Path("C:/downloads") if sys.platform == "win32" else Path("/downloads")

    def test_ignoreerrors_is_locked_off(self) -> None:
        for cfg in (
            config(self.dest),
            config(self.dest, audio_only=True),
            config(self.dest, subtitles=True, quality="720p"),
            config(self.dest, use_archive=False, playlist=False),
        ):
            with self.subTest(cfg=cfg.quality, audio=cfg.audio_only):
                self.assertIs(build_ydl_opts(cfg)["ignoreerrors"], False)

    def test_no_config_field_can_turn_ignoreerrors_on(self) -> None:
        self.assertNotIn(
            "ignoreerrors",
            {field for field in DownloadConfig(dest=self.dest).as_mapping()},
        )

    def test_quality_becomes_a_height_limited_format(self) -> None:
        opts = build_ydl_opts(config(self.dest, quality="720p"))
        self.assertEqual(
            opts["format"],
            "bestvideo[height<=720]+bestaudio/best[height<=720]/best")
        self.assertEqual(opts["merge_output_format"], "mp4")

    def test_best_quality(self) -> None:
        self.assertEqual(build_ydl_opts(config(self.dest))["format"],
                         "bestvideo+bestaudio/best")

    def test_audio_only_skips_the_video_options(self) -> None:
        opts = build_ydl_opts(config(self.dest, audio_only=True, subtitles=True))
        self.assertEqual(opts["format"], "bestaudio/best")
        self.assertNotIn("merge_output_format", opts)
        self.assertNotIn("writesubtitles", opts)
        self.assertEqual(opts["postprocessors"][0]["key"], "FFmpegExtractAudio")

    def test_subtitles(self) -> None:
        opts = build_ydl_opts(config(self.dest, subtitles=True))
        self.assertTrue(opts["writesubtitles"])
        self.assertFalse(opts["writeautomaticsub"])
        self.assertIn("fr", opts["subtitleslangs"])
        self.assertEqual(opts["postprocessors"][0]["key"], "FFmpegEmbedSubtitle")

    def test_archive_and_output_template(self) -> None:
        opts = build_ydl_opts(config(self.dest))
        self.assertTrue(opts["outtmpl"].startswith(str(self.dest)))
        self.assertTrue(opts["download_archive"].endswith(ARCHIVE_NAME))
        self.assertNotIn("download_archive", build_ydl_opts(config(self.dest, use_archive=False)))

    def test_cookies_only_when_a_browser_is_chosen(self) -> None:
        self.assertNotIn("cookiesfrombrowser", build_ydl_opts(config(self.dest)))
        self.assertNotIn("cookiesfrombrowser",
                         build_ydl_opts(config(self.dest, cookies_browser="None")))
        self.assertEqual(
            build_ydl_opts(config(self.dest, cookies_browser="firefox"))["cookiesfrombrowser"],
            ("firefox",))

    def test_logger_and_progress(self) -> None:
        self.assertNotIn("logger", build_ydl_opts(config(self.dest)))
        sentinel = object()
        self.assertIs(build_ydl_opts(config(self.dest), logger=sentinel)["logger"], sentinel)
        self.assertTrue(build_ydl_opts(config(self.dest))["noprogress"])
        self.assertTrue(build_ydl_opts(config(self.dest))["continuedl"])

    def test_rate_limit_and_ffmpeg_dir(self) -> None:
        opts = build_ydl_opts(config(self.dest, rate_limit=150_000), ffmpeg_dir="C:/ff/bin")
        self.assertEqual(opts["ratelimit"], 150_000)
        self.assertEqual(opts["ffmpeg_location"], "C:/ff/bin")
        self.assertNotIn("ratelimit", build_ydl_opts(config(self.dest)))


class ErrorTests(unittest.TestCase):
    def test_doubled_error_prefix_is_fully_stripped(self) -> None:
        info = humanize_error(Exception("ERROR: ERROR: Failed to decrypt with DPAPI"))
        self.assertIs(info.kind, ErrorKind.COOKIES)
        self.assertNotIn("ERROR:", info.message)
        self.assertIn("ERROR: ERROR:", info.raw)

    def test_404_is_named_and_the_caused_by_tail_dropped(self) -> None:
        info = humanize_error(Exception(
            "ERROR: [generic] Unable to download webpage: HTTP Error 404: "
            "File not found (caused by <HTTPError 404: File not found>)"))
        self.assertIs(info.kind, ErrorKind.NOT_FOUND)
        self.assertNotIn("caused by", info.message)
        self.assertNotIn("[generic]", info.message)

    def test_bad_url(self) -> None:
        info = humanize_error(Exception("ERROR: [generic] 'hello world' is not a valid URL"))
        self.assertIs(info.kind, ErrorKind.BAD_URL)

    def test_drm(self) -> None:
        info = humanize_error(Exception("ERROR: [site] This video is DRM protected"))
        self.assertIs(info.kind, ErrorKind.DRM)

    def test_stale_extractor_gets_an_update_hint(self) -> None:
        info = humanize_error(Exception("ERROR: [site] Unable to extract player version"))
        self.assertIs(info.kind, ErrorKind.EXTRACTOR_STALE)
        self.assertIsNotNone(info.hint)

    def test_network(self) -> None:
        info = humanize_error(Exception(
            "ERROR: [generic] Failed to establish a new connection: [WinError 10061]"))
        self.assertIs(info.kind, ErrorKind.NETWORK)

    def test_unknown_keeps_the_original_sentence(self) -> None:
        info = humanize_error(Exception("ERROR: something entirely new"))
        self.assertIs(info.kind, ErrorKind.UNKNOWN)
        self.assertEqual(info.message, "something entirely new")

    def test_ansi_is_stripped(self) -> None:
        self.assertEqual(clean("\x1b[31mred\x1b[0m"), "red")


class VersionTests(unittest.TestCase):
    def test_both_spellings_parse_to_the_same_date(self) -> None:
        self.assertEqual(ver.ytdlp_release_date("2026.03.17"), dt.date(2026, 3, 17))
        self.assertEqual(ver.ytdlp_release_date("2026.3.17"), dt.date(2026, 3, 17))

    def test_nightly_suffix_is_tolerated(self) -> None:
        self.assertEqual(ver.ytdlp_release_date("2026.03.17.232332"), dt.date(2026, 3, 17))

    def test_garbage_version(self) -> None:
        self.assertIsNone(ver.ytdlp_release_date("not-a-version"))
        self.assertIsNone(ver.ytdlp_age_days("not-a-version"))

    def test_age_is_computed_offline(self) -> None:
        self.assertEqual(
            ver.ytdlp_age_days("2026.03.17", today=dt.date(2026, 9, 2)), 169)

    def test_note_nags_only_when_stale(self) -> None:
        fresh = ver.version_note.__wrapped__ if hasattr(ver.version_note, "__wrapped__") else None
        self.assertIsNone(fresh)
        note = ver.version_note(stale_after_days=10_000)
        self.assertNotIn("consider", note)

    def test_update_command_targets_this_interpreter(self) -> None:
        command = ver.ytdlp_update_command()
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[-1], "yt-dlp")
        self.assertIn("-U", command)


class ModelTests(unittest.TestCase):
    def test_summary_counts_and_wording(self) -> None:
        jobs = [
            JobState(url="a", status=Status.DONE),
            JobState(url="b", status=Status.DONE),
            JobState(url="c", status=Status.SKIPPED),
            JobState(url="d", status=Status.FAILED),
            JobState(url="e", status=Status.CANCELLED),
            JobState(url="f", status=Status.PENDING),
        ]
        summary = summarize(jobs)
        self.assertEqual(
            (summary.done, summary.skipped, summary.failed, summary.cancelled),
            (2, 1, 1, 1))
        self.assertEqual(summary.total, 5)
        self.assertIn("2 finished", summary.as_text())
        self.assertIn("1 already had", summary.as_text())
        self.assertEqual(summary.tone, "danger")

    def test_empty_summary(self) -> None:
        self.assertEqual(QueueSummary().as_text(), "Nothing to do.")
        self.assertEqual(summarize([]).tone, "success")

    def test_config_validation(self) -> None:
        self.assertEqual(config(Path("C:/x")).validate(), [])
        self.assertTrue(config(Path(""), quality="4K").validate())
        self.assertTrue(config(Path("C:/x"), naming="nope").validate())
        self.assertTrue(config(Path("C:/x"), rate_limit=0).validate())

    def test_config_survives_a_json_round_trip(self) -> None:
        original = config(Path("C:/x"), quality="720p", cookies_browser="brave",
                          audio_only=True, use_archive=False)
        restored = DownloadConfig.from_mapping(original.as_mapping())
        self.assertEqual(restored.quality, "720p")
        self.assertEqual(restored.cookies_browser, "brave")
        self.assertTrue(restored.audio_only)
        self.assertFalse(restored.use_archive)

    def test_none_browser_round_trips_as_none(self) -> None:
        self.assertIsNone(
            DownloadConfig.from_mapping({"dest": "C:/x", "cookies_browser": "None"}).cookies_browser)

    def test_label_falls_back_to_the_url(self) -> None:
        job = JobState(url="https://example.test/v")
        self.assertEqual(job.label, "https://example.test/v")
        job.title = "Title"
        self.assertEqual(job.label, "Title")

    def test_human_helpers(self) -> None:
        self.assertEqual(human_size(0), "")
        self.assertEqual(human_size(1536), "1.5 KB")
        self.assertTrue(human_speed(1_048_576).endswith("/s"))
        self.assertEqual(human_eta(90), "1m 30s")
        self.assertEqual(human_eta(None), "")


class QueueTests(EngineTestCase):
    def test_duplicate_urls_are_not_queued_twice(self) -> None:
        engine = self.engine(plain_download())
        self.assertIsNotNone(engine.add("https://example.test/a"))
        self.assertIsNone(engine.add("https://example.test/a"))
        self.assertIsNone(engine.add("   "))
        self.assertEqual(len(engine.jobs()), 1)

    def test_a_finished_url_can_be_queued_again(self) -> None:
        engine = self.engine(plain_download())
        job = engine.add("https://example.test/a")
        job.status = Status.DONE
        self.assertIsNotNone(engine.add("https://example.test/a"))

    def test_remove_and_clear(self) -> None:
        engine = self.engine(plain_download())
        job = engine.add("https://example.test/a")
        self.assertTrue(engine.remove(job.job_id))
        self.assertEqual(engine.jobs(), [])
        engine.add_many(["https://example.test/b", "https://example.test/c"])
        self.assertTrue(engine.clear())
        self.assertEqual(engine.jobs(), [])

    def test_clear_refuses_while_a_queue_is_running(self) -> None:
        outcome: dict = {}

        def plan(ydl: ScriptedYDL, url: str):
            outcome["clear"] = engine.clear()
            outcome["remove"] = engine.remove(engine.jobs()[0].job_id)
            return plain_download()(ydl, url)

        engine = self.engine(plan)
        engine.add("https://example.test/a")
        engine.run(config(self.dest))
        self.assertFalse(outcome["clear"], "clear() emptied a running queue")
        self.assertFalse(outcome["remove"], "remove() dropped a running job")

    def test_clear_finished_keeps_the_rest(self) -> None:
        engine = self.engine(plain_download())
        first = engine.add("https://example.test/a")
        engine.add("https://example.test/b")
        first.status = Status.DONE
        self.assertEqual(engine.clear_finished(), 1)
        self.assertEqual(len(engine.jobs()), 1)


class DownloadTests(EngineTestCase):
    def test_a_plain_download_finishes(self) -> None:
        engine = self.engine(plain_download())
        job = engine.add("https://example.test/a")
        summary = engine.run(config(self.dest))

        self.assertIs(job.status, Status.DONE)
        self.assertEqual(job.title, "A video")
        self.assertEqual(job.frac, 1.0)
        self.assertEqual(summary.done, 1)
        self.assertIsNotNone(job.filepath)
        self.assertEqual(job.filepath.name, "video.mp4")
        self.assertEqual(len(self.kinds("finished")), 1)

    def test_archive_hit_is_skipped_not_done(self) -> None:
        def plan(ydl: ScriptedYDL, url: str):
            return {"title": "Already there"}

        engine = self.engine(plan)
        job = engine.add("https://example.test/a")
        summary = engine.run(config(self.dest))

        self.assertIs(job.status, Status.SKIPPED)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(summary.done, 0)
        self.assertFalse(job.saw_progress)
        self.assertEqual(job.frac, 0.0, "a skip must not fake a 100% bar")

    def test_one_failure_does_not_stop_the_queue(self) -> None:
        def plan(ydl: ScriptedYDL, url: str):
            if url.endswith("bad"):
                raise RuntimeError(
                    "ERROR: [generic] Unable to download webpage: HTTP Error 404: "
                    "File not found (caused by <HTTPError 404>)")
            return plain_download()(ydl, url)

        engine = self.engine(plan)
        bad = engine.add("https://example.test/bad")
        good = engine.add("https://example.test/good")
        summary = engine.run(config(self.dest))

        self.assertIs(bad.status, Status.FAILED)
        self.assertIs(bad.error.kind, ErrorKind.NOT_FOUND)
        self.assertIs(good.status, Status.DONE)
        self.assertEqual((summary.failed, summary.done), (1, 1))

    def test_a_missing_result_is_a_failure_not_a_success(self) -> None:
        engine = self.engine(lambda ydl, url: None)
        job = engine.add("https://example.test/a")
        engine.run(config(self.dest))
        self.assertIs(job.status, Status.FAILED)

    def test_ydl_is_used_as_a_context_manager(self) -> None:
        make = factory(plain_download())
        engine = DownloadEngine(self.on_event, ydl_factory=make, ffmpeg_dir="")
        engine.add("https://example.test/a")
        engine.run(config(self.dest))
        self.assertTrue(make.made[0].closed, "YoutubeDL was not closed")

    def test_the_logger_reaches_the_log_pane(self) -> None:
        def plan(ydl: ScriptedYDL, url: str):
            ydl.opts["logger"].error("ERROR: something went wrong")
            ydl.opts["logger"].debug("[debug] internal chatter")
            return plain_download()(ydl, url)

        engine = self.engine(plan)
        engine.add("https://example.test/a")
        engine.run(config(self.dest))

        logs = self.kinds("log")
        self.assertTrue(any(e.level == "error" and "went wrong" in e.text for e in logs))
        self.assertTrue(any(e.level == "debug" for e in logs),
                        "debug lines must survive as debug, for the details toggle")

    def test_progress_is_throttled_but_the_end_always_lands(self) -> None:
        engine = self.engine(plain_download(chunks=200))
        job = engine.add("https://example.test/a")
        engine.run(config(self.dest))

        progress = self.kinds("progress")
        self.assertLess(len(progress), 20,
                        "200 hook calls should not become 200 UI events")
        self.assertEqual(job.frac, 1.0)

    def test_hls_fragments_drive_the_bar_when_there_is_no_total(self) -> None:
        seen: list[float] = []

        def plan(ydl: ScriptedYDL, url: str):
            ydl.progress(status="downloading", downloaded_bytes=10, total_bytes=None,
                         fragment_index=5, fragment_count=20, info_dict={})
            seen.append(engine.jobs()[0].frac)
            return {"title": "hls"}

        engine = self.engine(plan)
        engine.add("https://example.test/a")
        engine.run(config(self.dest))
        self.assertEqual(seen, [0.25])

    def test_a_failed_resume_is_reported(self) -> None:
        part = self.dest / "video.mp4.part"
        part.write_bytes(b"0" * 2_000_000)

        def plan(ydl: ScriptedYDL, url: str):
            ydl.progress(status="downloading", downloaded_bytes=1024,
                         total_bytes=5_000_000, tmpfilename=str(part), info_dict={})
            ydl.progress(status="finished", filename=str(part.with_suffix("")),
                         info_dict={})
            return {"title": "restarted"}

        engine = self.engine(plan)
        engine.add("https://example.test/a")
        engine.run(config(self.dest))

        self.assertTrue(any("does not support resuming" in event.text
                            for event in self.kinds("log")))


class CancellationTests(EngineTestCase):
    def test_cancelling_mid_download_stops_that_job_and_the_rest(self) -> None:
        token = CancelToken()

        def plan(ydl: ScriptedYDL, url: str):
            token.cancel()
            ydl.progress(status="downloading", downloaded_bytes=1,
                         total_bytes=100, info_dict={})
            raise AssertionError("the progress hook did not interrupt the download")

        engine = self.engine(plan)
        first = engine.add("https://example.test/a")
        second = engine.add("https://example.test/b")
        summary = engine.run(config(self.dest), token)

        self.assertIs(first.status, Status.CANCELLED)
        self.assertIs(second.status, Status.CANCELLED)
        self.assertEqual(summary.cancelled, 2)

    def test_cancel_during_post_processing_is_deferred_never_killed(self) -> None:
        token = CancelToken()
        record: dict = {"pp_raised": False, "ffmpeg_finished": False}

        def plan(ydl: ScriptedYDL, url: str):
            ydl.progress(status="downloading", downloaded_bytes=50, total_bytes=100,
                         info_dict={"title": "Big video"})
            ydl.progress(status="finished", filename="big.mp4", info_dict={})
            token.cancel()
            try:
                ydl.post_process("Merger", "started")
            except BaseException:
                record["pp_raised"] = True
                raise
            record["ffmpeg_finished"] = True
            ydl.post_process("Merger", "finished")
            return {"title": "Big video", "requested_downloads": [{"filepath": "big.mp4"}]}

        engine = self.engine(plan)
        job = engine.add("https://example.test/a")
        engine.run(config(self.dest), token)

        self.assertFalse(record["pp_raised"],
                         "the post-processor hook raised; ffmpeg would be interrupted")
        self.assertTrue(record["ffmpeg_finished"])
        self.assertIs(job.status, Status.CANCELLED)
        self.assertTrue(any("processing had started" in event.text
                            for event in self.kinds("log")))

    def test_a_cancel_during_processing_shows_as_cancelling_first(self) -> None:
        token = CancelToken()
        seen: list[Status] = []
        engine = None

        def plan(ydl: ScriptedYDL, url: str):
            ydl.progress(status="downloading", downloaded_bytes=1, total_bytes=2,
                         info_dict={})
            ydl.post_process("Merger", "started")
            seen.append(engine.jobs()[0].status)
            token.cancel()
            ydl.post_process("Merger", "started")
            seen.append(engine.jobs()[0].status)
            ydl.post_process("Merger", "finished")
            return {"title": "x"}

        engine = self.engine(plan)
        engine.add("https://example.test/a")
        engine.run(config(self.dest), token)

        self.assertEqual(seen, [Status.PROCESSING, Status.CANCELLING])

    def test_duplicate_post_processor_events_do_not_drift(self) -> None:
        def plan(ydl: ScriptedYDL, url: str):
            ydl.progress(status="downloading", downloaded_bytes=1, total_bytes=1,
                         info_dict={})
            for status in ("started", "started", "finished", "finished"):
                ydl.post_process("ExtractAudio", status)
            for status in ("started", "finished"):
                ydl.post_process("MoveFiles", status)
            return {"title": "audio"}

        engine = self.engine(plan)
        job = engine.add("https://example.test/a")
        engine.run(config(self.dest))

        self.assertIs(job.status, Status.DONE)
        self.assertEqual(job.stage, "")

    def test_cancelling_one_pending_job_leaves_the_others_alone(self) -> None:
        engine = self.engine(plain_download())
        first = engine.add("https://example.test/a")
        second = engine.add("https://example.test/b")
        engine.cancel_job(first.job_id)
        summary = engine.run(config(self.dest))

        self.assertIs(first.status, Status.CANCELLED)
        self.assertIs(second.status, Status.DONE)
        self.assertEqual((summary.cancelled, summary.done), (1, 1))

    def test_wait_idle_is_true_when_nothing_is_running(self) -> None:
        engine = self.engine(plain_download())
        self.assertTrue(engine.wait_idle(0.1))

    def test_a_second_run_is_refused(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def plan(ydl: ScriptedYDL, url: str):
            started.set()
            release.wait(5)
            return plain_download()(ydl, url)

        engine = self.engine(plan)
        engine.add("https://example.test/a")
        worker = threading.Thread(target=lambda: engine.run(config(self.dest)))
        worker.start()
        try:
            started.wait(5)
            self.assertFalse(engine.wait_idle(0.05))
            with self.assertRaises(RuntimeError):
                engine.run(config(self.dest))
        finally:
            release.set()
            worker.join(10)
        self.assertTrue(engine.wait_idle(5))


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    payload = b"\0" * (4 * 1024 * 1024)

    def do_HEAD(self) -> None:
        self._headers()

    def do_GET(self) -> None:
        self._headers()
        try:
            self.wfile.write(self.payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(self.payload)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


@unittest.skipUnless(ver.ytdlp_available(), "yt-dlp is not installed")
class IgnoreErrorsRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/sample.mp4"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)

    def _download(self, *, ignoreerrors: bool):
        from yt_dlp import YoutubeDL

        with TemporaryDirectory() as tmp:
            cfg = config(Path(tmp), use_archive=False, rate_limit=200_000)
            opts = build_ydl_opts(cfg)
            opts["ignoreerrors"] = ignoreerrors
            opts["retries"] = 0
            opts["logger"] = _SilentLogger()
            opts["progress_hooks"] = [self._raise_on_first]
            with YoutubeDL(opts) as ydl:
                return ydl.extract_info(self.url, download=True)

    @staticmethod
    def _raise_on_first(payload: dict) -> None:
        if payload.get("status") == "downloading":
            raise Cancelled()

    def test_cancel_escapes_extract_info(self) -> None:
        with self.assertRaises(Cancelled):
            self._download(ignoreerrors=False)

    def test_ignoreerrors_true_swallows_the_cancellation(self) -> None:
        self.assertIsNone(self._download(ignoreerrors=True))

    def test_the_shipped_options_have_it_off(self) -> None:
        self.assertIs(build_ydl_opts(config(Path("."))) ["ignoreerrors"], False)


class _SilentLogger:
    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str, *a, **k) -> None: ...
    def error(self, message: str, *a, **k) -> None: ...


if __name__ == "__main__":
    unittest.main(verbosity=2)
