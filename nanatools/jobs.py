from __future__ import annotations

import itertools
import logging
import queue
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_PROGRESS = "progress"
_DONE = "done"
_ERROR = "error"
_CANCELLED = "cancelled"


class JobCancelled(Exception):
    pass


class CancelToken:
    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise JobCancelled()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


class JobContext:
    __slots__ = ("job_id", "token", "_emit")

    def __init__(self, job_id: int, token: CancelToken, emit: Callable[[tuple], None]) -> None:
        self.job_id = job_id
        self.token = token
        self._emit = emit

    def progress(self, **payload: Any) -> None:
        self._emit((_PROGRESS, self.job_id, payload))

    @property
    def cancelled(self) -> bool:
        return self.token.cancelled

    def check_cancelled(self) -> None:
        self.token.check()


@dataclass
class JobHandle:
    id: int
    name: str
    token: CancelToken = field(default_factory=CancelToken)
    finished: bool = False

    def cancel(self) -> None:
        self.token.cancel()

    @property
    def cancelled(self) -> bool:
        return self.token.cancelled


class JobRunner:
    def __init__(self, widget: Any, *, poll_ms: int = 60) -> None:
        self._widget = widget
        self._poll_ms = max(10, int(poll_ms))
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._ids = itertools.count(1)
        self._callbacks: dict[int, dict] = {}
        self._handles: dict[int, JobHandle] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._after_id: Optional[str] = None
        self._schedule()


    def submit(
        self,
        fn: Callable[[JobContext], Any],
        *,
        name: str = "job",
        on_progress: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
    ) -> JobHandle:
        if self._closed:
            raise RuntimeError("JobRunner is shut down")

        job_id = next(self._ids)
        handle = JobHandle(id=job_id, name=name)
        with self._lock:
            self._handles[job_id] = handle
            self._callbacks[job_id] = {
                _PROGRESS: on_progress,
                _DONE: on_done,
                _ERROR: on_error,
                _CANCELLED: on_cancelled,
            }

        ctx = JobContext(job_id, handle.token, self._queue.put)
        thread = threading.Thread(
            target=self._run,
            args=(fn, ctx),
            name=f"nanatools-{name}-{job_id}",
            daemon=True,
        )
        thread.start()
        return handle

    def cancel_all(self) -> None:
        with self._lock:
            handles = list(self._handles.values())
        for handle in handles:
            handle.cancel()

    def shutdown(self) -> None:
        self.cancel_all()
        self._closed = True
        after_id, self._after_id = self._after_id, None
        if after_id is not None:
            try:
                self._widget.after_cancel(after_id)
            except Exception:
                pass

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._handles)


    def _run(self, fn: Callable[[JobContext], Any], ctx: JobContext) -> None:
        try:
            result = fn(ctx)
        except JobCancelled:
            self._queue.put((_CANCELLED, ctx.job_id, None))
        except BaseException as exc:
            log.error(
                "job %s failed: %s\n%s",
                ctx.job_id, exc, "".join(traceback.format_exception(exc)),
            )
            self._queue.put((_ERROR, ctx.job_id, exc))
        else:
            if ctx.token.cancelled:
                self._queue.put((_CANCELLED, ctx.job_id, None))
            else:
                self._queue.put((_DONE, ctx.job_id, result))


    def _schedule(self) -> None:
        if self._closed:
            return
        try:
            self._after_id = self._widget.after(self._poll_ms, self._pump)
        except Exception:
            self._closed = True

    def _pump(self) -> None:
        try:
            while True:
                try:
                    kind, job_id, payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._dispatch(kind, job_id, payload)
        finally:
            self._schedule()

    def _dispatch(self, kind: str, job_id: int, payload: Any) -> None:
        with self._lock:
            callbacks = self._callbacks.get(job_id)
            if kind != _PROGRESS:
                self._callbacks.pop(job_id, None)
                handle = self._handles.pop(job_id, None)
                if handle is not None:
                    handle.finished = True
        if not callbacks:
            return
        callback = callbacks.get(kind)
        if callback is None:
            if kind == _ERROR:
                log.error("job %s errored with no handler: %s", job_id, payload)
            return
        try:
            if kind == _PROGRESS:
                callback(payload)
            elif kind == _CANCELLED:
                callback()
            else:
                callback(payload)
        except Exception:
            log.exception("job %s callback (%s) raised", job_id, kind)


__all__ = ["JobRunner", "JobHandle", "JobContext", "CancelToken", "JobCancelled"]
