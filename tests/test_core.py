"""Run with: python3 -m unittest discover -s tests

Uses a fake CLI and temporary directories, so it never touches real lights.
"""
import contextlib
import io
import json
import os
import stat
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

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
                  ALIENFX_STEAM_ROOT=str(Path(TMP.name) / "no-steam"),
                  QT_QPA_PLATFORM="offscreen")
os.environ["ALIENFX_STEAM_PID"] = str(Path(TMP.name) / "steam.pid")
os.environ.pop("ALIENFX_KEYBOARD_DEV", None)
os.environ.pop("ALIENFX_ELC_DEV", None)

from alienfx_studio import device, inotify, kbmap, rules, steam, watcher  # noqa: E402
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
        self.addCleanup(win.file_events.close)
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
        self.addCleanup(self.win.file_events.close)
        self.addCleanup(self.win.games.steam_events.close)
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


class ShowingTests(unittest.TestCase):
    """A game's temporary profile must never become the one restored at login."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(self.dir.name)
        game = default_profile()
        game.name = "Game"
        self.store.save(game)

    def test_showing_leaves_normal_profile_alone(self):
        self.store.set_showing("Game", "steam:1677110")
        self.assertEqual(self.store.showing(), ("Game", "steam:1677110"))
        self.assertEqual(self.store.current_name(), "Game")
        self.assertEqual(self.store.active_name(), "Default")
        self.assertEqual(json.loads(self.store.state.read_text()), {"active": "Default"})

    def test_clearing_returns_to_normal(self):
        self.store.set_showing("Game", "steam:1")
        self.store.clear_showing()
        self.store.clear_showing()      # already gone is fine
        self.assertIsNone(self.store.showing())
        self.assertEqual(self.store.current_name(), "Default")

    def test_lost_runtime_file_returns_to_normal(self):
        self.store.set_showing("Game", "steam:1")
        self.store.showing_path.unlink()        # what a reboot does
        self.assertEqual(Store(self.dir.name).current_name(), "Default")

    def test_choosing_a_profile_ends_temporary_one(self):
        self.store.set_showing("Game", "steam:1")
        self.store.set_active("Game")
        self.assertIsNone(self.store.showing())
        self.assertEqual(self.store.active_name(), "Game")

    def test_rename_follows_temporary_profile(self):
        self.store.set_showing("Game", "steam:1")
        self.store.rename("Game", "Renamed")
        self.assertEqual(self.store.showing(), ("Renamed", "steam:1"))
        self.assertEqual(self.store.active_name(), "Default")

    def test_renaming_normal_profile_keeps_temporary_one(self):
        self.store.set_showing("Game", "steam:1")
        self.store.rename("Default", "Home")
        self.assertEqual(self.store.active_name(), "Home")
        self.assertEqual(self.store.showing(), ("Game", "steam:1"))

    def test_deleted_or_corrupt_temporary_profile_is_ignored(self):
        self.store.set_showing("Game", "steam:1")
        self.store.delete("Game")
        self.assertIsNone(self.store.showing())
        self.assertEqual(self.store.current_name(), "Default")
        for text in ("not json", "[]", '{"profile": 3, "source": "x"}'):
            self.store.showing_path.write_text(text)
            self.assertIsNone(self.store.showing())

    def test_cli_list_and_apply(self):
        from alienfx_studio import __main__ as cli
        LOG.unlink(missing_ok=True)
        env = {"XDG_CONFIG_HOME": self.dir.name, "XDG_RUNTIME_DIR": self.dir.name}
        store = Store(Path(self.dir.name) / "alienfx")
        game = default_profile()
        game.name = "Game"
        store.save(game)

        def run(*args):
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", ["x", *args]), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(), 0)
                shown = Store()
            return out.getvalue().splitlines(), shown

        with mock.patch.dict(os.environ, env):
            Store().set_showing("Game", "steam:1")
        self.assertEqual(run("list")[0], ["* Default", "> Game"])
        _, after = run("apply")                 # what the login service runs
        self.assertIsNone(after.showing())
        self.assertEqual(after.active_name(), "Default")
        self.assertTrue(calls())


class RulesTests(unittest.TestCase):
    def rules(self):
        return rules.Rules()

    def test_game_link_beats_type(self):
        table = self.rules()
        table.type_named("RPG").profile = "Red"
        table.games["steam:1"] = rules.GameRule(profile="Mine")
        self.assertEqual(table.profile_for("steam:1", ("RPG",), lambda _: True),
                         ("Mine", "linked game"))
        self.assertEqual(table.profile_for("steam:2", ("RPG",), lambda _: True),
                         ("Red", "RPG type"))

    def test_first_type_in_order_wins_and_override_beats_it(self):
        table = self.rules()
        tags = ("FPS", "RPG")
        self.assertEqual(table.type_of("steam:1", tags).name, "Shooter")
        table.types.sort(key=lambda t: t.name != "RPG")
        self.assertEqual(table.type_of("steam:1", tags).name, "RPG")
        table.games["steam:1"] = rules.GameRule(type="Adventure")
        self.assertEqual(table.type_of("steam:1", tags).name, "Adventure")

    def test_missing_profile_falls_through(self):
        table = self.rules()
        table.type_named("RPG").profile = "Red"
        table.games["steam:1"] = rules.GameRule(profile="Deleted")
        self.assertEqual(table.profile_for("steam:1", ("RPG",), lambda n: n == "Red"),
                         ("Red", "RPG type"))
        self.assertIsNone(table.profile_for("steam:1", ("RPG",), lambda _: False))

    def test_disabled_matches_nothing(self):
        table = self.rules()
        table.enabled = False
        table.games["steam:1"] = rules.GameRule(profile="Mine")
        self.assertIsNone(table.profile_for("steam:1", (), lambda _: True))

    def test_round_trip_and_malformed_file(self):
        with tempfile.TemporaryDirectory() as root:
            table = self.rules()
            table.games["steam:1"] = rules.GameRule(profile="Mine", type="RPG")
            rules.save(root, table)
            self.assertEqual(rules.load(root).to_dict(), table.to_dict())
            rules.path(root).write_text("{broken")
            with self.assertRaises(rules.RulesError):
                rules.load(root)

    def test_store_rename_updates_links(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            table = self.rules()
            table.type_named("RPG").profile = "Default"
            table.games["steam:1"] = rules.GameRule(profile="Default")
            rules.save(root, table)
            store.rename("Default", "Home")
            loaded = rules.load(root)
            self.assertEqual(loaded.type_named("RPG").profile, "Home")
            self.assertEqual(loaded.games["steam:1"].profile, "Home")


def write_appinfo(path, apps):
    strings = ["appinfo", "common", "type", "store_tags", "0", "1"]
    key = lambda name: struct.pack("<I", strings.index(name))
    body = b""
    for app_id, (kind, tags) in apps.items():
        vdf = b"\0" + key("appinfo") + b"\0" + key("common")
        vdf += b"\1" + key("type") + kind.encode() + b"\0"
        vdf += b"\0" + key("store_tags")
        for i, tag in enumerate(tags):
            vdf += b"\2" + key(str(i)) + struct.pack("<i", tag)
        vdf += b"\x08\x08\x08\x08"
        body += struct.pack("<II", app_id, 60 + len(vdf)) + bytes(60) + vdf
    body += struct.pack("<I", 0)
    table = struct.pack("<I", len(strings)) + b"".join(s.encode() + b"\0" for s in strings)
    path.write_bytes(struct.pack("<IIq", 0x07564429, 1, 16 + len(body)) + body + table)


class SteamTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "Steam"
        extra = Path(self.dir.name) / "Other Drive"
        (self.root / "steamapps").mkdir(parents=True)
        (self.root / "appcache").mkdir()
        (extra / "steamapps").mkdir(parents=True)
        (self.root / "steamapps" / "libraryfolders.vdf").write_text(
            f'"libraryfolders"\n{{\n"0"\n{{\n"path" "{extra}"\n}}\n}}\n')
        self.manifest(self.root, 10, "Beta Game")
        self.manifest(extra, 20, "Alpha Game")
        self.manifest(self.root, 30, "Proton Tool")
        self.info = self.root / "appcache" / "appinfo.vdf"
        write_appinfo(self.info, {10: ("Game", [1, 2]), 20: ("game", [2, 999]),
                                  30: ("tool", [1]), 40: ("game", [1])})
        patcher = mock.patch.object(steam, "tag_names",
                                    return_value={1: "Automation", 2: "RPG"})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def manifest(library, app_id, name):
        (library / "steamapps" / f"appmanifest_{app_id}.acf").write_text(
            f'"AppState"\n{{\n"appid" "{app_id}"\n"name" "{name}"\n}}\n')

    def test_reads_games_across_libraries(self):
        games = steam.Library(self.root).games()
        self.assertEqual([(g.app_id, g.name, g.tags) for g in games],
                         [(20, "Alpha Game", ("RPG",)), (10, "Beta Game", ("Automation", "RPG"))])

    def test_bad_appinfo_still_lists_games(self):
        for content in (b"", b"\0" * 32):
            self.info.write_bytes(content)
            with contextlib.redirect_stderr(io.StringIO()):
                games = steam.Library(self.root).games()
            self.assertEqual({g.app_id for g in games}, {10, 20, 30})
            self.assertTrue(all(g.tags == () for g in games))

    def test_unknown_running_game_gets_a_name(self):
        self.assertEqual(steam.Library(self.root).game(77).name, "Steam app 77")

    def test_stamp_tracks_installs_but_not_download_progress(self):
        library = steam.Library(self.root)
        first = library.stamp()
        manifest = self.root / "steamapps" / "appmanifest_10.acf"
        os.utime(manifest, ns=(1, 1))
        self.assertEqual(library.stamp(), first)
        self.manifest(self.root, 50, "New Game")
        second = library.stamp()
        self.assertNotEqual(second, first)
        (self.root / "steamapps" / "appmanifest_50.acf").unlink()
        self.assertEqual(library.stamp(), first)
        os.utime(self.info, ns=(2, 2))
        self.assertNotEqual(library.stamp(), first)

    def test_bundled_tag_names(self):
        patcher = mock.patch.stopall
        patcher()
        names = steam.tag_names()
        self.assertIn("Automation", names.values())
        self.assertIn("Open World Survival Craft", names.values())


class FakeLibrary:
    def __init__(self, tags=None):
        self.tags = tags or {}

    def game(self, app_id):
        return steam.Game(app_id, f"Game {app_id}", self.tags.get(app_id, ()))


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(self.dir.name)
        for name, color in (("Green", "#00FF00"), ("Red", "#FF0000")):
            p = default_profile()
            p.name, p.kb_default = name, color
            self.store.save(p)
        table = rules.Rules()
        table.type_named("Survival").profile = "Green"
        table.games["steam:2"] = rules.GameRule(profile="Red")
        rules.save(self.dir.name, table)
        self.running = set()
        self.sent = []
        output = io.StringIO()
        redirect = contextlib.redirect_stdout(output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.watch = watcher.Watcher(self.store, FakeLibrary({1: ("Survival",)}),
                                     self.sent.append, lambda: set(self.running))

    def frame(self, name):
        return device.build_frame(self.store.load(name))

    def test_type_profile_holds_until_exit_then_restores(self):
        self.running = {1}
        self.watch.tick(0)
        self.assertEqual(self.sent, [self.frame("Green")])
        self.assertEqual(self.store.showing(), ("Green", "steam:1"))
        self.assertEqual(self.store.active_name(), "Default")
        self.watch.tick(10)
        self.assertEqual(len(self.sent), 1)     # staying open never rewrites the lights

        self.running = set()
        self.watch.tick(20)
        self.assertEqual(len(self.sent), 1)     # within the grace period
        self.watch.tick(20 + watcher.EXIT_GRACE)
        self.assertEqual(self.sent[-1], self.frame("Default"))
        self.assertIsNone(self.store.showing())

    def test_brief_restart_does_not_flash(self):
        self.running = {1}
        self.watch.tick(0)
        self.running = set()
        self.watch.tick(1)
        self.running = {1}
        self.watch.tick(2)
        self.running = set()
        self.watch.tick(3)
        self.watch.tick(3 + watcher.EXIT_GRACE - 1)
        self.assertEqual(len(self.sent), 1)

    def test_game_without_profile_uses_default(self):
        self.running = {9}
        self.watch.tick(0)
        self.assertEqual(self.sent, [])         # the default is already on the lights
        self.assertEqual(self.store.showing(), ("Default", "steam:9"))

    def test_first_game_keeps_lights_then_second_takes_over(self):
        self.running = {1}
        self.watch.tick(0)
        self.running = {1, 2}
        self.watch.tick(1)
        self.assertEqual(self.store.showing()[0], "Green")
        self.running = {2}
        self.watch.tick(2)
        self.watch.tick(2 + watcher.EXIT_GRACE)
        self.assertEqual(self.store.showing(), ("Red", "steam:2"))
        self.assertEqual(self.sent[-1], self.frame("Red"))

    def test_manual_choice_is_left_alone_until_exit(self):
        self.running = {1}
        self.watch.tick(0)
        self.store.set_active("Red")            # the user picked a profile
        self.watch.tick(1)
        self.watch.tick(2)
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.store.showing())

        self.running = set()
        self.watch.tick(3)
        self.watch.tick(3 + watcher.EXIT_GRACE)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.store.active_name(), "Red")
        self.running = {1}
        self.watch.tick(20)                     # the next launch follows the rules again
        self.assertEqual(self.store.showing(), ("Green", "steam:1"))

    def test_stale_temporary_profile_is_cleared_on_start(self):
        self.store.set_showing("Red", "steam:2")
        self.watch.tick(0)
        self.assertIsNone(self.store.showing())
        self.assertEqual(self.sent, [self.frame("Default")])

    def test_rule_changes_apply_during_a_game(self):
        self.running = {1}
        self.watch.tick(0)
        table = rules.load(self.dir.name)
        table.games["steam:1"] = rules.GameRule(profile="Red")
        rules.save(self.dir.name, table)
        os.utime(rules.path(self.dir.name), ns=(1, 1))
        self.watch.tick(1)
        self.assertEqual(self.store.showing(), ("Red", "steam:1"))

    def test_broken_rules_keep_last_good_rules(self):
        self.running = {1}
        rules.path(self.dir.name).write_text("{broken")
        self.watch.tick(0)
        self.assertEqual(self.store.showing(), ("Green", "steam:1"))

    def test_scan_finds_only_steam_reapers(self):
        with tempfile.TemporaryDirectory() as proc:
            for pid, comm, args in ((1, "reaper", b"reaper\0SteamLaunch\0AppId=42\0--\0game\0"),
                                    (2, "oom_reaper", b""),
                                    (3, "sh", b"sh\0-c\0reaper SteamLaunch AppId=7\0"),
                                    (4, "reaper", b"reaper\0other\0AppId=8\0"),
                                    (5, "reaper", b"reaper\0SteamLaunch\0AppId=0\0")):
                (Path(proc) / str(pid)).mkdir()
                (Path(proc) / str(pid) / "comm").write_text(comm + "\n")
                (Path(proc) / str(pid) / "cmdline").write_bytes(args)
            (Path(proc) / "self").mkdir()
            self.assertEqual(watcher.running_steam_apps(proc), {42})


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.notifier = inotify.Notifier()
        self.addCleanup(self.notifier.close)
        self.notifier.add(self.dir.name)
        self.path = os.path.join(self.dir.name, "state.json")

    def test_reports_atomic_replace_and_delete(self):
        Store._write(Path(self.path), {"a": 1})
        self.assertIn(self.path, self.notifier.read())
        os.unlink(self.path)
        self.assertEqual(self.notifier.read(), {self.path})
        self.assertEqual(self.notifier.read(), set())

    def test_lock_probe_raises_no_event(self):
        name = device.EDITOR_LOCK
        watched = inotify.Notifier()
        self.addCleanup(watched.close)
        watched.add(device.runtime_dir())
        lock = device.try_lock(name)
        watched.read()
        self.assertTrue(device.lock_held(name))
        self.assertEqual(watched.read(), set())
        lock.close()
        self.assertIn(os.path.join(device.runtime_dir(), name), watched.read())

    def test_steam_state(self):
        with tempfile.TemporaryDirectory() as proc:
            (Path(proc) / "42").mkdir()
            (Path(proc) / "42" / "comm").write_text("steam\n")
            (Path(proc) / "43").mkdir()
            (Path(proc) / "43" / "comm").write_text("bash\n")
            for text, state in (("42\n", "running"), ("43", "stopped"), ("99", "stopped"),
                                ("junk", "unknown")):
                Path(self.path).write_text(text)
                self.assertEqual(steam.steam_state(self.path, proc), state)
            self.assertEqual(steam.steam_state(self.path + ".none", proc), "unknown")

    def test_watcher_delay_follows_steam_and_games(self):
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(io.StringIO()):
            w = watcher.Watcher(Store(root), FakeLibrary(), lambda _: None, lambda: {1},
                                lambda: False)
            with mock.patch.object(steam, "steam_state", return_value="stopped"):
                self.assertIsNone(w.delay())
            with mock.patch.object(steam, "steam_state", return_value="running"):
                self.assertEqual(w.delay(), watcher.IDLE_SECONDS)
            with mock.patch.object(steam, "steam_state", return_value="unknown"):
                self.assertEqual(w.delay(), watcher.FALLBACK_SECONDS)
            w.tick(0)
            self.assertEqual(w.delay(), watcher.GAME_SECONDS)
            self.assertEqual(w.store.running_games(), {1})


class ExternalWriteTests(unittest.TestCase):
    def test_same_frame_is_rewritten_after_another_writer(self):
        LOG.unlink(missing_ok=True)
        done = threading.Event()
        writer = device.LiveWriter(lambda *_: done.set())
        self.addCleanup(writer.close)
        frame = device.build_frame(default_profile())
        writer.submit(frame)
        self.assertTrue(done.wait(5))
        done.clear()
        other = default_profile()
        other.kb_default = "#010203"
        time.sleep(.01)
        device.send(device.build_frame(other))
        before = len(calls())
        writer.submit(frame)
        self.assertTrue(done.wait(5))
        self.assertGreater(len(calls()), before)


class ChooseTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(self.dir.name)
        for name in ("Game", "Pick"):
            p = default_profile()
            p.name = name
            self.store.save(p)

    def hold_watch(self):
        lock = device.try_lock(device.WATCH_LOCK)
        self.assertIsNotNone(lock)
        self.addCleanup(lock.close)

    def test_pick_during_game_is_temporary(self):
        self.hold_watch()
        self.store.set_showing("Game", "steam:1", "Some Game")
        self.store.choose("Pick")
        self.assertEqual(self.store.showing(), ("Pick", "manual:steam:1"))
        self.assertEqual(self.store.showing_game(), "Some Game")
        self.assertEqual(self.store.active_name(), "Default")
        self.store.choose("Game")
        self.assertEqual(self.store.showing(), ("Game", "manual:steam:1"))

    def test_saving_the_shown_profile_keeps_the_game_session(self):
        self.hold_watch()
        self.store.set_showing("Game", "steam:1")
        self.store.choose("Game")
        self.assertEqual(self.store.showing(), ("Game", "steam:1"))

    def test_pick_outside_a_game_keeps_the_default(self):
        self.store.set_showing("Game", "steam:1")     # left by a watcher that died
        self.store.choose("Pick")
        self.assertEqual(self.store.showing(), ("Pick", "picked"))
        self.assertEqual(self.store.active_name(), "Default")
        self.assertFalse(self.store.drop_stale_showing())
        self.store.choose("Default")
        self.assertIsNone(self.store.showing())

    def test_setting_default_leaves_the_lights_alone(self):
        self.store.choose("Pick")
        self.store.set_default("Game")
        self.assertEqual(self.store.active_name(), "Game")
        self.assertEqual(self.store.current_name(), "Pick")
        self.store.set_default("Pick")
        self.assertIsNone(self.store.showing())
        self.assertEqual(self.store.current_name(), "Pick")

    def test_stale_temporary_profile_is_dropped_only_without_watcher(self):
        self.store.set_showing("Game", "steam:1")
        lock = device.try_lock(device.WATCH_LOCK)
        self.assertFalse(self.store.drop_stale_showing())
        lock.close()
        self.assertTrue(self.store.drop_stale_showing())
        self.assertIsNone(self.store.showing())

    def test_lock_probe_does_not_block_a_holder(self):
        self.assertFalse(device.lock_held(device.EDITOR_LOCK))
        lock = device.try_lock(device.EDITOR_LOCK)
        self.addCleanup(lock.close)
        self.assertTrue(device.lock_held(device.EDITOR_LOCK))
        self.assertIsNone(device.try_lock(device.EDITOR_LOCK))


class WatcherCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(self.dir.name)
        for name in ("Game", "Pick"):
            p = default_profile()
            p.name, p.kb_default = name, "#00FF00" if name == "Game" else "#FF0000"
            self.store.save(p)
        table = rules.Rules()
        table.games["steam:1"] = rules.GameRule(profile="Game")
        rules.save(self.dir.name, table)
        self.running, self.sent, self.is_paused = set(), [], False
        redirect = contextlib.redirect_stdout(io.StringIO())
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.watch = watcher.Watcher(self.store, FakeLibrary(), self.sent.append,
                                     lambda: set(self.running), lambda: self.is_paused)

    def test_paused_launch_applies_after_editing(self):
        self.is_paused, self.running = True, {1}
        self.watch.tick(0)
        self.assertEqual(self.sent, [])
        self.is_paused = False
        self.watch.tick(1)
        self.assertEqual(self.store.showing()[0], "Game")

    def test_manual_pick_returns_to_normal_on_exit(self):
        self.running = {1}
        self.watch.tick(0)
        self.store.set_showing("Pick", "manual:steam:1", "Game 1")
        self.watch.tick(1)
        self.assertEqual(len(self.sent), 1)
        self.running = set()
        self.watch.tick(2)
        self.watch.tick(2 + watcher.EXIT_GRACE)
        self.assertIsNone(self.store.showing())
        self.assertEqual(self.store.active_name(), "Default")
        self.assertEqual(self.sent[-1], device.build_frame(self.store.load("Default")))

    def test_exit_while_paused_restores_after_editing(self):
        self.running = {1}
        self.watch.tick(0)
        self.is_paused, self.running = True, set()
        self.watch.tick(1)
        self.watch.tick(1 + watcher.EXIT_GRACE)
        self.assertEqual(len(self.sent), 1)
        self.is_paused = False
        self.watch.tick(10)
        self.assertIsNone(self.store.showing())
        self.assertEqual(self.sent[-1], device.build_frame(self.store.load("Default")))

    def test_picked_profile_survives_until_a_game_then_default_returns(self):
        self.store.choose("Pick")
        self.watch.tick(0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.showing(), ("Pick", "picked"))
        self.running = {9}                      # a game with no profile uses the default
        self.watch.tick(1)
        self.assertEqual(self.store.showing(), ("Default", "steam:9"))
        self.assertEqual(self.sent, [device.build_frame(self.store.load("Default"))])
        self.running = set()
        self.watch.tick(2)
        self.watch.tick(2 + watcher.EXIT_GRACE)
        self.assertIsNone(self.store.showing())
        self.assertEqual(len(self.sent), 1)

    def test_game_on_default_lights_writes_nothing(self):
        self.running = {9}
        self.watch.tick(0)
        self.assertEqual(self.store.showing(), ("Default", "steam:9"))
        self.running = set()
        self.watch.tick(1)
        self.watch.tick(1 + watcher.EXIT_GRACE)
        self.assertEqual(self.sent, [])

    def test_stopping_while_paused_does_not_overwrite_editor(self):
        self.running = {1}
        self.watch.tick(0)
        self.is_paused = True
        self.watch.release()
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.store.showing())


class GamesGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt6 not installed")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from alienfx_studio.app import MainWindow
        self.root = Path(TMP.name) / "alienfx"
        rules.path(self.root).unlink(missing_ok=True)
        store = Store()
        self.original = store.active_name()
        store.clear_showing()
        for name in ("Green", "Red"):
            p = default_profile()
            p.name = name
            store.save(p)
        self.games = [steam.Game(1, "Valheim", ("Survival", "RPG")),
                      steam.Game(2, "Cyberpunk", ("FPS", "RPG")),
                      steam.Game(3, "Mystery", ())]
        patcher = mock.patch("alienfx_studio.games.Library")
        self.steam = patcher.start().return_value
        self.steam.games.return_value = self.games
        self.steam.stamp.return_value = "first"
        self.steam.watch_dirs.return_value = []
        self.addCleanup(patcher.stop)
        self.win = MainWindow()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.win.file_events.close()
        self.win.games.steam_events.close()
        if self.win._pause_lock:
            self.win._pause_lock.close()
            self.win._pause_lock = None
        self.win.hide()
        self.win.writer.close()
        store = Store()
        store.clear_showing()
        store.set_active(self.original)
        for name in ("Green", "Red"):
            store.delete(name)
        rules.path(self.root).unlink(missing_ok=True)

    def row(self, name):
        return next(r for r in self.win.games.game_rows if r.game.name == name)

    def open_games(self):
        self.win.resize(1440, 900)
        self.win.show()
        self.win.show_games()
        self.app.processEvents()
        return self.win.games

    def test_library_button_opens_games(self):
        self.win.show()
        self.win.library.games_btn.click()
        self.assertIs(self.win.pages.currentWidget(), self.win.games)
        self.win.games.back_btn.click()
        self.assertIs(self.win.pages.currentWidget(), self.win.library)

    def test_rows_show_detected_types_and_running_state(self):
        lock = device.try_lock(device.WATCH_LOCK)
        self.addCleanup(lock.close)
        self.win.store.set_running({1})
        self.addCleanup(self.win.store.clear_running)
        self.open_games()
        self.assertEqual(self.row("Valheim").type.currentText(), "Survival")
        self.assertEqual(self.row("Cyberpunk").type.currentText(), "Shooter")
        self.assertEqual(self.row("Mystery").type.currentText(), "No type")
        self.assertEqual(self.row("Valheim").profile.currentText(), "Survival's Profile")
        self.assertEqual(self.row("Mystery").profile.currentText(), "Default Profile")
        names = [self.row("Valheim").type.itemText(i)
                 for i in range(self.row("Valheim").type.count())]
        self.assertEqual(names.count("Survival"), 1)
        rows = self.win.games.type_list.rows
        self.assertEqual(rows[0].profile.currentText(), "Default Profile")
        self.assertFalse(self.row("Valheim").running.isHidden())
        self.assertTrue(self.row("Mystery").running.isHidden())

    def test_type_profile_flows_to_game_rows_and_disk(self):
        page = self.open_games()
        row = next(r for r in page.type_list.rows if r.name == "Survival")
        row.profile.setCurrentIndex(row.profile.findData("Green"))
        row.profile.activated.emit(row.profile.currentIndex())
        self.assertEqual(rules.load(self.root).type_named("Survival").profile, "Green")
        self.assertEqual(self.row("Valheim").profile.currentText(), "Survival's Profile")
        self.assertEqual(self.row("Valheim").profile.toolTip(), "Survival uses Green")

    def test_game_overrides_save_and_clear(self):
        self.open_games()
        row = self.row("Cyberpunk")
        row.type.setCurrentIndex(row.type.findData("RPG"))
        row.type.activated.emit(row.type.currentIndex())
        row = self.row("Cyberpunk")
        row.profile.setCurrentIndex(row.profile.findData("Red"))
        row.profile.activated.emit(row.profile.currentIndex())
        saved = rules.load(self.root).games["steam:2"]
        self.assertEqual((saved.type, saved.profile), ("RPG", "Red"))
        row.type.setCurrentIndex(0)
        row.type.activated.emit(0)
        row.profile.setCurrentIndex(0)
        row.profile.activated.emit(0)
        self.assertNotIn("steam:2", rules.load(self.root).to_dict()["games"])

    def test_keyboard_and_mouse_reorder_types(self):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        page = self.open_games()
        first = page.type_list.rows[0]
        QTest.keyClick(first.handle, Qt.Key.Key_Down)
        self.assertEqual([t.name for t in rules.load(self.root).types][:2],
                         ["Survival", "Automation"])
        last = page.type_list.rows[-1]
        target = page.type_list.rows[0]
        page.type_list._drag(last, target.mapToGlobal(QPoint(0, 0)).y() - 5)
        page.type_list._drop()
        self.assertEqual(rules.load(self.root).types[0].name, "Adventure")

    def test_auto_switch_toggle_and_broken_rules(self):
        page = self.open_games()
        page.auto.click()
        self.assertFalse(rules.load(self.root).enabled)
        rules.path(self.root).write_text("{broken")
        page.refresh()
        self.assertFalse(page.body.isEnabled())
        self.assertIn("could not be read", page.notice.text())
        self.assertEqual(rules.path(self.root).read_text(), "{broken")

    def test_editor_and_unsaved_edits_pause_the_watcher(self):
        self.win.show()
        self.win.show_library()
        self.assertFalse(device.lock_held(device.EDITOR_LOCK))
        self.win.show_editor()
        self.assertTrue(device.lock_held(device.EDITOR_LOCK))
        self.win.view.select({"K:W"})
        self.win.picker.hex.textEdited.emit("#010203")
        self.win.show_library()
        self.assertTrue(device.lock_held(device.EDITOR_LOCK), "unsaved edits keep it paused")
        self.win.undo()
        self.assertFalse(device.lock_held(device.EDITOR_LOCK))

    def test_gui_follows_watcher_without_writing(self):
        lock = device.try_lock(device.WATCH_LOCK)
        self.addCleanup(lock.close)
        self.win.writer.close()
        self.win.writer = mock.Mock()
        self.win.show()
        self.win.show_library()
        self.win.store.set_showing("Green", "steam:1", "Valheim")
        self.win._poll_game_state()
        self.assertEqual(self.win.profile.name, "Green")
        self.assertEqual(self.win.game_session, ("Green", "Valheim"))
        self.assertEqual(self.win.library.cards["Green"].status.text(), "Until Valheim exits")
        self.win.writer.submit.assert_not_called()
        self.win.switch_profile("Red")          # a pick during a game
        self.assertEqual(self.win.store.showing(), ("Red", "manual:steam:1"))
        self.assertEqual(self.win.store.active_name(), self.original)
        self.win.store.clear_showing()
        self.win._poll_game_state()
        self.assertEqual(self.win.profile.name, self.original)

    def test_crashed_watcher_restores_normal_lighting(self):
        self.win.store.set_showing("Green", "steam:1", "Valheim")
        self.win.writer.close()
        self.win.writer = mock.Mock()
        self.win._poll_game_state()
        self.assertIsNone(self.win.store.showing())
        self.assertEqual(self.win.profile.name, self.original)
        self.win.writer.submit.assert_called_once()

    def test_crash_during_editing_restores_after_editing(self):
        self.win.writer.close()
        self.win.writer = mock.Mock()
        self.win.show()
        self.win.show_editor()
        self.win.store.set_showing("Green", "steam:1", "Valheim")
        self.win._poll_game_state()
        self.win.writer.submit.assert_not_called()
        self.win.show_library()
        self.win._poll_game_state()
        self.win.writer.submit.assert_called_once()

    def library_changes(self, games):
        self.steam.games.return_value = games
        self.steam.stamp.return_value = object()
        self.win.games.check_library()
        self.app.processEvents()

    def names(self):
        return [r.game.name for r in self.win.games.game_rows]

    def test_open_screen_adds_and_removes_games_in_order(self):
        self.open_games()
        kept = self.row("Valheim")
        added = steam.Game(4, "Hades", ("Roguelike",))
        self.library_changes([added, self.games[1], self.games[0]])
        self.assertEqual(self.names(), ["Hades", "Cyberpunk", "Valheim"])
        self.assertIs(self.row("Valheim"), kept, "unchanged rows are not rebuilt")
        self.assertEqual(self.row("Hades").type.currentText(), "Roguelike")
        box = self.win.games.game_box
        shown = [box.itemAt(i).widget() for i in range(1, box.count())]
        self.assertEqual([w.game.name for w in shown], self.names())

    def test_reinstalled_game_keeps_its_settings(self):
        self.open_games()
        row = self.row("Cyberpunk")
        row.profile.setCurrentIndex(row.profile.findData("Red"))
        row.profile.activated.emit(row.profile.currentIndex())
        self.library_changes([self.games[0], self.games[2]])
        self.assertNotIn("Cyberpunk", self.names())
        self.assertEqual(rules.load(self.root).games["steam:2"].profile, "Red")
        self.library_changes(self.games)
        self.assertEqual(self.row("Cyberpunk").profile.currentText(), "Red")

    def test_new_tags_update_a_listed_game(self):
        self.open_games()
        self.library_changes([steam.Game(3, "Mystery", ("RPG",)), *self.games[:2]])
        self.assertEqual(self.row("Mystery").type.currentText(), "RPG")

    def test_live_update_keeps_search_and_waits_for_open_dropdown(self):
        page = self.open_games()
        page.search.setText("val")
        added = steam.Game(4, "Valley", ())
        with mock.patch.object(page, "_popup_open", return_value=True):
            self.library_changes([*self.games, added])
        self.assertNotIn("Valley", self.names())
        page.check_library()
        self.assertIn("Valley", self.names())
        self.assertEqual(page.search.text(), "val")
        self.assertTrue(self.row("Mystery").isHidden())
        self.assertFalse(self.row("Valley").isHidden())

    def test_unchanged_library_is_not_reread(self):
        self.open_games()
        self.steam.games.reset_mock()
        self.win.games.check_library()
        self.steam.games.assert_not_called()

    def test_default_checkbox_keeps_one_default(self):
        self.win.resize(1440, 900)
        self.win.show()
        self.win.show_library()
        self.win.library.refresh()
        self.win.writer.close()
        self.win.writer = mock.Mock()
        current = self.win.profile.name
        self.win.library.cards["Red"].default_box.click()
        self.assertEqual(self.win.store.active_name(), "Red")
        self.assertEqual(self.win.profile.name, current)
        self.win.writer.submit.assert_not_called()
        cards = self.win.library.cards
        self.assertEqual([n for n, c in cards.items() if c.default_box.isChecked()], ["Red"])
        cards["Red"].default_box.click()        # the default cannot be unchecked
        self.assertTrue(self.win.library.cards["Red"].default_box.isChecked())
        self.assertEqual(self.win.store.active_name(), "Red")

    def test_card_click_applies_without_changing_default(self):
        self.win.writer.close()
        self.win.writer = mock.Mock()
        self.win.switch_profile("Green")
        self.assertEqual(self.win.store.active_name(), self.original)
        self.assertEqual(self.win.store.current_name(), "Green")
        self.win.writer.submit.assert_called_once()

    def test_tray_shows_game_and_toggle(self):
        from alienfx_studio.tray import Tray
        self.win.store.set_showing("Green", "steam:1", "Valheim")
        lock = device.try_lock(device.WATCH_LOCK)
        self.addCleanup(lock.close)
        self.win._read_game_session()
        tray = Tray(self.win, lambda: None)
        self.addCleanup(tray.hide)
        texts = [a.text() for a in tray.menu.actions()]
        self.assertIn("Valheim: Green", texts)
        toggle = next(a for a in tray.menu.actions() if a.text() == "Automatic game switching")
        self.assertTrue(toggle.isChecked())
        toggle.trigger()
        self.assertFalse(rules.load(self.root).enabled)


if __name__ == "__main__":
    unittest.main()
