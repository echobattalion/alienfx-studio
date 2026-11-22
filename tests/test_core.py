"""Run with: python3 -m unittest discover -s tests

Uses a fake CLI and temporary directories, so it never touches real lights.
"""
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.TemporaryDirectory()
LOG = Path(TMP.name) / "cli.log"
FAKE = Path(TMP.name) / "fake_cli"
FAKE.write_text(f"""#!{sys.executable}
import json, sys
args = sys.argv[1:]
stdin = sys.stdin.read() if "setbatch" in args else ""
with open({str(LOG)!r}, "a") as f:
    f.write(json.dumps({{"args": args, "stdin": stdin}}) + "\\n")
""")
FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)
os.environ.update(ALIENFX_CLI=str(FAKE), XDG_RUNTIME_DIR=TMP.name, XDG_CONFIG_HOME=TMP.name,
                  QT_QPA_PLATFORM="offscreen")
os.environ.pop("ALIENFX_KEYBOARD_DEV", None)
os.environ.pop("ALIENFX_ELC_DEV", None)

from alienfx_studio import device, kbmap  # noqa: E402
from alienfx_studio.profiles import Profile, ProfileError, Store, default_profile  # noqa: E402


def calls():
    return [json.loads(line) for line in LOG.read_text().splitlines()] if LOG.exists() else []


def counts(entries):
    lines = [l for e in entries for l in e["stdin"].splitlines()]
    return (sum(l.startswith("0 ") for l in lines), sum(l.startswith("1 ") for l in lines),
            sum("setpower" in e["args"] for e in entries))


