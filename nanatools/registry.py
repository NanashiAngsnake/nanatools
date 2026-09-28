from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    glyph: str
    summary: str
    module: str
    cls: str
    order: int

    def load(self) -> Any:
        module = importlib.import_module(self.module)
        return getattr(module, self.cls)


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="downloader",
        name="Video downloader",
        glyph="⬇",
        summary="Download videos and audio from a URL, with a queue.",
        module="nanatools.ui.panels.downloader",
        cls="DownloaderPanel",
        order=10,
    ),
    ToolSpec(
        id="organizer",
        name="File organizer",
        glyph="\U0001f5c2",
        summary="Sort a cluttered folder into categories -- preview first, undo always.",
        module="nanatools.ui.panels.organizer",
        cls="OrganizerPanel",
        order=20,
    ),
    ToolSpec(
        id="imaging",
        name="Image converter",
        glyph="\U0001f5bc",
        summary="Batch convert, resize and re-encode images.",
        module="nanatools.ui.panels.imaging",
        cls="ImagingPanel",
        order=30,
    ),
    ToolSpec(
        id="japanese",
        name="Japanese analyzer",
        glyph="文",
        summary="Segment Japanese text, show readings and dictionary entries.",
        module="nanatools.ui.panels.japanese",
        cls="JapanesePanel",
        order=40,
    ),
)


def ordered() -> list[ToolSpec]:
    return sorted(TOOLS, key=lambda spec: spec.order)


def by_id(tool_id: str) -> ToolSpec | None:
    for spec in TOOLS:
        if spec.id == tool_id:
            return spec
    return None


__all__ = ["ToolSpec", "TOOLS", "ordered", "by_id"]
