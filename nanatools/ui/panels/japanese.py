from __future__ import annotations

import bisect
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont
from typing import Callable, Optional

import customtkinter as ctk

from ... import registry, theme
from ...core import japanese as jp
from ..base import ToolPanel

_ENCODINGS = ("utf-8-sig", "utf-8", "cp932", "euc-jp", "utf-16")

_SAMPLE = (
    "昨日、友達と渋谷でラーメンを食べに行きました。とても美味しかったです。\n"
    "彼は毎日日本語を勉強しています。二人で話し合った結果、走って帰ることにしました。"
)


def _mode_colour(colour) -> str:
    if isinstance(colour, (tuple, list)):
        return colour[1] if ctk.get_appearance_mode() == "Dark" else colour[0]
    return colour


class RubyCanvas(tk.Canvas):
    _LINE_PAD = 14
    _RUBY_GAP = 1
    _MARGIN = 18

    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master, highlightthickness=0, borderwidth=0,
            bg=_mode_colour(theme.CARD), **kwargs,
        )
        self._words: list[jp.Word] = []
        self._widths: list[int] = []
        self._lines: list[list[tuple[int, int, int]]] = []
        self._line_tops: list[int] = []
        self._drawn: dict[int, list[int]] = {}
        self._highlight: Optional[int] = None
        self._hover: Optional[int] = None
        self._dict_available = True

        self._size = 20
        self._gap = 4
        self._show_ruby = True
        self._base_font = tkfont.Font(family=theme.JP_FAMILY, size=self._size)
        self._ruby_font = tkfont.Font(family=theme.JP_FAMILY, size=max(8, self._size // 2))

        self._on_click: Optional[Callable[[jp.Word], None]] = None
        self._resize_after: Optional[str] = None
        self._last_width = 0
        self._placeholder = ""

        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_button)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<MouseWheel>", self._on_wheel)


    def set_words(self, words: list[jp.Word], *, dict_available: bool = True) -> None:
        self._words = words
        self._dict_available = dict_available
        self._highlight = None
        self._hover = None
        self._measure()
        self._relayout()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        if not self._words:
            self._relayout()

    def on_click(self, callback: Callable[[jp.Word], None]) -> None:
        self._on_click = callback

    def set_font_size(self, size: int) -> None:
        self._size = max(12, min(40, int(size)))
        self._base_font.configure(size=self._size)
        self._ruby_font.configure(size=max(8, self._size // 2))
        self._measure()
        self._relayout()

    def set_show_ruby(self, show: bool) -> None:
        self._show_ruby = bool(show)
        self._relayout()

    def set_gap(self, gap: int) -> None:
        self._gap = max(0, int(gap))
        self._relayout()


    def _measure(self) -> None:
        base, ruby = self._base_font, self._ruby_font
        self._widths = [
            max(base.measure(w.surface), ruby.measure(w.ruby) if w.ruby else 0)
            for w in self._words
        ]

    @property
    def _ruby_h(self) -> int:
        return self._ruby_font.metrics("linespace") if self._show_ruby else 0

    @property
    def _line_h(self) -> int:
        return self._ruby_h + self._base_font.metrics("linespace") + self._LINE_PAD

    def _relayout(self) -> None:
        self.delete("all")
        self._drawn.clear()
        self._lines = []
        self._line_tops = []

        width = max(self.winfo_width(), 200)
        self._last_width = width
        if not self._words:
            if self._placeholder:
                self.create_text(
                    self._MARGIN, self._MARGIN, anchor="nw", width=width - 2 * self._MARGIN,
                    text=self._placeholder, fill=theme.MUTED,
                    font=(theme.FONT_FAMILY, 13),
                )
            self.configure(scrollregion=(0, 0, width, 1))
            return

        wrap = width - 2 * self._MARGIN
        line: list[tuple[int, int, int]] = []
        x = self._MARGIN
        top = self._MARGIN

        def break_line(blank: int = 0) -> None:
            nonlocal line, x, top
            self._lines.append(line)
            self._line_tops.append(top)
            top += self._line_h * (1 + blank)
            line, x = [], self._MARGIN

        for index, word in enumerate(self._words):
            newlines = word.space_before.count("\n")
            if newlines and line:
                break_line(newlines - 1)
            item_width = self._widths[index] + self._gap
            if line and x + item_width > self._MARGIN + wrap:
                break_line()
            line.append((index, x, self._widths[index]))
            x += item_width
        if line:
            break_line()

        height = top + self._MARGIN
        self.configure(scrollregion=(0, 0, width, height))
        self._ensure_drawn()


    def _visible_range(self) -> tuple[int, int]:
        if not self._line_tops:
            return 0, 0
        top = self.canvasy(0)
        bottom = top + self.winfo_height()
        margin = self.winfo_height()
        first = bisect.bisect_right(self._line_tops, top - margin) - 1
        last = bisect.bisect_left(self._line_tops, bottom + margin)
        return max(0, first), min(len(self._lines), last + 1)

    def _ensure_drawn(self) -> None:
        first, last = self._visible_range()
        for index in range(first, last):
            if index not in self._drawn:
                self._draw_line(index)

    def _draw_line(self, index: int) -> None:
        items: list[int] = []
        top = self._line_tops[index]
        ruby_h = self._ruby_h
        for word_index, x, width in self._lines[index]:
            word = self._words[word_index]
            centre = x + width / 2
            if self._show_ruby and word.ruby:
                items.append(self.create_text(
                    centre, top, anchor="n", text=word.ruby,
                    font=self._ruby_font, fill=theme.ACCENT,
                ))
            items.append(self.create_text(
                centre, top + ruby_h + self._RUBY_GAP, anchor="n", text=word.surface,
                font=self._base_font, fill=self._colour(word), tags=("word",),
            ))
        self._drawn[index] = items

    def _colour(self, word: jp.Word) -> str:
        if not word.is_content:
            return theme.MUTED
        if not word.is_vocabulary:
            return theme.TEXT_DIM
        if self._dict_available and not word.entries:
            return theme.WARNING
        return theme.TEXT


    def _word_at(self, x: float, y: float) -> Optional[int]:
        if not self._lines:
            return None
        index = bisect.bisect_right(self._line_tops, y) - 1
        if not 0 <= index < len(self._lines):
            return None
        if y > self._line_tops[index] + self._line_h:
            return None
        for word_index, wx, width in self._lines[index]:
            if wx - self._gap / 2 <= x <= wx + width + self._gap / 2:
                return word_index
        return None

    def _on_button(self, event) -> None:
        index = self._word_at(self.canvasx(event.x), self.canvasy(event.y))
        if index is None:
            return
        self.select(index)
        if self._on_click is not None:
            self._on_click(self._words[index])

    def _on_motion(self, event) -> None:
        self._set_hover(self._word_at(self.canvasx(event.x), self.canvasy(event.y)))

    def _set_hover(self, index: Optional[int]) -> None:
        if index == self._hover:
            return
        self._hover = index
        self.configure(cursor="hand2" if index is not None else "")
        self._paint_marker("hover", index, theme.HOVER)

    def select(self, index: Optional[int]) -> None:
        self._highlight = index
        self._paint_marker("selection", index, theme.ACCENT_SOFT)

    def _paint_marker(self, tag: str, index: Optional[int], colour: str) -> None:
        self.delete(tag)
        if index is None:
            return
        box = self._box_of(index)
        if box is None:
            return
        x, top, width = box
        self.create_rectangle(
            x - 2, top - 1, x + width + 2, top + self._line_h - self._LINE_PAD + 3,
            fill=colour, outline="", tags=(tag,),
        )
        self.tag_lower(tag)

    def _box_of(self, index: int) -> Optional[tuple[int, int, int]]:
        for line_index, line in enumerate(self._lines):
            for word_index, x, width in line:
                if word_index == index:
                    return x, self._line_tops[line_index], width
        return None

    def reveal(self, word: jp.Word) -> None:
        try:
            index = self._words.index(word)
        except ValueError:
            return
        box = self._box_of(index)
        if box is None:
            return
        _x, top, _w = box
        region = self.cget("scrollregion")
        if region:
            height = float(str(region).split()[3]) or 1.0
            self.yview_moveto(max(0.0, (top - self.winfo_height() / 3) / height))
        self._ensure_drawn()
        self.select(index)


    def _on_configure(self, _event) -> None:
        if self.winfo_width() == self._last_width:
            self._ensure_drawn()
            return
        if self._resize_after is not None:
            self.after_cancel(self._resize_after)
        self._resize_after = self.after(120, self._resize_done)

    def _resize_done(self) -> None:
        self._resize_after = None
        self._relayout()

    def _on_wheel(self, event) -> None:
        self.yview_scroll(-int(event.delta / 120), "units")
        self._ensure_drawn()

    def yview(self, *args):
        result = super().yview(*args)
        self._ensure_drawn()
        return result


def _styled_text(master, *, size: int = 12, family: Optional[str] = None) -> tk.Text:
    widget = tk.Text(
        master, wrap="word", borderwidth=0, highlightthickness=0,
        bg=_mode_colour(theme.CARD_ALT), fg=theme.TEXT,
        insertbackground=theme.TEXT, selectbackground=theme.ACCENT_SOFT,
        padx=12, pady=10, spacing1=1, spacing3=3, cursor="arrow",
        font=(family or theme.FONT_FAMILY, size),
    )
    widget.tag_configure("h1", font=(theme.JP_FAMILY, 22), foreground=theme.TEXT,
                         spacing3=2)
    widget.tag_configure("jp", font=(theme.JP_FAMILY, 14), foreground=theme.TEXT)
    widget.tag_configure("jp_dim", font=(theme.JP_FAMILY, 12), foreground=theme.TEXT_DIM)
    widget.tag_configure("label", font=(theme.FONT_FAMILY, 10), foreground=theme.MUTED)
    widget.tag_configure("dim", font=(theme.FONT_FAMILY, 11), foreground=theme.TEXT_DIM)
    widget.tag_configure("accent", font=(theme.FONT_FAMILY, 11, "bold"),
                         foreground=theme.ACCENT)
    widget.tag_configure("good", font=(theme.FONT_FAMILY, 10, "bold"),
                         foreground=theme.SUCCESS)
    widget.tag_configure("warn", font=(theme.FONT_FAMILY, 10), foreground=theme.WARNING)
    widget.configure(state="disabled")
    return widget


class JapanesePanel(ToolPanel):
    spec = registry.by_id("japanese")


    def build(self) -> None:
        self._analysis: Optional[jp.Analysis] = None
        self._selected: Optional[jp.Word] = None
        self._vocab_rows: list[jp.Word] = []
        self._kanji_rows: list[jp.Word] = []
        self._job = None

        override = self.settings.get("dict_path", "")
        self._analyzer = jp.Analyzer(
            jp.DictionaryProvider(Path(override) if override else None)
        )

        self._lang = ctk.StringVar(value=self.settings.get("language", "fr"))
        self._furigana = ctk.BooleanVar(value=self.settings.get("furigana", True))
        self._spaced = ctk.BooleanVar(value=self.settings.get("spaced", True))
        self._font_size = int(self.settings.get("font_size", 20))
        self._min_common = ctk.StringVar(value=self.settings.get("min_commonness", "all"))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_banner()
        self._build_toolbar()
        self._build_body()

        self._canvas.set_placeholder(
            "Paste or type Japanese above, then press Analyze.\n\n"
            "Words are split, kanji get furigana, and clicking one shows its "
            "dictionary entry."
        )
        last = self.settings.get("text", "")
        self._input.insert("1.0", last or _SAMPLE)

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="Japanese analyzer", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self._summary = ctk.CTkLabel(
            header, text="", anchor="e", text_color=theme.MUTED,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
        )
        self._summary.grid(row=0, column=1, sticky="e")

    def _build_banner(self) -> None:
        self._banner = ctk.CTkFrame(self, fg_color=theme.CARD_ALT, corner_radius=8)
        self._banner.grid_columnconfigure(0, weight=1)
        self._banner_label = ctk.CTkLabel(
            self._banner, text="", anchor="w", justify="left", wraplength=900,
            text_color=theme.WARNING,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
        )
        self._banner_label.grid(row=0, column=0, sticky="ew", padx=14, pady=10)
        ctk.CTkButton(
            self._banner, text="Choose a dictionary...", width=170, height=28,
            fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
            command=self._choose_dictionary,
        ).grid(row=0, column=1, padx=(0, 12), pady=10)
        self._banner.grid_remove()

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(14, 10))
        bar.grid_columnconfigure(4, weight=1)

        self._analyze_button = ctk.CTkButton(
            bar, text="Analyze", width=110, height=34, command=self._analyze,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=13, weight="bold"),
        )
        self._analyze_button.grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(bar, text="Paste", width=76, height=34, fg_color=theme.ACCENT_SOFT,
                      hover_color=theme.ACCENT_SOFT_HOVER, command=self._paste,
                      ).grid(row=0, column=1, padx=4)
        ctk.CTkButton(bar, text="Open file...", width=104, height=34,
                      fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
                      command=self._open_file).grid(row=0, column=2, padx=4)
        ctk.CTkButton(bar, text="Clear", width=70, height=34, fg_color=theme.ACCENT_SOFT,
                      hover_color=theme.ACCENT_SOFT_HOVER, command=self._clear,
                      ).grid(row=0, column=3, padx=4)

        options = ctk.CTkFrame(bar, fg_color="transparent")
        options.grid(row=0, column=5, sticky="e")
        ctk.CTkSwitch(options, text="Furigana", variable=self._furigana,
                      command=self._toggle_furigana, width=44,
                      font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
                      ).grid(row=0, column=0, padx=(0, 14))
        ctk.CTkSwitch(options, text="Spaced", variable=self._spaced,
                      command=self._toggle_spacing, width=44,
                      font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
                      ).grid(row=0, column=1, padx=(0, 14))
        ctk.CTkButton(options, text="A-", width=34, height=30, fg_color=theme.ACCENT_SOFT,
                      hover_color=theme.ACCENT_SOFT_HOVER,
                      command=lambda: self._resize_font(-2)).grid(row=0, column=2, padx=2)
        ctk.CTkButton(options, text="A+", width=34, height=30, fg_color=theme.ACCENT_SOFT,
                      hover_color=theme.ACCENT_SOFT_HOVER,
                      command=lambda: self._resize_font(2)).grid(row=0, column=3, padx=2)
        self._lang_switch = ctk.CTkSegmentedButton(
            options, values=["FR", "EN"], width=100, height=30,
            command=self._set_language,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
        )
        self._lang_switch.grid(row=0, column=4, padx=(14, 0))
        self._lang_switch.set("FR" if self._lang.get() == "fr" else "EN")

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 20))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        self._input = ctk.CTkTextbox(
            body, height=110, wrap="word", border_width=1,
            font=ctk.CTkFont(family=theme.JP_FAMILY, size=14),
        )
        self._input.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        reading = ctk.CTkFrame(body, fg_color=theme.CARD, corner_radius=8)
        reading.grid(row=1, column=0, sticky="nsew")
        reading.grid_columnconfigure(0, weight=1)
        reading.grid_rowconfigure(0, weight=1)

        self._canvas = RubyCanvas(reading)
        self._canvas.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        self._canvas.set_font_size(self._font_size)
        self._canvas.set_show_ruby(self._furigana.get())
        self._canvas.set_gap(4 if self._spaced.get() else 0)
        self._canvas.on_click(self._select_word)

        scrollbar = ctk.CTkScrollbar(reading, command=self._canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=4)
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._build_sidebar(body)

    def _build_sidebar(self, body) -> None:
        side = ctk.CTkFrame(body, fg_color="transparent", width=360)
        side.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(14, 0))
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(1, weight=1)

        self._tabs = ctk.CTkSegmentedButton(
            side, values=["Word", "Vocabulary", "Kanji"], height=32,
            command=self._show_tab,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=12),
        )
        self._tabs.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self._tabs.set("Word")

        holder = ctk.CTkFrame(side, fg_color=theme.CARD_ALT, corner_radius=8)
        holder.grid(row=1, column=0, sticky="nsew")
        holder.grid_columnconfigure(0, weight=1)
        holder.grid_rowconfigure(0, weight=1)

        self._views: dict[str, tk.Text] = {}
        for name in ("Word", "Vocabulary", "Kanji"):
            view = _styled_text(holder)
            view.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
            self._views[name] = view
            view.grid_remove()
        self._views["Word"].grid()

        self._export_bar = ctk.CTkFrame(side, fg_color="transparent")
        self._export_bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self._export_bar.grid_columnconfigure(2, weight=1)
        ctk.CTkButton(self._export_bar, text="Export TSV", width=100, height=30,
                      command=lambda: self._export("tsv")).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(self._export_bar, text="Export CSV", width=100, height=30,
                      fg_color=theme.ACCENT_SOFT, hover_color=theme.ACCENT_SOFT_HOVER,
                      command=lambda: self._export("csv")).grid(row=0, column=1)
        ctk.CTkOptionMenu(
            self._export_bar, values=["all", "common", "very common"], width=120, height=30,
            variable=self._min_common, command=self._set_min_commonness,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=11),
        ).grid(row=0, column=2, sticky="e")
        self._export_bar.grid_remove()

        self._write(self._views["Word"], [
            ("Click a word in the text to see its reading, its dictionary form "
             "and its meaning.\n", "dim"),
        ])


    def _analyze(self) -> None:
        text = self._input.get("1.0", "end-1c").strip()
        if not text:
            self.status("Nothing to analyze -- paste some Japanese first.", tone="warning")
            return
        if not jp.is_japanese(text):
            self.status("That text has no Japanese in it.", tone="warning")
            return
        if self._job is not None and not self._job.finished:
            self.status("Already analyzing.", tone="info")
            return

        self.settings.set("text", text[:20000])
        self._analyze_button.configure(state="disabled", text="Analyzing...")
        self.status("Analyzing...")

        analyzer = self._analyzer

        def work(ctx):
            ctx.progress(stage="Loading the dictionary...")
            analyzer.warmup()
            ctx.check_cancelled()
            ctx.progress(stage="Segmenting...")
            return analyzer.analyze(text, cancel=ctx.token)

        self._job = self.run(
            work, name="japanese-analyze",
            on_progress=lambda p: self.status(p.get("stage", "")),
            on_done=self._analysis_done,
            on_error=self._analysis_failed,
            on_cancelled=lambda: self._analyze_button.configure(
                state="normal", text="Analyze"),
        )

    def _analysis_done(self, analysis: jp.Analysis) -> None:
        self._analyze_button.configure(state="normal", text="Analyze")
        self._analysis = analysis
        self._selected = None

        self._canvas.set_words(analysis.words,
                               dict_available=analysis.dict_status.available)
        self._update_banner(analysis.dict_status)

        vocab = analysis.vocabulary()
        kanji = analysis.kanji()
        self._summary.configure(
            text=f"{len(analysis.text):,} characters   {len(analysis.words):,} words   "
                 f"{len(vocab):,} vocabulary   {len(kanji):,} kanji   "
                 f"{analysis.elapsed_ms:.0f} ms"
        )
        self._render_vocabulary()
        self._render_kanji()
        self._write(self._views["Word"], [
            ("Click a word in the text to see its entry.\n", "dim"),
        ])

        if analysis.truncated:
            self.status(
                f"Text was longer than {jp.MAX_ANALYSIS_CHARS:,} characters; "
                "only the beginning was analyzed.", tone="warning")
        else:
            self.status(f"Analyzed {len(analysis.words):,} words in "
                        f"{analysis.elapsed_ms:.0f} ms.", tone="success")

    def _analysis_failed(self, exc: BaseException) -> None:
        self._analyze_button.configure(state="normal", text="Analyze")
        if isinstance(exc, ModuleNotFoundError):
            self._show_missing_dependency(exc)
            return
        self.log.error("analysis failed: %s", exc, exc_info=True)
        self.status(f"Could not analyze the text: {exc}", tone="danger")

    def _show_missing_dependency(self, exc: ModuleNotFoundError) -> None:
        name = exc.name or "fugashi"
        self._banner_label.configure(
            text=(f"Segmentation needs the '{name}' package, which is not installed.\n"
                  "Install it with:  pip install fugashi unidic-lite"),
            text_color=theme.DANGER,
        )
        self._banner.grid(row=1, column=0, sticky="ew", padx=24, pady=(12, 0))
        self.status(f"'{name}' is not installed.", tone="danger")

    def _update_banner(self, status: jp.DictStatus) -> None:
        if status.available:
            self._banner.grid_remove()
            self.log.info("dictionary: %s", status.message)
            return
        self._banner_label.configure(text=status.message, text_color=theme.WARNING)
        self._banner.grid(row=1, column=0, sticky="ew", padx=24, pady=(12, 0))


    def _select_word(self, word: jp.Word) -> None:
        self._selected = word
        self._tabs.set("Word")
        self._show_tab("Word")
        self._render_word(word)

    def _render_word(self, word: jp.Word) -> None:
        lang = self._lang.get()
        chunks: list[tuple[str, str]] = [(word.surface + "\n", "h1")]
        if word.reading and word.reading != word.surface:
            chunks.append((word.reading + "\n", "jp_dim"))
        chunks.append(("\n", "dim"))

        chunks.append(("dictionary form   ", "label"))
        chunks.append((word.lemma, "jp"))
        if word.lemma_reading and word.lemma_reading != word.lemma:
            chunks.append(("  " + word.lemma_reading, "jp_dim"))
        chunks.append(("\n", "dim"))
        chunks.append(("part of speech   ", "label"))
        chunks.append((f"{word.pos} {word.pos2}\n", "dim"))
        if len(word.tokens) > 1:
            chunks.append(("built from   ", "label"))
            chunks.append((" + ".join(word.tokens) + "\n", "jp_dim"))

        entry = word.best
        if entry is not None:
            if entry.commonness:
                chunks.append(("\n" + entry.commonness_label.upper(), "good"))
                if entry.tags:
                    chunks.append(("   " + " ".join(entry.tags), "dim"))
                chunks.append(("\n", "dim"))
            if entry.matched_on == "reading":
                chunks.append(("Matched by reading, not by spelling.\n", "warn"))
            if not entry.pos_match:
                chunks.append(
                    ("No entry matches this word class; the senses below may "
                     "belong to a different word written the same way.\n", "warn"))

        if not word.entries:
            if not self._dictionary_available():
                chunks.append(("\nNo dictionary loaded, so no meanings.\n", "warn"))
            else:
                chunks.append(("\nNo dictionary entry for this word.\n", "warn"))
        for entry in word.entries[:4]:
            chunks.append(("\n" + entry.expr, "jp"))
            if entry.reading and entry.reading != entry.expr:
                chunks.append(("  " + entry.reading, "jp_dim"))
            chunks.append(("\n", "dim"))
            for sense in self._ordered_senses(entry, lang):
                chunks.append((f"  [{sense.lang}] ", "accent"))
                codes = " ".join(sense.pos_codes)
                if codes:
                    chunks.append((codes + "  ", "label"))
                chunks.append((sense.text + "\n", "dim"))

        sentence = self._analysis.sentence_at(word) if self._analysis else ""
        if sentence and sentence != word.surface:
            chunks.append(("\nin context\n", "label"))
            chunks.append((sentence + "\n", "jp_dim"))

        self._write(self._views["Word"], chunks)

    @staticmethod
    def _ordered_senses(entry: jp.Entry, lang: str) -> list[jp.Sense]:
        return ([s for s in entry.senses if s.lang == lang]
                + [s for s in entry.senses if s.lang != lang])[:6]

    def _render_vocabulary(self) -> None:
        view = self._views["Vocabulary"]
        if self._analysis is None:
            return
        minimum = {"all": 0, "common": 1, "very common": 2}[self._min_common.get()]
        self._vocab_rows = self._analysis.vocabulary(min_commonness=minimum)
        lang = self._lang.get()

        view.configure(state="normal")
        view.delete("1.0", "end")
        view.insert("end", f"{len(self._vocab_rows)} words\n\n", "label")
        for index, word in enumerate(self._vocab_rows):
            tag = f"vocab{index}"
            start = view.index("end-1c")
            view.insert("end", word.lemma, ("jp", tag))
            if word.lemma_reading and word.lemma_reading != word.lemma:
                view.insert("end", "  " + word.lemma_reading, ("jp_dim", tag))
            entry = word.best
            if entry is not None and entry.commonness == 2:
                view.insert("end", "  ★", ("good", tag))
            view.insert("end", "\n", tag)
            meaning = word.meaning(lang)
            view.insert("end", ("  " + meaning if meaning else "  --") + "\n\n",
                        ("dim", tag))
            view.tag_add(tag, start, view.index("end-1c"))
            view.tag_bind(tag, "<Button-1>",
                          lambda _e, w=word: self._jump_to(w))
            view.tag_bind(tag, "<Enter>", lambda _e, v=view: v.configure(cursor="hand2"))
            view.tag_bind(tag, "<Leave>", lambda _e, v=view: v.configure(cursor="arrow"))
        view.configure(state="disabled")

    def _render_kanji(self) -> None:
        view = self._views["Kanji"]
        if self._analysis is None:
            return
        rows = self._analysis.kanji()
        view.configure(state="normal")
        view.delete("1.0", "end")
        view.insert("end", f"{len(rows)} distinct kanji in this text\n", "label")
        view.insert("end",
                    "Stroke counts and JLPT levels are not in the dictionary, "
                    "so they are not shown.\n\n", "dim")
        for char, words in rows:
            view.insert("end", char + "   ", "h1")
            view.insert("end", f"{len(words)} word{'s' if len(words) != 1 else ''}\n",
                        "label")
            for word in words[:8]:
                view.insert("end", "  " + word.lemma, "jp")
                if word.lemma_reading:
                    view.insert("end", "  " + word.lemma_reading, "jp_dim")
                view.insert("end", "\n", "dim")
            view.insert("end", "\n", "dim")
        view.configure(state="disabled")

    def _write(self, view: tk.Text, chunks: list[tuple[str, str]]) -> None:
        view.configure(state="normal")
        view.delete("1.0", "end")
        for text, tag in chunks:
            view.insert("end", text, tag)
        view.configure(state="disabled")

    def _show_tab(self, name: str) -> None:
        for other, view in self._views.items():
            if other == name:
                view.grid()
            else:
                view.grid_remove()
        if name == "Vocabulary":
            self._export_bar.grid()
        else:
            self._export_bar.grid_remove()

    def _jump_to(self, word: jp.Word) -> None:
        self._canvas.reveal(word)
        self._selected = word
        self._render_word(word)


    def _paste(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.status("The clipboard is empty or holds something else.",
                        tone="warning")
            return
        self._input.delete("1.0", "end")
        self._input.insert("1.0", text)
        self._analyze()

    def _open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Open a Japanese text file",
            filetypes=[("Text files", "*.txt *.md *.srt *.vtt"), ("All files", "*.*")],
        )
        if not path:
            return
        raw = Path(path).read_bytes()
        for encoding in _ENCODINGS:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
            self.status("That file's encoding was not recognised; some "
                        "characters may be wrong.", tone="warning")
        self._input.delete("1.0", "end")
        self._input.insert("1.0", text)
        self._analyze()

    def _clear(self) -> None:
        self._input.delete("1.0", "end")
        self._analysis = None
        self._selected = None
        self._canvas.set_words([])
        self._summary.configure(text="")
        for view in self._views.values():
            self._write(view, [])
        self.settings.set("text", "")
        self.status("Cleared.")

    def _export(self, kind: str) -> None:
        if self._analysis is None or not self._vocab_rows:
            self.status("Analyze some text first.", tone="warning")
            return
        suffix = ".tsv" if kind == "tsv" else ".csv"
        path = filedialog.asksaveasfilename(
            title="Export vocabulary", defaultextension=suffix,
            initialfile=f"vocabulary{suffix}",
            filetypes=[("Anki TSV", "*.tsv")] if kind == "tsv" else [("CSV", "*.csv")],
        )
        if not path:
            return
        analysis, rows, lang = self._analysis, list(self._vocab_rows), self._lang.get()
        writer = jp.write_tsv if kind == "tsv" else jp.write_csv

        def work(_ctx):
            return writer(analysis, rows, Path(path), lang=lang)

        self.run(
            work, name="japanese-export",
            on_done=lambda count: self.status(
                f"Exported {count} words to {Path(path).name}.", tone="success"),
        )

    def _choose_dictionary(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a JMdict SQLite dictionary",
            filetypes=[("SQLite database", "*.db *.sqlite *.sqlite3"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        provider = jp.DictionaryProvider(Path(path))
        status = provider.resolve()
        if not status.available:
            self.status("That file is not a dictionary this tool can read.",
                        tone="danger")
            self._banner_label.configure(text=status.message, text_color=theme.DANGER)
            return
        self.settings.set("dict_path", path)
        self._analyzer.provider.close()
        self._analyzer.provider = provider
        self._update_banner(status)
        self.status(f"Dictionary loaded: {status.entry_count:,} entries.",
                    tone="success")
        if self._analysis is not None:
            self._analyze()

    def _dictionary_available(self) -> bool:
        return self._analyzer.provider.status.available


    def _toggle_furigana(self) -> None:
        self._canvas.set_show_ruby(self._furigana.get())
        self.settings.set("furigana", self._furigana.get())

    def _toggle_spacing(self) -> None:
        self._canvas.set_gap(4 if self._spaced.get() else 0)
        self.settings.set("spaced", self._spaced.get())

    def _resize_font(self, delta: int) -> None:
        self._font_size = max(12, min(40, self._font_size + delta))
        self._canvas.set_font_size(self._font_size)
        self.settings.set("font_size", self._font_size)

    def _set_language(self, value: str) -> None:
        self._lang.set("fr" if value == "FR" else "en")
        self.settings.set("language", self._lang.get())
        if self._analysis is not None:
            self._render_vocabulary()
            if self._selected is not None:
                self._render_word(self._selected)

    def _set_min_commonness(self, _value: str) -> None:
        self.settings.set("min_commonness", self._min_common.get())
        self._render_vocabulary()


    def on_close(self) -> None:
        if self._job is not None and not self._job.finished:
            self._job.cancel()
        self._analyzer.provider.close()


__all__ = ["JapanesePanel", "RubyCanvas"]
