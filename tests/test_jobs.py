from __future__ import annotations

import sys
import threading
import time
import tkinter
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nanatools.jobs import JobCancelled, JobRunner


def pump(root: tkinter.Tk, seconds: float = 2.0, until=None) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None or bool(until())


class JobRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tkinter.Tk()
        self.root.withdraw()
        self.runner = JobRunner(self.root, poll_ms=10)

    def tearDown(self) -> None:
        try:
            self.runner.shutdown()
        finally:
            try:
                self.root.destroy()
            except tkinter.TclError:
                pass


    def test_callbacks_run_on_the_main_thread(self) -> None:
        main_thread = threading.main_thread().ident
        seen: dict[str, object] = {}

        def work(ctx):
            seen["worker_thread"] = threading.get_ident()
            ctx.progress(step=1)
            return "result"

        self.runner.submit(
            work,
            on_progress=lambda p: seen.setdefault("progress_thread", threading.get_ident()),
            on_done=lambda r: seen.update(done_thread=threading.get_ident(), result=r),
        )
        self.assertTrue(pump(self.root, until=lambda: "result" in seen), "job never finished")

        self.assertNotEqual(seen["worker_thread"], main_thread,
                            "work must not run on the UI thread")
        self.assertEqual(seen["progress_thread"], main_thread,
                         "on_progress must be marshalled to the UI thread")
        self.assertEqual(seen["done_thread"], main_thread,
                         "on_done must be marshalled to the UI thread")
        self.assertEqual(seen["result"], "result")


    def test_cancellation_stops_the_job(self) -> None:
        state: dict[str, object] = {"iterations": 0}
        started = threading.Event()

        def work(ctx):
            started.set()
            for _ in range(10_000):
                ctx.check_cancelled()
                state["iterations"] = int(state["iterations"]) + 1
                time.sleep(0.001)
            state["completed"] = True

        handle = self.runner.submit(
            work,
            on_done=lambda r: state.update(outcome="done"),
            on_cancelled=lambda: state.update(outcome="cancelled"),
        )
        self.assertTrue(started.wait(2.0), "job never started")
        pump(self.root, 0.05)
        handle.cancel()

        self.assertTrue(pump(self.root, until=lambda: "outcome" in state), "no terminal event")
        self.assertEqual(state["outcome"], "cancelled")
        self.assertNotIn("completed", state, "job ran to completion despite cancel")
        self.assertLess(int(state["iterations"]), 10_000)


    def test_callback_touching_a_dead_widget_is_contained(self) -> None:
        dead_label = tkinter.Label(self.root, text="before")
        dead_label.destroy()

        escaped: list[tuple] = []
        self.root.report_callback_exception = lambda *exc: escaped.append(exc)

        trace: list[str] = []
        recovered: dict[str, object] = {}

        def finished_work(ctx):
            return "late result"

        def update_a_dead_widget(result) -> None:
            trace.append("callback entered")
            dead_label.configure(text=str(result))
            trace.append("should never get here")

        self.runner.submit(finished_work, on_done=update_a_dead_widget)
        pump(self.root, 0.4)

        self.assertEqual(trace, ["callback entered"],
                         "the callback should have raised part-way through")
        self.assertEqual(escaped, [],
                         "the TclError escaped the runner and reached Tk's "
                         "exception handler, which would show the user a dialog")

        self.runner.submit(lambda ctx: "still working",
                           on_done=lambda r: recovered.update(value=r))
        self.assertTrue(pump(self.root, until=lambda: "value" in recovered),
                        "a TclError in one callback killed the pump for everyone")
        self.assertEqual(recovered["value"], "still working")

    def test_destroy_during_job(self) -> None:
        started = threading.Event()
        release = threading.Event()
        errors: list[BaseException] = []

        def work(ctx):
            started.set()
            release.wait(3.0)
            ctx.progress(late=True)
            return "too late"

        def record(args) -> None:
            if args.exc_value is not None:
                errors.append(args.exc_value)

        previous_hook = threading.excepthook
        threading.excepthook = record
        try:
            self.runner.submit(work, on_progress=lambda p: None, on_done=lambda r: None)
            self.assertTrue(started.wait(2.0), "job never started")

            self.runner.shutdown()
            self.root.destroy()

            release.set()
            time.sleep(0.3)
        finally:
            threading.excepthook = previous_hook

        self.assertEqual(errors, [], f"worker raised after window destroy: {errors}")


    def test_exception_reaches_on_error(self) -> None:
        captured: dict[str, BaseException] = {}

        def work(ctx):
            raise ValueError("boom")

        self.runner.submit(work, on_error=lambda exc: captured.update(exc=exc))
        self.assertTrue(pump(self.root, until=lambda: "exc" in captured), "no error event")
        self.assertIsInstance(captured["exc"], ValueError)
        self.assertEqual(str(captured["exc"]), "boom")


    def test_bad_callback_does_not_kill_the_pump(self) -> None:
        survived: dict[str, object] = {}

        def boom(ctx):
            return 1

        def fine(ctx):
            return 2

        self.runner.submit(boom, on_done=lambda r: (_ for _ in ()).throw(RuntimeError("bad cb")))
        pump(self.root, 0.3)
        self.runner.submit(fine, on_done=lambda r: survived.update(value=r))

        self.assertTrue(pump(self.root, until=lambda: "value" in survived),
                        "pump stopped after a callback raised")
        self.assertEqual(survived["value"], 2)


    def test_job_cancelled_exception_is_a_clean_stop(self) -> None:
        state: dict[str, object] = {}

        def work(ctx):
            raise JobCancelled()

        self.runner.submit(
            work,
            on_error=lambda exc: state.update(outcome="error"),
            on_cancelled=lambda: state.update(outcome="cancelled"),
        )
        self.assertTrue(pump(self.root, until=lambda: "outcome" in state))
        self.assertEqual(state["outcome"], "cancelled",
                         "JobCancelled must not be reported as an error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
