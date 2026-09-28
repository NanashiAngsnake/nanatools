from __future__ import annotations

import logging
import sys
import traceback
from typing import Optional

import customtkinter as ctk

from .. import config, registry, theme
from ..jobs import JobRunner
from ..registry import ToolSpec
from ..settings import SettingsStore
from ..version import __version__
from .base import Services, ToolPanel

log = logging.getLogger(config.APP_NAME)

_TONES = {
    "info": theme.TEXT_DIM,
    "success": theme.SUCCESS,
    "warning": theme.WARNING,
    "danger": theme.DANGER,
}


class BrokenToolPanel(ctk.CTkFrame):
    def __init__(self, master, spec: ToolSpec, exc: BaseException) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self, text=f"{spec.name} could not start",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=20, weight="bold"),
            text_color=theme.DANGER, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 4))

        hint = str(exc) or exc.__class__.__name__
        if isinstance(exc, ModuleNotFoundError) and exc.name:
            hint = (f"The Python package '{exc.name}' is not installed.\n\n"
                    f"Install it with:\n    pip install {exc.name}\n\n"
                    "The other tools keep working in the meantime.")
        ctk.CTkLabel(
            self, text=hint, justify="left", anchor="nw",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=13),
            text_color=theme.TEXT_DIM, wraplength=680,
        ).grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))

        details = ctk.CTkTextbox(
            self, font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11), wrap="none",
        )
        details.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 24))
        details.insert("1.0", "".join(traceback.format_exception(exc)))
        details.configure(state="disabled")


