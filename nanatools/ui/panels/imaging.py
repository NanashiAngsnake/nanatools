from __future__ import annotations

import os
from pathlib import Path
from tkinter import filedialog
from typing import Any, Optional

import customtkinter as ctk

from ... import theme
from ..base import ToolPanel
from ... import registry

try:
    from ...core import imaging as core
    from PIL import Image
    IMPORT_ERROR: Optional[BaseException] = None
except BaseException as exc:
    core = None
    Image = None
    IMPORT_ERROR = exc

MAX_TABLE_ROWS = 300

_RESIZE_LABELS = {
    "Keep original size": "none",
    "Fit inside width x height": "fit",
    "Longest edge": "long_edge",
    "Percentage": "percent",
    "Exact size (may distort)": "exact",
}
_METADATA_LABELS = {"Keep": "keep", "Colour only": "colour", "Strip": "strip"}
_ANIMATION_LABELS = {"Preserve": "preserve", "First frame": "first", "Skip": "skip"}
_COLLISION_LABELS = {"Rename": "rename", "Skip": "skip", "Overwrite": "overwrite"}


def _invert(mapping: dict) -> dict:
    return {v: k for k, v in mapping.items()}


def _parse_hex(text: str, fallback=(255, 255, 255)) -> tuple[int, int, int]:
    raw = text.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if len(raw) != 6:
        return fallback
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return fallback


