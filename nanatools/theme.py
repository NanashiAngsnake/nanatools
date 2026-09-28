from __future__ import annotations

import customtkinter as ctk

ACCENT = "#7c5cff"
ACCENT_HOVER = "#6a48f0"
ACCENT_SOFT = "#241f47"
ACCENT_SOFT_HOVER = "#2f2857"

SUCCESS = "#3ddc97"
DANGER = "#ff6b6b"
WARNING = "#ffb454"
MUTED = "#8b90a3"
TEXT = "#e8e9f0"
TEXT_DIM = "#a6abbd"

WINDOW = ("#f5f5fa", "#0a0b0f")
SIDEBAR = ("#ecedf4", "#0e1016")
CARD = ("#ffffff", "#161923")
CARD_ALT = ("#eef0f6", "#1d2130")
BORDER = "#272b3a"
HOVER = "#232736"
ROW_A = ("#f7f8fc", "#181c27")
ROW_B = ("#eff1f7", "#13161f")

TOOL_COLOURS = {
    "downloader": "#7c5cff",
    "organizer": "#3ddc97",
    "imaging": "#ffb454",
    "japanese": "#ff6b9d",
}

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Cascadia Mono"
JP_FAMILY = "Yu Gothic UI"


def _override(theme: dict) -> None:
    theme["CTk"]["fg_color"] = list(WINDOW)
    theme["CTkToplevel"]["fg_color"] = list(WINDOW)

    theme["CTkFrame"].update(
        fg_color=list(CARD),
        top_fg_color=list(CARD_ALT),
        border_color=BORDER,
    )
    theme["CTkButton"].update(
        fg_color=ACCENT,
        hover_color=ACCENT_HOVER,
        border_color=BORDER,
        text_color=["#ffffff", "#ffffff"],
        text_color_disabled=[MUTED, MUTED],
    )
    theme["CTkEntry"].update(
        fg_color=list(CARD_ALT),
        border_color=BORDER,
        text_color=[("#11131a"), TEXT],
        placeholder_text_color=[MUTED, MUTED],
    )
    theme["CTkTextbox"].update(
        fg_color=list(CARD_ALT),
        border_color=BORDER,
        text_color=["#11131a", TEXT],
        scrollbar_button_color=[MUTED, "#3a3f52"],
        scrollbar_button_hover_color=[ACCENT, ACCENT],
    )
    theme["CTkLabel"]["text_color"] = ["#11131a", TEXT]
    theme["CTkCheckBox"].update(
        fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=[MUTED, "#3a3f52"],
        text_color=["#11131a", TEXT],
    )
    theme["CTkRadioButton"].update(
        fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=[MUTED, "#3a3f52"],
        text_color=["#11131a", TEXT],
    )
    theme["CTkSwitch"].update(
        progress_color=ACCENT, button_color=["#ffffff", "#d9dbe6"],
        button_hover_color=["#ffffff", "#ffffff"],
        fg_color=[MUTED, "#2b3040"], text_color=["#11131a", TEXT],
    )
    theme["CTkProgressBar"].update(
        fg_color=[MUTED, "#242938"], progress_color=ACCENT, border_color=BORDER,
    )
    theme["CTkSlider"].update(
        fg_color=[MUTED, "#242938"], progress_color=ACCENT,
        button_color=ACCENT, button_hover_color=ACCENT_HOVER,
    )
    theme["CTkOptionMenu"].update(
        fg_color=list(CARD_ALT), button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        text_color=["#11131a", TEXT], text_color_disabled=[MUTED, MUTED],
    )
    theme["CTkComboBox"].update(
        fg_color=list(CARD_ALT), border_color=BORDER,
        button_color=BORDER, button_hover_color=ACCENT,
        text_color=["#11131a", TEXT],
    )
    theme["CTkScrollbar"].update(
        fg_color="transparent",
        button_color=[MUTED, "#31364a"],
        button_hover_color=[ACCENT, ACCENT],
    )
    theme["CTkSegmentedButton"].update(
        fg_color=list(CARD_ALT),
        selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
        unselected_color=list(CARD_ALT), unselected_hover_color=HOVER,
        text_color=["#11131a", TEXT],
    )
    theme["CTkScrollableFrame"]["label_fg_color"] = list(CARD_ALT)


def apply(appearance: str = "dark") -> None:
    ctk.set_appearance_mode(appearance)
    ctk.set_default_color_theme("blue")
    _override(ctk.ThemeManager.theme)


__all__ = ["apply", "ACCENT", "ACCENT_HOVER", "ACCENT_SOFT", "ACCENT_SOFT_HOVER",
           "SUCCESS", "DANGER", "WARNING", "MUTED", "TEXT", "TEXT_DIM",
           "WINDOW", "SIDEBAR", "CARD", "CARD_ALT", "BORDER", "HOVER",
           "ROW_A", "ROW_B", "TOOL_COLOURS",
           "FONT_FAMILY", "MONO_FAMILY", "JP_FAMILY"]