class Shell(ctk.CTk):
    def __init__(self, settings: SettingsStore) -> None:
        super().__init__()

        self.settings = settings
        self._ui = settings.namespace("shell")
        self._panels: dict[str, ctk.CTkFrame] = {}
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._current: Optional[str] = None
        self._status_after: Optional[str] = None
        self._closing = False

        self.title(f"{config.APP_NAME} {__version__}")
        self.minsize(940, 600)
        self.geometry(self._ui.get("geometry", "1180x740"))
        self._set_icon()

        self.jobs = JobRunner(self, poll_ms=60)
        self.services = Services(
            settings=settings, jobs=self.jobs,
            log=logging.getLogger(config.APP_NAME), shell=self,
        )

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.report_callback_exception = self._on_tk_exception

        first = self._ui.get("last_tool") or registry.ordered()[0].id
        self.show_tool(first if registry.by_id(first) else registry.ordered()[0].id)


    def _set_icon(self) -> None:
        icon = config.ASSETS_DIR / "nanatools.ico"
        if icon.exists():
            try:
                self.iconbitmap(default=str(icon))
            except Exception as exc:
                log.debug("could not set window icon: %s", exc)
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    f"Nana.{config.APP_NAME}.{__version__}"
                )
            except Exception as exc:
                log.debug("could not set AppUserModelID: %s", exc)

    def _build(self) -> None:
        self.configure(fg_color=theme.WINDOW)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=232, corner_radius=0, fg_color=theme.SIDEBAR)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(99, weight=1)

        ctk.CTkLabel(
            sidebar, text=config.APP_NAME, anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=21, weight="bold"),
            text_color=theme.ACCENT,
        ).grid(row=0, column=0, sticky="ew", padx=20, pady=(22, 2))
        ctk.CTkLabel(
            sidebar, text=f"version {__version__}", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            text_color=theme.MUTED,
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))

        for index, spec in enumerate(registry.ordered(), start=2):
            button = ctk.CTkButton(
                sidebar,
                text=f"  {spec.glyph}   {spec.name}",
                anchor="w", height=42, corner_radius=8,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=13),
                fg_color="transparent", hover_color=theme.HOVER,
                text_color=theme.TEXT_DIM,
                command=lambda s=spec: self.show_tool(s.id),
            )
            button.grid(row=index, column=0, sticky="ew", padx=12, pady=3)
            self._buttons[spec.id] = button

        ctk.CTkLabel(
            sidebar, text="Logs: logs\\nanatools.log", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=10),
            text_color=theme.MUTED,
        ).grid(row=100, column=0, sticky="ew", padx=20, pady=(0, 14))

        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        bar = ctk.CTkFrame(self, height=30, corner_radius=0, fg_color=theme.SIDEBAR)
        bar.grid(row=1, column=1, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        self._status = ctk.CTkLabel(
            bar, text="Ready", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            text_color=theme.TEXT_DIM,
        )
        self._status.grid(row=0, column=0, sticky="ew", padx=18)


    def show_tool(self, tool_id: str) -> None:
        if tool_id == self._current:
            return
        spec = registry.by_id(tool_id)
        if spec is None:
            log.warning("no such tool: %s", tool_id)
            return

        previous = self._panels.get(self._current or "")
        if isinstance(previous, ToolPanel):
            try:
                previous.on_hide()
            except Exception:
                log.exception("%s.on_hide failed", self._current)
        if previous is not None:
            previous.grid_remove()

        panel = self._panels.get(tool_id) or self._create_panel(spec)
        panel.grid(row=0, column=0, sticky="nsew")
        if isinstance(panel, ToolPanel):
            try:
                panel.on_show()
            except Exception:
                log.exception("%s.on_show failed", tool_id)

        self._current = tool_id
        self._ui.set("last_tool", tool_id)
        self._highlight(tool_id)
        self.set_status(spec.summary)

    def _create_panel(self, spec: ToolSpec) -> ctk.CTkFrame:
        try:
            panel_cls = spec.load()
            panel = panel_cls(self._content, self.services)
        except BaseException as exc:
            log.error("could not build panel %s: %s", spec.id, exc, exc_info=True)
            panel = BrokenToolPanel(self._content, spec, exc)
        self._panels[spec.id] = panel
        return panel

    def _highlight(self, tool_id: str) -> None:
        for other, button in self._buttons.items():
            selected = other == tool_id
            button.configure(
                fg_color=theme.ACCENT_SOFT if selected else "transparent",
                text_color=theme.TEXT if selected else theme.TEXT_DIM,
            )


    def set_status(self, message: str, *, tone: str = "info", clear_after: int = 0) -> None:
        try:
            self._status.configure(text=message, text_color=_TONES.get(tone, theme.TEXT_DIM))
        except Exception:
            return
        if self._status_after is not None:
            try:
                self.after_cancel(self._status_after)
            except Exception:
                pass
            self._status_after = None
        if clear_after > 0:
            self._status_after = self.after(clear_after, lambda: self.set_status("Ready"))


    def _on_tk_exception(self, exc_type, exc, tb) -> None:
        log.error("unhandled exception in a Tk callback:\n%s",
                  "".join(traceback.format_exception(exc_type, exc, tb)))
        self.set_status(f"Something went wrong: {exc}. See logs\\nanatools.log",
                        tone="danger")

    def report_background_error(self, context: str, exc: BaseException) -> None:
        try:
            self.after(0, lambda: self.set_status(
                f"Error in {context}: {exc}. See logs\\nanatools.log", tone="danger"))
        except Exception:
            pass


    def close(self) -> None:
        if self._closing:
            return
        self._closing = True

        log.info("shutting down")
        try:
            self._ui.set("geometry", self.winfo_geometry())
        except Exception:
            pass
        for tool_id, panel in self._panels.items():
            if isinstance(panel, ToolPanel):
                try:
                    panel.on_close()
                except Exception:
                    log.exception("%s.on_close failed", tool_id)
        self.jobs.shutdown()
        self.settings.save()
        self._cancel_pending_after_callbacks()
        try:
            self.destroy()
        except Exception as exc:
            log.debug("window was already gone at destroy time: %s", exc)

    def _cancel_pending_after_callbacks(self) -> None:
        try:
            pending = self.tk.splitlist(self.tk.call("after", "info"))
        except Exception:
            return
        for after_id in pending:
            try:
                self.tk.call("after", "cancel", after_id)
            except Exception:
                pass


def run() -> int:
    config.ensure_dirs()
    settings = SettingsStore(config.SETTINGS_FILE)
    theme.apply(settings.get("shell", "appearance", "dark"))

    shell = Shell(settings)
    from .. import logging_setup

    logging_setup.install_excepthooks(shell.report_background_error)
    shell.mainloop()
    return 0


__all__ = ["Shell", "run", "BrokenToolPanel"]