class ImagingPanel(ToolPanel):
    spec = registry.by_id("imaging")


    def build(self) -> None:
        self._sources: list[Path] = []
        self._job = None
        self._rows = 0
        self._preview_image = None

        if IMPORT_ERROR is not None:
            self._build_unavailable()
            return

        core.set_pixel_limit(250_000_000)

        self._settings = core.ConvertSettings.from_dict(
            self.settings.get("convert", {}) or {})

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_options()
        self._build_right()
        self._build_actions()
        self._sync_format_controls()
        self._refresh_source_summary()

    def _build_unavailable(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text="Image converter unavailable", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=20, weight="bold"),
            text_color=theme.DANGER,
        ).grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 6))
        ctk.CTkLabel(
            self, justify="left", anchor="nw", wraplength=620,
            text=("This tool needs Pillow, which could not be loaded:\n\n"
                  f"    {type(IMPORT_ERROR).__name__}: {IMPORT_ERROR}\n\n"
                  "Install or repair it with:\n    pip install --upgrade Pillow\n\n"
                  "The other tools are unaffected."),
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=13),
            text_color=theme.TEXT_DIM,
        ).grid(row=1, column=0, sticky="ew", padx=24)

    def _build_header(self) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=24, pady=(20, 10))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="Image converter", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, anchor="w", text_color=theme.TEXT_DIM,
            text="Convert, resize and re-encode a batch. Originals are never touched.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))


    def _build_options(self) -> None:
        box = ctk.CTkScrollableFrame(self, width=356, fg_color=theme.CARD,
                                     corner_radius=10)
        box.grid(row=1, column=0, sticky="nsw", padx=(24, 12), pady=(0, 10))
        box.grid_columnconfigure(0, weight=1)
        self._options_box = box
        row = 0

        row = self._section(box, row, "Output")

        formats = core.available_formats()
        current = self._settings.target_format
        if current not in formats:
            current = formats[0]
        self._format_var = ctk.StringVar(value=current)
        self._format_menu = ctk.CTkOptionMenu(
            box, values=formats, variable=self._format_var,
            command=lambda _: self._on_format_change(),
        )
        self._format_menu.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 4))
        row += 1

        self._format_note = ctk.CTkLabel(
            box, text="", anchor="w", justify="left", wraplength=310,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            text_color=theme.TEXT_DIM,
        )
        self._format_note.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        row += 1

        dest = ctk.CTkFrame(box, fg_color="transparent")
        dest.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 10))
        dest.grid_columnconfigure(0, weight=1)
        self._dest_entry = ctk.CTkEntry(
            dest, placeholder_text="<source folder>\\converted")
        self._dest_entry.grid(row=0, column=0, sticky="ew")
        if self._settings.dest_dir:
            self._dest_entry.insert(0, str(self._settings.dest_dir))
        ctk.CTkButton(dest, text="...", width=38, command=self._pick_dest,
                      fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER
                      ).grid(row=0, column=1, padx=(6, 0))
        row += 1

        self._quality_head_row = row
        row = self._section(box, row, "Quality")

        self._quality_label = ctk.CTkLabel(
            box, anchor="w", text_color=theme.TEXT_DIM,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11))
        self._quality_label.grid(row=row, column=0, sticky="ew", padx=14)
        self._quality_label_row = row
        row += 1

        self._quality = ctk.CTkSlider(box, from_=1, to=100, number_of_steps=99,
                                      command=self._on_quality)
        self._quality.set(self._settings.quality)
        self._quality.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        self._quality_row = row
        row += 1

        self._lossless_var = ctk.BooleanVar(value=self._settings.webp_lossless)
        self._lossless = ctk.CTkCheckBox(
            box, text="Lossless (WEBP only)", variable=self._lossless_var,
            command=self._on_quality,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12))
        self._lossless.grid(row=row, column=0, sticky="w", padx=14, pady=(0, 10))
        self._lossless_row = row
        row += 1

        row = self._section(box, row, "Resize")
        self._resize_var = ctk.StringVar(
            value=_invert(_RESIZE_LABELS)[self._settings.resize_mode.value])
        ctk.CTkOptionMenu(box, values=list(_RESIZE_LABELS), variable=self._resize_var,
                          command=lambda _: self._sync_resize_controls()
                          ).grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 6))
        row += 1

        self._dims = ctk.CTkFrame(box, fg_color="transparent")
        self._dims.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 6))
        self._dims.grid_columnconfigure((1, 3), weight=1)
        self._dims_row = row
        row += 1
        self._w_entry = self._labelled_entry(self._dims, 0, "W", self._settings.max_w)
        self._h_entry = self._labelled_entry(self._dims, 2, "H", self._settings.max_h)

        self._single = ctk.CTkFrame(box, fg_color="transparent")
        self._single.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 6))
        self._single.grid_columnconfigure(1, weight=1)
        self._single_row = row
        row += 1
        self._single_label = ctk.CTkLabel(
            self._single, text="px", width=26, anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
            text_color=theme.TEXT_DIM)
        self._single_label.grid(row=0, column=0, sticky="w")
        self._single_entry = ctk.CTkEntry(self._single, width=90)
        self._single_entry.insert(0, str(self._settings.long_edge))
        self._single_entry.grid(row=0, column=1, sticky="w")

        self._upscale_var = ctk.BooleanVar(value=self._settings.allow_upscale)
        ctk.CTkCheckBox(box, text="Allow enlarging", variable=self._upscale_var,
                        font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12)
                        ).grid(row=row, column=0, sticky="w", padx=14, pady=(0, 4))
        row += 1

        self._resample_var = ctk.StringVar(value=self._settings.resample)
        ctk.CTkOptionMenu(box, values=list(core.RESAMPLERS), variable=self._resample_var
                          ).grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 10))
        row += 1

        row = self._section(box, row, "Handling")

        row = self._caption(box, row, "Metadata (EXIF, colour profile)")
        self._metadata_var = ctk.StringVar(
            value=_invert(_METADATA_LABELS)[self._settings.metadata.value])
        ctk.CTkSegmentedButton(box, values=list(_METADATA_LABELS),
                               variable=self._metadata_var
                               ).grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        row += 1

        row = self._caption(box, row, "Animated images")
        self._animation_var = ctk.StringVar(
            value=_invert(_ANIMATION_LABELS)[self._settings.animation.value])
        ctk.CTkSegmentedButton(box, values=list(_ANIMATION_LABELS),
                               variable=self._animation_var
                               ).grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        row += 1

        bg = ctk.CTkFrame(box, fg_color="transparent")
        bg.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        bg.grid_columnconfigure(1, weight=1)
        row += 1
        ctk.CTkLabel(bg, text="Flatten onto", anchor="w", text_color=theme.TEXT_DIM,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12)
                     ).grid(row=0, column=0, sticky="w")
        self._bg_entry = ctk.CTkEntry(bg, width=90)
        self._bg_entry.insert(0, "#%02x%02x%02x" % tuple(self._settings.flatten_bg))
        self._bg_entry.grid(row=0, column=1, sticky="e")

        row = self._caption(box, row, "If the output name is taken")
        self._collision_var = ctk.StringVar(
            value=_invert(_COLLISION_LABELS)[self._settings.on_collision.value])
        ctk.CTkSegmentedButton(box, values=list(_COLLISION_LABELS),
                               variable=self._collision_var
                               ).grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 8))
        row += 1

        self._downcast_var = ctk.BooleanVar(
            value=self._settings.high_depth is core.HighDepthPolicy.DOWNCAST)
        ctk.CTkCheckBox(
            box, text="Allow 16-bit down-casting", variable=self._downcast_var,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
        ).grid(row=row, column=0, sticky="w", padx=14, pady=(0, 2))
        row += 1
        ctk.CTkLabel(
            box, anchor="w", justify="left", wraplength=310,
            text="Off, a 16-bit scan is refused rather than clipped to white "
                 "by a format that cannot hold it.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            text_color=theme.MUTED,
        ).grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 10))
        row += 1

        workers = ctk.CTkFrame(box, fg_color="transparent")
        workers.grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 14))
        workers.grid_columnconfigure(1, weight=1)
        row += 1
        ctk.CTkLabel(workers, text="Workers", anchor="w", text_color=theme.TEXT_DIM,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12)
                     ).grid(row=0, column=0, sticky="w")
        self._workers_var = ctk.StringVar(
            value="Auto" if self._settings.workers <= 0 else str(self._settings.workers))
        ctk.CTkOptionMenu(workers, width=100,
                          values=["Auto"] + [str(n) for n in (1, 2, 4, 6, 8, 12)],
                          variable=self._workers_var
                          ).grid(row=0, column=1, sticky="e")

    def _section(self, parent, row: int, title: str) -> int:
        ctk.CTkLabel(
            parent, text=title.upper(), anchor="w", text_color=theme.MUTED,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11, weight="bold"),
        ).grid(row=row, column=0, sticky="ew", padx=14, pady=(12, 6))
        return row + 1

    def _caption(self, parent, row: int, text: str) -> int:
        ctk.CTkLabel(
            parent, text=text, anchor="w", text_color=theme.TEXT_DIM,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
        ).grid(row=row, column=0, sticky="ew", padx=14, pady=(0, 3))
        return row + 1

    def _labelled_entry(self, parent, column: int, label: str, value: int):
        ctk.CTkLabel(parent, text=label, width=18, anchor="w",
                     text_color=theme.TEXT_DIM,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12)
                     ).grid(row=0, column=column, sticky="w")
        entry = ctk.CTkEntry(parent, width=76)
        entry.insert(0, str(value))
        entry.grid(row=0, column=column + 1, sticky="w", padx=(0, 10))
        return entry


    def _build_right(self) -> None:
        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(0, 24), pady=(0, 10))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        src = ctk.CTkFrame(right, fg_color=theme.CARD, corner_radius=10)
        src.grid(row=0, column=0, sticky="ew")
        src.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(src, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        bar.grid_columnconfigure(3, weight=1)
        ctk.CTkButton(bar, text="Add files", width=92, command=self._pick_files
                      ).grid(row=0, column=0)
        ctk.CTkButton(bar, text="Add folder", width=100, command=self._pick_folder,
                      fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER
                      ).grid(row=0, column=1, padx=6)
        ctk.CTkButton(bar, text="Clear", width=64, command=self._clear_sources,
                      fg_color="transparent", hover_color=theme.HOVER,
                      text_color=theme.TEXT_DIM, border_width=1,
                      border_color=theme.BORDER).grid(row=0, column=2)
        self._recursive_var = ctk.BooleanVar(
            value=bool(self.settings.get("recursive", False)))
        ctk.CTkCheckBox(bar, text="Include subfolders", variable=self._recursive_var,
                        font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12)
                        ).grid(row=0, column=3, sticky="e")

        filt = ctk.CTkFrame(src, fg_color="transparent")
        filt.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))
        filt.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(filt, text="Only", width=34, anchor="w",
                     text_color=theme.TEXT_DIM,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12)
                     ).grid(row=0, column=0, sticky="w")
        self._filter_entry = ctk.CTkEntry(
            filt, placeholder_text="jpg png webp -- blank means every image type")
        self._filter_entry.insert(0, self.settings.get("extensions", "") or "")
        self._filter_entry.grid(row=0, column=1, sticky="ew")

        ctk.CTkLabel(src, anchor="w", text_color=theme.MUTED,
                     text="Or paste paths, one per line:",
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11)
                     ).grid(row=2, column=0, sticky="ew", padx=14)
        self._paste = ctk.CTkTextbox(
            src, height=52, wrap="none",
            font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11))
        self._paste.grid(row=3, column=0, sticky="ew", padx=14, pady=(2, 6))
        ctk.CTkButton(src, text="Add pasted paths", height=26, width=140,
                      command=self._add_pasted, fg_color=theme.ACCENT_SOFT,
                      hover_color=theme.ACCENT_SOFT_HOVER,
                      font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12)
                      ).grid(row=4, column=0, sticky="w", padx=14, pady=(0, 12))

        prev = ctk.CTkFrame(right, fg_color=theme.CARD, corner_radius=10, height=118)
        prev.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        prev.grid_columnconfigure(1, weight=1)
        prev.grid_propagate(False)
        self._preview = ctk.CTkLabel(prev, text="", width=150, height=94)
        self._preview.grid(row=0, column=0, padx=(12, 10), pady=12)
        self._summary = ctk.CTkLabel(
            prev, text="No files selected.", anchor="nw", justify="left",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
            text_color=theme.TEXT_DIM)
        self._summary.grid(row=0, column=1, sticky="nsew", pady=12, padx=(0, 12))

        self._table = ctk.CTkScrollableFrame(right, fg_color=theme.CARD,
                                             corner_radius=10, label_text="Results")
        self._table.grid(row=2, column=0, sticky="nsew")
        self._table.grid_columnconfigure(0, weight=1)
        self._empty = ctk.CTkLabel(
            self._table, text="Nothing converted yet.", text_color=theme.MUTED,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12))
        self._empty.grid(row=0, column=0, pady=18)

    def _build_actions(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=24, pady=(0, 18))
        bar.grid_columnconfigure(2, weight=1)

        self._convert_btn = ctk.CTkButton(bar, text="Convert", width=130, height=36,
                                          command=self._start)
        self._convert_btn.grid(row=0, column=0)
        self._cancel_btn = ctk.CTkButton(
            bar, text="Cancel", width=90, height=36, command=self._cancel,
            state="disabled", fg_color="transparent", hover_color=theme.HOVER,
            text_color=theme.TEXT_DIM, border_width=1, border_color=theme.BORDER)
        self._cancel_btn.grid(row=0, column=1, padx=8)

        self._progress = ctk.CTkProgressBar(bar, height=8)
        self._progress.set(0)
        self._progress.grid(row=0, column=2, sticky="ew", padx=14)

        self._totals = ctk.CTkLabel(
            bar, text="", anchor="e",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
            text_color=theme.TEXT_DIM)
        self._totals.grid(row=0, column=3, sticky="e")


    def _on_format_change(self) -> None:
        self._sync_format_controls()

    def _on_quality(self, _value: Any = None) -> None:
        self._quality_label.configure(text=self._quality_text())

    def _quality_text(self) -> str:
        fmt = self._format_var.get()
        value = int(self._quality.get())
        if fmt == "PNG":
            return "PNG is lossless; the slider does not apply."
        if fmt == "WEBP" and self._lossless_var.get():
            return "Lossless WEBP: every pixel identical to the source."
        return f"Quality {value}"

    def _sync_format_controls(self) -> None:
        fmt = self._format_var.get()
        lossy = fmt in ("JPEG", "WEBP", "AVIF")
        for widget, row in ((self._quality_label, self._quality_label_row),
                            (self._quality, self._quality_row)):
            if lossy:
                widget.grid()
            else:
                widget.grid_remove()
        if fmt == "WEBP":
            self._lossless.grid()
        else:
            self._lossless.grid_remove()

        notes = {
            "JPEG": "No transparency: transparent pixels are composited onto the "
                    "background colour below.",
            "PNG": "Lossless, keeps transparency, and one of the two formats that "
                   "can hold 16-bit data.",
            "WEBP": "Small and keeps transparency. Tick lossless for an exact copy. "
                    "No side may exceed 16383 px.",
            "AVIF": "Smallest at a given quality, but has no true lossless mode -- "
                    "even quality 100 alters pixels. Use PNG or lossless WEBP for "
                    "an exact copy.",
            "BMP": "Uncompressed. Large files, no options.",
            "TIFF": "Lossless, keeps 16-bit data and unusual colour modes.",
            "GIF": "256 colours per frame. Fine for animation, poor for photos.",
            "ICO": "Icon sizes larger than the source are dropped automatically.",
        }
        self._format_note.configure(text=notes.get(fmt, ""))
        self._sync_resize_controls()
        self._on_quality()

    def _sync_resize_controls(self) -> None:
        mode = _RESIZE_LABELS[self._resize_var.get()]
        if mode == "fit" or mode == "exact":
            self._dims.grid()
            self._single.grid_remove()
        elif mode in ("long_edge", "percent"):
            self._dims.grid_remove()
            self._single.grid()
            self._single_label.configure(text="px" if mode == "long_edge" else "%")
        else:
            self._dims.grid_remove()
            self._single.grid_remove()

    def _read_int(self, entry, fallback: int) -> int:
        try:
            return max(1, int(float(entry.get().strip())))
        except (TypeError, ValueError):
            return fallback

    def _collect_settings(self) -> "core.ConvertSettings":
        mode = core.ResizeMode(_RESIZE_LABELS[self._resize_var.get()])
        single = self._read_int(self._single_entry, 1920)
        dest = self._dest_entry.get().strip()
        workers = self._workers_var.get()
        return core.ConvertSettings(
            target_format=self._format_var.get(),
            dest_dir=Path(dest) if dest else None,
            resize_mode=mode,
            max_w=self._read_int(self._w_entry, 1920),
            max_h=self._read_int(self._h_entry, 1920),
            long_edge=single,
            percent=float(single) if mode is core.ResizeMode.PERCENT else 100.0,
            allow_upscale=bool(self._upscale_var.get()),
            resample=self._resample_var.get(),
            quality=int(self._quality.get()),
            webp_lossless=bool(self._lossless_var.get()),
            metadata=core.MetadataPolicy(_METADATA_LABELS[self._metadata_var.get()]),
            flatten_bg=_parse_hex(self._bg_entry.get()),
            animation=core.AnimationMode(_ANIMATION_LABELS[self._animation_var.get()]),
            on_collision=core.OnCollision(_COLLISION_LABELS[self._collision_var.get()]),
            high_depth=(core.HighDepthPolicy.DOWNCAST if self._downcast_var.get()
                        else core.HighDepthPolicy.BLOCK),
            workers=0 if workers == "Auto" else int(workers),
        )


    def _extensions(self) -> Optional[list[str]]:
        raw = self._filter_entry.get().replace(",", " ").split()
        return raw or None

    def _add(self, paths) -> None:
        found = core.discover(paths, recursive=bool(self._recursive_var.get()),
                              extensions=self._extensions())
        known = {str(p).lower() for p in self._sources}
        added = [p for p in found if str(p).lower() not in known]
        self._sources.extend(added)
        self._refresh_source_summary()
        if not added:
            self.status("Nothing new to add -- no matching images there.",
                        tone="warning")
        else:
            self.status(f"Added {len(added)} image(s).", tone="success")

    def _pick_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose images",
            initialdir=self.settings.get("last_dir", "") or None,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.avif *.bmp *.tif "
                                  "*.tiff *.gif *.ico"), ("All files", "*.*")])
        if paths:
            self.settings.set("last_dir", str(Path(paths[0]).parent))
            self._add([Path(p) for p in paths])

    def _pick_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Choose a folder of images",
            initialdir=self.settings.get("last_dir", "") or None)
        if folder:
            self.settings.set("last_dir", folder)
            self._add([Path(folder)])

    def _pick_dest(self) -> None:
        folder = filedialog.askdirectory(title="Choose the output folder")
        if folder:
            self._dest_entry.delete(0, "end")
            self._dest_entry.insert(0, folder)

    def _add_pasted(self) -> None:
        text = self._paste.get("1.0", "end").strip()
        if not text:
            return
        self._add([line for line in text.splitlines() if line.strip()])
        self._paste.delete("1.0", "end")

    def _clear_sources(self) -> None:
        self._sources.clear()
        self._refresh_source_summary()

    def _refresh_source_summary(self) -> None:
        count = len(self._sources)
        if not count:
            self._summary.configure(text="No files selected.")
            self._preview.configure(image=None, text="")
            self._preview_image = None
            return
        total = 0
        for path in self._sources:
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        first = self._sources[0]
        self._summary.configure(
            text=f"{count} image{'s' if count != 1 else ''}, "
                 f"{core.human_bytes(total)}\n{first.name}"
                 + (f"\n+ {count - 1} more" if count > 1 else ""))
        self._load_preview(first)

    def _load_preview(self, path: Path) -> None:
        def work(ctx):
            with Image.open(path) as im:
                im.draft(None, (300, 200))
                thumb = im.convert("RGBA")
                thumb.thumbnail((150, 94), Image.Resampling.LANCZOS)
                return thumb, im.size, im.mode, int(getattr(im, "n_frames", 1) or 1)

        def done(payload) -> None:
            if not self._sources or self._sources[0] != path:
                return
            thumb, size, mode, frames = payload
            self._preview_image = ctk.CTkImage(light_image=thumb, dark_image=thumb,
                                               size=thumb.size)
            self._preview.configure(image=self._preview_image, text="")
            extra = f", {frames} frames" if frames > 1 else ""
            self._summary.configure(
                text=self._summary.cget("text") + f"\n{size[0]}x{size[1]} {mode}{extra}")

        def failed(_exc) -> None:
            self._preview.configure(image=None, text="no preview",
                                    text_color=theme.MUTED)

        self.run(work, name="imaging-preview", on_done=done, on_error=failed)


    def _start(self) -> None:
        if self._job is not None:
            return
        if not self._sources:
            self.status("Add some images first.", tone="warning")
            return

        settings = self._collect_settings()
        self._persist(settings)
        sources = list(self._sources)

        self._clear_table()
        self._progress.set(0)
        self._totals.configure(text="")
        self._convert_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self.status(f"Converting {len(sources)} image(s) to "
                    f"{settings.target_format}...")

        def work(ctx):
            def report(result, done, total):
                ctx.progress(result=result, done=done, total=total)

            return core.convert_batch(
                sources, settings,
                on_result=report,
                should_cancel=lambda: ctx.cancelled,
            )

        self._job = self.run(
            work, name="imaging-convert",
            on_progress=self._on_progress,
            on_done=self._on_done,
            on_error=self._on_error,
            on_cancelled=self._on_cancelled,
        )

    def _cancel(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.status("Stopping after the images already in flight...",
                        tone="warning")

    def _on_progress(self, payload: dict) -> None:
        result = payload.get("result")
        done, total = payload.get("done", 0), payload.get("total", 1)
        self._progress.set(done / total if total else 0)
        if result is not None:
            self._add_row(result)

    def _on_done(self, summary) -> None:
        self._finish()
        if summary.cancelled:
            self.status(f"Stopped. {summary.converted} converted before cancelling.",
                        tone="warning")
        elif summary.failed:
            self.status(f"{summary.converted} converted, {summary.failed} failed.",
                        tone="warning")
        else:
            self.status(f"{summary.converted} converted.", tone="success")
        self._progress.set(1 if not summary.cancelled else self._progress.get())
        self._show_totals(summary)

    def _on_cancelled(self) -> None:
        self._finish()
        self.status("Conversion cancelled.", tone="warning")

    def _on_error(self, exc: BaseException) -> None:
        self._finish()
        self.log.error("conversion failed: %s", exc, exc_info=True)
        self.status(f"Conversion failed: {exc}", tone="danger")

    def _finish(self) -> None:
        self._job = None
        self._convert_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")

    def _show_totals(self, summary) -> None:
        if not summary.converted:
            self._totals.configure(text="")
            return
        saved = summary.saved_bytes
        word = "saved" if saved >= 0 else "added"
        self._totals.configure(
            text=(f"{core.human_bytes(summary.total_src_bytes)} -> "
                  f"{core.human_bytes(summary.total_dst_bytes)}   "
                  f"{core.human_bytes(abs(saved))} {word} "
                  f"({abs(summary.saved_pct):.0f}%)"),
            text_color=theme.SUCCESS if saved >= 0 else theme.WARNING)


    def _clear_table(self) -> None:
        for child in self._table.winfo_children():
            child.destroy()
        self._rows = 0
        self._empty = None

    def _add_row(self, result) -> None:
        self._rows += 1
        if self._rows == MAX_TABLE_ROWS + 1:
            ctk.CTkLabel(
                self._table, text="Further rows are not listed; the totals below "
                                  "still count every file.",
                text_color=theme.MUTED,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
            ).grid(row=self._rows, column=0, sticky="ew", padx=10, pady=6)
            return
        if self._rows > MAX_TABLE_ROWS:
            return

        row = ctk.CTkFrame(
            self._table, corner_radius=6,
            fg_color=theme.ROW_A if self._rows % 2 else theme.ROW_B)
        row.grid(row=self._rows, column=0, sticky="ew", padx=6, pady=1)
        row.grid_columnconfigure(0, weight=1)

        if result.ok:
            tone, mark = theme.SUCCESS, "OK"
        elif result.skipped:
            tone, mark = theme.MUTED, "skipped"
        else:
            tone, mark = theme.DANGER, "failed"

        ctk.CTkLabel(row, text=result.source.name, anchor="w",
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12)
                     ).grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=(6, 0))
        ctk.CTkLabel(row, text=mark, anchor="e", text_color=tone, width=58,
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11)
                     ).grid(row=0, column=1, sticky="e", padx=(0, 10), pady=(6, 0))

        if result.ok:
            size = (f"{result.dst_size[0]}x{result.dst_size[1]}"
                    if result.dst_size else "")
            frames = f", {result.out_frames} frames" if result.out_frames > 1 else ""
            detail = (f"{core.human_bytes(result.src_bytes)} -> "
                      f"{core.human_bytes(result.dst_bytes)}  "
                      f"({result.delta_pct:+.0f}%)   {size}{frames}")
            colour = theme.TEXT_DIM
        else:
            detail = result.error or ""
            colour = theme.MUTED if result.skipped else theme.DANGER
        if result.warnings:
            detail += "   " + "; ".join(result.warnings)
        ctk.CTkLabel(row, text=detail, anchor="w", text_color=colour, justify="left",
                     font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11)
                     ).grid(row=1, column=0, columnspan=2, sticky="ew",
                            padx=10, pady=(0, 6))


    def _persist(self, settings=None) -> None:
        if IMPORT_ERROR is not None:
            return
        try:
            settings = settings or self._collect_settings()
        except Exception:
            return
        self.settings.update({
            "convert": settings.to_dict(),
            "recursive": bool(self._recursive_var.get()),
            "extensions": self._filter_entry.get().strip(),
        })

    def on_hide(self) -> None:
        self._persist()

    def on_close(self) -> None:
        if self._job is not None:
            self._job.cancel()
        self._persist()


__all__ = ["ImagingPanel"]
