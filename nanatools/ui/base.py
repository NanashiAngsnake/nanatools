from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

import customtkinter as ctk

from ..jobs import JobHandle, JobRunner
from ..registry import ToolSpec
from ..settings import Namespace, SettingsStore


@dataclass
class Services:
    settings: SettingsStore
    jobs: JobRunner
    log: logging.Logger
    shell: Any = None

    def namespace(self, tool_id: str) -> Namespace:
        return self.settings.namespace(tool_id)


class ToolPanel(ctk.CTkFrame):
    spec: ToolSpec

    def __init__(self, master: Any, services: Services) -> None:
        super().__init__(master, fg_color="transparent")
        self.services = services
        self.settings: Namespace = services.namespace(self.spec.id)
        self.log = logging.getLogger(f"NanaTools.{self.spec.id}")
        self.build()


    def build(self) -> None:
        raise NotImplementedError

    def on_show(self) -> None:
        pass

    def on_hide(self) -> None:
        pass

    def on_close(self) -> None:
        pass


    def run(
        self,
        fn: Callable[[Any], Any],
        *,
        name: Optional[str] = None,
        on_progress: Optional[Callable[[dict], None]] = None,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
    ) -> JobHandle:
        return self.services.jobs.submit(
            fn,
            name=name or self.spec.id,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error or self._default_error,
            on_cancelled=on_cancelled,
        )

    def _default_error(self, exc: BaseException) -> None:
        self.log.error("%s: unhandled job error: %s", self.spec.id, exc)
        self.status(f"Error: {exc}", tone="danger")

    def status(self, message: str, *, tone: str = "info") -> None:
        shell = self.services.shell
        if shell is not None and hasattr(shell, "set_status"):
            shell.set_status(message, tone=tone)
        else:
            self.log.info("status: %s", message)


__all__ = ["ToolPanel", "Services"]