class ProfileTests(unittest.TestCase):
    def test_round_trip(self):
        p = default_profile()
        self.assertEqual(Profile.from_dict(p.to_dict()).to_dict(), p.to_dict())

    def test_rejects_bad_color(self):
        d = default_profile().to_dict()
        d["keyboard"]["default"] = "red"
        with self.assertRaises(ProfileError):
            Profile.from_dict(d)

    def test_store_rename_and_last_profile(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            store.rename("Default", "Mine")
            self.assertEqual(store.active_name(), "Mine")
            with self.assertRaises(ProfileError):
                store.delete("Mine")


class DeviceTests(unittest.TestCase):
    def setUp(self):
        LOG.unlink(missing_ok=True)

    def test_full_frame(self):
        device.send(device.build_frame(default_profile()))
        entries = calls()
        self.assertEqual(counts(entries), (kbmap.N, 19, 1))
        self.assertNotIn("--save", [a for e in entries for a in e["args"]])
        self.assertEqual(entries[1]["args"][3:5], ["1", "19"])

    def test_arrows_write_every_bank(self):
        lines = device.build_frame(default_profile()).batch.splitlines()
        for name in ("LEFT", "DOWN", "RIGHT"):
            for i in kbmap.KEYS[name]:
                self.assertIn(f"0 {i} 0 0 255", lines)

    def test_delta(self):
        p = default_profile()
        base = device.build_frame(p)
        p.keys["W"] = "#00FF00"
        d = device.delta(device.build_frame(p), base)
        self.assertEqual((d.batch.count("\n"), len(d.power)), (kbmap.N, 0))
        p = default_profile()
        p.zones[12] = "#010203"
        d = device.delta(device.build_frame(p), base)
        self.assertEqual((d.batch, d.power), ("1 12 1 2 3\n", ()))
        p = default_profile()
        p.zones[19] = "#010203"
        d = device.delta(device.build_frame(p), base)
        self.assertEqual((d.batch, d.power), ("", ((19, (1, 2, 3)),)))

    def test_external_write_forces_full_resync(self):
        done = []
        writer = device.LiveWriter(lambda ok, msg: done.append(ok))
        p = default_profile()
        writer.submit(device.build_frame(p))
        while not done:
            time.sleep(.005)
        time.sleep(.01)
        device.send(device.build_frame(default_profile()))
        LOG.unlink()
        p.zones[12] = "#040506"
        writer.submit(device.build_frame(p))
        while len(done) < 2:
            time.sleep(.005)
        writer.close()
        self.assertEqual(counts(calls()), (kbmap.N, 19, 1))


class GuiTests(unittest.TestCase):
    def test_edit_undo_save(self):
        try:
            from PyQt6.QtGui import QColor
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PyQt6 not installed")
        from alienfx_studio.app import MainWindow
        app = QApplication.instance() or QApplication([])
        win = MainWindow()
        original = win.profile.color_of("K:W")
        win.view.select({"K:W"})
        self.assertFalse(win.dirty)
        win.picker.setCurrentColor(QColor("#00FFFF"))
        app.processEvents()
        self.assertEqual(win.profile.color_of("K:W"), "#00FFFF")
        self.assertTrue(win.dirty)
        win.undo()
        self.assertEqual(win.profile.color_of("K:W"), original)
        self.assertFalse(win.dirty)
        win.writer.close()


class LayoutTests(unittest.TestCase):
    def setUp(self):
        from alienfx_studio import layout
        self.layout = layout
        self.items = layout.build_items()

    def test_every_mapped_key_is_present_and_enabled(self):
        by_id = {i.id: i for i in self.items}
        for name in kbmap.KEYS:
            self.assertTrue(by_id[f"K:{name}"].enabled, name)

    def test_unmapped_keys_are_disabled(self):
        by_id = {i.id: i for i in self.items}
        for name in ("MEDIA1", "SPACE"):
            self.assertFalse(by_id[f"K:{name}"].enabled, name)

    def test_probed_keys_are_mapped(self):
        self.assertEqual(kbmap.KEYS["MEDIA2"], [18])
        self.assertEqual(kbmap.KEYS["MEDIA3"], [17])
        self.assertEqual(kbmap.KEYS["MEDIA4"], [16, 113, 115])
        self.assertEqual(kbmap.KEYS["RWIN"], [109])
        indices = [i for leds in kbmap.KEYS.values() for i in leds]
        self.assertEqual(len(indices), len(set(indices)))

    def test_windows_legends_share_the_fn_alt_top_line(self):
        from PyQt6.QtWidgets import QApplication
        from alienfx_studio.keyboard_view import legend_path
        type(self).app = QApplication.instance() or QApplication([])  # must outlive the test
        by_id = {i.id: i for i in self.items}
        self.assertEqual(by_id["K:RWIN"].legend, "win_lock")
        tops = [legend_path(by_id[f"K:{n}"], 80, 80, 80).boundingRect().top()
                for n in ("FN", "LWIN", "LALT", "RALT", "RWIN")]
        self.assertLess(max(tops) - min(tops), 1.0)

    def test_rows_are_sixteen_wide(self):
        for _, _, row in self.layout.PHYSICAL_ROWS:
            self.assertAlmostEqual(sum(w for _, w in row), self.layout.WIDTH)

    def test_no_overlaps(self):
        for n, a in enumerate(self.items):
            for b in self.items[n + 1:]:
                overlap = (a.x < b.x + b.w - 1e-6 and b.x < a.x + a.w - 1e-6
                           and a.y < b.y + b.h - 1e-6 and b.y < a.y + a.h - 1e-6)
                self.assertFalse(overlap, f"{a.id} overlaps {b.id}")


class RedesignGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 not installed")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from alienfx_studio.app import MainWindow
        self.win = MainWindow()
        self.addCleanup(self.win.writer.close)

    def settle(self):
        """Wait for queued live writes, so they cannot leak into a later assertion."""
        self.win.writer.close()
        from alienfx_studio import device
        self.win.writer = device.LiveWriter(lambda ok, msg: None)
        self.app.processEvents()

    def test_disabled_keys_cannot_be_selected(self):
        self.win.view.select({"K:SPACE", "K:MEDIA1", "K:W"})  # no LED, fixed LED, normal key
        self.assertEqual(self.win.view.selected, {"K:W"})
        item = next(i for i in self.win.view.items if i.id == "K:SPACE")
        self.win.resize(1440, 900)
        self.assertIsNone(self.win.view._at(self.win.view._rect(item).center()))

    def test_invalid_hex_changes_nothing(self):
        self.win.view.select({"K:W"})
        before = self.win.profile.to_dict()
        for text in ("", "#12", "#GGGGGG", "nope", "#1234567"):
            self.win.picker.hex.setText(text)
            self.win.picker.hex.textEdited.emit(text)
        self.assertEqual(self.win.profile.to_dict(), before)

    def test_valid_hex_paints(self):
        self.win.view.select({"K:W"})
        self.win.picker.hex.textEdited.emit("#12ab34")
        self.assertEqual(self.win.profile.color_of("K:W"), "#12AB34")
        self.win.undo()
        self.settle()

    def test_dark_colors_survive_the_dock(self):
        self.win.view.select({"K:W"})
        self.win.picker.setCurrentColor(__import__("PyQt6.QtGui").QtGui.QColor("#102030"))
        self.assertEqual(self.win.profile.color_of("K:W"), "#102030")
        self.win.undo()
        self.settle()

    def test_drag_is_one_undo_step(self):
        self.win.view.select({"K:W"})
        depth = len(self.win.undo_stack)
        self.win.picker.interactionStarted.emit()
        for value in (.1, .2, .3, .4):
            self.win.picker.hue.valueChanged.emit(value)
        self.win.picker.interactionFinished.emit()
        self.assertEqual(len(self.win.undo_stack), depth + 1)
        self.win.undo()
        self.settle()

    def test_brightness_display_keeps_stored_colors(self):
        before = self.win.profile.color_of("K:W")
        self.win.profile.brightness = 0
        self.win.view.grab()
        self.assertEqual(self.win.profile.color_of("K:W"), before)
        self.win.profile.brightness = 100

    def test_library_render_sends_no_hardware_writes(self):
        self.settle()
        LOG.unlink(missing_ok=True)
        self.win.show_library()
        self.win.library.refresh()
        self.win.resize(1600, 1000)
        self.win.grab()
        self.app.processEvents()
        time.sleep(.05)
        self.assertEqual(calls(), [])
        self.win.show_editor()

    def test_library_apply_honors_unsaved_prompt(self):
        from unittest import mock
        from PyQt6.QtWidgets import QMessageBox
        other = default_profile()
        other.name = "Library Other"
        self.win.store.save(other)
        self.addCleanup(lambda: self.win.store.delete("Library Other"))
        self.win.library.refresh()
        self.win.view.select({"K:W"})
        self.win.picker.hex.textEdited.emit("#010203")
        self.assertTrue(self.win.dirty)
        current = self.win.profile.name
        with mock.patch.object(QMessageBox, "question",
                               return_value=QMessageBox.StandardButton.Cancel) as asked:
            self.win.library.cards["Library Other"].apply()
        asked.assert_called_once()
        self.assertEqual(self.win.profile.name, current)
        self.assertEqual(self.win.profile.color_of("K:W"), "#010203")
        self.win.undo()
        self.settle()

    def test_library_is_home_and_leads_to_editor(self):
        self.assertIs(self.win.pages.currentWidget(), self.win.library)
        self.assertFalse(hasattr(self.win, "tabs"))
        self.assertFalse(hasattr(self.win, "nav_editor"))
        other = default_profile()
        other.name = "Library Edit"
        self.win.store.save(other)
        original = self.win.profile.name
        def cleanup():
            self.win.store.set_active(original)
            self.win.store.delete("Library Edit")
        self.addCleanup(cleanup)
        self.win.library.refresh()
        self.win.library.cards["Library Edit"].edit_btn.click()
        self.assertEqual(self.win.profile.name, "Library Edit")
        self.assertIs(self.win.pages.currentWidget(), self.win.editor_page)
        self.assertEqual(self.win.profile_label.text(), "Library Edit")
        self.win.back_btn.click()
        self.assertIs(self.win.pages.currentWidget(), self.win.library)
        self.settle()

    def test_library_card_click_applies_and_edit_opens(self):
        from unittest import mock
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        other = default_profile()
        other.name = "Card Click"
        self.win.store.save(other)
        original = self.win.profile.name
        def cleanup():
            self.win.store.set_active(original)
            self.win.store.delete("Card Click")
        self.addCleanup(cleanup)
        self.addCleanup(self.win.hide)
        self.win.resize(1440, 900)
        self.win.show()
        self.win.library.refresh()
        self.app.processEvents()
        lib = self.win.library
        self.assertFalse(hasattr(lib, "new_btn"))
        self.assertFalse(hasattr(lib.cards["Card Click"], "action"))
        card = lib.cards["Card Click"]
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=card.preview.geometry().center())
        self.app.processEvents()
        self.assertEqual(self.win.profile.name, "Card Click")
        self.assertIs(self.win.pages.currentWidget(), lib, "a click must stay in the Library")
        active = lib.cards["Card Click"]
        with mock.patch.object(self.win, "switch_profile") as switched:
            QTest.mouseClick(active, Qt.MouseButton.LeftButton,
                             pos=active.preview.geometry().center())
        switched.assert_not_called()
        lib.cards[original].edit_btn.click()
        self.assertEqual(self.win.profile.name, original)
        self.assertIs(self.win.pages.currentWidget(), self.win.editor_page)
        self.settle()

    def _paint_setup(self, color="#123456"):
        from PyQt6.QtGui import QColor
        self.win.resize(1440, 900)
        self.win.show()
        self.win.show_editor()
        self.app.processEvents()
        self.win.picker.setCurrentColor(QColor(color))
        self.win.set_paint_mode(True)
        self.addCleanup(self.win.hide)
        self.addCleanup(self.settle)
        self.addCleanup(lambda: self.win.revert())

    def _point(self, key):
        item = next(i for i in self.win.view.items if i.id == key)
        return self.win.view._rect(item).center().toPoint()

    def test_paint_click_applies_color_and_keeps_it(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self._paint_setup()
        for key in ("K:W", "K:P", "E:3"):
            QTest.mouseClick(self.win.view, Qt.MouseButton.LeftButton, pos=self._point(key))
            self.assertEqual(self.win.profile.color_of(key), "#123456")
        self.assertEqual(self.win._picker_hex(), "#123456")
        self.assertEqual(self.win.view.selected, set())
        self.assertEqual(self.win.recent[0], "#123456")

    def test_paint_skips_disabled_keys(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self._paint_setup()
        before = self.win.profile.to_dict()
        for key in ("K:SPACE", "K:MEDIA1"):
            QTest.mouseClick(self.win.view, Qt.MouseButton.LeftButton, pos=self._point(key))
        self.assertEqual(self.win.profile.to_dict(), before)

    def test_paint_drag_is_continuous_and_one_undo(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self._paint_setup()
        original = self.win.profile.color_of("K:E")
        depth = len(self.win.undo_stack)
        view = self.win.view
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=self._point("K:Q"))
        QTest.mouseMove(view, self._point("K:P"))     # one jump must not skip keys
        self.app.processEvents()
        QTest.qWait(30)
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=self._point("K:P"))
        for key in "QWERTYUIOP":
            self.assertEqual(self.win.profile.color_of(f"K:{key}"), "#123456", key)
        self.assertEqual(len(self.win.undo_stack), depth + 1)
        self.win.undo()
        self.assertEqual(self.win.profile.color_of("K:E"), original)

    def test_paint_chip_fills_group(self):
        self._paint_setup()
        depth = len(self.win.undo_stack)
        self.win.chips["WASD"].click()
        for key in "WASD":
            self.assertEqual(self.win.profile.color_of(f"K:{key}"), "#123456")
        self.assertEqual(self.win.view.selected, set())
        self.assertEqual(len(self.win.undo_stack), depth + 1)

    def test_paint_shortcuts_and_exit(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self._paint_setup()
        self.assertTrue(self.win.picker.paint_btn.isChecked())
        self.win.activateWindow()
        self.win.picker.hex.setFocus()
        self.app.processEvents()
        self.win.picker.hex.selectAll()
        QTest.keyClick(self.win.picker.hex, Qt.Key.Key_B)
        self.assertTrue(self.win.view.paint_mode, "typing B in the hex field must not toggle")
        self.win.view.setFocus()
        self.win._paint_exit.activated.emit()
        self.assertFalse(self.win.view.paint_mode)
        self.assertFalse(self.win.picker.paint_btn.isChecked())
        self.win.set_paint_mode(True)
        self.win.show_library()
        self.assertFalse(self.win.view.paint_mode)
        self.win._toggle_paint_mode()
        self.assertFalse(self.win.view.paint_mode, "B must do nothing in the Library")

    def test_old_profile_round_trips_unchanged(self):
        old = {"version": 1, "name": "Old", "brightness": 40,
               "keyboard": {"default": "#112233", "keys": {"W": "#FF0000", "ZZZ": "#00FF00"}},
               "elc": {"default": "#445566", "zones": {"3": "#0000FF"}}}
        self.assertEqual(Profile.from_dict(old).to_dict(), old)


if __name__ == "__main__":
    unittest.main()
