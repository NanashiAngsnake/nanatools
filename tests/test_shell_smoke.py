from __future__ import annotations

import sys
import time
import tkinter
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nanatools import registry, theme
from nanatools.settings import SettingsStore
from nanatools.ui.base import ToolPanel
from nanatools.ui.shell import BrokenToolPanel, Shell


def is_on_screen(widget) -> bool:
    try:
        return bool(widget.grid_info())
    except tkinter.TclError:
        return False


def still_alive(widget) -> bool:
    try:
        return bool(widget.winfo_exists())
    except tkinter.TclError:
        return False


class ShellSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        theme.apply("dark")
        self.settings = SettingsStore(Path(self._tmp.name) / "settings.json")
        self.shell = Shell(self.settings)
        self.shell.withdraw()
        self.pump(0.1)

    def tearDown(self) -> None:
        try:
            self.shell.close()
        except tkinter.TclError:
            pass
        finally:
            self._tmp.cleanup()

    def pump(self, seconds: float = 0.15) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.shell.update()
            except tkinter.TclError:
                return
            time.sleep(0.01)


    def test_window_builds_and_shows_a_tool(self) -> None:
        self.assertTrue(still_alive(self.shell))
        self.assertIsNotNone(self.shell._current)
        self.assertIn(self.shell._current, {spec.id for spec in registry.TOOLS})

    def test_every_tool_opens(self) -> None:
        broken: list[str] = []
        for spec in registry.ordered():
            self.shell.show_tool(spec.id)
            self.pump(0.1)
            self.assertTrue(still_alive(self.shell),
                            f"the window died opening {spec.id}")
            self.assertEqual(self.shell._current, spec.id)
            panel = self.shell._panels[spec.id]
            self.assertTrue(is_on_screen(panel), f"{spec.id} panel is not on screen")
            if isinstance(panel, BrokenToolPanel):
                broken.append(spec.id)

        self.assertEqual(
            broken, [],
            "these tools failed to build: " + ", ".join(broken) +
            " (the shell survived, which is the point of BrokenToolPanel, "
            "but they are not working)",
        )

    def test_panels_are_built_lazily(self) -> None:
        self.assertEqual(len(self.shell._panels), 1,
                         "opening the window built more than the visible panel")

    def test_switching_hides_the_previous_panel(self) -> None:
        specs = registry.ordered()
        if len(specs) < 2:
            self.skipTest("needs at least two tools")
        self.shell.show_tool(specs[0].id)
        self.pump()
        self.shell.show_tool(specs[1].id)
        self.pump()
        self.assertFalse(is_on_screen(self.shell._panels[specs[0].id]),
                         "the previous panel is still on screen")
        self.assertTrue(is_on_screen(self.shell._panels[specs[1].id]))

    def test_selected_tool_is_remembered(self) -> None:
        target = registry.ordered()[-1]
        self.shell.show_tool(target.id)
        self.pump()
        self.assertEqual(self.settings.get("shell", "last_tool"), target.id)

    def test_status_bar_updates(self) -> None:
        self.shell.set_status("hello", tone="success")
        self.pump(0.05)
        self.assertEqual(self.shell._status.cget("text"), "hello")

    def test_close_is_clean_and_saves_settings(self) -> None:
        self.shell.show_tool(registry.ordered()[-1].id)
        self.pump()
        self.shell.close()
        self.pump(0.05)
        self.assertFalse(still_alive(self.shell), "window survived close()")
        self.assertTrue(self.settings._path.exists(), "settings were not written on close")
        self.assertTrue(self.shell.jobs._closed)


class PanelContractTests(unittest.TestCase):
    def test_specs_are_unique_and_ordered(self) -> None:
        ids = [spec.id for spec in registry.TOOLS]
        self.assertEqual(len(ids), len(set(ids)), "duplicate tool id")
        orders = [spec.order for spec in registry.TOOLS]
        self.assertEqual(len(orders), len(set(orders)), "duplicate tool order")

    def test_every_panel_class_imports(self) -> None:
        failures: list[str] = []
        for spec in registry.TOOLS:
            try:
                cls = spec.load()
            except BaseException as exc:
                failures.append(f"{spec.id}: {type(exc).__name__}: {exc}")
                continue
            if not issubclass(cls, ToolPanel):
                failures.append(f"{spec.id}: {cls.__name__} is not a ToolPanel")
            elif getattr(cls, "spec", None) is None:
                failures.append(f"{spec.id}: panel class has no .spec")
        self.assertEqual(failures, [], "; ".join(failures))


if __name__ == "__main__":
    unittest.main(verbosity=2)
