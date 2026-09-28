from __future__ import annotations

import os
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from ... import config, registry, theme
from ...core import organizer
from ..base import ToolPanel

CHUNK = 60

AGE_CHOICES = ("0", "1", "3", "7", "14", "30", "90")


def human_size(count: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            return f"{count:,.0f} {unit}" if unit == "B" else f"{count:,.1f} {unit}"
        count /= 1024
    return f"{count:.1f} TB"


def display_name(name: str) -> str:
    return name.replace("\u00a0", "\u2423").replace("\u200b", "\u2423")


class OrganizerPanel(ToolPanel):
    spec = registry.by_id("organizer")


    def build(self) -> None:
        self._plan: Optional[organizer.Plan] = None
        self._selected: set[str] = set()
        self._groups: dict[str, dict] = {}
        self._job = None
        self._busy = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_controls()
        self._build_body()
        self._build_actions()

        self._restore_settings()
        self.refresh_history()
        self._set_empty("Choose a folder and press Preview. Nothing moves until "
                        "you press Move.")

    def _build_controls(self) -> None:
        card = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=12)
        card.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="File organizer", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=19, weight="bold"),
        ).grid(row=0, column=0, columnspan=5, sticky="w", padx=18, pady=(14, 0))
        ctk.CTkLabel(
            card, anchor="w", text_color=theme.TEXT_DIM,
            text="Only the chosen folder itself is sorted -- never its subfolders.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
        ).grid(row=1, column=0, columnspan=5, sticky="w", padx=18, pady=(0, 10))

        ctk.CTkLabel(card, text="Folder", anchor="w", width=58,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
                     ).grid(row=2, column=0, sticky="w", padx=(18, 6))
        self._folder = ctk.CTkEntry(
            card, placeholder_text=r"C:\Users\...\Downloads", height=34,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12))
        self._folder.grid(row=2, column=1, sticky="ew", padx=(0, 8))

        ctk.CTkButton(card, text="Browse", width=84, height=34,
                      command=self._browse).grid(row=2, column=2, padx=(0, 6))
        ctk.CTkButton(card, text="Downloads", width=96, height=34,
                      fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
                      command=lambda: self._set_folder(config.DOWNLOADS_DIR),
                      ).grid(row=2, column=3, padx=(0, 6))
        ctk.CTkButton(card, text="Desktop", width=84, height=34,
                      fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
                      command=lambda: self._set_folder(config.DESKTOP_DIR),
                      ).grid(row=2, column=4, padx=(0, 18))

        options = ctk.CTkFrame(card, fg_color="transparent")
        options.grid(row=3, column=0, columnspan=5, sticky="ew", padx=18,
                     pady=(12, 14))

        ctk.CTkLabel(options, text="Leave alone anything newer than",
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
                     ).pack(side="left")
        self._age = ctk.CTkOptionMenu(options, values=list(AGE_CHOICES), width=72,
                                      height=30, command=lambda _: self._save())
        self._age.pack(side="left", padx=(8, 4))
        ctk.CTkLabel(options, text="days", text_color=theme.TEXT_DIM,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
                     ).pack(side="left", padx=(0, 20))

        self._dated = ctk.CTkCheckBox(
            options, text="Dated subfolders", command=self._save,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12))
        self._dated.pack(side="left", padx=(0, 16))
        self._keep_shortcuts = ctk.CTkCheckBox(
            options, text="Leave shortcuts where they are", command=self._save,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12))
        self._keep_shortcuts.pack(side="left", padx=(0, 16))
        self._hidden = ctk.CTkCheckBox(
            options, text="Include hidden files", command=self._save,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12))
        self._hidden.pack(side="left")

        self._preview_button = ctk.CTkButton(
            options, text="Preview", width=112, height=32,
            fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=13, weight="bold"),
            command=self.preview)
        self._preview_button.pack(side="right")

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=20)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color=theme.CARD, corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        header.grid_columnconfigure(0, weight=1)
        self._preview_title = ctk.CTkLabel(
            header, text="Preview", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=14, weight="bold"))
        self._preview_title.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(header, text="Tick all", width=72, height=26,
                      fg_color="transparent", hover_color=theme.HOVER,
                      text_color=theme.TEXT_DIM, border_width=1,
                      border_color=theme.BORDER,
                      command=lambda: self._select_all(True),
                      ).grid(row=0, column=1, padx=(6, 0))
        ctk.CTkButton(header, text="Untick all", width=78, height=26,
                      fg_color="transparent", hover_color=theme.HOVER,
                      text_color=theme.TEXT_DIM, border_width=1,
                      border_color=theme.BORDER,
                      command=lambda: self._select_all(False),
                      ).grid(row=0, column=2, padx=(6, 0))

        self._table = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._table.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self._table.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(body, fg_color=theme.CARD, corner_radius=12,
                             width=320)
        right.grid(row=0, column=1, sticky="ns")
        right.grid_propagate(False)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(right, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Previous runs", anchor="w",
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=14,
                                      weight="bold"),
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="Refresh", width=68, height=26,
                      fg_color="transparent", hover_color=theme.HOVER,
                      text_color=theme.TEXT_DIM, border_width=1,
                      border_color=theme.BORDER, command=self.refresh_history,
                      ).grid(row=0, column=1)

        self._history = ctk.CTkScrollableFrame(right, fg_color="transparent",
                                               width=286)
        self._history.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self._history.grid_columnconfigure(0, weight=1)

    def _build_actions(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=20, pady=(10, 16))
        bar.grid_columnconfigure(0, weight=1)

        self._summary = ctk.CTkLabel(
            bar, text="", anchor="w", text_color=theme.TEXT_DIM,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12))
        self._summary.grid(row=0, column=0, sticky="ew")

        self._progress = ctk.CTkProgressBar(bar, height=6, width=180)
        self._progress.set(0)
        self._progress.grid(row=0, column=1, padx=(10, 10))
        self._progress.grid_remove()

        self._cancel_button = ctk.CTkButton(
            bar, text="Stop", width=76, height=38,
            fg_color="transparent", border_width=1, border_color=theme.DANGER,
            text_color=theme.DANGER, hover_color=theme.HOVER,
            command=self._cancel)
        self._cancel_button.grid(row=0, column=2, padx=(0, 8))
        self._cancel_button.grid_remove()

        self._apply_button = ctk.CTkButton(
            bar, text="Move files", width=200, height=38,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=14, weight="bold"),
            state="disabled", command=self.apply_plan)
        self._apply_button.grid(row=0, column=3)


    def _restore_settings(self) -> None:
        folder = self.settings.get("folder", str(config.DOWNLOADS_DIR))
        self._folder.insert(0, folder)
        self._age.set(str(self.settings.get("min_age_days", "0")))
        if self.settings.get("dated_subfolders", False):
            self._dated.select()
        if self.settings.get("keep_shortcuts", True):
            self._keep_shortcuts.select()
        if self.settings.get("include_hidden", False):
            self._hidden.select()

    def _save(self) -> None:
        self.settings.update({
            "folder": self._folder.get().strip(),
            "min_age_days": self._age.get(),
            "dated_subfolders": bool(self._dated.get()),
            "keep_shortcuts": bool(self._keep_shortcuts.get()),
            "include_hidden": bool(self._hidden.get()),
        })

    def on_hide(self) -> None:
        self._save()

    def on_close(self) -> None:
        self._save()


    def _set_folder(self, path) -> None:
        self._folder.delete(0, "end")
        self._folder.insert(0, str(path))
        self._save()

    def _browse(self) -> None:
        current = self._folder.get().strip()
        chosen = filedialog.askdirectory(
            title="Folder to organize",
            initialdir=current if os.path.isdir(current) else str(config.DOWNLOADS_DIR))
        if chosen:
            self._set_folder(os.path.normpath(chosen))

    def _plan_options(self, *, allow_category_root: bool = False) -> organizer.Options:
        skip = frozenset({".lnk", ".url"}) if self._keep_shortcuts.get() else frozenset()
        rules = organizer.Ruleset(
            skip_extensions=skip,
            dated_subfolders=bool(self._dated.get()),
        )
        try:
            age = float(self._age.get())
        except ValueError:
            age = 0.0
        return organizer.Options(
            min_age_days=age,
            include_hidden=bool(self._hidden.get()),
            allow_category_root=allow_category_root,
            ruleset=rules,
        )


    def preview(self, *, allow_category_root: bool = False) -> None:
        if self._busy:
            return
        folder = self._folder.get().strip()
        if not folder:
            self.status("Choose a folder first.", tone="warning")
            return
        self._save()

        options = self._plan_options(allow_category_root=allow_category_root)
        self_paths = (str(config.SETTINGS_FILE), str(config.LOG_FILE))

        def work(ctx):
            return organizer.plan(
                folder, options,
                self_paths=self_paths,
                cancel=lambda: ctx.cancelled,
                progress=lambda done, total, name: ctx.progress(
                    done=done, total=total, name=name),
            )

        self._begin("Scanning...")
        self._job = self.run(work, name="organizer-plan",
                             on_progress=self._on_progress,
                             on_done=self._on_planned,
                             on_error=self._on_failed,
                             on_cancelled=self._on_stopped)

    def _on_planned(self, plan: organizer.Plan) -> None:
        self._end()
        self._plan = plan
        self._selected = {item.key for item in plan.items}
        self._render(plan)
        if plan.blocked:
            self.status(plan.blocking[0][1], tone="danger")
        elif plan.items:
            count, size = plan.totals()
            self.status(f"{count} files to sort ({human_size(size)}). "
                        "Nothing has moved.", tone="info")
        else:
            self.status("Nothing to sort -- this folder is already tidy.",
                        tone="success")


    def _clear_table(self) -> None:
        for child in self._table.winfo_children():
            child.destroy()
        self._groups.clear()

    def _set_empty(self, message: str) -> None:
        self._clear_table()
        ctk.CTkLabel(self._table, text=message, anchor="w", justify="left",
                     wraplength=520, text_color=theme.TEXT_DIM,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
                     ).grid(row=0, column=0, sticky="ew", padx=8, pady=14)

    def _render(self, plan: organizer.Plan) -> None:
        self._clear_table()
        row = 0

        for _, message in plan.blocking:
            row = self._add_notice(row, message, theme.DANGER)
        if any(kind is organizer.Block.CATEGORY_ROOT for kind, _ in plan.blocking):
            button = ctk.CTkButton(
                self._table, text="Sort it anyway", width=150, height=30,
                fg_color=theme.DANGER, hover_color="#e05555",
                command=lambda: self.preview(allow_category_root=True))
            button.grid(row=row, column=0, sticky="w", padx=8, pady=(0, 10))
            row += 1

        if not plan.items:
            if not plan.blocking:
                self._add_notice(row, "Nothing here needs sorting.", theme.SUCCESS)
                row += 1
        else:
            for category, items in plan.by_category().items():
                row = self._add_group(row, category, items)

        row = self._add_footnotes(row, plan)
        self._update_summary()
        self._preview_title.configure(
            text=f"Preview  --  {os.path.basename(plan.root) or plan.root}")

    def _add_notice(self, row: int, message: str, colour: str) -> int:
        ctk.CTkLabel(self._table, text=message, anchor="w", justify="left",
                     wraplength=520, text_color=colour,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
                     ).grid(row=row, column=0, sticky="ew", padx=8, pady=(8, 4))
        return row + 1

    def _add_group(self, row: int, category: str,
                   items: list[organizer.PlanItem]) -> int:
        size = sum(i.size for i in items)
        header = ctk.CTkFrame(self._table, fg_color=theme.CARD_ALT,
                              corner_radius=8)
        header.grid(row=row, column=0, sticky="ew", padx=4, pady=(6, 0))
        header.grid_columnconfigure(1, weight=1)

        toggle = ctk.CTkButton(
            header, text=f"\u25b8  {category}", width=180, height=30,
            anchor="w", fg_color="transparent", hover_color=theme.HOVER,
            text_color=theme.TEXT,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=13, weight="bold"),
            command=lambda c=category: self._toggle_group(c))
        toggle.grid(row=0, column=0, sticky="w", padx=(6, 0), pady=4)

        ctk.CTkLabel(header, anchor="w", text_color=theme.TEXT_DIM,
                     text=f"{len(items)} files  \u00b7  {human_size(size)}",
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
                     ).grid(row=0, column=1, sticky="w", padx=10)

        ctk.CTkButton(header, text="none", width=54, height=24,
                      fg_color="transparent", hover_color=theme.HOVER,
                      text_color=theme.TEXT_DIM,
                      command=lambda c=category: self._select_group(c, False),
                      ).grid(row=0, column=2, padx=(0, 2), pady=4)
        ctk.CTkButton(header, text="all", width=44, height=24,
                      fg_color="transparent", hover_color=theme.HOVER,
                      text_color=theme.TEXT_DIM,
                      command=lambda c=category: self._select_group(c, True),
                      ).grid(row=0, column=3, padx=(0, 6), pady=4)

        holder = ctk.CTkFrame(self._table, fg_color="transparent")
        holder.grid(row=row + 1, column=0, sticky="ew", padx=4)
        holder.grid_columnconfigure(1, weight=1)
        holder.grid_remove()

        self._groups[category] = {
            "items": items, "holder": holder, "toggle": toggle,
            "boxes": {}, "open": False, "built": 0,
        }
        return row + 2

    def _toggle_group(self, category: str) -> None:
        group = self._groups.get(category)
        if group is None:
            return
        if group["open"]:
            group["holder"].grid_remove()
            group["toggle"].configure(text=f"\u25b8  {category}")
            group["open"] = False
            return
        group["holder"].grid()
        group["toggle"].configure(text=f"\u25be  {category}")
        group["open"] = True
        if group["built"] < len(group["items"]):
            self._fill_group(category)

    def _fill_group(self, category: str) -> None:
        group = self._groups.get(category)
        if group is None or not group["open"]:
            return
        items = group["items"]
        start = group["built"]
        stop = min(start + CHUNK, len(items))

        for index in range(start, stop):
            self._add_row(group, index, items[index])
        group["built"] = stop

        if stop < len(items):
            self.after(1, lambda: self._fill_group(category))

    def _add_row(self, group: dict, index: int,
                 item: organizer.PlanItem) -> None:
        holder = group["holder"]
        tint = theme.ROW_A if index % 2 == 0 else theme.ROW_B
        row = ctk.CTkFrame(holder, fg_color=tint, corner_radius=6)
        row.grid(row=index, column=0, columnspan=2, sticky="ew", pady=1)
        row.grid_columnconfigure(0, weight=3)
        row.grid_columnconfigure(1, weight=2)

        box = ctk.CTkCheckBox(
            row, text=display_name(item.name), checkbox_width=18,
            checkbox_height=18,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
            command=lambda k=item.key: self._toggle_item(k))
        if item.key in self._selected:
            box.select()
        box.grid(row=0, column=0, sticky="w", padx=(10, 8), pady=5)
        group["boxes"][item.key] = box

        arrow = f"\u2192  {item.category}\\{display_name(item.dest_name)}"
        ctk.CTkLabel(row, text=arrow, anchor="w",
                     text_color=theme.WARNING if item.renamed else theme.TEXT_DIM,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
                     ).grid(row=0, column=1, sticky="w", padx=(0, 8))

        detail = human_size(item.size)
        if item.age_days >= 1:
            detail += f"  \u00b7  {item.age_days:.0f} d"
        if item.long_path:
            detail += "  \u00b7  very long path"
        ctk.CTkLabel(row, text=detail, anchor="e", width=140,
                     text_color=theme.MUTED,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
                     ).grid(row=0, column=2, sticky="e", padx=(0, 12))

    def _add_footnotes(self, row: int, plan: organizer.Plan) -> int:
        notes: list[str] = []
        if plan.subdirs:
            shown = ", ".join(plan.subdirs[:6])
            more = "..." if len(plan.subdirs) > 6 else ""
            notes.append(f"{len(plan.subdirs)} subfolders left untouched "
                         f"({shown}{more})")

        reasons: dict[str, int] = {}
        for skipped in plan.skipped:
            reasons[str(skipped.reason)] = reasons.get(str(skipped.reason), 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            notes.append(f"{count} skipped -- {reason}")

        if not notes:
            return row
        ctk.CTkLabel(
            self._table, text="\n".join(notes), anchor="w", justify="left",
            wraplength=520, text_color=theme.MUTED,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
        ).grid(row=row, column=0, sticky="ew", padx=10, pady=(14, 8))
        return row + 1


    def _toggle_item(self, key: str) -> None:
        if key in self._selected:
            self._selected.discard(key)
        else:
            self._selected.add(key)
        self._update_summary()

    def _select_group(self, category: str, wanted: bool) -> None:
        group = self._groups.get(category)
        if group is None:
            return
        for item in group["items"]:
            if wanted:
                self._selected.add(item.key)
            else:
                self._selected.discard(item.key)
        for box in group["boxes"].values():
            if wanted:
                box.select()
            else:
                box.deselect()
        self._update_summary()

    def _select_all(self, wanted: bool) -> None:
        if self._plan is None:
            return
        for category in self._groups:
            self._select_group(category, wanted)

    def _update_summary(self) -> None:
        if self._plan is None:
            self._summary.configure(text="")
            self._apply_button.configure(state="disabled", text="Move files")
            return
        total = len(self._plan.items)
        count, size = self._plan.totals(frozenset(self._selected))
        blocked = self._plan.blocked
        self._summary.configure(
            text=f"{count} of {total} files ticked  \u00b7  {human_size(size)}"
                 f"  \u00b7  into {self._plan.root}")
        self._apply_button.configure(
            state="normal" if count and not blocked and not self._busy else "disabled",
            text=f"Move {count} files" if count else "Move files")


    def apply_plan(self) -> None:
        if self._busy or self._plan is None or self._plan.blocked:
            return
        selection = frozenset(self._selected)
        count, size = self._plan.totals(selection)
        if not count:
            return
        if not messagebox.askyesno(
            "Move files",
            f"Move {count} files ({human_size(size)}) into category folders "
            f"inside\n{self._plan.root}?\n\n"
            "Every move is journalled, so this run can be undone from the "
            "Previous runs list.",
            parent=self,
        ):
            return

        plan = self._plan

        def work(ctx):
            return organizer.apply(
                plan, selection=selection,
                cancel=lambda: ctx.cancelled,
                progress=lambda done, total, name: ctx.progress(
                    done=done, total=total, name=name),
            )

        self._begin("Moving...")
        self._job = self.run(work, name="organizer-apply",
                             on_progress=self._on_progress,
                             on_done=self._on_applied,
                             on_error=self._on_failed,
                             on_cancelled=self._on_stopped)

    def _on_applied(self, result: organizer.ApplyResult) -> None:
        self._end()
        moved, failed = len(result.moved), len(result.failed)
        self.refresh_history()

        message = f"Moved {moved} files."
        tone = "success"
        if result.cancelled:
            message = f"Stopped after {moved} files. The run is still undoable."
            tone = "warning"
        if failed:
            reasons: dict[str, int] = {}
            for item in result.failed:
                label = str(item.reason or organizer.Fail.ERROR)
                reasons[label] = reasons.get(label, 0) + 1
            detail = ", ".join(f"{n} {r}" for r, n in reasons.items())
            message += f" {failed} left in place ({detail})."
            tone = "warning"
        self.status(message, tone=tone)

        self._plan = None
        self._selected.clear()
        self._update_summary()
        self._set_empty(message + "\n\nPress Preview to look again.")


    def refresh_history(self) -> None:
        for child in self._history.winfo_children():
            child.destroy()

        try:
            runs = organizer.list_journals()
        except OSError as exc:
            self.log.warning("could not list journals: %s", exc)
            runs = []

        if not runs:
            ctk.CTkLabel(
                self._history, text="No runs yet. Once you move files, each run "
                                    "is listed here and can be undone.",
                anchor="w", justify="left", wraplength=250,
                text_color=theme.MUTED,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=12)
            return

        for row, info in enumerate(runs[:40]):
            self._add_history_row(row, info)

    def _add_history_row(self, row: int, info: organizer.JournalInfo) -> None:
        card = ctk.CTkFrame(self._history, fg_color=theme.CARD_ALT,
                            corner_radius=8)
        card.grid(row=row, column=0, sticky="ew", padx=2, pady=3)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text=info.started_text, anchor="w",
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12,
                                      weight="bold"),
                     ).grid(row=0, column=0, sticky="w", padx=10, pady=(7, 0))

        state = ""
        if info.undone:
            state = "  \u00b7  undone"
        elif not info.complete:
            state = "  \u00b7  interrupted"
        ctk.CTkLabel(card, text=f"{info.moved} files{state}", anchor="w",
                     text_color=theme.MUTED if info.undone else theme.TEXT_DIM,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
                     ).grid(row=1, column=0, sticky="w", padx=10)
        ctk.CTkLabel(card, text=info.root or "?", anchor="w", wraplength=180,
                     text_color=theme.MUTED, justify="left",
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=10),
                     ).grid(row=2, column=0, sticky="w", padx=10, pady=(0, 8))

        button = ctk.CTkButton(
            card, text="Undone" if info.undone else "Undo", width=66, height=28,
            fg_color="transparent", border_width=1,
            border_color=theme.BORDER if info.undone else theme.SUCCESS,
            text_color=theme.MUTED if info.undone else theme.SUCCESS,
            hover_color=theme.HOVER,
            state="disabled" if info.undone else "normal",
            command=lambda i=info: self.undo_run(i))
        button.grid(row=0, column=1, rowspan=3, padx=(4, 10))

    def undo_run(self, info: organizer.JournalInfo) -> None:
        if self._busy:
            return
        if not messagebox.askyesno(
            "Undo this run",
            f"Put back the {info.moved} files moved on {info.started_text}?\n\n"
            "Anything you have edited or replaced since is left alone, and "
            "nothing is ever overwritten.",
            parent=self,
        ):
            return

        path = info.path

        def work(ctx):
            return organizer.undo(
                path,
                cancel=lambda: ctx.cancelled,
                progress=lambda done, total, name: ctx.progress(
                    done=done, total=total, name=name),
            )

        self._begin("Putting files back...")
        self._job = self.run(work, name="organizer-undo",
                             on_progress=self._on_progress,
                             on_done=self._on_undone,
                             on_error=self._on_failed,
                             on_cancelled=self._on_stopped)

    def _on_undone(self, result: organizer.UndoResult) -> None:
        self._end()
        self.refresh_history()
        if result.already_undone:
            self.status("That run was already undone.", tone="info")
            return
        message = f"Put back {result.restored_count} files."
        tone = "success"
        if result.blocked:
            reasons: dict[str, int] = {}
            for item in result.blocked:
                reasons[item.reason] = reasons.get(item.reason, 0) + 1
            detail = ", ".join(f"{n} {r}" for r, n in reasons.items())
            message += f" {len(result.blocked)} left alone ({detail})."
            tone = "warning"
        self.status(message, tone=tone)


    def _begin(self, message: str) -> None:
        self._busy = True
        self._preview_button.configure(state="disabled")
        self._apply_button.configure(state="disabled")
        self._progress.set(0)
        self._progress.grid()
        self._cancel_button.grid()
        self.status(message)

    def _end(self) -> None:
        self._busy = False
        self._job = None
        self._preview_button.configure(state="normal")
        self._progress.grid_remove()
        self._cancel_button.grid_remove()
        self._update_summary()

    def _cancel(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.status("Stopping...", tone="warning")

    def _on_progress(self, payload: dict) -> None:
        total = payload.get("total") or 0
        done = payload.get("done") or 0
        if total:
            self._progress.set(min(1.0, done / total))
        name = payload.get("name") or ""
        self._summary.configure(text=f"{done} / {total}   {display_name(name)}")

    def _on_failed(self, exc: BaseException) -> None:
        self._end()
        self.log.error("organizer job failed: %s", exc, exc_info=exc)
        self.status(str(exc) or exc.__class__.__name__, tone="danger")

    def _on_stopped(self) -> None:
        self._end()
        self.status("Stopped.", tone="warning")


__all__ = ["OrganizerPanel"]
