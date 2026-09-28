from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from typing import Callable, Optional

from . import config

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
_configured = False


def setup(level: int = logging.INFO) -> logging.Logger:
    global _configured
    if _configured:
        return logging.getLogger(config.APP_NAME)

    config.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(level)

    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(stream)

    logging.getLogger("PIL").setLevel(logging.WARNING)
    _configured = True

    app_log = logging.getLogger(config.APP_NAME)
    app_log.info("--- %s starting ---", config.APP_NAME)
    return app_log


def install_excepthooks(report: Optional[Callable[[str, BaseException], None]] = None) -> None:
    log = logging.getLogger(config.APP_NAME)

    def _handle(context: str, exc: BaseException) -> None:
        log.error(
            "unhandled exception in %s:\n%s",
            context, "".join(traceback.format_exception(exc)),
        )
        if report is not None:
            try:
                report(context, exc)
            except Exception:
                log.exception("error reporter itself failed")

    def _sys_hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _handle("main thread", exc)

    def _thread_hook(args) -> None:
        if args.exc_value is None:
            return
        name = args.thread.name if args.thread else "?"
        _handle(f"thread {name}", args.exc_value)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


__all__ = ["setup", "install_excepthooks"]
